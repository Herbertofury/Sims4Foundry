"""Single-dispatch workflow engine with an explicitly synthetic model backend.

No module in this file can start a real game, import an Apex helper, or send
Windows input. The model tests orchestration, not game APIs or UI correctness.
"""
from copy import deepcopy
from pathlib import Path
import time
import uuid
from .catalog import get_operation, STATES
from .errors import FoundryError
from .io import (digest, exact_fields, exact_int, external_path, lock, new_json,
                 plain_path, read_json, replace_json)

SCHEMA = 1
MAX_PLAN_AGE = 30


def _id(value):
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise FoundryError("invalid_id", "Expected a returned 32-character operation/plan identifier.")
    return value


def create_session(directory, backend="simulation"):
    if backend != "simulation":
        raise FoundryError("live_backend_unqualified", "Real session creation is blocked until a no-Apex adapter has passed qualification.")
    directory = external_path(directory)
    # mkdir(exist_ok=False) is the non-destructive ownership boundary.
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "plans").mkdir()
    (directory / "operations").mkdir()
    (directory / "observations").mkdir()
    value = {"schema": SCHEMA, "backend": backend, "session_id": uuid.uuid4().hex,
             "revision": 0, "mode": "closed", "dirty": False, "properties": {},
             "saved_slots": {}, "last_operation": None, "undo": [], "redo": [],
             "origin": None, "cas_baseline": None}
    new_json(directory / "state.json", value)
    return {"ok": True, "simulation": True, "session": str(directory), "session_id": value["session_id"]}


