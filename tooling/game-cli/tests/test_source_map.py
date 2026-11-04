import ast
from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import unittest

from sims4foundry_cli.catalog import CATALOG
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli import source_map
from sims4foundry_cli.source_map import operation_source


class SourceMapTests(unittest.TestCase):
    def text(self, name, field):
        return " ".join(operation_source(name)[field])

    def test_every_catalog_operation_is_explicitly_mapped(self):
        self.assertEqual(45, len(CATALOG))
        self.assertEqual(set(CATALOG), set(source_map._SOURCES))
        tree = ast.parse(Path(source_map.__file__).read_text(encoding="utf-8"))
        literal = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "_SOURCES"
                               for target in node.targets))
        self.assertIsInstance(literal, ast.Dict)
        self.assertEqual(set(CATALOG), {node.value for node in literal.keys})

    def test_metadata_is_json_serializable_and_always_unqualified(self):
        statuses = {"inspected_call_sites", "apex_only_path", "not_identified_in_bounded_review"}
        for name in CATALOG:
            with self.subTest(name=name):
                row = operation_source(name)
                self.assertEqual(name, row["operation"])
                self.assertEqual(row, json.loads(json.dumps(row, allow_nan=False)))
                self.assertIn(row["source_status"], statuses)
                self.assertIs(row["live_qualified"], False)
                self.assertIs(row["dispatch_available"], False)
                self.assertIs(row["fallback_available"], False)
                self.assertIn(row["contract_coverage"], {"partial", "not_established"})
                self.assertIs(row["review"]["runtime_tested"], False)
                self.assertIs(row["review"]["current_game_compatibility_verified"], False)
                self.assertTrue(row["scope_limits"])
                self.assertTrue(row["readback_limits"])

    def test_each_result_is_recursively_detached(self):
        for name in CATALOG:
            with self.subTest(name=name):
                baseline = operation_source(name)
                result = operation_source(name)
                result["live_qualified"] = True
                result["dispatch_available"] = True
                result["legacy_operations"].append("invented_dispatch")
                result["native_calls"].append("invented_native_call")
                result["review"]["runtime_tested"] = True
                result["scope_limits"].clear()
                result["readback_limits"].append("invented_verified_persistence")
                if result["source_references"]:
                    result["source_references"][0]["lines"].append("invented")
                if result["quarantined_paths"]:
                    result["quarantined_paths"][0]["usable"] = True
                self.assertEqual(baseline, operation_source(name))

    def test_shared_review_is_not_mutated_across_operations(self):
        baseline = deepcopy(source_map._SOURCES)
        changed = operation_source("cas.select_sim")
        changed["review"]["scope"] = "all files inspected"
        changed["dependencies"].clear()
        self.assertEqual(baseline, source_map._SOURCES)

    def test_unknown_and_malformed_names_have_no_fallback(self):
        for name in ("game.raw_click", "cas.occult-cache-view", "cas.occult-owner-view",
                     "cas.set_aspiration", "build.raw_drag", "", None, [], {}, 1):
            with self.subTest(name=name):
                with self.assertRaises(FoundryError) as caught:
                    operation_source(name)
                self.assertEqual("unsupported_operation", caught.exception.code)
                self.assertIn("fallback", str(caught.exception))

    def test_no_repository_wide_absence_claim(self):
        for name in CATALOG:
            with self.subTest(name=name):
                scope = operation_source(name)["review"]["scope"]
                self.assertIn("bounded review", scope)
                self.assertIn("access denial", scope)
                self.assertIn("not absent", scope)

    def test_lineage_paths_are_relative_and_contain_no_private_handoffs(self):
        for name in CATALOG:
            row = operation_source(name)
            with self.subTest(name=name):
                text = json.dumps(row)
                for forbidden in ("/workspace/", "C:\\Users", "foundry-source-research",
                                  "companion-and-requirements-handoff", "cas-operation-inventory",
                                  "thread_id", "task_id", ".work/"):
                    self.assertNotIn(forbidden, text)
                for ref in row["source_references"]:
                    self.assertFalse(PurePosixPath(ref["path"]).is_absolute())
                    self.assertNotIn("..", PurePosixPath(ref["path"]).parts)
                    self.assertNotIn("\\", ref["path"])
                    self.assertIn(ref["repository"], {"reviewed_apex_repository", "foundry_repository"})

    def test_metadata_has_no_runtime_or_legacy_imports(self):
        tree = ast.parse(Path(source_map.__file__).read_text(encoding="utf-8"))
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertEqual([(1, "errors"), (0, "copy")],
                         sorted([(node.level, node.module) for node in imports
                                 if isinstance(node, ast.ImportFrom)], reverse=True))
        self.assertTrue(all(isinstance(node, ast.ImportFrom) for node in imports))

    def test_generic_modifier_is_not_qualified_by_color_sliders(self):
        row = operation_source("cas.set_modifier")
        self.assertEqual("not_identified_in_bounded_review", row["source_status"])
        self.assertEqual("not_established", row["contract_coverage"])
        self.assertIn("color-sliders", row["legacy_operations"])
        self.assertIn("not generic facial/body morph", self.text("cas.set_modifier", "scope_limits"))
        self.assertIn("returned ranges", self.text("cas.set_modifier", "scope_limits"))

    def test_missing_name_trait_outfit_remove_and_cancel_are_explicit(self):
        for name in ("cas.set_name", "cas.set_trait", "cas.remove_outfit", "cas.cancel"):
            with self.subTest(name=name):
                row = operation_source(name)
                self.assertEqual("not_identified_in_bounded_review", row["source_status"])
                self.assertEqual([], row["native_calls"])
        self.assertIn("aspiration", self.text("cas.set_trait", "scope_limits"))
        self.assertIn("not a name setter", self.text("cas.set_name", "scope_limits"))
        self.assertIn("not cancelling CAS", self.text("cas.cancel", "scope_limits"))

    def test_all_build_contracts_are_unconfirmed_observation_only(self):
        names = [name for name in CATALOG if name.startswith("build.")]
        self.assertEqual(12, len(names))
        for name in names:
            with self.subTest(name=name):
                row = operation_source(name)
                self.assertEqual("not_identified_in_bounded_review", row["source_status"])
                self.assertEqual([], row["legacy_operations"])
                self.assertEqual([], row["native_calls"])
                self.assertEqual([], row["custom_adapter_calls"])
                self.assertEqual(["zone.is_in_build_buy"], row["observed_state_reads"])

    def test_physique_is_one_parameter_and_staged_not_atomic_pair(self):
        scope = self.text("cas.set_physique", "scope_limits")
        self.assertIn("one physique_type", scope)
        self.assertIn("separate start/commit", scope)
        self.assertIn("does not establish an atomic", scope)
        self.assertIn("1e-6", self.text("cas.set_physique", "readback_limits"))

    def test_voice_requires_actor_resolution_native_ranges_and_eligibility(self):
        scope = self.text("cas.set_voice", "scope_limits")
        for expected in ("zero-based actor index", "eligible age/species", "minPitch..maxPitch",
                         "primary layer", "forced full editing", "separate writes"):
            self.assertIn(expected, scope)

    def test_swatches_are_distinct_native_paths_not_universal_mapping(self):
        row = operation_source("cas.select_swatch")
        self.assertEqual(["select", "swatch", "hair-swatch"], row["legacy_operations"])
        self.assertIn("no universal part_id/swatch_id", self.text("cas.select_swatch", "scope_limits"))
        self.assertIn("CasGetSwatchColorSelectedWithModifier", row["native_calls"])
        self.assertIn("ApexSelectEyebrowSwatch", row["custom_adapter_calls"])

    def test_history_change_is_not_expected_target_evidence(self):
        for name in ("cas.undo", "cas.redo"):
            with self.subTest(name=name):
                readback = self.text(name, "readback_limits")
                self.assertIn("history_expected_target_verified=False", readback)
                self.assertIn("not proof of the intended history target", readback)

    def test_accept_is_existing_household_custom_two_phase_not_persistence(self):
        row = operation_source("cas.accept")
        self.assertEqual("apex_only_path", row["source_status"])
        self.assertIn("two-phase", self.text("cas.accept", "dependencies"))
        self.assertIn("Existing-household", self.text("cas.accept", "dependencies"))
        self.assertIn("not a verified vanilla", self.text("cas.accept", "scope_limits"))
        self.assertIn("do not establish persistence", self.text("cas.accept", "readback_limits"))

    def test_quarantined_paths_never_become_usable_operations(self):
        excluded = {}
        for name in CATALOG:
            row = operation_source(name)
            for path in row["quarantined_paths"]:
                self.assertIs(path["usable"], False)
                self.assertNotIn(path["legacy_operation"], row["legacy_operations"])
                excluded[path["legacy_operation"]] = path["reason"]
        self.assertEqual({"occult-cache-view", "occult-owner-view", "select (wings)",
                          "first-tattoo insertion"}, set(excluded))
        self.assertIn("crash", excluded["occult-cache-view"])
        self.assertIn("rejected", excluded["occult-owner-view"])

    def test_cas_getter_hazards_ownership_and_thread_limits_are_carried(self):
        for name in CATALOG:
            if name.startswith("cas."):
                with self.subTest(name=name):
                    deps = self.text(name, "dependencies")
                    readback = self.text(name, "readback_limits")
                    self.assertIn("Apex UI owner", deps)
                    self.assertIn("native UI thread contract is not fully proven", deps)
                    self.assertIn("CASGetSimInfo", readback)
                    self.assertIn("create outfits lazily", readback)
                    self.assertIn("not proof of side-effect freedom", readback)

    def test_speed_signature_is_evidence_not_admission(self):
        row = operation_source("live.set_speed")
        self.assertEqual("inspected_call_sites", row["source_status"])
        self.assertEqual(["test_pause", "test_play", "test_speed2", "test_speed3"], row["legacy_operations"])
        self.assertIn("_connection=client.id", row["native_calls"][0])
        self.assertIn("default to denied admission", self.text("live.set_speed", "dependencies"))
        self.assertIn("does not establish simulation advance", self.text("live.set_speed", "readback_limits"))

    def test_exit_stops_at_menu_request(self):
        self.assertIn("requests the normal quit menu only", self.text("game.exit", "scope_limits"))
        self.assertIn("game_exit_verified=False", self.text("game.exit", "readback_limits"))
        self.assertIn("No forced termination", self.text("game.exit", "scope_limits"))

    def test_cas_entry_does_not_strip_preservation_or_use_implicit_sim(self):
        row = operation_source("cas.enter")
        self.assertEqual("apex_only_path", row["source_status"])
        self.assertIn("preservation gates cannot be omitted", self.text("cas.enter", "dependencies"))
        self.assertIn("blank-ID active-Sim fallback", self.text("cas.enter", "scope_limits"))
        self.assertIn("cas_visible_verified=False", self.text("cas.enter", "readback_limits"))


if __name__ == "__main__":
    unittest.main()
