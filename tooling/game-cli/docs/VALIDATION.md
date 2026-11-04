# Validation record

2026-11-04: Linux cloud workspace, Python 3.12.14. No desktop/game/profile interactions.

Passed locally:

- Full suite with `FOUNDRY_COMPANION_PYTHON37` pointing to the isolated interpreter: 1,084 tests collected, 1,070 passed, 14 Windows-only fixture tests skipped. Optional cross-runtime checks are reported as skipped when their configured reference interpreter is unavailable.
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

At Build-observation head 89d4ff93fa4aa1da95cc1198035758f130200117, all four
Windows/Linux Python 3.11/3.13 jobs passed in both push and pull-request workflows.
The Windows 3.13 log explicitly passed all 48 Build-observation tests, with 722
tests collected and 9 total skips. The PR head matched the source checkpoint.
Run: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41829895044

The pure Live fixture contract adds 28 tests for explicit availability, ordered
queued/running rows, duplicate tuning IDs, transient instance references,
snapshot-local handles, detached aliases and bounded malformed data. All IDs
use canonical positive uint64 decimal strings, including lossless JSON round
trips around 2**53 and at uint64 maximum. Review prompted this representation
instead of JSON numeric IDs, and explicit machine-readable handle scope.
Every result stays offline-fixture, non-atomic, unqualified and unknown-outcome.
No native reader, event, action, clock, CLI or protocol route was introduced.

The existing two-OS CI matrix also adds Python 3.14, preserving action pins,
permissions and test commands. Six hosted jobs now need exact-head verification;
the preceding local run was Python 3.12, not evidence of Python 3.14 support.

At Live-fixture head 6fa1888138e19fba9be10c2c8ce5cd9c9bcc2068, all six push
jobs passed. Setup logs identify Python 3.14.10 on Windows and 3.14.9 on Linux.
The exact-source wheel also passed isolated import, synthetic workflow, journal,
staging, launch-metadata and lossless Live-fixture checks. However, the PR workflow
failed one existing Windows 3.11 receipt-recovery test after authenticated
dispatch. The journal reported uncertainty and did not replay. Its test helper
hid the underlying callback cause, so the historical cause remains unproven.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41831912433
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41831921361

The test-only correction retains bounded callback type/code/errno diagnostics
without exception text. Happy-path exchanges use the existing four-second fixture
budget, still below the five-second server deadline; explicit short timeout and
EOF-loss cases are unchanged. A client-only virtual clock, anchored at exchange
start, advances exactly 2.5 seconds after parsing a real authenticated receipt and
EOF. This reproducibly distinguishes the two- and four-second fixture budgets
without claiming to reproduce the historical Windows cause. The short case stays
uncertain with one dispatch and no replay. Production limits, journal behavior
and cleanup remain unchanged. All 17 recovery cases, including actual Python 3.7
subprocesses, and the full local suite above pass. Exact-head hosted CI is pending.

At recovery-fixture head 7638a95a512edea05bea14f5dec6a0c585a4248f, all six
Windows/Linux Python 3.11/3.13/3.14 jobs passed in both push and pull-request
workflows. The Windows 3.11 PR log explicitly passed the formerly failing
receipt-recovery case and all three new diagnostic/deadline regressions.
Windows collected 753 tests with nine skips; the local run above used actual
Python 3.7 for the optional subprocess fixtures. Runtime source was identical
to the preceding wheel checkpoint; this correction changed tests and docs only.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41834125645
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41834135907

The script-archive preflight adds 30 tiny-fixture tests. A three-entry ZIP
reproduces the original stdlib allocation order before a caller count limit.
The correction bounds the central directory to 4 MiB and counts at most 4,096
actual records before constructing ZipInfo objects. Immutable validated metadata
and a same-handle fixed-length view keep a changed directory/EOF from reopening
that allocation gap. Stored and Deflate are the deliberately supported methods;
ZIP64 central/end metadata, split disks and inferred offsets are refused. A
local-only ZIP64 header with bounded classic central metadata remains supported.

Independent review also reproduced malformed Deflate escaping as raw zlib.error;
a tiny invalid-block regression now verifies a static structured refusal. Tests
cover dishonest counts, exact/one-over bounds, malformed metadata, source drift,
short reads, unsupported decompressors and retained staging behavior. The full
local suite above passes with actual Python 3.7 subprocess fixtures enabled.
This checks fabricated archives without extraction or execution, not game
compatibility or malware safety. Exact-head hosted CI is pending. See
[SCRIPT_ARCHIVE.md](SCRIPT_ARCHIVE.md) for format and concurrent-writer limits.

