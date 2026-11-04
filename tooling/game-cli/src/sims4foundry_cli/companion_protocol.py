"""Offline schema-1 wire contract for an unqualified Foundry companion.

The separate companion_transport module wraps this unchanged schema in an
explicit schema-2 TTL/context envelope; v1 alone is not queue admission.

There is no transport, listener, authentication, permission check, game API,
dispatch queue, credential generation, or persistent journal in this module.
Valid JSON, an allowlisted operation, and an ``accepted`` response do NOT
authorize execution. A future integration must independently authenticate its
peer, bind observed process/session identity, apply permissions and qualification,
and durably record intent before any game-facing dispatch.

Wire format: four unsigned big-endian bytes give the UTF-8 JSON object length,
excluding the prefix. Payloads are 1..65,536 bytes. Each ``feed`` accepts at most
65,540 bytes; split larger transport reads. Fragmentation and coalescing are both
supported. Malformed input poisons a decoder rather than attempting resync. A
failed feed returns no frames, including valid prefixes in that particular feed.

Request/session IDs are lowercase 32-digit hex strings, matching the client
journal's ID shape. Process epochs contain PID and creation FILETIME, not PID
alone. These are declarations, not proof of identity. A future integration must
observe and bind them externally and use a fresh session after companion restart
or loss of deduplication history, even if the game process stays alive.

Replay classification is pure and consumes caller-supplied records. It neither
stores records nor recreates the existing journal engine. ``first_seen`` means
only that supplied complete history has no matching record, never permission to
dispatch. Identical pending/unknown requests require read-only reconciliation;
terminal records may be returned without another dispatch. Missing/incomplete
history defaults to reconciliation. An in-memory cache cannot guarantee
at-most-once behavior across crashes. Do not replace an uncertain request with a
new request ID or new session to try the action again.

This is Python 3.11+ *client* code. The future embedded game Python version and
API compatibility remain uncertain pending source research and qualification;
this file is not represented as an installable Sims script mod.
"""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
import struct


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


class FrameDecoder:
    """Incremental, bounded decoder; use a new instance after failure or EOF.

    Buffered input is at most ``max_frame_bytes`` (or the four-byte prefix).
    One feed's input and returned tuple are bounded by MAX_FEED_BYTES. No bytes
    are interpreted as code. Instances are single-consumer and not thread-safe.
    """

    def __init__(self, *, max_frame_bytes: int = MAX_FRAME_BYTES):
        _require(_integer(max_frame_bytes, 1, MAX_FRAME_BYTES), "invalid_bound",
                 "Frame limit must be an integer from 1 through 65,536.")
        self._max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()
        self._expected = None
        self._failed = False
        self._closed = False

    @property
    def buffered_bytes(self):
        return len(self._buffer)

    @property
    def max_frame_bytes(self):
        return self._max_frame_bytes

    @property
    def failed(self):
        return self._failed

    def _open(self):
        _require(not self._failed, "decoder_failed", "Decoder is poisoned; do not resynchronize it.")
        _require(not self._closed, "decoder_closed", "Decoder has reached EOF.")

    def feed(self, chunk: bytes) -> tuple[dict, ...]:
        self._open()
        try:
            _require(type(chunk) in (bytes, bytearray), "invalid_frame", "Feed bytes or bytearray.")
            _require(len(chunk) <= MAX_FEED_BYTES, "oversized_feed", "Split input into bounded chunks.")
            source = memoryview(chunk)
            offset = 0
            result = []
            while offset < len(source):
                target = 4 if self._expected is None else self._expected
                take = min(target - len(self._buffer), len(source) - offset)
                self._buffer.extend(source[offset:offset + take])
                offset += take
                if len(self._buffer) < target:
                    break
                if self._expected is None:
                    self._expected = struct.unpack(">I", self._buffer)[0]
                    self._buffer.clear()
                    _require(1 <= self._expected <= self.max_frame_bytes, "invalid_frame",
                             "Declared payload length is zero or exceeds the bound.")
                else:
                    result.append(decode_payload(bytes(self._buffer)))
                    self._buffer.clear()
                    self._expected = None
            return tuple(result)
        except ProtocolError:
            self._failed = True
            self._buffer.clear()
            self._expected = None
            raise

    def finish(self) -> None:
        """Declare transport EOF. An incomplete prefix or payload is an error."""
        self._open()
        if self._buffer or self._expected is not None:
            self._failed = True
            self._buffer.clear()
            self._expected = None
            raise ProtocolError("truncated_frame", "EOF interrupted a frame; its outcome is not inferred.")
        self._closed = True


