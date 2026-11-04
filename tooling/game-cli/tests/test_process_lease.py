"""Offline lease contracts plus opt-in hosted-Windows synthetic mutex fixtures.

No test discovers or targets Sims, a desktop window, a profile or a game install.
The public lease uses mocked observations here. Native fixtures use only Python
subprocesses and unique mutex names, with files confined to temporary directories.
"""
from contextlib import ExitStack
import ast
import ctypes
from ctypes import wintypes as wt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

from sims4foundry_cli import process_lease as leases
from sims4foundry_cli.errors import FoundryError


def identity():
    return {"schema": 1, "kind": "foundry_windows_identity", "pid": 42,
            "creation_filetime": 123, "executable": r"C:\Fixtures\TS4_x64.exe",
            "executable_sha256": "a" * 64, "hwnd": 100, "thread_id": 200,
            "window_class": "Canvas-Fixture", "client_width": 640,
            "client_height": 480, "client_origin": [0, 0], "dpi": 96,
            "integrity": 8192, "observed_at": time.time()}


class LeaseContractTests(unittest.TestCase):
    def setUp(self):
        self.api = mock.Mock()
        self.api.open_target.return_value = "target"
        self.api.create_mutex.return_value = "mutex"
        self.api.acquire.return_value = False
        self.api.current_security_context.return_value = ("S-1-5-21-123", 1)
        self.api.security_context.return_value = ("S-1-5-21-123", 1)
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.object(leases, "_WindowsApi", return_value=self.api))
        self.observer = self.patches.enter_context(mock.patch.object(leases.windows, "inspect", side_effect=lambda *args: identity()))
        # Synthetic cleanup-failure tests intentionally poison an in-memory key.
        self.patches.enter_context(mock.patch.object(leases, "_held_names", set()))

    def assert_code(self, expected, function):
        with self.assertRaises(FoundryError) as caught:
            function()
        self.assertEqual(expected, caught.exception.code)

    def test_context_retains_handles_and_requires_reconciliation(self):
        expected = identity()
        lease = leases.process_lease(expected)
        self.assert_code("lease_context", lease.observe)
        with lease as owned:
            self.assertIs(owned, lease)
            self.assertFalse(owned.abandoned)
            self.assertTrue(owned.reconciliation_required)
            self.assertFalse(owned.mutation_permitted)
            self.assertEqual(2, self.observer.call_count)
            self.api.close.assert_not_called()
            self.assertEqual(expected["pid"], owned.observe()["pid"])
            self.observer.assert_called_with(42, expected["executable"], "a" * 64, 100)
        self.assertEqual([mock.call("mutex"), mock.call("target")], self.api.close.call_args_list)
        self.api.release.assert_called_once_with("mutex")
        self.assert_code("lease_context", lease.observe)
        self.assert_code("lease_context", lease.__enter__)

    def test_abandonment_does_not_resolve_uncertain_action(self):
        self.api.acquire.return_value = True
        with leases.process_lease(identity()) as lease:
            self.assertTrue(lease.abandoned)
            self.assertTrue(lease.reconciliation_required)
            self.assertFalse(lease.mutation_permitted)
            for field in ("abandoned", "reconciliation_required", "mutation_permitted"):
                with self.subTest(field=field), self.assertRaises(AttributeError):
                    setattr(lease, field, False)
        # Windows may forget abandonment when the last OS handle is gone.
        self.api.acquire.return_value = False
        with leases.process_lease(identity()) as next_lease:
            self.assertTrue(next_lease.reconciliation_required)
            self.assertFalse(next_lease.mutation_permitted)

    def test_no_workspace_namespace_or_reconciliation_override(self):
        for field in ("directory", "path", "namespace", "sid", "backend", "observer", "reconciled"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                leases.process_lease(identity(), **{field: True})

    def test_source_has_no_native_sender_or_security_changes(self):
        tree = ast.parse(Path(leases.__file__).read_text())
        literals = {node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        forbidden = {"SendInput", "PostMessageW", "SetForegroundWindow", "SetCursorPos",
                     "TerminateProcess", "ShellExecuteW", "WriteProcessMemory", "SetTokenInformation",
                     "AdjustTokenPrivileges", "SetNamedSecurityInfoW", "SetSecurityInfo"}
        self.assertTrue(forbidden.isdisjoint(literals))

    def test_key_is_workspace_and_window_independent_but_lifetime_specific(self):
        first = identity()
        second = dict(first, executable=r"D:\Alternative\TS4_x64.exe", hwnd=900,
                      executable_sha256="b" * 64, thread_id=999, client_width=1000)
        key = leases._mutex_name("S-1-5-21-123", first)
        self.assertEqual(key, leases._mutex_name("S-1-5-21-123", second))
        self.assertTrue(key.startswith("Local\\Foundry.Game.ProcessLease.v1."))
        self.assertNotIn("S-1-5-21-123", key)
        self.assertNotIn("Global", key)
        self.assertLess(len(key), 260)
        for changed in (dict(first, pid=43), dict(first, creation_filetime=124)):
            self.assertNotEqual(key, leases._mutex_name("S-1-5-21-123", changed))
        self.assertNotEqual(key, leases._mutex_name("S-1-5-21-456", first))

    def test_invalid_exact_identity_never_opens_target(self):
        for changes in ({"pid": True}, {"pid": 2**32}, {"creation_filetime": 0},
                        {"creation_filetime": 2**64}, {"executable_sha256": "wrong"},
                        {"hwnd": 0}, {"schema": True}):
            with self.subTest(changes=changes), self.assertRaises(FoundryError):
                leases.process_lease(dict(identity(), **changes))
        self.api.open_target.assert_not_called()

    def test_expected_and_returned_observations_cannot_mutate_private_identity(self):
        expected = identity()
        lease = leases.process_lease(expected)
        expected["pid"] = 99
        expected["client_origin"][0] = 25
        with lease:
            observed = lease.observe()
            observed["pid"] = 100
            observed["client_origin"][0] = 50
            self.assertEqual(42, lease.observe()["pid"])
            self.assertEqual([0, 0], lease.observe()["client_origin"])
        self.api.open_target.assert_called_once_with(42)

    def test_same_thread_nested_independent_context_is_busy_not_recursive(self):
        with leases.process_lease(identity()):
            nested = leases.process_lease(identity())
            self.assert_code("process_busy", nested.__enter__)
            self.api.create_mutex.assert_called_once()
            self.api.release.assert_not_called()
        with leases.process_lease(identity()):
            pass
        self.assertEqual(2, self.api.create_mutex.call_count)

    def test_other_thread_context_is_busy_and_cannot_use_or_exit_owner(self):
        errors = []
        with leases.process_lease(identity()) as owner:
            def attempt():
                for action in (leases.process_lease(identity()).__enter__, owner.observe,
                               lambda: owner.__exit__(None, None, None)):
                    try:
                        action()
                    except FoundryError as exc:
                        errors.append(exc.code)
            worker = threading.Thread(target=attempt)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(["process_busy", "lease_thread", "lease_thread"], errors)
            self.assertEqual(42, owner.observe()["pid"])
        self.api.release.assert_called_once()

    def test_recycled_numeric_thread_ident_cannot_observe_or_release_owner(self):
        with leases.process_lease(identity()) as owner:
            owner_ident = threading.get_ident()
            replacement = threading.Thread(name="synthetic-recycled-ident")
            self.api.reset_mock()
            self.observer.reset_mock()
            with mock.patch.object(leases.threading, "get_ident", return_value=owner_ident), \
                    mock.patch.object(leases.threading, "current_thread", return_value=replacement):
                self.assert_code("lease_thread", owner.observe)
                self.assert_code("lease_thread", lambda: owner.__exit__(None, None, None))
            self.observer.assert_not_called()
            self.api.validate_lifetime.assert_not_called()
            self.api.release.assert_not_called()
            self.api.close.assert_not_called()
            # A rejected replacement does not consume the original context.
            self.assertEqual(42, owner.observe()["pid"])
        self.api.release.assert_called_once_with("mutex")

    def test_scope_mismatch_fails_before_mutex(self):
        for target in (("S-1-5-21-456", 1), ("S-1-5-21-123", 2)):
            self.api.security_context.return_value = target
            with self.subTest(target=target):
                self.assert_code("lease_scope", leases.process_lease(identity()).__enter__)
        self.api.create_mutex.assert_not_called()
        self.observer.assert_not_called()
        self.assertEqual(2, self.api.close.call_count)

    def test_impersonation_refusal_precedes_process_open(self):
        self.api.reject_impersonation.side_effect = FoundryError("lease_scope", "impersonation")
        self.assert_code("lease_scope", leases.process_lease(identity()).__enter__)
        self.api.open_target.assert_not_called()

    def test_changed_lifetime_before_observation_never_acquires(self):
        self.api.validate_lifetime.side_effect = FoundryError("identity_changed", "PID reused")
        self.assert_code("identity_changed", leases.process_lease(identity()).__enter__)
        self.api.create_mutex.assert_not_called()
        self.observer.assert_not_called()
        self.api.close.assert_called_once_with("target")

    def test_full_identity_change_before_and_after_acquisition_is_refused(self):
        for field, replacement in (("creation_filetime", 124), ("executable_sha256", "b" * 64),
                                   ("hwnd", 101), ("thread_id", 201), ("client_width", 641),
                                   ("window_class", "Canvas-Other"), ("integrity", 4096)):
            for after in (False, True):
                self.api.reset_mock()
                calls = []
                def observe(*args):
                    calls.append(1)
                    result = identity()
                    if not after or len(calls) == 2:
                        result[field] = replacement
                    return result
                self.observer.side_effect = observe
                with self.subTest(field=field, after=after):
                    self.assert_code("identity_changed", leases.process_lease(identity()).__enter__)
                    self.assertEqual(int(after), self.api.release.call_count)
                    self.assertEqual(1 + int(after), self.api.close.call_count)

    def test_stale_missing_future_nonfinite_or_boolean_observation_refused(self):
        for timestamp in (None, 0, time.time() + 1000, float("nan"), float("inf"), True):
            self.observer.side_effect = lambda *args: dict(identity(), observed_at=timestamp)
            with self.subTest(timestamp=timestamp):
                self.assert_code("stale_observation", leases.process_lease(identity()).__enter__)
        self.api.create_mutex.assert_not_called()

    def test_clock_reversal_and_slow_observer_refused(self):
        for wall, monotonic in (([10, 9], [0, 1]), ([10, 11], [0, 6]), ([10, 11], [2, 1])):
            fixture = identity()
            self.observer.side_effect = lambda *args: dict(fixture, observed_at=10)
            with self.subTest(wall=wall, monotonic=monotonic), \
                    mock.patch.object(leases.time, "time", side_effect=wall), \
                    mock.patch.object(leases.time, "monotonic", side_effect=monotonic):
                self.assert_code("stale_observation", lambda: leases._observe(fixture))

    def test_target_dies_during_post_acquisition_observation(self):
        self.api.validate_lifetime.side_effect = [None, None, FoundryError("process_gone", "exited")]
        self.assert_code("process_gone", leases.process_lease(identity()).__enter__)
        self.api.release.assert_called_once()
        self.assertEqual([mock.call("mutex"), mock.call("target")], self.api.close.call_args_list)

    def test_exception_or_base_exception_releases_then_allows_next_context(self):
        for error in (RuntimeError("failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                with leases.process_lease(identity()):
                    raise error
            with leases.process_lease(identity()):
                pass
        self.assertEqual(4, self.api.release.call_count)

    def test_busy_wait_closes_without_releasing_and_keeps_no_reservation(self):
        self.api.acquire.side_effect = FoundryError("process_busy", "held elsewhere")
        self.assert_code("process_busy", leases.process_lease(identity()).__enter__)
        self.api.release.assert_not_called()
        self.assertEqual([mock.call("mutex"), mock.call("target")], self.api.close.call_args_list)
        self.api.acquire.side_effect = None
        with leases.process_lease(identity()):
            pass

    def test_create_failure_has_no_alternate_lock_scope(self):
        self.api.create_mutex.side_effect = FoundryError("windows_access", "denied")
        self.assert_code("windows_access", leases.process_lease(identity()).__enter__)
        self.api.create_mutex.assert_called_once()
        self.api.acquire.assert_not_called()
        self.api.close.assert_called_once_with("target")

    def test_cleanup_failure_poisoned_key_cannot_recursively_reenter(self):
        for method in ("release", "close"):
            with self.subTest(method=method), mock.patch.object(leases, "_held_names", set()):
                self.api.reset_mock()
                self.api.release.side_effect = None
                self.api.close.side_effect = None
                getattr(self.api, method).side_effect = FoundryError("windows_access", "cleanup failed")
                self.assert_code("windows_access", lambda: self._empty_context())
                self.api.release.side_effect = None
                self.api.close.side_effect = None
                self.assert_code("process_busy", leases.process_lease(identity()).__enter__)
                self.api.create_mutex.assert_called_once()

    @staticmethod
    def _empty_context():
        with leases.process_lease(identity()):
            pass


class NativeBindingContractTests(unittest.TestCase):
    """Mock DLL calls on every platform; no OS/game calls are made."""

    def setUp(self):
        self.kernel, self.advapi = mock.Mock(), mock.Mock()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(leases.os, "name", "nt"))
        self.stack.enter_context(mock.patch.object(ctypes, "WinDLL", create=True,
                                                  side_effect=[self.kernel, self.advapi]))
        self.last_error = self.stack.enter_context(mock.patch.object(ctypes, "get_last_error", create=True, return_value=5))
        self.api = leases._WindowsApi()

    def assert_code(self, code, action):
        with self.assertRaises(FoundryError) as caught:
            action()
        self.assertEqual(code, caught.exception.code)

    def test_mutex_minimal_access_noninherited_and_zero_wait(self):
        self.kernel.CreateMutexExW.return_value = 900
        self.kernel.WaitForSingleObject.return_value = leases.WAIT_OBJECT_0
        handle = self.api.create_mutex(r"Local\synthetic")
        self.assertFalse(self.api.acquire(handle))
        self.kernel.CreateMutexExW.assert_called_once_with(None, r"Local\synthetic", 0, 0x100001)
        self.kernel.WaitForSingleObject.assert_called_once_with(900, 0)
        self.assertIs(wt.HANDLE, self.kernel.CreateMutexExW.restype)
        self.assertEqual([wt.HANDLE, wt.DWORD], self.kernel.WaitForSingleObject.argtypes)

    def test_timeout_abandoned_failed_and_unexpected_waits(self):
        self.kernel.WaitForSingleObject.return_value = leases.WAIT_ABANDONED
        self.assertTrue(self.api.acquire(900))
        for status, code in ((leases.WAIT_TIMEOUT, "process_busy"), (leases.WAIT_FAILED, "windows_access"),
                             (7, "lease_wait_failed")):
            self.kernel.WaitForSingleObject.return_value = status
            with self.subTest(status=status):
                self.assert_code(code, lambda: self.api.acquire(900))

    def test_native_open_close_release_errors_are_not_silent(self):
        for function, action in ((self.kernel.CreateMutexExW, lambda: self.api.create_mutex("name")),
                                 (self.kernel.OpenProcess, lambda: self.api.open_target(42)),
                                 (self.kernel.ReleaseMutex, lambda: self.api.release(900)),
                                 (self.kernel.CloseHandle, lambda: self.api.close(900))):
            function.return_value = 0
            with self.subTest(function=function):
                self.assert_code("windows_access", action)
        self.kernel.OpenProcess.assert_called_once_with(0x101000, False, 42)

    def test_creation_filetime_not_undefined_exit_time_controls_identity(self):
        def times(handle, created, exited, kernel, user):
            ctypes.cast(created, ctypes.POINTER(wt.FILETIME)).contents.dwLowDateTime = 123
            ctypes.cast(exited, ctypes.POINTER(wt.FILETIME)).contents.dwLowDateTime = 456
            return 1
        self.kernel.GetProcessTimes.side_effect = times
        self.kernel.WaitForSingleObject.return_value = leases.WAIT_TIMEOUT
        self.api.validate_lifetime(100, identity())
        self.assert_code("identity_changed", lambda: self.api.validate_lifetime(100, dict(identity(), creation_filetime=124)))
        self.kernel.WaitForSingleObject.return_value = leases.WAIT_OBJECT_0
        self.assert_code("process_gone", lambda: self.api.validate_lifetime(100, identity()))
        self.kernel.WaitForSingleObject.return_value = leases.WAIT_FAILED
        self.assert_code("windows_access", lambda: self.api.validate_lifetime(100, identity()))

    def test_impersonation_query_must_be_no_token_not_arbitrary_failure(self):
        self.advapi.OpenThreadToken.return_value = 0
        self.last_error.return_value = 1008
        self.api.reject_impersonation()
        self.last_error.return_value = 5
        self.assert_code("windows_access", self.api.reject_impersonation)
        self.advapi.OpenThreadToken.return_value = 1
        self.assert_code("lease_scope", self.api.reject_impersonation)
        self.kernel.CloseHandle.assert_called_once()

    def mock_security_query(self):
        # Keep the Python-owned SID buffer alive through the mocked LocalFree.
        sid_text = ctypes.create_unicode_buffer("S-1-5-21-123")
        def open_token(process, access, output):
            ctypes.cast(output, ctypes.POINTER(wt.HANDLE))[0] = 700
            return 1
        def information(token, kind, output, size, needed):
            required = ctypes.cast(needed, ctypes.POINTER(wt.DWORD))
            if kind == 1:
                required[0] = 64
                if output is None:
                    return 0
                ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = 1234
            else:
                self.assertEqual(12, kind)
                required[0] = ctypes.sizeof(wt.DWORD)
                ctypes.cast(output, ctypes.POINTER(wt.DWORD))[0] = 3
            return 1
        def convert(sid, output):
            self.assertEqual(1234, sid)
            ctypes.cast(output, ctypes.POINTER(wt.LPWSTR))[0] = ctypes.cast(sid_text, wt.LPWSTR)
            return 1
        self.advapi.OpenProcessToken.side_effect = open_token
        self.advapi.GetTokenInformation.side_effect = information
        self.advapi.ConvertSidToStringSidW.side_effect = convert
        self.kernel.LocalFree.return_value = None
        self.last_error.return_value = 122

    def test_user_session_query_is_read_only_and_frees_both_resources(self):
        self.mock_security_query()
        self.assertEqual(("S-1-5-21-123", 3), self.api.security_context(100))
        self.assertEqual(0x0008, self.advapi.OpenProcessToken.call_args.args[1])
        self.assertEqual([1, 1, 12], [call.args[1] for call in self.advapi.GetTokenInformation.call_args_list])
        self.kernel.LocalFree.assert_called_once()
        self.assertEqual(700, self.kernel.CloseHandle.call_args.args[0].value)

    def test_user_query_buffer_or_access_failure_closes_token(self):
        for required, error in ((0, 122), (65537, 122), (64, 5)):
            self.mock_security_query()
            def information(token, kind, output, size, needed):
                ctypes.cast(needed, ctypes.POINTER(wt.DWORD))[0] = required
                return 0
            self.advapi.GetTokenInformation.side_effect = information
            self.last_error.return_value = error
            self.kernel.CloseHandle.reset_mock()
            with self.subTest(required=required, error=error):
                self.assert_code("windows_access", lambda: self.api.security_context(100))
                self.kernel.CloseHandle.assert_called_once()

    def test_sid_conversion_failure_closes_token_without_freeing_unallocated_memory(self):
        self.mock_security_query()
        self.advapi.ConvertSidToStringSidW.side_effect = None
        self.advapi.ConvertSidToStringSidW.return_value = 0
        self.assert_code("windows_access", lambda: self.api.security_context(100))
        self.kernel.CloseHandle.assert_called_once()
        self.kernel.LocalFree.assert_not_called()

    def test_session_query_failure_frees_sid_and_token(self):
        self.mock_security_query()
        original = self.advapi.GetTokenInformation.side_effect
        self.advapi.GetTokenInformation.side_effect = lambda token, kind, *args: (
            0 if kind == 12 else original(token, kind, *args))
        self.assert_code("windows_access", lambda: self.api.security_context(100))
        self.kernel.LocalFree.assert_called_once()
        self.kernel.CloseHandle.assert_called_once()


@unittest.skipIf(os.name == "nt", "Non-Windows refusal contract")
class UnsupportedPlatformTests(unittest.TestCase):
    def test_no_posix_or_workspace_fallback(self):
        with self.assertRaises(FoundryError) as caught:
            with leases.process_lease(identity()):
                self.fail("must refuse")
        self.assertEqual("windows_required", caught.exception.code)


_NATIVE_CI = os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"


@unittest.skipUnless(_NATIVE_CI, "Only disposable GitHub Windows runners may run native OS-lock fixtures")
class WindowsMutexFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="foundry-lease-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspaces = [self.root / "first", self.root / "second"]
        for workspace in self.workspaces:
            workspace.mkdir()
        self.name = "Local\\Foundry.ProcessLease.Test." + uuid.uuid4().hex
        self.api = leases._WindowsApi()
        self.env = dict(os.environ, PYTHONPATH=str(Path(leases.__file__).resolve().parents[1]))

    def run_child(self, code, workspace=1):
        return subprocess.run([sys.executable, "-c", code, self.name], cwd=self.workspaces[workspace],
                              env=self.env, capture_output=True, text=True, timeout=15, check=True)

    def test_different_workspaces_contend_then_release(self):
        code = """
import json, sys
from sims4foundry_cli.process_lease import _WindowsApi, _claim
from sims4foundry_cli.errors import FoundryError
try:
    with _claim(_WindowsApi(), sys.argv[1]) as abandoned:
        print(json.dumps({'acquired': True, 'abandoned': abandoned}))
except FoundryError as exc:
    print(json.dumps({'code': exc.code}))
"""
        with leases._claim(self.api, self.name):
            result = json.loads(self.run_child(code).stdout)
            self.assertEqual({"code": "process_busy"}, result)
        result = json.loads(self.run_child(code).stdout)
        self.assertEqual({"acquired": True, "abandoned": False}, result)

    def test_process_crash_releases_mutex_and_reports_abandoned(self):
        # Retain an unowned handle so Windows preserves the abandoned object.
        keeper = self.api.create_mutex(self.name)
        try:
            code = """
import os, sys
from sims4foundry_cli.process_lease import _WindowsApi, _claim
with _claim(_WindowsApi(), sys.argv[1]):
    os._exit(0)
"""
            self.run_child(code, workspace=0)
            with leases._claim(self.api, self.name) as abandoned:
                self.assertTrue(abandoned)
        finally:
            self.api.close(keeper)
        # No retained object means no durable abandonment evidence.
        with leases._claim(self.api, self.name) as abandoned:
            self.assertFalse(abandoned)

    def test_native_exception_release_and_same_thread_recursion_guard(self):
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            with leases._claim(self.api, self.name):
                with self.assertRaises(FoundryError) as caught:
                    with leases._claim(self.api, self.name):
                        self.fail("recursive ownership must be refused")
                self.assertEqual("process_busy", caught.exception.code)
                raise RuntimeError("fixture")
        with leases._claim(self.api, self.name) as abandoned:
            self.assertFalse(abandoned)


if __name__ == "__main__":
    unittest.main()
