# Offline Live interaction observation contract

`src/sims4foundry_cli/live_observation.py` validates explicitly supplied, fabricated
fixture data and returns a detached plain dictionary. It reads no game objects,
files, clocks, process, profile, desktop or network. It has no reader callback,
event hook, scheduler, listener, native import, push/cancel method or mutation.
There is no CLI, capability, companion or protocol integration.

The contract makes future readback requirements precise. It is **not a Live
observer, native adapter or game-qualified implementation**. Even structurally
valid fixtures may be completely fabricated. Nothing in a matching ID, label,
availability flag or result establishes identity, freshness, authority or game
compatibility. The CLI client requires Python 3.11+; this module is not an
installable in-game script.

## Python-only interface

`validate_live_fixture(value)` accepts one exact plain dictionary and returns a
fresh plain dictionary. Invalid or oversized data raises `LiveObservationError`
with a static message and one of these codes: `invalid_shape`, `invalid_value`,
`inconsistent_availability`, `limit_exceeded`. Diagnostics do not echo supplied
values or unknown field names. JSON text, bytes, generators, arbitrary objects
and container/primitive subclasses are not accepted. No conversion is attempted.

Example input, written as JSON for readability:

```json
{
  "schema_version": 1,
  "fixture_observation_id": "example-1",
  "fixture_epoch": "example-epoch-1",
  "actor_sim_id": "10",
  "sim_instance_available": true,
  "queue_available": true,
  "running_available": false,
  "queued": [
    {
      "tuning_guid64": "100",
      "runtime_instance_id": "200",
      "target": {"kind": "object", "id": "300"},
      "label": "Fixture interaction"
    },
    {"tuning_guid64": "100"}
  ],
  "running": null
}
```

All nine root fields are required; extra fields are refused:

- `schema_version`: exact integer `1`, never boolean or float.
- `fixture_observation_id`, `fixture_epoch`: nonempty ASCII labels of 1–64
  characters. The first character is a letter/digit; subsequent characters may
  also contain `.`, `_` or `-`. Both labels are arbitrary fixture annotations.
  They may repeat and do not prove an observed process/session/zone epoch,
  monotonicity, uniqueness, freshness or provenance. No real timestamp is added.
- `actor_sim_id`: explicit canonical positive uint64 decimal string. It is never
  inferred from the active Sim, a display name, a target or another row.
- `sim_instance_available`, `queue_available`, `running_available`: exact
  booleans declaring availability in the supplied fixture, not independently
  observed facts.
- `queued`, `running`: separate plain arrays when that channel is available,
  otherwise `null`. At most 64 rows combined are accepted.

An unavailable Sim requires both channels unavailable and both arrays `null`.
An available Sim permits either or both channels unavailable independently.
Available channel plus `[]` means only **observed empty in this fixture**;
unavailable plus `[]`, or available plus `null`, is inconsistent and refused.
SimInfo existence alone must never be substituted for Sim-instance availability.

## Interaction rows and identity limits

Each row requires `tuning_guid64` and permits only these optional fields:

- `tuning_guid64`: canonical positive uint64 decimal string identifying tuning/type.
  It is not a unique queued or running occurrence.
- `runtime_instance_id`: optional canonical positive uint64 decimal string,
  otherwise `null`. Missing stays `null`; the validator does not substitute the tuning ID. This
  is only a fixture-declared transient reference, not a durable identity or a
  lookup/cancellation capability. Its uniqueness, lifetime and reuse rules are
  not established.
- `target`: optional exact object with `kind` (`sim` or `object`) and canonical
  positive uint64 decimal-string `id`, otherwise `null`. Both fields are mandatory
  when a target is supplied. The ID denotes the declared instance, not an object definition or
  tuning GUID. Missing means unavailable, not "no target", "self" or "active
  Sim". Target resolution is never attempted.
- `label`: optional nonempty string of at most 256 UTF-8 bytes, otherwise `null`.
  This is inert fixture text, never an action state or semantic identifier.

All IDs remain exact strings in the output, including values above `2**53`.
Only ASCII decimal digits with no leading zero are allowed, within `1` through
`18446744073709551615`. Integer/float inputs, booleans, zero, negative values,
signs, whitespace, leading zeros, Unicode digits, exponent forms and values above
uint64 are refused. The validator never converts numeric input to text. This
representation preserves identity through JSON consumers without depending on
IEEE-754/JavaScript integer precision. Do not convert the strings through floats.

Rows preserve their supplied array order. Repeated tuning IDs, different targets,
identical rows and the same runtime ID in both arrays are all retained. There is
no deduplication, inferred correlation or merge between queued and running.
Observed order does not promise FIFO execution or exhaustive mixer coverage.

