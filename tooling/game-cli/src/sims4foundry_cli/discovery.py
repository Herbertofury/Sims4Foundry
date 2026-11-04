"""Bounded, passive discovery for one explicitly selected Sims 4 executable.

Toolhelp's image name is only a cheap candidate filter. Each candidate still
requires windows.inspect's path/hash/process-lifetime/window checks. A failed
candidate is retained as a blocker, including an exited or inaccessible process.
This is a point-in-time observation, not absence proof, exclusive ownership,
launch admission or control qualification. There are no retries or action APIs.

Microsoft contracts (consulted 2026-11-04):
https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/ns-tlhelp32-processentry32w
https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-createtoolhelp32snapshot
https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-process32firstw
https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-process32nextw
"""
import ctypes
from ctypes import wintypes as wt
from copy import deepcopy
import os
from pathlib import PureWindowsPath
import time

from .errors import FoundryError
from .path_identity import ascii_lower, same_windows_path
from .io import exact_int, plain_path, sha256
from . import windows


IMAGE_NAME = "TS4_x64.exe"
MAX_PROCESSES = 32768
MAX_CANDIDATES = 64
ERROR_NO_MORE_FILES = 18
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _ProcessEntry32W(ctypes.Structure):
    # DWORD/LONG have fixed 32-bit widths, including on 64-bit Windows;
    # ULONG_PTR follows pointer width. WCHAR follows the Windows native ABI.
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_int32),
                ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260)]


def _limits(max_processes, max_candidates):
    if not exact_int(max_processes, 1, MAX_PROCESSES) or not exact_int(max_candidates, 1, MAX_CANDIDATES):
        raise FoundryError("invalid_discovery_limit", "Discovery limits must be bounded exact integers.")


class _Toolhelp:
    """Native snapshot access only; never opens an individual process."""

    def __init__(self):
        if os.name != "nt":
            raise FoundryError("windows_required", "Passive process discovery requires Windows. No host action was taken.")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.snapshot = windows._bind(kernel, "CreateToolhelp32Snapshot", wt.HANDLE,
                                      [wt.DWORD, wt.DWORD])
        self.first = windows._bind(kernel, "Process32FirstW", wt.BOOL,
                                   [wt.HANDLE, ctypes.POINTER(_ProcessEntry32W)])
        self.next = windows._bind(kernel, "Process32NextW", wt.BOOL,
                                  [wt.HANDLE, ctypes.POINTER(_ProcessEntry32W)])
        self.close = windows._bind(kernel, "CloseHandle", wt.BOOL, [wt.HANDLE])
        self.last_error = ctypes.get_last_error


def _snapshot_image_pids(image_name, *, max_processes=MAX_PROCESSES,
                         max_candidates=MAX_CANDIDATES, api=None):
    """Enumerate one exact basename; generic only for enumeration-only CI tests."""
    _limits(max_processes, max_candidates)
    if (not isinstance(image_name, str) or not image_name or len(image_name) >= 260
            or any(ord(c) < 32 or c in '\\/:*?"<>|' for c in image_name)
            or image_name in (".", "..")):
        raise FoundryError("invalid_discovery_image", "Use one exact executable basename.")
    api = _Toolhelp() if api is None else api
    # TH32CS_SNAPPROCESS, without inheritance, modules, heaps or threads.
    handle = api.snapshot(0x00000002, 0)
    if not handle or handle == INVALID_HANDLE_VALUE:
        raise FoundryError("discovery_incomplete", "Cannot obtain a process snapshot.",
                           windows_error=api.last_error())
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        present = api.first(handle, ctypes.byref(entry))
        pids, seen = [], set()
        examined = 0
        while present:
            examined += 1
            if examined > max_processes:
                raise FoundryError("discovery_limit", "Process snapshot exceeds the enumeration bound.",
                                   max_processes=max_processes)
            # Do not query or retain information for unrelated image names.
            if ascii_lower(entry.szExeFile) == ascii_lower(image_name):
                pid = int(entry.th32ProcessID)
                if not exact_int(pid, 1, 0xffffffff) or pid in seen:
                    raise FoundryError("invalid_discovery_snapshot", "Snapshot candidate PID is invalid or repeated.")
                if len(pids) >= max_candidates:
                    raise FoundryError("discovery_limit", "Process snapshot exceeds the candidate bound.",
                                       max_candidates=max_candidates)
                seen.add(pid)
                pids.append(pid)
            present = api.next(handle, ctypes.byref(entry))
        error = api.last_error()
        if error != ERROR_NO_MORE_FILES:
            raise FoundryError("discovery_incomplete", "Process enumeration ended without a complete snapshot.",
                               windows_error=error)
        return pids
    finally:
        if not api.close(handle):
            raise FoundryError("discovery_cleanup", "Cannot confirm the process snapshot handle was closed.",
                               windows_error=api.last_error())


