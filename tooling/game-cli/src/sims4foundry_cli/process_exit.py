"""Observe one pinned process lifetime ending, without requesting an exit.

A signalled retained process handle proves termination, not normal shutdown,
saving, CAS acceptance or persistence. A missing/inaccessible PID does not prove
termination. No enumeration, fallback, input, elevation or termination API.
"""
import ctypes
from ctypes import wintypes as wt
import math
import os
from pathlib import PureWindowsPath
import time

from . import launch, windows
from .errors import FoundryError
from .io import exact_int, finite_number, plain_path


MAX_SECONDS = 60.0
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
WAIT_FAILED = 0xffffffff


class _WindowsApi:
    """Query-only handle binding; tests may supply an owned fixture implementation."""
    def __init__(self):
        if os.name != 'nt':
            raise FoundryError('windows_required', 'Exit observation requires Windows.')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        bind = windows._bind
        self._open = bind(kernel, 'OpenProcess', wt.HANDLE, [wt.DWORD, wt.BOOL, wt.DWORD])
        self._close = bind(kernel, 'CloseHandle', wt.BOOL, [wt.HANDLE])
        self._times = bind(kernel, 'GetProcessTimes', wt.BOOL,
                           [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4)
        self._image = bind(kernel, 'QueryFullProcessImageNameW', wt.BOOL,
                           [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)])
        self._wait = bind(kernel, 'WaitForSingleObject', wt.DWORD, [wt.HANDLE, wt.DWORD])

    @staticmethod
    def _require(value, message):
        if not value:
            raise FoundryError('exit_observation_unavailable', message,
                               windows_error=ctypes.get_last_error(),
                               process_exit_verified=False)

    def open(self, pid):
        handle = self._open(0x1000 | 0x100000, False, pid)
        self._require(handle, 'Cannot retain this exact process; absence or denied access is not exit proof.')
        return handle

    def identity(self, handle):
        created, exited, kernel, user = (wt.FILETIME() for _ in range(4))
        self._require(self._times(handle, ctypes.byref(created), ctypes.byref(exited),
                                 ctypes.byref(kernel), ctypes.byref(user)),
                      'Cannot read the retained process creation time.')
        # Exit FILETIME is undefined before termination. Do not read it here.
        capacity = wt.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        self._require(self._image(handle, 0, buffer, ctypes.byref(capacity)),
                      'Cannot read the retained process image path.')
        if not 1 <= capacity.value < len(buffer) or len(buffer.value) != capacity.value:
            raise FoundryError('exit_observation_unavailable', 'Malformed process image observation.')
        return buffer.value, (created.dwHighDateTime << 32) | created.dwLowDateTime

    def wait(self, handle, milliseconds):
        result = self._wait(handle, milliseconds)
        if result == WAIT_FAILED:
            self._require(False, 'Cannot observe the retained process handle state.')
        return result

    def close(self, handle):
        self._require(self._close(handle), 'Cannot confirm the observation handle was released.')


def _same_image(first, second):
    # Unicode casefold can expand distinct Windows filenames (e.g. ß -> ss).
    # Permit only ASCII case differences; non-ASCII spelling must match exactly.
    # This can conservatively refuse an equivalent Unicode case variant, but
    # cannot broaden a selected path through casefold expansion.
    lower_ascii = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
    return (str(PureWindowsPath(first)).translate(lower_ascii)
            == str(PureWindowsPath(second)).translate(lower_ascii))


def _wait_retained(pid, executable, creation_filetime, seconds, api, monotonic):
    """Single-handle primitive, also tested against newly owned Python children."""
    started = monotonic()
    deadline = started + seconds
    handle = api.open(pid)
    try:
        image, created = api.identity(handle)
        if (type(image) is not str or not _same_image(image, executable)
                or type(created) is not int or created != creation_filetime):
            raise FoundryError('process_lifetime_changed',
                               'The PID does not match the exact expected image and creation time.',
                               process_exit_verified=False)
        waits = 0
        while True:
            remaining = max(0.0, deadline - monotonic())
            # Short finite waits permit checking a single monotonic deadline.
            milliseconds = min(250, max(0, math.floor(remaining * 1000)))
            result = api.wait(handle, milliseconds)
            waits += 1
            elapsed = max(0.0, monotonic() - started)
            if type(result) is not int:
                raise FoundryError('exit_observation_unavailable', 'Process wait returned an invalid type.')
            if result == WAIT_OBJECT_0:
                return True, elapsed, waits
            if result != WAIT_TIMEOUT:
                raise FoundryError('exit_observation_unavailable',
                                   'Unexpected process wait result; exit is unverified.',
                                   wait_result=result, process_exit_verified=False)
            if elapsed >= seconds or milliseconds == 0:
                return False, elapsed, waits
    finally:
        # A cleanup failure must not be hidden behind a successful observation.
        api.close(handle)


def observe_exit(identity, seconds=30.0):
    """Observe only: caller must separately own and authorize any quit request.

    Windows API and file-access failures stop this call. It does not retry with
    another process query, inspect a profile, send quit or kill a process.
    """
    if not finite_number(seconds, 0, MAX_SECONDS):
        raise FoundryError('invalid_timeout', 'Exit observation accepts zero through sixty seconds.')
    windows.validate_identity(identity)
    pid, created = identity['pid'], identity['creation_filetime']
    executable, checksum = identity['executable'], identity['executable_sha256']
    if not exact_int(pid, 1, 0xffffffff):
        raise FoundryError('invalid_identity', 'The selected PID must fit a Windows DWORD.')
    if not _same_image(PureWindowsPath(executable).name, 'TS4_x64.exe'):
        raise FoundryError('invalid_identity', 'Expected the exact Sims executable basename.')
    # Capture primitive values before invoking any filesystem/native callback.
    api = _WindowsApi()
    path = plain_path(executable)
    stamp = launch._source_stat(path, 'executable_bound', launch.MAX_BINARY_BYTES)
    actual, _, _ = launch._snapshot(path, stamp, 'installation_changed')
    if actual != checksum:
        raise FoundryError('wrong_executable', 'The executable no longer matches the expected SHA-256.')
    ended, elapsed, waits = _wait_retained(pid, executable, created, float(seconds), api, time.monotonic)
    return {'ok': ended, 'schema': 1, 'kind': 'foundry_process_exit_observation',
            'outcome': 'process_terminated' if ended else 'unresolved',
            'pid': pid, 'creation_filetime': created,
            'executable_sha256': checksum, 'process_exit_verified': ended,
            'normal_exit_verified': False, 'save_verified': False,
            'input_sent': False, 'quit_requested': False, 'termination_requested': False,
            'profile_accessed': False, 'apex_required': False,
            'observation_limit_seconds': float(seconds), 'elapsed_seconds': elapsed,
            'wait_observations': waits,
            'scope': 'Exact retained process handle only. Exit cause and save policy are not observed.'}
