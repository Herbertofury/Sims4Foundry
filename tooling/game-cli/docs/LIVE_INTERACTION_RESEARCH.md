# Live interaction source evidence

## Status and provenance

This is a bounded review of public, author-owned source. It is **not installed-game validation, a qualified native adapter, or dispatch support**. No game, profile, desktop, or game process was accessed. No live observations, runtime timestamps, or successful actions are claimed. This document does not enable capabilities or change the source map.

All references below pin **DeviantGameMods/Sims4CommunityLibrary**, commit **`a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5`**, whose [mod metadata][metadata] declares version **3.23** and author **DeviantGameMods**. The inspected file headers state **Copyright (c) DEVIANTGAMEMODS**, licensed under [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). This document contains findings and short paraphrases, not copied implementation.

## Identifiers and precise targeting

- **Interaction tuning ID:** `CommonInteractionUtils.get_interaction_id(interaction)` reads `guid64`; `load_interaction_by_id(...)` resolves `Types.INTERACTION`. This identifies the tuning/type, not a unique occurrence in a queue. The `has_interaction_*` helpers and `skip_if_running` use this type ID, without distinguishing targets or duplicate occurrences. [ID and lookup source][interaction-ids], [queue source][queue].
- **Interaction instance reference:** the social continuation path passes `super_interaction.id` as `source_interaction_id`, separately from its tuning ID. This proves that this field is used for an instance reference in that path. It does not establish general lookup support, persistence, uniqueness scope, or reuse rules. Treat any exported instance reference as transient until qualified. [Continuation source][continuation].
- **Object instance ID:** `CommonObjectUtils.get_object_id(obj)` reads `obj.id`; `get_game_object(id)` resolves through the current object manager. Object GUID, definition ID, and instance ID are different. `create_unique_identifier()` explicitly yields the same identifier for objects of the same type, so it cannot precisely select one placed object. [Object source][objects].
- **Sim ID and presence:** Sim ID resolves through the Sim-info manager; resolving a SimInfo does not guarantee a loaded Sim instance. `get_sim_instance(...)` can return `None`. The implementation defaults to including hidden instances. Names and active-Sim fallback are unsuitable as silent substitutes for an explicit actor ID. [Sim source][sims].

## Queue observation

`get_queued_interactions_gen(sim_info, include_interaction_callback=None)` iterates a tuple snapshot of `sim.queue`; `get_running_interactions_gen(...)` uses `sim.si_state`. Both yield no entries when the Sim instance or corresponding state is missing. **Empty output alone does not establish idle.** The combined reader enumerates queued entries and then running entries separately, with no atomicity or deduplication guarantee. `si_state` coverage is not proof of exhaustive coverage of every mixer or action. [Readers][readers].

Registered object interactions are not a validated action menu: `get_all_interactions_registered_to_object_gen(...)` enumerates super, phone, and relationship-panel affordances without testing current applicability. [Object affordance enumeration][affordances].

## Push and cancellation semantics

- `queue_interaction(sim_info, interaction_id, social_super_interaction_id=None, target=None, picked_object=None, interaction_context=None, skip_if_running=False, **kwargs)` is an enqueue entry point. Its super-interaction path calls `sim.push_super_affordance(...)` with the resolved affordance, target, context, and picked object, returning the native result directly. An annotation is not a guarantee that every returned object is normalized to the wrapper type. [Queue source][queue].
- `CommonEnqueueResult` truth combines the test and execution results. Its success means **enqueue success**, not completion of the requested world-state change. The general queue entry point defaults to insertion at `LAST`, whereas `create_interaction_context(...)` defaults to `NEXT`; default context uses high priority and script-with-user-intent source. [Enqueue result][enqueue-result], [context defaults][context].
- `cancel_interaction(interaction, cancel_reason, finishing_type=FinishingType.USER_CANCEL, **kwargs)` requires an actual interaction object. For super interactions its defaults are `immediate=False`, `ignore_must_run=False`, and `carry_cancel_override=None`; it returns native cancellation's result. Both bulk cancellation helpers discard each individual return and then return `True`. **Bulk success does not verify removal. Cancellation is not rollback.** [Cancellation helpers][cancel].
- A separate qualification concern: the mixer queue path skips a candidate when converted `test_result.result` is truthy, and can fall through without an explicit result. This is a potentially inverted guard, not a demonstrated installed-game failure. [Mixer path][mixer].