class Session:
    def __init__(self, directory, clock=time.time, fault=None):
        self.directory = external_path(directory)
        self.clock = clock
        self.fault = fault or (lambda _: None)

    def _path(self, *parts):
        return plain_path(self.directory.joinpath(*parts))

    def load(self):
        value = read_json(self._path("state.json"))
        exact_fields(value, {"schema", "backend", "session_id", "revision", "mode", "dirty", "properties",
                             "saved_slots", "last_operation", "undo", "redo", "origin", "cas_baseline"})
        if type(value["schema"]) is not int or value["schema"] != SCHEMA or value["backend"] != "simulation":
            raise FoundryError("live_backend_unqualified", "Only explicitly synthetic sessions can execute in this release.")
        _id(value["session_id"])
        if not exact_int(value["revision"]) or value["mode"] not in STATES or type(value["dirty"]) is not bool:
            raise FoundryError("invalid_state", "Session state is malformed.")
        if not isinstance(value["properties"], dict) or not isinstance(value["saved_slots"], dict):
            raise FoundryError("invalid_state", "Session model is malformed.")
        return value

    def _lock(self):
        return lock(self._path("operation.lock"))

    def _pending(self):
        rows = []
        for directory in self._path("operations").iterdir():
            plain_path(directory)
            if not directory.is_dir():
                continue
            result_path = directory / "result.json"
            if result_path.exists():
                try:
                    result = read_json(result_path)
                    if (not isinstance(result, dict) or result.get("operation_id") != directory.name
                            or result.get("outcome") not in ("verified_simulation", "not_dispatched")):
                        raise FoundryError("receipt_corrupt", "Terminal receipt is invalid.")
                except (FoundryError, OSError):
                    rows.append(directory.name)
            else:
                rows.append(directory.name)
        return sorted(rows)

    def _observe(self, state):
        value = {"schema": SCHEMA, "observation_id": uuid.uuid4().hex,
                 "session_id": state["session_id"], "backend": "simulation", "simulation": True,
                 "revision": state["revision"], "mode": state["mode"], "dirty": state["dirty"],
                 "state_sha256": digest(state), "observed_at": self.clock(),
                 "source": "synthetic_model", "real_game_evidence": False}
        new_json(self._path("observations", value["observation_id"] + ".json"), value)
        return value

    def status(self):
        with self._lock():
            state = self.load()
            return {"ok": True, "simulation": True, "state": state, "pending_operations": self._pending(),
                    "real_game_control_ready": False}

    def observe(self):
        with self._lock():
            return {"ok": True, "observation": self._observe(self.load())}

    def plan(self, name, arguments):
        operation = get_operation(name)
        operation.validate(arguments)
        with self._lock():
            state = self.load()
            pending = self._pending()
            if pending:
                raise FoundryError("pending_operation", "Reconcile the previous dispatch before planning another mutation.", operation_ids=pending)
            if state["mode"] not in operation.states:
                raise FoundryError("wrong_mode", "Operation is not valid in the observed mode.", observed=state["mode"], required=operation.states)
            self._precondition(state, name, arguments)
            before = self._observe(state)
            plan = {"schema": SCHEMA, "plan_id": uuid.uuid4().hex, "session_id": state["session_id"],
                    "backend": "simulation", "operation": name, "arguments": arguments,
                    "before_observation_id": before["observation_id"], "before_state_sha256": digest(state),
                    "revision": state["revision"], "created_at": self.clock(),
                    "simulation": True, "real_game_evidence": False}
            plan["plan_sha256"] = digest(plan)
            new_json(self._path("plans", plan["plan_id"] + ".json"), plan)
            return {"ok": True, "executed": False, "plan": plan}

    def _read_plan(self, plan_id):
        plan = read_json(self._path("plans", _id(plan_id) + ".json"))
        exact_fields(plan, {"schema", "plan_id", "session_id", "backend", "operation", "arguments",
                           "before_observation_id", "before_state_sha256", "revision", "created_at",
                           "simulation", "real_game_evidence", "plan_sha256"})
        unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
        if (plan["plan_id"] != plan_id or plan["schema"] != SCHEMA or plan["backend"] != "simulation"
                or plan["simulation"] is not True or plan["real_game_evidence"] is not False
                or digest(unsigned) != plan["plan_sha256"]):
            raise FoundryError("invalid_plan", "Plan identity/digest/backend changed.")
        _id(plan["before_observation_id"])
        return plan

    def execute(self, plan_id, expected_sha256):
        with self._lock():
            plan = self._read_plan(plan_id)
            if expected_sha256 != plan["plan_sha256"]:
                raise FoundryError("plan_digest_mismatch", "Execution requires the exact returned plan SHA-256.")
            directory = self._path("operations", plan_id)
            if directory.exists():
                intent = read_json(directory / "intent.json")
                if intent["plan_sha256"] != plan["plan_sha256"]:
                    raise FoundryError("intent_conflict", "Operation ID already belongs to different parameters.")
                if (directory / "result.json").exists():
                    return dict(read_json(directory / "result.json"), replayed_result=True, dispatched_again=False)
                raise FoundryError("pending_operation", "An intent already exists. Reconcile it; do not resend.", operation_id=plan_id)
            state = self.load()
            pending = self._pending()
            if pending:
                raise FoundryError("pending_operation", "An earlier dispatch is unresolved.", operation_ids=pending)
            if plan["session_id"] != state["session_id"] or plan["revision"] != state["revision"] or plan["before_state_sha256"] != digest(state):
                raise FoundryError("stale_plan", "Session identity/state changed after planning.")
            age = self.clock() - plan["created_at"]
            if not 0 <= age <= MAX_PLAN_AGE:
                raise FoundryError("stale_plan", "Plan expired or the clock moved backwards; make a new plan.")
            before = read_json(self._path("observations", plan["before_observation_id"] + ".json"))
            if (before.get("state_sha256") != digest(state) or before.get("session_id") != state["session_id"]
                    or before.get("simulation") is not True or before.get("source") != "synthetic_model"):
                raise FoundryError("invalid_observation", "Before evidence no longer matches the exact session.")
            operation = get_operation(plan["operation"])
            operation.validate(plan["arguments"])
            if state["mode"] not in operation.states:
                raise FoundryError("wrong_mode", "Mode changed before dispatch.")
            self._precondition(state, operation.name, plan["arguments"])
            directory.mkdir()
            new_json(directory / "intent.json", plan)
            self.fault("intent_persisted")
            # Persist dispatch intent BEFORE model mutation. Windows power-loss
            # ordering is not yet qualified for live-game use.
            new_json(directory / "dispatch_started.json", {"operation_id": plan_id, "plan_sha256": expected_sha256,
                                                          "at": self.clock(), "simulation": True})
            self.fault("dispatch_started")
            try:
                after = self._apply(state, operation, plan["arguments"])
                after["revision"] += 1
                after["last_operation"] = {"id": plan_id, "plan_sha256": expected_sha256}
                replace_json(self._path("state.json"), after)
                self.fault("state_persisted")
                observed = self._observe(self.load())
                return self._finish(directory, plan, observed, recovered=False)
            except Exception as exc:
                # No retries, no implicit rollback, no assumption that nothing happened.
                raise FoundryError("dispatch_uncertain", "Dispatch began but its result was not durably verified. Reconcile this ID only.", operation_id=plan_id, cause=type(exc).__name__) from exc

    def _finish(self, directory, plan, observed, recovered):
        if observed["revision"] != plan["revision"] + 1:
            raise FoundryError("postcondition_failed", "Expected exactly one state transition.")
        result = {"ok": True, "outcome": "verified_simulation", "simulation": True,
                  "real_game_evidence": False, "operation_id": plan["plan_id"],
                  "operation": plan["operation"], "plan_sha256": plan["plan_sha256"],
                  "before_observation_id": plan["before_observation_id"],
                  "after_observation_id": observed["observation_id"], "mode": observed["mode"],
                  "recovered_by_observation": recovered, "dispatched_again": False}
        new_json(directory / "result.json", result)
        return result

    def reconcile(self, operation_id):
        with self._lock():
            directory = self._path("operations", _id(operation_id))
            plan = read_json(directory / "intent.json")
            if (directory / "result.json").exists():
                return dict(read_json(directory / "result.json"), replayed_result=True, dispatched_again=False)
            if not (directory / "dispatch_started.json").exists():
                result = {"ok": False, "outcome": "not_dispatched", "operation_id": operation_id,
                          "simulation": True, "dispatched_again": False,
                          "message": "Durable dispatch marker absent. This intent is closed; create a fresh plan."}
                new_json(directory / "result.json", result)
                return result
            state = self.load()
            if (state["session_id"] == plan["session_id"] and state["last_operation"] ==
                    {"id": operation_id, "plan_sha256": plan["plan_sha256"]}):
                return self._finish(directory, plan, self._observe(state), recovered=True)
            return {"ok": False, "outcome": "unresolved", "simulation": True, "operation_id": operation_id,
                    "dispatched_again": False, "message": "No exact postcondition exists. No retry or rollback was performed."}

    @staticmethod
    def _precondition(state, name, arguments):
        if name == "game.load" and arguments["slot_id"] not in state["saved_slots"]:
            raise FoundryError("unknown_save", "Simulator has no such saved slot; create and save a synthetic household first.")
        if name in ("game.main_menu", "game.exit") and arguments["save_policy"] == "save" and state["dirty"]:
            raise FoundryError("explicit_save_required", "Save to an explicit slot before requesting an exit that retains changes.")
        if name.endswith(".undo") and not state["undo"]:
            raise FoundryError("empty_history", "Nothing to undo in this synthetic mode.")
        if name.endswith(".redo") and not state["redo"]:
            raise FoundryError("empty_history", "Nothing to redo in this synthetic mode.")

    @staticmethod
    def _apply(state, operation, arguments):
        state = deepcopy(state)
        name = operation.name
        if name == "game.save":
            state["saved_slots"][arguments["slot_id"]] = {"name": arguments["slot_name"], "properties": deepcopy(state["properties"])}
            state["dirty"] = False
        elif name == "game.load":
            state["properties"] = deepcopy(state["saved_slots"][arguments["slot_id"]]["properties"])
            state["dirty"] = False
        elif operation.effect == "undo":
            state["redo"].append(deepcopy(state["properties"]))
            state["properties"] = state["undo"].pop()
            state["dirty"] = True
        elif operation.effect == "redo":
            state["undo"].append(deepcopy(state["properties"]))
            state["properties"] = state["redo"].pop()
            state["dirty"] = True
        elif operation.effect == "edit":
            # This is only an abstract command model, NOT Sims emulation.
            state["undo"].append(deepcopy(state["properties"]))
            state["undo"] = state["undo"][-50:]
            state["redo"] = []
            state["properties"][name] = deepcopy(arguments)
            state["dirty"] = True
        if name in ("game.new", "cas.enter"):
            state["origin"] = state["mode"]
            state["cas_baseline"] = {"properties": deepcopy(state["properties"]), "dirty": state["dirty"]}
        if name == "cas.cancel":
            state["properties"] = state["cas_baseline"]["properties"]
            state["dirty"] = state["cas_baseline"]["dirty"]
            state["mode"] = state["origin"]
        elif operation.effect in STATES:
            state["mode"] = operation.effect
        if name == "cas.accept":
            state["dirty"] = True
        if name in ("cas.accept", "cas.cancel"):
            state["cas_baseline"] = None
            state["origin"] = None
        if name in ("game.main_menu", "game.exit"):
            state["dirty"] = False
            state["properties"] = {}
            state["origin"] = None
            state["cas_baseline"] = None
        if operation.effect in STATES:
            state["undo"] = []
            state["redo"] = []
        return state
