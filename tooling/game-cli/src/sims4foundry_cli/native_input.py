"""Source/fixture-only, default-denied Win32 packet backend.

No production admission or CLI route exists. The public factory always refuses.
Private Python fixtures exercise the implementation, never game qualification.
See docs/NATIVE_INPUT.md for trust boundaries, races and cleanup limitations.
Historical release-discipline lineage SHA-256 (no Apex source is imported):
d8df2930835408334411e9f141a3d704e9dc32536eb75cca8f44136b90dfdcbd.
"""
from contextlib import ExitStack
from copy import deepcopy
import ctypes
from ctypes import wintypes as wt
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import PureWindowsPath
import threading
import time

from .errors import FoundryError
from .guarded_input import _validate_sequence
from .input_packets import InputPacket, Message, CoordinateSpace, MAX_DURATION_MS
from . import process_lease as leases
from . import windows

MAX_IMAGE_BYTES = 256 * 1024 * 1024
MAX_PREPARATION_SECONDS = 5
MAX_QUERY_SECONDS = .25
MAX_RECORDING_AGE_SECONDS = 1
OWNED_KEYS = ("pid", "creation_filetime", "executable", "executable_sha256", "hwnd", "thread_id", "window_class")


def _fail(code, message):
    raise FoundryError(code, message) from None


def _check(condition, code, message):
    if not condition:
        _fail(code, message)


def _packet(value):
    _check(type(value) is InputPacket, "invalid_packet", "Only an exact typed InputPacket is accepted.")
    try:
        # Frozen dataclasses can still be altered via object.__setattr__. Rebuild.
        return InputPacket(value.message, value.wparam, value.lparam, value.offset_ms,
                           value.coordinate_space, value.release_intent)
    except BaseException:
        _fail("invalid_packet", "Packet fields failed independent validation.")


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True, slots=True)
class _RecorderSample:
    epoch: object
    sequence: int
    captured_at: float


@dataclass(frozen=True, slots=True)
class _FixtureTicket:
    """Trusted in-process test evidence, deliberately not a production capability."""
    window_epoch: object
    recording: _RecorderSample


class _FixturePolicy:
    """Implement ONLY inside a controlled test, never with user-supplied data.

    admit must establish a newly owned synthetic fixture and reconciled fixture
    history. window_epoch must independently reject destruction/recreation.
    recorder_sample must sample an independent advancing synthetic recorder.
    All hooks must return promptly; Python cannot forcibly bound arbitrary code.
    Base implementations deny, and booleans are not tickets or observations.
    """
    def admit(self, expected, packets):
        _fail("input_not_admitted", "No trusted isolated fixture admission was supplied.")

    def window_epoch(self):
        _fail("window_unverified", "Original fixture window continuity is not observable.")

    def recorder_sample(self):
        _fail("recording_unverified", "No independent fixture recorder observation is available.")


def native_packet_backend(expected_identity):
    """Always refuse: identity JSON, leases and flags are not live authority.

    This function performs no DLL loading, file access, lease acquisition or input.
    A future reviewed integration must implement actual admission/reconciliation.
    """
    _fail("input_not_admitted", "Native input has no production admission; source and isolated fixtures only.")


def _fixture_backend(expected, packets, policy):
    """Private test seam, not a live opt-in. Construction is native-side-effect free."""
    return _PacketBackend(expected, packets, policy)


class _FileInfo(ctypes.Structure):
    _fields_ = [("attributes", wt.DWORD), ("created", wt.FILETIME), ("accessed", wt.FILETIME),
                ("written", wt.FILETIME), ("volume", wt.DWORD), ("size_high", wt.DWORD),
                ("size_low", wt.DWORD), ("links", wt.DWORD), ("index_high", wt.DWORD), ("index_low", wt.DWORD)]