def candidate_pids(*, max_processes=MAX_PROCESSES, max_candidates=MAX_CANDIDATES):
    """List name-filtered candidates, never verified game identities."""
    return _snapshot_image_pids(IMAGE_NAME, max_processes=max_processes, max_candidates=max_candidates)


def _selected_executable(executable, expected_sha256):
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_sha256)):
        raise FoundryError("wrong_executable", "Use an explicit lowercase SHA-256 for the selected executable.")
    executable = plain_path(executable)
    if ascii_lower(executable.name) != ascii_lower(IMAGE_NAME):
        raise FoundryError("wrong_executable", "Only the explicitly selected TS4_x64.exe is accepted.")
    try:
        actual = sha256(executable)
    except OSError as exc:
        raise FoundryError("executable_unavailable", "Cannot read the selected executable.") from exc
    if actual != expected_sha256:
        raise FoundryError("wrong_executable", "Selected executable hash does not match.")
    return executable


def _validate_observation(identity, pid, executable, expected_sha256):
    windows.validate_identity(identity)
    # validate_identity accepts some general 64-bit numbers; actual Win32 PID
    # and thread IDs are DWORDs. Reject booleans and truncated/wrapped values.
    if not exact_int(identity["pid"], 1, 0xffffffff) or not exact_int(identity["thread_id"], 1, 0xffffffff):
        raise FoundryError("invalid_identity", "Observed process/thread ID must be an exact DWORD.")
    if (identity["pid"] != pid or identity["executable_sha256"] != expected_sha256
            or not same_windows_path(identity["executable"], str(executable))):
        raise FoundryError("wrong_process", "Observed identity does not match the selected PID, executable path and hash.")
    if (identity.get("input_sent", False) is not False
            or identity.get("real_game_control_ready", False) is not False
            or identity.get("passive", True) is not True):
        raise FoundryError("invalid_identity", "Discovery accepts passive, unqualified observations only.")
    return deepcopy(identity)


def discover(executable, expected_sha256, *, enumerator=None, observer=None,
             max_processes=MAX_PROCESSES, max_candidates=MAX_CANDIDATES):
    """Report all candidate outcomes without choosing, launching or controlling.

    Injected enumerators must return a complete list of name-filtered PIDs; the
    native default performs bounded enumeration before opening any candidate.
    An incomplete snapshot raises instead of returning a partial or empty list.
    A candidate observation error blocks discovery even if other candidates match.
    The default observer performs query-only exact path/hash/lifetime/window
    verification. No matching result or empty snapshot authorizes a later action.
    """
    _limits(max_processes, max_candidates)
    executable = _selected_executable(executable, expected_sha256)
    enumerator = candidate_pids if enumerator is None else enumerator
    observer = windows.inspect if observer is None else observer
    pids = enumerator(max_processes=max_processes, max_candidates=max_candidates)
    if not isinstance(pids, list) or len(pids) > max_candidates:
        raise FoundryError("invalid_discovery_snapshot", "Expected a complete bounded candidate PID list.")
    if any(not exact_int(pid, 1, 0xffffffff) for pid in pids) or len(set(pids)) != len(pids):
        raise FoundryError("invalid_discovery_snapshot", "Candidate PIDs must be unique exact DWORD values.")
    observations, matches = [], []
    for pid in pids:
        try:
            identity = _validate_observation(observer(pid, executable, expected_sha256),
                                             pid, executable, expected_sha256)
        except FoundryError as exc:
            observations.append({"pid": pid, "status": "mismatched" if exc.code in
                                 ("wrong_process", "wrong_executable") else "unresolved", "error": exc.as_dict()})
        except OSError as exc:
            observations.append({"pid": pid, "status": "unresolved", "error": {
                "ok": False, "code": "observation_unavailable", "message": "Cannot observe this candidate.",
                "os_error": exc.errno}})
        else:
            observations.append({"pid": pid, "status": "matched", "identity": identity})
            matches.append(identity)
    # Recheck even for an empty snapshot. Path/hash drift never yields success.
    _selected_executable(executable, expected_sha256)
    blockers = len(observations) - len(matches)
    status = ("blocked" if blockers else "ambiguous" if len(matches) > 1
              else "one_observed" if matches else "none_observed")
    return {"ok": True, "schema": 1, "kind": "foundry_game_discovery", "status": status,
            "executable": str(executable), "executable_sha256": expected_sha256,
            "candidate_count": len(pids), "matched_count": len(matches), "blocker_count": blockers,
            "candidates": observations, "matches": matches, "snapshot_complete": True,
            "observed_at": time.time(), "passive": True, "input_sent": False, "launched": False,
            "absence_proven": False, "launch_qualified": False, "real_game_control_ready": False}
