# Validation record

2026-11-04: Linux cloud workspace, Python 3.12.14. No desktop/game/profile interactions.

Passed locally:

- Full suite with `FOUNDRY_COMPANION_PYTHON37` pointing to the isolated interpreter: 722 tests collected, 708 passed, 14 Windows-only fixture tests skipped. Without that optional interpreter path, four additional cross-runtime tests skip.
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

The peer-hook batch adds 32 authentication tests. All pass on the current
interpreter with the optional separate-Python-3.7 server test enabled. Real
Python 3.7 runs 26 portable cases and skips six current-client cases. Tests cover
full pinned golden messages, mutual proofs, wrong keys, replay/reflection,
two-connection relay rejection, malformed frames, absolute deadlines, secret
redaction and no application access before authentication. This is an internal
source/regression review, not an external cryptographic audit or game qualification.
Secure key provisioning and qualified host/channel admission remain separate.
The combined correction/authentication source still requires its exact-head CI.

At peer-hook head 7344ed8829e2ab3ed8e2b473573f4932c968aa90, both Linux jobs
passed. Windows exposed one remaining negative-fixture reset variant when rejecting
a surplus authentication proof. The fixture now distinguishes an observed closed
exchange from a fully validated exact error envelope. Four deterministic
regressions cover every partial-response cutoff, EOF/reset/abort variants and
send/shutdown failure without retry; malformed success/extra frames, timeouts and
unrelated errors remain failures. Queue/record/submission counts must remain zero.
Production authentication and codecs were unchanged.

At corrected branch head 0cf5fe80e3e0b3154d5f7b7fb2ae2a8d52e7b253, all four
Windows/Linux Python 3.11/3.13 jobs passed, including native exclusive-bind
collision rejection and the peer-auth regressions. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41812882523

The source-only durable client journal adds 60 passing local regressions. Tests
cover immutable intent before exchange, receipt-only reconciliation, process
death, partial publication, callback reentrancy, competing processes/instances,
strict history bounds, terminal immutability and diagnostic redaction. A separate
review prompted fixes for token-shaped reason leakage, admission exception text
and malformed-storage traceback text. Adapter assertions never resolve a game
mutation or become independent evidence. The full local suite above passes;
the new journal's native Windows filesystem/locking tests still require CI.
There is no production executor or cross-workspace trusted history anchor, and
Windows power-loss durability remains unqualified. See
[COMPANION_JOURNAL.md](COMPANION_JOURNAL.md).

At journal branch head 87fc1855035bc46ff5e881087977a45b842db7a0, all four
Windows/Linux Python 3.11/3.13 jobs passed, including actual Windows ordinary
filesystem/byte-range-lock contention, callback reentrancy and crash-release
tests. These are process-crash tests, not Windows power-loss qualification.
A clean installation of the exact-source wheel also passed all 14 synthetic
workflow steps. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41815495665

The recovery batch adds 14 isolated integration tests composing exact durable
intent, peer authentication, actual loopback sockets and owner-thread dispatch.
Two use a separate actual Python 3.7.17 server. Controlled dropped replies and
socket timeouts preserve uncertainty; reopening the journal only permits reads
of the original receipt, never another submission. History loss, wrong keys,
process changes and fabricated context mismatches cannot produce an extra
dispatch. Cleanup checks account for owned server/client threads and processes.
Nine additional tests cover the passive `companion journal-status` CLI, unchanged
files, exact selection, missing/corrupt/busy history and the absence of mutation
or network routes. Both additions passed independent source review and the full
local suite above; their new exact-head Windows/Linux CI remains to be run.
See [COMPANION_RECOVERY.md](COMPANION_RECOVERY.md) for the test-only boundaries.

At recovery branch head 1feccc8a910f8eefa86ab96a3e460117454adf20, all four
Windows/Linux Python 3.11/3.13 jobs passed, including real authenticated
loopback recovery and passive journal CLI tests. An isolated, no-site-packages
import of the exact-source wheel also passed the 14-step synthetic workflow
and read-only journal status. No game bootstrap or live provider was installed.
Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41816735723

Native-input integration review found an accidental alias in the injected runner:
the caller and audit callback could retain and change the same target dictionary,
preventing release to the still-original window. The runner now detaches its
internal target and its audit copy, including nested geometry. Three regressions
cover caller/audit mutations before or after Down and changed/unknown/failing
integrity checks on release. Original Up still ignores recorder/geometry failure,
but never bypasses missing ownership or equal known integrity. All 23 focused
runner tests and the full local suite above pass; this is synthetic evidence,
not a native sender or game validation.

At release-binding head 45ee402a7c4bf2772d086f17d57ef9e9530b5d90, all four
Windows/Linux Python 3.11/3.13 jobs passed. Both push and pull-request workflows
were successful. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41817611227

