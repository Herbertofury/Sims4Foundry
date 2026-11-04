# Source-only native packet backend

## Status and admission boundary

`native_input.py` contains an original ctypes-backed `PostMessageW` implementation
that composes with `guarded_input.run_packets`. **It has no production admission,
no input CLI command, and no demonstrated game qualification.**

`native_packet_backend(identity)` always raises `input_not_admitted`, before any
DLL loading, file access, mutex acquisition or input. A passive identity JSON,
process lease, boolean flag, empty callback returning `True`, synthetic recorder,
or successful CI job cannot unlock this public entry point.

The private `_fixture_backend(identity, packets, policy)` exists only for trusted
Python test code on disposable GitHub Windows runners. It requires a subclass of
a default-denied fixture policy, a typed ticket and independently sampled typed
recorder observations. Fixture admission precedes native preparation. The test
must establish ownership of a newly created synthetic target and its exact
history, and supply independent window-continuity and recording observations.
Neither an environment variable nor the fixture ticket is live authority. Python
private names are not a sandbox: code permitted to import private APIs or monkey
patch this module can already call ctypes itself. Do not expose that seam through
JSON, a CLI, IPC, plugins or untrusted Python.

A future integration needs separate reviewed authority for the exact disposable
game profile, process lifetime, operation, recorder and current shared durable
history. It must reconcile **every** admission, not just abandoned mutexes. This
module has no production recorder verifier, journal authority, profile admission,
semantic verifier, installation, launch or exit implementation. A process lease
continues to report `mutation_permitted=False` and supplies no reconciliation.

## Bound context and native checks

The fixture backend binds one bounded immutable packet plan and a detached target
identity. It independently reconstructs exact `InputPacket` objects, including
when received for posting. It allows only the next bound event, or its one exact
matching original release. No arbitrary virtual keys or message numbers, thread
posts, NULL HWND or broadcast target are supported. Admission receives detached
packet and identity objects. Returned observations are detached too.

The backend retains the cooperative process-lifetime lease and an additional
query/synchronization-only process handle on its acquiring Python thread. Both
thread identity and the actual Thread object are checked. Per-event observation
checks the original process creation time and nonsignaled process handle, image
path, current non-impersonating controller, original user/session, HWND owner PID,
UI thread and exact class. Sender and target integrity must be known and equal.
Normal input additionally requires exact geometry, DPI, visible/non-minimized
state, unchanged nonzero foreground, current keyboard layout, actual modifiers,
no physically held mouse buttons or conflicting navigation key, and a fresh
advancing recorder. No focus or cursor mutation is performed.

The image is opened using `CreateFileW` with `GENERIC_READ`, `FILE_SHARE_READ`,
`OPEN_EXISTING` and a non-inherited handle. Only ordinary fixed local disk files
up to 256 MiB are accepted; directory/reparse/device/network image paths are
refused. The retained handle is hashed before input and held throughout the
context, refusing write/delete sharing. Microsoft documents that omitting
`FILE_SHARE_WRITE` also rejects an existing writable file mapping. The initial
lease retains its existing full independent observations, including hashes.
During a held sequence, identity continuity uses the retained process and file
handles rather than reopening and rehashing the full executable for each packet.
This pins the observed on-disk image under ordinary Windows sharing semantics,
not the target's loaded modules or process memory. It is not protection against
kernel-level or hostile privileged tampering. Conflicting image access fails
closed; there is no weaker sharing-mode fallback.

## Keyboard and modifier contracts

`GetKeyboardLayout` is queried for the exact target UI thread, not thread zero or
the sender's layout. Its exact nonzero HKL must remain the admitted baseline.
`MapVirtualKeyExW(..., MAPVK_VK_TO_VSC_EX, hkl)` must produce the packet's exact
scan byte and E0 flag. Zero, E1 and unsupported multi-byte results are refused.
The source never loads or activates layouts. See Microsoft's
[GetKeyboardLayout](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getkeyboardlayout)
and [MapVirtualKeyExW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-mapvirtualkeyexw)
contracts.

Only the high bit of `GetAsyncKeyState` is used; the low recent-press bit is
unreliable. Zero may mean inaccessible input state, not simply an unpressed key.
The backend first checks controller and target desktop input availability and
equal known foreground integrity. It requests existing `DESKTOP_READOBJECTS`,
`DESKTOP_HOOKCONTROL` and `DESKTOP_JOURNALRECORD` access solely to establish whether
key-state observation is available. Those rights are not literally read-only;
the backend only queries them, immediately closes the handle, installs no hooks
and changes no desktop permissions or ACLs. SHIFT/CTRL must exactly match mouse flags;
keyboard navigation forbids held SHIFT/CTRL. ALT, either Windows key and physical
mouse buttons are refused. Mouse flags do not synthesize modifier presses.
These are sampled observations, not an atomic lock against a user's next action.
See [GetAsyncKeyState](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate),
[GetUserObjectInformationW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getuserobjectinformationw)
and [OpenInputDesktop](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-openinputdesktop).

