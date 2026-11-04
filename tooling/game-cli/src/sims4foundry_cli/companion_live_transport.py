"""Opt-in, default-denied schema-3 Live read exchange, separate from schema 2.

This source-only sender ships no CLI route, peer policy, key provisioning or game
provider. A digest binds intent; authentication alone grants no operation scope
and supplies no integrity-protected application channel or game qualification.

One connection, one application frame plus write EOF, one reply plus EOF, and one
absolute deadline. There is no retry, reconnect, downgrade or fallback. Any error
can be uncertain: durable callers must retain the original intent and use only
its receipt read afterward. The journal, not this stateless function, prevents
replay across calls.
"""
import hashlib
import socket
import struct
from time import monotonic

from . import companion_protocol as wire
from . import companion_live_protocol as live

_require = wire._require


def _submission(payload):
    """Reconstruct a detached view solely from the private canonical bytes."""
    value = wire.decode_payload(payload)
    return live.parse_live_submit(value, value['session_id'],
                                  wire.ProcessEpoch(**value['process_epoch']))


def _unchanged(view, payload):
    try:
        return wire.encode_frame(live.LiveQueueSubmission.to_wire(view))[4:] == payload
    except (wire.ProtocolError, AttributeError, TypeError, ValueError, KeyError):
        return False


def exchange_once(port: int, message: live.LiveQueueSubmission | live.LiveQueueReceiptRead,
                  *, authorize_peer=None, timeout_seconds: float = 2.0) -> dict:
    """Exchange exact schema-3 intent once after an explicit peer policy.

    The hook receives (socket, absolute deadline, detached original submission)
    and must return exact True without changing intent. It cannot reach the
    separately reconstructed receipt-validation view through its arguments.
    Trusted hook blocking cannot be preempted by Python; it must honor the bound.
    This protects supplied aliases, not hostile code in the same interpreter.
    """
    _require(type(port) is int and 1 <= port <= 65535,
             'invalid_port', 'Expected an explicit bound port.')
    _require(type(timeout_seconds) in (int, float) and 0 < timeout_seconds <= 5,
             'invalid_timeout', 'Timeout must be finite, positive and at most five seconds.')
    _require(type(message) in (live.LiveQueueSubmission, live.LiveQueueReceiptRead),
             'invalid_request', 'Use an exact typed Live submission or receipt read.')
    _require(callable(authorize_peer), 'host_policy_required', 'Independent server authorization is absent.')
    original = message if type(message) is live.LiveQueueSubmission else message.submission
    # Class dispatch, exact nested types and the strict codec capture every field
    # before any callback or I/O. Numeric TTL spelling is part of these bytes.
    payload = wire.encode_frame(live.LiveQueueSubmission.to_wire(original))[4:]
    if type(message) is live.LiveQueueSubmission:
        frame = struct.pack('>I', len(payload)) + payload
    else:
        value = wire.decode_payload(payload)
        frame = wire.encode_frame(dict(schema=3, kind='receipt_read',
            request_id=value['request_id'], session_id=value['session_id'],
            process_epoch=value['process_epoch'], request_sha256=hashlib.sha256(payload).hexdigest()))
    authorization_view = _submission(payload)
    deadline = monotonic() + timeout_seconds

    def remaining():
        seconds = deadline - monotonic()
        _require(seconds > 0, 'connection_deadline', 'Absolute exchange deadline elapsed; do not retry.')
        return seconds

    def read_exact(connection, count):
        result = bytearray()
        while len(result) < count:
            connection.settimeout(remaining())
            chunk = connection.recv(count - len(result))
            remaining()
            _require(bool(chunk), 'truncated_frame', 'Missing reply bytes; outcome remains uncertain.')
            result.extend(chunk)
        return bytes(result)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining())
        connection.connect(('127.0.0.1', port))
        remaining()
        allowed = authorize_peer(connection, deadline, authorization_view)
        remaining()
        _require(allowed is True, 'peer_denied', 'Server identity was not independently authorized.')
        _require(_unchanged(authorization_view, payload),
                 'peer_intent_changed', 'Peer policy changed the original Live intent.')
        connection.settimeout(remaining())
        connection.sendall(frame)
        remaining()
        connection.settimeout(remaining())
        connection.shutdown(socket.SHUT_WR)
        remaining()
        size = struct.unpack('>I', read_exact(connection, 4))[0]
        _require(1 <= size <= wire.MAX_FRAME_BYTES, 'invalid_frame', 'Invalid reply frame size.')
        response = read_exact(connection, size)
        connection.settimeout(remaining())
        extra = connection.recv(1)
        remaining()
        _require(not extra, 'extra_frame', 'Only one response is allowed.')
        value = wire.decode_payload(response)
        remaining()
        parse_view = _submission(payload)
        remaining()
        result = live.parse_live_receipt(value, parse_view)
        remaining()
        return result