At script-metadata head 857bbf9aa01dd93e81105d9666affd98a114c74d, all six
Windows/Linux Python 3.11/3.13/3.14 jobs passed in both push and PR workflows.
The Windows 3.14 PR log explicitly passed 29 new archive tests and skipped the
POSIX-only open-file replacement fixture: 783 tests collected, 10 total skips.
The isolated wheel passed synthetic workflow, journal, default input refusal,
launch-metadata, Live-fixture, real fabricated ZIP/CRC checks, metadata-bound
refusal and never-in-place mod staging. It contains no installable companion.
Wheel SHA-256:
68f1d214882daa7078c68948171f880700afc71c1e345d882e0b6ca8774d64a8.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41837262718
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41837272266

The opt-in bytecode-header report adds 27 tests and preserves ordinary artifact
inspection/staging contracts. Full CRC validation precedes retained 16-byte
prefix reads through the same bounded archive view; source drift checks follow
collection. Target comparison is independent of reference layout parsing, with
explicit flags-2 loader divergence, unknown layouts and non-vacuous comparison
counts. Narrow row/name budgets and the actual indented JSON size bound precede
publication. Exit zero can contain mismatches and is not a compatibility verdict.

Independent review passed the 108 focused archive/mod/report tests plus ten
extra adversarial review cases covering 294 header/compression/length combinations,
JSON escaping, short reads, CRC failures, source mutation and output bounds. A
separate check also compiled only original harmless fixture source with official isolated
CPython 3.7.17, then inspected a Deflate archive of timestamp, checked-hash and
unchecked-hash results without importing or executing them. Their prefixes match
the pinned reference fields. No real mod, installed game runtime or compatibility
was tested. The combined local suite above passes; exact-head hosted CI is pending.

At bytecode-report head c062d2e7a2e67d0d2ce52d7e697786c1c42af125, all six
Windows/Linux Python 3.11/3.13/3.14 jobs passed in both push and PR workflows.
The Windows 3.14 PR log explicitly passed all 27 new header-report tests: 810
tests collected, 10 total skips. The isolated wheel passed existing smoke checks,
the new CLI report, and all three actual CPython 3.7.17-compiled reference modes.
No candidate code was imported, unmarshalled or executed during inspection.
Wheel SHA-256:
3bf54387ebefbe55e164a15be93be6e2f9b58acf7f8b45d6800709a565202065.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41839717233
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41839727419

The native mode audit reproduced a source-contract gap with fabricated services
on current Python and actual Python 3.7: unchanged running-zone/Build flags could
produce a verified speed result after a hidden-mode change, even when generic
admission checked Live before dispatch. This does not demonstrate that the same
state combination occurs in the installed game. A separate fabricated mode hook
also reproduced direct API reentrancy reaching two commands inside one outer call.

The correction requires a typed, full-request/client-bound declaration from the
same admission provider, correlated to a fresh opaque per-sample token. Generic
permission cannot fabricate exact mode. Guards run before/after imports and around
CAS preparation, including immediately after scope entry before target construction;
post-mode native reads catch observed context changes, and speed
verification uses the final clock rather than stale pre-callback data. A per-
instance busy guard unwinds in finally. Post-speed missing/non-Live/invalid mode
remains unverified and latches unresolved mutation without replay. Providers remain
trusted, synchronous and separately unqualified; finite sampling is not atomic UI
proof and the token is not authentication.

Thirty-two new mode-boundary tests and the 45 original native tests pass. The direct
Python 3.7 native/mode/queue/Build aggregate passes all 177 tests. Independent
review and the complete local suite above pass; exact-head hosted CI is pending.
No mode source, bootstrap, game install or game-facing execution was added.

At explicit-mode head 78af6c693af7bf5493e14e834aedc80a4c2a9645, all six
Windows/Linux Python 3.11/3.13/3.14 jobs passed in both push and PR workflows.
The Windows 3.14 PR log explicitly passed all 32 new mode-boundary tests: 842
tests collected, 10 total skips. Independent review also passed 58 extra
fabricated checks on current Python and actual Python 3.7. The host CLI source
and packaging inputs are unchanged from the preceding validated wheel; this
checkpoint changes uninstalled companion source, tests and documentation only.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41843368204
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41843381808

The uninstalled Live queue observer adds 42 fabricated tests. It admits one
explicit actor only through the same host provider's separate Live-read scope,
with captured owner/process/session and exact host-declared mode/save/generation
context. Queued and running traversal shares a 64-row limit before materializing
native iterables; one overflow probe reads no fields. Missing actors, channels
and fields stay unavailable rather than empty or invented. Ordered duplicate
occurrences remain distinct without durable selection or cancellation authority.

