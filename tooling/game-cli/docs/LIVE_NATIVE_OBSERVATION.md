# Source-only Live queue observation

`companion-src/foundry_companion/live_observation.py` adds a separate, uninstalled,
default-denied readback seam. It observes one explicitly selected actor through
injected trusted Python readers. It has no game/S4CL imports, service discovery,
bootstrap, installation, event listeners, scheduler, clock, I/O, dispatch or mode
changes. It is not wired into transport, CLI/catalog, host fixture validation or
capability enablement. The existing Build observer is unchanged.

This is synthetic source coverage, **not installed-game qualification**. Neither
successful readback nor a `live` declaration permits selection, cancellation,
mutation, retries or action-completion claims.

## Host interface

```python
LiveQueueObserver(context_reader, sim_info_reader, instance_reader,
                  admission=None, session_id=None, process_epoch=None)
observer.observe(request, expected_context, explicit_actor_id)
observer.capabilities()
```

- `request` is the existing exact companion `Request`, with operation `observe`,
  exact `ObserveArguments`, valid TTL, exact process epoch and no mutation
  preconditions. The seam validates TTL but **does not enforce freshness**.
- `expected_context` is an exact frozen `LiveContext`. Its fields are session ID,
  exact `ProcessEpoch`, canonical positive uint64 decimal text `zone_id`,
  `household_id`, `client_id`, exact uint64 integer `save_slot`, canonical uint64
  text `save_guid`, and exact positive uint64 integer `zone_generation`.
  Zero is permitted for save slot/GUID only. The nested epoch is detached on
  construction and whenever an admission view is copied.
- `explicit_actor_id` is one canonical positive uint64 decimal string. No integer,
  bool, subclass, whitespace, sign, leading zero, Unicode digit, name, actor list,
  active-Sim fallback or iterable selector is accepted.
- Construction captures the exact owner `Thread` object **and** its ident, plus
  immutable session/PID/creation-FILETIME values. An observer with both binding
  inputs omitted is inert; a partially supplied/invalid binding refuses.
  No reset/rebind exists. Capturing a thread does not prove game scheduler ownership.

Before **any** context/actor/instance reader, the **same reviewed host admission
object used elsewhere** must implement:

```python
require_live_read(detached_request, detached_expected_context, actor_id)
```

Only exact `True` admits. Default `DenyAdmission`, generic `require_read`, and
`require_build_read` cannot grant this scope; there is no fallback. Supplying an
object, caller JSON, context, mode string or observation is not independently
verified authority. Host integration must ensure the readers and admission belong
to the same reviewed configuration, including permitted actor/hidden-instance
scope. Household membership is not inferred from context.

The callback receives fresh copies. Immutable primitive baselines detect changed
request/context fields, nested epoch changes and int/float TTL type changes even
when Python numeric equality would compare equal. Retained earlier views and
caller-owned inputs cannot redirect the current copied baseline. Frozen
classes are not treated as alias protection or a sandbox against trusted Python.

### Required trusted adapters

1. `context_reader()` returns an exact plain dict with exactly `context`,
   `zone_running`, and `mode`. Context must be exact `LiveContext`, running exact
   `True`, and mode exact plain string `live`. Missing/changed/malformed values
   refuse the whole call. The generation must change for **every zone lifetime**,
   including same-ID reloads. Stale/lying declarations cannot be detected here.
2. `sim_info_reader(int_actor_id)` resolves only that actor's SimInfo or returns
   `None`. The observer reads `.id`, requiring an exact positive uint64 integer
   equal to the explicit actor. No manager scan or alternative actor lookup exists.
3. `instance_reader(sim_info)` returns an **already existing** Sim or `None`.
   The observer reads `.sim_id` with the same exact identity requirement. The
   trusted adapter must not instantiate, spawn, create, summon, load or make a
   missing Sim selectable, and must document its explicit hidden-Sim policy.
   This seam supplies no default adapter or guessed hidden flags.

Exact mode must be separately qualified. `not in_build_buy`, running status and
an unchanged zone are not enough to exclude CAS/loading/menu/teardown. Shared
native vocabulary is `live`, `cas`, `build_buy`, `loading`, `menu`, `unknown`;
only `live` permits this read. NativeAPI's `HostModeDeclaration`/`observe_mode`
mutation contract is **different** and is not accepted in place of this context
reader. Neither API is a source of independent mode evidence.

## Sequential sampling and failure behavior

The busy guard is set before admission; reentrant reads refuse without running
nested readers. Owner checks surround admission and every sampled operation.
Context samples bracket each adapter call, property access, `iter(container)`
and `next(iterator)`. The context reader itself gets owner checks before/after
and immediate exact-value validation, without recursive admission or sampling.

