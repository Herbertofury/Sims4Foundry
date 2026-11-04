# Architecture and safety contract

## Boundary

The controller and semantic catalog are independent of Apex. The only executable
semantic backend in 0.1 is `simulation`. Native Windows inspection/capture is a
separate passive tool, not a route around that gate. There is deliberately no raw
click, key, shell, forced quit or caller-controlled `qualified=true` escape hatch.

The catalog is an initial typed surface, not exhaustive Sims functionality.
`engine._apply` records abstract properties for regression tests; it does not emulate
CAS parts, the interaction queue, rooms, objects or real save serialization.

## Plan → dispatch → observe

1. Acquire a workspace lock before state observation.
2. Validate the operation, exact argument shape, current mode and operation-specific
   predicates. Refuse an unresolved prior operation.
3. Save a synthetic observation and a plan bound to session ID, revision, state hash,
   arguments and timestamp. Planning performs no game mutation.
4. Execution requires the returned plan ID/hash, unmodified state and an age from
   zero through 30 seconds. Backwards wall-clock movement is rejected.
5. Publish intent and dispatch-start records before the model mutation.
6. Apply one transition, observe the resulting state, and publish its result.
7. On uncertainty, preserve evidence and reconcile by observation only. No retry or
   rollback is inferred from a timeout, missing response or process crash.

Completed calls can return an existing receipt. This is idempotent result retrieval,
not permission to submit another input. A failed workflow stops at the first bad
step; rerunning the complete workflow is not a supported resume mechanism.

## Storage

JSON rejects duplicate keys and nonfinite numbers; read/write limits are both 4 MiB.
New records are fsynced then atomically published with no-overwrite hard-link
semantics. State uses same-directory replacement. Unsupported filesystems fail
rather than downgrade to overwrite. Old receipts remain unchanged.

Locks are OS-held (`flock` or Windows byte-range locking), on a stable inode, and are
released by process exit. The lock file remains present; its existence is not a busy
signal. A real adapter must instead use a per-process-lifetime lock in a common
machine namespace so separate workspaces cannot operate the same game. Even that
would coordinate only participating tools, not Apex or manual activity.

POSIX file/directory flushes are implemented. Windows power-loss ordering and
filesystem crash semantics are not qualified for live actions. The module therefore
must not be reused for live dispatch without completing those tests.

Path guards reject observed symlinks/reparse points and known game/profile names.
They are conservative preflight checks on a cooperative local workspace, not a
security boundary against a hostile process swapping path components concurrently.
Never choose a profile or game directory for evidence or staging.

## Future backend contract

A real provider must supply:

- Target identity: retained process handle, PID plus creation FILETIME, exact image
  path/hash/version, window HWND/thread/class, integrity, session and geometry/DPI.
- Observation provenance: native state and/or exact captured pixels, freshness,
  source identity, modal/interaction state and confidence. Simulation never qualifies.
- Per-operation capabilities with reviewed qualification bound to provider version,
  game build/hash, operation/domain and UI configuration. Capability absence refuses.
- Dispatch status separated into not-dispatched, dispatched, uncertain. Queue/API
  acceptance is not a successful semantic postcondition.
- Independently observed operation-specific postconditions, and read-only request-ID
  reconciliation. Composite actions reobserve before every step.
- Explicit save/retain/discard intentions and exact slot/household/Sim/object IDs.
  Attach does not transfer ownership of a preexisting game or test session.

Prefer a small, mod-neutral Foundry in-game bridge for semantic native operations,
with a separately qualified external input adapter for menus and paths unavailable
through native APIs. Never expose all Apex actions through an unvalidated generic
command relay. The implementation and game tests for these providers are pending.

## Input construction versus live dispatch

`input_packets` constructs immutable allowlisted message plans; it never sends.
`guarded_input` runs a plan only against an injected backend. There is no concrete
Windows input backend or CLI input command in this release. Its test backends prove
release/error ordering, not game support. Backend contracts require explicit True
for recorder progress and current scan/layout/modifier verification, equal known
integrity, an exact foreground baseline and fresh owned-window identity.

The CI-only native fixture sends packets solely to a newly compiled owned fixture
on a disposable hosted Windows runner. It checks message receipt and unchanged
foreground/cursor. This is transport evidence for a synthetic window, not proof
that Sims consumes the same packets or preserves gameplay/save state.

The guarded runner records complete packet intent before input. While a button or
key may be held, optional per-packet records remain in memory and flush only after
release; journal stalls therefore cannot delay the matching Up. Every native
backend operation must be bounded and the eventual host must supervise cleanup.
The pure runner itself cannot guarantee release after a native hang or process
termination. Runtime validation repeats timing, path-distance and wheel viewport
limits so direct packet construction does not bypass encoder policy.

## Source-only companion client ledger

The separate [companion journal](COMPANION_JOURNAL.md) now preserves exact
schema-2 intents, callback attempts and receipt claims across client restart.
It is not the synthetic engine, a transport executor or independent game evidence.

The source-only [native packet backend](NATIVE_INPUT.md) supplies Win32 bindings
for isolated fixture tests of the runner. Its public factory always refuses and
there is no CLI input route. It retains the original process/image/lease binding,
rechecks integrity and normal input context, and distinguishes queue acceptance
from delivery or semantics. Original Up ignores geometry/recorder failure but
still requires original ownership and equal known integrity. Native timing checks
detect slow completion; they cannot forcibly preempt an OS call or trusted hook.
Its default-denied injected seam cannot promote qualification; adapter-returned
mutations remain unresolved. Shared cross-workspace history, a real admitted host,
independent verification and Windows power-loss qualification are still absent.
