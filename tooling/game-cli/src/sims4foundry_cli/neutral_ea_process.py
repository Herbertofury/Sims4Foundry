"""Read-only identity of one explicit EA Desktop process lifetime."""
import ctypes
from ctypes import wintypes
import os
import hashlib
import re
import stat
import time
from pathlib import Path

from .errors import FoundryError
from .io import plain_path
from .launch import _same_open_source, _stamp
from .path_identity import same_windows_path
from .windows import _bind


_read = os.read
_open = os.open
_fstat = os.fstat
_lstat = lambda path: path.lstat()

MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024


def _check_deadline(deadline, monotonic):
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError('EA operation deadline expired.')


def _file_key(info):
    return _stamp(info)


def stable_executable(path, *, deadline=None, monotonic=None):
    """Hash only one admitted regular-file extent, rejecting growth/replacement.

    This is a byte bound, not a guarantee about a filesystem driver's latency.
    The no-follow/nonblocking open flags are used where the OS provides them.
    """
    monotonic = monotonic or time.monotonic
    _check_deadline(deadline, monotonic)
    path = plain_path(path)
    before = _lstat(path)
    if (not stat.S_ISREG(before.st_mode) or not before.st_dev or not before.st_ino
            or not 1 <= before.st_size <= MAX_EXECUTABLE_BYTES):
        raise FoundryError('ea_image_bound', 'EA executable must be a bounded stable regular file.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = _open(path, flags)
    try:
        path_stamp = _file_key(before)
        descriptor_stamp = _file_key(_fstat(fd))
        if not _same_open_source(path_stamp, descriptor_stamp, path):
            raise FoundryError('ea_image_changed', 'EA executable changed before it was read.')
        hashed = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            _check_deadline(deadline, monotonic)
            chunk = _read(fd, min(1024 * 1024, remaining))
            if not chunk:
                raise FoundryError('ea_image_changed', 'EA executable shrank during hashing.')
            hashed.update(chunk)
            remaining -= len(chunk)
        _check_deadline(deadline, monotonic)
        if _read(fd, 1) or _file_key(_fstat(fd)) != descriptor_stamp:
            raise FoundryError('ea_image_changed', 'EA executable grew or changed during hashing.')
        if _file_key(_lstat(plain_path(path))) != path_stamp:
            raise FoundryError('ea_image_changed', 'EA executable path changed during hashing.')
        _check_deadline(deadline, monotonic)
        return {'sha256':hashed.hexdigest(), 'identity':path_stamp}
    finally:
        os.close(fd)


class _WindowsApi:
    def __init__(self):
        if os.name != 'nt':
            raise FoundryError('windows_required', 'EA process observation requires Windows.')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self._open = _bind(kernel, 'OpenProcess', wintypes.HANDLE,
                           [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD])
        self._close = _bind(kernel, 'CloseHandle', wintypes.BOOL, [wintypes.HANDLE])
        self._wait = _bind(kernel, 'WaitForSingleObject', wintypes.DWORD,
                           [wintypes.HANDLE,wintypes.DWORD])
        self._times = _bind(kernel, 'GetProcessTimes', wintypes.BOOL,
                            [wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4)
        self._image = _bind(kernel, 'QueryFullProcessImageNameW', wintypes.BOOL,
                            [wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)])

    def open(self, pid):
        handle = self._open(0x1000 | 0x100000, False, pid)
        if not handle:
            raise FoundryError('ea_process_unavailable', 'Cannot inspect the selected EA process.',
                               windows_error=ctypes.get_last_error())
        return handle

    def live(self, handle):
        return self._wait(handle, 0) == 0x102

    def created(self, handle):
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not self._times(handle, ctypes.byref(created), ctypes.byref(exited),
                           ctypes.byref(kernel), ctypes.byref(user)):
            raise FoundryError('ea_process_unavailable', 'Cannot inspect EA process lifetime.')
        return (created.dwHighDateTime << 32) | created.dwLowDateTime

    def image(self, handle):
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not self._image(handle, 0, buf, ctypes.byref(size)) or not 0 < size.value < 32768:
            raise FoundryError('ea_process_unavailable', 'Cannot inspect EA process image.')
        return buf.value

    def close(self, handle):
        if not self._close(handle):
            raise FoundryError('ea_process_unavailable', 'Cannot close EA process observation handle.')


def inspect(pid, expected_image, expected_sha256, *, api=None, deadline=None, monotonic=None):
    if type(pid) is not int or not 1 <= pid <= 0xffffffff:
        raise FoundryError('ea_process_identity', 'Expected one exact EA process ID.')
    monotonic = monotonic or time.monotonic
    _check_deadline(deadline, monotonic)
    if type(expected_sha256) is not str or re.fullmatch(r'[0-9a-f]{64}', expected_sha256) is None:
        raise FoundryError('ea_process_identity', 'Expected an exact EA executable SHA-256.')
    lexical = Path(os.path.abspath(expected_image))
    if lexical.name.casefold() != 'eadesktop.exe':
        raise FoundryError('ea_process_identity', 'Expected the selected EADesktop.exe.')
    resolved = plain_path(lexical.resolve(strict=True))
    snapshot = stable_executable(resolved, deadline=deadline, monotonic=monotonic)
    if snapshot['sha256'] != expected_sha256:
        raise FoundryError('ea_process_identity', 'EA image did not match the selected hash.')
    api = api or _WindowsApi()
    _check_deadline(deadline, monotonic)
    handle = api.open(pid)
    try:
        if api.live(handle) is not True:
            raise FoundryError('ea_process_exited', 'The selected EA process is not running.')
        created = api.created(handle)
        image = api.image(handle)
        if (type(created) is not int or not 1 <= created <= 2**64-1
                or type(image) is not str
                or not same_windows_path(str(Path(image).resolve(strict=True)), str(resolved))
                or api.live(handle) is not True
                or plain_path(lexical.resolve(strict=True)) != resolved
                or stable_executable(resolved, deadline=deadline, monotonic=monotonic) != snapshot):
            raise FoundryError('ea_process_identity', 'EA process lifetime or image changed.')
        _check_deadline(deadline, monotonic)
        return {'pid':pid, 'creation_filetime':created, 'image':image,
                'resolved_image':str(resolved), 'image_sha256':expected_sha256}
    finally:
        api.close(handle)
