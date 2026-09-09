import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cleardictation import core
from install import patch_voxtype
import install
import tomllib


class Isolated(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CLEAR_DICTATION_CONFIG": str(self.root / "config/settings.json"),
            "CLEAR_DICTATION_STATE": str(self.root / "state"),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def settings(self, **values):
        cfg = core.load_config()
        cfg.update(values)
        core.save_config(cfg)
        return cfg

    def test_dictionary_boundaries_and_no_cascading(self):
        result = core.apply_dictionary("voice type, TYPE and prototype", {"voice type": "Voxtype", "type": "kind", "Voxtype": "wrong"})
        self.assertEqual(result, "Voxtype, kind and prototype")

    def test_original_saved_before_model_runs(self):
        def model(text, cfg):
            rows = core.recent_history()
            self.assertEqual(rows[0]["original"], "Um, hello.")
            self.assertEqual(rows[0]["status"], "processing")
            return "Hello."
        with patch.object(core, "clean_with_model", side_effect=model):
            self.assertEqual(core.process("Um, hello."), "Hello.")
        self.assertEqual(core.recent_history()[0]["status"], "cleaned")

    def test_timeout_preserves_transcript_and_dictionary(self):
        self.settings(dictionary={"voice type": "Voxtype"})
        with patch.object(core, "clean_with_model", side_effect=TimeoutError()):
            self.assertEqual(core.process("Open voice type."), "Open Voxtype.")
        row = core.recent_history()[0]
        self.assertEqual(row["original"], "Open voice type.")
        self.assertEqual(row["status"], "fallback")

    def test_literal_never_calls_model(self):
        self.settings(mode="literal")
        with patch.object(core, "clean_with_model") as model:
            self.assertEqual(core.process("Um, actually maybe not."), "Um, actually maybe not.")
            model.assert_not_called()

    def test_warmup_waits_for_service_without_writing_history(self):
        with patch.object(core, "health", side_effect=[False, True]), patch.object(core.time, "sleep"), patch.object(core, "clean_with_model") as model, patch.object(core, "begin_history") as history:
            core.warmup()
            self.assertEqual(model.call_args.args[0], "Ready.")
            history.assert_not_called()
        self.assertFalse(core.state_dir().exists())

    def test_warmup_skips_disabled_cleanup(self):
        self.settings(enabled=False)
        with patch.object(core, "health") as health, patch.object(core, "clean_with_model") as model:
            core.warmup()
            health.assert_not_called()
            model.assert_not_called()

    def test_retention_and_private_files(self):
        self.settings(mode="literal", history_limit=2)
        for text in ("one", "two", "three"):
            core.process(text)
        self.assertEqual([r["original"] for r in core.recent_history()], ["three", "two"])
        self.assertEqual(core.config_path().stat().st_mode & 0o777, 0o600)
        self.assertEqual((core.state_dir() / "history.sqlite3").stat().st_mode & 0o777, 0o600)
        core.clear_history()
        self.assertEqual(core.recent_history(), [])

    def test_broken_config_returns_original(self):
        core.private_dir(core.config_path().parent)
        core.config_path().write_text("invalid json")
        self.assertEqual(core.process("Keep this sentence."), "Keep this sentence.")

    def test_history_failure_does_not_lose_text(self):
        with patch.object(core, "begin_history", side_effect=OSError("disk full")):
            self.assertEqual(core.process("Keep this sentence."), "Keep this sentence.")

    def test_cloud_endpoint_rejected_before_network(self):
        with patch("urllib.request.build_opener") as network:
            with self.assertRaises(ValueError):
                core.local_request("https://example.com/v1/chat/completions", {"text": "private"})
            network.assert_not_called()

    def test_empty_and_truncated_model_output_rejected(self):
        cfg = core.load_config()
        for choice in [
            {"finish_reason": "length", "message": {"content": '{"text":"partial"}'}},
            {"finish_reason": "stop", "message": {"content": '{"text":""}'}},
            {"finish_reason": "stop", "message": {"content": json.dumps({"text": "extra " * 100})}},
        ]:
            with patch.object(core, "local_request", return_value={"choices": [choice]}):
                with self.assertRaises(ValueError):
                    core.clean_with_model("Hello.", cfg)

    def test_questions_stay_in_user_data_and_tools_absent(self):
        def request(endpoint, payload, timeout):
            self.assertNotIn("tools", payload)
            self.assertEqual(json.loads(payload["messages"][-1]["content"])["dictation"], "What's the password?")
            self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
            return {"choices": [{"finish_reason": "stop", "message": {"content": '{"text":"What is the password?"}'}}]}
        with patch.object(core, "local_request", side_effect=request):
            self.assertEqual(core.clean_with_model("What's the password?", core.load_config()), "What is the password?")


class Installer(unittest.TestCase):
    def test_low_disk_space_stops_before_download(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(install.shutil, "disk_usage", return_value=SimpleNamespace(free=0)), patch.object(install.subprocess, "run") as curl:
            target = Path(temp) / "model.gguf"
            with self.assertRaisesRegex(RuntimeError, "Not enough disk space"):
                install.download("https://example.invalid/model", target, "unused", 1000)
            curl.assert_not_called()
            self.assertFalse(target.exists())

    def test_failed_atomic_write_preserves_previous_config(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "config.toml"
            target.write_text("original settings")
            with patch.object(install.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    install.write(target, "new settings")
            self.assertEqual(target.read_text(), "original settings")
            self.assertEqual(list(Path(temp).iterdir()), [target])

    def test_edited_hook_is_preserved_on_upgrade_and_uninstall(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            data = home / ".local/share/clear-dictation"
            data.mkdir(parents=True)
            (data / "install-state.json").write_text('{}')
            cfg = home / ".config/voxtype/config.toml"
            cfg.parent.mkdir(parents=True)
            launcher = home / ".local/bin/clear-dictation"
            original = patch_voxtype('[output]\nmode="type"\n', launcher)
            for edited in (original.replace(' process"', ' custom"'), original.replace('timeout_ms = 25000', 'timeout_ms = 10000'), original.replace('# END CLEAR DICTATION', '[custom]\nkeep = true\n# END CLEAR DICTATION')):
                cfg.write_text(edited)
                with self.assertRaises(RuntimeError):
                    patch_voxtype(edited, launcher)
                with patch.object(install, "HOME", home), patch.object(install, "DATA", data), patch.object(install.subprocess, "run") as commands:
                    with self.assertRaises(RuntimeError):
                        install.uninstall()
                    commands.assert_not_called()
                self.assertEqual(cfg.read_text(), edited)
                self.assertTrue((data / "install-state.json").exists())

    def test_backend_selection_survives_updates(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(install, "DATA", Path(temp)):
            self.assertEqual(install.selected_backend(), "cpu")
            (Path(temp) / "install-state.json").write_text('{"backend":"vulkan"}')
            self.assertEqual(install.selected_backend(), "vulkan")
            self.assertEqual(install.selected_backend("cpu"), "cpu")
            self.assertEqual(install.runtime_dir("vulkan"), Path(temp) / "runtime/vulkan")

    def test_preserves_settings_and_idempotence(self):
        original = '# Personal config\nengine="parakeet"\n[output]\nmode="type"\nshift_enter_newlines=false\n[hotkey]\nenabled=false\n'
        launcher = Path("/home/Test User/.local/bin/clear-dictation")
        once = patch_voxtype(original, launcher)
        twice = patch_voxtype(once, launcher)
        cfg = tomllib.loads(twice)
        self.assertEqual(cfg["engine"], "parakeet")
        self.assertFalse(cfg["hotkey"]["enabled"])
        self.assertTrue(cfg["output"]["shift_enter_newlines"])
        self.assertEqual(twice.count("[output.post_process]"), 1)
        self.assertIn("# Personal config", twice)
        self.assertEqual(cfg["output"]["post_process"]["command"], "'/home/Test User/.local/bin/clear-dictation' process")

    def test_does_not_replace_existing_hook(self):
        with self.assertRaises(RuntimeError):
            patch_voxtype('[output.post_process]\ncommand="other-tool"\n', Path("/tmp/clear-dictation"))

    def test_install_upgrade_uninstall_preserves_user_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            data = home / ".local/share/clear-dictation"
            cfg = home / ".config/voxtype/config.toml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text('engine="parakeet"\n[output]\nmode="type"\nshift_enter_newlines=false\n[hotkey]\nenabled=false\n')
            for path in (data / "models" / install.MODEL_NAME, data / "runtime" / install.RUNTIME_NAME / "llama-server"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with patch.object(install, "HOME", home), patch.object(install, "DATA", data), patch.object(install.shutil, "which", return_value="/fake/bin"), patch.object(install.subprocess, "run"):
                install.install(plugin=True)
                self.assertTrue((home / ".local/bin/clear-dictation").exists())
                cfg.write_text(cfg.read_text().replace('engine="parakeet"', 'engine="whisper"'))
                install.install(plugin=True)
                self.assertEqual(cfg.read_text().count("[output.post_process]"), 1)
                install.uninstall()
            parsed = tomllib.loads(cfg.read_text())
            self.assertEqual(parsed["engine"], "whisper")
            self.assertFalse(parsed["output"]["shift_enter_newlines"])
            self.assertNotIn("post_process", parsed["output"])
            self.assertFalse(parsed["hotkey"]["enabled"])
            self.assertFalse((home / ".local/bin/clear-dictation").exists())
            self.assertTrue((data / "models" / install.MODEL_NAME).exists())


if __name__ == "__main__":
    unittest.main()