The source-only native packet backend adds 42 tests: 38 portable cases pass and
four hosted Windows click/key/wheel/drag cases skip in this Linux workspace.
Source review prompted fixes for exception-chain redaction and detached admission
packet objects. Tests cover default denial, retained ownership, file-pin sharing,
input-desktop observability, exact layout/modifiers, recorder faults and one
original release after uncertainty. Full current-Python plus actual Python-3.7
companion integration passes as counted above. Actual native image pinning,
input-desktop observation and backend packet delivery still require exact-head
Windows CI. The public native entry always refuses and game qualification remains
absent. See [NATIVE_INPUT.md](NATIVE_INPUT.md).

At native-backend head 4cb97b50542df61fa362951f3ae5b228a96b6362, all four
Windows/Linux Python 3.11/3.13 jobs passed, in both push and pull-request workflows.
The Windows logs explicitly report all four new click/key/wheel/drag backend tests
as passed, not skipped. They exercise actual image pinning, input-desktop queries,
PostMessage packet receipts and unchanged cursor/foreground on freshly compiled
synthetic windows. This is still not Sims, a real recorder, game/UI lifetime
proof or production admission. The exact-source wheel also passed an isolated
no-site-packages import, the synthetic 14-step workflow, passive journal status
and unconditional public native-input refusal. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41818794815

A further cross-component review reproduced a late-success deadline defect in
the original transport. Six deterministic regressions now cover I/O completing
after the absolute deadline, late parsing, retained server history and client
journal uncertainty without replay. They assert exact failures, one send or no
admission where applicable, and cleanup. Three server cases also pass on actual
Python 3.7; three current-client cases intentionally skip there.

Native CAS regressions reproduced missing Sim-ID revalidation and target-helper
construction after the final context check. Target preparation now precedes the
last exact save/client/zone/household/Sim/instance checks inside the preservation
scope. Direct native calls also require captured host-declared session/process
binding and full mutation save preconditions before game service access. Typed
request and admission observation views are detached; host declarations and
arbitrary trusted Python hooks do not establish OS identity or live authority.
These changes remain uninstalled source, with no added game qualification.

The final native suite has 45 passing tests on both current Python and actual
Python 3.7. It additionally distinguishes admission TTL types (`5` versus `5.0`)
and performs one bounded post-admission context read, without another admission
call. Twenty-one final-hook context-drift cases across speed, quit and CAS now
refuse before native dispatch; CAS scopes unwind. Independent review and the
combined local suite above pass. Exact-head Windows/Linux CI remains required
for this source update.

At boundary-hardening head d1a58017cef607aeb4633cc84d84cecd02a951df, all four
Windows/Linux Python 3.11/3.13 jobs passed, in both push and pull-request
workflows. This includes the unchanged native packet fixtures and the new late
deadline/native-context regressions. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41822417000

The offline mod-update audit reproduced three staging defects: a manifest could
change an artifact's extension, file/ancestor collisions passed dry-run, and a
growing regular source could consume more than its declared extent before later
rejection. Staging now requires matching artifact suffixes, rejects casefolded
ancestor conflicts before destination creation, and bounds hashing/copying to the
declared extent plus one non-written probe. Valid same-format renames remain
supported. Sixteen additional tests cover these cases plus source immutability,
interrupted copies, no-overwrite receipts, symlinks, ZIP CRC and cumulative bounds.
Independent review and the local suite above pass; Windows-specific filesystem
behavior still requires exact-head CI. No game/profile installation or access
was used.

At offline staging head e1df93f28fb14b1ed042d2bf1570c4894ce6453d, all four
Windows/Linux Python 3.11/3.13 jobs passed in both push and pull-request
workflows. The exact-source wheel also passed a clean no-site import, the
synthetic workflow, passive journal status, default native denial and external
staging of a fabricated package. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41823502980

The follow-on audit found eager recursive sorting could scan unbounded irrelevant
entries before enforcing artifact limits. Inventory now uses non-following
directory-entry inspection, rejecting observed links/reparse points before
descent, with 16,384 total entries, 32 nested directory levels and 4,096 artifacts
as explicit caps. One overflow entry may be fetched to detect the bound, but is
not inspected or descended into. Directory handles close before opening another;
only bounded collected artifact rows are sorted. This does not make a changing
or hostile filesystem safe.

Manifest depth and UTF-16 length limits are checked before constructing ancestor
prefixes. Output paths for artifacts and both receipts, including their temporary
filenames and empty manifests, are limited to 259 UTF-16 units overall; created
directory paths are capped at 247. Components are limited to both 255 UTF-16 units
and 255 UTF-8 bytes before source I/O or directory creation. Fifteen additional
tiny-fixture regressions cover traversal, resource bounds, links, deterministic
output, path depth, exact accepted limits and output length. Independent review
reproduced and prompted fixes for temporary receipt overhead and multibyte POSIX
component limits.
These limits apply to offline mod inventory/staging, not ZIP member names or the
shared path helpers. Exact-head Windows CI remains required for this update.

