"""Opt-in Windows named-pipe transport for the inert fixture owner only.

Uses one current-logon SID DACL, local-only first-instance pipes and an explicit
32-byte per-session secret. No listener is created on import and no CLI/live
backend is wired. HMAC/DPAPI and same-logon ACLs do not authenticate hostile
same-user programs, establish endpoint authority, or qualify game behavior.
"""
import ctypes
from ctypes import wintypes as wt
import hashlib
import hmac
import math
import os
import secrets
import struct
import time

from .errors import FoundryError
from .independent_owner import FixtureOwner, MAX_FRAME, _depth_ok
from .io import canonical, loads

_PIPE_ACCESS_DUPLEX = 3
_FIRST = 0x00080000
_OVERLAPPED = 0x40000000
_REJECT_REMOTE = 8
_OPEN_EXISTING = 3
_GENERIC_READ_WRITE = 0xC0000000
_ERROR_PENDING = 997
_ERROR_PIPE_CONNECTED = 535
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_OPERATION_ABORTED = 995
_ERROR_NOT_FOUND = 1168
_WAIT_OBJECT = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_DOMAIN_REQ = b'foundry-fixture-pipe-request-v1\0'
_DOMAIN_RES = b'foundry-fixture-pipe-response-v1\0'
_UNRESOLVED_PIPES = []


class _Offset(ctypes.Structure):
    _fields_ = [('low', wt.DWORD), ('high', wt.DWORD)]


class _OffsetUnion(ctypes.Union):
    _fields_ = [('offset', _Offset), ('pointer', ctypes.c_void_p)]


class _Overlapped(ctypes.Structure):
    _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                ('union', _OffsetUnion), ('event', wt.HANDLE)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [('length', wt.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', wt.BOOL)]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [('sid', ctypes.c_void_p), ('attributes', wt.DWORD)]


class _TokenGroups(ctypes.Structure):
    _fields_ = [('count', wt.DWORD), ('groups', _SidAndAttributes * 1)]


def _bind(dll, name, result, args):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, args
    return function


def _require(condition, label):
    if not condition:
        raise ctypes.WinError(ctypes.get_last_error(), label)


class _Api:
    def __init__(self):
        if os.name != 'nt':
            raise FoundryError('windows_required', 'The fixture named pipe requires Windows.')
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        a = ctypes.WinDLL('advapi32', use_last_error=True)
        p = ctypes.c_void_p
        self.current = _bind(k, 'GetCurrentProcess', wt.HANDLE, [])
        self.close = _bind(k, 'CloseHandle', wt.BOOL, [wt.HANDLE])
        self.local_free = _bind(k, 'LocalFree', p, [p])
        self.open_token = _bind(a, 'OpenProcessToken', wt.BOOL, [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)])
        self.token_info = _bind(a, 'GetTokenInformation', wt.BOOL, [wt.HANDLE, ctypes.c_int, p, wt.DWORD, ctypes.POINTER(wt.DWORD)])
        self.sid_string = _bind(a, 'ConvertSidToStringSidW', wt.BOOL, [p, ctypes.POINTER(p)])
        self.sddl = _bind(a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', wt.BOOL,
                          [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(p), ctypes.POINTER(wt.DWORD)])
        self.create_pipe = _bind(k, 'CreateNamedPipeW', wt.HANDLE,
            [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.POINTER(_SecurityAttributes)])
        self.create_file = _bind(k, 'CreateFileW', wt.HANDLE,
            [wt.LPCWSTR, wt.DWORD, wt.DWORD, p, wt.DWORD, wt.DWORD, wt.HANDLE])
        self.connect = _bind(k, 'ConnectNamedPipe', wt.BOOL, [wt.HANDLE, ctypes.POINTER(_Overlapped)])
        self.disconnect = _bind(k, 'DisconnectNamedPipe', wt.BOOL, [wt.HANDLE])
        self.read = _bind(k, 'ReadFile', wt.BOOL, [wt.HANDLE, p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(_Overlapped)])
        self.write = _bind(k, 'WriteFile', wt.BOOL, [wt.HANDLE, p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(_Overlapped)])
        self.event = _bind(k, 'CreateEventW', wt.HANDLE, [p, wt.BOOL, wt.BOOL, wt.LPCWSTR])
        self.wait = _bind(k, 'WaitForSingleObject', wt.DWORD, [wt.HANDLE, wt.DWORD])
        self.complete = _bind(k, 'GetOverlappedResult', wt.BOOL, [wt.HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(wt.DWORD), wt.BOOL])
        self.cancel = _bind(k, 'CancelIoEx', wt.BOOL, [wt.HANDLE, ctypes.POINTER(_Overlapped)])

    def close_once(self, handle):
        _require(self.close(handle), 'CloseHandle failed')

    def logon_sid(self):
        token = wt.HANDLE()
        _require(self.open_token(self.current(), 8, ctypes.byref(token)), 'OpenProcessToken failed')
        try:
            size = wt.DWORD()
            result = self.token_info(token, 28, None, 0, ctypes.byref(size))
            if result or ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or not 0 < size.value <= 65536:
                raise FoundryError('fixture_pipe_token', 'Exact current-logon SID size unavailable.')
            buffer = ctypes.create_string_buffer(size.value)
            _require(self.token_info(token, 28, buffer, size.value, ctypes.byref(size)), 'GetTokenInformation failed')
            groups = ctypes.cast(buffer, ctypes.POINTER(_TokenGroups)).contents
            if (groups.count != 1 or not groups.groups[0].sid or
                    groups.groups[0].attributes & 0xC0000000 != 0xC0000000):
                raise FoundryError('fixture_pipe_token', 'Exactly one logon SID is required.')
            result_ptr = ctypes.c_void_p()
            _require(self.sid_string(groups.groups[0].sid, ctypes.byref(result_ptr)), 'ConvertSidToStringSid failed')
            try:
                value = ctypes.wstring_at(result_ptr)
            finally:
                _require(not self.local_free(result_ptr), 'LocalFree failed')
            if not value.startswith('S-1-5-5-'):
                raise FoundryError('fixture_pipe_token', 'Unexpected current-logon SID type.')
            return value
        finally:
            self.close_once(token)

    def make_server(self, name):
        sid = self.logon_sid()
        descriptor = ctypes.c_void_p()
        _require(self.sddl('D:P(A;;GA;;;' + sid + ')', 1, ctypes.byref(descriptor), None), 'SDDL conversion failed')
        handle = None
        try:
            attrs = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            handle = self.create_pipe(name, _PIPE_ACCESS_DUPLEX | _FIRST | _OVERLAPPED,
                                      _REJECT_REMOTE, 1, MAX_FRAME + 256, MAX_FRAME + 256, 0, ctypes.byref(attrs))
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error(), 'CreateNamedPipe failed')
        finally:
            try:
                free_result = self.local_free(descriptor)
            except BaseException:
                _UNRESOLVED_PIPES.append((self, handle, descriptor))
                raise
            if free_result:
                # The returned handle and descriptor can no longer be handed to
                # their caller reliably. Retain them, and never retry cleanup.
                _UNRESOLVED_PIPES.append((self, handle, descriptor))
                raise ctypes.WinError(ctypes.get_last_error(), 'LocalFree failed; pipe ownership unresolved')
        return handle

    def make_client(self, name):
        handle = self.create_file(name, _GENERIC_READ_WRITE, 0, None, _OPEN_EXISTING, _OVERLAPPED, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error(), 'CreateFile failed')
        return handle


