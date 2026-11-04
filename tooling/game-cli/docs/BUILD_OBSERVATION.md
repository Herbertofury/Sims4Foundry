# Source-only Build observation seam

`companion-src/foundry_companion/build_observation.py` advances bounded Build
readback only. It is uninstalled and has no game imports, bootstrap, actual event
hook, scheduler, transport operation, listener, mutation or dispatch method.
Neither this source nor its fabricated tests establishes live Build control.
All returned results state `source_only: true`, `live_qualified: false`,
`independent_evidence: false`, `atomic_snapshot: false` and
`context_source: "injected_host_declaration"`.

There is no entry/exit request, placement, movement, removal, undo, save or generic
command fallback. Existing CLI/protocol operations are unchanged. Nothing in
caller JSON, matching IDs, an observed event, or an admission return establishes
live qualification. No Sims profile, desktop, process or save was used for these
tests, and S4CL is not imported or added as a dependency.

## Small Python-only interface

Construct `BuildObserver(context_reader, object_reader, admission=None,
session_id=None, process_epoch=None)` on the future host's owner thread.

- `observe(request, expected_context, object_ids=())` samples the current mode and
  only the selected object IDs. An empty selection requests mode only.
- `record_transition(request, expected_context, sequence, transition)` records
  an already-observed `entered` or `exited` notification and separately samples
  current mode. It does not request a transition or call an event hook.
- `capabilities()` describes this source-only boundary; it grants nothing.

Both reads reuse the exact existing queue `Request` type, restricted to its
`observe` operation, and `ProcessEpoch`. Results carry the detached request ID
and complete detached session/process/context. The seam does not enqueue,
deduplicate requests, enforce a TTL deadline, timestamp evidence, keep receipts,
or authorize replay. A future qualified owner scheduler must establish and
enforce request TTL/freshness, admission, process lifetime, profile lease and
any applicable durable journal requirements. The request's validated TTL field
alone is not elapsed-time enforcement here.

`BuildContext` contains the expected session ID, `ProcessEpoch`, canonical
positive decimal zone/household/client IDs, save slot, canonical unsigned decimal
save GUID, and positive `zone_generation`. Save slot/GUID may genuinely be zero;
absence is never converted to zero. The host must independently observe a new
`zone_generation` on every zone lifetime, including reloads of the same zone ID.
This number is an **unverified host declaration**, not a native generation or an
OS identity proof. No rebinding or generation-discovery mechanism is supplied.

### Same admission provider, narrower explicit scope

The same independently reviewed host admission object must provide:

```python
require_build_read(detached_request, detached_expected_context, explicit_object_ids)
```

Only exact `True` permits proceeding. The object selection is an immutable tuple
of canonical ID strings; context and request, including their nested epochs,
are detached copies. A callback that changes its current request/context view is
refused. Retaining and changing old views cannot alter captured baselines.
Event recording requests an empty object selection because it only samples mode.

The default provider lacks this narrow approval and denies. A provider that only
implements the existing `require_read` method **cannot** permit Build reads. No
existing transport operation supplies this extra scope or wires this seam into
the queue. This is not a second authority service or caller-supplied permission:
it is an additional, precise read contract on the same trusted host provider.
No production provider ships.

Before any injected reader, the seam checks its captured owner Thread object
**and** ident, rejects reentrancy, validates/copies inputs, checks the captured
session/process binding, and asks that provider once. Default denial, wrong
thread, malformed context or an invalid selection causes no reader calls.
After admission there is no admission loop. Current context is freshly sampled
before and after each object resolver and each detached field read. Every sample
must match all expected context fields and report exact `zone_running: True`.
A mismatch, unavailable context or context exception discards the whole result.
Admission/context callback exceptions become static refusals; arbitrary exception
messages are not included in returned data or displayed refusal text. These
checks are ordinary local defenses, not a sandbox for injected Python.

## Injected readers and detached values

`context_reader()` returns a plain dictionary with:

- `context`: an exact `BuildContext` value matching the expected context.
- `zone_running`: exact boolean, required to be true.
- `in_build_buy`: exact boolean or unavailable. A missing field, `None` or a
  nonboolean does not become false. If any sample is missing or disagrees during
  a read, returned mode is unavailable even if first/last samples agree.

`object_reader(int_id)` resolves only that explicit ID to a native-style object,
or returns `None`. Its read-only shape is:

- `id`: exact positive uint64 integer, matching the selected ID.
- `definition.id`: exact positive uint64 integer.
- `position.x/y/z`: exact int/float values, finite and within the conservative
  numeric bound of plus/minus uint64 maximum; returned as detached floats.
- `orientation.x/y/z/w`: the same finite numeric shape, with squared norm within
  0.0001 of one. Invalid quaternions stay unavailable; no normalization is applied.
- `routing_surface.primary_id`: exact unsigned uint64 integer;
  `secondary_id`: exact signed 32-bit integer; `type`: exact nonnegative signed
  32-bit integer. Enum coercion or interpretation is not guessed here.