Context/owner checks surround every ordinary reader/property/iteration step,
including errors and exhaustion; control exceptions unwind without more reader
work. Detached primitive results are capped at 32 KiB and always state unknown
outcome, false qualification/evidence/atomicity, and call-local identity scope.
Tests explicitly demonstrate an undetectable between-sample ABA transition, so
passing tests cannot imply atomic readback or qualified native semantics.

The 219-test Live/Build/native/mode/queue aggregate passes on current Python and
actual isolated Python 3.7. Independent review passed 29 additional adversarial
tests on both interpreters; the full local suite above passes. Existing native,
Build, queue, transport, host CLI/fixture and packaging source remain unchanged.
No real adapters, game imports, bootstrap, transport/CLI route or native action
were added. Exact-head hosted CI is pending. See
[LIVE_NATIVE_OBSERVATION.md](LIVE_NATIVE_OBSERVATION.md).

At Live-reader head 6d5931bb8cc06007f9557407d43eb92daa8bea21, all six
Windows/Linux Python 3.11/3.13/3.14 jobs passed in both push and PR workflows.
The Windows 3.14 PR log explicitly passed all 42 new reader tests: 884 tests
collected, 10 total skips. The PR remained draft at the matching source head.
Host CLI/packaging inputs remain identical to the earlier validated c062d2e
wheel; the new companion reader is deliberately not bundled into that wheel.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41846054259
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41846063134

Sixteen new cross-component observation tests exercise the real source readers,
owner queue, both wire codecs, host receipt parser and temporary synthetic journal.
Maximum source DTOs can fit the byte budget while exceeding the queue's structural
limit; availability fields and wrapper metadata change the exact threshold.
After a started read returns an oversized result, the queue records unknown and
retains no rejected DTO. Same-ID attempts do not execute or send again. Small
accepted observations preserve unavailable markers and exact IDs through both
codecs, while the journal deliberately retains only a summary and receipt digest.

All 16 new tests pass on current Python. On actual Python 3.7, 12 pass and four
host-only checks skip; the combined Build/Live/queue/budget group runs 158 tests
with the same four host-only skips there, and all 158 pass on current Python.
The full local suite above passes. This test/documentation checkpoint changes no
production code, routes, limits or authority. Exact-head hosted CI is pending.
See [OBSERVATION_TRANSPORT.md](OBSERVATION_TRANSPORT.md) for wrapper-specific
thresholds and the remaining integration contract.
Independent review found no blocking issues. Six extra adversarial probes passed
on both runtimes, including deferred completion under lock contention; six
actual cross-runtime frames passed type-aware roundtrips in each direction.

At observation-budget head d4c04a70aefde3b21479351d7bb17ad33959a462, all six
push jobs and five of six PR jobs passed. Every new observation-boundary test
passed in the inspected Windows 3.14 PR log (900 collected, 10 skips). The
Windows 3.11 PR job failed two existing recovery tests: one masked an absent
reply with IndexError; the other recorded peer_auth_failed before expected
dispatch. This checkpoint is not fully green. The root cause is being investigated;
no production authentication or deadline policy has been relaxed.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41850712880
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41850725708

The resource-update comparison adds 41 offline tests. It hashes validated opaque
stored ranges during the existing second same-handle source pass, binds parsed
header/index slices to that pass, and rechecks both snapshots before output.
Exact keys determine additions/removals; changed common rows independently flag
stored-byte, metadata and layout changes. Unchanged rows remain counted, and
oversized complete reports refuse without truncation. Same-path/hardlink version
comparisons are allowed without changing overlap-owner rejection.

The 177 focused resource/mod/CLI regressions and full local suite above pass.
Tests include maximum-entry unchanged inputs, opaque compressed bytes, tiny
chunk boundaries, hidden-stat metadata restoration, source drift and CLI
publication protections. No resource decoding, installation or game access was
performed. Independent review and exact-head hosted CI are pending.
Independent resource-diff review found no reproducible defect. Separate probes
covered 240 generated comparison pairs, 1,000 range/chunk partitions, 100 short-
read cases, 300 mutated fixtures, one-byte metadata-binding attacks, and 80
old/new public-report comparisons. These remain offline synthetic evidence.

A test-only recovery correction now requires the exact injected post-reply
exception, one accepted reply and one verified fabricated dispatch. Pre-reply
authentication errors can no longer satisfy that case or become an empty-list
IndexError. Three extra regressions exercise auth-read reset/timeout injection and
bounded, cycle-safe cause/context diagnostics. Only sanitized type/code/errno
fields are retained, never raw exception text or protocol data. All 20 focused
recovery tests pass, including both actual Python 3.7 server cases. The original
Windows authentication failure's underlying cause remains unknown; production
authentication, timeouts, no-replay and mutation policy are unchanged.

