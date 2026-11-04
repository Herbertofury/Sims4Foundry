# Neutral companion components: not installed or live-qualified

These sources are another engineering step toward the requested reusable CLI,
not a declaration that the full CAS, Live or Build workflow works in the game.
The CLI still refuses real semantic execution. No code here was installed in a
Sims profile, connected to a running game, or used to alter a save.

## Components

- `src/sims4foundry_cli/companion_protocol.py`: offline 64-KiB length-prefixed strict
  JSON framing, four typed operations, exact request/session/process lifetime
  binding, correlated responses and conservative replay classification. This v1
  contract is unchanged; it cannot carry the queue's TTL/save context by itself.
- `src/sims4foundry_cli/companion_transport.py` and companion `wire.py`/`transport.py`:
  an explicit schema-2 context envelope, receipt-only reconnects and a real bounded
  loopback socket bridge. Server and client default deny without independently
  established host/peer authorization hooks; no such production provider ships.
  See [TRANSPORT.md](TRANSPORT.md) for exact framing and security boundaries.
- `companion-src/foundry_companion/peer_auth.py` and client `companion_auth.py`:
  optional mutual HMAC-SHA256 peer hooks with explicit caller-owned keys, fresh
  nonces, role separation and locally observed socket endpoint binding. No key
  provisioning, host authorization, encrypted application channel or OS identity
  claim is added. See [PEER_AUTH.md](PEER_AUTH.md).
- `companion-src/foundry_companion/queue.py`: Python-3.7-compatible immutable
  requests, process epoch and exact zone/household/save preconditions, bounded TTL,
  pre-start owner admission and an irreversible uncertainty latch, 48 pending requests
  and 128 retained IDs. Only the captured Thread object plus ident drains; at
  most one native call per tick. Empty ticks do no I/O. No admitted ID is evicted,
  no reset exists, and started/unknown requests never requeue. Exhaustion refuses
  new work until a future host performs explicit, durable session reconciliation.
- `companion-src/foundry_companion/native.py`: owner-thread-only observed status,
  native clock-speed call plus independent readback, exact instanced-Sim CAS entry
  call behind an additional preservation scope, and ordinary quit-menu request.
  Admission denies by default. Command modules load only after mutation/context
  admission, with another check afterward. CAS visibility, save persistence and
  actual process exit are never inferred from a call returning.
- `src/sims4foundry_cli/process_lease.py`: fixed per-user Windows Local mutex keyed
  by PID and creation FILETIME, retained query-only process handle, exact fresh
  identity checks, fail-fast contention and recursive-owner rejection. It only
  coordinates participating Foundry controllers. Every acquisition still requires
  cross-workspace durable journal reconciliation; an abandoned or new mutex
  cannot establish whether a previous game action happened.

## Existing-source lineage and limits

A read-only review of the existing Apex source on 2026-11-04 established native
call signatures in `Source/apex_core/test_driver.py`, owner-thread queue handling
in `Source/apex_core/command_queue.py`, and final scheduler definitions in
`Source/td1_occult_hybrid_apex.py`. These files were not imported or copied wholesale.
The new code does not include exported private helper modules or game/account data.

The existing API route uses a bounded queue drained on the owner thread through
an actual wall-clock alarm/core tick. Earlier alarm definitions are superseded
by later definitions in that module. The new queue has no installed scheduler.
Original callbacks, zone transitions and pending cancellation still require
qualified integration; a fast empty callback does not make an expensive native
transaction preemptible.

Apex CAS entry has profile marker, preservation, checkpoint, frontdoor and form
transaction guards. Removing them is not a neutral adapter. The new CAS branch
therefore requires a separately reviewed preservation scope and cannot perform
full-edit, accept/cancel, part edits or any Build transaction. Normal quit merely
requests its menu and still needs independently observed choices/process closure.

## Integration still required

1. An admitted disposable profile, exact process lifetime, shared durable journal,
   authenticated bounded transport and version-scoped provider qualification.
   The new schema-2 transport preserves epoch, TTL and zone/household/save context
   into the queue. Secure key provisioning, qualified channel/peer policy, durable
   journaling and live host authorization remain absent; caller JSON or possession
   of a shared key cannot establish operation authorization.
2. Owner-thread bootstrap and zone lifecycle hooks that coexist with other mods;
   no game API may run on a network worker. The source-only listener has an explicit
   caller-controlled lifecycle and no automatic startup hook or scheduling.
3. CAS UI ownership and readback independent of gameplay Python availability,
   with original native semantics and preservation. Build and menu providers are
   likewise missing. No generic cheat/raw-input fallback is exposed.
4. Operation-specific observed evidence before protocol `verified`. Queue results
   are adapter assertions, not independent evidence. Protocol fingerprints are
   consistency checks, not authentication.
5. Controlled nonoverlapping live tests, recovery and save/reload/normal-exit gates
   in QUALIFICATION.md. Existing Apex/CAS tests and user input remain outside the
   cooperative mutex and must never be disturbed.

## Python compatibility evidence

The existing build expects Python 3.7 bytecode magic `420d0d0a`, checked-hash
invalidation and optimization zero. Companion sources, including wire/transport,
were parsed and tested with an isolated source-built official Python 3.7.17.
Queue/native/socket regressions and an actual current-Python client to a separate
3.7 socket-server fixture passed. The game itself was not present. No `.ts4script`
was built; there is no bootstrap to install. Reproducible test commands and scope
are in [TRANSPORT.md](TRANSPORT.md).

Python 3.7 is end-of-life. This isolated interpreter is used only for trusted local
compatibility/socket fixtures, never a production network service or the current CLI interpreter.
See the [official Python 3.7.17 release](https://www.python.org/downloads/release/python-3717/).
The actual game build, embedded Python behavior and UI resources still need
current-installation qualification, rather than relying on historical 1.128.90
source comments.
