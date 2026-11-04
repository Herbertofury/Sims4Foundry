"""Bounded, read-only ownership observation for one connected IPv4 loopback tuple.

This is not process authentication. The caller must independently retain and
verify the expected process lifetime and keep the same connected socket open.
Importing this module does not load a Windows DLL or perform an observation.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
import sys

from .errors import FoundryError


# Windows DWORD/ULONG remain 32 bits on both Win32 and Win64. Do not use the
# host's c_ulong: it is 64 bits on some platforms used by fabricated tests.
_DWORD = ctypes.c_uint32
_AF_INET = 2
_TCP_TABLE_OWNER_PID_CONNECTIONS = 4
_MIB_TCP_STATE_LISTEN = 2
_MIB_TCP_STATE_ESTAB = 5
_NO_ERROR = 0
_ERROR_ACCESS_DENIED = 5
_ERROR_INSUFFICIENT_BUFFER = 122
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
_LOOPBACK_DWORD = int.from_bytes(b"\x7f\x00\x00\x01", "little")

MAX_TABLE_BYTES = 256 * 1024
MAX_TABLE_ROWS = 4096
MAX_TABLE_CALLS = 3  # One size probe, then at most two bounded table reads.


class _TcpRowOwnerPid(ctypes.LittleEndianStructure):
    _fields_ = [
        ("dwState", _DWORD),
        ("dwLocalAddr", _DWORD),
        ("dwLocalPort", _DWORD),
        ("dwRemoteAddr", _DWORD),
        ("dwRemotePort", _DWORD),
        ("dwOwningPid", _DWORD),
    ]


class _TcpTableOwnerPid(ctypes.LittleEndianStructure):
    _fields_ = [("dwNumEntries", _DWORD), ("table", _TcpRowOwnerPid * 1)]


# Use the ABI's field offset and row stride, rather than packed wire offsets.
_ROWS_OFFSET = _TcpTableOwnerPid.table.offset
_ROW_STRIDE = ctypes.sizeof(_TcpRowOwnerPid)


@dataclass(frozen=True)
class ConnectionOwnerObservation:
    """A transient matching row, not a lease, capability, or reusable admission.

    Both addresses are exactly 127.0.0.1. Ports are oriented from the server's
    side of the caller's still-open connection. No unrelated endpoints escape.
    """

    pid: int
    server_port: int
    client_port: int


def observe_connection_owner(
    expected_pid: int, server_port: int, client_port: int
) -> ConnectionOwnerObservation:
    """Observe exactly one ESTABLISHED server-side tuple owned by expected_pid.

    Obtain server_port from the connected socket's getpeername(), client_port
    from its getsockname(), and check that both addresses are exactly 127.0.0.1.
    Keep that socket and the independently acquired target process handle alive.
    Verify that retained lifetime before/after observation and authentication.
    A bare PID, PID from a peer, discovery file, or HMAC proof is not a lifetime.

    This explicit call is Windows-only and reads IP Helper's connection table.
    It sends nothing, opens no socket, and never opens or enumerates processes.
    All unavailable, incomplete, ambiguous, or nonmatching results refuse with
    FoundryError. There is no native fallback and no result cache.
    """
    _validate_request(expected_pid, server_port, client_port)
    if os.name != "nt" or sys.byteorder != "little":
        raise FoundryError(
            "connection_owner_unavailable",
            "Connection ownership observation requires little-endian Windows.",
        )
    api = _load_native_api()
    return _observe_connection_owner_with_api(
        expected_pid, server_port, client_port, get_extended_tcp_table=api
    )


def _validate_request(expected_pid, server_port, client_port):
    if type(expected_pid) is not int or not 1 <= expected_pid <= 0xFFFFFFFF:
        raise FoundryError(
            "invalid_connection_owner_request", "Expected PID must be a positive DWORD."
        )
    if any(type(port) is not int or not 1 <= port <= 65535
           for port in (server_port, client_port)):
        raise FoundryError(
            "invalid_connection_owner_request", "Both explicit ports must be integers from 1 to 65535."
        )
    if server_port == client_port:
        raise FoundryError(
            "invalid_connection_owner_request", "Server and client ports must be distinct."
        )


def _load_native_api():
    # Lazy and System32-only: do not search a game directory, PATH, or cwd.
    try:
        dll = ctypes.WinDLL("iphlpapi.dll", winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
        api = dll.GetExtendedTcpTable
        api.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_DWORD), ctypes.c_int32,
            _DWORD, ctypes.c_int32, _DWORD,
        ]
        api.restype = _DWORD
    except (AttributeError, OSError, TypeError):
        raise FoundryError(
            "connection_owner_unavailable", "The Windows connection owner API is unavailable."
        ) from None

    def call(table, size, order, family, table_class, reserved):
        # Explicitly retain the DLL for the entire observation.
        _ = dll
        return api(table, size, order, family, table_class, reserved)

    return call


def _call_table(api, buffer, size):
    try:
        code = api(
            buffer, ctypes.byref(size), 0, _AF_INET,
            _TCP_TABLE_OWNER_PID_CONNECTIONS, 0,
        )
    except Exception:
        # Do not include foreign exception text or table contents in diagnostics.
        raise FoundryError(
            "connection_owner_unavailable", "The connection owner query failed."
        ) from None
    if type(code) is not int or not 0 <= code <= 0xFFFFFFFF:
        raise FoundryError(
            "connection_owner_malformed", "The connection owner API returned an invalid status."
        )
    if code == _ERROR_ACCESS_DENIED:
        raise FoundryError(
            "connection_owner_denied", "Access to the connection owner observation was denied."
        )
    if code not in (_NO_ERROR, _ERROR_INSUFFICIENT_BUFFER):
        raise FoundryError(
            "connection_owner_unavailable", "The connection owner query did not succeed.",
            winerror=code,
        )
    return code


def _bounded_size(size):
    if size < _ROWS_OFFSET:
        raise FoundryError(
            "connection_owner_incomplete", "The connection table header is incomplete."
        )
    if size > MAX_TABLE_BYTES:
        raise FoundryError(
            "connection_owner_limit", "The connection table exceeds the observation byte limit."
        )
    return size


def _observe_connection_owner_with_api(
    expected_pid, server_port, client_port, *, get_extended_tcp_table
):
    """Private fabricated-test seam; never inject a live alternate owner source."""
    _validate_request(expected_pid, server_port, client_port)
    size = _DWORD(0)
    if _call_table(get_extended_tcp_table, None, size) != _ERROR_INSUFFICIENT_BUFFER:
        raise FoundryError(
            "connection_owner_incomplete", "The connection table size probe returned no table."
        )
    capacity = _bounded_size(size.value)
    for _ in range(MAX_TABLE_CALLS - 1):
        try:
            buffer = ctypes.create_string_buffer(capacity)
        except (MemoryError, OverflowError):
            raise FoundryError(
                "connection_owner_unavailable", "The bounded connection table allocation failed."
            ) from None
        try:
            size = _DWORD(capacity)
            code = _call_table(get_extended_tcp_table, buffer, size)
            reported = _bounded_size(size.value)
            if code == _ERROR_INSUFFICIENT_BUFFER:
                if reported <= capacity:
                    raise FoundryError(
                        "connection_owner_malformed", "The connection table size did not progress."
                    )
                capacity = reported
                continue
            if reported > ctypes.sizeof(buffer):
                raise FoundryError(
                    "connection_owner_incomplete", "The connection table exceeds its returned buffer."
                )
            return _match_table(buffer, reported, expected_pid, server_port, client_port)
        finally:
            # IP Helper necessarily returns other rows. Never copy, persist, log,
            # or return them; erase the entire transient allocation on every path.
            ctypes.memset(ctypes.addressof(buffer), 0, ctypes.sizeof(buffer))
    raise FoundryError(
        "connection_owner_incomplete", "The connection table changed beyond the bounded read limit."
    )


def _port_from_dword(value):
    # As in Microsoft's sample: ntohs((u_short)dwLocalPort). Only the low WORD
    # carries the port; the DWORD is not a big-endian 32-bit port number.
    return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)


def _match_table(buffer, size, expected_pid, server_port, client_port):
    count = _DWORD.from_buffer(buffer, _TcpTableOwnerPid.dwNumEntries.offset).value
    if count > MAX_TABLE_ROWS:
        raise FoundryError(
            "connection_owner_limit", "The connection table exceeds the observation row limit."
        )
    if _ROWS_OFFSET + count * _ROW_STRIDE > size:
        raise FoundryError(
            "connection_owner_incomplete", "The connection table does not contain every declared row."
        )
    matches = 0
    matched_pid = matched_state = None
    for index in range(count):
        # This is a view into the wiped buffer, not a copied row or endpoint list.
        row = _TcpRowOwnerPid.from_buffer(buffer, _ROWS_OFFSET + index * _ROW_STRIDE)
        if not 1 <= row.dwState <= 12 or row.dwState == _MIB_TCP_STATE_LISTEN:
            raise FoundryError(
                "connection_owner_malformed", "The connection table contains an invalid connection state."
            )
        if (row.dwLocalAddr != _LOOPBACK_DWORD
                or row.dwRemoteAddr != _LOOPBACK_DWORD
                or _port_from_dword(row.dwLocalPort) != server_port
                or _port_from_dword(row.dwRemotePort) != client_port):
            continue
        matches += 1
        if matches > 1:
            raise FoundryError(
                "connection_owner_ambiguous", "More than one row matches the exact server-side connection."
            )
        matched_pid, matched_state = row.dwOwningPid, row.dwState
    if not matches:
        raise FoundryError(
            "connection_owner_missing", "No exact server-side loopback connection was observed."
        )
    if matched_state != _MIB_TCP_STATE_ESTAB:
        raise FoundryError(
            "connection_owner_not_established", "The exact loopback connection is not established."
        )
    if matched_pid != expected_pid:
        raise FoundryError(
            "connection_owner_mismatch", "The exact loopback connection is not owned by the expected process."
        )
    return ConnectionOwnerObservation(expected_pid, server_port, client_port)
