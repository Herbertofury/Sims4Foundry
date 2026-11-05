"""Passive Windows process/window identity and independently pinned WGC capture.

No SendInput, PostMessage, focus, cursor, elevation, registry, DLL injection or
process-termination API exists here. Native execution still needs Windows QA.
"""
import ctypes
from ctypes import wintypes as wt
import os
from pathlib import PureWindowsPath
import struct
import subprocess
import time
from .errors import FoundryError
from .capture_pin import retained_capture_image
from .io import exact_int, external_path, new_json, plain_path, sha256
from .path_identity import ascii_lower, same_windows_path

IDENTITY_KEYS = ("pid", "creation_filetime", "executable", "executable_sha256", "hwnd", "thread_id",
                 "window_class", "client_width", "client_height", "client_origin", "dpi", "integrity")


def validate_identity(value):
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value.get("schema") != 1 or value.get("kind") != "foundry_windows_identity":
        raise FoundryError("invalid_identity", "Expected a Foundry passive Windows identity.")
    for key in ("pid", "creation_filetime", "hwnd", "thread_id", "dpi", "integrity"):
        if not exact_int(value.get(key), 1, 2**64-1):
            raise FoundryError("invalid_identity", "Invalid process/window identity field.", field=key)
    if not all(exact_int(value.get(k), 1, 16384) for k in ("client_width", "client_height")):
        raise FoundryError("invalid_identity", "Missing/unsupported client dimensions.")
    image = value.get("executable")
    if not isinstance(image, str) or not PureWindowsPath(image).is_absolute() or ascii_lower(PureWindowsPath(image).name) != "ts4_x64.exe":
        raise FoundryError("wrong_executable", "Only an explicitly selected Sims 4 DX11 executable is accepted.")
    checksum = value.get("executable_sha256")
    if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        raise FoundryError("invalid_identity", "Executable hash is invalid.")
    if not isinstance(value.get("window_class"), str) or not value["window_class"].startswith("Canvas-"):
        raise FoundryError("wrong_window", "Window must have the expected Canvas- class.")
    origin = value.get("client_origin")
    if not isinstance(origin, list) or len(origin) != 2 or not all(exact_int(v, -2**31, 2**31-1) for v in origin):
        raise FoundryError("invalid_identity", "Invalid client origin.")
    return value


def same_identity(before, after):
    validate_identity(before)
    validate_identity(after)
    if any(before[key] != after[key] for key in IDENTITY_KEYS):
        raise FoundryError("identity_changed", "Process lifetime, image or window geometry changed; no action is qualified.")


def _bind(library, name, result, args):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = args
    return function