Ordinary reader exceptions, missing properties and `StopIteration` all get the
post-operation checks. Context/owner loss takes precedence over field/channel
unavailability, exhaustion or overflow, discarding the whole observation.
A process-control `BaseException` such as `KeyboardInterrupt`, `SystemExit` or
`GeneratorExit` instead propagates immediately, without extra context-reader work
that could mask it. Every entered outer call clears its guard on exit; a rejected
reentrant call leaves the active outer guard intact. This control-exception
exception to post-sampling is deliberate; it never produces successful readback.

A missing or invalid initial SimInfo/Sim yields an explicit unavailable actor and
two unavailable channels, with a final context sample. Valid actors are resolved
again after both channels and must have the **same object references** and
matching identities. Their first references remain alive until comparison, so
Python address reuse cannot hide replacement. Disappearance/replacement/identity
failure during revalidation refuses the entire call.

The observer reads `.queue`, then `.si_state` separately. A missing, null, throwing
or non-iterable channel is unavailable rather than empty. An iterator-construction
exception is also channel-unavailable. Every present container is reread after
consumption (also after failed iterator construction), requiring the same reference.
Missing/null channels are not retried inside the call. An iteration exception
other than `StopIteration` refuses the whole call as `iteration_failed`; partial
rows never become successful truncated observations.

All refusals use static `NativeRefusal` messages. No arbitrary exception text,
dynamic exception name, native object formatting, labels, targets or raw
tracebacks are placed in results or diagnostics. Sanitized ordinary refusals are
constructed outside caught reader-exception scopes, without deliberately chaining
the reader error. No invasive traceback surgery is attempted.

## Bound before materialization

`MAX_ROWS = 64` is a **combined** queued/running occurrence budget. Each occurrence
counts, including duplicates, repeated references and malformed fields. Traversal
uses guarded `iter`/`next` directly, never tuple/list conversion, sorting, `len`,
`length_hint`, filtering or a snapshot helper on native iterables.

One extra yielded occurrence detects overflow: the 65th yield reads **no fields**
and refuses as `row_limit_exceeded`. The first 64 are never returned as success.
For two completing channels there are at most two iterator constructions, 65
combined yielded references, 64 parsed rows and 66 `next` calls, including
exhaustion probes. No reported container length substitutes for actual exhaustion.

Only `.guid64` and `.id` are read once per occurrence, independently. Each must
be an exact integer in `1..2**64-1`; valid values become canonical decimal text.
Missing/null/invalid/throwing fields remain explicit unavailable values in the
same row, never inferred from the other ID, Python `id()`, row index or a token.
Tuning GUID64 identifies tuning/type, not a queue occurrence. Optional runtime
ID is a transient observed field, not a proven unique/durable/resolvable handle.

These bounds constrain observer-controlled reads and output, **not execution
time, allocations or side effects inside trusted readers, properties or iterable
methods**. There are no workers, timeout threads, blocking locks or cancellation
claims. S4CL generator helpers are deliberately unused because the reviewed
helpers materialize complete tuples and erase missing-container state.

## Detached result schema, version 1

Every result uses exact plain dict/list/str/int/bool/null values, with compact JSON
limited to 32 KiB. Oversize output refuses; it is never truncated. No native or
caller-owned container escapes.

```json
{
  "schema_version": 1,
  "source": "source_only_injected_readers",
  "source_only": true,
  "read_only": true,
  "live_qualified": false,
  "independent_evidence": false,
  "atomic_snapshot": false,
  "context_source": "injected_host_declaration",
  "identity_scope": "this_call_only",
  "selection_authority": false,
  "cancellation_authority": false,
  "action_outcome": "unknown",
  "request_id": "11111111111111111111111111111111",
  "context": {
    "session_id": "22222222222222222222222222222222",
    "process_epoch": {"pid": "123", "creation_filetime": "456"},
    "zone_id": "10", "household_id": "20", "client_id": "5",
    "save_slot": "4", "save_guid": "99", "zone_generation": "1"
  },
  "actor_sim_id": "30",
  "actor": {"status": "observed"},
  "queued": {"status": "observed", "rows": [
    {"index": 0,
     "tuning_guid64": {"status": "available", "value": "123"},
     "runtime_instance_id": {
       "status": "unavailable", "value": null, "reason": "missing_field"}}
  ]},
  "running": {"status": "unavailable", "rows": null, "reason": "null_value"}
}
```

An unavailable actor uses `{"status":"unavailable","reason":...}`; its channels
both use unavailable status, null rows and `actor_unavailable`. An observed empty
channel has `rows: []`, only after actual exhaustion. No separate availability
booleans can disagree with status.

