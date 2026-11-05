"""Bounded, explicit WGC frame stream for an independently selected window.

This starts only an explicitly pinned FFmpeg helper. It does not send input or
establish semantic UI state, game qualification, or target-window continuity.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
import struct
import subprocess
import threading
import time
import zlib

from .capture_pin import retained_capture_image
from .errors import FoundryError
from .io import plain_path
from .path_identity import ascii_lower
from .windows import inspect, same_identity, validate_identity

MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_CHUNKS = 1024
MAX_FRAME_AGE = 1.0
START_TIMEOUT = 5.0
CLEANUP_TIMEOUT = 2.0
_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_QUARANTINE = set()
_QUARANTINE_LOCK = threading.Lock()


def _retain_unresolved(recorder):
    # Strong ownership prevents finalization from releasing the helper pin
    # while child/worker cleanup is unresolved.
    with _QUARANTINE_LOCK:
        _QUARANTINE.add(recorder)


def _release_resolved(recorder):
    with _QUARANTINE_LOCK:
        _QUARANTINE.discard(recorder)


def _fail(code, message):
    raise FoundryError(code, message) from None


def _read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        chunk = stream.read(size - len(result))
        if type(chunk) is not bytes or not chunk or len(chunk) > size - len(result):
            _fail("recording_stream_invalid", "Recorder stream ended or returned invalid bytes.")
        result.extend(chunk)
    return bytes(result)


def _read_frame(stream):
    """Read one complete bounded PNG with CRCs; do not decompress pixels."""
    signature = _read_exact(stream, 8)
    if signature != _SIGNATURE:
        _fail("recording_stream_invalid", "Recorder frame signature is invalid.")
    result = bytearray(signature)
    shape, image_data = None, False
    for _ in range(MAX_CHUNKS):
        header = _read_exact(stream, 8)
        size, kind = struct.unpack(">I4s", header)
        if size > MAX_FRAME_BYTES - len(result) - 12:
            _fail("recording_stream_invalid", "Recorder frame exceeds its byte bound.")
        body = _read_exact(stream, size + 4)
        payload, crc = body[:-4], struct.unpack(">I", body[-4:])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            _fail("recording_stream_invalid", "Recorder frame CRC is invalid.")
        if shape is None:
            if kind != b"IHDR" or size != 13:
                _fail("recording_stream_invalid", "Recorder frame has no valid header.")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if (not 1 <= width <= 16384 or not 1 <= height <= 16384 or width * height > 16777216
                    or depth != 8 or color not in (0, 2, 4, 6)
                    or compression != 0 or filtering != 0 or interlace != 0):
                _fail("recording_stream_invalid", "Recorder frame format is unsupported.")
            shape = (width, height)
        elif kind == b"IHDR":
            _fail("recording_stream_invalid", "Recorder frame has duplicate headers.")
        if kind == b"IDAT" and size:
            image_data = True
        if kind == b"IEND" and (size or not image_data):
            _fail("recording_stream_invalid", "Recorder frame end is invalid.")
        if kind not in (b"IHDR", b"IDAT", b"IEND", b"PLTE") and not kind[0] & 32:
            _fail("recording_stream_invalid", "Recorder frame has an unsupported critical chunk.")
        result.extend(header)
        result.extend(body)
        if kind == b"IEND":
            return bytes(result), shape
    _fail("recording_stream_invalid", "Recorder frame exceeds its chunk bound.")


@dataclass(frozen=True)
class Frame:
    epoch: object
    sequence: int
    received_at: float
    sha256: str
    width: int
    height: int
    png: bytes


class WgcRecorder:
    """One explicitly started helper, with bounded latest-frame retention."""
    def __init__(self, identity, ffmpeg, ffmpeg_sha256, *, observer=inspect,
                 popen=subprocess.Popen, pin=retained_capture_image, clock=time.monotonic):
        validate_identity(identity)
        self._identity = deepcopy(identity)
        self._ffmpeg = plain_path(ffmpeg)
        if ascii_lower(self._ffmpeg.name) != "ffmpeg.exe":
            _fail("capture_tool_identity", "Select an explicit ffmpeg.exe.")
        self._hash = ffmpeg_sha256
        self._observer, self._popen, self._pin_factory, self._clock = observer, popen, pin, clock
        self._owner, self._owner_id = threading.current_thread(), threading.get_ident()
        self._state, self._pin, self._process, self._worker = "new", None, None, None
        self._pin_active = False
        self._pin_cleanup_failed = False
        self._cleanup_attempted = False
        self._spawn_state = "not_started"
        self._starter, self._start_error = None, False
        self._start_done, self._cancel = threading.Event(), threading.Event()
        self._cleanup_lock = threading.Lock()
        self._lock = threading.Lock()
        self._epoch, self._sequence, self._latest, self._error = object(), 0, None, False

    def _owned(self):
        if threading.current_thread() is not self._owner or threading.get_ident() != self._owner_id:
            _fail("recording_thread", "Recorder control belongs to its original thread.")

    def _observe(self):
        value = self._observer(self._identity["pid"], self._identity["executable"],
                               self._identity["executable_sha256"], self._identity["hwnd"])
        same_identity(self._identity, value)

    def start(self):
        self._owned()
        if self._state != "new":
            _fail("recording_state", "Recorder startup is single-use.")
        self._state = "starting"
        try:
            self._pin = self._pin_factory(str(self._ffmpeg), self._hash)
            self._pin.__enter__()
            self._pin_active = True
            self._observe()
            command = [str(self._ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
                       "-f", "lavfi", "-i",
                       "gfxcapture=hwnd=%s:max_framerate=5:capture_cursor=0:capture_border=0:display_border=0"
                       % self._identity["hwnd"],
                       "-vf", "hwdownload,format=bgra", "-c:v", "png", "-f", "image2pipe", "pipe:1"]
            def spawn():
                try:
                    process = self._popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if process is None or process is False:
                        raise RuntimeError('Recorder spawn returned no original process witness')
                    self._process = process
                    self._spawn_state = "returned"
                    if self._cancel.is_set():
                        with self._cleanup_lock:
                            if not self._cleanup():
                                _retain_unresolved(self)
                except BaseException:
                    self._start_error = True
                    if self._spawn_state != "returned":
                        self._spawn_state = "unresolved"
                    if self._pin_active:
                        _retain_unresolved(self)
                finally:
                    self._start_done.set()
            self._starter = threading.Thread(target=spawn, name="FoundryWgcStart", daemon=True)
            self._spawn_state = "in_progress"
            self._starter.start()
            if not self._start_done.wait(START_TIMEOUT) or self._start_error:
                _fail("recording_start_failed", "Recorder startup timed out or failed.")
            if self._process.stdout is None:
                _fail("recording_start_failed", "Recorder output pipe is unavailable.")
            self._worker = threading.Thread(target=self._pump, name="FoundryWgcFrames", daemon=True)
            self._worker.start()
            self._state = "running"
        except BaseException as error:
            self._cancel.set()
            self._state = "failed"
            if not self.close():
                _fail("recording_cleanup_unresolved", "Recorder startup cleanup is unresolved.")
            if not isinstance(error, Exception):
                raise
            _fail("recording_start_failed", "Recorder could not be started.")

    def _pump(self):
        try:
            while True:
                raw, shape = _read_frame(self._process.stdout)
                now = self._clock()
                with self._lock:
                    self._sequence += 1
                    self._latest = Frame(self._epoch, self._sequence, now, hashlib.sha256(raw).hexdigest(),
                                         shape[0], shape[1], raw)
        except Exception:
            with self._lock:
                self._error = True

    def sample(self):
        self._owned()
        if self._state != "running" or self._process.poll() is not None:
            _fail("recording_unavailable", "Recorder process is not running.")
        self._observe()
        with self._lock:
            frame, error = self._latest, self._error
        now = self._clock()
        if (error or frame is None or type(frame.received_at) not in (int, float)
                or not 0 <= now - frame.received_at <= MAX_FRAME_AGE):
            _fail("recording_unavailable", "No fresh complete recorder frame is available.")
        self._observe()
        with self._lock:
            failed = self._error
        now = self._clock()
        if (failed or self._process.poll() is not None
                or not 0 <= now - frame.received_at <= MAX_FRAME_AGE):
            _fail("recording_unavailable", "Recorder frame became stale during observation.")
        return frame

    def _cleanup(self):
        if self._cleanup_attempted:
            return self._state == "closed"
        self._cleanup_attempted = True
        process = self._process
        if self._spawn_state in ("in_progress", "unresolved") and process is None:
            return False
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=CLEANUP_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=CLEANUP_TIMEOUT)
                if process.poll() is None:
                    return False
            except Exception:
                return False
        if self._worker is not None:
            self._worker.join(timeout=CLEANUP_TIMEOUT)
            if self._worker.is_alive():
                return False
        if self._pin_cleanup_failed:
            return False
        try:
            if process is not None and process.stdout is not None:
                process.stdout.close()
            if self._pin_active:
                self._pin.__exit__(None, None, None)
                self._pin_active = False
                self._pin = None
        except Exception:
            self._pin_cleanup_failed = True
            return False
        self._state = "closed"
        _release_resolved(self)
        return True

    def close(self):
        """Stop only this recorder's child; False means cleanup is unresolved."""
        self._owned()
        if self._state == "closed":
            return True
        self._cancel.set()
        if self._starter is not None and self._starter.is_alive():
            self._starter.join(timeout=CLEANUP_TIMEOUT)
            if self._starter.is_alive():
                with self._cleanup_lock:
                    if self._state == "closed":
                        return True
                    _retain_unresolved(self)
                    return False
        with self._cleanup_lock:
            try:
                result = self._cleanup()
            except BaseException:
                _retain_unresolved(self)
                raise
            if not result:
                _retain_unresolved(self)
            return result