@dataclass(frozen=True)
class ProcessEpoch:
    pid: int
    creation_filetime: int

    def __post_init__(self):
        _require(_integer(self.pid, 1, 2**32 - 1)
                 and _integer(self.creation_filetime, 1, MAX_INTEGER),
                 "invalid_epoch", "Expected a PID and positive process creation FILETIME.")

    def to_wire(self):
        return {"pid": self.pid, "creation_filetime": self.creation_filetime}


def _epoch(value):
    _object(value, {"pid", "creation_filetime"}, "invalid_epoch")
    return ProcessEpoch(value["pid"], value["creation_filetime"])


class Operation(str, Enum):
    OBSERVE = "observe"
    SET_SPEED = "live.set_speed"
    ENTER_CAS = "cas.enter"
    REQUEST_EXIT_MENU = "game.request_exit_menu"


@dataclass(frozen=True)
class ObserveArguments:
    def to_wire(self):
        return {}


@dataclass(frozen=True)
class SetSpeedArguments:
    speed: int

    def __post_init__(self):
        _require(_integer(self.speed, 0, 3), "invalid_arguments", "Speed must be an integer from 0 through 3.")

    def to_wire(self):
        return {"speed": self.speed}


@dataclass(frozen=True)
class EnterCasArguments:
    """Exact positive uint64 SimInfo ID, decimal text to avoid JSON rounding.

    Symbolic lookup belongs before request creation. Leading zeroes, signs,
    whitespace and numeric JSON values are not canonical identifiers.
    """

    sim_id: str

    def __post_init__(self):
        _require(_matches(self.sim_id, _SIM_ID) and int(self.sim_id) <= MAX_INTEGER,
                 "invalid_arguments", "Expected a canonical positive uint64 Sim ID as decimal text.")

    def to_wire(self):
        return {"sim_id": self.sim_id}


@dataclass(frozen=True)
class RequestExitMenuArguments:
    """Request only the menu. No save/discard choice, confirmation, or quit."""

    def to_wire(self):
        return {}


_ARGUMENT_TYPES = {
    Operation.OBSERVE: (ObserveArguments, set()),
    Operation.SET_SPEED: (SetSpeedArguments, {"speed"}),
    Operation.ENTER_CAS: (EnterCasArguments, {"sim_id"}),
    Operation.REQUEST_EXIT_MENU: (RequestExitMenuArguments, set()),
}


@dataclass(frozen=True)
class Request:
    request_id: str
    session_id: str
    process_epoch: ProcessEpoch
    operation: Operation
    arguments: ObserveArguments | SetSpeedArguments | EnterCasArguments | RequestExitMenuArguments

    def __post_init__(self):
        _identity(self.request_id, self.session_id, self.process_epoch)
        _require(type(self.operation) is Operation, "invalid_operation", "Use a listed typed operation.")
        _require(type(self.arguments) is _ARGUMENT_TYPES[self.operation][0], "invalid_arguments",
                 "Arguments must match the selected operation exactly.")

    def to_wire(self):
        return {"schema": PROTOCOL_VERSION, "kind": "request", "request_id": self.request_id,
                "session_id": self.session_id, "process_epoch": self.process_epoch.to_wire(),
                "operation": self.operation.value, "arguments": self.arguments.to_wire()}


def _identity(request_id, session_id, process_epoch):
    _require(_matches(request_id, _ID) and _matches(session_id, _ID), "invalid_identity",
             "Request and session IDs must be lowercase 32-digit hex strings.")
    _require(type(process_epoch) is ProcessEpoch, "invalid_epoch", "Expected a typed process epoch.")


def _envelope(value, fields, kind):
    _object(value, fields, "invalid_envelope")
    _require(type(value["schema"]) is int and value["schema"] == PROTOCOL_VERSION
             and type(value["kind"]) is str and value["kind"] == kind,
             "invalid_envelope", "Unsupported protocol version or envelope kind.")