class _Pipe:
    def __init__(self, handle, api):
        self._handle, self._api, self._uncertain = handle, api, []
        self._closed = False

    def _retain_uncertain(self, ov, event, buffer):
        self._uncertain.append((ov, event, buffer))
        if self not in _UNRESOLVED_PIPES:
            _UNRESOLVED_PIPES.append(self)

    def _operation(self, kind, data, deadline):
        if self._closed or self._uncertain:
            raise FoundryError('fixture_pipe_unresolved', 'Original pipe operation or close is unresolved.')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FoundryError('fixture_pipe_timeout', 'Fixture pipe deadline expired.')
        event = self._api.event(None, True, False, None)
        _require(event, 'CreateEvent failed')
        ov = None
        buffer = None
        pending = False
        try:
            ov, count = _Overlapped(), wt.DWORD()
            ov.event = event
            # Native code may have adopted the OVERLAPPED even if the call or
            # immediate status inspection is interrupted before returning.
            pending = True
            if kind == 'connect':
                ok = self._api.connect(self._handle, ctypes.byref(ov))
            elif kind == 'read':
                buffer = ctypes.create_string_buffer(data)
                ok = self._api.read(self._handle, buffer, data, ctypes.byref(count), ctypes.byref(ov))
            else:
                buffer = ctypes.create_string_buffer(data, len(data))
                ok = self._api.write(self._handle, buffer, len(data), ctypes.byref(count), ctypes.byref(ov))
            if not ok:
                error = ctypes.get_last_error()
                if kind == 'connect' and error == _ERROR_PIPE_CONNECTED:
                    pending = False
                    return b''
                if error != _ERROR_PENDING:
                    pending = False
                    raise ctypes.WinError(error, 'Fixture named-pipe operation failed')
                millis = max(0, min(5000, math.floor((deadline - time.monotonic()) * 1000)))
                waited = self._api.wait(event, millis)
                if waited == _WAIT_TIMEOUT:
                    cancelled = self._api.cancel(self._handle, ctypes.byref(ov))
                    cancel_error = 0 if cancelled else ctypes.get_last_error()
                    if not cancelled and cancel_error != _ERROR_NOT_FOUND:
                        self._retain_uncertain(ov, event, buffer)
                        event = None
                        raise ctypes.WinError(cancel_error, 'CancelIoEx failed')
                    waited = self._api.wait(event, 1000)
                    if waited != _WAIT_OBJECT:
                        self._retain_uncertain(ov, event, buffer)
                        event = None  # retain storage/handle; no cleanup retry
                        raise FoundryError('fixture_pipe_unresolved', 'Canceled fixture I/O did not reach completion.')
                    completed = self._api.complete(self._handle, ctypes.byref(ov), ctypes.byref(count), False)
                    if completed:
                        pending = False
                        raise FoundryError('fixture_pipe_unresolved',
                            'Fixture I/O completed during cancellation; its result must not be replayed.')
                    if not completed and ctypes.get_last_error() != _ERROR_OPERATION_ABORTED:
                        self._retain_uncertain(ov, event, buffer)
                        event = None
                        raise FoundryError('fixture_pipe_unresolved', 'Canceled fixture I/O completion is unknown.')
                    pending = False
                    raise FoundryError('fixture_pipe_timeout', 'Fixture I/O timed out; no request may be replayed.')
                if waited != _WAIT_OBJECT:
                    self._retain_uncertain(ov, event, buffer)
                    event = None
                    if waited == _WAIT_FAILED:
                        raise ctypes.WinError(ctypes.get_last_error(), 'Fixture I/O wait failed')
                    raise FoundryError('fixture_pipe_unresolved', 'Fixture I/O completion is unavailable.')
                _require(self._api.complete(self._handle, ctypes.byref(ov), ctypes.byref(count), False),
                         'GetOverlappedResult failed')
                pending = False
            else:
                pending = False
            if kind == 'read':
                if not count.value:
                    raise FoundryError('fixture_pipe_truncated', 'Fixture peer closed before a complete frame.')
                return buffer.raw[:count.value]
            if kind == 'write' and count.value != len(data):
                raise FoundryError('fixture_pipe_unresolved', 'Fixture write was partial; do not replay.')
            return b''
        finally:
            if pending and event is not None:
                # The kernel may still reference ov/buffer after an exception.
                self._retain_uncertain(ov, event, buffer)
                event = None
            if event is not None:
                try:
                    self._api.close_once(event)
                except BaseException:
                    # Preserve event/operation storage even when CloseHandle's
                    # outcome is unknown; do not repeat the close.
                    self._retain_uncertain(ov, event, buffer)
                    raise

    def read_exact(self, size, deadline):
        data = bytearray()
        while len(data) < size:
            data.extend(self._operation('read', size - len(data), deadline))
        return bytes(data)

    def write(self, data, deadline):
        self._operation('write', data, deadline)

    def close(self):
        if self._closed:
            raise FoundryError('fixture_pipe_unresolved', 'Pipe close already attempted; do not retry.')
        if self._uncertain:
            raise FoundryError('fixture_pipe_unresolved', 'Pending fixture I/O remains retained.')
        self._closed = True
        try:
            self._api.close_once(self._handle)
        except BaseException:
            if self not in _UNRESOLVED_PIPES:
                _UNRESOLVED_PIPES.append(self)
            raise


