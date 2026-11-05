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
sender binding; there is no CLI command or qualified recovery protocol yet.
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
