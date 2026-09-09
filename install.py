#!/usr/bin/env python3
"""User-local installer. Never modifies packaged Omarchy files."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
DATA = HOME / ".local/share/clear-dictation"
MODEL_NAME = "Qwen3-4B-Q4_K_M.gguf"
MODEL_SHA = "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"
RUNTIME_NAME = "llama-b10867"
RUNTIME_SHA = "e52005c40754ad0608b633b699d63972e11f56deaa1c0f011881eaa491d84bb5"
VULKAN_SHA = "5fe998ee6b06d65d80acdb036e6625ad9441d2bcd696011d9da68390959b04c1"
HOOK_BEGIN = "# BEGIN CLEAR DICTATION"
HOOK_END = "# END CLEAR DICTATION"


def checked_download_file(fd, directory, name):
    """Check the opened inode, never trust metadata from a followed pathname."""
    info = os.fstat(fd)
    entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o022
            or (info.st_dev, info.st_ino) != (entry.st_dev, entry.st_ino)):
        raise RuntimeError(f"Unsafe download file: {name}")
    return info


def download(url, target, digest, size):
    target.parent.mkdir(parents=True, exist_ok=True)
    parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            existing = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            pass
        else:
            with os.fdopen(existing, "rb") as src:
                checked_download_file(src.fileno(), parent, target.name)
                if hashlib.file_digest(src, "sha256").hexdigest() == digest:
                    return
            raise RuntimeError(f"Existing file has an unexpected checksum: {target}")

        # Ignore legacy adjacent .part files: their provenance is unknown.
        private_name = ".clear-dictation-downloads"
        try:
            os.mkdir(private_name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        directory = os.open(private_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise RuntimeError("Download directory must be owned by the current user with mode 0700")
            name = target.name + ".part"
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            except FileExistsError:
                fd = os.open(name, flags, dir_fd=directory)
            with os.fdopen(fd, "r+b") as part:
                # Serialize concurrent installers before inspecting or resuming.
                fcntl.flock(part.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                info = checked_download_file(part.fileno(), directory, name)
                if stat.S_IMODE(info.st_mode) != 0o600:
                    raise RuntimeError(f"Partial download must have mode 0600: {name}")
                remaining = max(0, size - info.st_size)
                if shutil.disk_usage(directory).free < remaining + 64 * 1024 * 1024:
                    raise RuntimeError(f"Not enough disk space to download {target.name}; need at least {remaining / 1024**3:.2f} GiB plus 64 MiB free. Partial downloads are kept for resuming.")
                # stdout is dup'd from our verified descriptor. Curl never opens
                # a writable pathname, including when resuming an interrupted file.
                part.seek(0, os.SEEK_END)
                subprocess.run(["curl", "--fail", "--location", "--retry", "2",
                                "--continue-at", str(info.st_size), "--output", "-", url],
                               stdout=part, check=True)
                part.seek(0)
                if hashlib.file_digest(part, "sha256").hexdigest() != digest:
                    raise RuntimeError(f"Checksum mismatch: {name}")
                checked_download_file(part.fileno(), directory, name)
                os.fsync(part.fileno())
                os.replace(name, target.name, src_dir_fd=directory, dst_dir_fd=parent)
                os.fsync(parent)
        finally:
            os.close(directory)
    finally:
        os.close(parent)


def selected_backend(backend=None):
    if backend is None:
        state = DATA / "install-state.json"
        backend = json.loads(state.read_text()).get("backend", "cpu") if state.exists() else "cpu"
    if backend not in ("cpu", "vulkan"):
        raise ValueError("Unknown model backend")
    return backend


def runtime_dir(backend):
    return DATA / "runtime" / "vulkan" if backend == "vulkan" else DATA / "runtime"


def install_model(backend=None):
    backend = selected_backend(backend)
    download("https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/" + MODEL_NAME, DATA / "models" / MODEL_NAME, MODEL_SHA, 2497280256)
    suffix = "-vulkan" if backend == "vulkan" else ""
    archive = DATA / "downloads" / (RUNTIME_NAME + suffix + ".tar.gz")
    download(f"https://github.com/ggml-org/llama.cpp/releases/download/b10867/llama-b10867-bin-ubuntu{suffix}-x64.tar.gz", archive, VULKAN_SHA if backend == "vulkan" else RUNTIME_SHA, 34421539 if backend == "vulkan" else 20 * 1024 * 1024)
    target = runtime_dir(backend)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        if shutil.disk_usage(target).free < sum(item.size for item in tar.getmembers()) + 64 * 1024 * 1024:
            raise RuntimeError("Not enough disk space to extract the runtime. Downloaded files were kept; free space and retry.")
        tar.extractall(target, filter="data")


def check_managed_hook(text, launcher):
    expected = shlex.quote(str(launcher)) + " process"
    marked = re.search(r"(?ms)^# BEGIN CLEAR DICTATION\n(.*?)^# END CLEAR DICTATION\n?", text)
    if marked:
        marked_config = tomllib.loads(marked.group(1))
        marked_hook = marked_config.get("output", {}).get("post_process", {})
        if marked_config != {"output": {"post_process": marked_hook}} or marked_hook != {"command": expected, "timeout_ms": 25000}:
            raise RuntimeError("The managed Voxtype hook was edited after installation. Move custom settings outside the CLEAR DICTATION block before updating or uninstalling; nothing was changed.")
    elif tomllib.loads(text).get("output", {}).get("post_process", {}).get("command") == expected:
        raise RuntimeError("Clear Dictation's hook markers are missing. Remove its post_process hook manually before updating or uninstalling; nothing was changed.")


def patch_voxtype(text, launcher):
    check_managed_hook(text, launcher)
    config = tomllib.loads(text)
    old = config.get("output", {}).get("post_process", {}).get("command", "")
    expected = shlex.quote(str(launcher)) + " process"
    if old and old != expected:
        raise RuntimeError("Voxtype already has a different post-processing command. Preserve or merge that integration before installing.")
    text = re.sub(r"(?ms)^# BEGIN CLEAR DICTATION\n.*?^# END CLEAR DICTATION\n?", "", text)
    # Scope newline behavior strictly to [output], never other tables.
    match = re.search(r"(?ms)^\[output\][^\n]*\n(.*?)(?=^\[|\Z)", text)
    if not match:
        text += "\n[output]\nshift_enter_newlines = true\n"
    else:
        block = match.group(0)
        if re.search(r"(?m)^shift_enter_newlines\s*=", block):
            block = re.sub(r"(?m)^shift_enter_newlines\s*=.*$", "shift_enter_newlines = true", block)
        else:
            block += "shift_enter_newlines = true\n"
        text = text[:match.start()] + block + text[match.end():]
    text += f"\n{HOOK_BEGIN}\n[output.post_process]\ncommand = {json.dumps(expected)}\ntimeout_ms = 25000\n{HOOK_END}\n"
    tomllib.loads(text)
    return text


def write(path, content, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o755 if executable else (path.stat().st_mode & 0o777 if path.exists() else 0o600)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".install-")
    try:
        with os.fdopen(fd, "w") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
            os.fchmod(out.fileno(), mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def install(plugin=False, backend=None):
    backend = selected_backend(backend)
    for cmd in ("voxtype", "python3", "wl-copy"):
        if not shutil.which(cmd):
            raise RuntimeError(f"Missing dependency: {cmd}")
    subprocess.run(["python3", "-c", "import gi; gi.require_version('Gtk', '4.0')"], check=True)
    model = DATA / "models" / MODEL_NAME
    server = runtime_dir(backend) / RUNTIME_NAME / "llama-server"
    if not model.exists() or not server.exists():
        raise RuntimeError("Local model/runtime missing. Run: python3 install.py --download-model")
    if backend == "vulkan":
        devices = subprocess.run([str(server), "--list-devices"], capture_output=True, text=True, check=True, timeout=15)
        if "Vulkan" not in devices.stdout:
            raise RuntimeError("No Vulkan graphics device found. Install a compatible Vulkan driver or use --backend cpu.")
    launcher = HOME / ".local/bin/clear-dictation"
    config = HOME / ".config/voxtype/config.toml"
    original = config.read_text() if config.exists() else ""
    patched = patch_voxtype(original, launcher)
    DATA.mkdir(parents=True, exist_ok=True)
    install_state_path = DATA / "install-state.json"
    if install_state_path.exists():
        state = json.loads(install_state_path.read_text())
    else:
        backup = DATA / ("voxtype-before-install-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".toml")
        write(backup, original)
        backup.chmod(0o600)
        state = {"backup": str(backup), "previous_shift_enter_newlines": tomllib.loads(original).get("output", {}).get("shift_enter_newlines"), "plugin_installed": False}
    installed = DATA / "app"
    installed.mkdir(exist_ok=True)
    for name in ("cleardictation", "omarchy"):
        shutil.copytree(ROOT / name, installed / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("install.py", "manifest.json", "README.md", "VALIDATION.md", "LICENSE"):
        shutil.copy2(ROOT / name, installed / name)
    write(launcher, "#!/bin/sh\nexec python3 " + shlex.quote(str(installed / "launch.py")) + ' "$@"\n', True)
    write(installed / "launch.py", "from cleardictation.__main__ import main\nmain()\n")
    write(HOME / ".local/share/applications/clear-dictation.desktop", f"""[Desktop Entry]
