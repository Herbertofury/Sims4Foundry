"""Retain a Windows capture-helper image and its path until a child exits.

Opening occurs only on explicit use. This pins an executable file, not its DLLs,
plugins, configuration, process identity or behavior.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
import hashlib
import os
from pathlib import PureWindowsPath
import re

from .errors import FoundryError

MAX_IMAGE_BYTES = 256 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class _FileInfo(ctypes.Structure):
    _fields_ = [("attributes", wt.DWORD), ("created", wt.FILETIME), ("accessed", wt.FILETIME),
                ("written", wt.FILETIME), ("volume", wt.DWORD), ("size_high", wt.DWORD),
                ("size_low", wt.DWORD), ("links", wt.DWORD), ("index_high", wt.DWORD),
                ("index_low", wt.DWORD)]


def _fail():
    raise FoundryError("capture_tool_identity", "Capture helper or its path could not be pinned.") from None


class _WindowsApi:
    def __init__(self):
        if os.name != "nt":
            raise FoundryError("windows_required", "Capture helper pinning requires Windows.")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        def bind(name, result, args):
            function = getattr(kernel, name)
            function.restype, function.argtypes = result, args
            return function
        self._drive = bind("GetDriveTypeW", wt.UINT, [wt.LPCWSTR])
        self._open = bind("CreateFileW", wt.HANDLE,
                          [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE])
        self._type = bind("GetFileType", wt.DWORD, [wt.HANDLE])
        self._info = bind("GetFileInformationByHandle", wt.BOOL, [wt.HANDLE, ctypes.POINTER(_FileInfo)])
        self._read = bind("ReadFile", wt.BOOL,
                          [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p])
        self._close = bind("CloseHandle", wt.BOOL, [wt.HANDLE])

    def fixed_drive(self, root):
        return self._drive(root) == 3

    def open_directory(self, path):
        # FILE_READ_ATTRIBUTES, read/write sharing but no delete sharing;
        # OPEN_REPARSE_POINT | BACKUP_SEMANTICS opens the directory itself.
        return self._open(path, 0x80, 3, None, 3, 0x02200000, None)

    def open_image(self, path):
        # GENERIC_READ, FILE_SHARE_READ only: no write/delete sharing.
        return self._open(path, 0x80000000, 1, None, 3, 0x00200000, None)

    def metadata(self, handle):
        info = _FileInfo()
        if self._type(handle) != 1 or not self._info(handle, ctypes.byref(info)):
            _fail()
        return {"attributes":int(info.attributes), "size":(int(info.size_high) << 32) | int(info.size_low),
                "links":int(info.links)}

    def read(self, handle, size):
        buffer = ctypes.create_string_buffer(size)
        count = wt.DWORD()
        if not self._read(handle, buffer, size, ctypes.byref(count), None) or count.value > size:
            _fail()
        return buffer.raw[:count.value]

    def close(self, handle):
        return bool(self._close(handle))


def _valid_handle(value):
    return value not in (None, 0, -1, ctypes.c_void_p(-1).value)


@contextmanager
def _retain(path, expected_hash, api):
    """Private API seam for fabricated unit fixtures and Windows binding."""
    value = PureWindowsPath(path)
    if (not value.is_absolute() or len(value.drive) != 2 or value.drive[1] != ":"
            or not value.drive[0].isalpha() or len(value.parts) < 2
            or any(part in (".", "..") or ":" in part for part in value.parts[1:])
            or value.name.casefold() != "ffmpeg.exe" or type(expected_hash) is not str
            or _HASH.fullmatch(expected_hash) is None or not api.fixed_drive(value.anchor)):
        _fail()
    handles = []
    try:
        parent = PureWindowsPath(value.anchor)
        for part in value.parts[1:-1]:
            parent /= part
            handle = api.open_directory(str(parent))
            if not _valid_handle(handle):
                _fail()
            handles.append(handle)
            info = api.metadata(handle)
            if not info["attributes"] & 0x10 or info["attributes"] & 0x400:
                _fail()
        handle = api.open_image(str(value))
        if not _valid_handle(handle):
            _fail()
        handles.append(handle)
        info = api.metadata(handle)
        size = info["size"]
        if (info["attributes"] & (0x10 | 0x400) or info["links"] != 1
                or type(size) is not int or not 0 < size <= MAX_IMAGE_BYTES):
            _fail()
        hashed, remaining = hashlib.sha256(), size
        while remaining:
            chunk = api.read(handle, min(CHUNK_BYTES, remaining))
            if type(chunk) is not bytes or not 0 < len(chunk) <= min(CHUNK_BYTES, remaining):
                _fail()
            hashed.update(chunk)
            remaining -= len(chunk)
        if api.read(handle, 1) != b"" or hashed.hexdigest() != expected_hash:
            _fail()
        yield
    finally:
        closed = True
        for handle in reversed(handles):
            try:
                if api.close(handle) is not True:
                    closed = False
            except Exception:
                closed = False
        if not closed:
            raise FoundryError("capture_pin_cleanup_failed",
                               "Capture helper pin cleanup could not be confirmed.")


def retained_capture_image(path, expected_hash):
    return _retain(path, expected_hash, _WindowsApi())
