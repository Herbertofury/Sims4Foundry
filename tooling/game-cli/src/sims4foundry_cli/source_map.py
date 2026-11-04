"""Source-review lineage for desired operations, never a dispatch registry.

Names and ranges refer to the reviewed source repository, unless marked as
Foundry documentation. They are facts extracted from a bounded source review,
not redistributed source, installed dependencies, or evidence of live support.
"""

from copy import deepcopy

from .errors import FoundryError


_REVIEW = {
    "kind": "source_only",
    "reviewed_on": "2026-11-04",
    "scope": "Selected registries and call sites were inspected successfully. "
             "A recursive enumeration encountered an access denial and was stopped; "
             "missing coverage means not identified in this bounded review, not absent "
             "from the entire repository.",
    "current_game_compatibility_verified": False,
    "runtime_tested": False,
}

_CAS_DEPENDENCIES = (
    "The inspected CAS path belongs to the Apex UI owner, native hook and "
    "preservation/admission system; it is not a neutral vanilla provider.",
    "The custom GetSimOccult transport requires its matching native service hook. "
    "Socket callbacks transport bytes; owned UI work and owner-thread receipt "
    "validation are separate. The native UI thread contract is not fully proven.",
    "Exact selected member, household/session, UI lifetime and later receipt must "
    "remain bound. Preserve existing ownership; do not install a competing UI override.",
)

_CAS_READBACK_LIMITS = (
    "A returned service call or receipt is not proof of visible success or saved persistence.",
    "Status/getter intent is not proof of side-effect freedom: CASGetSimInfo may "
    "synchronize or replace appearance caches, and hidden-outfit queries may create "
    "outfits lazily. Independently qualify bounded current-panel observations.",
)


def _ref(path, lines=None, repository="reviewed_apex_repository"):
    return {"repository": repository, "path": path, "lines": list(lines or ())}


def _entry(name, status, *, legacy=(), native=(), custom=(), refs=(),
           dependencies=(), scope=(), readback=(), quarantined=(), observations=()):
    is_cas = name.startswith("cas.")
    return {
        "operation": name,
        "source_status": status,
        "contract_coverage": "not_established" if status == "not_identified_in_bounded_review" else "partial",
        "live_qualified": False,
        "dispatch_available": False,
        "fallback_available": False,
        "legacy_operations": list(legacy),
        "native_calls": list(native),
        "custom_adapter_calls": list(custom),
        "observed_state_reads": list(observations),
        "source_references": list(refs),
        "dependencies": list(_CAS_DEPENDENCIES if is_cas else ()) + list(dependencies),
        "scope_limits": list(scope),
        "readback_limits": list(_CAS_READBACK_LIMITS if is_cas else ()) + list(readback),
        "quarantined_paths": list(quarantined),
        "review": _REVIEW,
    }


_REGISTRY_REFS = (
    _ref("Source/apex_core/cas_ui.py", ("1436-1506",)),
    _ref("Source/apex_core/cas_controls.py", ("31-113",)),
)
_BUILD_REFS = (
    _ref("Source/apex_core/test_driver.py", ("181-193",)),
    _ref("tooling/game-cli/docs/UI_ADAPTER.md", repository="foundry_repository"),
)


def _build_gap(name, missing):
    return _entry(
        name, "not_identified_in_bounded_review", refs=_BUILD_REFS,
        observations=("zone.is_in_build_buy",),
        scope=(missing, "Only the Build/Buy state observation was identified; "
               "it establishes no Build action or native editing contract."),
        readback=("The mode boolean cannot verify placement, edits, history targets "
                  "or persisted build state.",),
    )


