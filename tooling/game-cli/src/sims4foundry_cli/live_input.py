"""Explicit non-Apex Windows packet sender for a retained selected process.

Queue acceptance is not delivery or semantic success. No input is sent during
construction or context entry. A caller must use a durable input journal and
independently verify the UI result. This path has not been qualified in game.
"""
from contextlib import ExitStack
from copy import deepcopy
import math
import os
import threading
import time

from . import process_lease, windows
from .errors import FoundryError
from .guarded_input import _validate_sequence
from .input_packets import CoordinateSpace, InputPacket, Message, MAX_DURATION_MS
from .native_input import _NativeApi, _packet, OWNED_KEYS
from .wgc_recorder import Frame, WgcRecorder

MAX_FRAME_AGE = 1.0
MAX_START_SECONDS = 8.0
MAX_POST_PREFLIGHT = .25


def _require(value, code, message):
    if not value:
        raise FoundryError(code, message) from None


class LiveInputBackend:
    """Single-use sender bound to exact packets, target and WGC recorder.

    Process lifetime is retained and the HWND owner/class/thread is rechecked,
    but Windows supplies no retained HWND lifetime handle here: destruction and
    recreation between observations cannot be excluded. Received frame time is
    not independently verified game capture time.
    """
    def __init__(self, expected, packets, ffmpeg, ffmpeg_sha256):
        windows.validate_identity(expected)
        _require(type(packets) is tuple and 1 <= len(packets) <= 129,
                 "invalid_sequence", "An exact bounded packet tuple is required.")
        self._expected = deepcopy(expected)
        self._packets = tuple(_packet(packet) for packet in packets)
        self._release = _validate_sequence(self._packets, self._expected)
        _require(type(ffmpeg) is str and bool(ffmpeg) and type(ffmpeg_sha256) is str
                 and len(ffmpeg_sha256) == 64
                 and all(c in "0123456789abcdef" for c in ffmpeg_sha256),
                 "capture_tool_identity", "An explicit pinned FFmpeg helper is required.")
        self._ffmpeg, self._ffmpeg_sha256 = ffmpeg, ffmpeg_sha256
        self._stack, self._recorder, self._used = None, None, False
        self._owner, self._owner_id = None, None
        self._held, self._release_attempted, self._uncertain = False, False, False
        self._next, self._last_point = 0, self._packets[0].lparam
        self._frame_epoch, self._frame_sequence, self._frame_time = None, None, None

    def _context(self):
        _require(self._stack is not None, "input_context", "The original input context is not held.")
        _require(threading.current_thread() is self._owner and threading.get_ident() == self._owner_id,
                 "input_thread", "Input ownership belongs to its original thread.")
        self._lease._check_context()

    def __enter__(self):
        _require(not self._used, "input_context", "Input contexts are single-use.")
        self._used = True
        _require(os.name == "nt", "windows_required", "Native input requires Windows.")
        self._owner, self._owner_id = threading.current_thread(), threading.get_ident()
        try:
            with ExitStack() as stack:
                self._api = _NativeApi()
                image = self._api.pin_image(self._expected)
                stack.callback(self._api.close, image)
                self._lease = stack.enter_context(process_lease.process_lease(self._expected))
                self._process = self._api.open_target(self._expected["pid"])
                stack.callback(self._api.close, self._process)
                self._security = self._api.current_security_context()
                self._baseline = self._api.foreground()
                _require(type(self._baseline) is int and 0 < self._baseline <= 2**64-1,
                         "unknown_foreground", "A known foreground baseline is required.")
                self._layout = self._api.layout(self._expected["thread_id"])
                _require(type(self._layout) is int and self._layout > 0,
                         "input_context_unverified", "Target keyboard layout is unavailable.")
                self._stack = stack.pop_all()
            self.observe()
            self._recorder = WgcRecorder(self._expected, self._ffmpeg, self._ffmpeg_sha256,
                                         observer=lambda *args:self.observe())
            self._recorder.start()
            deadline = time.monotonic() + MAX_START_SECONDS
            first = self._wait_frame(deadline, after=0)
            second = self._wait_frame(deadline, after=first.sequence)
            _require(second.epoch is first.epoch, "recording_unverified", "Recorder epoch changed.")
            self._frame_epoch, self._frame_sequence, self._frame_time = second.epoch, second.sequence, second.received_at
            return self
        except BaseException as error:
            clean = self._cleanup()
            if not clean:
                raise FoundryError("input_cleanup_unresolved", "Input setup cleanup could not be confirmed.") from None
            if not isinstance(error, Exception):
                raise
            raise FoundryError("input_preparation_failed", "Input setup failed before dispatch.") from None

    def _wait_frame(self, deadline, after):
        while time.monotonic() < deadline:
            try:
                frame = self._recorder.sample()
                if type(frame) is Frame and type(frame.sequence) is int and frame.sequence > after:
                    return frame
            except FoundryError as error:
                if error.code != "recording_unavailable":
                    raise
            time.sleep(min(.05, max(0.0, deadline - time.monotonic())))
        raise FoundryError("recording_unverified", "An advancing frame stream was not observed.")

    def observe_owned(self):
        self._context()
        result = self._api.ownership(self._process, self._expected, self._security)
        _require(type(result) is dict and all(result.get(key) == self._expected[key] for key in OWNED_KEYS),
                 "ownership_changed", "The original process/window binding changed.")
        self.integrities()
        return deepcopy(result)

    def observe(self):
        owned = self.observe_owned()
        result = dict(self._expected, **owned, **self._api.geometry(self._expected))
        windows.same_identity(self._expected, result)
        return deepcopy(result)

    def integrities(self):
        self._context()
        levels = (self._api.integrity(self._api._current_process()), self._api.integrity(self._process))
        _require(type(levels) is tuple and len(levels) == 2
                 and all(type(value) is int and value > 0 for value in levels)
                 and levels[0] == levels[1] == self._expected["integrity"],
                 "integrity_mismatch", "Equal known sender and target integrity is required.")
        return levels

    def foreground(self):
        self._context()
        value = self._api.foreground()
        _require(type(value) is int and value == self._baseline,
                 "foreground_changed", "The foreground baseline changed.")
        return value

    def verify_recording(self):
        self._context()
        frame = self._recorder.sample()
        now = time.monotonic()
        _require(type(frame) is Frame and frame.epoch is self._frame_epoch
                 and type(frame.sequence) is int and frame.sequence >= self._frame_sequence
                 and type(frame.received_at) in (int,float) and math.isfinite(frame.received_at)
                 and self._frame_time <= frame.received_at <= now
                 and now-frame.received_at <= MAX_FRAME_AGE
                 and (frame.sequence != self._frame_sequence or frame.received_at == self._frame_time),
                 "recording_unverified", "Recorder sequence or receipt freshness is unavailable.")
        self._frame_sequence, self._frame_time = frame.sequence, frame.received_at
        return True

    def verify_input_context(self, packet):
        self._context()
        packet = _packet(packet)
        _require(not packet.release_intent and packet in self._packets,
                 "invalid_packet", "Only a bound non-release packet can be checked.")
        self._api.input_observable(self._expected, self.foreground(), self._expected["integrity"])
        layout = self._api.layout(self._expected["thread_id"])
        _require(type(layout) is int and layout == self._layout,
                 "input_context_unverified", "Target keyboard layout changed.")
        if packet.coordinate_space is CoordinateSpace.NONE:
            scan = self._api.mapping(packet.wparam, layout)
            _require(type(scan) is int and 0 < scan <= 0xffff and (scan >> 8) in (0,0xe0)
                     and (scan & 255) == ((packet.lparam >> 16) & 255)
                     and bool(scan >> 8) == bool(packet.lparam & 0x01000000),
                     "input_context_unverified", "Keyboard layout does not match the scan code.")
        flags = packet.wparam & 12 if packet.coordinate_space is not CoordinateSpace.NONE else 0
        states = {key:self._api.key_down(key) for key in (0x10,0x11,0x12,0x5b,0x5c,1,2,4,5,6)}
        _require(all(type(value) is bool for value in states.values())
                 and states[0x10] == bool(flags & 4) and states[0x11] == bool(flags & 8)
                 and not any(states[key] for key in states if key not in (0x10,0x11)),
                 "input_context_unverified", "Modifiers or physical buttons differ from the packet.")
        if packet.coordinate_space is CoordinateSpace.NONE:
            _require(self._api.key_down(packet.wparam) is False,
                     "input_context_unverified", "Navigation key is already physically held.")
        _require(self._api.layout(self._expected["thread_id"]) == self._layout,
                 "input_context_unverified", "Target keyboard layout changed.")
        self.foreground()
        return True

    def post(self, hwnd, packet):
        self._context()
        packet = _packet(packet)
        _require(type(hwnd) is int and hwnd == self._expected["hwnd"],
                 "ownership_changed", "Only the original bound window can receive input.")
        if packet.release_intent:
            _require(self._held and not self._release_attempted, "invalid_release", "No original release remains.")
            expected = self._release
            _require(expected is not None and packet.message is expected.message and packet.wparam == expected.wparam
                     and packet.offset_ms == expected.offset_ms and packet.coordinate_space is expected.coordinate_space
                     and packet.lparam == (expected.lparam if packet.coordinate_space is CoordinateSpace.NONE else self._last_point),
                     "invalid_release", "Release must match the original input and last attempted point.")
            self._release_attempted = True
            self.observe_owned()
        else:
            start = time.monotonic()
            _require(not self._uncertain and not self._release_attempted and self._next < len(self._packets)
                     and packet == self._packets[self._next],
                     "invalid_sequence", "Only the next original packet can be sent.")
            self.observe()
            self.verify_recording()
            self.observe_owned()
            self.verify_input_context(packet)
            self._api.window_owner(self._expected)
            _require(0 <= time.monotonic()-start <= MAX_POST_PREFLIGHT,
                     "stale_observation", "Packet preflight exceeded its time budget.")
            self.foreground()
            _require(0 <= time.monotonic()-start <= MAX_POST_PREFLIGHT,
                     "stale_observation", "Final foreground check exceeded the preflight time budget.")
            if packet.message in (Message.LEFT_DOWN,Message.RIGHT_DOWN,Message.KEY_DOWN):
                self._held = True
            self._last_point = packet.lparam
        try:
            accepted = self._api.post(hwnd, packet)
        except BaseException:
            self._uncertain = True
            raise FoundryError("post_unresolved", "Packet acceptance is unknown; do not replay it.") from None
        if accepted is not True:
            self._uncertain = True
            if accepted is False and not packet.release_intent and packet.message in (Message.LEFT_DOWN,Message.RIGHT_DOWN,Message.KEY_DOWN):
                self._held = False
            _require(accepted is False, "post_unresolved", "Packet acceptance is unknown.")
            return False
        if packet.release_intent:
            self._held = False
        self._next += 1
        return True

    def monotonic(self):
        self._context()
        return time.monotonic()

    def sleep(self, seconds):
        self._context()
        _require(type(seconds) in (int,float) and math.isfinite(seconds)
                 and 0 <= seconds <= MAX_DURATION_MS/1000,
                 "invalid_timing", "Packet delay is outside its bound.")
        time.sleep(seconds)

    @property
    def unresolved(self):
        return self._uncertain or self._held

    def _cleanup(self):
        clean = True
        if self._recorder is not None:
            try:
                if self._recorder.close() is not True:
                    clean = False
            except BaseException:
                clean = False
        if self._stack is not None:
            stack, self._stack = self._stack, None
            try:
                stack.close()
            except BaseException:
                clean = False
        return clean

    def __exit__(self, exc_type, exc, traceback):
        self._context()
        clean = self._cleanup()
        if not clean:
            self._uncertain = True
            raise FoundryError("input_cleanup_unresolved", "Input context cleanup could not be confirmed.")
        if self._held:
            self._uncertain = True
            raise FoundryError("release_unresolved", "Original release remains unresolved; do not replay.")
