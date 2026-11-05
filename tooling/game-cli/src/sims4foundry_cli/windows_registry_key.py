"""Explicit user-scoped DPAPI key material for cooperative registry clients.

DPAPI generally limits decryption to this Windows user and machine. It does not
authenticate programs running as that user, protect a rollbackable anchor, or
erase Python memory. Construction of this module makes no OS or file changes.
"""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import os
import re
import secrets

from . import companion_build
from .errors import FoundryError
from .io import external_path, loads, new_json

SCOPE = re.compile(r'[0-9a-f]{64}\Z')
TAG = b'Foundry session registry key v1\0'


class _Blob(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _transform(data, entropy, protect):
    if os.name != 'nt':
        raise FoundryError('windows_required', 'User-scoped key protection requires Windows.')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    blobp = ctypes.POINTER(_Blob)
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.argtypes = [blobp, wintypes.LPCWSTR if protect else ctypes.c_void_p, blobp,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blobp]
    fn.restype = wintypes.BOOL
    free = kernel.LocalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    source_buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    entropy_buf = (ctypes.c_ubyte * len(entropy)).from_buffer_copy(entropy)
    source = _Blob(len(data), source_buf)
    entropy_blob = _Blob(len(entropy), entropy_buf)
    result = _Blob()
    ok = fn(ctypes.byref(source), None, ctypes.byref(entropy_blob), None, None, 1, ctypes.byref(result))
    if not ok:
        raise FoundryError('key_protection_failed', 'Windows user-scoped key protection failed.')
    try:
        if not result.data or not 0 < result.length <= 65536:
            raise FoundryError('key_protection_failed', 'Protected key output is invalid.')
        return ctypes.string_at(result.data, result.length)
    finally:
        if result.data:
            if 0 < result.length <= 65536:
                ctypes.memset(result.data, 0, result.length)
            if free(result.data):
                raise FoundryError('key_cleanup_unresolved', 'Windows key buffer cleanup could not be confirmed.')


def _scope(value):
    if type(value) is not str or not SCOPE.fullmatch(value):
        raise FoundryError('key_scope', 'An exact registry scope digest is required.')
    return bytes.fromhex(value)


def create_key(path, scope):
    """Create one new user-protected key file; never replace an existing file."""
    scope_bytes = _scope(scope)
    destination = external_path(path)
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise FoundryError('key_exists', 'Select a new key file in an existing directory.')
    key = secrets.token_bytes(32)
    payload = TAG + key + hashlib.sha256(TAG + scope_bytes + key).digest()
    protected = _transform(payload, scope_bytes, True)
    new_json(destination, {'schema':1, 'scope':scope, 'protected':base64.b64encode(protected).decode('ascii')})
    return key


def load_key(path, scope):
    """Open the original key for this Windows user; never generate a replacement."""
    scope_bytes = _scope(scope)
    raw, _, _ = companion_build._regular(external_path(path), 65536)
    value = loads(raw)
    if (type(value) is not dict or set(value) != {'schema','scope','protected'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['scope'] != scope or type(value['protected']) is not str):
        raise FoundryError('key_invalid', 'Protected key record is invalid.')
    try:
        protected = base64.b64decode(value['protected'], validate=True)
    except (ValueError, TypeError):
        raise FoundryError('key_invalid', 'Protected key encoding is invalid.') from None
    if not 0 < len(protected) <= 65536:
        raise FoundryError('key_invalid', 'Protected key data is invalid.')
    payload = _transform(protected, scope_bytes, False)
    if (len(payload) != len(TAG) + 64 or not payload.startswith(TAG)
            or not hmac.compare_digest(payload[-32:], hashlib.sha256(TAG + scope_bytes + payload[len(TAG):-32]).digest())):
        raise FoundryError('key_invalid', 'Protected key payload failed integrity validation.')
    return payload[len(TAG):-32]
