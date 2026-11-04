"""Current-CLI client peer hook for explicitly provisioned session keys.

Source only: no installation, key provisioning, listener authorization, game API,
CLI activation, persistent access or production qualification. HMAC proves shared
key possession, not OS identity. All other transport/host gates remain unchanged.
See docs/PEER_AUTH.md for exact transcript, threat model and remaining boundaries.
The common codec-independent helper block is intentionally mirrored in both
runtimes; tests require exact source parity and pinned cross-codec transcripts.
"""

from . import companion_protocol as codec

import hashlib
import hmac
import math
import os
import re
import socket
import struct
from time import monotonic

AUTH_VERSION = 1
MAX_AUTH_BYTES = 1024
MAX_AUTH_DEPTH = 2
NONCE_BYTES = 32
MIN_KEY_BYTES = 32
MAX_KEY_BYTES = 64
MAX_DEADLINE_SECONDS = 5.0
_DOMAIN = b"Foundry companion peer authentication\x00v1\x00"
_ID = re.compile(r"[0-9a-f]{32}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_BASE_FIELDS = {"auth_schema", "kind", "session_id", "process_epoch", "connection", "client_nonce"}
_PROOF_KINDS = {"server_proof", "client_proof", "server_ready"}


class PeerAuthError(codec.ProtocolError):
    """Static diagnostics only: no key, proof, nonce or peer-supplied data."""


def _require(condition, code="peer_auth_failed"):
    if not condition:
        raise PeerAuthError(code, "Peer authentication failed; no application authorization was granted.")


def _remaining(deadline):
    _require(type(deadline) in (int, float) and math.isfinite(deadline), "auth_deadline")
    left = deadline - monotonic()
    _require(0 < left <= MAX_DEADLINE_SECONDS, "auth_deadline")
    return left


def _matches(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _context(connection, session_id, epoch, server):
    _require(_matches(session_id, _ID)
             and _integer(epoch.pid, 1, 2**32 - 1)
             and _integer(epoch.creation_filetime, 1, 2**64 - 1), "auth_binding")
    _require(connection.family == socket.AF_INET and connection.type == socket.SOCK_STREAM,
             "auth_socket")
    local, remote = connection.getsockname(), connection.getpeername()
    for endpoint in (local, remote):
        _require(type(endpoint) is tuple and len(endpoint) == 2
                 and endpoint[0] == "127.0.0.1"
                 and _integer(endpoint[1], 1, 65535), "auth_socket")
    client, listener = (remote, local) if server else (local, remote)
    return {"session_id": session_id,
            "process_epoch": {"pid": epoch.pid, "creation_filetime": epoch.creation_filetime},
            "connection": {"client_port": client[1], "server_port": listener[1]}}


def _nonce():
    # Never used for keys. No fallback to a predictable source on RNG failure.
    value = os.urandom(NONCE_BYTES)
    _require(type(value) is bytes and len(value) == NONCE_BYTES, "auth_random")
    return value.hex()


def _envelope(kind, context, client_nonce, server_nonce=None):
    value = dict(context, auth_schema=AUTH_VERSION, kind=kind, client_nonce=client_nonce)
    if server_nonce is not None:
        value["server_nonce"] = server_nonce
    return value


def _tag(key, value):
    # Only schema-validated fixed-size envelopes reach this private helper.
    # The kind is part of the MAC, separating both roles and server confirmation.
    return hmac.new(key, _DOMAIN + codec.encode_frame(value)[4:], hashlib.sha256).hexdigest()


def _signed(key, kind, context, client_nonce, server_nonce):
    value = _envelope(kind, context, client_nonce, server_nonce)
    value["proof"] = _tag(key, value)
    return value


def _validate(value, kind, context, client_nonce=None, server_nonce=None):
    fields = _BASE_FIELDS | ({"server_nonce", "proof"} if kind in _PROOF_KINDS else set())
    _require(type(value) is dict and value.keys() == fields, "auth_envelope")
    _require(type(value["auth_schema"]) is int and value["auth_schema"] == AUTH_VERSION
             and type(value["kind"]) is str and value["kind"] == kind, "auth_envelope")
    _require(_matches(value["session_id"], _ID), "auth_binding")
    epoch, ports = value["process_epoch"], value["connection"]
    _require(type(epoch) is dict and epoch.keys() == {"pid", "creation_filetime"}
             and _integer(epoch["pid"], 1, 2**32 - 1)
             and _integer(epoch["creation_filetime"], 1, 2**64 - 1), "auth_binding")
    _require(type(ports) is dict and ports.keys() == {"client_port", "server_port"}
             and all(_integer(port, 1, 65535) for port in ports.values()), "auth_binding")
    _require(all(value[field] == context[field] for field in context), "auth_binding")
    _require(_matches(value["client_nonce"], _HEX), "auth_nonce")
    if client_nonce is not None:
        _require(value["client_nonce"] == client_nonce, "auth_nonce")
    if kind in _PROOF_KINDS:
        _require(_matches(value["server_nonce"], _HEX)
                 and value["server_nonce"] != value["client_nonce"], "auth_nonce")
        if server_nonce is not None:
            _require(value["server_nonce"] == server_nonce, "auth_nonce")
        _require(_matches(value["proof"], _HEX), "auth_proof")
    return value


def _verify(key, value):
    unsigned = {field: item for field, item in value.items() if field != "proof"}
    _require(hmac.compare_digest(_tag(key, unsigned), value["proof"]), "auth_proof")


def _read_exact(connection, size, deadline):
    result = bytearray()
    while len(result) < size:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(size - len(result))
        _require(bool(chunk), "auth_truncated")
        result.extend(chunk)
    _remaining(deadline)
    return bytes(result)


def _read(connection, deadline):
    size = struct.unpack(">I", _read_exact(connection, 4, deadline))[0]
    _require(1 <= size <= MAX_AUTH_BYTES, "auth_size")
    payload = _read_exact(connection, size, deadline)
    # Bound nesting BEFORE the recursive codec parses any untrusted JSON. The
    # only nested containers in this protocol are process_epoch and connection.
    depth = 0
    quoted = escaped = False
    for char in payload:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            _require(depth <= MAX_AUTH_DEPTH, "auth_depth")
        elif char in (93, 125):
            depth -= 1
    value = codec.decode_payload(payload)
    _remaining(deadline)
    return value


def _send(connection, value, deadline):
    frame = codec.encode_frame(value)
    _require(1 <= len(frame) - 4 <= MAX_AUTH_BYTES, "auth_size")
    connection.settimeout(_remaining(deadline))
    connection.sendall(frame)
    _remaining(deadline)


class SessionKeyPeer:
    """Possession of a caller-supplied session key, never a host authorization.

    Accepts only immutable bytes of length 32 through 64. Length cannot establish
    entropy: secure provisioning is a separate, explicitly authorized host step.
    This object does not generate/load/save keys, open sockets, log, or bootstrap.
    It retains a reference in memory; Python does not guarantee secure erasure.
    """
    __slots__ = ("_key",)

    def __init__(self, key):
        _require(type(key) is bytes and MIN_KEY_BYTES <= len(key) <= MAX_KEY_BYTES, "auth_key")
        self._key = key

    def __repr__(self):
        return "SessionKeyPeer(<redacted>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("SessionKeyPeer cannot be serialized.")

    def __call__(self, connection, deadline, submission):
        """Authenticate the server before exchange_once sends application bytes.

        The exact submission's session/process lifetime is trusted caller input,
        not an identity obtained from the peer. This hook confers no operation,
        profile, journal, game-process or provider permission.
        """
        try:
            _remaining(deadline)
            request = submission.request
            context = _context(connection, request.session_id, request.process_epoch, False)
            client_nonce = _nonce()
            _send(connection, _envelope("client_hello", context, client_nonce), deadline)
            proof = _validate(_read(connection, deadline), "server_proof", context, client_nonce)
            _verify(self._key, proof)
            server_nonce = proof["server_nonce"]
            _send(connection, _signed(self._key, "client_proof", context,
                                      client_nonce, server_nonce), deadline)
            ready = _validate(_read(connection, deadline), "server_ready", context,
                              client_nonce, server_nonce)
            _verify(self._key, ready)
            _remaining(deadline)
            return True
        except PeerAuthError:
            raise
        except Exception:
            raise PeerAuthError("peer_auth_failed", "Peer authentication failed.") from None
