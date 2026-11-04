"""Strict Python-3.7 wire codec for the uninstalled, default-denied transport.

Schema 2 explicitly wraps the unchanged schema 1 request with TTL and save
preconditions. Schema 1 alone is NOT accepted for queue admission. Framing and
strict JSON limits deliberately match the existing client codec; cross-runtime
fixtures check both. Digests correlate immutable payloads, never authenticate.
"""

from dataclasses import dataclass
import hashlib
import json
import math
import re
import struct
from .queue import (Request, ProcessEpoch, ObserveArguments, SetSpeedArguments,
                    EnterCasArguments, RequestExitMenuArguments, QueueError)

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 64 * 1024
MAX_FEED_BYTES = MAX_FRAME_BYTES + 4
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 8192
MAX_INTEGER = 2**64 - 1

_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SIM_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class ProtocolError(ValueError):
    """Malformed, mismatched, or unsafe-to-reinterpret protocol data."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require(condition, code, message):
    if not condition:
        raise ProtocolError(code, message)


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _matches(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _object(value, fields, code):
    _require(type(value) is dict and value.keys() == set(fields), code,
             "Expected an object with exactly the declared fields.")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "invalid_json", "Duplicate JSON keys are forbidden.")
        result[key] = value
    return result


def _parse_float(text):
    value = float(text)
    _require(math.isfinite(value), "invalid_json", "Nonfinite numbers are forbidden.")
    return value


def _parse_integer(text):
    _require(len(text.lstrip("-")) <= 20, "invalid_json", "JSON integer is too large.")
    value = int(text)
    _require(-MAX_INTEGER <= value <= MAX_INTEGER, "invalid_json", "JSON integer is too large.")
    return value


def _no_constant(_):
    raise ProtocolError("invalid_json", "Nonfinite numbers are forbidden.")


def _check_nesting(text):
    # Count structural nesting before invoking the recursive JSON parser. Quoted
    # brackets and escaped quotes do not count; json.loads checks syntax itself.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            _require(depth <= MAX_JSON_DEPTH, "invalid_json", "JSON nesting exceeds the bound.")
        elif char in "]}":
            depth -= 1


def _check_tree(value):
    _require(type(value) is dict, "invalid_json", "A frame must contain a JSON object.")
    count = string_bytes = 0

    def visit(item, container_depth):
        nonlocal count, string_bytes
        count += 1
        _require(count <= MAX_JSON_NODES, "invalid_json", "JSON node count exceeds the bound.")
        kind = type(item)
        if kind in (dict, list):
            container_depth += 1
            _require(container_depth <= MAX_JSON_DEPTH, "invalid_json", "JSON nesting exceeds the bound.")
            for key in item if kind is dict else ():
                _require(type(key) is str, "invalid_json", "JSON object keys must be strings.")
                visit(key, container_depth)
            for child in item.values() if kind is dict else item:
                visit(child, container_depth)
        elif kind is str:
            _require(len(item) <= MAX_FRAME_BYTES, "oversized_frame", "JSON string exceeds the frame bound.")
            try:
                string_bytes += len(item.encode("utf-8", errors="strict"))
            except UnicodeError as exc:
                raise ProtocolError("invalid_json", "Unpaired Unicode surrogates are forbidden.") from exc
            _require(string_bytes <= MAX_FRAME_BYTES, "oversized_frame", "JSON strings exceed the frame bound.")
        elif kind is int:
            _require(-MAX_INTEGER <= item <= MAX_INTEGER, "invalid_json", "JSON integer is too large.")
        elif kind is float:
            _require(math.isfinite(item), "invalid_json", "Nonfinite numbers are forbidden.")
        else:
            _require(item is None or kind is bool, "invalid_json", "Only plain JSON values are accepted.")

    visit(value, 0)


def _payload(value):
    _check_tree(value)
    result = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True,
                               separators=(",", ":"))
    # Bounded accumulation, rather than constructing an unbounded JSON string
    # and only checking its size afterwards.
    for chunk in encoder.iterencode(value):
        raw = chunk.encode("utf-8")
        _require(len(result) + len(raw) <= MAX_FRAME_BYTES, "oversized_frame",
                 "JSON payload exceeds 64 KiB.")
        result.extend(raw)
    return bytes(result)


def encode_frame(value: dict) -> bytes:
    """Encode a bounded plain JSON object, not necessarily a valid envelope."""
    payload = _payload(value)
    return struct.pack(">I", len(payload)) + payload


def decode_payload(payload: bytes) -> dict:
    """Decode one exact payload without a prefix or any envelope authorization."""
    _require(type(payload) is bytes, "invalid_frame", "Payload must be bytes.")
    _require(1 <= len(payload) <= MAX_FRAME_BYTES, "invalid_frame", "Invalid payload length.")
    try:
        text = payload.decode("utf-8", errors="strict")
        _check_nesting(text)
        value = json.loads(text, object_pairs_hook=_unique_pairs, parse_float=_parse_float,
                           parse_int=_parse_integer, parse_constant=_no_constant)
        _check_tree(value)
        return value
    except ProtocolError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("invalid_json", "Expected one strict UTF-8 JSON object.") from exc



TRANSPORT_VERSION = 2


def _epoch(value):
    _object(value, {"pid", "creation_filetime"}, "invalid_epoch")
    try:
        return ProcessEpoch(value["pid"], value["creation_filetime"])
    except QueueError as error:
        raise ProtocolError(error.code, "Invalid process lifetime.")


def epoch_wire(epoch):
    return {"pid": epoch.pid, "creation_filetime": epoch.creation_filetime}


def _bound(value, session_id, process_epoch):
    _require(_matches(value["request_id"], _ID)
             and _matches(value["session_id"], _ID), "invalid_identity", "Invalid IDs.")
    _require(value["session_id"] == session_id
             and _epoch(value["process_epoch"]) == process_epoch,
             "context_mismatch", "Wrong session or process lifetime.")


@dataclass(frozen=True)
class Submission:
    request: Request
    request_sha256: str
    canonical_payload: bytes


def parse_submit(value, session_id, process_epoch):
    """Validate all fields before constructing an immutable queue request."""
    _check_tree(value)
    _object(value, {"schema", "kind", "request", "ttl_seconds", "expected_context"},
            "invalid_envelope")
    _require(type(value["schema"]) is int and value["schema"] == TRANSPORT_VERSION
             and value["kind"] == "submit", "invalid_envelope", "Expected transport schema 2 submit.")
    inner = value["request"]
    _object(inner, {"schema", "kind", "request_id", "session_id", "process_epoch",
                    "operation", "arguments"}, "invalid_envelope")
    _require(type(inner["schema"]) is int and inner["schema"] == 1
             and inner["kind"] == "request", "invalid_envelope", "Expected unchanged schema 1 request.")
    _bound(inner, session_id, process_epoch)
    shapes = {"observe": (ObserveArguments, set()),
              "live.set_speed": (SetSpeedArguments, {"speed"}),
              "cas.enter": (EnterCasArguments, {"sim_id"}),
              "game.request_exit_menu": (RequestExitMenuArguments, set())}
    operation = inner["operation"]
    _require(type(operation) is str and operation in shapes,
             "invalid_operation", "No generic command, code or input relay exists.")
    shape, fields = shapes[operation]
    _object(inner["arguments"], fields, "invalid_arguments")
    context = value["expected_context"]
    if operation == "observe":
        _require(context is None, "invalid_context", "Observation has no mutation preconditions.")
        extra = {}
    else:
        _object(context, {"zone_id", "household_id", "save_slot", "save_guid"}, "invalid_context")
        # Null is never a shorthand for an omitted mutation/save precondition.
        _require(all(item is not None for item in context.values()),
                 "invalid_context", "Every mutation precondition is required.")
        extra = dict(expected_zone_id=context["zone_id"],
                     expected_household_id=context["household_id"],
                     expected_save_slot=context["save_slot"], expected_save_guid=context["save_guid"])
    try:
        request = Request(inner["request_id"], inner["session_id"], operation,
                          shape(**inner["arguments"]), ttl_seconds=value["ttl_seconds"],
                          process_epoch=process_epoch, **extra)
    except QueueError as error:
        raise ProtocolError(error.code, "Invalid typed queue request.")
    payload = _payload(value)
    return Submission(request, hashlib.sha256(payload).hexdigest(), payload)


def parse_receipt_read(value, session_id, process_epoch):
    """Read-only lookup, with exact immutable envelope digest correlation."""
    _check_tree(value)
    _object(value, {"schema", "kind", "request_id", "session_id", "process_epoch",
                    "request_sha256"}, "invalid_envelope")
    _require(type(value["schema"]) is int and value["schema"] == TRANSPORT_VERSION
             and value["kind"] == "receipt_read", "invalid_envelope", "Expected receipt_read.")
    _bound(value, session_id, process_epoch)
    _require(_matches(value["request_sha256"], _SHA256), "invalid_identity", "Invalid request digest.")
    return value["request_id"], value["request_sha256"]


def receipt(request_id, digest, session_id, process_epoch, snapshot=None):
    """Never turn an adapter assertion into independent gameplay evidence."""
    state, reason, adapter_result = "not_found", "history_missing", None
    if snapshot is not None:
        state = {"pending": "accepted", "started": "dispatched",
                 "verified": "adapter_returned", "unverified": "adapter_returned",
                 "refused": "refused", "unknown": "unknown"}[snapshot.state]
        reason = snapshot.reason
        def thaw(item):
            if hasattr(item, "items"):
                return {key: thaw(value) for key, value in item.items()}
            if type(item) is tuple:
                return [thaw(value) for value in item]
            return item
        adapter_result = thaw(snapshot.result)
    return {"schema": TRANSPORT_VERSION, "kind": "receipt", "request_id": request_id,
            "session_id": session_id, "process_epoch": epoch_wire(process_epoch),
            "request_sha256": digest, "state": state, "reason": reason,
            "adapter_result": adapter_result, "independent_evidence": False}