At bounded inventory head 6b5c54f169f7a5d88d24bad07bcbc13115352608, all four
Windows/Linux Python 3.11/3.13 jobs passed in both push and pull-request workflows.
An isolated archive of the exact committed source passed 650 tests with 13 native
skips and the optional actual Python 3.7 integration enabled. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41826362465

The launch-metadata audit reproduced hashing before size checks, unbounded binary
hash reads, accepted growth during a small log read and a selected-record digest
over replacement-decoded text rather than source bytes. Preflight now checks all
regular-file extents before opening any source: 512 MiB per executable/launcher,
1 MiB manifest and a 2 MiB tail per selected log. Reads stay within the captured
extent plus an EOF probe; log tails additionally inspect one preceding delimiter
byte. Same-API stamps and final bounded hashes reject observed drift. Windows
cross-API identity uses explicit birth time where available, avoiding the known
stat/fstat ctime mismatch without dropping same-API change checks.

Selected request and successful-launch evidence are hashed as exact record bytes.
Malformed UTF-8 cannot become selected evidence. Truncated leading tail records
are discarded; unrelated malformed records retain their predecessor-window
position. Lazy line iteration and a fixed 30-record deque prevent newline-count
memory amplification while preserving latest-valid selection and rotation seams.
Twenty new tiny-fixture tests plus the eight earlier launch cases pass, alongside
independent review and the full local suite above. This remains read-only metadata
preparation, not launch execution or authority. Exact-head Windows CI is pending.

At launch-bound head ff5683ec37074e51bd45dda50c6ca45fed750e7a, both Linux jobs
passed but both Windows jobs failed before reading the fabricated executable.
CPython path-based stat adds filename-derived execute bits for `.exe`, `.bat`,
`.cmd` and `.com`, unlike descriptor-based fstat. The cross-API comparison now
reconciles only that exact Windows regular-file difference, with known suffix,
no descriptor execute bits and otherwise identical mode. Full original same-API
stamps, file type, identity, size, timestamps and attributes remain checked.
Sources: [CPython 3.11.14](https://github.com/python/cpython/blob/v3.11.14/Modules/posixmodule.c#L1925-L1938)
and [CPython 3.13.17](https://github.com/python/cpython/blob/v3.13.17/Modules/posixmodule.c#L1845-L1868).

Three portable regressions cover this narrow reconciliation and retained drift
checks. A fourth Windows-only test checks real stat/fstat for tiny fabricated
executable-named files without executing them. Independent review, 31 passing
local launch tests and the combined local suite above pass; the new native test
skips on Linux. Exact-head Windows CI must confirm the correction.

At corrected launch head 9e5cc019da44c577e687c4d9763a653fa93083a6, both push
and pull-request workflows passed all four Windows/Linux Python 3.11/3.13 jobs.
Both Windows logs explicitly passed the new native stat/fstat diagnostic and
the three portable regressions: 674 collected, 665 passed, 9 skipped on each.
No executable-named fixture file was executed. The exact-source wheel also
passed an isolated no-site import, the synthetic 14-step workflow, passive
journal status, default input denial, fabricated mod staging and exact-byte
launch-metadata preparation. Run:
https://github.com/Herbertofury/Sims4Foundry/actions/runs/41828121473

The uninstalled Build observation seam adds 48 tests, all passing on current
Python and actual isolated Python 3.7.17. The 45 earlier native-binding tests also
pass on both interpreters. It samples at most 32 explicit object IDs through
injected fabricated readers, with owner-thread/reentrancy checks, captured
session/process binding and exact host-declared zone/save context around reads.
The same host admission provider must explicitly approve detached Build context
and object selection; generic status-read admission cannot expand into Build.

Review prompted static callback-error redaction, narrower admission scope and
explicit request-bound, non-atomic snapshot labeling. Missing fields, invalid
quaternions/surfaces, object disappearance/reuse and mode changes stay unavailable
rather than becoming default values. Transition history has bounded retention and
never supplies current mode. No native objects survive between calls. Final
independent review and the combined suite above pass; exact-head hosted CI is
pending. No game imports, bootstrap, hooks, mutation or transport route were added.
See [BUILD_OBSERVATION.md](BUILD_OBSERVATION.md) for all retained limits.

[Live interaction source research](LIVE_INTERACTION_RESEARCH.md) records pinned
public-source identity and outcome caveats. It introduces no adapter or dispatch
support: tuning GUIDs are not queue-instance IDs, unavailable readers are not
empty-state proof, and enqueue/cancel returns or pre-call events are not verified
completion. Current installed-game behavior remains untested.
