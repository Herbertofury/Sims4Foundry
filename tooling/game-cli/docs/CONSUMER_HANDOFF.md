# Handoff for Apex and external CAS tasks

Use the existing Modernize/Apex controller for its established game paths while
the neutral Foundry provider is completed. Foundry does not replace those paths
with computer use. It also does not claim that a working Apex dependency is an
independent no-Apex implementation.

## Use Foundry now

Install from the selected Sims4Foundry checkout with Python 3.11 or later:

```text
python -m pip install ./tooling/game-cli
foundry-game capabilities --source-details
foundry-game apex-inventory --repository "APEX_CHECKOUT"
```

`apex-inventory` parses source and reports its declarations/hashes. It does not
import the helper, contact a bridge, admit a game session or certify its current
behavior. Inspect the output against the actual owner's checkout, not an old
snapshot or a similarly named journal.

These commands perform real offline mod work without a screen or game process:

```text
foundry-game mods inventory --directory "EXPLICIT_MOD_SOURCE" --output "NEW_INVENTORY.json"
foundry-game mods resources --file "CANDIDATE.package" --output "NEW_RESOURCES.json"
foundry-game mods overlaps --file "FIRST.package" --file "SECOND.package" --output "NEW_OVERLAPS.json"
foundry-game mods resource-diff --before "OLD.package" --after "CANDIDATE.package" --output "NEW_RESOURCE_DIFF.json"
foundry-game mods bytecode --file "CANDIDATE.ts4script" --target-magic 420d0d0a
foundry-game mods stage --manifest "NEW_INVENTORY.json" --destination "NEW_EXTERNAL_STAGING" --execute
```

Select only sources the task is authorized to read. Staging creates verified
copies in a new external directory; it is not installation into Mods, a profile
swap or a compatibility verdict. The explicit bytecode magic is a comparison
target, not proof of the installed game's interpreter version. Resource bodies
are not imported, executed, decoded or installed by these inspection commands.

`discover`, `inspect` and pinned `capture` provide process/window identity and
WGC/FFmpeg components. Their Windows fixture tests are not Sims qualification.
Use them only within the consumer task's authorized scope; they are not an
alternate route around a denied process/profile inspection. `launch-plan` only
prepares read-only EA metadata and does not launch a game.

## Reuse Modernize's existing game methods

The supplied control handoff and source review established these paths:

- EA launch through the existing launcher and one-shot ordinary permission
  broker. The historical integrated invocation was `python tools/apex_cli.py
  launch --state STATE --game-root GAME_ROOT --execute
  --elevate-permission-once`. It requests ordinary elevation, not a bypass.
  The historical helper invoked EA's exact permission-dialog OK; Windows UAC
  was a separate prompt and was not automated.
- Background capture through pinned WGC/FFmpeg, independently of Apex input.
- Background clicks/keys through the installed Apex bridge and in-game DLL,
  avoiding foreground or OS-cursor movement in that established path.
- Native CAS entry and semantic CAS panel/catalog/edit operations through the
  existing guarded Apex owner/UI service, not coordinate guessing.
- Journaled Resume and normal exit orchestration, using the existing native
  request plus captured menu/confirmation readback and process-exit observation.

These methods were not discarded or treated as unresearched. Their source
lineage and dependency boundaries are in [MIGRATION.md](MIGRATION.md) and
[UI_ADAPTER.md](UI_ADAPTER.md). Historical success does not establish current
helper hashes, current profile ownership or every operation's current-patch
behavior. A parsed command name is not a passing game test.

The reviewed historical CLI exposes these exact command families, to be checked
against the current owner's checkout before use:

```text
python tools/apex_cli.py wait --state "CURRENT_VERIFIED_STATE" --seconds 30
python tools/apex_cli.py probe --state "CURRENT_VERIFIED_STATE" --output "NEW_EXTERNAL_PROOF.json"
python tools/apex_cli.py game resume --state "CURRENT_VERIFIED_STATE" --background --output "NEW_RESUME_PROOF.json"
python tools/apex_cli.py game cas --state "CURRENT_VERIFIED_STATE" --sim-id "EXACT_SIM_ID" --value full --seconds 45 --output "NEW_ENTRY_PROOF.json"
python -X utf8 tools/apex_cli.py cas status --state "CURRENT_VERIFIED_STATE" --sim-id "EXACT_SIM_ID" --output "NEW_CAS_STATUS.json"
python -X utf8 tools/apex_cli.py cas panel --panel skin_details --state "CURRENT_VERIFIED_STATE" --sim-id "EXACT_SIM_ID"
python -X utf8 tools/apex_cli.py cas outfit --category 0 --index 1 --state "CURRENT_VERIFIED_STATE" --sim-id "EXACT_SIM_ID"
python -X utf8 tools/apex_cli.py cas result --request-id "RETURNED_REQUEST_ID" --state "CURRENT_VERIFIED_STATE" --sim-id "EXACT_SIM_ID"
python tools/apex_cli.py game shutdown --state "CURRENT_VERIFIED_STATE" --background --output "NEW_SAVE_EXIT_PROOF.json"
python tools/apex_cli.py game exit --state "CURRENT_VERIFIED_STATE" --background --sim-id "EXACT_SIM_ID" --household-id "EXACT_HOUSEHOLD_ID" --save-guid "EXACT_SAVE_GUID" --output "NEW_UNSAVED_EXIT_PROOF.json"
```

These are command templates, not instructions to run against an unbound active
session. `shutdown` is the save-and-exit path. `game quit` merely requests the
native quit menu; a successful request alone is not process exit. The separate
unsaved `game exit` path additionally requires exact Sim, household and save
GUID plus its own proof. Do not substitute saving, discarding or termination for
the user's intended exit policy. CAS return/accept and preservation commits have
their own recorded continuation and must not be replayed after uncertainty.

The retained evidence supports a background unsaved normal exit and a separate
Save and Exit with slot rewrite plus process exit. CAS evidence includes a
primary-Human entry/handshake with 72 raw catalog records and a scoped Fairy
edit/return/away-back case. An earlier crash and failed variant selection remain
part of that record. Save rewrite alone does not prove reload/appearance
retention, and these narrow successes do not establish all CAS controls.

The current task owner must reconcile the exact process lifetime, disposable
marker, installed artifacts, save identity and existing journal before using a
game-facing path. Do not reuse an old journal with a new marker, fabricate a
restoration baseline, restore changed files, or take over another active task's
session. Preserve user changes and use the existing owner's coordination.

## New neutral Live read integration

The source-only neutral path now includes `LiveQueueRequest`, its exact
actor/context binding, an injected owner-thread reader, complete receipt
validation, an intent journal and a separate `companion_live_transport.exchange_once`.
It works against authenticated fabricated current-Python and actual Python 3.7
fixture peers. See [LIVE_READ_ROUTE.md](LIVE_READ_ROUTE.md) and
[LIVE_EXCHANGE.md](LIVE_EXCHANGE.md).

Consumers must not point those fixtures or public test keys at a game. A real
host still needs its own installed companion, native readers and owner scheduler,
exact process/profile permission, session-key provisioning and durable shared
history reconciliation. The CLI has no live-send switch. Default gates remain
denied, and a returned adapter claim is not independent evidence.

## What is still missing

- A deployed, current-game-qualified neutral provider and normal launch/entry/
  save/reload/exit workflow independent of Apex.
- A single current CAS UI owner integrated with existing preservation rules;
  complete neutral CAS controls and non-Apex input qualification.
- Build placement/edit/remove/undo semantics. The standalone Build observer is
  an injected read seam, not a Build controller or transition command.
- Current-game validation for wheel/drag/text and full Live interaction actions.
- Completion of the earlier final independent owner/transport re-review. The
  separate host-sender review does not replace that incomplete assessment.

Use [VALIDATION.md](VALIDATION.md) for exact source/test evidence and CI status,
and [QUALIFICATION.md](QUALIFICATION.md) for outstanding live requirements.
Do not present the synthetic 45-operation catalog or 14-step example as working
CAS/Live/Build gameplay.
