"""Single-use process-bound peer authentication for the source-only senders.

This composes an already held process lease, exact connected-loopback ownership,
and possession of an explicitly supplied session key. It does not acquire a
lease, provision a key, open a socket, grant mutation permission, reconcile a
journal, select a profile, or qualify a game/provider. Importing it does no I/O.
"""

from __future__ import annotations

import math
import socket
from time import monotonic

from . import companion_auth as auth
from . import companion_live_protocol as live
from . import companion_protocol as wire
from . import companion_transport as transport
from . import loopback_owner
from . import process_lease as leases
from .errors import FoundryError


# Keep the real concrete socket type, not a duck-typed socket or caller wrapper.
# Tests patch this private binding with a fabricated socket; no API accepts one.
_SOCKET_TYPE = socket.socket
_MAX_DEADLINE_SECONDS = 5.0
_Submission = transport.Submission | live.LiveQueueSubmission


def _require(condition, code, message):
    if not condition:
        raise FoundryError(code, message)


def _remaining(deadline):
    _require(type(deadline) in (int, float) and math.isfinite(deadline),
             "process_peer_deadline", "Expected the sender's finite absolute monotonic deadline.")
    remaining = deadline - monotonic()
    _require(0 < remaining <= _MAX_DEADLINE_SECONDS,
             "process_peer_deadline", "The bounded exchange deadline elapsed or is invalid; do not retry.")
    return remaining


def _schema2_submission(submission):
    """Rebuild exact typed fields without calling supplied instance methods."""
    request = submission.request
    _require(type(request) is wire.Request, "invalid_request", "Expected an exact schema-1 request.")
    _require(type(request.process_epoch) is wire.ProcessEpoch,
             "invalid_epoch", "Expected an exact typed process lifetime.")
    epoch = wire.ProcessEpoch(request.process_epoch.pid, request.process_epoch.creation_filetime)
    argument_types = {
        wire.Operation.OBSERVE: wire.ObserveArguments,
        wire.Operation.SET_SPEED: wire.SetSpeedArguments,
        wire.Operation.ENTER_CAS: wire.EnterCasArguments,
        wire.Operation.REQUEST_EXIT_MENU: wire.RequestExitMenuArguments,
    }
    _require(type(request.operation) is wire.Operation,
             "invalid_operation", "Expected a listed typed operation.")
    argument_type = argument_types[request.operation]
    _require(type(request.arguments) is argument_type,
             "invalid_arguments", "Expected the operation's exact typed arguments.")
    value = dict(schema=1, kind="request", request_id=request.request_id,
                 session_id=request.session_id,
                 process_epoch=dict(pid=epoch.pid, creation_filetime=epoch.creation_filetime),
                 operation=request.operation.value,
                 arguments=argument_type.to_wire(request.arguments))
    # The strict codec rejects subclasses, excessive size, and nonplain values.
    value = wire.decode_payload(wire.encode_frame(value)[4:])
    detached_request = wire.parse_request(value, session_id=value["session_id"], process_epoch=epoch)
    context = submission.expected_context
    if context is not None:
        _require(type(context) is transport.ExpectedContext,
                 "invalid_context", "Expected the exact typed schema-2 context.")
        context = transport.ExpectedContext(context.zone_id, context.household_id,
                                            context.save_slot, context.save_guid)
    return transport.Submission(detached_request, submission.ttl_seconds, context)


def _snapshot(submission):
    """Return detached, revalidated typed intent and its exact canonical bytes."""
    _require(type(submission) in (transport.Submission, live.LiveQueueSubmission),
             "invalid_request", "Expected an exact schema-2 or schema-3 original submission.")
    try:
        if type(submission) is transport.Submission:
            detached = _schema2_submission(submission)
            payload = wire.encode_frame(transport.Submission.to_wire(detached))[4:]
        else:
            payload = wire.encode_frame(live.LiveQueueSubmission.to_wire(submission))[4:]
            value = wire.decode_payload(payload)
            detached = live.parse_live_submit(value, value["session_id"],
                                              wire.ProcessEpoch(**value["process_epoch"]))
        return detached, payload
    except (AttributeError, KeyError, TypeError):
        # Missing or corrupted fields must not invoke a caller fallback or expose
        # arbitrary object representations in diagnostics. Protocol errors pass on.
        raise FoundryError("invalid_request", "The typed submission is incomplete or malformed.") from None


def _epoch_from_observation(observation):
    _require(type(observation) is dict,
             "process_peer_identity", "The held lease returned no exact process observation.")
    pid, created = observation.get("pid"), observation.get("creation_filetime")
    _require(type(pid) is int and 1 <= pid <= 0xFFFFFFFF
             and type(created) is int and 1 <= created <= 0xFFFFFFFFFFFFFFFF,
             "process_peer_identity", "The held lease returned an invalid process lifetime.")
    return wire.ProcessEpoch(pid, created)


def _observe(lease):
    # Exact type plus class dispatch rejects substitutes and an instance's
    # shadowed observe method. This is not a hostile-Python sandbox.
    return _epoch_from_observation(leases._ProcessLease.observe(lease))


