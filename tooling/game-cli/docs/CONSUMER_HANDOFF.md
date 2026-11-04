# Standalone Foundry handoff

Foundry must work independently of Apex so the same tool can test other mods.
Apex is a source reference and a possible consumer, not a required provider,
installed dependency, startup prerequisite or fallback. The implementation does
not import Apex, call its bridge or require its DLL. The canceled compatibility
adapter is not included. Existing Modernize methods guide the neutral port;
their historical success is not a claim that the independent port is complete.

## Use Foundry now

Install from the selected Sims4Foundry checkout with Python 3.11 or later:

```text
python -m pip install ./tooling/game-cli
foundry-game capabilities --source-details
```

An optional migration-research command, `apex-inventory --repository PATH`, parses
an explicitly selected historical source tree. It is not part of Foundry setup
or operation. No Apex checkout is needed for the commands below.

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

Independent process termination can now be observed without an Apex bridge:

```text
foundry-game observe-exit --identity "CURRENT_IDENTITY.json" --seconds 30 --output "NEW_EXIT_OBSERVATION.json"
```

This sends no quit or save request. It retains and verifies the exact process
lifetime; a missing/inaccessible PID is not success. Termination does not prove
normal exit, saving or persistence. See [PROCESS_EXIT.md](PROCESS_EXIT.md).

Build the neutral development archive without an Apex checkout or installation:

```text
foundry-game companion-build --source "tooling/game-cli/companion-src" --output "NEW_EXTERNAL/FoundryCompanion.ts4script" --python37 "TRUSTED_PYTHON37" --compiler-sha256 "EXPECTED_COMPILER_SHA256"
```

The archive and completion manifest contain deterministic source/bytecode hashes.
The build does not install, activate or qualify the companion. The explicit
owner-runtime integration is present; automatic startup, production admission,
key provisioning and real-game checks are still separate work. See
[COMPANION_BUILD.md](COMPANION_BUILD.md) and [OWNER_RUNTIME.md](OWNER_RUNTIME.md).

`startup-probe-build` separately packages a credential-free loader/thread
diagnostic for a later authorized disposable-profile run. It does not install or
run it, and does not turn on companion control. The next qualification step is
to establish Foundry's actual loader entry and startup/tick relationship without
Apex. See [STARTUP_PROBE_BUILD.md](STARTUP_PROBE_BUILD.md).

Host integration also has a bounded Windows connected-socket ownership observer.
It matches the server-side established loopback four-tuple to an independently
retained target process, instead of treating an HMAC key or port as PID proof.
This is a library primitive, not a new live-send CLI switch. See
[LOOPBACK_OWNER.md](LOOPBACK_OWNER.md).

## Historical source evidence for the standalone port

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

The reviewed historical CLI exposes the following reference commands. These are
source/evidence locators for porting the behavior, not Foundry commands or a
recommendation to install Apex to make Foundry work:

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

These historical templates must not be run against an unbound active session or
substituted for the independent implementation. `shutdown` is the save-and-exit
path. `game quit` merely requests the
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
host still needs its own installed companion, native readers and admitted owner runtime,
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