class _NativeApi(leases._WindowsApi):
    """Read-only query bindings plus the sole allowlisted asynchronous sender."""
    def __init__(self):
        super().__init__()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        user = ctypes.WinDLL("user32", use_last_error=True)
        bind = windows._bind
        self._image = bind(kernel, "QueryFullProcessImageNameW", wt.BOOL,
                           [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)])
        self._file = bind(kernel, "CreateFileW", wt.HANDLE,
                          [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE])
        self._file_type = bind(kernel, "GetFileType", wt.DWORD, [wt.HANDLE])
        self._drive_type = bind(kernel, "GetDriveTypeW", wt.UINT, [wt.LPCWSTR])
        self._file_info = bind(kernel, "GetFileInformationByHandle", wt.BOOL,
                               [wt.HANDLE, ctypes.POINTER(_FileInfo)])
        self._read = bind(kernel, "ReadFile", wt.BOOL,
                          [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p])
        self._window_pid = bind(user, "GetWindowThreadProcessId", wt.DWORD, [wt.HWND, ctypes.POINTER(wt.DWORD)])
        self._class = bind(user, "GetClassNameW", ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int])
        self._visible = bind(user, "IsWindowVisible", wt.BOOL, [wt.HWND])
        self._iconic = bind(user, "IsIconic", wt.BOOL, [wt.HWND])
        self._rect = bind(user, "GetClientRect", wt.BOOL, [wt.HWND, ctypes.POINTER(wt.RECT)])
        self._origin = bind(user, "ClientToScreen", wt.BOOL, [wt.HWND, ctypes.POINTER(wt.POINT)])
        self._dpi = bind(user, "GetDpiForWindow", wt.UINT, [wt.HWND])
        self._foreground = bind(user, "GetForegroundWindow", wt.HWND, [])
        self._layout = bind(user, "GetKeyboardLayout", wt.HANDLE, [wt.DWORD])
        self._map = bind(user, "MapVirtualKeyExW", wt.UINT, [wt.UINT, wt.UINT, wt.HANDLE])
        self._key = bind(user, "GetAsyncKeyState", wt.SHORT, [ctypes.c_int])
        self._thread_id = bind(kernel, "GetCurrentThreadId", wt.DWORD, [])
        self._desktop = bind(user, "GetThreadDesktop", wt.HANDLE, [wt.DWORD])
        self._desktop_info = bind(user, "GetUserObjectInformationW", wt.BOOL,
                                  [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)])
        self._input_desktop = bind(user, "OpenInputDesktop", wt.HANDLE, [wt.DWORD, wt.BOOL, wt.DWORD])
        self._close_desktop = bind(user, "CloseDesktop", wt.BOOL, [wt.HANDLE])
        self._post = bind(user, "PostMessageW", wt.BOOL, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM])

    def pin_image(self, expected):
        # OPEN_EXISTING; no write/delete sharing, no inherited handle or ACL edits.
        # Opening an image with an existing writable mapping must also fail.
        path = PureWindowsPath(expected["executable"])
        _check(len(path.drive) == 2 and path.drive[1] == ":" and path.drive[0].isalpha()
               and ".." not in path.parts and all(":" not in part for part in path.parts[1:])
               and self._drive_type(path.anchor) == 3,
               "image_unverified", "Only an ordinary fixed local disk image may be pinned.")
        handle = self._file(expected["executable"], 0x80000000, 1, None, 3, 0x00200000, None)
        _check(handle not in (None, 0, -1, ctypes.c_void_p(-1).value), "image_unverified", "Cannot retain a read-only image pin.")
        try:
            info = _FileInfo()
            _check(self._file_type(handle) == 1 and self._file_info(handle, ctypes.byref(info)),
                   "image_unverified", "Image pin is not an observable disk file.")
            size = (info.size_high << 32) | info.size_low
            _check(not info.attributes & (0x10 | 0x400) and 0 < size <= MAX_IMAGE_BYTES,
                   "image_unverified", "Image pin is a directory, reparse point or unsupported size.")
            start, digest, total = time.monotonic(), hashlib.sha256(), 0
            buffer = ctypes.create_string_buffer(1024 * 1024)
            while total < size:
                read = wt.DWORD()
                _check(self._read(handle, buffer, min(len(buffer), size-total), ctypes.byref(read), None),
                       "image_unverified", "Cannot hash the retained image before input.")
                _check(0 < read.value <= min(len(buffer), size-total), "image_unverified", "Image read was incomplete.")
                digest.update(buffer.raw[:read.value])
                total += read.value
                _check(0 <= time.monotonic()-start <= MAX_PREPARATION_SECONDS,
                       "image_unverified", "Image preparation exceeded its time budget before input.")
            _check(digest.hexdigest() == expected["executable_sha256"], "image_unverified", "Pinned image hash does not match.")
            return handle
        except BaseException:
            self.close(handle)
            raise

    def integrity(self, process):
        token = wt.HANDLE()
        _check(self._open_token(process, 8, ctypes.byref(token)), "integrity_unverified", "Cannot query process integrity.")
        try:
            size = wt.DWORD()
            result = self._token_info(token, 25, None, 0, ctypes.byref(size))
            _check(not result and ctypes.get_last_error() == 122 and ctypes.sizeof(ctypes.c_void_p) <= size.value <= 65536,
                   "integrity_unverified", "Integrity buffer requirement is invalid.")
            buffer = ctypes.create_string_buffer(size.value)
            _check(self._token_info(token, 25, buffer, len(buffer), ctypes.byref(size)) and size.value <= len(buffer),
                   "integrity_unverified", "Integrity observation failed.")
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            start = ctypes.addressof(buffer)
            _check(type(sid) is int and start <= sid <= start + size.value - 8,
                   "integrity_unverified", "Integrity SID pointer is invalid.")
            header = ctypes.string_at(sid, 8)
            count = header[1]
            _check(header[0] == 1 and 1 <= count <= 15 and header[2:] == b"\0\0\0\0\0\x10"
                   and sid + 8 + count * 4 <= start + size.value,
                   "integrity_unverified", "Integrity SID is invalid.")
            level = int.from_bytes(ctypes.string_at(sid + 8 + (count-1)*4, 4), "little")
            _check(level > 0, "integrity_unverified", "Integrity level is unknown.")
            return level
        finally:
            self.close(token)

    def ownership(self, process, expected, security):
        self.reject_impersonation()
        self.validate_lifetime(process, expected)
        _check(self.current_security_context() == security == self.security_context(process),
               "ownership_changed", "Process user or session changed.")
        name = ctypes.create_unicode_buffer(32768)
        length = wt.DWORD(len(name))
        _check(self._image(process, 0, name, ctypes.byref(length)) and 0 < length.value < len(name),
               "ownership_changed", "Retained process image is unobservable.")
        _check(str(PureWindowsPath(name.value)).casefold() == str(PureWindowsPath(expected["executable"])).casefold(),
               "ownership_changed", "Retained process image path changed.")
        self.window_owner(expected)
        self.validate_lifetime(process, expected)
        return {key: deepcopy(expected[key]) for key in OWNED_KEYS}

    def window_owner(self, expected):
        pid, text = wt.DWORD(), ctypes.create_unicode_buffer(256)
        thread = self._window_pid(expected["hwnd"], ctypes.byref(pid))
        length = self._class(expected["hwnd"], text, len(text))
        _check(thread == expected["thread_id"] and pid.value == expected["pid"]
               and 0 < length < len(text)-1 and text.value == expected["window_class"],
               "ownership_changed", "Original window ownership is no longer observable.")

    def geometry(self, expected):
        hwnd, rect, origin = expected["hwnd"], wt.RECT(), wt.POINT()
        _check(self._visible(hwnd) and not self._iconic(hwnd) and self._rect(hwnd, ctypes.byref(rect))
               and self._origin(hwnd, ctypes.byref(origin)), "geometry_changed", "Original visible geometry is unavailable.")
        return {"client_width": rect.right-rect.left, "client_height": rect.bottom-rect.top,
                "client_origin": [origin.x, origin.y], "dpi": int(self._dpi(hwnd))}

    def foreground(self):
        return int(self._foreground() or 0)

    def layout(self, thread):
        return int(self._layout(thread) or 0)

    def mapping(self, key, layout):
        return int(self._map(key, 4, layout))  # MAPVK_VK_TO_VSC_EX, preserving E0/E1.

    def input_observable(self, expected, foreground, integrity):
        # Zero from GetAsyncKeyState is ambiguous, not independent proof of Up.
        # Establish current/target input desktop and required access first.
        for thread in (int(self._thread_id()), expected["thread_id"]):
            desktop, active, size = self._desktop(thread), wt.BOOL(), wt.DWORD()
            _check(desktop and self._desktop_info(desktop, 6, ctypes.byref(active), ctypes.sizeof(active), ctypes.byref(size))
                   and size.value == ctypes.sizeof(active) and active.value,
                   "input_context_unverified", "Controller and target must use the active input desktop.")
        desktop = self._input_desktop(0, False, 0x19)  # READOBJECTS, HOOKCONTROL, JOURNALRECORD; no hooks installed.
        _check(desktop, "input_context_unverified", "Asynchronous key-state observation access is unavailable.")
        _check(self._close_desktop(desktop), "input_context_unverified", "Cannot close the input-desktop query handle.")
        pid = wt.DWORD()
        _check(self._window_pid(foreground, ctypes.byref(pid)) and pid.value,
               "input_context_unverified", "Foreground ownership is unobservable.")
        process = self.open_target(pid.value)
        try:
            _check(self.integrity(process) == integrity, "input_context_unverified", "Foreground integrity prevents exact key observation.")
        finally:
            self.close(process)

    def key_down(self, key):
        return bool(self._key(key) & 0x8000)  # Never use the unreliable low bit.

    def post(self, hwnd, packet):
        packet = _packet(packet)
        _check(type(hwnd) is int and 0 < hwnd <= 2**64-1 and hwnd != 0xffff,
               "invalid_identity", "Native posting requires one explicit non-broadcast window.")
        return bool(self._post(hwnd, int(packet.message), packet.wparam, packet.lparam))