def validate_context(request: Request, *, session_id: str, process_epoch: ProcessEpoch) -> None:
    """Compare with externally established context, not untrusted peer claims."""
    _require(type(request) is Request, "invalid_request", "Expected a typed request.")
    _identity(request.request_id, session_id, process_epoch)
    _require(request.session_id == session_id and request.process_epoch == process_epoch,
             "context_mismatch", "Session or process lifetime changed; do not dispatch or replay.")


def parse_request(value: dict, *, session_id: str, process_epoch: ProcessEpoch) -> Request:
    """Parse the exact allowlist and bind externally supplied expected context."""
    _envelope(value, {"schema", "kind", "request_id", "session_id", "process_epoch",
                      "operation", "arguments"}, "request")
    _require(type(value["operation"]) is str, "invalid_operation", "Expected an operation name.")
    try:
        operation = Operation(value["operation"])
    except ValueError as exc:
        raise ProtocolError("invalid_operation", "No generic command, code, or input relay exists.") from exc
    argument_type, fields = _ARGUMENT_TYPES[operation]
    _object(value["arguments"], fields, "invalid_arguments")
    request = Request(value["request_id"], value["session_id"], _epoch(value["process_epoch"]),
                      operation, argument_type(**value["arguments"]))
    validate_context(request, session_id=session_id, process_epoch=process_epoch)
    return request


def request_fingerprint(request: Request) -> str:
    """Consistency digest of the complete envelope, not a signature or token."""
    _require(type(request) is Request, "invalid_request", "Expected a typed request.")
    return hashlib.sha256(_payload(request.to_wire())).hexdigest()


class ResponseState(str, Enum):
    ACCEPTED = "accepted"        # Received/recorded; not dispatched or completed.
    DISPATCHED = "dispatched"    # Attempt began; not a verified postcondition.
    VERIFIED = "verified"        # External provider claims referenced evidence.
    REFUSED = "refused"          # Known not dispatched.
    UNKNOWN = "unknown"          # Outcome uncertain; never automatic retry.


@dataclass(frozen=True)
class EvidenceReference:
    """Opaque reference only; this module cannot validate observation contents."""

    observation_id: str
    observation_sha256: str

    def __post_init__(self):
        _require(_matches(self.observation_id, _ID) and _matches(self.observation_sha256, _SHA256),
                 "invalid_evidence", "Expected an observation ID and content digest.")

    def to_wire(self):
        return {"observation_id": self.observation_id, "observation_sha256": self.observation_sha256}


@dataclass(frozen=True)
class Response:
    request_id: str
    session_id: str
    process_epoch: ProcessEpoch
    request_sha256: str
    state: ResponseState
    evidence: EvidenceReference | None = None
    reason: str | None = None

    def __post_init__(self):
        _identity(self.request_id, self.session_id, self.process_epoch)
        _require(_matches(self.request_sha256, _SHA256), "invalid_response", "Expected a request digest.")
        _require(type(self.state) is ResponseState, "invalid_response", "Expected a listed response state.")
        if self.state is ResponseState.VERIFIED:
            _require(type(self.evidence) is EvidenceReference and self.reason is None,
                     "invalid_response", "Verified requires an evidence reference and no refusal reason.")
        elif self.state in (ResponseState.REFUSED, ResponseState.UNKNOWN):
            _require(self.evidence is None and _matches(self.reason, _REASON), "invalid_response",
                     "Refused/unknown require a bounded reason code, not completion evidence.")
        else:
            _require(self.evidence is None and self.reason is None, "invalid_response",
                     "Acceptance/dispatch must not claim completion evidence or refusal.")

    def to_wire(self):
        return {"schema": PROTOCOL_VERSION, "kind": "response", "request_id": self.request_id,
                "session_id": self.session_id, "process_epoch": self.process_epoch.to_wire(),
                "request_sha256": self.request_sha256, "state": self.state.value,
                "evidence": self.evidence.to_wire() if self.evidence is not None else None,
                "reason": self.reason}


def response_for(request: Request, state: ResponseState, *, evidence=None, reason=None) -> Response:
    """Construct a claim for offline handling; does not establish its truth."""
    digest = request_fingerprint(request)
    return Response(request.request_id, request.session_id, request.process_epoch,
                    digest, state, evidence, reason)


