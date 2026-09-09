import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import install


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'model'
        self.private = self.root / '.clear-dictation-downloads'
        self.private.mkdir(mode=0o700)
        self.part = self.private / 'model.part'
        self.payload = b'complete model contents\n'
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.victim = self.root / 'victim'
        self.victim.write_bytes(b'keep me')

    def download(self, url='https://example.invalid/model'):
        install.download(url, self.target, self.digest, len(self.payload))

    def seed(self, data):
        self.part.write_bytes(data)
        self.part.chmod(0o600)

    def reject(self):
        with patch.object(install.subprocess, 'run') as curl:
            with self.assertRaises((OSError, RuntimeError)):
                self.download()
            curl.assert_not_called()
        self.assertEqual(self.victim.read_bytes(), b'keep me')
        self.assertFalse(self.target.exists())

    def test_symlink_partial_rejected(self):
        self.part.symlink_to(self.victim)
        self.reject()

    def test_hardlink_partial_rejected(self):
        os.link(self.victim, self.part)
        self.reject()

    def test_fifo_partial_rejected_without_blocking(self):
        os.mkfifo(self.part, 0o600)
        self.reject()

    def test_directory_partial_rejected(self):
        self.part.mkdir()
        self.reject()

    def test_public_partial_rejected(self):
        self.seed(b'prefix')
        self.part.chmod(0o644)
        self.reject()

    def test_public_directory_rejected(self):
        self.private.chmod(0o755)
        self.reject()

    def test_symlink_directory_rejected(self):
        self.private.rmdir()
        self.private.symlink_to(self.root, target_is_directory=True)
        self.reject()

    def test_wrong_owner_rejected(self):
        with patch.object(install.os, 'getuid', return_value=os.getuid() + 1):
            self.reject()

    def test_wrong_partial_owner_rejected(self):
        self.seed(b'prefix')
        real_fstat = os.fstat

        def wrong_owner(fd):
            result = real_fstat(fd)
            if stat.S_ISREG(result.st_mode):
                fields = list(result)
                fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return result

        with patch.object(install.os, 'fstat', side_effect=wrong_owner):
            self.reject()

    def test_concurrent_resume_rejected(self):
        self.seed(b'prefix')
        with self.part.open('r+b') as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.reject()
        self.assertEqual(self.part.read_bytes(), b'prefix')

    def test_existing_target_symlink_rejected(self):
        self.target.symlink_to(self.victim)
        with patch.object(install.subprocess, 'run') as curl:
            with self.assertRaises(OSError):
                self.download()
            curl.assert_not_called()
        self.assertEqual(self.victim.read_bytes(), b'keep me')

    def test_checksum_failure_keeps_partial_only(self):
        def curl(args, *, stdout, check):
            os.write(stdout.fileno(), b'corrupt')
        with patch.object(install.subprocess, 'run', side_effect=curl):
            with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
                self.download()
        self.assertFalse(self.target.exists())
        self.assertEqual(self.part.read_bytes(), b'corrupt')

    def test_swapped_partial_never_promoted(self):
        def curl(args, *, stdout, check):
            self.part.unlink()
            self.part.symlink_to(self.victim)
            os.write(stdout.fileno(), self.payload)
        with patch.object(install.subprocess, 'run', side_effect=curl):
            with self.assertRaisesRegex(RuntimeError, 'Unsafe download file'):
                self.download()
        self.assertFalse(self.target.exists())
        self.assertEqual(self.victim.read_bytes(), b'keep me')

    def test_interruption_resumes_from_verified_descriptor(self):
        def interrupted(args, *, stdout, check):
            os.write(stdout.fileno(), self.payload[:5])
            raise subprocess.CalledProcessError(18, args)
        with patch.object(install.subprocess, 'run', side_effect=interrupted):
            with self.assertRaises(subprocess.CalledProcessError):
                self.download()
        self.assertFalse(self.target.exists())

        def resumed(args, *, stdout, check):
            self.assertEqual(args[args.index('--continue-at') + 1], '5')
            self.assertEqual(args[args.index('--output') + 1], '-')
            self.assertEqual(os.lseek(stdout.fileno(), 0, os.SEEK_CUR), 5)
            os.write(stdout.fileno(), self.payload[5:])
        with patch.object(install.subprocess, 'run', side_effect=resumed):
            self.download()
        self.assertEqual(self.target.read_bytes(), self.payload)
        self.assertFalse(self.part.exists())

    @unittest.skipUnless(shutil.which('curl'), 'curl required')
    def test_real_curl_fresh_and_resumed_downloads(self):
        source = self.root / 'source'
        source.write_bytes(self.payload)
        for prefix in (b'', self.payload[:5], self.payload):
            with self.subTest(prefix=prefix):
                self.target.unlink(missing_ok=True)
                self.seed(prefix)
                self.download(source.as_uri())
                self.assertEqual(self.target.read_bytes(), self.payload)
                self.assertFalse(self.part.exists())

    @unittest.skipUnless(shutil.which('curl'), 'curl required')
    def test_legacy_symlink_ignored_and_private_directory_created(self):
        self.private.rmdir()
        self.target.with_name('model.part').symlink_to(self.victim)
        source = self.root / 'source'
        source.write_bytes(self.payload)
        self.download(source.as_uri())
        self.assertEqual(self.victim.read_bytes(), b'keep me')
        self.assertEqual(self.target.read_bytes(), self.payload)
        self.assertEqual(stat.S_IMODE(self.private.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        with patch.object(install.subprocess, 'run') as curl:
            self.download()
            curl.assert_not_called()


if __name__ == '__main__':
    unittest.main()
