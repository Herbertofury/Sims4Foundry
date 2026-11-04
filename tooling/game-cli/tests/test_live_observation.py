"""Tiny adversarial offline fixtures; no game, process, filesystem or network reader."""

import ast
from copy import deepcopy
import inspect
import json
import unittest
from unittest.mock import patch

from sims4foundry_cli import live_observation as live


def fixture():
    return {
        "schema_version": 1,
        "fixture_observation_id": "fixture-1",
        "fixture_epoch": "fixture-epoch-1",
        "actor_sim_id": "10",
        "sim_instance_available": True,
        "queue_available": True,
        "running_available": True,
        "queued": [],
        "running": [],
    }


def row(**changes):
    value = {"tuning_guid64": "100", "runtime_instance_id": "200",
             "target": {"kind": "object", "id": "300"}, "label": "Fixture interaction"}
    value.update(changes)
    return value


class LiveObservationTests(unittest.TestCase):
    def assert_refused(self, value, code=None):
        with self.assertRaises(live.LiveObservationError) as caught:
            live.validate_live_fixture(value)
        if code:
            self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_empty_is_observed_empty_not_idle_or_completed(self):
        result = live.validate_live_fixture(fixture())
        self.assertEqual([], result["queued"])
        self.assertEqual([], result["running"])
        self.assertEqual("unknown", result["action_outcome"])
        self.assertEqual("this_snapshot_only", result["occurrence_handle_scope"])
        self.assertNotIn("idle", result)
        self.assertNotIn("completed", result)
        self.assertNotIn("cancelled", result)

    def test_qualification_and_source_are_fixed_in_output(self):
        result = live.validate_live_fixture(fixture())
        self.assertEqual("offline_fixture", result["source"])
        for key in ("atomic_snapshot", "live_qualified", "independent_evidence"):
            self.assertIs(result[key], False)
        self.assertEqual("unknown", result["action_outcome"])

    def test_missing_sim_is_distinct_from_empty_channels(self):
        value = fixture()
        value.update(sim_instance_available=False, queue_available=False,
                     running_available=False, queued=None, running=None)
        result = live.validate_live_fixture(value)
        self.assertIs(result["sim_instance_available"], False)
        self.assertIsNone(result["queued"])
        self.assertIsNone(result["running"])
        self.assertEqual("unknown", result["action_outcome"])

    def test_channel_availability_is_independent(self):
        for missing, other in (("queue", "running"), ("running", "queue")):
            value = fixture()
            rows = "queued" if missing == "queue" else "running"
            value[missing + "_available"] = False
            value[rows] = None
            result = live.validate_live_fixture(value)
            self.assertIs(result[other + "_available"], True)
            self.assertIsNone(result[rows])
            self.assertEqual("unknown", result["action_outcome"])

    def test_availability_inconsistencies_refuse(self):
        for flag, key in (("queue_available", "queued"), ("running_available", "running")):
            for available, rows in ((True, None), (False, []), (False, [row()])):
                value = fixture()
                value.update({flag: available, key: rows})
                with self.subTest(flag=flag, available=available, rows=rows):
                    self.assert_refused(value, "inconsistent_availability")
            value = fixture()
            value["sim_instance_available"] = False
            self.assert_refused(value, "inconsistent_availability")

    def test_available_sim_can_have_both_channels_unavailable(self):
        value = fixture()
        value.update(queue_available=False, running_available=False, queued=None, running=None)
        result = live.validate_live_fixture(value)
        self.assertTrue(result["sim_instance_available"])
        self.assertIsNone(result["queued"])
        self.assertIsNone(result["running"])

    def test_availability_requires_exact_booleans(self):
        for key in ("sim_instance_available", "queue_available", "running_available"):
            for invalid in (0, 1, None, "true", []):
                value = fixture()
                value[key] = invalid
                self.assert_refused(value)

    def test_queued_and_running_preserve_order_duplicates_and_separation(self):
        value = fixture()
        repeated = row()
        value["queued"] = [repeated, row(target={"kind": "object", "id": "301"}), repeated]
        value["running"] = [repeated, row(tuning_guid64="101", target={"kind": "sim", "id": "10"})]
        result = live.validate_live_fixture(value)
        self.assertEqual(["100", "100", "100"], [r["tuning_guid64"] for r in result["queued"]])
        self.assertEqual(["300", "301", "300"], [r["target"]["id"] for r in result["queued"]])
        self.assertEqual(["100", "101"], [r["tuning_guid64"] for r in result["running"]])
        self.assertEqual(["queued:0", "queued:1", "queued:2"],
                         [r["occurrence_handle"] for r in result["queued"]])
        self.assertEqual(["running:0", "running:1"],
                         [r["occurrence_handle"] for r in result["running"]])
        self.assertEqual("200", result["running"][0]["runtime_instance_id"])

    def test_handles_do_not_encode_ids_or_claim_cross_snapshot_identity(self):
        first = fixture()
        first["queued"] = [row()]
        second = deepcopy(first)
        second["fixture_epoch"] = "another-fixture-label"
        second["queued"] = [row(tuning_guid64="999", runtime_instance_id="998")]
        a = live.validate_live_fixture(first)
        b = live.validate_live_fixture(second)
        self.assertEqual("queued:0", a["queued"][0]["occurrence_handle"])
        self.assertEqual(a["queued"][0]["occurrence_handle"], b["queued"][0]["occurrence_handle"])
        self.assertNotEqual(a["queued"][0]["tuning_guid64"], b["queued"][0]["tuning_guid64"])

    def test_missing_or_null_optional_fields_remain_unavailable(self):
        value = fixture()
        value["queued"] = [{"tuning_guid64": "100"}, row(runtime_instance_id=None, target=None, label=None)]
        result = live.validate_live_fixture(value)
        for interaction in result["queued"]:
            for key in ("runtime_instance_id", "target", "label"):
                self.assertIsNone(interaction[key])
            self.assertEqual("100", interaction["tuning_guid64"])

    def test_output_is_detached_in_both_directions_and_from_aliases(self):
        value = fixture()
        shared_row = row()
        value["queued"] = [shared_row, shared_row]
        value["running"] = value["queued"]
        original = deepcopy(value)
        first = live.validate_live_fixture(value)
        self.assertEqual(original, value)
        value["queued"][0]["target"]["id"] = "999"
        value["running"].clear()
        self.assertEqual("300", first["queued"][0]["target"]["id"])
        self.assertEqual(2, len(first["running"]))
        first["queued"][0]["target"]["id"] = "998"
        self.assertEqual("300", first["queued"][1]["target"]["id"])
        self.assertEqual("300", first["running"][0]["target"]["id"])
        second = live.validate_live_fixture(original)
        first["source"] = "pretend_live"
        first["live_qualified"] = True
        first["action_outcome"] = "completed"
        self.assertEqual("offline_fixture", second["source"])
        self.assertIs(second["live_qualified"], False)
        self.assertEqual("unknown", second["action_outcome"])

    def test_all_identifiers_are_canonical_positive_uint64_strings_without_coercion(self):
        for invalid in (True, False, 0, 1, -1, live.MAX_UINT64, live.MAX_UINT64 + 1,
                        1.0, "0", "-1", "+1", "01", " 1", "1 ", "1\n", "1.0", "1e0", "١",
                        str(live.MAX_UINT64 + 1), "1" * 21, "", None):
            for slot in ("actor", "tuning", "runtime", "target"):
                if slot == "runtime" and invalid is None:
                    continue
                value = fixture()
                value["queued"] = [row()]
                if slot == "actor":
                    value["actor_sim_id"] = invalid
                elif slot == "target":
                    value["queued"][0]["target"]["id"] = invalid
                else:
                    key = "tuning_guid64" if slot == "tuning" else "runtime_instance_id"
                    value["queued"][0][key] = invalid
                with self.subTest(slot=slot, invalid=invalid):
                    self.assert_refused(value)

    def test_uint64_boundaries_are_preserved_without_float_roundtrip(self):
        for number in (1, 2**53 - 1, 2**53, 2**53 + 1, live.MAX_UINT64):
            identifier = str(number)
            value = fixture()
            value["actor_sim_id"] = identifier
            value["queued"] = [row(tuning_guid64=identifier, runtime_instance_id=identifier,
                                   target={"kind": "object", "id": identifier})]
            result = json.loads(json.dumps(live.validate_live_fixture(value)))
            self.assertEqual(identifier, result["actor_sim_id"])
            self.assertEqual(identifier, result["queued"][0]["tuning_guid64"])
            self.assertEqual(identifier, result["queued"][0]["runtime_instance_id"])
            self.assertEqual(identifier, result["queued"][0]["target"]["id"])
            self.assertIs(type(result["queued"][0]["tuning_guid64"]), str)

    def test_targets_require_both_explicit_kind_and_instance_id(self):
        for invalid in ({}, {"id": "300"}, {"kind": "object"}, {"kind": "definition", "id": "300"},
                        {"kind": "none", "id": "300"}, {"kind": "sim", "id": "300", "name": "x"},
                        "actor", [], 300):
            value = fixture()
            value["queued"] = [row(target=invalid)]
            self.assert_refused(value)

    def test_empty_and_wrong_type_labels_refuse(self):
        for invalid in ("", 1, True, [], {}):
            value = fixture()
            value["queued"] = [row(label=invalid)]
            self.assert_refused(value)

    def test_utf8_label_byte_bound_and_surrogates(self):
        value = fixture()
        value["queued"] = [row(label="😀" * (live.MAX_STRING_BYTES // 4))]
        self.assertEqual(value["queued"][0]["label"], live.validate_live_fixture(value)["queued"][0]["label"])
        for invalid in ("x" * (live.MAX_STRING_BYTES + 1), "😀" * 65, "\ud800", "\udfff"):
            value["queued"][0]["label"] = invalid
            self.assert_refused(value)

    def test_fixture_labels_are_labels_not_timestamps_or_runtime_identity(self):
        for key in ("fixture_observation_id", "fixture_epoch"):
            value = fixture()
            value[key] = "a" * live.MAX_LABEL_CHARS
            self.assertEqual(value[key], live.validate_live_fixture(value)[key])
            for invalid in (None, 1, True, "", "a" * 65, "../path", "a b", "雪", "2026-11-04T12:00:00Z"):
                value[key] = invalid
                self.assert_refused(value)

    def test_only_declared_root_fields_are_accepted(self):
        for key in fixture():
            value = fixture()
            del value[key]
            self.assert_refused(value)
        for key, item in (("source", "offline_fixture"), ("live_qualified", True),
                          ("atomic_snapshot", True), ("independent_evidence", True),
                          ("action_outcome", "completed"), ("cancelled", True),
                          ("occurrence_handle_scope", "durable"),
                          ("observed_at", "now"), ("events", []), ("enqueue_result", True)):
            value = fixture()
            value[key] = item
            self.assert_refused(value)

    def test_row_cannot_supply_handles_statuses_or_event_claims(self):
        for key, item in (("occurrence_handle", "durable-id"), ("state", "completed"),
                          ("cancelled", True), ("started", True), ("event", "cancelled"),
                          ("action_outcome", "success")):
            value = fixture()
            value["queued"] = [row(**{key: item})]
            self.assert_refused(value)

    def test_label_names_and_disappearance_do_not_change_unknown_outcome(self):
        value = fixture()
        for label in ("completed", "cancelled", "success", "interaction_post_run"):
            value["queued"] = [row(label=label)]
            result = live.validate_live_fixture(value)
            self.assertEqual(label, result["queued"][0]["label"])
            self.assertEqual("unknown", result["action_outcome"])
        value["queued"] = []
        self.assertEqual("unknown", live.validate_live_fixture(value)["action_outcome"])

    def test_schema_version_is_exact_and_known(self):
        for invalid in (True, False, 0, 2, "1", 1.0, None):
            value = fixture()
            value["schema_version"] = invalid
            self.assert_refused(value)

    def test_total_row_bound_spans_both_channels(self):
        value = fixture()
        value["queued"] = [row(label="x" * live.MAX_STRING_BYTES)] * live.MAX_ROWS
        self.assertEqual(live.MAX_ROWS, len(live.validate_live_fixture(value)["queued"]))
        value["queued"] = [{"tuning_guid64": "1"}] * (live.MAX_ROWS // 2)
        value["running"] = [{"tuning_guid64": "1"}] * (live.MAX_ROWS // 2)
        result = live.validate_live_fixture(value)
        self.assertEqual(live.MAX_ROWS, len(result["queued"]) + len(result["running"]))
        value["running"].append({"tuning_guid64": "1"})
        with patch.object(live, "_rows", side_effect=AssertionError("No row copies expected")):
            self.assert_refused(value, "limit_exceeded")
        value["queued"] = [{"tuning_guid64": "1"}] * (live.MAX_ROWS + 1)
        self.assert_refused(value, "limit_exceeded")

    def test_both_input_and_normalized_output_have_compact_byte_bounds(self):
        value = fixture()
        value["queued"] = [row(label="\x00" * live.MAX_STRING_BYTES)] * live.MAX_ROWS
        self.assert_refused(value, "limit_exceeded")
        value = fixture()
        compact_input = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        with patch.object(live, "MAX_DATA_BYTES", compact_input):
            self.assert_refused(value, "limit_exceeded")
        with patch.object(live, "MAX_DATA_BYTES", compact_input - 1):
            self.assert_refused(value, "limit_exceeded")

    def test_all_floats_including_nonfinite_are_refused(self):
        for number in (float("nan"), float("inf"), -float("inf"), 1.0, 0.0):
            value = fixture()
            value["queued"] = [row(runtime_instance_id=number)]
            self.assert_refused(value)

    def test_cyclic_deep_or_many_node_data_refuses_before_serialization(self):
        cyclic = fixture()
        cyclic["queued"] = [cyclic]
        deep = fixture()
        deep["queued"] = [[[[[]]]]]
        many = fixture()
        many["queued"] = [None] * (live.MAX_NODES + 1)
        for value in (cyclic, deep, many):
            with patch.object(live, "_check_encoded_size", side_effect=AssertionError("No encoding expected")):
                self.assert_refused(value)

    def test_nonplain_inputs_do_not_invoke_custom_methods_or_coercion(self):
        class Hostile:
            def __repr__(self):
                raise AssertionError("repr must not run")
            def __int__(self):
                raise AssertionError("int must not run")
            def __iter__(self):
                raise AssertionError("iterator must not run")
        class DictSubclass(dict):
            def items(self):
                raise AssertionError("items must not run")
        class ListSubclass(list):
            def __iter__(self):
                raise AssertionError("iterator must not run")
        class IntSubclass(int):
            pass
        class StringSubclass(str):
            pass
        for invalid in (Hostile(), DictSubclass(fixture()), ListSubclass(), (), b"{}", "{}"):
            self.assert_refused(invalid)
        for invalid in (Hostile(), IntSubclass(1), StringSubclass("1")):
            value = fixture()
            value["actor_sim_id"] = invalid
            self.assert_refused(value)
        value = fixture()
        value["queued"] = ListSubclass()
        self.assert_refused(value)
        value = fixture()
        value[StringSubclass("extra")] = None
        self.assert_refused(value)

    def test_errors_do_not_echo_values_or_unknown_keys(self):
        marker = "PRIVATE_SENTINEL_DO_NOT_PRINT"
        value = fixture()
        value[marker] = marker
        error = self.assert_refused(value)
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, error.code)
        value = fixture()
        value["actor_sim_id"] = marker
        self.assertNotIn(marker, str(self.assert_refused(value)))

    def test_validate_never_imports_or_performs_io_or_reads_a_clock(self):
        tree = ast.parse(inspect.getsource(live))
        imports = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
        self.assertEqual({"json", "re"}, imports)
        self.assertFalse(any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree)))
        with patch("builtins.open", side_effect=AssertionError("No file reads")), \
                patch("builtins.__import__", side_effect=AssertionError("No imports")):
            result = live.validate_live_fixture(fixture())
        self.assertNotIn("timestamp", result)
        self.assertNotIn("process_epoch", result)
        self.assertNotIn("session_id", result)


if __name__ == "__main__":
    unittest.main()
