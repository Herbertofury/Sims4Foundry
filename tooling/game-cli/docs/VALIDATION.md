# Validation record

2026-11-04: Linux cloud workspace, Python 3.12.14. No desktop/game/profile interactions.

Passed locally:

- `python -m unittest discover -s tests -v`: 63 tests.
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

Initial CI passed both Linux jobs and exposed a Windows-only test-fixture issue:
Python's ZIP writer and reader normalized a deliberately hostile backslash name
before the validator saw it. The fixture now patches raw member-name bytes, and
the validator checks ZipInfo.orig_filename before any normalized alias. A separate
regression test simulates Windows sanitization on every platform. Cross-platform rerun is
required for the new source head. Strict terminal-result validation and empty-intent
crash recovery also have dedicated regression tests.