class _PacketBackend:
    def __init__(self, expected, packets, policy):
        windows.validate_identity(expected)
        _check(type(policy) is not _FixturePolicy and isinstance(policy, _FixturePolicy),
               "input_not_admitted", "Only a trusted Python fixture policy may exercise this private seam.")
        _check(type(packets) is tuple and 1 <= len(packets) <= 129, "invalid_sequence", "A bounded immutable fixture packet plan is required.")
        self._expected = deepcopy(expected)
        self._packets = tuple(_packet(p) for p in packets)
        self._release = _validate_sequence(self._packets, self._expected)
        _check(expected["pid"] <= 0xffffffff and expected["thread_id"] <= 0xffffffff
               and expected["hwnd"] != 0xffff and len(expected["executable"]) <= 32767,
               "invalid_identity", "Unsupported native target identity.")
        self._policy, self._stack, self._used = policy, None, False
        self._held, self._release_attempted, self._uncertain = False, False, False
        self._next, self._last_point = 0, self._packets[0].lparam
        self._owner_thread, self._owner_ident = None, None

    def _hook(self, name, *args):
        start = time.monotonic()
        try:
            result = getattr(self._policy, name)(*args)
        except BaseException:
            _fail("fixture_policy_failed", "Trusted fixture observation failed; external diagnostics were suppressed.")
        _check(0 <= time.monotonic()-start <= MAX_QUERY_SECONDS,
               "fixture_policy_slow", "Trusted fixture observation exceeded its time budget.")
        return result

    def __enter__(self):
        _check(not self._used, "input_context", "Native packet contexts are single-use.")
        self._used = True
        _check(os.name == "nt", "windows_required", "Native packets require Windows; no fallback exists.")
        _check(os.environ.get("GITHUB_ACTIONS") == "true", "input_not_admitted", "Private native fixtures are restricted to disposable Windows CI.")
        # Trusted fixture admission happens before DLL load, handles or mutexes.
        ticket = self._hook("admit", deepcopy(self._expected), tuple(_packet(p) for p in self._packets))
        _check(type(ticket) is _FixtureTicket and type(ticket.recording) is _RecorderSample
               and ticket.window_epoch is not None and ticket.recording.epoch is not None,
               "input_not_admitted", "Fixture admission did not return exact test evidence.")
        self._ticket = _FixtureTicket(ticket.window_epoch, _RecorderSample(
            ticket.recording.epoch, ticket.recording.sequence, ticket.recording.captured_at))
        self._sample = self._ticket.recording
        self._check_sample(self._sample, initial=True)
        self._owner_thread, self._owner_ident = threading.current_thread(), threading.get_ident()
        try:
            with ExitStack() as stack:
                self._api = _NativeApi()
                self._image_pin = self._api.pin_image(self._expected)
                stack.callback(self._api.close, self._image_pin)
                self._lease = stack.enter_context(leases.process_lease(self._expected))
                self._process = self._api.open_target(self._expected["pid"])
                stack.callback(self._api.close, self._process)
                self._security = self._api.current_security_context()
                self._baseline = self._api.foreground()
                _check(type(self._baseline) is int and 0 < self._baseline <= 2**64-1,
                       "unknown_foreground", "An exact nonzero foreground baseline is required.")
                self._layout = self._api.layout(self._expected["thread_id"])
                _check(type(self._layout) is int and self._layout > 0, "input_context_unverified", "Target keyboard layout is unavailable.")
                self._stack = stack.pop_all()
            self.observe()
            self.verify_recording()
            return self
        except BaseException:
            if self._stack is not None:
                stack, self._stack = self._stack, None
                try:
                    stack.close()
                except BaseException:
                    _fail("input_cleanup_unresolved", "Native context preparation cleanup is unverified.")
            _fail("input_preparation_failed", "Native fixture preparation failed before input; external diagnostics were suppressed.")

    def _context(self):
        _check(self._stack is not None, "input_context", "Native packet context is not active.")
        _check(threading.current_thread() is self._owner_thread and threading.get_ident() == self._owner_ident,
               "input_thread", "Native packet ownership must stay on its acquiring thread.")
        try:
            self._lease._check_context()
        except BaseException:
            _fail("input_context", "The original cooperative process lease is no longer held.")

    def _native(self, function, *args):
        try:
            return function(*args)
        except BaseException:
            _fail("native_observation_failed", "Native observation failed; original target state remains unverified.")

    def _owned(self):
        self._context()
        start = time.monotonic()
        _check(self._hook("window_epoch") is self._ticket.window_epoch,
               "ownership_changed", "Original fixture window continuity was lost.")
        result = self._native(self._api.ownership, self._process, self._expected, self._security)
        _check(type(result) is dict and all(result.get(k) == self._expected[k] for k in OWNED_KEYS),
               "ownership_changed", "Original ownership observation did not match.")
        self.integrities()
        _check(0 <= time.monotonic()-start <= MAX_QUERY_SECONDS,
               "stale_observation", "Original ownership observation exceeded its time budget.")
        return deepcopy(result)

    def observe_owned(self):
        return self._owned()

    def observe(self):
        owned = self._owned()
        geometry = self._native(self._api.geometry, self._expected)
        observed = dict(self._expected, **owned, **geometry)
        windows.same_identity(self._expected, observed)
        return deepcopy(observed)

    def integrities(self):
        self._context()
        levels = (self._native(self._api.integrity, self._native(self._api._current_process)),
                  self._native(self._api.integrity, self._process))
        _check(all(type(v) is int and v > 0 for v in levels) and levels[0] == levels[1] == self._expected["integrity"],
               "integrity_mismatch", "Equal known sender and original target integrity is required.")
        return levels

    def foreground(self):
        self._context()
        value = self._native(self._api.foreground)
        _check(type(value) is int and value == self._baseline, "foreground_changed", "The exact foreground baseline changed.")
        return value

    def _check_sample(self, sample, initial=False):
        now = time.monotonic()
        _check(type(sample) is _RecorderSample and sample.epoch is self._ticket.recording.epoch
               and type(sample.sequence) is int and sample.sequence >= 0 and _finite(sample.captured_at)
               and 0 <= now-sample.captured_at <= MAX_RECORDING_AGE_SECONDS,
               "recording_unverified", "Recorder identity, timestamp or freshness is unverified.")
        if not initial:
            prior = self._sample
            _check(sample.sequence >= prior.sequence and sample.captured_at >= prior.captured_at
                   and (sample.sequence != prior.sequence or sample.captured_at == prior.captured_at)
                   and (self._recording_started or sample.sequence > prior.sequence),
                   "recording_unverified", "Recorder progress regressed or was never established.")
            self._recording_started = True
        else:
            self._recording_started = False

    def verify_recording(self):
        self._context()
        sample = self._hook("recorder_sample")
        self._check_sample(sample)
        self._sample = _RecorderSample(sample.epoch, sample.sequence, sample.captured_at)
        return True

    def verify_input_context(self, packet):
        self._context()
        packet = _packet(packet)
        _check(not packet.release_intent and packet in self._packets, "invalid_packet", "Input context requires a bound non-release packet.")
        baseline = self.foreground()
        self._native(self._api.input_observable, self._expected, baseline, self._expected["integrity"])
        layout = self._native(self._api.layout, self._expected["thread_id"])
        _check(type(layout) is int and layout == self._layout, "input_context_unverified", "Target keyboard layout changed.")
        if packet.coordinate_space is CoordinateSpace.NONE:
            scan = self._native(self._api.mapping, packet.wparam, layout)
            _check(type(scan) is int and 0 < scan <= 0xffff and (scan >> 8) in (0, 0xe0)
                   and (scan & 255) == ((packet.lparam >> 16) & 255)
                   and bool(scan >> 8) == bool(packet.lparam & 0x01000000),
                   "input_context_unverified", "Current layout does not match the exact scan byte and E0 status.")
        flags = packet.wparam & 12 if packet.coordinate_space is not CoordinateSpace.NONE else 0
        states = {key: self._native(self._api.key_down, key) for key in (0x10, 0x11, 0x12, 0x5b, 0x5c, 1, 2, 4, 5, 6)}
        _check(all(type(v) is bool for v in states.values()) and states[0x10] == bool(flags & 4)
               and states[0x11] == bool(flags & 8) and not any(states[k] for k in states if k not in (0x10, 0x11)),
               "input_context_unverified", "Actual modifiers or physical buttons differ from the packet context.")
        if packet.coordinate_space is CoordinateSpace.NONE:
            _check(self._native(self._api.key_down, packet.wparam) is False,
                   "input_context_unverified", "The navigation key is already physically held.")
        _check(self._native(self._api.layout, self._expected["thread_id"]) == self._layout,
               "input_context_unverified", "Keyboard layout changed during input observation.")
        self.foreground()
        return True

    def post(self, hwnd, packet):
        self._context()
        packet = _packet(packet)
        _check(type(hwnd) is int and hwnd == self._expected["hwnd"], "ownership_changed", "Only the original bound window may receive a packet.")
        if packet.release_intent:
            _check(self._held and not self._release_attempted, "invalid_release", "No unattempted original release obligation exists.")
            expected = self._release
            _check(expected is not None and packet.message is expected.message and packet.wparam == expected.wparam
                   and packet.offset_ms == expected.offset_ms and packet.coordinate_space is expected.coordinate_space
                   and packet.lparam == (expected.lparam if packet.coordinate_space is CoordinateSpace.NONE else self._last_point),
                   "invalid_release", "Release must exactly match the original held input and last attempted point.")
            self._release_attempted = True  # Consume once even if validation/native call fails.
            self._owned()  # No geometry, recording, foreground, layout, modifiers or disk hash.
        else:
            start = time.monotonic()
            _check(not self._uncertain and not self._release_attempted and self._next < len(self._packets)
                   and packet == self._packets[self._next], "invalid_sequence", "Packet is not the next bound event; replay is forbidden.")
            self.observe()
            self.verify_recording()
            self._owned()  # Recheck after recorder callbacks, without disk work.
            self.verify_input_context(packet)
            self._native(self._api.window_owner, self._expected)
            _check(0 <= time.monotonic()-start <= MAX_QUERY_SECONDS,
                   "stale_observation", "Packet preflight exceeded its time budget.")
            self.foreground()
            if packet.message in (Message.LEFT_DOWN, Message.RIGHT_DOWN, Message.KEY_DOWN):
                self._held = True
            self._last_point = packet.lparam
        try:
            accepted = self._api.post(hwnd, packet)
        except BaseException:
            self._uncertain = True
            _fail("post_unresolved", "Native queue acceptance is unknown; the packet will not be retried.")
        if accepted is not True:
            self._uncertain = True
            if accepted is False and not packet.release_intent and packet.message in (Message.LEFT_DOWN, Message.RIGHT_DOWN, Message.KEY_DOWN):
                self._held = False  # Explicit refusal differs from lost/unknown return.
            if accepted is not False:
                _fail("post_unresolved", "Native queue acceptance is unknown; the packet will not be retried.")
            return False
        if packet.release_intent:
            self._held = False
        self._next += 1
        return True  # Queue acceptance only, never delivery/semantic success.

    def monotonic(self):
        self._context()
        return time.monotonic()

    def sleep(self, seconds):
        self._context()
        _check(_finite(seconds) and 0 <= seconds <= MAX_DURATION_MS/1000,
               "invalid_timing", "Requested packet delay is outside its bounded timing policy.")
        try:
            time.sleep(seconds)
        except BaseException:
            _fail("input_interrupted", "Packet delay was interrupted; original release remains required.")

    @property
    def unresolved(self):
        return self._uncertain or self._held

    def __exit__(self, exc_type, exc, traceback):
        self._context()
        stack, self._stack = self._stack, None
        try:
            stack.close()
        except BaseException:
            self._uncertain = True
            _fail("input_cleanup_unresolved", "Native handle or lease cleanup is unverified; do not reacquire or replay.")
        # Never invent a second cleanup post, and never silently erase obligations.
        if self._held:
            self._uncertain = True
            _fail("release_unresolved", "Original held-input release remains unresolved; no retry was attempted.")
        return False
