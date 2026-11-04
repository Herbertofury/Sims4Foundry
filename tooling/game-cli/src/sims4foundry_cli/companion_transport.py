"""Opt-in schema-2 client contract for the uninstalled companion transport.

The unchanged v1 request is nested inside an explicit TTL/context envelope. No
CLI command enables this module. Nothing authenticates itself from loopback, PID,
session or a digest. exchange_once defaults denied and requires an independently
established, bounded server authorization hook. No credentials/provider ship here.

Send one frame then write EOF. Read one reply then EOF. One absolute deadline,
no retries, no auto-reconnect, no mutation recovery. After ANY uncertain exchange,
use a ReceiptRead for the exact original envelope, never resubmit/replace it.
"""

from dataclasses import dataclass
import hashlib
import re
import socket
import struct
from time import monotonic

from . import companion_protocol as v1

TRANSPORT_VERSION = 2


def _require(condition, code, message):
    if not condition:
        raise v1.ProtocolError(code, message)


def _object(value, fields):
    _require(type(value) is dict and value.keys() == set(fields),
             "invalid_envelope", "Expected exactly the transport envelope fields.")


@dataclass(frozen=True)
class ExpectedContext:
    zone_id: str
    household_id: str
    save_slot: int
    save_guid: str

    def __post_init__(self):
        def decimal(value, zero=False):
            return (type(value) is str and
                    ((zero and value == "0") or re.fullmatch(r"[1-9][0-9]{0,19}", value))
                    and int(value) <= v1.MAX_INTEGER)
        _require(decimal(self.zone_id) and decimal(self.household_id)
                 and type(self.save_slot) is int and 0 <= self.save_slot <= v1.MAX_INTEGER
                 and decimal(self.save_guid, zero=True),
                 "invalid_context", "Exact zone/household/save identity is required.")

    def to_wire(self):
        self.__post_init__()
        return {"zone_id": self.zone_id, "household_id": self.household_id,
                "save_slot": self.save_slot, "save_guid": self.save_guid}


@dataclass(frozen=True)
class Submission:
    request: v1.Request
    ttl_seconds: float = 5.0
    expected_context: ExpectedContext | None = None

    def __post_init__(self):
        _require(type(self.request) is v1.Request, "invalid_request", "Expected unchanged typed v1 request.")
        v1.parse_request(self.request.to_wire(), session_id=self.request.session_id,
                         process_epoch=self.request.process_epoch)
        _require(type(self.ttl_seconds) in (int, float) and 0 < self.ttl_seconds <= 30,
                 "invalid_ttl", "TTL must be finite, positive and at most thirty seconds.")
        if self.request.operation is v1.Operation.OBSERVE:
            _require(self.expected_context is None, "invalid_context", "Observation has no mutation context.")
        else:
            _require(type(self.expected_context) is ExpectedContext,
                     "invalid_context", "Mutations require all explicit context fields.")
            self.expected_context.__post_init__()

    def to_wire(self):
        self.__post_init__()
        return {"schema": TRANSPORT_VERSION, "kind": "submit", "request": self.request.to_wire(),
                "ttl_seconds": self.ttl_seconds,
                "expected_context": self.expected_context.to_wire() if self.expected_context else None}

    @property
    def request_sha256(self):
        return hashlib.sha256(v1.encode_frame(self.to_wire())[4:]).hexdigest()


@dataclass(frozen=True)
class ReceiptRead:
    """Read-only query bound to the full original immutable submission."""
    submission: Submission

    def to_wire(self):
        _require(type(self.submission) is Submission, "invalid_request", "Expected original submission.")
        request = self.submission.request
        return {"schema": TRANSPORT_VERSION, "kind": "receipt_read", "request_id": request.request_id,
                "session_id": request.session_id, "process_epoch": request.process_epoch.to_wire(),
                "request_sha256": self.submission.request_sha256}


