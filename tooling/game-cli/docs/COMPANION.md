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
- Separate `companion_live_protocol.py`, companion `live_wire.py` and
  `live_executor.py`: a schema-3 request-bound Live queue read through the existing
  owner queue and journal, with strict actor/context/result binding and whole-read
  limits. It uses injected readers only; no native adapter, startup, CLI sender or
  live qualification is added. Legacy v1/v2 and authentication are unchanged.
  See [LIVE_READ_ROUTE.md](LIVE_READ_ROUTE.md).
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
  native clock-speed call plus service readback, exact instanced-Sim CAS entry
  call behind an additional preservation scope, and ordinary quit-menu request.
  Admission denies by default. An explicit captured session/process-epoch binding
  is required even for observation; every mutation additionally requires exact
  zone/household/save context before reads or command imports. Command modules
  load only after mutation/context admission and an explicit host mode declaration,
  with another check after each import. Generic read/mutation permission and
  `zone_running && !in_build_buy` cannot establish exact Live mode. CAS
  target construction is inside the preservation scope and precedes the final
  fresh context and exact Sim ID/object/instance/household checks. CAS visibility,
  save persistence and actual process exit are never inferred from a call returning.
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

## Native host declaration and callback boundaries

A future qualified host must explicitly provide `session_id` and a typed
`ProcessEpoch` to `NativeAPI`. Leaving both unset constructs an inert adapter;
execution refuses before admission callbacks, service reads, command imports or
preservation scopes. Partial or malformed configured bindings are rejected at
construction. Requests must match the captured session, PID and creation FILETIME.
The adapter copies the configured identity into primitive values, so changing the
caller-owned epoch object cannot rebind it. The queue's legacy pure test interface
still permits missing epoch/save fields; the native boundary does not.

This is only a host-supplied declaration. It is not an OS identity proof, a profile
lease, a durable journal or live authority. The host must independently validate
the process lifetime and provide current operation-specific admission. Providing
matching identity fields does not override the default-deny admission provider.

At execution entry the adapter copies all typed request, argument and epoch
values. Read/mutation admission receives detached request views, and mutation
admission also receives a detached observation with its own error map. Retained
views cannot rewrite the adapter's previously checked baselines. A hook that
changes the request view it is currently asked to admit is refused before work
continues. After each mutation admission hook, one bounded fresh read must match
the same running zone/household/client/save context. These primitive checks do not
identify exact UI mode or exclude CAS/loading. Explicit mode observation follows,
with one further native read to catch its ordinary context side effects. There
is no recursive admission or mode-observation loop.
CAS preservation receives a detached request but the actual Sim/client,
then target preparation and fresh read/mutation admission occur inside the scope.
Refusals and constructor/native errors unwind that scope; native errors are not
retried. These local alias defenses are not a sandbox for untrusted Python hooks:
services, command modules and preservation/admission providers remain trusted,
independently reviewed integration code. A per-instance execution guard refuses
direct callback reentrancy, including a mode provider calling `execute` again;
`finally` cleanup releases that guard after exceptions. This is not a sandbox or
permission to replay a previously started request.

## Explicit native mode contract

Every native mutation additionally requires the **same trusted admission object**
to implement `observe_mode(request, observation, sample_token)`. It receives a
fresh detached typed request, detached primitive observation with a separate error
map, and a fresh opaque per-call object token. It must return exactly
`HostModeDeclaration(request, client_id, sample_token, mode)`:

- The returned request must match every value and exact TTL numeric type,
  including operation/arguments, request/session IDs, process epoch and expected
  zone/household/save context. Changing the input request view is also refused.
- `client_id` must be the exact canonical string from the current native read.
  The returned token must be the same object supplied for this invocation. A
  cached declaration fails on a subsequent sample or another adapter instance.
- `mode` must be an exact string from `live`, `cas`, `build_buy`, `loading`, `menu`
  or `unknown`. Only `live` can admit a mutation. Missing observers, boolean
  verdicts, malformed declarations, stale tokens and exceptions fail closed.

The token only prevents accidental reuse; it does not authenticate a host or
prove a fresh UI observation. Returning these fields is a **host assertion**, not
new authority. Existing read/mutation admission, exact process/save bindings,
CAS preservation and queue/journal safeguards all remain necessary. No
installed-game source is currently qualified to supply this declaration, and no
production implementation ships. Providers must establish exact current mode
from a separately qualified source, never infer it from the two zone booleans,
clock values, command return, generic admission, or a previous declaration.

Mode observations run on the captured Thread object plus ident after generic
admission and before any command-module import. They repeat after each import,
after the initial Sim lookup before entering its preservation scope, immediately
after scope entry before target construction, after target preparation, and after
final target checks before dispatch. Each successful
mode callback is followed by one bounded native context reread; changed client,
zone, household, save or running/build flags prevent the next action. Native
readers and the `require_read`, `require_mutation` and `observe_mode` callbacks must
remain bounded, synchronous and free of dispatch, command imports, network/durable
I/O or waits. In particular, the mode observer must be read-only:
it must not change gameplay state, Sims/instances/households, UI mode or admission
policy. Its final native reread does not revalidate every Sim field, so direct
state replacement is outside this trusted provider contract. The declarations
and native rereads are not atomic:
a lying provider or an unobserved change-and-return between samples is not solved
by correlation. Qualification and host lifecycle integration remain required.

After the speed call, the adapter samples mode again and then reads native state
again. Verification uses the **final** clock speed and unchanged native context,
not the pre-callback speed. Missing, malformed, failed or non-Live post-call mode
is `unverified`, even when the requested speed was observed. The result labels
`observation.host_mode` with `mode_source: injected_host_declaration` and retains
`game_qualified: false` and `persistence_verified: false`. Ordinary read-only
`observe` does not invoke this provider or claim a gameplay mode.

Mode failures before dispatch prevent subsequent imports, scopes and commands.
They do not rewrite queue history: once the queue has published `started`, any
adapter exception is conservatively `unknown`, even if this adapter refused
before the command. Post-speed `unverified` and exceptions likewise latch the
queue's unresolved mutation state. Neither the same ID nor a replacement ID is
permission to retry; external durable reconciliation remains necessary.

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
3. A qualified exact UI-mode observer for native mutations, plus CAS UI ownership
   and readback independent of gameplay Python availability,
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