Type=Application
Name=Clear Dictation
Comment=Local voice cleanup, personal vocabulary, and transcript recovery
Exec=\"{launcher}\" ui
Icon=audio-input-microphone
Terminal=false
Categories=Utility;AudioVideo;
Keywords=dictation;voice;speech;voxtype;
StartupWMClass=local.cleardictation.App
""")
    # A wrapper keeps shell escaping separate from systemd's percent expansion.
    runner = DATA / "run-model"
    key = HOME / ".config/clear-dictation/model.key"
    key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key.parent.chmod(0o700)
    if not key.exists():
        fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(secrets.token_urlsafe(32) + "\n")
    key.chmod(0o600)
    write(runner, "#!/bin/sh\nexec " + " ".join(shlex.quote(str(x)) for x in [server, "--model", model, "--host", "127.0.0.1", "--port", "18089", "--api-key-file", key, "--cors-origins", "http://127.0.0.1:18089", "--ctx-size", "3072", "--parallel", "1", "--threads", "4", "--threads-batch", "4", "--no-webui", "--jinja", "--chat-template-kwargs", '{"enable_thinking":false}', "--sleep-idle-seconds", "-1"]) + "\n", True)
    service = HOME / ".config/systemd/user/clear-dictation-model.service"
    write(service, f"""[Unit]
