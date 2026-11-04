# Neutral owner-thread lifecycle

`companion-src/foundry_companion/owner_runtime.py` is an explicit, source-only
lifecycle integration for the existing `LoopbackTransport`, `NativeAPI`, and
optional typed `LiveQueueExecutor`. It has **no required Apex dependency** and
does not import Apex or copy its runtime. It is not an installed game bootstrap,
a production admission provider, or proof of compatibility with a current game.

## What is connected

An explicitly trusted host can construct `OwnerRuntime`, call `install()` once
on the verified game owner thread, and later call `close()` on that same thread
outside a tick. Construction and import start no socket, thread, game import or
hook. The injected module loader is called only during explicit installation for
`sims4.core_services` and `services`. Both results must be exact module objects.

The wrapper calls the previously installed `core_services.on_tick` first with
the same positional and keyword arguments. It returns the identical original
result. Exceptions from the original, including control exceptions, propagate
unchanged and prevent queue draining on that invocation. An earlier mod's hook
is treated as the original; its attributes are not copied into Foundry's wrapper.

After a successful original call, an outermost owner invocation attempts at most
one `transport.owner_tick(execute)` drain. Foreign-thread invocations call only
the original. Nested calls from either the original or the executor also call
the original, but never dispatch again. Queue contention skips without waiting
or attempting a second drain on that tick. The existing queue decides which
requests, if any, were consumed and retains their exact receipts.

The adapter routes exact typed requests, not arbitrary operation strings:

- Legacy `Request` goes to `NativeAPI` for its existing four operations:
  observation, clock speed, guarded CAS entry request, and quit-menu request.
- `LiveQueueRequest` goes only to an explicitly configured `LiveQueueExecutor`
  for the existing actor-bound Live read. It never falls back to the legacy API.
- Other types or an absent Live executor are refused. The existing queue treats
  exceptions after its started boundary as unknown, even when the adapter did
  not reach a native call. This lifecycle does not reinterpret that uncertainty.

The host session and process epoch are copied into the native/Live components
from the transport's captured binding. Binding objects returned for inspection
are detached copies. There is no rebind or replacement-executor API.

## Host obligations and default denial

The default transport policy denies listener admission. Native admission also
defaults denied independently. Optional Live readers require their own exact
Live-read admission. No allow-all production policy, wire field, CLI enable flag,
credential provisioning or peer-authentication shortcut is included.

A qualified host must independently establish the actual owner thread, exact
process/profile/session, peer authentication, authorized intent, durable history
and reconciliation, fresh context, operation-specific admission, and installed
game qualification. Capturing the calling Python thread or accepting a typed
epoch is **not** proof of any of those facts. The four native operations retain
their existing mode checks, narrow arguments, CAS preservation/frontdoor needs,
and unqualified outcomes.

Illustrative composition, using an already reviewed host object rather than a
sample permissive policy:

```python
from foundry_companion.owner_runtime import OwnerRuntime

owner = OwnerRuntime(
    host.session_id,
    host.process_epoch,
    module_loader=host.load_core_or_services,
    host_policy=host.transport_policy,
    native_admission=host.native_admission,
    command_modules=host.already_loaded_command_modules,
    live_readers=host.live_readers,  # None when the typed read is unavailable.
)
address = owner.install(port=0)  # Explicitly outside a tick, on the verified owner.
# Later, outside a tick on the same owner:
clean = owner.close(timeout=2.0)
```

The host object in this example is a contract, not an implementation supplied by
this repository. `LiveReaders` packages the three existing synchronous reader
callables and their separate admission provider. It supplies no game traversal.

## No lifecycle I/O on ticks

The hook performs no socket/file/journal I/O, worker joins, thread creation,
logging, reporting callback, cleanup or module-loader calls. The transport's
owner path remains its nonblocking queue path. Host admission, mode observation,
native services and optional Live readers are trusted synchronous code and must
also be bounded and avoid I/O or waits. This is not a sandbox capable of making
arbitrary injected callbacks safe or interrupting a slow native operation.

`NativeAPI` ordinarily resolves command modules after mutation admission. The
lifecycle supplies a **cache-only** resolver for that seam. The host may provide
already-loaded modules with only these four names:

