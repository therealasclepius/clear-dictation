# Clear Dictation

A local dictation companion for Voxtype, with a native GTK app and an optional Omarchy bar plugin. **Public alpha: 0.1.2.**

Speak using your existing Voxtype shortcut. Clear Dictation cleans up the transcript before Voxtype inserts it, while preserving the original in local history.

Tested end to end on one Omarchy 4.0.3 system with Voxtype 1.0.1, Python 3.14.7, GTK 4.22.4, and Intel Core 5 320 graphics. Other machines and older Omarchy plugin APIs still need validation. This release is intended for early testers comfortable with terminal setup.

## Features

- **Natural:** remove false starts and filler while retaining your voice.
- **Polished:** smooth grammar for professional writing.
- **Literal:** bypass the language model and apply only your dictionary.
- Personal spellings for names, products, and technical terms.
- Last 100 original and processed transcripts, with copy and clear controls.
- A playground to preview cleanup without typing into another app.
- Original-text fallback if cleanup is unavailable, times out, or returns invalid output.
- Optional Omarchy bar widget: click to open the app, right-click to toggle recording.

## Requirements

Linux x86-64 with AVX2, Python 3.12+, GTK 4/PyGObject, systemd user services, wl-clipboard, curl, and a working Voxtype installation. The optional plugin requires Omarchy's Quickshell plugin API.

The default cleanup model is **Qwen3-4B Q4_K_M**, running through a pinned llama.cpp runtime with CPU or optional Vulkan graphics acceleration. Allow approximately 3 GB of additional RAM and 3 GB of free disk for installation. On a 16 GB laptop, keeping other large AI models open can cause memory pressure. Speech recognition remains configured in Voxtype.

## Install

With the requirements above installed:

```sh
git clone https://github.com/therealasclepius/clear-dictation.git
cd clear-dictation
python3 install.py --download-model --plugin
```

The download is approximately 2.5 GB. Files are verified against pinned SHA-256 checksums. Downloads and runtime extraction check available disk space first. Installation is user-local and requires no root access. It installs a desktop launcher, a user model service, and a Voxtype post-processing hook. Existing Voxtype settings are backed up; an unrelated existing post-processing command is never silently replaced.

The app appears as **Clear Dictation** in the application launcher. Add its optional widget through Omarchy's bar settings. Keep your existing Voxtype shortcuts. This installer does not claim Right Alt or change the built-in hotkey listener.

The installer enables `output.shift_enter_newlines` so generated paragraphs use Shift+Enter instead of Enter. Application behavior can vary. Voxtype's auto-submit setting is not enabled by this app.

For an existing local runtime/model installation, omit `--download-model`.

For a compatible GPU with Vulkan drivers installed:

```sh
python3 install.py --download-model --plugin --backend vulkan
```

The installer checks that Vulkan detects a device before changing the integration. Future updates retain the chosen backend. To switch back, run `python3 install.py --download-model --backend cpu`. Acceleration depends on your hardware; benchmark both if necessary.

## Use

```sh
clear-dictation ui
clear-dictation mode natural
clear-dictation mode polished
clear-dictation mode literal
clear-dictation dictionary "voice type" "Voxtype"
clear-dictation status
printf '%s' 'Tuesday, actually Wednesday at three.' | clear-dictation process
```

The model stays loaded between dictations. At service startup, a synthetic request primes the instruction cache without creating a history entry. On an Intel Core 5 320 laptop, Vulkan reduced warm cleanup of short sentences from approximately 2–5 seconds to 1.5–2.3 seconds, with the same Qwen3-4B model. A longer example dropped from 8.5 to 3.0 seconds. These are local measurements, not guaranteed timings. Startup still takes several seconds, and switching writing modes can require reprocessing instructions. Longer dictations may exceed the cleanup timeout and use the original transcript. The model request times out after 18 seconds; Voxtype's outer hook has a 25-second limit. An interrupted cleanup's original remains recoverable in history.

## Privacy and limitations

Audio stays with your local Voxtype engine. This app sends only text to a loopback-only model server protected by a private local token, follows no redirects, disables HTTP proxies, and has no cloud mode or telemetry. It does not inspect your screen or other applications. Model tools, file access, and shell execution are not enabled.

History is saved in `~/.local/state/clear-dictation/history.sqlite3` with private filesystem permissions. Clear it from the app. No audio is stored by this app. AI cleanup can still change meaning or make mistakes; use Literal for exact text and Copy original to recover the transcription. JSON validation and length limits are checks, not a guarantee of semantic fidelity. No context-aware app formatting or automatically learned vocabulary is implemented yet.

## Files and updates

- App: `~/.local/share/clear-dictation/app/`
- Models/runtime: `~/.local/share/clear-dictation/`
- Settings and dictionary: `~/.config/clear-dictation/config.json`
- History: `~/.local/state/clear-dictation/`
- Service: `clear-dictation-model.service`
- Plugin: `~/.config/omarchy/plugins/kosta.clear-dictation/`

Rerun the installer after updating source. Settings, dictionary, and history live outside the app code and are preserved. The app works independently of the optional bar plugin.

## Uninstall

```sh
python3 ~/.local/share/clear-dictation/app/install.py --uninstall
```

This removes the hook, launcher, service, and installed plugin. When the Omarchy CLI is available, the widget is disabled before removal. Models, history, dictionary, and backups are retained. Other Voxtype settings are preserved.

## Troubleshooting and feedback

If cleanup falls back to the original, check `systemctl --user status clear-dictation-model.service` and `clear-dictation status`. For Vulkan startup errors, retry installation with `--backend cpu`. Use Literal mode to keep dictating while investigating.

Update and uninstall stop if the managed Voxtype block was customized; preserve those custom settings outside the `CLEAR DICTATION` markers before retrying. Configuration files are replaced atomically, but installation as a whole does not yet provide transactional rollback for every service or filesystem failure. The original Voxtype backup is retained in the app data directory.

Report problems through [GitHub Issues](https://github.com/therealasclepius/clear-dictation/issues/new/choose). When reporting a bug, include OS, Voxtype version, CPU/GPU, backend, steps to reproduce, and a short synthetic dictation. Do not attach private transcript databases, personal dictionaries, or `model.key`.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m cleardictation ui
```

The core uses Python's standard library. GTK is imported only by the UI. Set `CLEAR_DICTATION_CONFIG` and `CLEAR_DICTATION_STATE` to isolate tests and experiments from real user data.

## Attribution

Clear Dictation code is MIT licensed. Voxtype is a separate MIT-licensed dependency. The runtime is [llama.cpp b10867](https://github.com/ggml-org/llama.cpp/releases/tag/b10867), MIT licensed. The cleanup model is [Qwen3-4B-GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF), under its upstream Apache-2.0 license. Models and runtime are downloaded separately rather than included in this project's source distribution.

This is an independent project and is not affiliated with Willow, Wispr, or the Omarchy maintainers.
