# Contributing

This is an early alpha. Small fixes with a reproducible example are welcome.

Run `python3 -m unittest discover -s tests -v` from the repository root. The tests use temporary directories and mock system services; no model download or live desktop is required. GitHub CI runs this suite across Python 3.12–3.14 using the official [checkout](https://github.com/actions/checkout) and [setup-python](https://github.com/actions/setup-python) actions.

For cleanup changes, also test the real model on synthetic examples containing spoken corrections, questions, names, numbers, and negatives. Report both latency and meaning changes. Do not add real personal dictations to fixtures.

For installer changes, verify fresh install, upgrade, and uninstall on a disposable Omarchy user account. Preserve existing hotkeys, unrelated Voxtype settings, and private local data. Report the tested OS, Voxtype, and backend versions. A passing unit suite alone does not prove desktop integration works.