def inspect(pid, executable, expected_sha256, hwnd=None):
    if os.name != "nt":
        raise FoundryError("windows_required", "Passive game inspection requires Windows. No host action was taken.")
    if not exact_int(pid, 1, 0xffffffff) or hwnd is not None and not exact_int(hwnd, 1, 2**64-1):
        raise FoundryError("invalid_identity", "Use an explicit PID and optional HWND.")
    executable = plain_path(executable)
    if ascii_lower(executable.name) != "ts4_x64.exe" or sha256(executable) != expected_sha256:
        raise FoundryError("wrong_executable", "Selected executable name/hash does not match.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    open_process = _bind(kernel, "OpenProcess", wt.HANDLE, [wt.DWORD, wt.BOOL, wt.DWORD])
    close = _bind(kernel, "CloseHandle", wt.BOOL, [wt.HANDLE])
    process_times = _bind(kernel, "GetProcessTimes", wt.BOOL, [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4)
    query_image = _bind(kernel, "QueryFullProcessImageNameW", wt.BOOL, [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)])
    wait = _bind(kernel, "WaitForSingleObject", wt.DWORD, [wt.HANDLE, wt.DWORD])
    window_pid = _bind(user, "GetWindowThreadProcessId", wt.DWORD, [wt.HWND, ctypes.POINTER(wt.DWORD)])
    visible = _bind(user, "IsWindowVisible", wt.BOOL, [wt.HWND])
    iconic = _bind(user, "IsIconic", wt.BOOL, [wt.HWND])
    window_class = _bind(user, "GetClassNameW", ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int])
    client_rect = _bind(user, "GetClientRect", wt.BOOL, [wt.HWND, ctypes.POINTER(wt.RECT)])
    client_screen = _bind(user, "ClientToScreen", wt.BOOL, [wt.HWND, ctypes.POINTER(wt.POINT)])
    dpi_for_window = _bind(user, "GetDpiForWindow", wt.UINT, [wt.HWND])
    open_token = _bind(advapi, "OpenProcessToken", wt.BOOL, [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)])
    token_info = _bind(advapi, "GetTokenInformation", wt.BOOL, [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)])
    sid_count = _bind(advapi, "GetSidSubAuthorityCount", ctypes.POINTER(ctypes.c_ubyte), [ctypes.c_void_p])
    sid_sub = _bind(advapi, "GetSidSubAuthority", ctypes.POINTER(wt.DWORD), [ctypes.c_void_p, wt.DWORD])

    def require(ok, message):
        if not ok:
            raise FoundryError("windows_access", message, windows_error=ctypes.get_last_error())

    def times(handle):
        created, exited, cpu, wall = (wt.FILETIME() for _ in range(4))
        require(process_times(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(cpu), ctypes.byref(wall)), "Cannot inspect process lifetime.")
        # GetProcessTimes documents exit FILETIME as undefined for a running
        # process. The pinned handle's nonsignalled wait is the liveness proof.
        if wait(handle, 0) != 0x102:
            raise FoundryError("process_gone", "The selected process is not running.")
        return (created.dwHighDateTime << 32) | created.dwLowDateTime

    # QUERY_LIMITED_INFORMATION + SYNCHRONIZE, never VM_READ/WRITE or full access.
    process = open_process(0x1000 | 0x100000, False, pid)
    require(process, "Cannot open the selected process with query-only rights.")
    try:
        created = times(process)
        name = ctypes.create_unicode_buffer(32768)
        length = wt.DWORD(len(name))
        require(query_image(process, 0, name, ctypes.byref(length)), "Cannot inspect executable path.")
        if not same_windows_path(name.value, str(executable)):
            raise FoundryError("wrong_process", "PID does not belong to the selected executable path.")
        token = wt.HANDLE()
        require(open_token(process, 0x0008, ctypes.byref(token)), "Cannot read target integrity.")
        try:
            needed = wt.DWORD()
            token_info(token, 25, None, 0, ctypes.byref(needed))
            if not 1 <= needed.value <= 65536:
                raise FoundryError("windows_access", "Target integrity query is unavailable.")
            buffer = ctypes.create_string_buffer(needed.value)
            require(token_info(token, 25, buffer, len(buffer), ctypes.byref(needed)), "Cannot read target integrity.")
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            count = sid_count(sid)
            require(count and count[0] > 0, "Malformed target integrity SID.")
            integrity = int(sid_sub(sid, count[0] - 1)[0])
        finally:
            close(token)
        candidates = []
        callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def collect(window, _):
            owner = wt.DWORD()
            thread_id = window_pid(window, ctypes.byref(owner))
            if not thread_id or owner.value != pid or not visible(window) or iconic(window):
                return True
            text = ctypes.create_unicode_buffer(256)
            length = window_class(window, text, len(text))
            if length > 0 and text.value.startswith("Canvas-") and (hwnd is None or int(window) == hwnd):
                candidates.append((int(window), int(thread_id), text.value))
            return True

        enum_windows = _bind(user, "EnumWindows", wt.BOOL, [callback_type, wt.LPARAM])
        require(enum_windows(callback_type(collect), 0), "Cannot enumerate visible windows.")
        if len(candidates) != 1:
            raise FoundryError("ambiguous_window", "Expected exactly one visible non-minimized Sims Canvas window.", count=len(candidates))
        window, thread_id, class_name = candidates[0]
        rect, origin = wt.RECT(), wt.POINT(0, 0)
        require(client_rect(window, ctypes.byref(rect)) and client_screen(window, ctypes.byref(origin)), "Cannot observe client geometry.")
        dpi = int(dpi_for_window(window))
        owner = wt.DWORD()
        if window_pid(window, ctypes.byref(owner)) != thread_id or owner.value != pid or times(process) != created or sha256(executable) != expected_sha256:
            raise FoundryError("identity_changed", "Target identity changed during passive inspection.")
        result = {"schema": 1, "kind": "foundry_windows_identity", "pid": pid, "creation_filetime": created,
                  "executable": str(executable), "executable_sha256": expected_sha256, "hwnd": window,
                  "thread_id": thread_id, "window_class": class_name, "client_width": rect.right - rect.left,
                  "client_height": rect.bottom - rect.top, "client_origin": [origin.x, origin.y],
                  "dpi": dpi, "integrity": integrity, "observed_at": time.time(),
                  "passive": True, "input_sent": False, "real_game_control_ready": False}
        return validate_identity(result)
    finally:
        close(process)