Unavailable reasons are static: `not_found`, `not_instanced`,
`actor_unavailable`, `identity_mismatch_or_unavailable`, `missing_field`,
`null_value`, `invalid_value`, `not_iterable`, `reader_exception`.

All numeric identity/context metadata, including PID, creation FILETIME, save
slot and generation, are decimal **strings**, avoiding uint64 precision loss.
Schema version and small row indices are numbers. Request/session IDs retain
32-character lowercase hexadecimal format. No timestamps, fixture IDs, receipt,
event history or action result is manufactured.

## Explicit non-guarantees and remaining qualification

- Empty channels do **not** prove idle; `si_state` is not established to cover every
  mixer/action. Row disappearance does not establish completion/failure/cancellation.
- Original order and all duplicates are preserved. An interaction moving from queue
  to running may appear in both; the channels are not an atomic partition or FIFO
  execution promise. Fields within one row may come from different instants.
- Matching references/context cannot prove atomicity, content coherence or ABA
  detection. Transitions that change and revert entirely between finite sequential
  samples are undetectable. `index` only means position within this returned array.
- Equal IDs or repeated request IDs across calls establish no correlation, durable
  handle, receipt, deduplication, retry permission or execution outcome.
- Observer state contains only configured readers/provider, owner/binding and busy
  guard. There is no observer-owned per-call cache, history, last actor/iterator,
  error or traceback. Successful and refused calls retain no native references
  through new observer state. Caller-held traceback frames can retain call locals;
  trusted configured closures/services can retain their own data. Those lifetimes
  are outside this seam's control. Adapters must not add per-call caching on its
  behalf.
- Future authorized host qualification must separately establish process/profile
  authority and lease, owner scheduler, exact Live-mode semantics, zone generation,
  request freshness, permitted actor/hidden-instance scope, native no-create
  behavior, property/iterator behavior and installed-version compatibility.
  Source review and isolated Python 3.7 tests cannot establish those facts.

`src/sims4foundry_cli/live_observation.py` remains an **offline fixture** validator.
Its schema requires valid tuning IDs and cannot express these unavailable native
fields. This separate DTO is not silently coerced into that schema and does not
promote fixture validation into native qualification.

## Primary-source provenance

Interface findings were reviewed 2026-11-04 against public
[DeviantGameMods/Sims4CommunityLibrary commit a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5](https://github.com/DeviantGameMods/Sims4CommunityLibrary/tree/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5),
version 3.23. Reviewed headers credit Copyright (c) DEVIANTGAMEMODS and
[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
This implementation is original neutral code informed by paraphrased interface
findings; it does not copy S4CL implementations, import S4CL, bundle game code or
private Apex exports, or imply endorsement.

- [Explicit actor identity and manager lookup, common_sim_utils.py L267–315](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_utils.py#L267-L315):
  separate SimInfo `.id`, Sim `.sim_id` and exact supplied-ID lookup patterns.
- [Existing instance/hidden policy, L317–355](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_utils.py#L317-L355):
  wrapper forwards `allow_hidden_flags`, defaults to `ALL_HIDDEN_REASONS` despite a
  contradictory docstring signature, and has no explicit spawn call. This does
  **not** qualify native getter side effects or justify guessing hidden policy.
- [Separate/combined channels, common_sim_interaction_utils.py L489–548](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_interaction_utils.py#L489-L548):
  `.queue`, `.si_state`, queued-before-running order, tuple materialization and
  missing-state loss motivate direct bounded traversal rather than helper reuse.
- [Tuning GUID accessor, common_interaction_utils.py L223–235](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/resources/common_interaction_utils.py#L223-L235)
  and [tuning-manager load path L389–434](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/resources/common_interaction_utils.py#L389-L434):
  support the limited `tuning_guid64` meaning, not an occurrence handle.
- [Social continuation runtime reference, L808–823](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_interaction_utils.py#L808-L823):
  a separate runtime `.id` is observed in one continuation path only; uniqueness,
  lifecycle, general resolvability and cancellation are not established.

## Synthetic validation

`tests/test_companion_live_observation.py` exercises zero-reader preflight,
exact types/binding/admission and retained aliases; ownership/reentrancy/control
unwinds; context races including exceptions/exhaustion/overflow; actor/container
revalidation; absent channels and hostile iterables; combined bounds; independent
field availability/redaction; primitive/size bounds; weakref retention and static
non-integration checks. All fixtures are fabricated. Run from the tooling root:

```sh
PYTHONPATH=tests python -m unittest test_companion_live_observation -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

The focused suite is also runnable on actual isolated Python 3.7 with `-B -S -s`
and `PYTHONPATH=tests`. Tests explicitly demonstrate an undetectable between-sample
ABA transition; passing tests never claim atomicity or game qualification.
