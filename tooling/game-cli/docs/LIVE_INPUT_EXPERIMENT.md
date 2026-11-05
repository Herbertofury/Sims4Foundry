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