The final combined local suite passes all 944 collected cases with 14 Windows-only
skips. Recovery-only independent review also passes all 20 cases and three
no-socket diagnostic probes, including exact exception identity and bounded
message-free causal inspection. New exact-head CI is required for this combined
checkpoint; the preceding Windows authentication cause is not claimed resolved.

At resource-diff/recovery-diagnostic head
ba9e7e5cd23f6715c3ab8cf70aa0f6fdf2951c15, all six Windows/Linux Python
3.11/3.13/3.14 jobs passed in both push and PR workflows. The Windows 3.11 and
3.14 PR logs collected all 41 resource-diff and 20 recovery tests:
40 resource-diff cases passed and the open-file replacement case skipped
because Windows prevented replacement; 18 recovery cases passed and two optional
Python 3.7 server cases skipped there. Overall: 944 collected, 11 skips. This clean run does not establish the cause of the
previous intermittent authentication failure; the stronger fault assertions and
sanitized context diagnostics remain in place.

The wheel built from the exact matching source tree passes isolated no-site
smoke tests for the 14-step synthetic workflow, passive journal inspection,
default native-input refusal, staged fabricated mods, bounded script headers,
resource update comparison, and unchanged actual Python 3.7 reference headers.
The uninstalled companion readers are not bundled. Wheel SHA-256:
9e3395d807c0ef2774ee8acd4d445f144fe78c0e619942d488e046fa88b4a909.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41852658881
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41852665237

## Source-only request-bound Live read checkpoint

The separate schema-3 path connects one explicit actor and complete process,
zone, household, client, save and generation context through the source reader,
owner queue, strict result/receipt validation and the client intent journal.
It introduces no native provider, installed companion, production permission
policy, CLI live command or schema-3 network sender. The 49-row whole-read bound
fits the unchanged queue limits; standalone readers keep their existing bounds.

The final local suite collects 1,043 cases: 1,029 pass and 14 Windows-only cases
skip. A selected queue/Live protocol/reader/executor/route/native/mode aggregate
runs on actual isolated Python 3.7.17: 248 collected, 238 passed and 10 explicit
host-only skips. The full current-host suite also runs both optional real
Python 3.7 recovery servers. Handler-only schema-3 tests use fabricated readers
and owner ticks with sockets disabled; codec subprocess checks are not network
or game validation.

The two new cross-runtime fixtures use only the explicitly configured
`FOUNDRY_COMPANION_PYTHON37`, verify the child is Python 3.7, and disable site
imports/bytecode writes. No developer-machine path is embedded or inferred.
The legacy sender compatibility test uses a valid port and explicit fixture
policy, confirming `invalid_request` for both v3 message types before any socket.

Independent protocol/journal review passed 105 authored cases plus 12 protocol
and 12 journal probe groups. Its actual Python 3.7 run passed 17 authored cases
with six host-only skips and 10 independent groups with two host-only skips.
Shared schema copies remain byte-identical. This review prompted strict attempt
field checks, class-captured serialization, separate callback views and rejection
of impossible schema-3 verified summaries during journal replay.

Owner review identified three issues: mutation completion classification,
retained public snapshot aliases, and legacy read-to-mutation intent changes.
The implementation now retains original classification and complete primitive
intent, reconstructs distinct callback views, and deeply detaches public result
containers. Legacy transport policy views receive the same canonical-baseline
protection as the new path. Authored regressions and all three saved reviewer
reproductions pass on current Python and actual Python 3.7.

**The final independent owner/transport re-review is incomplete.** The first two
fixes were independently reproduced before the third issue was found; the last
fixes and additional transport changes have ordinary regression/compatibility
evidence, not a completed final independent assessment. Exact-head hosted CI is
pending for this checkpoint. No approval, installation or real-game qualification
follows from these local results.

An isolated no-site wheel smoke test passes the 14-step synthetic workflow,
new typed Live-read journal admission/receipt/no-replay path, default input
refusal, fabricated mod staging, resource comparisons and script headers.
The three actual Python 3.7-compiled reference headers still match. The wheel
contains no installable companion. It was built from production source at
cade849f840d6e87065724470268bf2358a80603; subsequent portability corrections
change only tests and this validation record. Wheel SHA-256:
83b7b2a3ee5e4b8495c63d9f40ac9b16c8411c83ebce089105555e95060f0920.

## Separate Live host exchange checkpoint

