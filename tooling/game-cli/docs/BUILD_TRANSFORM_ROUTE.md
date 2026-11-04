# Standalone retained-selection Build route

This checkpoint integrates one concrete path: capture an explicitly selected
existing world object, then attempt one direct move/rotation of that retained
object. It uses the neutral shared queue, transport, process-bound peer policy,
durable journal and owner runtime. It does not import or require Apex.

**Implemented and fixture-tested, not game-qualified.** No production provider,
automatic bootstrap, credentials, permissive admission or profile authority is
included. The current desktop game/profile remains untouched. The earlier
denied owner/transport re-review is not completed by these new-feature tests.

## What the route checks

- Schema 4 has exactly `build.capture_transform` and
  `build.transform_selected`. Unknown operations/types do not become reads.
- Both requests bind the complete canonical intent, session, PID and process
  creation time, deployment SHA-256, running Build context, save slot/GUID,
  zone/household/client, zone generation and bounded TTL. Numeric spelling,
  including signed zero, remains part of the canonical binding.
- Capture requires the exact expected object ID, definition, world position,
  quaternion and routing surface. It retains the resolved Python object itself.
  Selection identity begins at this capture, not an earlier observation.
- Transform consumes that selection on its first admitted attempt, before native
  preflight. Resolution must still return that same object by identity. A new
  object with the same ID, definition and pose is refused.
- Placement checks are followed by fresh full-request admission, exact retained
  identity/readback and a current-owner-hook check before `move_to`. Mode/context,
  definition or pose changes, expiry, callback-time hook replacement and shutdown
  prevent reuse. Observed mode exit invalidates retained selections permanently.
- The bounded owner-only registry retains consumed/expired ID tombstones and
  releases object references. It cannot renew, rebind or evict an ID for reuse.
- All schema versions share request-ID conflicts, history limits and the sticky
  unresolved-mutation barrier. Capture is a read; transform is a mutation.

Readers and callbacks are trusted synchronous host code, not sandboxed. Sampling
does not prove atomic state or detect every transition between ticks. The real
provider must establish that retained Python identity corresponds to a native
object lifetime. The deployment digest is a frozen declaration/correlation value,
not independent proof of a profile, artifact installation or authentication.

## Consume it from another project

Build the development archive using the existing explicit trusted-compiler flow:

```sh
foundry-game companion-build --source ./companion-src \
  --output /external/builds/release-001/FoundryCompanion.ts4script \
  --python37 /trusted/python37/python --compiler-sha256 VERIFIED_COMPILER_SHA256
```

The archive now contains 21 modules and 42 source/bytecode members. The host CLI
requires Python 3.11+; the companion is tested on actual CPython 3.7.17 with magic
`420d0d0a`. This command does not install or launch anything.

For an independently admitted host, the exact integration interfaces are:

1. Construct `foundry_companion.owner_runtime.BuildReaders` with a frozen
   deployment digest, explicit context/object readers, vector/quaternion/surface/
   transform/location factories, world-surface type, placement test and admission.
   The admission method is `require_build_request(exact_BuildRequest) -> True`.
   Missing admission denies. Factories follow the verified published Build
   adapter interfaces, not a guessed UI or Apex bridge.
2. Pass it as `build_readers=` to `OwnerRuntime`, alongside the independently
   verified session/process binding, existing host policy and admitted modules.
   Installation remains explicit, outside a tick. The original owner tick runs
   first. The runtime supplies the current-hook predicate; no wire flag can
   supply or override it.
3. On the host, use `sims4foundry_cli.companion_build_protocol.BuildRequest`,
   `BuildExpectedContext`, `BuildCaptureArguments`, `BuildSurface` and
   `BuildSubmission`. IDs are canonical strings; frozen vectors are tuples.
   Submit only with `companion_journal.Journal.submit_once`, using the existing
   independently verified authorization and exchange callbacks.
4. The exchange callback calls `companion_build_transport.exchange_once` with
   an explicit bound port and independently authorized peer. It sends one
   application message, without retry/reconnect/downgrade. The journal, not this
   stateless sender, prevents repetition across calls.
5. Read the original receipt through `Journal.reconcile`. Validate the captured
   raw receipt using `companion_build_protocol.capture_ready(receipt, submission)`
   before constructing a transform. This returns only the original capture
   fields plus `capture_request_id` and `capture_request_sha256`. Convert JSON
   vector lists with `arguments_from_wire(..., 'build.transform_selected')` when
   adding destination/yaw/level, or construct the exact typed arguments directly.
6. Submit the transform as a new journal intent, preserving the same session,
   process/deployment/context and exact capture fields. Never replace a missing,
   uncertain or expired original intent with a new ID to make it run.

The journal retains only its existing sanitized receipt summary, not arbitrary
adapter payloads. A consumer may validate the raw response within its exchange
callback. Lost responses permit only reads of the exact original receipt. The
original intent supplies the recoverable capture ID/digest; it does not by itself
prove capture readiness or that the in-memory selection is still alive.

There is deliberately no production command that accepts an arbitrary local
policy, assumes a live session or makes a permissive fixture provider real.
Other tasks can consume this API after supplying and qualifying their host
integration. They cannot yet use it as verified autonomous game control.

## Honest outcomes and remaining work

All adapter results have `outcome: unverified`, `game_qualified: false`,
`transaction_verified: false` and `undo_recorded: false`. `dispatched` means the
single native call was attempted and read back, not independently proved success.
Even a placement refusal returned after queue start is an adapter assertion.
Any uncertain mutation remains unresolved in the shared queue/journal and blocks
later mutations. This slice does not relax that barrier for repeated moves.

The five earlier Build modules are packaged with their public commit/blob
lineage. Create is not purchase/placement, destroy is not sale/refund, and
definition changes are not qualified appearance/swatches. Those operations are
not exposed through schema 4. Purchase, sale, undo/redo, full CAS control, normal
game exit and a complete independently admitted game bootstrap remain separate
missing integration/qualification work.

## Essential offline checks

From the repository root with the host package on `PYTHONPATH`:

```sh
PYTHONPATH=tooling/game-cli/src:tooling/game-cli/tests python -B -m unittest \
  test_build_executor test_build_runtime_route test_build_transform
```

These use owned fake objects/core modules. Runtime fixtures suppress listener
startup and exercise the real shared transport ledger, queue and owner hook.
They cover capture/transform, replacement identity, missing providers, late
admission loss, hook replacement, expiry, consumed selections and no replay.
Additional protocol/journal fixtures cover canonical bytes, cross-schema ID
conflicts, malformed/budgeted receipts and restart/lost-response reconciliation.
Fixture allow-all policies are never packaged as production admission.