Description=Clear Dictation local text cleanup model
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=\"{str(runner).replace('%', '%%')}\"
ExecStartPost=-\"{str(launcher).replace('%', '%%')}\" warmup
TimeoutStartSec=60
Restart=on-failure
RestartSec=5
Nice=5
MemoryMax=4G
UMask=0077

[Install]
WantedBy=graphical-session.target
""")
    write(config, patched)
    if plugin:
        plugin_dir = HOME / ".config/omarchy/plugins/kosta.clear-dictation"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "manifest.json", plugin_dir / "manifest.json")
        shutil.copytree(ROOT / "omarchy", plugin_dir / "omarchy", dirs_exist_ok=True)
        state["plugin_installed"] = True
    state["backend"] = backend
    write(install_state_path, json.dumps(state, indent=2) + "\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "clear-dictation-model.service"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "clear-dictation-model.service"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "voxtype"], check=True)
    print("Installed Clear Dictation. Launch it from your application menu.")
    if plugin:
        print("Omarchy widget installed. Add Clear Dictation using the bar settings.")


def uninstall():
    state_path = DATA / "install-state.json"
    if not state_path.exists():
        raise RuntimeError("No Clear Dictation installation record found")
    state = json.loads(state_path.read_text())
    config = HOME / ".config/voxtype/config.toml"
    text = config.read_text()
    check_managed_hook(text, HOME / ".local/bin/clear-dictation")
    text = re.sub(r"(?ms)^# BEGIN CLEAR DICTATION\n.*?^# END CLEAR DICTATION\n?", "", text)
    # Preserve all other post-install edits. Restore only our newline setting.
    match = re.search(r"(?ms)^\[output\][^\n]*\n(.*?)(?=^\[|\Z)", text)
    if match:
        if state["previous_shift_enter_newlines"] is not None:
            block = re.sub(r"(?m)^shift_enter_newlines[^\S\n]*=[^\S\n]*true[^\S\n]*$", "shift_enter_newlines = " + str(state["previous_shift_enter_newlines"]).lower(), match.group(0))
        else:
            block = re.sub(r"(?m)^shift_enter_newlines\s*=\s*true[^\S\n]*\n?", "", match.group(0))
        text = text[:match.start()] + block + text[match.end():]
    tomllib.loads(text)
    write(config, text)
    subprocess.run(["systemctl", "--user", "disable", "--now", "clear-dictation-model.service"], check=False)
    for path in (HOME / ".config/systemd/user/clear-dictation-model.service", HOME / ".local/share/applications/clear-dictation.desktop", HOME / ".local/bin/clear-dictation"):
        path.unlink(missing_ok=True)
    if state.get("plugin_installed"):
        if shutil.which("omarchy"):
            subprocess.run(["omarchy", "plugin", "disable", "kosta.clear-dictation"], check=False)
        plugin_dir = HOME / ".config/omarchy/plugins/kosta.clear-dictation"
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
    state_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "voxtype"], check=True)
    print("Integration removed. Your model files, settings, history, and backup were kept.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-model", action="store_true", help="Download the pinned local model and runtime, then install")
    parser.add_argument("--plugin", action="store_true", help="Also install the optional Omarchy bar widget")
    parser.add_argument("--backend", choices=("cpu", "vulkan"), help="Model acceleration; defaults to the previous selection, or CPU for new installs")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    try:
        if args.uninstall:
            uninstall()
        else:
            if args.download_model:
                install_model(args.backend)
            install(args.plugin, args.backend)
    except Exception as error:
        parser.exit(1, f"Clear Dictation: {error}\n")
