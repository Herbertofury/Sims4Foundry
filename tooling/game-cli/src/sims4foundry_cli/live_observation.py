"""Strict, detached Live observation DTOs made only from supplied offline fixtures.

This module has no readers, game imports, events, clock, I/O, authority, or action
methods. Validation does not establish that a fixture ever described a game.
Tuning IDs describe types; optional runtime IDs do not establish durable identity.
Occurrence handles identify positions in this one returned snapshot only.
"""

import json
import re


SCHEMA_VERSION = 1
MAX_UINT64 = 2**64 - 1
MAX_ROWS = 64  # Combined queued and running rows, not a per-channel allowance.
MAX_STRING_BYTES = 256
MAX_LABEL_CHARS = 64
MAX_DEPTH = 4
MAX_NODES = 1024
MAX_DATA_BYTES = 32 * 1024

_FIXTURE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_IDENTIFIER = re.compile(r"[1-9][0-9]{0,19}\Z")
_ROOT_FIELDS = frozenset((
    "schema_version", "fixture_observation_id", "fixture_epoch", "actor_sim_id",
    "sim_instance_available", "queue_available", "running_available", "queued", "running",
))


class LiveObservationError(ValueError):
    """A malformed or oversized fixture, with a static non-input diagnostic."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _require(condition, code, message):
    if not condition:
        raise LiveObservationError(code, message)


def _check_plain_data(value):
    """Bound work and reject custom objects before any JSON encoding or copying."""
    nodes = 0
    active_containers = set()

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        _require(nodes <= MAX_NODES, "limit_exceeded", "Fixture has too many data nodes.")
        kind = type(item)
        if kind is dict or kind is list:
            depth += 1
            _require(depth <= MAX_DEPTH, "limit_exceeded", "Fixture nesting is too deep.")
            minimum_children = len(item) * (2 if kind is dict else 1)
            _require(minimum_children <= MAX_NODES - nodes, "limit_exceeded",
                     "Fixture container has too many entries.")
            identity = id(item)
            _require(identity not in active_containers, "invalid_shape",
                     "Cyclic fixture containers are forbidden.")
            active_containers.add(identity)
            if kind is dict:
                for key, child in item.items():
                    _require(type(key) is str, "invalid_shape",
                             "Fixture object keys must be plain strings.")
                    visit(key, depth)
                    visit(child, depth)
            else:
                for child in item:
                    visit(child, depth)
            active_containers.remove(identity)
        elif kind is str:
            _require(len(item) <= MAX_STRING_BYTES, "limit_exceeded",
                     "Fixture string exceeds its byte bound.")
            try:
                size = len(item.encode("utf-8", errors="strict"))
            except UnicodeError:
                raise LiveObservationError("invalid_value", "Fixture string is not valid UTF-8.") from None
            _require(size <= MAX_STRING_BYTES, "limit_exceeded",
                     "Fixture string exceeds its byte bound.")
        elif kind is int:
            _require(0 <= item <= MAX_UINT64, "invalid_value",
                     "Fixture integer is outside the unsigned 64-bit range.")
        else:
            _require(item is None or kind is bool, "invalid_shape",
                     "Only plain objects, arrays, strings, integers, booleans and null are accepted.")

    visit(value, 0)


def _check_encoded_size(value):
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf-8"))
        _require(size <= MAX_DATA_BYTES, "limit_exceeded",
                 "Fixture data exceeds the compact JSON byte bound.")


def _fields(value, required, optional=frozenset()):
    _require(type(value) is dict, "invalid_shape", "Expected a plain fixture object.")
    keys = value.keys()
    _require(required <= keys and keys <= required | optional, "invalid_shape",
             "Fixture object does not have the declared fields.")


def _identifier(value):
    _require(type(value) is str and _IDENTIFIER.fullmatch(value) is not None
             and int(value) <= MAX_UINT64, "invalid_value",
             "Identifiers must be canonical positive uint64 decimal strings.")
    return value


def _fixture_label(value):
    _require(type(value) is str and len(value) <= MAX_LABEL_CHARS
             and _FIXTURE_LABEL.fullmatch(value) is not None, "invalid_value",
             "Fixture labels must be bounded ASCII label strings.")
    return value


def _boolean(value):
    _require(type(value) is bool, "invalid_value", "Availability must be an exact boolean.")
    return value


def _target(value):
    if value is None:
        return None
    _fields(value, frozenset(("kind", "id")))
    _require(type(value["kind"]) is str and value["kind"] in ("sim", "object"),
             "invalid_value", "Target kind must explicitly identify a Sim or object instance.")
    return {"kind": value["kind"], "id": _identifier(value["id"])}


def _channel(value, available):
    if not available:
        _require(value is None, "inconsistent_availability",
                 "Unavailable channels must have null rows, not an observed array.")
        return None
    _require(type(value) is list, "inconsistent_availability",
             "Available channels must have a plain observed array.")
    _require(len(value) <= MAX_ROWS, "limit_exceeded", "Fixture has too many interaction rows.")
    return value


def _rows(value, channel):
    if value is None:
        return None
    result = []
    for index, row in enumerate(value):
        _fields(row, frozenset(("tuning_guid64",)),
                frozenset(("runtime_instance_id", "target", "label")))
        runtime_id = row.get("runtime_instance_id")
        label = row.get("label")
        _require(label is None or type(label) is str and bool(label), "invalid_value",
                 "Interaction labels must be nonempty strings or unavailable.")
        result.append({
            "occurrence_handle": "{}:{}".format(channel, index),
            "tuning_guid64": _identifier(row["tuning_guid64"]),
            "runtime_instance_id": None if runtime_id is None else _identifier(runtime_id),
            "target": _target(row.get("target")),
            "label": label,
        })
    return result


def validate_live_fixture(value):
    """Return a fresh bounded DTO, or raise ``LiveObservationError``.

    Input must be a plain dictionary, never JSON text or a reader. Optional row
    fields omitted or null remain unavailable. All caller-owned containers are
    detached; callers must not concurrently mutate input during validation.
    Repeated labels/IDs/rows are allowed and do not correlate separate snapshots.
    The returned dictionary is data, not an immutable object or trusted evidence.
    """
    _check_plain_data(value)
    _fields(value, _ROOT_FIELDS)
    _require(type(value["schema_version"]) is int and value["schema_version"] == SCHEMA_VERSION,
             "invalid_value", "Unsupported fixture schema version.")
    sim_available = _boolean(value["sim_instance_available"])
    queue_available = _boolean(value["queue_available"])
    running_available = _boolean(value["running_available"])
    _require(sim_available or not (queue_available or running_available),
             "inconsistent_availability", "An unavailable Sim cannot have available channels.")
    queued_input = _channel(value["queued"], queue_available)
    running_input = _channel(value["running"], running_available)
    _require(len(queued_input or ()) + len(running_input or ()) <= MAX_ROWS, "limit_exceeded",
             "Fixture has too many interaction rows across both channels.")
    result = {
        "schema_version": SCHEMA_VERSION,
        "fixture_observation_id": _fixture_label(value["fixture_observation_id"]),
        "fixture_epoch": _fixture_label(value["fixture_epoch"]),
        "actor_sim_id": _identifier(value["actor_sim_id"]),
        "sim_instance_available": sim_available,
        "queue_available": queue_available,
        "running_available": running_available,
        "queued": _rows(queued_input, "queued"),
        "running": _rows(running_input, "running"),
        "source": "offline_fixture",
        "atomic_snapshot": False,
        "live_qualified": False,
        "independent_evidence": False,
        "action_outcome": "unknown",
        "occurrence_handle_scope": "this_snapshot_only",
    }
    _check_encoded_size(value)
    _check_encoded_size(result)
    return result