The result adds `occurrence_handle` to every row, such as `queued:0` or `running:1`.
This handle identifies **only that position within that one returned snapshot**.
It is not derived from tuning/runtime IDs, cannot select a game interaction and
must not be used to correlate later snapshots. Handles can repeat for entirely
different rows on the next call, even with identical fixture labels. Caller-
supplied handles are rejected rather than trusted.

## Output and evidence boundary

The output retains root fields and normalizes absent optional row fields to
`null`. It always adds these constants; callers cannot supply or override them
through fixture input, even with the same value:

```json
{
  "source": "offline_fixture",
  "atomic_snapshot": false,
  "live_qualified": false,
  "independent_evidence": false,
  "action_outcome": "unknown",
  "occurrence_handle_scope": "this_snapshot_only"
}
```

The function retains no state. It copies every output array, row and target
dictionary separately, including when input containers were aliased. Mutating
the input after a call cannot change the result; changing one returned row
cannot change another row or a future result. The output is still ordinary
mutable data, not an immutable trust object. Editing its flags confers no
authority. Callers must not mutate input concurrently during validation; this
function does not lock input or promise atomic capture.

No event schema, timer, lifecycle tracker or action-result field is accepted.
Empty arrays, absent rows in a later fixture, enqueue truth and labels such as
"completed" or "cancelled" cannot change `action_outcome`. No idle, completion,
cancellation, rollback or persistence conclusion is produced. An action-specific
postcondition must be independently observed by a separately qualified future
implementation before any terminal world-state claim is possible.

## Bounds and malformed data

Before any JSON encoding or detached row construction, a preflight accepts only
plain dictionaries/lists and bounded primitive values. Input is limited to four
nested containers and 1,024 nodes, counting object keys and values. Container
sizes are checked before traversal. Cycles, custom types, invalid UTF-8 strings
and every float (including NaN/infinity) are refused. All input strings, including
keys, have a 256-byte UTF-8 bound. Fixture labels have the narrower ASCII limit.

Root/channel shape and the combined 64-row limit are checked before row copies.
Each row's exact fields are checked before copying that row.
Input and normalized output must each fit 32 KiB when encoded as compact UTF-8
JSON (`ensure_ascii=False`, separators `,` and `:`). Encoding is counted in bounded
chunks rather than accumulated as a second complete JSON document. JSON escape
expansion counts toward the limit. There is no permissive metadata bag, arbitrary
nesting, extra field, hidden native reference or exception-text passthrough.

These are finite data/work bounds, not timing guarantees or a sandbox for code
that changes the module or concurrently mutates its inputs.

## Public source evidence and attribution

This is new neutral code informed by the bounded findings in
[Live interaction research](LIVE_INTERACTION_RESEARCH.md), not copied S4CL
implementation. That review pinned **DeviantGameMods/Sims4CommunityLibrary**
commit **`a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5`**. Its
[metadata](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/modinfo.py#L1-L35)
declares version 3.23 and author DeviantGameMods. Inspected headers state
**Copyright (c) DEVIANTGAMEMODS**, licensed under
[Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
No dependency, source redistribution, installed-game compatibility or endorsement
is implied.

- [Interaction ID and tuning lookup](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/resources/common_interaction_utils.py#L223-L434)
  uses `guid64`; the separate
  [continuation path](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_interaction_utils.py#L808-L823)
  uses `super_interaction.id` as an instance reference without proving durable
  identity or general lookup support.
- [Queue and running readers](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_interaction_utils.py#L489-L548)
  observe `sim.queue` and `sim.si_state` separately and can yield no rows when
  either the Sim instance or corresponding state is unavailable. This supports
  separate availability and ordered arrays, not atomic or deduplicated readback.
- [Sim presence](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_utils.py#L267-L355)
  distinguishes SimInfo from a loaded instance;
  [object identity](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/objects/common_object_utils.py#L33-L196)
  distinguishes object instance IDs from type/definition identifiers.
- [Enqueue result](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/classes/testing/common_enqueue_result.py#L14-L87),
  [run hooks](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/events/interaction/common_interaction_event_dispatcher.py#L61-L284)
  and [cancellation hooks](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/events/interaction/common_interaction_event_dispatcher.py#L398-L497)
  do not establish successful action completion or accepted/completed cancellation
  from a result's truth value or an event name.

## Synthetic verification

From `tooling/game-cli`:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_live_observation.py -v
```

Tests use tiny fabricated dictionaries and cover unavailable/empty consistency,
ordered duplicates and different targets, same transient IDs across channels,
snapshot-local handles, null/missing optional fields, detached copies/aliases,
fixed output labels, unknown outcomes, canonical uint64 strings and lossless JSON
round trips around `2**53` and uint64 maximum, malformed shapes,
nonfinite/coercible/custom types, cycles/depth/node/row/string/byte limits and
input-free errors. They do not access a game or qualify live behavior.
