# Experimental independent native input

`live_input.LiveInputBackend` is a source-level integration of the existing
allowlisted Windows message sender with the process lease, the owned WGC
recorder and `InputJournal`. It does not import Apex or use Computer Use.
Construction and context entry send no input. The caller supplies the exact
target identity, packet sequence and explicitly pinned FFmpeg image. A journal
must persist the intent and dispatch attempt before calling the backend. The
fixture tests cover a queued click, refusal, a lost native acknowledgement,
recorder loss, target ownership change and the single original release attempt.
An additional Windows test sends the actual allowlisted `PostMessageW` packets
to an owned hidden message-only window and observes their delivery there. It
does not launch or interact with the game and does not qualify game input.
An opt-in Windows test also captured an owned, briefly visible window through
the real pinned FFmpeg WGC stream, sent it native click packets and observed
the matching down/up sequence plus the expected black-to-white center pixel in
a later received frame. It does not exclude other input or establish that the
frame was captured after the click; receipt time is not proof of capture time.
The test requires explicit
`FOUNDRY_WGC_TEST_FFMPEG` and `FOUNDRY_WGC_TEST_SHA256` values and controls only
its own fixture window and recorder. Neither test qualifies Sims behavior.
A separate GitHub Windows runner test exercises the retained live sender and
journal against a newly compiled owned fixture, with a synthetic recorder; it
does not prove Sims or WGC behavior. Its `GITHUB_ACTIONS` environment check is
a convention, not proof that a host is disposable, and fixture child acquisition
and cleanup are not a production ownership protocol.
With `FOUNDRY_WGC_REGISTERED_TEST=1`, the opt-in owned-window test additionally
exercises the registry, retained witness, journal, live sender and real WGC
stream together. It substitutes the Sims-specific identity validator and
independent inspector only for the test's own PID, image and window; its key is
DPAPI-protected and its SQLite anchor is cooperative. It leaves the journal
awaiting readback and preserves the original fixture evidence directory; set
`FOUNDRY_REGISTERED_FIXTURE_ROOT` to select its existing parent directory.
The same fixture inspects the stored frame and decodes its pixels by comparing
it with itself. That exercises the evidence path with a real WGC PNG; it is not
an independent reference or game-state validation.
`input_frame_evidence.capture` can store a validated frame from the backend
instance used for the original registered input. The trusted in-process owner
correlates the journal location, request, intent, packet plan, recorder object
and epoch before dispatch, then requires a completed `awaiting_readback`
result. It refuses a different backend or recorder. This is not authentication
against hostile same-user or in-process code. The recorder may return its last
existing frame. The operation sends no input and does not acknowledge the
journal; receipt time does not establish capture time, causality, semantic
state or game qualification. A failed write or lost response may leave an
intent, PNG or completed receipt; inspect the original output and do not repeat
input. `input_frame_evidence.inspect` is a read-only Python API that inspects
the original journal and evidence directory, reporting missing files or a
matching set. It neither resolves the journal nor restores an incomplete
sender binding; there is no qualified recovery protocol yet.
For a lost journal return only, experimental
`registered_input.reconcile_frame_binding` can restore the in-memory frame
binding when the original sender, recorder and live witness are still held,
the journal is already awaiting readback, and the same registry attempt is
observed pending. It sends no packets and does not resolve the journal. It
cannot recover a failed admission, missing result, changed recorder, lost
owner or process restart. The journal and registry checks are cooperative,
path-based observations rather than an atomic snapshot; this is not a
game-qualified recovery protocol. The pending attempt is only observed at a
point in time: later passive capture does not reserve or recheck the registry
gate. The binding is not authority for a dependent input.
The read-only inspection and pixel comparison are also available without the
session owner through these installed commands (they never send input):

```text
foundry-frame-evidence inspect --journal JOURNAL --request-id REQUEST_ID --evidence EVIDENCE_DIRECTORY
foundry-frame-evidence compare --journal JOURNAL --request-id REQUEST_ID --evidence EVIDENCE_DIRECTORY --frame-sha256 FRAME_SHA256 --evidence-intent-sha256 INTENT_SHA256 --reference REFERENCE.png --reference-sha256 REFERENCE_SHA256 --region X Y WIDTH HEIGHT
```

