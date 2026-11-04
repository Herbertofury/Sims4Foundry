# CAS and Build provider boundary

Read-only source inspection on 2026-11-04 established the following migration
constraints. This is not current-game qualification, an implementation of a
neutral UI provider, or permission to attach to an existing UI owner.

## What the existing CAS path actually does

Gameplay Python's Distributor client can disconnect in CAS while its Python-hosted
loopback listener remains. This does not prove the listener survives unloading
the Python interpreter itself. Distinguish unavailable game services, Distributor
disconnection and actual runtime/process loss; never treat them interchangeably.

The CAS movie timer performs UI/game-service work and later readback. Socket
callbacks collect bytes. Python and a native worker transport bounded copied data;
receipt validation returns to the canonical game-thread owner. The reviewed
ActionScript binds original stage/movie, selected Sim, native scope and UI-instance
serial, but source alone does not establish a separately measured OS thread fence.

The legacy channel uses eight ASCII decimal length bytes, a 131072-byte UTF-8
frame cap and one absolute header/body deadline. Its semantic command is narrower:
seven fields, one request UUID, exact positive uint64 Sim ID and at most 512
printable ASCII characters. The new Foundry client framing is a separate contract;
there is no implicit compatibility or installed translator between them.

The newer receipt path claims before the first native call. It binds nonce,
request ID, exact wire hash, sequence and original UI lifetime; receipt-only resume
cannot fetch new actions. Unknown or unconfirmed claims remain blocking. A cache
or socket reset must not convert them into fresh mutations.

## Native dependency and resource ownership

The inspected current exchange reaches an Apex-specific native service hook,
`GetSimOccult` with its private protocol prefix. It explicitly rejects absence of
the matching native pipe. These are not established vanilla game capabilities.
The new Foundry companion does not use or pretend that hook exists.

The historical builder owns CASCustomizerMain UI resource
`62ECC59A:00000000:DF09D526F4C77B95` and selector resource
`62ECC59A:00000000:26DF5B0A7B97963E`, among other related resources. Two packages
independently replacing the same resources compete; a separate endpoint alone
does not solve this. Qualify one explicitly merged/version-pinned UI owner, or a
neutral interface integrated into the active owner's own guarded dispatch path.
Do not install a second override alongside Apex as an automation shortcut.

The existing builder pins historical 1.128.90 resources; later research references
1.129. Neither establishes the user's current installation, donor or DLL build.
No game resource bytes or private helper modules are included in this repository.

## Observation is not automatically side-effect-free

Existing source warns that `CASGetSimInfo` can synchronize or replace appearance
cache records, and querying hidden outfit categories can lazily create records.
A provider must independently qualify each getter's effects. Current-panel-only,
bounded observations are necessary safeguards, not proof of harmlessness.
A caller marking an operation "read-only" cannot authorize those effects.

A native outfit selection branch checks exact current member, category metadata
and existing index before `CasSelectPlannedOutfit`, then schedules later readback.
Adding an outfit additionally checks native capacity and before/after counts.
Returning from the service call or receiving queue bytes is not visible success
or saved persistence. The standalone provider has not implemented these branches.

Acceptance depends on the original nonce, member, household, editor/owner
lifetime, readiness and one commit attempt. User interference cancels pending
automation while native user input continues. Extra arguments in the existing
SaveAndExitCAS call belong to that custom adapter, not a verified vanilla API.
Cancelling an acceptance continuation is not the same operation as cancelling CAS.
No neutral CAS cancel/accept path is yet qualified.

## Missing Build and menu semantics

Within the inspected CAS registry, native handler and lifecycle scope, the only
Build/Buy semantic observation found was `zone.is_in_build_buy`. No qualified
Build placement, editing or undo path was identified. Absence in that bounded
inspection is not proof that no other repository code exists.

Normal quit requests a menu. Existing lifecycle code recognizes complete English
menu/confirmation labels from captured pixels/OCR and refuses conflicting peers
or unresolved requests. It is not a general native menu visibility getter. A
neutral lifecycle provider must qualify labels, geometry, choice semantics and
exact process closure without assuming this historical orchestration works.

## Source review locations

- `Source/apex_core/cas_socket.py` and `cas_ui.py`: framing, claim and receipt guards.
- `Source/CASUi/semantic_methods.as` and `catalog_receipt_methods.as`: owned UI timer,
  selected member/native scope, operation call sites and later readback.
- `NativeOverlay/Source/ApexNativeCasEngineWin.h` and `ApexCasNativePipeWin.h`:
  native capability, UI-instance binding and copied-byte transport.
- `tools/cas_ui_build.py`: single-resource owner, injection anchor and donor pins.
- `tools/game_lifecycle.py`: guarded English OCR exit orchestration.

These are lineage references for the supplied source review, not dependencies
shipped by Foundry. Do not import, strip or bypass their preservation/admission
checks when building a neutral provider.
