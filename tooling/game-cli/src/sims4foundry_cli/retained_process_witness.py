"""Query-only, provider-owned retained-process witness; no UI or game access.

The caller supplies an API implementing Foundry process_exit._WindowsApi and an
executable verifier. This registry is an in-process boundary, not a service,
authentication policy, durable handle store, or authorization to inspect a
process. It never reopens for witness recovery and never kills a process.
"""
from contextlib import contextmanager
from copy import deepcopy
import math
from pathlib import PureWindowsPath
import re
import threading
import time

from .errors import FoundryError
from .io import canonical
from .process_exit import WAIT_OBJECT_0, WAIT_TIMEOUT, _same_image
from .windows import same_identity, validate_identity

_HEX32 = re.compile(r'[0-9a-f]{32}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_FIELDS = {'pid', 'creation_filetime', 'executable', 'executable_sha256'}
_MAX_WAIT_OBSERVATIONS = 256


def _identity(value):
    if (type(value) is not dict or set(value) != _FIELDS or
            type(value['pid']) is not int or not 0 < value['pid'] <= 0xffffffff or
            type(value['creation_filetime']) is not int or not 0 < value['creation_filetime'] < 2**64 or
            type(value['executable']) is not str or not PureWindowsPath(value['executable']).is_absolute() or
            type(value['executable_sha256']) is not str or not _HEX64.fullmatch(value['executable_sha256'])):
        raise FoundryError('invalid_process_identity', 'An exact process lifetime, path and hash are required.')
    return deepcopy(value)


class Lease:
    """Opaque reference to the same provider-owned handle, not an OS proof."""
    def __init__(self, registry, token):
        self._registry, self._token = registry, token

    def snapshot(self):
        return self._registry._snapshot(self._token)

    def is_running(self):
        return self._registry._running(self._token)

    def wait_exit(self, seconds):
        return self._registry._wait_exit(self._token, seconds)

    def close(self):
        return self._registry._close(self._token)


class WindowBoundLease:
    """Explicit unqualified composition; window observation is not atomic input."""
    def __init__(self, lease, window_identity, window_reader):
        if type(lease) is not Lease or type(window_identity) is not dict or not callable(window_reader):
            raise FoundryError('invalid_process_provider', 'Exact retained lease and explicit window observer required.')
        validate_identity(window_identity)
        self._lease, self._window = lease, deepcopy(window_identity)
        self._reader = window_reader

    def identity(self):
        before = self._lease.snapshot()
        expected = {key: self._window[key] for key in _FIELDS}
        if canonical(before) != canonical(expected):
            raise FoundryError('process_witness_mismatch', 'Retained process and selected window do not match.')
        view = deepcopy(self._window)
        observed = self._reader(view)
        if canonical(view) != canonical(self._window):
            raise FoundryError('process_witness_mismatch', 'Window observer mutated the original binding.')
        same_identity(self._window, observed)
        if canonical(self._lease.snapshot()) != canonical(before):
            raise FoundryError('process_lifetime_changed', 'Retained process identity changed during window observation.')
        return deepcopy(observed)

    def is_running(self):
        return self._lease.is_running()

    def wait_exit(self, seconds):
        return self._lease.wait_exit(seconds)

    def close(self):
        # Compatibility with neutral_exit's injected provider-ack contract.
        # Its `released` field is a provider assertion, never OS verification.
        observation = self._lease.close()
        if (type(observation) is not dict or observation.get('outcome') != 'provider_reported_closed' or
                observation.get('os_release_verified') is not False):
            raise FoundryError('process_release_unresolved', 'Original release acknowledgment is unavailable.')
        return {'pid': observation['pid'], 'creation_filetime': observation['creation_filetime'],
                'released': True}


class RetainedProcessRegistry:
    """One owner thread and provider lifetime; no automatic restart recovery.

    Persist the exact request token/identity before invoking `acquire`. The
    host must authenticate access, own one stable registry and retain it while
    callers reconcile. A lost provider process loses its handles; recovery
    never calls OpenProcess again. Injected callbacks and witness claims require
    independent qualification, including file/window authority and durability.
    """
    def __init__(self, api, executable_verifier, *, clock=time.monotonic, limit=128):
        if (not all(callable(getattr(api, item, None)) for item in ('open', 'identity', 'wait', 'close')) or
                not callable(executable_verifier) or not callable(clock) or
                type(limit) is not int or not 1 <= limit <= 1024):
            raise FoundryError('invalid_process_provider', 'Exact query-only provider callbacks and bounds are required.')
        self._api, self._verify, self._clock = api, executable_verifier, clock
        self._owner = threading.current_thread()
        self._busy, self._slots, self._lifetimes, self._limit = False, {}, {}, limit

    @contextmanager
    def _entered(self):
        if threading.current_thread() is not self._owner:
            raise FoundryError('process_owner_changed', 'The captured provider owner thread is required.')
        if self._busy:
            raise FoundryError('process_provider_busy', 'Reentrant provider operations are refused.')
        self._busy = True
        try:
            yield
        finally:
            self._busy = False

    def _slot(self, token):
        slot = self._slots.get(token)
        if slot is None:
            raise FoundryError('process_witness_unknown', 'Original witness is unavailable; do not reacquire.')
        return slot

    def _held(self, token):
        slot = self._slot(token)
        if slot['state'] != 'held':
            raise FoundryError('process_witness_unresolved', 'Original handle is not a verified retained witness; do not reacquire.')
        return slot

    def _check_handle(self, slot):
        observed = self._api.identity(slot['handle'])
        expected = slot['identity']
        if (type(observed) is not tuple or len(observed) != 2 or type(observed[0]) is not str or
                type(observed[1]) is not int or observed[1] != expected['creation_filetime'] or
                not _same_image(observed[0], expected['executable'])):
            raise FoundryError('process_lifetime_changed', 'Retained handle does not match the selected lifetime.')

    def acquire(self, token, identity):
        """Call only once after durable intent; ambiguous outcomes stay latched."""
        with self._entered():
            if type(token) is not str or not _HEX32.fullmatch(token):
                raise FoundryError('invalid_process_witness', 'An exact original request ID is required.')
            selected = _identity(identity)
            life = (selected['pid'], selected['creation_filetime'])
            if token in self._slots or life in self._lifetimes or len(self._slots) >= self._limit:
                raise FoundryError('process_witness_exists', 'Lifetime or request already reserved; observe the original witness.')
            slot = {'identity': selected, 'state': 'preflight', 'handle': None}
            self._slots[token] = slot
            self._lifetimes[life] = token
            view = deepcopy(selected)
            if self._verify(view) is not True or canonical(view) != canonical(selected):
                slot['state'] = 'preflight_refused'
                raise FoundryError('process_executable_unverified', 'Exact selected executable evidence is required.')
            # State changes before the OS call. If open acquired a handle but
            # lost its return, the registry cannot safely infer or reacquire it.
            slot['state'] = 'acquisition_unconfirmed'
            handle = self._api.open(selected['pid'])
            if handle is None or handle is False or (type(handle) is int and handle == 0):
                raise FoundryError('process_acquisition_unresolved', 'No original handle witness was returned.')
            slot['handle'] = handle
            slot['state'] = 'returned_unverified'
            self._check_handle(slot)
            result = self._api.wait(handle, 0)
            if type(result) is not int or result != WAIT_TIMEOUT:
                raise FoundryError('process_lifetime_unresolved', 'Original handle was not confirmed running.')
            slot['state'] = 'held'
            return Lease(self, token)

    def recover(self, token, identity):
        """Recover only this registry's held reference; never open a PID."""
        with self._entered():
            slot = self._held(token)
            if canonical(_identity(identity)) != canonical(slot['identity']):
                raise FoundryError('process_witness_mismatch', 'Recovery identity differs from original intent.')
            return Lease(self, token)

    def status(self, token):
        with self._entered():
            slot = self._slot(token)
            return {'request_id': token, 'state': slot['state'], 'identity': deepcopy(slot['identity']),
                    'os_handle_exported': False, 'os_release_verified': False,
                    'loaded_image_hash_verified': False, 'game_qualified': False, 'authenticated': False}

    def _snapshot(self, token):
        with self._entered():
            slot = self._held(token)
            self._check_handle(slot)
            return deepcopy(slot['identity'])

    def _running(self, token):
        with self._entered():
            slot = self._held(token)
            result = self._api.wait(slot['handle'], 0)
            if type(result) is not int or result not in (WAIT_TIMEOUT, WAIT_OBJECT_0):
                raise FoundryError('process_wait_unresolved', 'Retained handle state is unavailable.')
            return result == WAIT_TIMEOUT

    def _wait_exit(self, token, seconds):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 60:
            raise FoundryError('invalid_timeout', 'Use a finite timeout between zero and sixty seconds.')
        with self._entered():
            slot = self._held(token)
            start = self._clock()
            if type(start) not in (int, float) or not math.isfinite(start):
                raise FoundryError('process_clock_unavailable', 'A finite monotonic clock is required.')
            deadline, last = start + seconds, start
            waits = 0
            while True:
                now = self._clock()
                if type(now) not in (int, float) or not math.isfinite(now) or now < last:
                    raise FoundryError('process_clock_unavailable', 'Monotonic clock moved backwards or became invalid.')
                last = now
                remaining = max(0.0, deadline - now)
                milliseconds = min(250, max(0, math.floor(remaining * 1000)))
                result = self._api.wait(slot['handle'], milliseconds)
                waits += 1
                if type(result) is not int or result not in (WAIT_TIMEOUT, WAIT_OBJECT_0):
                    raise FoundryError('process_wait_unresolved', 'Retained handle wait was not confirmed.')
                if result == WAIT_OBJECT_0 or milliseconds == 0:
                    expected = slot['identity']
                    return {'pid': expected['pid'], 'creation_filetime': expected['creation_filetime'],
                            'handle_retained': True, 'terminated': result == WAIT_OBJECT_0}
                if waits >= _MAX_WAIT_OBSERVATIONS:
                    raise FoundryError('process_wait_unresolved', 'Retained handle wait exceeded its observation bound.')

    def _close(self, token):
        with self._entered():
            slot = self._held(token)
            slot['state'] = 'release_unconfirmed'
            # A CloseHandle call can have completed even if its acknowledgment
            # is lost. Mark before entry and never retry the same handle.
            result = self._api.close(slot['handle'])
            if result is not None:
                raise FoundryError('process_release_unresolved', 'Unexpected release response; do not retry.')
            slot['state'] = 'provider_reported_closed'
            slot['handle'] = None
            expected = slot['identity']
            return {'pid': expected['pid'], 'creation_filetime': expected['creation_filetime'],
                    'outcome': 'provider_reported_closed', 'os_release_verified': False}