## Events, lifecycle, and outcome limitations

- The append hook emits its post-queued event after the native append call and carries its result. The started event follows the native start-event trigger. Neither proves intended action completion. [Queue/start hooks][event-queue].
- The run hook calls `run_interaction_gen`, immediately dispatches the returned value as `run_result`, and returns it. It does not exhaust a generator or use `yield from`. Therefore its boolean annotation and post-run documentation cannot establish terminal execution; the actual returned type and lifecycle need qualification. [Run hook][event-run].
- Outcome events dispatch **before** native `store_result_for_outcome`. Their payload contains the interaction, outcome, and result; the success helper compares against `OutcomeResult.SUCCESS`. That comparison is not evidence that all requested world-state effects finished. [Outcome hook][event-outcome], [payload][outcome-payload].
- Base, mixer, and super cancellation events dispatch **before** the corresponding native `cancel` call. An event named cancelled therefore cannot by itself prove cancellation was accepted or completed. [Cancellation hooks][event-cancel].
- Event handlers are invoked synchronously. A future passive observer should copy primitive observations, return `True`, and avoid blocking. Pre-run and pre-queue listeners can veto work when dispatch returns false. No independent worker-thread access, durable event journal, replay, exactly-once delivery, or lifecycle-wide identity guarantee was established by this review. [Registry][registry], [dispatcher][event-run].

## Smallest future read-only contract

**Proposal only; not implemented here.** Start with an offline-fixture DTO and validator, explicitly marked `offline_fixture` and `native_qualified=false`:

1. Provenance/version and a fixture observation ID; any fixture time is labeled as fixture data, never a runtime observation. Reserve a lifecycle epoch for a future qualified observer.
2. Explicit actor Sim ID plus separate availability flags for Sim instance, queue, and running state. Distinguish unavailable from observed empty.
3. Separate queued/running arrays preserving observed order. Each entry has a snapshot-local handle, tuning GUID64, optional observed runtime instance ID, optional target kind/ID, and a label. Missing data remains missing.
4. Optional factual event records with phase, correlation evidence, and primitive result fields. Do not promote event names into terminal action states.

Do not promise durable handles, atomicity, FIFO execution, exhaustive coverage, automatic deduplication, idle from unavailable data, or success from queue disappearance. Outcome stays **unknown** without an action-specific, independently observed postcondition.

## Gates before native mutation or completion claims

Keep native pushing and cancellation unsupported until a pinned installed-game/version qualification establishes scheduler/thread access, actor and target re-resolution, duplicate-instance selection, stale-reference rejection, accepted-result types, and actual queue readback. Also qualify travel/reload/despawn invalidation, cancellation refusal and delayed removal, event correlation/coverage, and action-specific final-state checks. General completion, rollback, and durable correlation across lifecycle changes are not supported by this source review.

[metadata]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/modinfo.py#L1-L35
[interaction-ids]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/resources/common_interaction_utils.py#L223-L434
[queue]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_interaction_utils.py#L551-L714
[continuation]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_interaction_utils.py#L808-L823
[objects]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/objects/common_object_utils.py#L33-L196
[sims]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_utils.py#L267-L355
[readers]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_interaction_utils.py#L489-L548
[affordances]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/objects/common_object_interaction_utils.py#L44-L75
[enqueue-result]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/classes/testing/common_enqueue_result.py#L14-L87
[context]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_interaction_utils.py#L1318-L1366
[cancel]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_interaction_utils.py#L381-L486
[mixer]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/sims/common_sim_interaction_utils.py#L923-L944
[event-queue]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/events/interaction/common_interaction_event_dispatcher.py#L287-L361
[event-run]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/events/interaction/common_interaction_event_dispatcher.py#L61-L284
[event-outcome]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/events/interaction/common_interaction_event_dispatcher.py#L364-L395
[outcome-payload]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/events/interaction/events/interaction_outcome.py#L45-L97
[event-cancel]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/events/interaction/common_interaction_event_dispatcher.py#L398-L497
[registry]: https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/events/event_handling/common_event_registry.py#L47-L78
