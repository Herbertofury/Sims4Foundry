from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from sims4foundry_cli.catalog import CATALOG, capabilities
from sims4foundry_cli.engine import Session, create_session
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import digest, read_json, replace_json
from sims4foundry_cli.workflows import run, validate


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "session"
        create_session(self.root)
        self.now = 1000.0
        self.session = Session(self.root, clock=lambda: self.now)

    def execute(self, name, args=None):
        p = self.session.plan(name, args or {})["plan"]
        return self.session.execute(p["plan_id"], p["plan_sha256"])

    def assert_code(self, code, fn):
        with self.assertRaises(FoundryError) as caught:
            fn()
        self.assertEqual(code, caught.exception.code)

    def test_all_live_capabilities_are_unqualified(self):
        cap = capabilities()
        self.assertFalse(cap["real_game_control_ready"])
        self.assertGreaterEqual(len(cap["operations"]), 40)
        self.assertTrue(all(not op["live_qualified"] and op["availability"] == "unqualified" for op in cap["operations"]))

    def test_real_session_is_blocked(self):
        self.assert_code("live_backend_unqualified", lambda: create_session(self.root / "real", "windows"))
        self.assertFalse((self.root / "real").exists())

    def test_plan_has_no_mutation(self):
        before = self.session.status()["state"]
        result = self.session.plan("game.launch", {})
        self.assertFalse(result["executed"])
        self.assertEqual(before, self.session.status()["state"])

    def test_exact_digest_required(self):
        plan = self.session.plan("game.launch", {})["plan"]
        self.assert_code("plan_digest_mismatch", lambda: self.session.execute(plan["plan_id"], "bad"))
        self.assertEqual(0, self.session.status()["state"]["revision"])

    def test_same_plan_is_never_dispatched_twice(self):
        p = self.session.plan("game.launch", {})["plan"]
        first = self.session.execute(p["plan_id"], p["plan_sha256"])
        again = self.session.execute(p["plan_id"], p["plan_sha256"])
        self.assertTrue(first["simulation"])
        self.assertTrue(again["replayed_result"])
        self.assertFalse(again["dispatched_again"])
        self.assertEqual(1, self.session.load()["revision"])

    def test_corrupt_result_cannot_clear_pending_or_claim_real_evidence(self):
        p = self.session.plan("game.launch", {})["plan"]
        self.session.execute(p["plan_id"], p["plan_sha256"])
        path = self.root / "operations" / p["plan_id"] / "result.json"
        valid = read_json(path)
        mutations = ({"real_game_evidence": True}, {"plan_sha256": "wrong"}, {"simulation": False},
                     {"mode": "cas"}, {"after_observation_id": "0" * 32}, {"ok": 1})
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                replace_json(path, dict(valid, **mutation))
                self.assertEqual([p["plan_id"]], self.session.status()["pending_operations"])
                self.assert_code("receipt_corrupt", lambda: self.session.reconcile(p["plan_id"]))
                self.assert_code("receipt_corrupt", lambda: self.session.execute(p["plan_id"], p["plan_sha256"]))
                self.assert_code("pending_operation", lambda: self.session.plan("game.new", {}))
        replace_json(path, valid)
        self.assertEqual([], self.session.status()["pending_operations"])

    def test_truncated_result_stays_pending(self):
        p = self.session.plan("game.launch", {})["plan"]
        self.session.execute(p["plan_id"], p["plan_sha256"])
        (self.root / "operations" / p["plan_id"] / "result.json").write_text('{"ok":')
        self.assertEqual([p["plan_id"]], self.session.status()["pending_operations"])
        self.assert_code("receipt_corrupt", lambda: self.session.reconcile(p["plan_id"]))

    def test_empty_operation_directory_recovers_as_not_dispatched(self):
        p = self.session.plan("game.launch", {})["plan"]
        (self.root / "operations" / p["plan_id"]).mkdir()
        self.assertEqual([p["plan_id"]], self.session.status()["pending_operations"])
        result = self.session.reconcile(p["plan_id"])
        self.assertEqual("not_dispatched", result["outcome"])
        self.assertFalse(result["real_game_evidence"])
        self.assertEqual([], self.session.status()["pending_operations"])
        self.assertEqual(0, self.session.load()["revision"])

    def test_dispatch_marker_without_intent_stays_blocked(self):
        from sims4foundry_cli.io import new_json
        p = self.session.plan("game.launch", {})["plan"]
        directory = self.root / "operations" / p["plan_id"]
        directory.mkdir()
        new_json(directory / "dispatch_started.json", {})
        self.assert_code("receipt_corrupt", lambda: self.session.reconcile(p["plan_id"]))
        self.assertEqual([p["plan_id"]], self.session.status()["pending_operations"])

    def test_huge_integer_is_structured_validation_error(self):
        self.assert_code("invalid_argument", lambda: CATALOG["cas.set_physique"].validate({"fitness": 10 ** 400, "weight": 0}))

    def test_stale_plan_refused(self):
        p = self.session.plan("game.launch", {})["plan"]
        self.execute("game.launch")
        self.assert_code("stale_plan", lambda: self.session.execute(p["plan_id"], p["plan_sha256"]))

    def test_expired_and_future_plans_refused(self):
        for delta in (31, -1):
            with self.subTest(delta=delta):
                p = self.session.plan("game.launch", {})["plan"]
                self.now += delta
                self.assert_code("stale_plan", lambda: self.session.execute(p["plan_id"], p["plan_sha256"]))
                self.now -= delta

    def test_wrong_mode_and_raw_input_refused(self):
        self.assert_code("wrong_mode", lambda: self.session.plan("cas.apply_part", {"part_id": "x", "body_type": "hair"}))
        self.assert_code("unsupported_operation", lambda: self.session.plan("game.raw_click", {"x": 0}))

    def test_unknown_and_malformed_arguments_refused(self):
        for value in ({"extra": True}, [], None):
            self.assert_code("invalid_schema", lambda: self.session.plan("game.launch", value))
        for value in (True, -1, 4, float("nan"), "1"):
            self.assert_code("invalid_argument", lambda: CATALOG["live.set_speed"].validate({"speed": value}))

    def test_tampered_plan_rejected(self):
        p = self.session.plan("game.launch", {})["plan"]
        file = self.root / "plans" / (p["plan_id"] + ".json")
        changed = dict(p, backend="windows")
        replace_json(file, changed)
        self.assert_code("invalid_plan", lambda: self.session.execute(p["plan_id"], p["plan_sha256"]))

    def test_tampered_observation_refused(self):
        p = self.session.plan("game.launch", {})["plan"]
        file = self.root / "observations" / (p["before_observation_id"] + ".json")
        replace_json(file, dict(read_json(file), simulation=False))
        self.assert_code("invalid_observation", lambda: self.session.execute(p["plan_id"], p["plan_sha256"]))

    def test_backend_forgery_refused(self):
        replace_json(self.root / "state.json", dict(self.session.load(), backend="windows"))
        self.assert_code("live_backend_unqualified", self.session.load)

    def test_persistence_failure_before_dispatch_has_zero_backend_calls(self):
        p = self.session.plan("game.launch", {})["plan"]
        with mock.patch("sims4foundry_cli.engine.new_json", side_effect=OSError("disk full")), mock.patch.object(self.session, "_apply") as apply:
            with self.assertRaises(OSError):
                self.session.execute(p["plan_id"], p["plan_sha256"])
            apply.assert_not_called()

    def test_after_state_failure_reconciles_without_replay(self):
        def fault(point):
            if point == "state_persisted":
                raise OSError("receipt unavailable")
        self.session.fault = fault
        p = self.session.plan("game.launch", {})["plan"]
        self.assert_code("dispatch_uncertain", lambda: self.session.execute(p["plan_id"], p["plan_sha256"]))
        self.assert_code("pending_operation", lambda: self.session.plan("game.new", {}))
        with mock.patch.object(self.session, "_apply") as apply:
            result = self.session.reconcile(p["plan_id"])
            apply.assert_not_called()
        self.assertTrue(result["recovered_by_observation"])
        self.assertEqual(1, self.session.load()["revision"])

    def test_after_dispatch_failure_never_replays(self):
        def fault(point):
            if point == "dispatch_started":
                raise RuntimeError("simulated crash")
        self.session.fault = fault
        p = self.session.plan("game.launch", {})["plan"]
        with self.assertRaises(RuntimeError):
            self.session.execute(p["plan_id"], p["plan_sha256"])
        with mock.patch.object(self.session, "_apply") as apply:
            result = self.session.reconcile(p["plan_id"])
            apply.assert_not_called()
        self.assertEqual("unresolved", result["outcome"])
        self.assert_code("pending_operation", lambda: self.session.execute(p["plan_id"], p["plan_sha256"]))
        self.assertEqual(0, self.session.load()["revision"])

    def test_before_dispatch_crash_closes_without_mutation(self):
        self.session.fault = lambda point: (_ for _ in ()).throw(RuntimeError("crash"))
        p = self.session.plan("game.launch", {})["plan"]
        with self.assertRaises(RuntimeError):
            self.session.execute(p["plan_id"], p["plan_sha256"])
        result = self.session.reconcile(p["plan_id"])
        self.assertEqual("not_dispatched", result["outcome"])
        self.assertEqual(0, self.session.load()["revision"])

    def test_cas_cancel_restores_before_state(self):
        self.execute("game.launch")
        self.execute("game.new")
        self.execute("cas.set_name", {"first_name": "Test", "last_name": "Fixture"})
        self.execute("cas.cancel")
        state = self.session.load()
        self.assertEqual("main_menu", state["mode"])
        self.assertEqual({}, state["properties"])
        self.assertFalse(state["dirty"])

    def test_explicit_save_required_before_retaining_exit(self):
        self.execute("game.launch")
        self.execute("game.new")
        self.execute("cas.accept")
        self.assert_code("explicit_save_required", lambda: self.session.plan("game.exit", {"save_policy": "save"}))
        self.execute("game.save", {"slot_id": "test", "slot_name": "Test"})
        self.execute("game.exit", {"save_policy": "save"})
        self.assertEqual("closed", self.session.load()["mode"])

    def test_undo_redo(self):
        self.execute("game.launch"); self.execute("game.new")
        args = {"first_name": "A", "last_name": "B"}
        self.execute("cas.set_name", args)
        self.execute("cas.undo")
        self.assertNotIn("cas.set_name", self.session.load()["properties"])
        self.execute("cas.redo")
        self.assertEqual(args, self.session.load()["properties"]["cas.set_name"])

    def test_unknown_save_does_not_dispatch(self):
        self.execute("game.launch")
        self.assert_code("unknown_save", lambda: self.session.plan("game.load", {"slot_id": "missing"}))
        self.assertEqual(1, self.session.load()["revision"])

    def test_full_synthetic_regression(self):
        value = read_json(Path(__file__).parents[1] / "examples" / "mod-regression.simulation.json")
        result = run(value, self.root)
        self.assertTrue(result["ok"])
        self.assertFalse(result["real_game_evidence"])
        self.assertEqual(14, len(result["results"]))
        self.assertEqual("closed", self.session.load()["mode"])
        self.assertIn("fixture.slot", self.session.load()["saved_slots"])

    def test_workflow_stops_first_failure(self):
        value = {"schema": 1, "name": "test", "backend": "simulation", "steps": [
            {"operation": "game.launch", "arguments": {}},
            {"operation": "build.enter", "arguments": {}},
            {"operation": "game.new", "arguments": {}}]}
        result = run(value, self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(1, result["stopped_at_step"])
        self.assertEqual(1, self.session.load()["revision"])

    def test_real_workflow_refused(self):
        self.assert_code("live_backend_unqualified", lambda: validate({"schema": 1, "name": "bad", "backend": "windows", "steps": []}))


if __name__ == "__main__":
    unittest.main()