The new `companion_live_transport` adds a low-level schema-3 sender; the preceding
checkpoint did not have one. It leaves legacy transport, authentication, journal,
owner queue and observer implementations unchanged. There is still no CLI live
switch, production admission/key provider, installed listener or game validation.

Forty-one new tests cover deterministic fake sockets and owned authenticated
fixture peers. Eleven cases run an actual configured Python 3.7 child with its
version checked and site imports/bytecode writes disabled. They cover accepted
before owner execution, complete 49-row and whole-unavailable 50-row results,
exact receipt reads, wrong authentication bindings, missing/invalid final
confirmation, real reply loss/timeout, and a post-reply callback failure whose
specific injected exception and prior milestones must be observed.

The full local suite passes 1,084 collected cases with 14 Windows-only skips.
The focused Live/auth/transport/journal/recovery run passes 289 collected cases
with one Windows-only skip. Missing configured Python 3.7 results in explicit
optional skips, not reference-runtime success. Journals retain only summary and
digest, lost history never permits resubmission, and a Live read cannot resolve
an earlier mutation's uncertainty. All readers and keys are fabricated.

Independent review of this new host module found no blocking correctness issue:
14 existing fake-socket tests and eight additional probe tests passed. It checked
typed values, receipt/framing and deadline behavior plus owned-resource cleanup;
it did not independently rerun authenticated sockets or Python 3.7 child suites.
Exact-head hosted CI is pending. The earlier final independent owner/transport
re-review remains incomplete and is not replaced by this separate feature's
tests or review.

The isolated no-site wheel built from
fa3a6eb4c7e8613cdac5873c9963c83675f2901c passes the existing packaging smoke
workflow plus default schema-3 sender denial before socket creation for both
submit and receipt-read types. Actual Python 3.7 reference-header checks still
pass. No companion is bundled or installed. Wheel SHA-256:
a20d1f53fa715bd2eb51a3f51030a9c6eea45073d6b796603f6419c44f1e25ce.

At request-bound Live head cedbe4d875140c7f88ed642fb33ad013ffaa13bb, all six
Windows/Linux Python 3.11/3.13/3.14 jobs passed in both push and PR workflows.
The inspected Windows 3.11 and 3.14 logs collected 1,043 tests with 13 skips.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41864583866
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41864591012

At host-exchange/handoff head ff89a735d932542a5647ce19352ec0404fb693d9, all six
jobs again passed in both workflows. The inspected Windows 3.11 and 3.14 PR logs
collected 1,084 tests with 24 skips, including all 41 new exchange cases:
30 passed and 11 optional actual-Python-3.7 cases skipped on those CI runners.
Those 11 reference-child cases passed in the configured local suite. The PR
remained draft at the matching head. These CI results do not complete the earlier
independent owner/transport review or establish real-game qualification.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41868505101
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41868668874

## Standalone companion build, owner lifecycle and exit observation

Foundry has no required Apex runtime dependency. The earlier handoff's suggestion
to rely on an Apex controller while waiting has been removed; historical commands
remain clearly labeled source references. No Apex compatibility adapter was
committed. The independent companion now has a deterministic archive builder and
an explicit original-first owner-tick integration, rather than an Apex wrapper.

The new owner runtime passes 37 tests on both current Python and actual isolated
Python 3.7.17. Fabricated services and owned loopback exercise observation, clock
speed, guarded CAS entry request, quit-menu request and the separate typed Live
read. Tests cover original return/errors, nested and foreign-thread calls, one
drain per tick, contention, later hook replacement, interrupted startup and
cleanup uncertainty. Production admission, automatic startup, real UI semantics
and current-game qualification are still missing. The separate earlier final
independent owner/transport re-review remains incomplete; these tests do not
replace it.

The builder's 30 tests plus five CLI tests pass with the actual isolated compiler.
Tests cover byte bounds, exact source allowlist, source/compiler pins, Windows
path/descriptor timestamp differences, output collisions and incomplete two-file
publication, descriptor failure, interrupted compiler startup and cleanup. The
trusted compiler builds without executing candidate source. Its executable hash
does not authenticate the rest of the compiler installation.

The actual complete neutral archive was built twice through the CLI and compared
byte-for-byte: 305,568 bytes, 24 source/bytecode members, SHA-256
edd9d3dda9bad5f7999e858777f0d549738f5834fc5c7924b417ae06d8c9466b.
All twelve modules also imported from that archive on isolated Python 3.7.17 with
socket/thread startup and game/Apex imports guarded against. That separate import
smoke test does not establish how the game loads or activates the archive. There
is no automatic bootstrap, installation, production key or admitted game host.

