# Version 0.1.2 alpha validation

- 19 automated tests cover history recovery/retention/permissions, dictionary matching, literal mode, failure fallback, local-only routing, response validation, install/update/uninstall behavior, persistent backend selection, and startup cache priming without history.
- Real Qwen3 cleanup resolved a corrected meeting day, removed an abandoned introduction after “scratch all this,” removed filler, preserved a negative commitment, and retained a question as a question.
- An audio clip was routed through a temporary PipeWire sink to the running Voxtype daemon. Parakeet transcribed it, Clear Dictation processed it, and Voxtype wrote the result to a test file. History confirmed successful cleanup in 4.45 seconds. The temporary sink and test history entry were removed afterward.
- GTK app rendering inspected on the desktop.
- Omarchy manifest validation passed; widget geometry was confirmed live in the bar, and shell invocation opened the app.
- Both user services verified active. The local model rejects requests without the installation's private token.

These are functional checks, not a general accuracy benchmark. Local cleanup can alter meaning, and the first request after loading is slower. Keep original recovery available and use Literal for exact transcription.

## Cleanup latency update (2026-09-09)

Measured the same seven synthetic dictations using Qwen3-4B Q4_K_M and llama.cpp b10867 on an Intel Core 5 320 with Intel WCL graphics. Temperature zero; natural mode; unchanged prompt and validation. Six short examples took 2.3–4.7 seconds on CPU. After the first request, Vulkan took 1.6–2.3 seconds; the initial uncached Vulkan request took 3.9 seconds. A longer example took 8.5 seconds on CPU and 3.0 seconds on Vulkan. Runtime startup is excluded. These single runs on a working desktop are directional measurements, not a controlled hardware benchmark.

The seven Vulkan outputs retained the correction, restart, negative commitment, question, ordinary “actually,” budget contrast, and longer message. The much smaller Qwen3-0.6B was rejected after it substituted example text and dropped content. N-gram speculative decoding was also rejected because it increased latency on this device. The production setup retains Qwen3-4B and uses Vulkan without speculation.

The model now stays loaded and primes the current mode's instructions on service startup. This avoids the prior ten-minute idle unload/reload cycle; it does not guarantee residency under arbitrary system memory pressure. Original history, dictionary, timeout fallback, and the Parakeet speech model are unchanged.

After installation, systemd confirmed successful startup cache priming. The installed Voxtype hook passed four correction/question checks with isolated temporary history and no fallback. Both services were active, and the Omarchy plugin manifest passed validation.

## Public alpha review

- Added installer checks for download/extraction disk space, atomic config writes, and refusal to overwrite or remove customized managed hooks. Regression tests cover low space, failed file replacement, and edited hooks during update/uninstall.
- Added GitHub Actions for Python 3.12, 3.13, and 3.14. All three jobs passed on [GitHub Actions](https://github.com/therealasclepius/clear-dictation/actions/runs/34331246560); the local suite also passed on Python 3.14.7. These tests mock external service operations and do not establish real desktop compatibility.
- Source and archive reviewed for user-specific paths and accidental transcript/model/token inclusion. Distribution contains source only.
- Still needed before a stable release: a fresh install/update/uninstall on another Omarchy machine, broader microphone/application and CPU/GPU testing, and failure recovery across partially completed installations.