def capture(identity, ffmpeg, expected_ffmpeg_sha256, destination, *, observer=inspect, runner=subprocess.run):
    validate_identity(identity)
    ffmpeg = plain_path(ffmpeg)
    if ascii_lower(ffmpeg.name) != "ffmpeg.exe":
        raise FoundryError("capture_tool_identity", "Use an explicitly selected, hash-pinned ffmpeg.exe.")
    # Hold every non-root directory plus the exact file, without delete/write
    # sharing on the image, across the child execution and capture checks.
    # Hashing by path and reopening later would not bind the executed image.
    with retained_capture_image(str(ffmpeg), expected_ffmpeg_sha256):
        result = _capture_held(identity, ffmpeg, expected_ffmpeg_sha256, destination, observer, runner)
    # A failed handle release must not leave a success receipt.
    new_json(external_path(destination) / "capture-complete.json", result)
    return result


def _capture_held(identity, ffmpeg, expected_ffmpeg_sha256, destination, observer, runner):
    fresh = observer(identity["pid"], identity["executable"], identity["executable_sha256"], identity["hwnd"])
    same_identity(identity, fresh)
    destination = external_path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    new_json(destination / "capture-intent.json", {"identity": fresh, "ffmpeg_sha256": expected_ffmpeg_sha256})
    output = destination / "frame.png"
    # -n prevents overwrite; never use desktop-wide capture or change foreground.
    command = [str(ffmpeg), "-nostdin", "-n", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
               f"gfxcapture=hwnd={fresh['hwnd']}:max_framerate=30:capture_cursor=0:capture_border=0:display_border=0",
               "-vf", "hwdownload,format=bgra", "-frames:v", "1", str(output)]
    try:
        result = runner(command, capture_output=True, timeout=30, check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise FoundryError("capture_timeout", "Capture timed out. No input was sent.", destination=str(destination)) from exc
    if result.returncode != 0 or not output.is_file():
        raise FoundryError("capture_failed", "WGC capture did not produce an image. No fallback or input was attempted.", returncode=result.returncode)
    from .png import inspect_png
    width, height = inspect_png(output)
    after = observer(identity["pid"], identity["executable"], identity["executable_sha256"], identity["hwnd"])
    same_identity(fresh, after)
    result = {"ok": True, "input_sent": False, "image": str(output), "image_sha256": sha256(output),
              "width": width, "height": height, "identity": after, "semantic_state_verified": False,
              "visual_review_required": True, "live_input_qualified": False,
              "capture_surface": "WGC window", "capture_geometry_qualified": False}
    return result