The new passive exit observer watches one retained exact process lifetime and
does not send quit, save or input. Fifteen fake-handle/CLI tests pass; one newly
owned Python-child Windows fixture is separately gated for hosted CI. A signalled
process handle does not distinguish normal shutdown from crash or prove saving.
Independent review reproduced a Unicode casefold collision, which was fixed by
conservative ASCII-only case normalization. The same narrow correction was
propagated to existing process/native-input path comparisons and launch evidence;
process matching preserves its prior parent-segment spelling behavior. Eight
additional path tests cover Unicode expansions, basename confusion and lexical
normalization scope.

Independent builder/exit review passed 49 focused checks with one native Windows
skip, including two actual Python 3.7 fabricated-source cases. Its six additional
publication-failure/collision probes retained refusal/incomplete outcomes without
completion manifests or false process matches. Review did not test actual power
loss or a running game. Hosted CI for this new source checkpoint is pending.

The final full local suite collected 1,180 tests: 1,165 passed and 15 Windows-only
fixtures skipped. Actual isolated Python 3.7 was explicitly configured. The
separate final path-propagation review passed 23 checks with one native Windows
skip and confirmed unchanged source hashes; it did not review native dispatch or
the earlier owner/transport boundaries.

At standalone head c8df9eb972f8f092be9018d5f58eb7ccacd7e260, all six matrix jobs
passed in both workflows. Windows 3.11 and 3.14 PR logs confirm that the newly
owned Python-child exit-monitor fixture ran and passed, rather than being
skipped. The installed no-site wheel also built the same neutral archive bytes
and passed the existing 14-step synthetic/offline packaging checks. This is not
a real-game exit test and does not complete the earlier denied independent
owner/transport re-review.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41876690782
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41876700941

## Credential-free startup probe and connected process binding

The new separate diagnostic runtime passes 49 fabricated-module/owned-thread
tests on both current Python 3.12 and actual isolated CPython 3.7.17. Independent
review found and verified fixes for a late Thread.start exception resource race
and a non-BMP Unicode evidence-size overflow. Explicit atomic resource ownership
now prevents the startup caller from closing a running writer's pinned directory
handle. Encoded loader-field budgets preserve the 16-KiB publication bound and
truthful truncation flags. A known-positive original tick frame refuses startup
before path metadata I/O, hook installation or thread creation. The absence of
that frame is still unknown, not proof that startup is outside a tick.

The separate fixed-entry archive builder passes 16 cloud-only tests, including
actual Python 3.7 compilation without running the diagnostic source. Its scoped
independent review found and verified a Windows reserved-device alias fix in the
shared relative-name validator, such as `NUL .json`. Source, tests and docs contain
no actual game/account data, keys, or required Apex runtime. No diagnostic has
been installed or run in a game. Windows path race limitations and self-report
limits remain explicit.

The connected-loopback ownership primitive passes 36 fabricated tests with one
owned GitHub-hosted Windows test skipped locally. Scoped independent review
verified the Microsoft ABI, tuple orientation, bounded error/denial behavior,
and buffer wiping; additional randomized malformed-table and port-decoding probes
passed. The hosted fixture is enabled only for a newly owned Windows runner and
does not contact a game or user's desktop. Its native result remains pending.
These new feature reviews do not replace the earlier incomplete independent
owner/transport re-review or qualify live game control.

The frozen startup checkpoint's complete local suite collected 1,282 tests:
1,266 passed and 16 Windows-only fixtures skipped, with the trusted Python 3.7
compiler explicitly configured. The actual separate diagnostic archive built
from that checkpoint has six members and 43,073 bytes. Its fixture-only output
configuration is not an installation instruction; no archive was deployed.

At connected-owner head d0aba920cf6dd13c0065537d59006d330827a131, all six jobs
passed in both workflows. Windows 3.11 and 3.14 PR logs collected 1,217 tests
with 27 skips and confirm the real newly owned connected-server fixture passed.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41882351258
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41882270058

At diagnostic head 5d0dbedf49f03ea15324317b22be757d311d8889, all six jobs also
passed in both workflows. Inspected Windows 3.11 and 3.14 PR logs collected 1,282
tests with 30 skips: 47 of the 49 new diagnostic tests passed and two explicitly
POSIX-only fixtures skipped. The real connected-server fixture passed again.
The isolated installed wheel passed the 14-step synthetic/offline smoke checks;
its host source and tests match the published checkpoint. None of these results
establish actual game loader, game-thread or profile isolation behavior.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41883162851
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41883172698

## Composed process-bound peer callback