def _tag(secret, domain, nonce, value):
    return hmac.new(secret, domain + nonce + canonical(value), hashlib.sha256).hexdigest()


def _read_frame(pipe, deadline):
    size = struct.unpack('>I', pipe.read_exact(4, deadline))[0]
    if not 1 <= size <= MAX_FRAME:
        raise FoundryError('fixture_pipe_frame', 'Fixture frame exceeds its bound.')
    raw = pipe.read_exact(size, deadline)
    if not _depth_ok(raw):
        raise FoundryError('fixture_pipe_frame', 'Fixture frame is malformed or too deep.')
    return loads(raw)


def _write_frame(pipe, value, deadline):
    raw = canonical(value)
    if not 1 <= len(raw) <= MAX_FRAME:
        raise FoundryError('fixture_pipe_frame', 'Fixture frame exceeds its bound.')
    pipe.write(struct.pack('>I', len(raw)) + raw, deadline)


class FixturePipe:
    """Explicit opt-in, bounded sequential connections, no live input."""
    def __init__(self, owner, secret):
        from .fixture_launcher import FixtureBroker
        if type(owner) not in (FixtureOwner, FixtureBroker) or type(secret) is not bytes or len(secret) != 32:
            raise FoundryError('fixture_pipe_scope', 'An inert fixture owner and exact secret are required.')
        if len(_UNRESOLVED_PIPES) >= 128:
            raise FoundryError('fixture_pipe_unresolved', 'Retained unresolved fixture-I/O capacity is full.')
        self._api, self._owner, self._secret = _Api(), owner, secret
        self.name = r'\\.\pipe\foundry-fixture-' + secrets.token_hex(16)
        self._pipe = _acquire_pipe(self._api, lambda: self._api.make_server(self.name))
        self._count, self._busy, self._failed = 0, False, False

    def serve_once(self, seconds=5.0):
        if (self._failed or self._busy or self._count >= 128 or
                type(seconds) not in (int, float) or not 0 < seconds <= 5):
            raise FoundryError('fixture_pipe_scope', 'An idle bounded fixture endpoint is required.')
        self._busy = True
        connected = False
        try:
            deadline = time.monotonic() + seconds
            self._pipe._operation('connect', None, deadline)
            connected = True
            nonce = secrets.token_bytes(32)
            self._pipe.write(nonce, deadline)
            envelope = _read_frame(self._pipe, deadline)
            if (type(envelope) is not dict or set(envelope) != {'request', 'mac'} or
                    type(envelope['mac']) is not str or
                    not hmac.compare_digest(envelope['mac'], _tag(self._secret, _DOMAIN_REQ, nonce, envelope['request']))):
                raise FoundryError('fixture_pipe_auth', 'Fixture secret proof failed; no request dispatched.')
            result = self._owner.handle(envelope['request'])
            _write_frame(self._pipe, {'result': result, 'mac': _tag(self._secret, _DOMAIN_RES, nonce, result)}, deadline)
            if self._pipe.read_exact(1, deadline) != b'\x01':
                raise FoundryError('fixture_pipe_unresolved', 'Fixture transport receipt was not observed; do not replay.')
            _require(self._api.disconnect(self._pipe._handle), 'DisconnectNamedPipe failed')
            self._count += 1
            return result
        except FoundryError as exc:
            # Only a pre-connect timeout with no retained I/O is rearmable.
            # Timeout after connection may have consumed an unknown request.
            if not connected and exc.code == 'fixture_pipe_timeout' and not self._pipe._uncertain:
                return {'fixture_idle': True}
            self._failed = True
            raise
        except BaseException:
            self._failed = True
            raise
        finally:
            self._busy = False

    def close(self):
        self._pipe.close()