# Every desired catalog key is explicit. Helpers share evidence boundaries only;
# they do not infer support, resolve game identifiers, import legacy code or call APIs.
_SOURCES = {
    "game.launch": _entry(
        "game.launch", "not_identified_in_bounded_review",
        refs=(_ref("tools/game_lifecycle.py"),),
        scope=("Lifecycle lineage is known, but this bounded review does not establish "
               "a neutral launch implementation or its exact installation/account binding.",),
        readback=("A launch request is not proof of the intended process or main menu. "
                  "Launch may activate a game window.",),
    ),
    "game.new": _entry(
        "game.new", "not_identified_in_bounded_review",
        scope=("No new-game/menu-to-CAS native semantic path was identified in the "
               "inspected registries and companion excerpts.",),
        readback=("CAS appearance alone would not identify a newly created game or household.",),
    ),
    "game.load": _entry(
        "game.load", "not_identified_in_bounded_review",
        refs=(_ref("Source/apex_core/test_driver.py", ("181-193",)),),
        observations=("services.get_persistence_service().get_save_slot_proto_buff",),
        scope=("Save-slot status is observed; no slot-targeted load action was identified.",),
        readback=("Reading a current slot does not prove a requested load completed.",),
    ),
    "game.save": _entry(
        "game.save", "not_identified_in_bounded_review",
        refs=(_ref("Source/apex_core/test_driver.py", ("181-193",)),),
        observations=("services.get_persistence_service().get_save_slot_proto_buff",),
        scope=("No save operation accepting the desired slot ID and slot name was "
               "identified. CAS SaveAndExitCAS is not a generic save-slot API.",),
        readback=("Current-slot status or CAS transition does not verify disk persistence "
                  "or a successful subsequent load.",),
    ),
    "game.main_menu": _entry(
        "game.main_menu", "not_identified_in_bounded_review",
        refs=(_ref("tools/game_lifecycle.py"),),
        scope=("No neutral return-to-main-menu path implementing save/discard was "
               "identified. A quit menu is not a main-menu transition.",),
        readback=("Historical complete English OCR labels are not a native menu getter "
                  "or verification of the requested save policy.",),
    ),
    "game.exit": _entry(
        "game.exit", "inspected_call_sites", legacy=("test_quit",),
        native=("sims4.commands.client_cheat('quit', connection)",),
        refs=(_ref("Source/apex_core/test_driver.py", ("739-751",)),
              _ref("tools/game_lifecycle.py")),
        dependencies=("Requires an observed native client connection; the legacy path "
                      "can use its previously observed connection when CAS unloads gameplay services.",
                      "Legacy dispatch is admission-guarded and owned by the game thread."),
        scope=("The call requests the normal quit menu only. It does not choose save/discard "
               "or exit the process. No forced termination fallback is established.",),
        readback=("Source explicitly reports game_exit_verified=False. A neutral provider "
                  "still needs qualified menu choices, conflict handling and exact process closure.",),
    ),
    "cas.enter": _entry(
        "cas.enter", "apex_only_path", legacy=("test_cas",),
        native=("server_commands.cas_commands.modify_in_cas", "server_commands.cas_commands.modify_in_cas_with_household_id",
                "server_commands.argument_helpers.OptionalTargetParam"),
        refs=(_ref("Source/apex_core/test_driver.py", ("871-873", "889-890", "938-940", "948-951")),),
        dependencies=("Requires an explicit instanced Sim and active game client, save-boundary "
                      "installation, member checkpoints, bank transaction, original-owner capture "
                      "and the prepared frontdoor context. These preservation gates cannot be omitted.",),
        scope=("The full-edit variant additionally enables cas.fulleditmode. Protected "
               "native-full and mod-specific entry paths are not neutral shortcuts.",
               "Explicit Sim lookup does not authorize the legacy blank-ID active-Sim fallback."),
        readback=("Source reports cas_visible_verified=False; submission does not prove CAS entry.",),
    ),
    "cas.accept": _entry(
        "cas.accept", "apex_only_path", legacy=("accept",),
        native=("CallGameService('SaveAndExitCAS')", "SendUIMessage('ShowTransitionScreen')"),
        refs=(_ref("Source/apex_core/cas_ui.py", ("1491-1495", "1908-1931")),
              _ref("Source/CASUi/semantic_methods.as", ("2288-2297", "2871-2961"))),
        dependencies=("Existing-household acceptance requires a prepared and ACK-armed two-phase "
                      "intent, original household/member, nonce, owner lifetime and one commit attempt.",),
        scope=("The accept ID/nonce and validation arguments belong to the custom Apex adapter; "
               "this is not a verified vanilla SaveAndExitCAS signature or new-household flow.",),
        readback=("Submitted transition and an acceptance receipt do not establish persistence.",),
    ),
    "cas.cancel": _entry(
        "cas.cancel", "not_identified_in_bounded_review",
        refs=_REGISTRY_REFS + (_ref("Source/CASUi/semantic_methods.as", ("2708", "2975")),),
        scope=("No ordinary CAS-session cancel operation appears in the inspected registries. "
               "Cancelling a pending accept intent is not cancelling CAS. An earlier native "
               "cancel wrapper reference does not establish a neutral exposed command.",),
        readback=("No rollback-to-entry appearance or return-to-Live outcome was established.",),
    ),
    "cas.select_sim": _entry(
        "cas.select_sim", "inspected_call_sites", legacy=("member-select",),
        native=("SendMessage('CASSelectSim', memberIndex)",),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("2106-2122",)),
              _ref("Source/apex_core/cas_ui.py", ("1467-1474", "1986-1999"))),
        scope=("Requires captured roster, original member, exact household/session/layer "
               "and resolution of an explicit target Sim to that roster index.",),
        readback=("Checks destination, original captures and unchanged session/roster; "
                  "the receipt does not claim appearance persistence.",),
    ),
    "cas.select_form": _entry(
        "cas.select_form", "apex_only_path",
        legacy=("occult-view", "form-select", "layer-select", "form-owner-select"),
        native=("SetSimOccult",),
        custom=("CallUIService('ApexNavigateOwnerLayer')", "reserved owner-selection token intercepted by native hook"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("1541-1555", "1599-1627", "2102-2103", "2123-2142")),
              _ref("Source/apex_core/cas_ui.py", ("1475-1486", "1516-1523")),
              _ref("Source/apex_core/cas_controls.py", ("209-253", "590-643"))),
        scope=("Existing single-bit occult membership, captured owner/layer and reserved "
               "one-shot owner tokens are distinct paths, not a generic form_id conversion.",
               "Owner selection is preservation-owned. SetSimOccult with a reserved hook "
               "token is not a generic native converter."),
        readback=("Requires unchanged full membership, original capture and resulting "
                  "selection; arbitrary form transitions remain unestablished.",),
        quarantined=({"legacy_operation": "occult-cache-view", "usable": False,
                      "reason": "Cache conversion quarantined after a crash; excluded from migration dispatch."},
                     {"legacy_operation": "occult-owner-view", "usable": False,
                      "reason": "Owner conversion quarantined upstream and rejected by the UI branch."}),
    ),
    "cas.select_outfit": _entry(
        "cas.select_outfit", "inspected_call_sites", legacy=("outfit",),
        native=("CasGetPlannedOutfits", "CasSelectPlannedOutfit", "SendUIMessage('CASRefreshActiveCatalog')"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("2150-2173",)),
              _ref("Source/apex_core/cas_ui.py", ("1303-1313", "1487-1497"))),
        scope=("Requires exact current member, available native category metadata and an "
               "existing index. Wire category 0..255 and index 0..4 are not unconditional "
               "eligibility; desired category labels require explicit native mapping.",),
        readback=("Later readback checks exact selected category/index, not persisted outfits.",),
    ),
    "cas.add_outfit": _entry(
        "cas.add_outfit", "inspected_call_sites", legacy=("outfit-add",),
        native=("CasGetPlannedOutfits", "CasAddPlannedOutfit"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("2150-2173",)),
              _ref("Source/apex_core/cas_ui.py", ("1303-1313", "1487-1497"))),
        scope=("Append additionally restricts native category to 0..13, requires available "
               "capacity and fewer than five slots; symbolic category mapping remains necessary.",),
        readback=("Checks count increased by one and the newly selected slot; hidden-category "
                  "queries must not be presumed harmless.",),
    ),
    "cas.remove_outfit": _entry(
        "cas.remove_outfit", "not_identified_in_bounded_review", refs=_REGISTRY_REFS,
        scope=("No outfit-removal operation was identified in the inspected registries. "
               "Part/layer removal and outfit append do not implement outfit deletion.",),
        readback=("No deletion count, retained-outfit identity or rollback contract was established.",),
    ),
    "cas.open_panel": _entry(
        "cas.open_panel", "inspected_call_sites", legacy=("panel", "panel-navigation"),
        native=("HandleContextMenuSetMenuState", "SendUIMessage('CASChangeMenuState')"),
        refs=(_ref("Source/apex_core/cas_ui.py", ("1202-1255", "1300-1302", "1450-1451", "1527-1538")),
              _ref("Source/CASUi/semantic_methods.as", ("2144-2149",))),
        scope=("Panel must be an installed eligible named panel mapped to an exact native "
               "menu state; panel-navigation also requires a test guard and fresh peer.",),
        readback=("Intended readback checks exact menu state and visible panel using an "
                  "owner-bound receipt, not arbitrary UI focus.",),
    ),
    "cas.search": _entry(
        "cas.search", "apex_only_path", legacy=("catalog-search",),
        custom=("ApexSearchText",),
        refs=(_ref("Source/apex_core/cas_controls.py", ("137-144", "450-456")),
              _ref("Source/CASUi/semantic_methods.as", ("1518",))),
        scope=("Search is the Apex catalog workspace, not a general native game search API. "
               "Its text bound is 160 printable characters and 350 encoded characters, "
               "not the desired catalog label's full range.",),
        readback=("Readback verifies workspace search state, not general native search completeness.",),
    ),
    "cas.apply_part": _entry(
        "cas.apply_part", "inspected_call_sites", legacy=("select", "select-layer", "preset"),
        native=("GetCatalogItem", "GetCatalogVariantItems", "GetCatalogColorFamilies", "SetCatalogSlotSelected",
                "CASAPI.GetBodyTypeLayers", "GetSelectedPresetFromMenuState"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("1805-1816", "1818-1898", "1979-1988", "2175-2264")),
              _ref("Source/apex_core/cas_ui.py", ("1316-1338", "1463-1466", "1498-1502", "1524-1525")),
              _ref("Source/apex_core/cas_controls.py", ("722-758",))),
        scope=("Requires one exact eligible filtered panel product and family swatch; "
               "layered parts require an existing exact layer and separate identity guards. "
               "Part/body_type arguments alone do not supply those bindings.",
               "Preset routes have panel exclusions; ordinary layer count is at most five "
               "and medical layer 127 is protected."),
        readback=("Checks equipped identity and optional base/variant binding or preset index; "
                  "native validation failures cannot be treated as success.",),
        quarantined=({"legacy_operation": "select (wings)", "usable": False,
                      "reason": "Wing selection quarantined after failed exact color readback and a crash trial."},
                     {"legacy_operation": "first-tattoo insertion", "usable": False,
                      "reason": "First-tattoo insertion explicitly disabled after failed native validation."}),
    ),
    "cas.remove_part": _entry(
        "cas.remove_part", "inspected_call_sites", legacy=("remove", "layer-remove"),
        native=("ClearSelectedBodyType", "RemoveBodyTypeLayer"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("1818-1898", "2005-2017")),
              _ref("Source/apex_core/cas_controls.py", ("727-762",))),
        scope=("Ordinary removal requires one exactly equipped non-layered part. Layer removal "
               "requires exact body type, layer index and layer ID, protecting medical layer 127. "
               "A body_type alone is not the complete source contract.",),
        readback=("Checks selected ID absence or the entire layer array and expected change.",),
    ),
    "cas.select_swatch": _entry(
        "cas.select_swatch", "inspected_call_sites", legacy=("select", "swatch", "hair-swatch"),
        native=("GetCatalogColorFamilies", "SetCatalogSlotSelected", "CasGetSwatchColorList",
                "CasGetSwatchColorSelected", "CasSetSwatchColorSelected", "CasGetSwatchColorSelectedWithModifier"),
        custom=("ApexSelectEyebrowSwatch",),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("1732-1765", "1990-1991", "2175-2264", "2272-2286")),
              _ref("Source/apex_core/cas_controls.py", ("294-301", "763-777"))),
        scope=("Part-family selection, general palettes and the hair palette are distinct "
               "paths. There is no universal part_id/swatch_id mapping established here.",
               "Palette types are 0..16 except 5; color index is zero except an existing "
               "fur slot for type 6. Hair uses type 2; eyebrows use an owned widget helper."),
        readback=("Requires exact returned swatch and selected ID/slot; skin uses modifier-aware "
                  "readback. These checks do not establish arbitrary palette compatibility.",),
        quarantined=({"legacy_operation": "select (wings)", "usable": False,
                      "reason": "Wing color selection remains quarantined; other palette paths do not lift that restriction."},),
    ),
    "cas.set_modifier": _entry(
        "cas.set_modifier", "not_identified_in_bounded_review", legacy=("modifiers", "color-sliders"),
        native=("GetCatalogItemsSelectedWithModifiers", "SetCatalogSlotSelected"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("1698-1730", "1970-1977")),
              _ref("Source/apex_core/cas_controls.py", ("266-278", "687-701"))),
        scope=("Only equipped-part hue, opacity, saturation and brightness/value controls "
               "were identified. These are not generic facial/body morph modifier_id support.",
               "Color values require the exact part's returned ranges and a bound ordinary "
               "slot or existing nonmedical layer; signed_unit is not that contract."),
        readback=("Color readback checks all four values and unchanged unrelated modifier "
                  "fields; it does not verify a generic morph change.",),
    ),
    "cas.set_physique": _entry(
        "cas.set_physique", "inspected_call_sites", legacy=("physique",),
        native=("CASGetSimInfo", "GetSimOccult", "SetSimPhysiqueValue"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("1767-1786", "1992-1996", "2341-2359")),
              _ref("Source/apex_core/cas_controls.py", ("302-307", "778-783"))),
        scope=("The source writes one physique_type (0 or 1) and finite value 0..1 at a time, "
               "with separate start/commit slider phases. It does not establish an atomic "
               "combined fitness-and-weight operation or a failure policy between two writes.",),
        readback=("Reads the current Sim parameter within 1e-6 and binds its starting value/owner; "
                  "both requested fields would require independent evidence.",),
    ),
    "cas.set_voice": _entry(
        "cas.set_voice", "inspected_call_sites", legacy=("voices", "voice-actor", "voice-pitch"),
        native=("CASGetSimInfo", "GetVoicesDataForAgeSpecies", "SetSimVoiceActor", "SetSimVoicePitch"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("1629-1653",)),
              _ref("Source/apex_core/cas_controls.py", ("255-261", "644-659"))),
        scope=("Writes require primary layer and forced full editing. voice_id must be resolved "
               "to a zero-based actor index in the eligible age/species rows; arbitrary IDs "
               "are not native actor indexes.",
               "Pitch must be finite within the selected row's minPitch..maxPitch, not "
               "assumed to share the desired 0..1 range. Actor and pitch are separate writes."),
        readback=("Snapshot checks selected actor, pitch and eligible bounds; combined atomic "
                  "voice-and-pitch success is not established.",),
    ),
    "cas.set_walkstyle": _entry(
        "cas.set_walkstyle", "inspected_call_sites", legacy=("walkstyles", "walkstyle"),
        native=("CasGetWalkCycleTraits", "CasSetWalkCycleTrait"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("1654-1669", "1942-1946")),
              _ref("Source/apex_core/cas_controls.py", ("660-669",))),
        scope=("Requires one exact eligible walk-cycle trait, primary human non-ghost "
               "non-robot and full editing. This is not general trait editing.",),
        readback=("Re-query requires exactly the requested equipped walk-cycle trait.",),
    ),
    "cas.set_name": _entry(
        "cas.set_name", "not_identified_in_bounded_review",
        refs=_REGISTRY_REFS + (_ref("Source/CASUi/semantic_methods.as", ("2904",)),),
        scope=("No general Sim name setter was identified in the inspected registries and "
               "service calls. CASCheckProfanityOnSimNames during accept is validation, "
               "not a name setter.",),
        readback=("No exact first-name/last-name setter or readback contract was established.",),
    ),
    "cas.set_trait": _entry(
        "cas.set_trait", "not_identified_in_bounded_review", refs=_REGISTRY_REFS,
        scope=("No ordinary trait-slot or aspiration setter was identified in the inspected "
               "registries and service calls. Walk-cycle trait selection is not general "
               "trait or aspiration editing.",),
        readback=("No slot-targeted trait mutation or retained-other-traits contract was established.",),
    ),
    "cas.undo": _entry(
        "cas.undo", "inspected_call_sites", legacy=("undo",),
        native=("CASPrePerformUndo", "CASPostPerformUndo", "CASPreventToggleReset", "CasNavigationUndo"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("2298-2308",)),
              _ref("Source/apex_core/cas_ui.py", ("1975-1985", "2093-2095"))),
        scope=("Native navigation undo is surrounded by controller messages; it is not "
               "an independently qualified semantic history stack.",),
        readback=("Source reports history_expected_target_verified=False. A changed Sim, "
                  "equipped part or palette is not proof of the intended history target.",),
    ),
    "cas.redo": _entry(
        "cas.redo", "inspected_call_sites", legacy=("redo",),
        native=("CASPerformRedo", "CASPostPerformRedo", "CASPreventToggleReset", "CasNavigationRedo"),
        refs=(_ref("Source/CASUi/semantic_methods.as", ("2298-2308",)),
              _ref("Source/apex_core/cas_ui.py", ("1975-1985", "2093-2095"))),
        scope=("Native navigation redo is surrounded by controller messages; it is not "
               "an independently qualified semantic history stack.",),
        readback=("Source reports history_expected_target_verified=False. A changed Sim, "
                  "equipped part or palette is not proof of the intended history target.",),
    ),
    "live.select_sim": _entry(
        "live.select_sim", "not_identified_in_bounded_review",
        refs=(_ref("Source/apex_core/test_driver.py"),),
        observations=("services.sim_info_manager().get(sid)",),
        scope=("An explicit Sim lookup is known, but lookup is not active-Sim selection. "
               "No neutral Live selection setter was identified; the blank-ID active-Sim "
               "fallback is not imported into this explicit-target contract.",),
        readback=("Resolving a SimInfo object cannot verify UI or active actor selection.",),
    ),
    "live.set_speed": _entry(
        "live.set_speed", "inspected_call_sites",
        legacy=("test_pause", "test_play", "test_speed2", "test_speed3"),
        native=("server_commands.clock_commands.set_speed(speed, source, _connection=client.id)",),
        refs=(_ref("Source/apex_core/test_driver.py", ("181-193", "206-248", "823-834")),
              _ref("Source/td1_occult_hybrid_apex.py", ("8475-8478", "8660-8691")),
              _ref("Source/apex_core/command_queue.py", ("119-121", "146-165"))),
        dependencies=("Requires the current observed client ID, known game-thread owner, "
                      "live timeline, exact identities and admission. Newly written neutral "
                      "bindings default to denied admission; source inspection does not enable them.",),
        scope=("Observed labels paused/one/two/three map to speeds 0/1/2/3. The call "
               "signature is source-confirmed; current-patch compatibility is not.",),
        readback=("Source compares game_clock_service().clock_speed. Game-clock advance "
                  "does not establish simulation advance; read the distinct clocks if required.",),
    ),
    "live.interact": _entry(
        "live.interact", "not_identified_in_bounded_review",
        scope=("No explicit actor/target/interaction dispatch contract was identified in "
               "the inspected companion excerpts. No click or console fallback is supplied.",),
        readback=("No queue admission, interaction execution or completion readback was established.",),
    ),
    "live.cancel_interaction": _entry(
        "live.cancel_interaction", "not_identified_in_bounded_review",
        scope=("No exact interaction-ID cancellation action was identified in the inspected "
               "companion excerpts; owner command cancellation is not gameplay cancellation.",),
        readback=("No exact gameplay queue removal or retained-interaction contract was established.",),
    ),
    "live.travel": _entry(
        "live.travel", "not_identified_in_bounded_review",
        scope=("No lot-targeted travel call was identified in the inspected companion excerpts. "
               "Zone lifecycle hooks do not establish a travel action.",),
        readback=("A zone change alone does not prove the intended lot or household arrived.",),
    ),
    "live.select_household": _entry(
        "live.select_household", "not_identified_in_bounded_review",
        refs=(_ref("Source/apex_core/test_driver.py", ("181-193",)),),
        observations=("services.active_household",),
        scope=("Active-household status is observed, but no household selection action was identified.",),
        readback=("A household getter does not establish household switching or its persistence.",),
    ),
    "build.enter": _build_gap("build.enter", "No enter-Build action was identified."),
    "build.exit": _build_gap("build.exit", "No exit-Build action was identified."),
    "build.search": _build_gap("build.search", "No native Build catalog search was identified."),
    "build.place": _build_gap("build.place", "No catalog-object placement or coordinate contract was identified."),
    "build.move": _build_gap("build.move", "No exact-object move/rotation/floor action was identified."),
    "build.delete": _build_gap("build.delete", "No exact-object deletion action was identified."),
    "build.set_swatch": _build_gap("build.set_swatch", "No Build object swatch selection was identified."),
    "build.draw_room": _build_gap("build.draw_room", "No room geometry creation action was identified."),
    "build.paint": _build_gap("build.paint", "No surface/material painting action was identified."),
    "build.set_floor": _build_gap("build.set_floor", "No active-floor selection action was identified."),
    "build.undo": _build_gap("build.undo", "No native Build undo action or history target was identified."),
    "build.redo": _build_gap("build.redo", "No native Build redo action or history target was identified."),
}


def operation_source(name):
    """Return detached JSON-serializable evidence, refusing unknown operations.

    The function performs no discovery or dispatch. Changing its returned data
    cannot grant qualifications, alter the map, or affect a later caller.
    """
    try:
        return deepcopy(_SOURCES[name])
    except (KeyError, TypeError) as exc:
        raise FoundryError(
            "unsupported_operation", "No source mapping or raw-input fallback exists for this operation.",
            operation=name,
        ) from exc
