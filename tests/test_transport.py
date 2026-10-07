"""Synthetic local listeners exercise the privacy boundary without a model."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from cleardictation import core


class ModelTransport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "config"
        self.directory.mkdir(mode=0o700)
        self.key = self.directory / "model.key"
        self.key.write_text("synthetic-token\n")
        self.env = patch.dict(os.environ, {
            "CLEAR_DICTATION_CONFIG": str(self.directory / "settings.json"),
            "CLEAR_DICTATION_STATE": str(Path(self.temp.name) / "state"),
            "http_proxy": "http://127.0.0.1:1",
            "HTTP_PROXY": "http://127.0.0.1:1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    @contextmanager
    def listener(self, body=b'{"status":"ok"}', status="200 OK", headers=""):
        """Capture all bytes, including an EOF before any HTTP data is sent."""
        path = self.directory / "model.sock"
        received, errors = [], []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            listener.listen(1)
            listener.settimeout(3)

            def serve():
                try:
                    with listener.accept()[0] as peer:
                        peer.settimeout(3)
                        data = b""
                        while b"\r\n\r\n" not in data:
                            chunk = peer.recv(65536)
                            if not chunk:
                                received.append(data)
                                return
                            data += chunk
                        head, payload = data.split(b"\r\n\r\n", 1)
                        length = next((int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                                       if line.lower().startswith(b"content-length:")), 0)
                        while len(payload) < length:
                            chunk = peer.recv(65536)
                            if not chunk:
                                break
                            data += chunk
                            payload += chunk
                        received.append(data)
                        response = (f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n"
                                    f"Connection: close\r\n{headers}\r\n").encode() + body
                        try:
                            peer.sendall(response)
                        except (BrokenPipeError, ConnectionResetError):
                            pass  # Rejected status or size limit can close early.
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=serve)
            thread.start()
            try:
                yield received
            finally:
                thread.join(4)
                path.unlink(missing_ok=True)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])

    def test_authenticated_socket_carries_token_and_exact_payload(self):
        payload = {"dictation": "Synthetic café transcript", "dictionary": {"voice type": "Voxtype"}}
        with self.listener() as received:
            self.assertEqual(core.local_request(core.MODEL_ENDPOINT, payload), {"status": "ok"})
        head, body = received[0].split(b"\r\n\r\n", 1)
        self.assertIn(b"POST /v1/chat/completions HTTP/1.1", head)
        self.assertIn(b"Authorization: Bearer synthetic-token", head)
        self.assertEqual(json.loads(body), payload)

    def test_foreign_peer_receives_zero_bytes_and_token_is_not_read(self):
        for endpoint, payload in ((core.MODEL_ENDPOINT, {"dictation": "private", "dictionary": {"name": "private"}}),
                                  ("http+unix://clear-dictation/health", None)):
            with self.subTest(endpoint=endpoint), self.listener() as received:
                real_socket = socket.socket

                def foreign_peer(*args, **kwargs):
                    peer = Mock(wraps=real_socket(*args, **kwargs))
                    peer.getsockopt.return_value = struct.pack("3i", 123, os.getuid() + 1, os.getgid())
                    return peer

                with patch.object(core.socket, "socket", side_effect=foreign_peer), patch.object(Path, "read_text") as read:
                    with self.assertRaisesRegex(ValueError, "different user"):
                        core.local_request(endpoint, payload)
                    read.assert_not_called()
            self.assertEqual(received, [b""])

    def test_credential_failure_closes_without_sending(self):
        with self.listener() as received:
            real_socket = socket.socket

            def broken_credentials(*args, **kwargs):
                peer = Mock(wraps=real_socket(*args, **kwargs))
                peer.getsockopt.side_effect = OSError("credentials unavailable")
                return peer

            with patch.object(core.socket, "socket", side_effect=broken_credentials):
                with self.assertRaises(OSError):
                    core.local_request(core.MODEL_ENDPOINT, {"dictation": "private"})
        self.assertEqual(received, [b""])

    def test_unsafe_directory_is_rejected_before_connecting(self):
        self.directory.chmod(0o755)
        with patch.object(core.socket, "socket") as network:
            with self.assertRaisesRegex(ValueError, "0700"):
                core.local_request(core.MODEL_ENDPOINT)
            network.assert_not_called()

    def test_symlink_directory_is_rejected(self):
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.directory, target_is_directory=True)
        with patch.dict(os.environ, CLEAR_DICTATION_CONFIG=str(link / "settings.json")):
            with self.assertRaisesRegex(ValueError, "symlink"):
                core.local_request(core.MODEL_ENDPOINT)

    def test_foreign_directory_is_rejected(self):
        with patch.object(core.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(ValueError, "user-owned"):
                core.local_request(core.MODEL_ENDPOINT)

    def test_tcp_and_arbitrary_endpoints_are_rejected_before_connecting(self):
        endpoints = [core.LEGACY_ENDPOINT, "http://localhost/health", "http://[::1]:18089/health",
                     "https://example.com/health", "http+unix://other/health",
                     core.MODEL_ENDPOINT + "?redirect=1", "http+unix://clear-dictation//example.com"]
        with patch.object(core.socket, "socket") as network:
            for endpoint in endpoints:
                with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                    core.local_request(endpoint, {"dictation": "private"})
            network.assert_not_called()

    def test_legacy_config_migrates_and_uses_socket_for_health(self):
        cfg = core.load_config()
        cfg.update(endpoint=core.LEGACY_ENDPOINT, dictionary={"voice type": "Voxtype"})
        core.save_config(cfg)
        loaded = core.load_config()
        self.assertEqual(loaded["endpoint"], core.MODEL_ENDPOINT)
        self.assertEqual(loaded["dictionary"], cfg["dictionary"])
        with self.listener() as received:
            self.assertTrue(core.health())
        self.assertTrue(received[0].startswith(b"GET /health HTTP/1.1"))

    def test_missing_or_stale_socket_preserves_dictation(self):
        cfg = core.load_config()
        cfg.update(endpoint=core.LEGACY_ENDPOINT, dictionary={"voice type": "Voxtype"})
        core.save_config(cfg)
        for stale in (False, True):
            with self.subTest(stale=stale):
                if stale:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                        peer.bind(str(self.directory / "model.sock"))
                with patch.object(core.socket, "create_connection") as tcp:
                    self.assertEqual(core.process("Open voice type."), "Open Voxtype.")
                    self.assertFalse(core.health())
                    tcp.assert_not_called()
                row = core.recent_history()[0]
                self.assertEqual(row["original"], "Open voice type.")
                self.assertEqual(row["status"], "fallback")

    def test_redirects_are_not_followed(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status), self.listener(status=f"{status} Redirect", headers="Location: http://127.0.0.1:18089/steal\r\n"):
                with patch.object(core.socket, "create_connection") as tcp:
                    with self.assertRaisesRegex(ValueError, "redirects"):
                        core.local_request(core.MODEL_ENDPOINT, {"dictation": "private"})
                    tcp.assert_not_called()

    def test_http_failure_and_response_size_limit(self):
        with self.listener(status="503 Unavailable"):
            with self.assertRaisesRegex(ValueError, "request failed"):
                core.local_request(core.MODEL_ENDPOINT)
        with self.listener(body=b"x" * (1024 * 1024 + 1)):
            with self.assertRaisesRegex(ValueError, "size limit"):
                core.local_request(core.MODEL_ENDPOINT)

    def test_missing_token_fails_without_sending(self):
        self.key.unlink()
        with self.listener() as received:
            with self.assertRaises(FileNotFoundError):
                core.local_request(core.MODEL_ENDPOINT)
        self.assertEqual(received, [b""])

    def test_service_start_removes_only_stale_socket(self):
        core.prepare_model_socket()
        path = self.directory / "model.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.bind(str(path))
        self.assertTrue(path.exists())
        core.prepare_model_socket()
        self.assertFalse(path.exists())

    def test_service_start_preserves_active_listener(self):
        with self.listener() as received:
            with self.assertRaisesRegex(ValueError, "already in use"):
                core.prepare_model_socket()
            self.assertTrue((self.directory / "model.sock").exists())
        self.assertEqual(received, [b""])

    def test_service_start_preserves_unexpected_files(self):
        path = self.directory / "model.sock"
        path.write_text("preserve me")
        with self.assertRaisesRegex(ValueError, "unsafe model socket"):
            core.prepare_model_socket()
        self.assertEqual(path.read_text(), "preserve me")
        path.unlink()
        path.symlink_to(self.key)
        with self.assertRaisesRegex(ValueError, "unsafe model socket"):
            core.prepare_model_socket()
        self.assertTrue(path.is_symlink())
        self.assertEqual(self.key.read_text(), "synthetic-token\n")