def _socket_snapshot(connection):
    _require(type(connection) is _SOCKET_TYPE,
             "process_peer_socket", "Expected the sender's actual connected socket.")
    _require(connection.family == socket.AF_INET and connection.type == socket.SOCK_STREAM,
             "process_peer_socket", "Only an IPv4 TCP stream socket is supported.")
    # An invalid/closed/unconnected socket raises its original OS error. Never
    # turn it into a successful observation or attempt a replacement connection.
    descriptor = _SOCKET_TYPE.fileno(connection)
    _require(type(descriptor) is int and descriptor >= 0
             and _SOCKET_TYPE.getsockopt(connection, socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM,
             "process_peer_socket", "The connected socket is closed or is not a stream.")
    client = _SOCKET_TYPE.getsockname(connection)
    server = _SOCKET_TYPE.getpeername(connection)
    for endpoint in (client, server):
        _require(type(endpoint) is tuple and len(endpoint) == 2
                 and type(endpoint[0]) is str and endpoint[0] == "127.0.0.1"
                 and type(endpoint[1]) is int and 1 <= endpoint[1] <= 65535,
                 "process_peer_socket", "Both connected endpoints must be exact IPv4 loopback tuples.")
    _require(client[1] != server[1],
             "process_peer_socket", "Client and server ports must be distinct.")
    return descriptor, client, server


def _same_socket(connection, original):
    _require(_socket_snapshot(connection) == original,
             "process_peer_socket_changed", "The original connected socket or endpoint changed; do not reconnect.")


def _same_submission(original, detached, payload):
    for submission in (original, detached):
        _require(_snapshot(submission)[1] == payload,
                 "process_peer_intent_changed", "The captured submission changed during peer authentication.")


class ProcessBoundPeer:
    """One callback attempt, bound to an active genuine lease and supplied key.

    Construct and call on the lease's acquiring thread inside its existing
    context. Pass this object as ``authorize_peer`` to either source-only sender.
    The callback receives the original submission, including for receipt reads.
    A fresh object is necessary for a separately authorized receipt read; a
    failure never authorizes replay of the original application submission.

    No public policy, observer, socket factory, clock or authentication override
    exists. The key is retained in memory only; Python cannot promise erasure.
    """

    __slots__ = ("_lease", "_epoch", "_peer", "_used")

    def __init__(self, lease: leases._ProcessLease, session_key: bytes):
        _require(type(lease) is leases._ProcessLease,
                 "process_peer_lease", "An already held genuine process lease is required.")
        leases._ProcessLease._check_context(lease)
        peer = auth.SessionKeyPeer(session_key)
        epoch = _observe(lease)
        self._lease, self._epoch, self._peer, self._used = lease, epoch, peer, False

    def __repr__(self):
        return "ProcessBoundPeer(<redacted>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("ProcessBoundPeer cannot be serialized.")

    def _check_lifetime(self, deadline, submission):
        _remaining(deadline)
        observed = _observe(self._lease)
        _remaining(deadline)
        _require(observed == self._epoch == submission.request.process_epoch,
                 "process_peer_identity", "Submission and held process lifetime do not match.")

    def __call__(self, connection: socket.socket, deadline: float,
                 submission: _Submission) -> bool:
        # Every attempt consumes the object, including malformed input, refusal,
        # cancellation, timeout or uncertain authentication. There is no reset.
        _require(not self._used,
                 "process_peer_used", "This process-bound peer callback has already been used.")
        self._used = True
        _remaining(deadline)
        leases._ProcessLease._check_context(self._lease)
        detached, payload = _snapshot(submission)
        _remaining(deadline)
        self._check_lifetime(deadline, detached)
        original_socket = _socket_snapshot(connection)
        _remaining(deadline)
        _, client, server = original_socket
        owner = loopback_owner.observe_connection_owner(self._epoch.pid, server[1], client[1])
        _remaining(deadline)
        _require(type(owner) is loopback_owner.ConnectionOwnerObservation
                 and type(owner.pid) is int and type(owner.server_port) is int and type(owner.client_port) is int
                 and (owner.pid, owner.server_port, owner.client_port)
                 == (self._epoch.pid, server[1], client[1]),
                 "process_peer_owner", "No exact server-side connection ownership was established.")
        self._check_lifetime(deadline, detached)
        _same_socket(connection, original_socket)
        _same_submission(submission, detached, payload)
        _remaining(deadline)

        # Nothing above sends authentication or application bytes. Use this very
        # connection and original deadline; HMAC possession is not the PID proof.
        allowed = auth.SessionKeyPeer.__call__(self._peer, connection, deadline, detached)
        _remaining(deadline)
        _require(allowed is True, "process_peer_auth", "Session-key authentication was not established.")
        self._check_lifetime(deadline, detached)
        _same_socket(connection, original_socket)
        _same_submission(submission, detached, payload)
        _remaining(deadline)
        return True