- `server_commands.clock_commands`
- `server_commands.cas_commands`
- `server_commands.argument_helpers`
- `sims4.commands`

The cache dictionary is copied, and its entries must be exact module objects.
There is no import fallback. Missing modules produce an execution refusal; the
host must not mistake the resulting unknown receipt for safe replay. This avoids
both loader/I/O on a tick and eager import-time effects before native operation
admission. The host is responsible for any previous module loading and its
authorization. The cached module objects themselves remain trusted references.

## Hook ownership, faults and shutdown

Installation is one-shot, including validation failure, loader failure, listener
denial or an uncertain thread startup. A claimed core module remains claimed for
its lifetime, even after clean closure: a second `OwnerRuntime` cannot overwrite
or restart it. The claim is an in-memory Python object, not an OS registry write.
Do not reload this module or replace the core to bypass that lifetime boundary.
Process restart and durable-history reconciliation belong to a separately
authorized host lifecycle, not this API.

Foundry attaches its wrapper disabled, starts the listener explicitly, and only
then enables draining if its exact hook is still current. Failure disables the
hook, restores it only if it is still the exact current callable, and attempts
shutdown once. The original startup exception is preserved even if cleanup also
fails. Failed cleanup is visible in `status.cleanup_complete`; nothing retries
installation, rebinds, clears receipts, or declares uncertain work unexecuted.

If another mod replaces `on_tick`, Foundry disables further dispatch when its
retained wrapper is next invoked, even if that wrapper is still in the later
mod's chain. It does not walk or rewrite that chain. `close()` restores the
captured original only when `core.on_tick` is Foundry's exact callable; otherwise
the later mod's hook is untouched. A retained Foundry wrapper stays inert after
close. Replacement during dispatch admission is checked again before entering
the executor. Concurrent or side-effectful host code is not made atomic by these
checks.

Unexpected dispatcher exceptions, including dispatcher control exceptions, are
isolated after the original succeeds: the original result is returned and future
dispatch is latched off. The queue owns the failed request's retained outcome.
This differs deliberately from original-tick exceptions, which propagate
unchanged. No tick attempts listener shutdown in response to such a fault.

The host must inspect lifecycle status and call `close()` outside the tick when
needed. An in-tick `close()` disables future dispatch and raises
`close_inside_tick` without I/O; cleanup remains pending for an explicit later
call. Outside ticks, `close()` disables first, conditionally restores its own hook,
and invokes the transport's bounded shutdown. `False` or an exception means
cleanup is pending. Another explicit close can finish cleanup but cannot start
anything or clear uncertainty. Existing in-flight native work is not preempted.

`status.enabled` describes only this lifecycle's dispatch latch, not peer
authentication, listener health, operation admission or game qualification. An
address is the transport's historical bound endpoint, not proof it is still
listening. No telemetry, periodic cleanup, retry timer or automatic recovery is
installed.

## Evidence and verification scope

The integration follows the previously inspected core-tick call shape and
original-first ordering. It does not incorporate unrelated hooks, zone rebinding,
alarms, profile handling or wholesale source. Existing narrow native operations
and typed Live reads remain their own implementations.

`tests/test_companion_owner_runtime.py` exercises fabricated owned modules,
services and policies, both fake lifecycle transports and the actual localhost
transport/native/Live components. Coverage includes exact original behavior,
thread ownership, nested ticks, nonblocking contention, competing wrappers,
startup and cleanup failures, one-shot claims, independent default denial,
cache-only command resolution, and no hook-side I/O. The fixture's permissive
policy is labeled test-only and is not a usable production provider.

Run under a development interpreter and an actual Python 3.7 interpreter:

```sh
python -m unittest discover -s tests -p test_companion_owner_runtime.py -v
python3.7 -m unittest discover -s tests -p test_companion_owner_runtime.py -v
```

These are isolated source/runtime fixture results. No owned desktop, game process,
profile, save, real Sim, actual UI, native Windows input or current-game build was
used. The lifecycle does not install a script archive, launch/stop a game, write
profiles/saves/registry, add full CAS or Build control, or execute arbitrary game
commands. Full CAS/Live/Build operation coverage and real-game qualification
remain open work.