Each field is `available` with detached primitive values or `unavailable` with
`value: null` and a bounded static reason. A missing property, getter exception,
nonfinite value, wrong type, or unsupported surface shape is never replaced with
a zero vector/quaternion/surface or fabricated definition. No arbitrary object
representation or property enumeration is performed.

Each present object is resolved twice. Its exact ID and same Python object
reference must survive the field sample. Missing/replaced objects produce an
unavailable row and discard sampled fields. The first reference stays alive
through the second resolution, preventing Python address reuse within that
check; no object reference is retained between calls. The reported
`identity_scope: "this_read_only"` means **only this local resolution interval**.
It cannot prove an object was not replaced between separate observations.

These are **sequential, non-atomic field observations**, not a coherent world or
object snapshot. Same-reference and same-context checks do not freeze properties,
prove unchanged definition/position, detect an ABA change that reverts between
samples, or prove spatial semantics, room membership, placement legality, UI
visibility or persistence. The provider itself can lie. Consumers must not use
this data alone to qualify a mutation or infer a successful Build transaction.

### Bounds and retention

Selections are exact plain lists/tuples containing zero to 32 unique canonical
positive uint64 decimal IDs. Generators, whole-world selectors, duplicate IDs,
coercible numbers and oversized selections are refused before admission/readers.
There is no manager iteration or object search. A present selected ID requires
at most two resolver calls; all field/property names are fixed. Intermediate
samples and output are bounded by the selection. A reader's own execution time
cannot be preempted or bounded by this Python seam.

Between calls the observer retains only its configured readers/provider, owner
binding, last accepted context tuple, last event sequence/kind and one gap flag.
No growing event history or native-object identity cache exists. Caller mutation
of returned dictionaries cannot rewrite retained state.

## Transition semantics and unknown state

A notification is an observation, never a command or an independent readback of
current Build mode. Current mode comes only from the separately injected context
reader. Event/mode agreement is reported as `matches_sampled_mode`, not proof.
A just-received `entered` event can coexist with sampled false or unavailable
mode; it does not overwrite it. Late start, absent events, changed zone context,
and same-ID zone reloads cannot establish the initial mode. Outputs always keep
`initial_mode_known: false` and `current_mode_from_events: null`.

Only the last event is retained for the last successfully observed context:

- First receipt is labeled `first`, regardless of its sequence number.
- Same sequence and kind is `duplicate` and does not advance the sequence.
- Lower sequence or same sequence with another kind is refused.
- Consecutive sequence is `contiguous`; a larger jump is `gap` and sets a sticky
  gap-observed flag for that context. A repeated kind at a later sequence is also
  explicitly labeled `repeated_transition`.
- An observed context change resets the receipt baseline. A stale event carrying
  the previous expected context fails fresh-context validation; a failed event
  sample does not commit its sequence.

The sequence must originate from a trusted source that defines its meaning.
A counter incremented only after delivery cannot reveal events never delivered.
No observed gap is **not** a continuity guarantee. Missing notifications do not
prevent a new independent mode sample from reporting a different mode. This seam
cannot establish event provenance or distinguish a fabricated fresh event from a
real event if the injected host lies.

## Source observations and attribution

The bounded public source review used Sims 4 Community Library 3.23 at commit
`a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5`, by **DEVIANTGAMEMODS**, licensed
[CC BY 4.0, as stated in that revision's README](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/README.md#copyright).
This is new neutral code informed by observed interfaces; no S4CL source was
copied or imported, and no endorsement is implied.

- [Build/Buy dispatcher, lines 39–65](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/events/build_buy/common_build_buy_event_dispatcher.py#L39-L65)
  wraps `Zone.on_build_buy_enter` and `Zone.on_build_buy_exit`. It calls the
  original first, then emits the event. An original exception means no event.
  This supports notification semantics, not an entry/exit request API.
- [Object identity utilities, lines 185–283](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/objects/common_object_utils.py#L185-L283)
  resolve explicit IDs through `services.object_manager().get(id)`.
- [Object location utilities, lines 180–319](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/objects/common_object_location_utils.py#L180-L319)
  expose location, position, orientation and routing-surface observations.

The reviewed sources do not establish a qualified Build placement, movement or
undo transaction. `objects.system.create_object` or assigning a location must
not be renamed or advertised as those transactions. Actual native property/enum
behavior, host generation tracking, event coexistence, qualification and
controlled live readback remain unimplemented or unverified.

## Synthetic verification

From `tooling/game-cli`:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_build_observation.py -v
PYTHONPATH=src /path/to/isolated/python3.7 -m unittest discover -s tests -p test_build_observation.py -v
```

The focused tests run on current Python and the existing isolated official-source
Python 3.7.17 interpreter. They cover denial before readers, expanded admission
scope, exact owner/thread and context binding, copied inputs/results, stale
process/zone lifetimes, missing/current mode disagreement, duplicates/gaps/missed
events, failed event commit, object disappearance/reuse, missing fields,
exceptions, nonfinite/oversize values, quaternion/routing validation, bounded
selection/retention and reentrant callbacks. This is synthetic evidence about
this source, not game-build, native-runtime or live transaction qualification.