The new callback composes a genuine already-held lease, the same connected
IPv4 loopback socket's OS owner and session-key authentication. It is not a live
CLI switch, key provider, profile permission or mutation admission. Its 34
fabricated tests exercise both sender versions and receipt reads with the real
HMAC codec in memory, plus owner denial, changed process/socket/intent, lifecycle
and deadline refusals before application send. The full configured local suite
collects 1,316 tests: 1,300 passed and 16 Windows-only fixtures skipped.

Independent review passed those 34 tests and 16 additional fabricated scenarios,
including lease closure during ownership/authentication, socket-snapshot deadline
expiry, malformed owner results, post-auth context changes and integer/float TTL
preservation. No actionable new integration defect was found. This review was
limited to the new composition and its call contracts; the older final
owner/transport re-review remains incomplete. The source documents the trusted
interpreter boundary and does not claim application-frame MAC/encryption or
real-game qualification.

Both exact-head CI workflows for callback commit
`33ac07aa16dfc46c50ec6a2892630b99737549ae` finished with all six jobs green. The
PR Windows 3.11 job initially failed two existing recovery fixtures with a
connection abort and authentication-admission timeout. A single rerun passed
without source changes, including both failing cases. The original failures
are retained; their underlying cause is not established or claimed fixed.
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41884404570
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41884419201

## Neutral profile, test-mod and EA handoff integration

The standalone integration combines the visible test-mod source checkpoint
`d29e8d50dcc4c74fb1f84a8d2aba31a499480574` with the generated neutral lifecycle
source handoff and reviewed repairs. No exported private Apex helper source or
game/account data is included. All commands remain independent of Apex.

The final local suite collected 1,517 tests: 1,501 passed and 16 Windows-only
fixtures skipped, with the trusted actual Python 3.7 compiler configured. The
201 neutral tests cover strict record reads, shared locks and durable directory
identities, exact activation bindings, late process-scan changes, one-shot EA
handoff/positive receipts, bounded executable reads, helper cancellation and
precommit deadlines, and proven never-dispatched reconciliation.

Independent EA review passed 73 scoped fixtures plus three closure probes. It
found and verified fixes for Windows executable stat/fstat normalization and
consent after a known pre-dispatch failure. Independent lifecycle review passed
117 scoped fixtures plus five final probes. It found and verified the fix that
bound test-mod mutation must refuse a pending launch after the lock releases.
Earlier extra probes also exercised partial receipts and lost acknowledgments.

The installed wheel passed 196 isolated no-site fixture checks, with imported
package modules and 45 Python archive members checked against the built source.
Wheel SHA-256: `ee04403423b040170ebc32c0c5a1bac57ba52fa9e9f738f4179c24f9a24878d9`.
Its size is 170,291 bytes. These fixture checks used owned temporary directories
and fabricated processes/helpers; no real EA, game or user profile was touched.

Limits remain explicit: successful/uncertain EA handoffs have no automatic
reservation-release path; only proven pre-dispatch failure can be resolved.
Generic same-process EA dialog wording is not request-specific ownership.
Arbitrary removal of marker/provenance followed by plain-profile admission is
outside the cooperating-command guarantee. An interrupted test-mod retirement
has status reporting but no resume command in this checkpoint. Native EA,
Windows UI Automation and actual game orchestration are not qualified. This
review does not complete the older denied final owner/transport review.
The first exact-head integration CI at
`48d68c353f3a521982b0555dcaba2ff4678ca008` passed the Ubuntu jobs, but Windows
reported two fixture assumptions: replacing an open owned executable was denied
by Windows sharing rules, and a relative-path test crossed the runner's C:/D:
drives. The test fix retains refusal and unchanged-byte checks for that sharing
denial, with no retry, and exercises a genuinely relative path from an owned
same-drive working directory. No production source changed. Independent review
verified both test diffs. All six jobs in both exact-head correction workflows
passed at `70a6bee0208307666727c93f72509ee4c0c75637`, including the formerly failing
Windows fixtures. The Windows 3.14 job collected 1,517 tests with 38 expected
platform/runtime skips; this validates owned Windows fixtures, not game behavior.
Corrected PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41892354871
Corrected push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41892344063
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41891383771
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41891369606


## Interrupted test-mod retirement recovery

An explicit recovery path now handles a proven pre-move retirement and a proven
post-move receipt loss. It preserves receipts, uses the shared lock and stored
profile binding, rejects pending launches and ambiguous locations, rechecks
content/identities around process scans, and never repeats a move solely because
its acknowledgment failed. The prior checkpoint's missing retirement resume path
is addressed by this addition; arbitrary external-race limits are unchanged.

