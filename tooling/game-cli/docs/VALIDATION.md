# Validation record

2026-11-04: Linux cloud workspace, Python 3.12.14. No desktop/game/profile interactions.

Passed locally:

- Full suite with `FOUNDRY_COMPANION_PYTHON37` pointing to the isolated interpreter: 455 tests collected, 446 passed, 9 Windows-only fixture tests skipped. Without that optional interpreter path, two additional cross-runtime tests skip.
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
The native Windows APIs were not executed locally. Four additional CI-only tests
compile a tiny synthetic Canvas-window application on GitHub's disposable Windows
runners and exercise the actual query-only Win32 bindings, ambiguity rejection and
minimized-window rejection. One fixture also receives the encoded mouse/key/wheel/drag
messages and checks that sending them does not change foreground or OS cursor. This fixture is not Sims and never runs on the user's
desktop. Windows CI does not constitute a real-game test. No launcher, profile mutation, input, save or exit
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

At remote head 216e4dbd894eaa0437b07811e11c8928a3d7356a, all four Linux/Windows
Python 3.11/3.13 jobs passed, including three actual Win32 query tests against a
compiled synthetic fixture. Later additions (metadata-only launch preparation,
packet construction, backend-injected release guards and fixture message delivery)
require a fresh CI pass. No real game input was sent.

A later future-sender review added regression coverage for stale guards across
journal I/O, signed wheel targeting, direct-packet timing/path limits and deferring
optional journal records until paired release. Native backend calls must still be
bounded; the backend-injected runner cannot supervise a hung native call.

At remote head 84bf546aa674010df68d05bc788311e044045910, all four Windows/Linux
Python 3.11/3.13 jobs passed, including the fourth native fixture's exact message
receipt and unchanged OS cursor/foreground checks. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41802314102

The following source checkpoint added a strict framed companion protocol, an uninstalled
Python-3.7-compatible owner queue/native API adapter, and a cooperative process
lease. Its three extra Windows mutex fixture tests passed in the CI run below.
On actual isolated Python 3.7.17, all 63 companion queue/native tests passed.
The official source SHA-256 was
7911051ed0422fd54b8f59ffc030f7cf2ae30e0f61bda191800bb040dce4f9d2;
the interpreter bytecode magic is 420d0d0a. This tested fabricated services and
native call signatures, not Sims. No script-mod package was built or installed.
The interpreter is an offline compatibility tool only, not the CLI/runtime server.

Review also corrected command-module import ordering before mutation admission,
speed-result verification across save/mode context changes, and Windows liveness
checks that must not rely on the undefined running-process exit FILETIME.

At remote head 77c1bacf2cafa3f390ec2941cd358a66b7e3fc4d, all four jobs passed,
including real Windows mutex fixtures. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41804039829
A CLI wheel from that source tree built, installed into a clean isolated virtual
environment, and passed the 14-step synthetic workflow. It contains no installable
game companion. Wheel SHA-256:
89f27d2045db0ca61779d934dfdb2ef4755bce265d3766f66e656ac1b04fb401.

New passive process discovery has 43 tests plus two CLI-routing tests. Its extra
native Toolhelp ABI/enumeration test only observes the already-running Python
process on a disposable Windows CI runner; it launches nothing. At remote head
d2500f4762755381cf0865257d2a5567e479f4fd, all four Windows/Linux Python 3.11/3.13
jobs passed, including that native enumeration test. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41806148587

The resource batch adds 39 synthetic DBPF resource/audit tests, 21 operation
source-map tests and two CLI routing tests. DBPF coverage includes all shared
index-field combinations, conditional compression records, invalid extents,
duplicate owners, aggregate bounds, source replacement/stat/hash drift, and
descriptor cleanup if file-wrapper construction fails. All fixture bodies are
arbitrary fabricated bytes, not game assets. The source map exposes no dispatcher
or dynamic imports and never claims live qualification. On platforms that deny replacement of an
open file, the replacement-race fixture is skipped; refusal is not simulated as a
successful replacement.

At remote head e519c77f4ca1da2d89a7a345264a3a420ea6c4e4, both Linux jobs and
Windows/Python 3.11 passed. Windows/Python 3.13 correctly refused packages because
the initial cross-API stat comparison was too strict. CPython's documented
[Windows stat/fstat ctime discrepancy](https://github.com/python/cpython/issues/157671)
and the 3.13.17 implementation show that path ctime may mean birth time while
descriptor ctime means metadata change time. The correction compares explicit
birth time across the APIs and retains both full original stamps for subsequent
same-API drift checks; identity, attributes, size, mtime and both hashes remain
checked. Three new regressions pass locally. At corrected remote head
3bcc9ef01ca359c0136beec02774618fc48abd6e, all four Windows/Linux Python 3.11/3.13
jobs passed, including native process/window/mutex fixtures. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41808723915

The next source-only transport batch adds real isolated socket tests, including
a current client talking to a separate Python-3.7 server fixture. On actual
Python 3.7.17 using `-B -S -s`, the queue/native/transport suite runs 116 tests:
111 passed and five current-client/platform tests skipped. Owner admission now
precedes started; every later exception remains unknown. Reviews prompted fixes
for listener loss, thread/socket startup and cleanup uncertainty, and exclusive
Windows listener binding. The new native Windows collision test still needs CI.
No live host admission, durable journal integration or game bootstrap was installed
or qualified. See [TRANSPORT.md](TRANSPORT.md).

At transport head a3ad5f4e205057324d705415a1a333e1348384d7, Windows's actual
exclusive-bind collision test passed. CI exposed negative-fixture assumptions
about intentionally closed sockets: Linux may report ENOTCONN at shutdown and
Windows may report connection-aborted rather than connection-reset. The fixture
correction handles only explicitly opted-in closure errors, retains all zero-
admission/no-dispatch checks, and still rejects timeouts, unrelated errors and
partial/trailing response bytes. Six deterministic fixture regressions were
added; production transport behavior was not relaxed.

The peer-hook batch adds 28 authentication tests. All pass on the current
interpreter with the optional separate-Python-3.7 server test enabled. Real
Python 3.7 runs 22 portable cases and skips six current-client cases. Tests cover
full pinned golden messages, mutual proofs, wrong keys, replay/reflection,
two-connection relay rejection, malformed frames, absolute deadlines, secret
redaction and no application access before authentication. This is an internal
source/regression review, not an external cryptographic audit or game qualification.
Secure key provisioning and qualified host/channel admission remain separate.
The combined correction/authentication source still requires its exact-head CI.