Inspection returns status 0 for matching observed files, 1 for missing or
unavailable files, and 2 for an error. Comparison returns 0 for a pixel match,
1 for a mismatch, and 2 for an error. These commands do not recover a sender.
Its observations are not an atomic snapshot and do not authenticate files.
`input_frame_evidence.compare` can compare a region against an explicitly
hash-pinned reference, using caller-supplied frame and evidence-intent hashes.
A matching region is a pixel observation, not an input acknowledgement,
capture-time proof or evidence of game state or action causality. Rechecking
the evidence before and after comparison does not produce an atomic snapshot.
The capture's separate path-based hash and PNG reads do not exclude concurrent
replacement either.
The parent is checked for named game/profile paths and existing reparse points
before directory creation, but this path-based check does not prevent a
concurrent replacement. Fixture records include local process and path details;
avoid publishing them. Its local manifest and outcome describe fixture scope,
not independently verified game evidence.
It does not qualify an unpatched Sims identity, game action, frame
capture time or authenticated semantic observation.
Recorder startup with an unknown child-creation outcome and unsuccessful
cleanup retain the recorder and pin objects in an in-process quarantine. If a
pin close itself failed, retained objects do not prove which OS handles remain
open. An uncertain cleanup is not silently repeated or reported as complete;
process termination ends that in-memory ownership and is not restart recovery.
An entry-time pin cleanup failure also remains unresolved. If termination of
the owned recorder does not complete in time, no second termination is issued.
Interruption during pin entry is treated as uncertain, and lock acquisition is
bounded. Python cannot forcibly bound a stalled native cleanup call. A fixture
interrupted before it invokes its latched close may retain a quarantined child
instead of proving cleanup.

This is not yet a supported CLI operation or game-qualified provider. A native
queue acceptance is not delivery, state transition or semantic success. The
journal leaves a queued sequence awaiting an independently trusted host
readback; unresolved attempts cannot be replayed within that journal. A new or
different journal is not an authoritative reconciliation mechanism. No common
authenticated registry prevents separate journals or other tools from acting.

The retained process handle identifies the process lifetime, and repeated
checks bind its image path, user/session, window owner, class, thread, geometry
and integrity. A same-owner HWND replacement between observations cannot be
excluded. Pinning the path's image file does not independently prove the bytes
of a running image. The WGC frame timestamp records receipt of bytes by the
controller; it does not prove when the game presented the frame or that a UI
change followed a particular input. Visual matching alone must not be treated
as an atomic action acknowledgement. No release can be guaranteed after loss
of ownership, process exit, crash or an unknown native result.
The preflight deadline is checked again after the final foreground call, but
the target, foreground or scene may still change before the non-atomic post.
The registered wrapper also refuses to send a release when its matching down
never reached the backend. A drag release uses the last point actually or
possibly forwarded to the native sender: a known preflight or explicit native
refusal keeps the previous point, while a lost move reply keeps the attempted
point. The runner validates the resulting release against the original plan.
The registry, journal and wrapper use a detached reconstruction of that plan.

Do not enable dependent controls, normal exit, or a production CLI route on
the strength of these fixtures. Integration requires a shared session authority,
reconciliation of unresolved attempts, an independently qualified observation
and dispatch contract, and real-game tests in the coordinated game window. The
separate neutral-exit experiment remains gated until its provider and lease
contracts are met.

## Fixture-only sequential composition

`LiveOwner.fixture_complete_step` composes one fixture-provided synchronous
observer, the original journal acknowledgement, registry reconciliation and
one sender cleanup. The original owner must then inspect the resulting in-memory
completion and explicitly confirm its exact token before a distinct next request.
A lost completion or confirmation reply can be inspected through that same owner;
there is no restart recovery. A failure fences the original sender and completion
attempt; it does not resend input or repeat acknowledgement or cleanup. The
observer is injected test code, not an authenticated operator, independent
visual observer or proof of game state. This method is not exposed through the
CLI or service, and the owner itself still admits only its private same-process
fixture. The fake-provider tests do not qualify sequential game controls.

`FixtureStepClient` wraps one unused original owner and latches its own caller
on an uncertain input, completion, or confirmation reply. After an uncertain
completion it permits only explicit inspection of that same owner; a ready
completion requires an explicit confirmation, and a confirmed completion can
be recognized without resending input or cleanup. Mismatched request IDs,
tokens or fixture status are refused. The client handles loss of its owner's
response only: its own enclosing caller can lose a response after the client
has cleared its pending state. That outer delivery requires its own explicit
inspection and continuation contract before dependent actions. This caller is
an in-process experiment: its gate and the owner's gate do not bind other
`LiveOwner` instances, other processes, direct owner calls, or another authority.
No client or owner state survives process loss. It is not wired into the service
or a game CLI. A fixture click receipt does not authenticate the evidence or
establish any semantic state.

The optional owned-window sequence fixture also exercises an explicit
fixture-window message observer and the original owner's confirmation before a
second click. Its first attempted run stopped at the first unresolved input
before any journaled packet event, and child cleanup was not confirmed; the
second click was not attempted. That run does not establish the cause or count
as a successful sequential test. The original evidence is retained locally;
the attempt must not be replayed or its uncertain cleanup repeated. A later
owned-window run, if separately arranged, emits only allowlisted diagnostic
codes for runner exceptions and retains the window while original owned
resources remain unresolved. For the service fixture it also correlates an
unresolved service entry with the original fixture HWND; an unavailable binding
retains the window conservatively. In-process retention does not survive process
exit or prove that a child or OS handle has closed.