All 26 focused owned-file/fake-process fixtures passed. Independent read-only
review passed those tests and seven extra adversarial probes, including partial
completion, post-move identity/binding changes, and repeated-completion late
changes. CLI routing has two additional fixtures. No native/game/profile-user
action occurred. The complete configured local suite collected 1,545 tests:
1,529 passed and 16 Windows-only fixtures skipped. The rebuilt installed wheel
passed 224 isolated no-site checks, with all 46 Python archive members matching
source. Wheel SHA-256: `6da1ee9b29979dc22bef5b40487dbfb8cf6b14feeb8d1ab33498c1b8e5a87ed9`;
size: 172,433 bytes. All six jobs in both exact-head workflows passed for
`259df99cf006fd9d4aba85e77c08cccac99ca9c1`, including the owned Windows fixtures.
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41893191264
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41893178459


## Startup diagnostic report consumer

The new read-only host inspector consumes explicit report/manifest files and
optional archive bytes. Fifteen focused tests and two CLI routing tests pass.
A producer-generated report is tested against the actual diagnostic runtime
with owned fake-core state. Independent review generated all eight publishable
runtime statuses and verified strict rejection of duplicates, denied reads and
misleading success labels. It identified and verified two fixes: disappearance
during final stable-read validation now refuses instead of becoming initial
absence, and compiler patch-version bounds match the builder's current contract.
The runtime and builder source hashes remained unchanged.

The configured full suite collected 1,562 tests: 1,546 passed and 16 Windows-only
fixtures skipped. The rebuilt installed wheel passed 241 isolated no-site checks;
all 47 Python source members matched the checkpoint. Wheel SHA-256:
`7087f55bdfa25f801dd6155257bad4aefb0b0c1a0db5c3824a32942b545adc0c`;
size: 177,135 bytes. No actual game, native process or user profile was accessed.

Consistent or forged files do not confer authority: loader/live/game and command
authorization remain false, while owner/outside-tick authority remains unknown.
Archive matching is size/hash correlation only, not code authentication or
execution evidence. All twelve exact-head CI jobs passed at
`2c3789f708f4eb541f8140a62c0404fd536434e3`.
PR: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41895372665
Push: https://github.com/Herbertofury/Sims4Foundry/actions/runs/41895364400

## Independent Build adapter packaging

Verified the twenty source-checkpoint blobs before adaptation. The five packaged
adapters preserve public commit/blob lineage and use relative imports. A reader
flag recheck has 42 callback-change subcases. All 109 Build fixtures passed on
the host and pinned actual Python 3.7; all 32 packaging tests passed, including
compilation and inert isolated imports under both interpreters. Independent
read-only review found no actionable issue in this twelve-file packaging slice.

The archive has seventeen modules and thirty-four members. Its SHA-256 is
`78a5f5b703cfbf323650a7af9de7013176e6463cd312d2f2798579f136c28322`.
This is fixture/package evidence only, not owner/transport re-review or native
qualification. No actual game, profile, desktop or native process was accessed.

## Retained-object Build transform route (schema 4)

The standalone route now joins exact capture/transform requests to the existing
shared queue, transport, process-bound peer, sanitized durable journal and owner
runtime. It retains the actual selected object, consumes selection lifetimes,
and preserves cross-schema request conflicts and unresolved-mutation barriers.
A focused new-path review identified numeric-spelling aliases and callback-time
hook replacement; both were fixed and covered by adversarial fixtures. This
review is not the previously denied owner/transport re-review.

The configured local suite collected 1,659 tests: 1,643 passed and sixteen
Windows-only fixtures skipped. All 32 package fixtures passed with the pinned
actual Python 3.7 compiler, including inert archive imports. The focused protocol,
shared integration and runtime tests passed 22 on the host; actual Python 3.7
passed sixteen with six host-only skips. Retained-selection/direct-transform
fixtures also passed on actual Python 3.7. The rebuilt installed wheel passed
34 isolated no-site tests; all fifty Python members matched source.

Wheel SHA-256: `c2c797cbb35233ef47419441326351c758f03cc7627cd9be7808ea0344ede653`
(185,881 bytes). The 21-module / 42-member companion archive SHA-256 is
`fe18fba1fe1bad4a71cb6649c6921640ef7d5d5a021937a2895012f65d0c0341`
(455,853). No game, user profile or native process was accessed.

The route has no production provider, installation or live qualification. A
native transform/readback remains an unverified mutation, without verified undo
or transaction semantics. Exact-head CI for this source checkpoint is pending.
See [BUILD_TRANSFORM_ROUTE.md](BUILD_TRANSFORM_ROUTE.md) for integration interfaces,
required bindings, single-dispatch behavior and remaining unsupported operations.
