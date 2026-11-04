"""Inspectable workflows execute one independently gated step at a time."""
from .catalog import get_operation
from .engine import Session
from .errors import FoundryError
from .io import exact_fields


def validate(value):
    exact_fields(value, {"schema", "name", "backend", "steps"})
    if type(value["schema"]) is not int or value["schema"] != 1 or value["backend"] != "simulation":
        raise FoundryError("live_backend_unqualified", "This release only executes synthetic workflows.")
    if not isinstance(value["name"], str) or not 0 < len(value["name"]) <= 200:
        raise FoundryError("invalid_workflow", "Workflow needs a bounded name.")
    if not isinstance(value["steps"], list) or not 1 <= len(value["steps"]) <= 100:
        raise FoundryError("invalid_workflow", "Workflow needs 1 to 100 steps.")
    for step in value["steps"]:
        exact_fields(step, {"operation", "arguments"})
        get_operation(step["operation"]).validate(step["arguments"])
    return {"ok": True, "valid_schema": True, "simulation": True,
            "step_count": len(value["steps"]), "real_game_evidence": False,
            "message": "Argument validation only. Runtime preconditions are checked before each step."}


def run(value, directory):
    validate(value)
    session = Session(directory)
    results = []
    for index, step in enumerate(value["steps"]):
        try:
            plan = session.plan(step["operation"], step["arguments"])["plan"]
            results.append(session.execute(plan["plan_id"], plan["plan_sha256"]))
        except FoundryError as exc:
            return {"ok": False, "simulation": True, "stopped_at_step": index,
                    "completed_steps": results, "error": exc.as_dict(), "real_game_evidence": False,
                    "message": "Stopped at the first failure. No previous or uncertain action was replayed."}
    return {"ok": True, "simulation": True, "results": results, "real_game_evidence": False}
