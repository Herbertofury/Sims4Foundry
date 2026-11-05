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
witness. `click_once` accepts one explicit client-area click, attempts it through
the existing durable input path, and records a passive WGC frame. Existing
request IDs are refused before the native sender is opened; an unresolved
registry gate blocks a later request. No input acknowledgment, UI transition,
semantic state or game qualification is claimed. Its owner must remain on the
same thread and must independently manage witness acquisition and lifetime.
This is not wired into the installed CLI or a cross-process owner service.

An opt-in owned-window fixture exercised this composition using real Windows
message posting and WGC against the test's own Python process. The fixture
substitutes the Sims-specific identity validator and inspector for that exact
owned process and window; the captured frame's timestamp is receipt time, and
the test provides no Sims or independent semantic verification. It leaves the
input journal awaiting readback and preserves its evidence. No game/profile was
accessed.