def parse_receipt(value: dict, submission: Submission) -> dict:
    """Correlate adapter claims; never produce v1 VERIFIED/evidence records."""
    _require(type(submission) is Submission, "invalid_request", "Expected original submission.")
    # Reapply strict plain-value bounds for direct callers as well as sockets.
    v1.encode_frame(value)
    if value.get("kind") == "error":
        _object(value, {"schema", "kind", "code", "outcome_known"})
        _require(type(value["schema"]) is int and value["schema"] == TRANSPORT_VERSION
                 and value["outcome_known"] is False and type(value["code"]) is str
                 and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value["code"]),
                 "invalid_response", "Malformed transport error.")
        raise v1.ProtocolError(value["code"], "Exchange failed; do not infer dispatch or retry.")
    _object(value, {"schema", "kind", "request_id", "session_id", "process_epoch",
                    "request_sha256", "state", "reason", "adapter_result", "independent_evidence"})
    request = submission.request
    _require(type(value["schema"]) is int and value["schema"] == TRANSPORT_VERSION
             and value["kind"] == "receipt" and value["independent_evidence"] is False,
             "invalid_response", "Receipt cannot claim independent evidence.")
    _object(value["process_epoch"], {"pid", "creation_filetime"})
    epoch = v1.ProcessEpoch(**value["process_epoch"])
    _require((value["request_id"], value["session_id"], epoch, value["request_sha256"])
             == (request.request_id, request.session_id, request.process_epoch, submission.request_sha256),
             "response_mismatch", "Receipt does not bind exact submission and process lifetime.")
    state, reason, result = value["state"], value["reason"], value["adapter_result"]
    _require(type(state) is str, "invalid_response", "Invalid receipt state.")
    if state in ("accepted", "dispatched"):
        valid = reason is None and result is None
    elif state == "adapter_returned":
        valid = (reason is None and type(result) is dict and type(result.get("outcome")) is str
                 and result["outcome"] in ("verified", "unverified"))
    elif state in ("refused", "unknown", "not_found"):
        valid = (result is None and type(reason) is str
                 and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason)
                 and (state != "not_found" or reason == "history_missing"))
    else:
        valid = False
    _require(valid, "invalid_response", "Adapter assertions are not independent game verification.")
    return value


def exchange_once(port: int, message: Submission | ReceiptRead, *, authorize_peer=None,
                  timeout_seconds: float = 2.0) -> dict:
    """One bounded exchange. The hook must authenticate the server before send.

    The hook receives (socket, absolute_monotonic_deadline, original_submission)
    and must return exact True. No production hook is supplied. Hook blocking
    cannot be preempted by Python; the trusted host must honor the deadline.
    An error, timeout or missing reply is uncertain, never an automatic retry.
    """
    _require(type(port) is int and 1 <= port <= 65535, "invalid_port", "Expected an explicit bound port.")
    _require(type(timeout_seconds) in (int, float) and 0 < timeout_seconds <= 5,
             "invalid_timeout", "Timeout must be positive and at most five seconds.")
    _require(type(message) in (Submission, ReceiptRead), "invalid_request", "Use a typed submission or receipt read.")
    _require(callable(authorize_peer), "host_policy_required", "Independent server authorization is absent.")
    submission = message if type(message) is Submission else message.submission
    frame = v1.encode_frame(message.to_wire())
    deadline = monotonic() + timeout_seconds
    def remaining():
        value = deadline - monotonic()
        _require(value > 0, "connection_deadline", "Absolute exchange deadline elapsed; do not retry.")
        return value
    def read_exact(connection, count):
        result = bytearray()
        while len(result) < count:
            connection.settimeout(remaining())
            chunk = connection.recv(count - len(result))
            _require(bool(chunk), "truncated_frame", "Missing reply bytes; outcome remains uncertain.")
            result.extend(chunk)
        return bytes(result)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining())
        connection.connect(("127.0.0.1", port))
        _require(authorize_peer(connection, deadline, submission) is True,
                 "peer_denied", "Server identity was not independently authorized.")
        connection.settimeout(remaining())
        connection.sendall(frame)
        connection.shutdown(socket.SHUT_WR)
        size = struct.unpack(">I", read_exact(connection, 4))[0]
        _require(1 <= size <= v1.MAX_FRAME_BYTES, "invalid_frame", "Invalid reply frame size.")
        payload = read_exact(connection, size)
        connection.settimeout(remaining())
        _require(not connection.recv(1), "extra_frame", "Only one response is allowed.")
        return parse_receipt(v1.decode_payload(payload), submission)
