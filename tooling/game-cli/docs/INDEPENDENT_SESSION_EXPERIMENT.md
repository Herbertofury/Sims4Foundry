# Independent session experiment

`foundry-game independent-session` creates a cooperative authority and session
record for a supplied Windows identity. Initialization does not open a process,
launch the game, access a game profile, or verify that the supplied identity
describes a running process. A PID and file record cannot reconstruct a lost
process handle. A lifetime reservation prevents cooperating sessions from
silently reusing the same PID and creation time, including after a partial
initialization. Inspect original records instead of recreating them after an
uncertain result.

For a fabricated fixture identity only, use new directories outside the game
and profile:

```text
foundry-game independent-session authority-init --authority AUTHORITY_DIR
foundry-game independent-session fixture-initialize --authority AUTHORITY_DIR --session SESSION_DIR
foundry-game independent-session status --authority AUTHORITY_DIR --session SESSION_DIR
```

The status output describes stored cooperative records. It is not process
discovery, a live witness, or permission to dispatch. `input-once` remains
explicitly denied. DPAPI protects the key at rest for the current Windows user;
it does not authenticate other same-user programs or prevent rollback of the
key, journal and SQLite anchor. The fixture-only owner and named-pipe code use
synthetic identity, sender and observer data. Their fake-provider tests are
not a native transport or Sims test. An earlier real-pipe experiment has an
unknown outcome; it must not be assumed clean or repeated as part of this
workflow. No fixture result qualifies game launch, control, saving or exit.

Local integration tests exercised creation and reopening with actual Windows
DPAPI and SQLite against an owned temporary directory and fabricated identity.
They also exercise the CLI session commands and explicitly denied live input.
No game process or profile was used. The live persistent owner, authenticated
observer, safe lifecycle integration and actual-game qualification are still
required before this can become a working game-control workflow.

`independent_live_owner.LiveOwner` is an experimental Python composition for a
host which already owns the exact registry, journal and retained process
witness. Live admission is currently denied: separate authorities can reserve
the same lifetime and bypass each other's pending records. A private test-only
admission permits only the current test process and is not a security boundary
against arbitrary same-user Python. Under that fixture admission, `click_once`
accepts one explicit client-area click, attempts it through
the existing durable input path, and records a passive WGC frame. It reserves
the shared input attempt before recorder setup; setup failure stays pending.
Existing request IDs are refused before the native sender is opened; an
unresolved registry gate blocks a later request. Once admission is entered,
errors report that input may have been sent and must not be replayed. The owner
retains the original sender and recorder until its explicit one-shot cleanup;
the same owner can reconcile a lost journal reply and capture a passive frame
without another input. If cleanup is interrupted or unconfirmed, do not retry
it or reopen a sender. No input acknowledgment, UI transition,
semantic state or game qualification is claimed. Its owner must remain on the
same thread and must independently manage witness acquisition and lifetime.
The installed CLI includes a foreground, newline-delimited JSON service
interface. It currently returns `live_authority_unqualified` before opening a
session, process or profile, even with the experimental opt-in. The syntax
below describes the interface for future qualification; it is not a working
game-control command:

```text
foundry-game experimental-live-owner --authority AUTHORITY_DIR --session SESSION_DIR --witness-id 32_LOWERCASE_HEX --ffmpeg FFMPEG.exe --ffmpeg-sha256 64_LOWERCASE_HEX --allow-experimental
```

For an admitted owned-process fixture, the session must already be initialized
against the exact selected identity. The fixture retains an image pin and
process witness; it does not start the game or prepare a profile. After its
ready message, it accepts one JSON object per line, for example:

```json
{"op":"status"}
{"op":"click","request_id":"22222222222222222222222222222222","x":20,"y":30,"expectation":{"screen":"expected"},"evidence_directory":"C:\\FoundryEvidence\\new-frame"}
{"op":"input_status","request_id":"22222222222222222222222222222222"}
{"op":"stop","abandon":true}
```

The fixture protocol also supports `right_click` with the same fields, `drag`
with `path` (client-coordinate pairs) and `duration_ms`, and `key` with an
allowlisted navigation key, exact `scan_code` and `extended` boolean. The native
sender checks the active keyboard layout before posting a key. These operations
are covered by fabricated-provider tests only; they are not game-qualified and
remain subject to the same single pending input gate.

`recover_frame_binding` accepts the original `request_id`; `capture_frame`
also accepts a new `evidence_directory`. Both use the still-retained sender and
send no input. Frame output and input status do not acknowledge a transition.
The shared gate remains pending after a click, so this service cannot drive a
sequence of game operations. A stop with `abandon:true` makes one cleanup
attempt and closes the original witness; it does not resolve pending input,
save or quit the game, or prove OS release. Loss of the console or process
does not recover the witness after restart. An owner-intent record prevents a
second acquisition through this session; a fresh authority can bypass that
reservation, so this is not global replay prevention. Inspect the original
marker and journal after an uncertain start or response. The marker is
published before acquisition, but Windows directory power-loss durability is
not established. Quarantine retains objects only inside the same Python
process: EOF, stdout loss, interruption or process exit can lose the witness,
and FFmpeg descendant cleanup is not established. A startup interruption may
bypass the formatted error; a stop interruption may leave release unconfirmed.
The service has no cross-process client authentication and must not be treated
as a production authority.

An opt-in owned-window fixture exercised this composition using real Windows
message posting and WGC against the test's own Python process. The fixture
substitutes the Sims-specific identity validator and inspector for that exact
owned process and window; the captured frame's timestamp is receipt time, and
the test provides no Sims or independent semantic verification. It leaves the
input journal awaiting readback and preserves its evidence. No game/profile was
accessed.
