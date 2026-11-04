"""Cooperative, cross-workspace ownership of one exact Windows process lifetime.

Usage: ``with process_lease(expected_identity) as lease: lease.observe()``.
Enter, observe and exit on the same thread. The non-inheritable OS handles stay
open for the entire context. A new controller must enter its own context; a
serialized observation or a previously returned lease is not ownership.

The fixed Local namespace is scoped to the OS user SID and PID/creation FILETIME,
never a workspace, executable spelling, window or caller-selected lock path.
Cross-user/session targets and impersonation are refused. Default OS ACLs remain
unchanged. This only coordinates participating Foundry controllers in one user
session: it cannot lock out the user, Apex, other tools or a hostile local actor.

EVERY admission requires independent durable-journal reconciliation before any
mutation, even when abandoned is false: Windows destroys a mutex when its last
handle closes, so abandonment is not a durable crash marker. There is no setter,
reconcile=True flag, dispatch method or mutation authorization here. A future
qualified adapter must reconcile all prior process-lifetime dispatches across
workspaces, refuse uncertainty, and reobserve immediately before each mutation.
This helper neither resolves outcomes nor relaxes existing qualification gates.

Microsoft contracts (consulted 2026-11-04):
https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexexw
https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject
https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-releasemutex
https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces
https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes
https://learn.microsoft.com/en-us/windows/win32/api/winnt/ne-winnt-token_information_class
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import ctypes
from ctypes import wintypes as wt
import hashlib
import os
import threading
import time

from .errors import FoundryError
from .io import exact_int, finite_number
from . import windows


WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 0x102
WAIT_FAILED = 0xffffffff
SYNCHRONIZE = 0x00100000
MUTEX_MODIFY_STATE = 0x0001
MAX_OBSERVATION_SECONDS = 5

# Win32 permits recursive mutex ownership. Reserve the same key across contexts
# in this interpreter too, including the interval before the OS wait completes.
_held_names = set()
_held_names_lock = threading.Lock()


def _mutex_name(sid, identity):
    user = hashlib.sha256(sid.encode("ascii")).hexdigest()
    # Coordination protocol name: do not change it with package/provider versions.
    return (f"Local\\Foundry.Game.ProcessLease.v1.{user}."
            f"{identity['pid']:08x}.{identity['creation_filetime']:016x}")


def _require(ok, message):
    if not ok:
        raise FoundryError("windows_access", message, windows_error=ctypes.get_last_error())


class _WindowsApi:
    """Small query/synchronization-only binding; no security changes or fallback."""

    def __init__(self):
        if os.name != "nt":
            raise FoundryError("windows_required", "Process leases require Windows; no file-lock fallback exists.")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        bind = windows._bind
        self._open_process = bind(kernel, "OpenProcess", wt.HANDLE, [wt.DWORD, wt.BOOL, wt.DWORD])
        self._close = bind(kernel, "CloseHandle", wt.BOOL, [wt.HANDLE])
        self._times = bind(kernel, "GetProcessTimes", wt.BOOL,
                           [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4)
        self._current_process = bind(kernel, "GetCurrentProcess", wt.HANDLE, [])
        self._current_thread = bind(kernel, "GetCurrentThread", wt.HANDLE, [])
        self._open_token = bind(advapi, "OpenProcessToken", wt.BOOL,
                                [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)])
        self._open_thread_token = bind(advapi, "OpenThreadToken", wt.BOOL,
                                       [wt.HANDLE, wt.DWORD, wt.BOOL, ctypes.POINTER(wt.HANDLE)])
        self._token_info = bind(advapi, "GetTokenInformation", wt.BOOL,
                                [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)])
        self._sid_string = bind(advapi, "ConvertSidToStringSidW", wt.BOOL,
                                [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)])
        self._local_free = bind(kernel, "LocalFree", ctypes.c_void_p, [ctypes.c_void_p])
        self._create_mutex = bind(kernel, "CreateMutexExW", wt.HANDLE,
                                  [ctypes.c_void_p, wt.LPCWSTR, wt.DWORD, wt.DWORD])
        self._wait = bind(kernel, "WaitForSingleObject", wt.DWORD, [wt.HANDLE, wt.DWORD])
        self._release = bind(kernel, "ReleaseMutex", wt.BOOL, [wt.HANDLE])

    def close(self, handle):
        _require(self._close(handle), "Cannot close a process lease handle; ownership cleanup is unverified.")

    def reject_impersonation(self):
        token = wt.HANDLE()
        if self._open_thread_token(self._current_thread(), 0x0008, True, ctypes.byref(token)):
            self.close(token)
            raise FoundryError("lease_scope", "Impersonating threads cannot acquire a process lease.")
        # ERROR_NO_TOKEN is the only expected failure for an ordinary thread.
        if ctypes.get_last_error() != 1008:
            _require(False, "Cannot establish the controller's non-impersonating security context.")

    def open_target(self, pid):
        handle = self._open_process(0x1000 | SYNCHRONIZE, False, pid)
        _require(handle, "Cannot retain the selected process with query-only rights.")
        return handle

    def current_security_context(self):
        return self.security_context(self._current_process())

    def security_context(self, process):
        token = wt.HANDLE()
        _require(self._open_token(process, 0x0008, ctypes.byref(token)), "Cannot query process user/session.")
        try:
            needed = wt.DWORD()
            result = self._token_info(token, 1, None, 0, ctypes.byref(needed))  # TokenUser
            if result or ctypes.get_last_error() != 122 or not ctypes.sizeof(ctypes.c_void_p) <= needed.value <= 65536:
                raise FoundryError("windows_access", "Process user query returned an invalid buffer requirement.")
            buffer = ctypes.create_string_buffer(needed.value)
            _require(self._token_info(token, 1, buffer, len(buffer), ctypes.byref(needed)), "Cannot query process user.")
            if not ctypes.sizeof(ctypes.c_void_p) <= needed.value <= len(buffer):
                raise FoundryError("windows_access", "Process user query returned malformed data.")
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            text = wt.LPWSTR()
            _require(self._sid_string(sid, ctypes.byref(text)), "Cannot identify the process user SID.")
            try:
                user = text.value
            finally:
                if self._local_free(ctypes.cast(text, ctypes.c_void_p)):
                    raise FoundryError("windows_access", "Cannot free the process user SID buffer.")
            session = wt.DWORD()
            _require(self._token_info(token, 12, ctypes.byref(session), ctypes.sizeof(session),
                                      ctypes.byref(needed)), "Cannot query process session.")  # TokenSessionId
            if not user or not user.startswith("S-1-") or needed.value != ctypes.sizeof(session):
                raise FoundryError("windows_access", "Process user/session query returned malformed data.")
            return user, session.value
        finally:
            self.close(token)

    def validate_lifetime(self, handle, expected):
        created, exited, kernel, user = (wt.FILETIME() for _ in range(4))
        _require(self._times(handle, ctypes.byref(created), ctypes.byref(exited),
                             ctypes.byref(kernel), ctypes.byref(user)), "Cannot query retained process lifetime.")
        if ((created.dwHighDateTime << 32) | created.dwLowDateTime) != expected["creation_filetime"]:
            raise FoundryError("identity_changed", "The PID belongs to a different process lifetime.")
        # Exit FILETIME is undefined while running. Only the wait establishes liveness.
        status = self._wait(handle, 0)
        if status == WAIT_FAILED:
            _require(False, "Cannot establish retained process liveness.")
        if status != WAIT_TIMEOUT:
            raise FoundryError("process_gone", "The exact selected process is no longer running.")

    def create_mutex(self, name):
        # NULL attributes: default ACL, not inherited. No initial ownership;
        # the single zero-time wait below decides ownership, even if it existed.
        handle = self._create_mutex(None, name, 0, SYNCHRONIZE | MUTEX_MODIFY_STATE)
        _require(handle, "Cannot open the process lease mutex; no alternate namespace will be tried.")
        return handle

    def acquire(self, handle):
        status = self._wait(handle, 0)
        if status == WAIT_TIMEOUT:
            raise FoundryError("process_busy", "Another participating Foundry controller owns this process lease.")
        if status == WAIT_FAILED:
            _require(False, "Cannot acquire the process lease mutex.")
        if status not in (WAIT_OBJECT_0, WAIT_ABANDONED):
            raise FoundryError("lease_wait_failed", "Unexpected mutex wait result; ownership is unverified.", wait_status=status)
        return status == WAIT_ABANDONED

    def release(self, handle):
        _require(self._release(handle), "Cannot release process lease ownership on this thread.")


@contextmanager
def _claim(api, name):
    with _held_names_lock:
        if name in _held_names:
            raise FoundryError("process_busy", "This controller already has an active or unresolved process lease.")
        _held_names.add(name)
    clean = True
    try:
        handle = api.create_mutex(name)
        try:
            abandoned = api.acquire(handle)
            try:
                yield abandoned
            finally:
                try:
                    api.release(handle)
                except BaseException:
                    clean = False
                    raise
        finally:
            try:
                api.close(handle)
            except BaseException:
                clean = False
                raise
    finally:
        # If native cleanup failed, fail closed for this interpreter. Never
        # erase an uncertain owner and recursively acquire its OS mutex again.
        if clean:
            with _held_names_lock:
                _held_names.remove(name)


def _observe(expected):
    start = time.time()
    monotonic_start = time.monotonic()
    observed = windows.inspect(expected["pid"], expected["executable"], expected["executable_sha256"], expected["hwnd"])
    end = time.time()
    elapsed = time.monotonic() - monotonic_start
    windows.same_identity(expected, observed)
    if (not 0 <= elapsed <= MAX_OBSERVATION_SECONDS or not start <= end
            or not finite_number(observed.get("observed_at"), start, end)):
        raise FoundryError("stale_observation", "The independent identity observation is missing, stale or too slow.")
    return deepcopy(observed)


class _ProcessLease:
    def __init__(self, expected):
        windows.validate_identity(expected)
        if not exact_int(expected["pid"], 1, 0xffffffff):
            raise FoundryError("invalid_identity", "Windows process IDs must fit a DWORD.")
        self._expected = deepcopy(expected)
        self._stack = None
        self._owner_thread = None
        self._owner_ident = None
        self._used = False
        self._abandoned = None

    @property
    def abandoned(self):
        """Diagnostic only. False is NOT evidence that an earlier action completed."""
        return self._abandoned

    @property
    def reconciliation_required(self):
        return True

    @property
    def mutation_permitted(self):
        return False

    def __enter__(self):
        if self._used:
            raise FoundryError("lease_context", "Process lease contexts are single-use and cannot be nested.")
        self._used = True
        self._owner_thread = threading.current_thread()
        self._owner_ident = threading.get_ident()
        with ExitStack() as stack:
            self._api = _WindowsApi()
            self._api.reject_impersonation()
            self._target = self._api.open_target(self._expected["pid"])
            stack.callback(self._api.close, self._target)
            self._api.validate_lifetime(self._target, self._expected)
            controller = self._api.current_security_context()
            target = self._api.security_context(self._target)
            if controller != target:
                raise FoundryError("lease_scope", "Only a target in the controller's own OS user and session can be leased.")
            _observe(self._expected)
            self._abandoned = stack.enter_context(_claim(self._api, _mutex_name(controller[0], self._expected)))
            # Reobserve AFTER ownership as well: the pre-acquisition snapshot
            # alone cannot authorize admission across an intervening change.
            self._refresh()
            self._stack = stack.pop_all()
        return self

    def _check_context(self):
        if self._stack is None:
            raise FoundryError("lease_context", "The process lease is not held in an active context.")
        # Numeric thread identifiers can be recycled after a thread exits. Keep
        # the actual Thread object as well so a replacement cannot inherit use.
        if (threading.current_thread() is not self._owner_thread
                or threading.get_ident() != self._owner_ident):
            raise FoundryError("lease_thread", "Process lease ownership must stay on its acquiring thread.")

    def _refresh(self):
        self._api.reject_impersonation()
        self._api.validate_lifetime(self._target, self._expected)
        observed = _observe(self._expected)
        self._api.validate_lifetime(self._target, self._expected)
        return observed

    def observe(self):
        """Fresh exact identity under the held lease; no journal outcome is inferred."""
        self._check_context()
        return self._refresh()

    def __exit__(self, exc_type, exc, traceback):
        # Reject a cross-thread exit without consuming the context; its original
        # owner can still exit normally. ReleaseMutex is a thread-owned operation.
        self._check_context()
        stack, self._stack = self._stack, None
        return stack.__exit__(exc_type, exc, traceback)


def process_lease(expected_identity):
    """Return a single-use, same-thread ownership context, never mutation permission.

    No caller-selected backend, username, workspace, namespace or reconciliation
    flag is accepted. Tests patch private bindings; non-Windows hosts fail closed.
    """
    return _ProcessLease(expected_identity)