def _acquire_pipe(api, acquire):
    """Quarantine a returned handle if wrapping it fails; never reopen it."""
    handle = None
    try:
        handle = acquire()
        return _Pipe(handle, api)
    except BaseException:
        # None does not prove the native acquisition had no side effect.
        _UNRESOLVED_PIPES.append((api, handle))
        raise


def fixture_client(name, secret, request, seconds=5.0):
    """Use only an explicitly inherited secret with a fixture pipe name."""
    if (type(name) is not str or not name.startswith(r'\\.\pipe\foundry-fixture-') or
            type(secret) is not bytes or len(secret) != 32 or type(seconds) not in (int, float) or not 0 < seconds <= 5):
        raise FoundryError('fixture_pipe_scope', 'Explicit fixture endpoint and inherited secret required.')
    if len(_UNRESOLVED_PIPES) >= 128:
        raise FoundryError('fixture_pipe_unresolved', 'Retained unresolved fixture-I/O capacity is full.')
    api = _Api()
    pipe = _acquire_pipe(api, lambda: api.make_client(name))
    deadline = time.monotonic() + seconds
    try:
        nonce = pipe.read_exact(32, deadline)
        _write_frame(pipe, {'request': request, 'mac': _tag(secret, _DOMAIN_REQ, nonce, request)}, deadline)
        envelope = _read_frame(pipe, deadline)
        if (type(envelope) is not dict or set(envelope) != {'result', 'mac'} or
                type(envelope['mac']) is not str or
                not hmac.compare_digest(envelope['mac'], _tag(secret, _DOMAIN_RES, nonce, envelope['result']))):
            raise FoundryError('fixture_pipe_auth', 'Fixture response proof failed; no outcome is inferred.')
        pipe.write(b'\x01', deadline)
        return envelope['result']
    finally:
        pipe.close()
