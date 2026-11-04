# Validation record

2026-11-04: Linux cloud workspace, Python 3.12.14. No desktop/game/profile interactions.

Passed locally:

- `python -m unittest discover -s tests -v`: 57 tests.
- `python -m compileall -q src`: source compilation.
- CLI help, capabilities and historical Apex AST inventory.
- The included 14-step CAS/Live/Build/save/reload/exit workflow, explicitly synthetic.

Tests cover typed/malformed arguments, state/mode gates, unsupported raw input,
live-backend and qualification forgery, plan hashes/expiry/state drift, uncertain
dispatch without replay, receipt persistence failures, crash-released OS locks,
strict JSON and bounds, unsafe paths, DBPF signature/ZIP member+CRC checks,
source drift and verified staging copies, PID/HWND/geometry identity changes,
helper pinning, post-capture identity changes, valid/truncated PNGs and CLI exits.

The fake capture runner writes an actual fixture PNG. It does not launch FFmpeg.
The native Windows APIs were not executed locally. Windows CI likewise does not
constitute a real-game test. No launcher, profile mutation, input, save or exit
validation took place. See QUALIFICATION.md for all outstanding live gates.

A separate code review found and prompted fixes for persistent crash locks,
partial receipt publication, inconsistent JSON bounds, nonfinite exponent parsing,
Windows reserved-path names, and shallow PNG validation. Windows power-loss
ordering remains explicitly unqualified.