def _bind_response(response, request):
    _require(type(response) is Response and type(request) is Request,
             "invalid_response", "Expected a typed response and request.")
    _require((response.request_id, response.session_id, response.process_epoch, response.request_sha256)
             == (request.request_id, request.session_id, request.process_epoch, request_fingerprint(request)),
             "response_mismatch", "Response does not bind the exact request and context.")


def parse_response(value: dict, *, request: Request) -> Response:
    """Parse and correlate a response; referenced evidence still needs review."""
    _envelope(value, {"schema", "kind", "request_id", "session_id", "process_epoch",
                      "request_sha256", "state", "evidence", "reason"}, "response")
    _require(type(value["state"]) is str, "invalid_response", "Expected a response state name.")
    try:
        state = ResponseState(value["state"])
    except ValueError as exc:
        raise ProtocolError("invalid_response", "Unknown response state.") from exc
    evidence = value["evidence"]
    if evidence is not None:
        _object(evidence, {"observation_id", "observation_sha256"}, "invalid_evidence")
        evidence = EvidenceReference(**evidence)
    response = Response(value["request_id"], value["session_id"], _epoch(value["process_epoch"]),
                        value["request_sha256"], state, evidence, value["reason"])
    _bind_response(response, request)
    return response


def validate_response_transition(previous: Response, current: Response) -> None:
    """Check claimed progress, without verifying dispatch or evidence.

    Terminal records are immutable. Unknown may become verified only after
    external read-only reconciliation. A dispatch can never become a refusal.
    """
    _require(type(previous) is Response and type(current) is Response,
             "invalid_response", "Expected typed responses.")
    _require((previous.request_id, previous.session_id, previous.process_epoch, previous.request_sha256)
             == (current.request_id, current.session_id, current.process_epoch, current.request_sha256),
             "response_mismatch", "A transition cannot change request binding.")
    if previous == current:
        return
    allowed = {
        ResponseState.ACCEPTED: {ResponseState.DISPATCHED, ResponseState.REFUSED, ResponseState.UNKNOWN},
        ResponseState.DISPATCHED: {ResponseState.VERIFIED, ResponseState.UNKNOWN},
        ResponseState.UNKNOWN: {ResponseState.VERIFIED},
        ResponseState.VERIFIED: set(),
        ResponseState.REFUSED: set(),
    }
    _require(current.state in allowed[previous.state], "invalid_transition",
             "Response regressed, rewrote a record, or conflated dispatch with verification.")


class ReplayDisposition(str, Enum):
    FIRST_SEEN = "first_seen"
    RETURN_RECORDED = "return_recorded"
    RECONCILE_ONLY = "reconcile_only"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class ReplayDecision:
    disposition: ReplayDisposition
    recorded_response: Response | None = None


def classify_replay(request: Request, *, previous_request: Request | None = None,
                    previous_response: Response | None = None,
                    history_complete: bool = False) -> ReplayDecision:
    """Classify supplied history without storage, execution, or permission.

    ``history_complete`` must come from a trusted journal integration, never a
    wire field. False includes restart, lost cache, eviction, and unreadable
    history. No result of this function is itself an execution authorization.
    """
    _require(type(request) is Request and type(history_complete) is bool,
             "invalid_history", "Expected a typed request and explicit history status.")
    if previous_request is None:
        _require(previous_response is None, "invalid_history", "A response without its request is insufficient history.")
        return ReplayDecision(ReplayDisposition.FIRST_SEEN if history_complete else ReplayDisposition.RECONCILE_ONLY)
    _require(type(previous_request) is Request, "invalid_history", "Expected a typed historical request.")
    if previous_response is not None:
        _bind_response(previous_response, previous_request)
    if request != previous_request:
        return ReplayDecision(ReplayDisposition.CONFLICT)
    if previous_response is not None and previous_response.state in (ResponseState.VERIFIED, ResponseState.REFUSED):
        return ReplayDecision(ReplayDisposition.RETURN_RECORDED, previous_response)
    return ReplayDecision(ReplayDisposition.RECONCILE_ONLY, previous_response)