## Recorder admission and timing

The default policy denies recording. A fixture supplies a recorder epoch object,
a nonnegative sequence number and a monotonic capture timestamp. The initial
observation must advance after admission. Subsequent observations cannot regress,
change epoch, fabricate a new timestamp without sequence progress, be future
dated or remain older than one second. Normal posts recheck recording after the
runner's earlier checks. The native fixture uses an independently advancing
synthetic heartbeat to test this contract; **that is not a WGC recording or game
evidence**. Replacing it with a constant `True` is refused.

Plans are bounded by the packet module's counts, duration and path limits. The
native sender makes exactly one asynchronous `PostMessageW` call per attempted
event; it never retries, uses a synchronous target message call, polls a target
message pump, changes UIPI/UAC, attaches input threads or injects a DLL. Ordinary
pre-post observations have a 250 ms elapsed budget and initial image preparation
a five-second elapsed budget. No whole-image disk work occurs while input is
held. These budgets detect slow completion; they do **not** forcibly interrupt a
stalled OS syscall, filesystem, trusted Python callback or interpreter. Fixture
hooks must be bounded cached observations. Hard real-time guarantees and crash
cleanup are not implemented, and production admission remains closed.

## Release and uncertainty

Before attempting a Down, the backend retains its exact release obligation and
last attempted point. Explicit native `False` means refusal and removes the
corresponding Down release obligation. A raised call or any other unknown result
may follow queue acceptance, so the obligation remains. Moves update the last
attempted point before dispatch, including a move whose result is lost.

Original Up ignores geometry, recording, foreground, layout and modifier changes.
It still requires the original same-thread active lease, retained live process,
current security context, exact HWND/PID/UI-thread/class, independent fixture
window-continuity evidence and equal known integrity. It cannot be directed at a
replacement or currently unobservable owner. One release attempt is consumed
before its final ownership checks or native call. Failed or unknown release
remains unresolved, is never blindly repeated, and is not erased on context exit.
Context exit closes handles, reports unresolved held input, and never invents
another native post. Backend uncertainty remains set after an unknown result even
if the subsequent release is accepted. Raw callback/native exception details are
suppressed, including displayed exception chaining.

Windows has no retained lifetime handle for an HWND. Repeated PID/thread/class
checks alone cannot prove that a window was not destroyed and recreated with the
same numeric handle and owner. The fixture supplies a window epoch under its
fixed never-recreate source contract; no production equivalent is implemented.
There is also an unavoidable observation-to-post race. The backend cannot promise
release after target destruction, inaccessible ownership, interpreter termination
or an OS failure. These limitations are reasons for continued default denial,
not grounds to choose a replacement target or clear uncertainty.

A successful [PostMessageW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-postmessagew)
return establishes queue acceptance only. It does not establish message processing,
input equivalence, gameplay effect or semantic success. UIPI and queue limits can
refuse a post; no bypass or retry is attempted. Even equal integrity is only one
necessary guard, not permission or evidence that Sims will honor these messages.

## Verification and provenance

`tests/test_native_input.py` covers fake-native admission, object aliasing, thread
ownership, identity/integrity changes, recorder faults, layout/scan/modifier state,
refusal versus unknown acceptance, one-attempt release, geometry-independent
cleanup, redaction and minimal native binding contracts. Native integration tests
are gated to `os.name == 'nt'` and `GITHUB_ACTIONS == 'true'`, reusing the repository's
newly compiled disposable C# fixture. They must never be run on a user's desktop,
real game, profile or preexisting process. The fixture checks its own message
receipts and unchanged cursor/foreground; no game behavior is inferred.

The implementation is original source derived from documented Win32 contracts
and the repository's typed packet/guarded runner design. Historical inspected
external-pointer lineage SHA-256:
`d8df2930835408334411e9f141a3d704e9dc32536eb75cca8f44136b90dfdcbd`.
That lineage used `PostMessageW` but did not demonstrate no-Apex qualification.
No Apex source, private path, profile data or external exported implementation is
included. Image sharing relies on Microsoft's
[CreateFileW contract](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew),
reviewed with the input documentation on 2026-11-04. Continue the independent gates
in [QUALIFICATION.md](QUALIFICATION.md); passing fixtures does not promote them.
