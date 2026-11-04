"""Fake snapshot/observer coverage; native enumeration only on Windows CI."""
import ast
import ctypes
import os
from pathlib import Path, PureWindowsPath
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sims4foundry_cli import discovery
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import sha256


SELECTED = PureWindowsPath(r"C:\Games\The Sims 4\Game\Bin\TS4_x64.exe")
CHECKSUM = "a" * 64


def identity(pid=42):
    return {"schema": 1, "kind": "foundry_windows_identity", "pid": pid,
            "creation_filetime": 123, "executable": str(SELECTED),
            "executable_sha256": CHECKSUM, "hwnd": 100, "thread_id": 200,
            "window_class": "Canvas-Fixture", "client_width": 320,
            "client_height": 240, "client_origin": [0, 0], "dpi": 96,
            "integrity": 8192, "passive": True, "input_sent": False,
            "real_game_control_ready": False}


class FakeToolhelp:
    def __init__(self, rows=(), *, end_error=18, snapshot_handle=99, close_ok=True):
        self.rows = list(rows)
        self.end_error = end_error
        self.snapshot_handle = snapshot_handle
        self.close_ok = close_ok
        self.calls = []
        self.position = 0

    def snapshot(self, flags, pid):
        self.calls.append(("snapshot", flags, pid))
        return self.snapshot_handle

    def _fill(self, pointer):
        if self.position == len(self.rows):
            return False
        pid, image = self.rows[self.position]
        entry = ctypes.cast(pointer, ctypes.POINTER(discovery._ProcessEntry32W)).contents
        if entry.dwSize != ctypes.sizeof(discovery._ProcessEntry32W):
            raise AssertionError("dwSize was not initialized")
        entry.th32ProcessID = pid
        entry.szExeFile = image
        self.position += 1
        return True

    def first(self, handle, pointer):
        self.calls.append(("first", handle))
        return self._fill(pointer)

    def next(self, handle, pointer):
        self.calls.append(("next", handle))
        return self._fill(pointer)

    def close(self, handle):
        self.calls.append(("close", handle))
        return self.close_ok

    def last_error(self):
        return self.end_error


class SnapshotTests(unittest.TestCase):
    def test_exact_basename_filter_without_opening_unrelated_processes(self):
        api = FakeToolhelp([(0, "System Idle Process"), (8, "explorer.exe"),
                            (10, "TS4_x64.exe.bak"), (11, "my_TS4_x64.exe"),
                            (12, r"C:\Games\TS4_x64.exe"), (13, "TS4_x64.exe "),
                            (42, "TS4_x64.exe"), (43, "ts4_X64.EXE")])
        self.assertEqual([42, 43], discovery._snapshot_image_pids("TS4_x64.exe", api=api))
        self.assertEqual(("snapshot", 2, 0), api.calls[0])
        self.assertEqual(("close", 99), api.calls[-1])
        self.assertEqual({"snapshot", "first", "next", "close"}, {c[0] for c in api.calls})

    def test_complete_empty_snapshot_is_distinct_from_error(self):
        api = FakeToolhelp()
        self.assertEqual([], discovery._snapshot_image_pids("TS4_x64.exe", api=api))
        self.assertEqual([("snapshot", 2, 0), ("first", 99), ("close", 99)], api.calls)

    def test_snapshot_failure_never_closes_an_invalid_handle_or_retries(self):
        for value in (None, 0, discovery.INVALID_HANDLE_VALUE):
            api = FakeToolhelp(snapshot_handle=value, end_error=5)
            with self.subTest(value=value), self.assertRaises(FoundryError) as caught:
                discovery._snapshot_image_pids("TS4_x64.exe", api=api)
            self.assertEqual("discovery_incomplete", caught.exception.code)
            self.assertEqual([("snapshot", 2, 0)], api.calls)

    def test_first_failure_is_not_an_empty_success(self):
        api = FakeToolhelp(end_error=5)
        with self.assertRaises(FoundryError) as caught:
            discovery._snapshot_image_pids("TS4_x64.exe", api=api)
        self.assertEqual("discovery_incomplete", caught.exception.code)
        self.assertEqual(5, caught.exception.details["windows_error"])
        self.assertEqual(("close", 99), api.calls[-1])

    def test_next_failure_discards_partial_results_and_closes(self):
        api = FakeToolhelp([(42, "TS4_x64.exe")], end_error=24)
        with self.assertRaises(FoundryError) as caught:
            discovery._snapshot_image_pids("TS4_x64.exe", api=api)
        self.assertEqual("discovery_incomplete", caught.exception.code)
        self.assertEqual(1, sum(c[0] == "snapshot" for c in api.calls))
        self.assertEqual(("close", 99), api.calls[-1])

    def test_total_enumeration_bound_includes_unrelated_images(self):
        api = FakeToolhelp([(1, "unrelated.exe"), (2, "unrelated.exe"), (42, "TS4_x64.exe")])
        with self.assertRaises(FoundryError) as caught:
            discovery._snapshot_image_pids("TS4_x64.exe", max_processes=2, api=api)
        self.assertEqual("discovery_limit", caught.exception.code)
        self.assertEqual(("close", 99), api.calls[-1])

    def test_exact_enumeration_bound_can_complete(self):
        api = FakeToolhelp([(42, "TS4_x64.exe"), (7, "unrelated.exe")])
        self.assertEqual([42], discovery._snapshot_image_pids("TS4_x64.exe", max_processes=2, api=api))

    def test_candidate_bound_fails_instead_of_truncating(self):
        api = FakeToolhelp([(42, "TS4_x64.exe"), (43, "TS4_x64.exe")])
        with self.assertRaises(FoundryError) as caught:
            discovery._snapshot_image_pids("TS4_x64.exe", max_candidates=1, api=api)
        self.assertEqual("discovery_limit", caught.exception.code)
        self.assertEqual(("close", 99), api.calls[-1])

    def test_invalid_or_duplicate_candidate_fails_and_closes(self):
        for rows in ([(0, "TS4_x64.exe")], [(42, "TS4_x64.exe"), (42, "TS4_x64.exe")]):
            api = FakeToolhelp(rows)
            with self.subTest(rows=rows), self.assertRaises(FoundryError) as caught:
                discovery._snapshot_image_pids("TS4_x64.exe", api=api)
            self.assertEqual("invalid_discovery_snapshot", caught.exception.code)
            self.assertEqual(("close", 99), api.calls[-1])

    def test_unexpected_walk_exception_still_closes_once(self):
        api = FakeToolhelp()
        api.first = mock.Mock(side_effect=RuntimeError("fixture exception"))
        with self.assertRaises(RuntimeError):
            discovery._snapshot_image_pids("TS4_x64.exe", api=api)
        self.assertEqual(1, sum(c[0] == "close" for c in api.calls))

    def test_close_failure_never_returns_success(self):
        api = FakeToolhelp(close_ok=False)
        with self.assertRaises(FoundryError) as caught:
            discovery._snapshot_image_pids("TS4_x64.exe", api=api)
        self.assertEqual("discovery_cleanup", caught.exception.code)
        self.assertEqual(1, sum(c[0] == "close" for c in api.calls))

    def test_invalid_limits_rejected_before_any_api_call(self):
        for field, maximum in (("max_processes", discovery.MAX_PROCESSES),
                               ("max_candidates", discovery.MAX_CANDIDATES)):
            for value in (True, False, 0, -1, 1.0, "1", maximum + 1):
                api = FakeToolhelp()
                with self.subTest(field=field, value=value), self.assertRaises(FoundryError):
                    discovery._snapshot_image_pids("TS4_x64.exe", api=api, **{field: value})
                self.assertEqual([], api.calls)

    def test_only_literal_basename_accepted(self):
        for image in (None, "", ".", "..", "*", r"C:\TS4_x64.exe", "/TS4_x64.exe", "a\0b", "x" * 260):
            api = FakeToolhelp()
            with self.subTest(image=image), self.assertRaises(FoundryError):
                discovery._snapshot_image_pids(image, api=api)
            self.assertEqual([], api.calls)

    def test_game_wrapper_fixes_filter_name(self):
        with mock.patch.object(discovery, "_snapshot_image_pids", return_value=[42]) as walk:
            self.assertEqual([42], discovery.candidate_pids(max_processes=100, max_candidates=4))
        walk.assert_called_once_with("TS4_x64.exe", max_processes=100, max_candidates=4)

    @unittest.skipIf(os.name == "nt", "Non-Windows refusal check only")
    def test_native_enumeration_refused_on_non_windows(self):
        with self.assertRaises(FoundryError) as caught:
            discovery.candidate_pids()
        self.assertEqual("windows_required", caught.exception.code)

    def test_bindings_use_exact_wide_apis_and_pointer_types(self):
        kernel = SimpleNamespace(**{name: mock.Mock() for name in
                                  ("CreateToolhelp32Snapshot", "Process32FirstW", "Process32NextW", "CloseHandle")})
        with mock.patch.object(discovery, "os", SimpleNamespace(name="nt")), \
                mock.patch.object(ctypes, "WinDLL", return_value=kernel, create=True) as load, \
                mock.patch.object(ctypes, "get_last_error", return_value=18, create=True):
            discovery._Toolhelp()
        load.assert_called_once_with("kernel32", use_last_error=True)
        self.assertEqual([discovery.wt.DWORD, discovery.wt.DWORD], kernel.CreateToolhelp32Snapshot.argtypes)
        self.assertIs(discovery.wt.HANDLE, kernel.CreateToolhelp32Snapshot.restype)
        for name in ("Process32FirstW", "Process32NextW"):
            self.assertEqual([discovery.wt.HANDLE, ctypes.POINTER(discovery._ProcessEntry32W)], getattr(kernel, name).argtypes)
        self.assertEqual(ctypes.c_size_t, dict(discovery._ProcessEntry32W._fields_)["th32DefaultHeapID"])
        self.assertEqual(ctypes.c_uint32, dict(discovery._ProcessEntry32W._fields_)["th32ProcessID"])


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        # PureWindowsPath stands in for a pinned Windows installation on Linux.
        # Real selected-file/link/hash checks have their own tests below.
        self.path = mock.patch.object(discovery, "plain_path", return_value=SELECTED).start()
        self.hash = mock.patch.object(discovery, "sha256", return_value=CHECKSUM).start()
        self.addCleanup(mock.patch.stopall)
        self.enumerator = mock.Mock(return_value=[42])
        self.observer = mock.Mock(side_effect=lambda pid, *_: identity(pid))

    def run_discovery(self, **kwargs):
        return discovery.discover(str(SELECTED), CHECKSUM,
                                  enumerator=self.enumerator, observer=self.observer, **kwargs)

    def assert_unqualified(self, result):
        self.assertTrue(result["passive"])
        for key in ("input_sent", "launched", "absence_proven", "launch_qualified", "real_game_control_ready"):
            self.assertIs(False, result[key], key)

    def test_exact_single_match_is_observation_only(self):
        result = self.run_discovery(max_processes=100, max_candidates=4)
        self.assertEqual("one_observed", result["status"])
        self.assertEqual([identity()], result["matches"])
        self.assertEqual(1, result["candidate_count"])
        self.assertEqual(0, result["blocker_count"])
        self.assert_unqualified(result)
        self.enumerator.assert_called_once_with(max_processes=100, max_candidates=4)
        self.observer.assert_called_once_with(42, SELECTED, CHECKSUM)
        self.assertEqual(2, self.hash.call_count)

    def test_empty_complete_snapshot_is_not_launch_admission(self):
        self.enumerator.return_value = []
        result = self.run_discovery()
        self.assertEqual("none_observed", result["status"])
        self.assertEqual(0, result["candidate_count"])
        self.assertTrue(result["snapshot_complete"])
        self.assert_unqualified(result)
        self.observer.assert_not_called()

    def test_every_candidate_is_observed_without_selection(self):
        self.enumerator.return_value = [42, 43]
        result = self.run_discovery()
        self.assertEqual("ambiguous", result["status"])
        self.assertEqual([42, 43], [item["pid"] for item in result["matches"]])
        self.assertEqual(2, self.observer.call_count)
        self.assert_unqualified(result)

    def test_unobservable_candidate_never_becomes_none_found(self):
        for code in ("windows_access", "process_gone", "ambiguous_window", "identity_changed", "wrong_process"):
            self.observer.side_effect = FoundryError(code, "Fixture refusal", count=2)
            with self.subTest(code=code):
                result = self.run_discovery()
                self.assertEqual("blocked", result["status"])
                self.assertEqual(1, result["blocker_count"])
                self.assertEqual([], result["matches"])
                self.assertEqual(code, result["candidates"][0]["error"]["code"])
                self.assertEqual(2, result["candidates"][0]["error"]["count"])
                self.assert_unqualified(result)

    def test_blocked_candidate_does_not_hide_other_matches_or_retry(self):
        self.enumerator.return_value = [41, 42, 43]
        self.observer.side_effect = [FoundryError("windows_access", "Cannot query"), identity(42), identity(43)]
        result = self.run_discovery()
        self.assertEqual("blocked", result["status"])
        self.assertEqual(2, result["matched_count"])
        self.assertEqual(1, result["blocker_count"])
        self.assertEqual([41, 42, 43], [c.args[0] for c in self.observer.call_args_list])
        self.enumerator.assert_called_once()
        self.assert_unqualified(result)

    def test_os_error_remains_an_unresolved_candidate(self):
        self.observer.side_effect = PermissionError(13, "Fixture access denied")
        result = self.run_discovery()
        self.assertEqual("blocked", result["status"])
        self.assertEqual("observation_unavailable", result["candidates"][0]["error"]["code"])
        self.assertEqual(13, result["candidates"][0]["error"]["os_error"])

    def test_observer_cannot_return_another_pid_path_or_hash(self):
        for key, value in (("pid", 43), ("executable", r"C:\Other\TS4_x64.exe"),
                           ("executable_sha256", "b" * 64)):
            self.observer.side_effect = None
            self.observer.return_value = dict(identity(), **{key: value})
            with self.subTest(key=key):
                result = self.run_discovery()
                self.assertEqual("blocked", result["status"])
                self.assertEqual("mismatched", result["candidates"][0]["status"])
                self.assertEqual([], result["matches"])

    def test_observed_identity_numeric_fields_are_strict(self):
        cases = [("pid", True), ("pid", 2**32), ("thread_id", True), ("thread_id", 2**32),
                 ("creation_filetime", 0), ("creation_filetime", True), ("creation_filetime", 2**64),
                 ("hwnd", 0), ("schema", True)]
        for key, value in cases:
            self.observer.side_effect = None
            self.observer.return_value = dict(identity(), **{key: value})
            with self.subTest(key=key, value=value):
                result = self.run_discovery()
                self.assertEqual("blocked", result["status"])
                self.assertEqual([], result["matches"])

    def test_nonpassive_or_qualified_identity_is_rejected(self):
        for key, value in (("passive", False), ("input_sent", True), ("input_sent", 0),
                           ("real_game_control_ready", True)):
            self.observer.side_effect = None
            self.observer.return_value = dict(identity(), **{key: value})
            with self.subTest(key=key, value=value):
                self.assertEqual("blocked", self.run_discovery()["status"])

    def test_malformed_observation_is_not_treated_as_absence(self):
        for value in (None, [], {}, "identity"):
            self.observer.side_effect = None
            self.observer.return_value = value
            with self.subTest(value=value):
                self.assertEqual("blocked", self.run_discovery()["status"])

    def test_returned_identity_is_copied(self):
        value = identity()
        self.observer.side_effect = None
        self.observer.return_value = value
        result = self.run_discovery()
        value["client_origin"][0] = 99
        self.assertEqual([0, 0], result["matches"][0]["client_origin"])

    def test_bad_snapshot_is_rejected_before_any_observation(self):
        cases = [None, (), iter([42]), [True], [0], [-1], [2**32], [42.0], ["42"], [42, 42],
                 list(range(1, discovery.MAX_CANDIDATES + 2))]
        for value in cases:
            self.enumerator.return_value = value
            with self.subTest(value=value), self.assertRaises(FoundryError) as caught:
                self.run_discovery()
            self.assertEqual("invalid_discovery_snapshot", caught.exception.code)
            self.observer.assert_not_called()

    def test_maximum_dword_pid_is_not_truncated(self):
        self.enumerator.return_value = [0xffffffff]
        result = self.run_discovery()
        self.assertEqual(0xffffffff, result["matches"][0]["pid"])
        self.observer.assert_called_once_with(0xffffffff, SELECTED, CHECKSUM)

    def test_incomplete_enumeration_is_never_a_partial_report(self):
        self.enumerator.side_effect = FoundryError("discovery_incomplete", "Snapshot incomplete")
        with self.assertRaises(FoundryError) as caught:
            self.run_discovery()
        self.assertEqual("discovery_incomplete", caught.exception.code)
        self.enumerator.assert_called_once()
        self.observer.assert_not_called()

    def test_selected_file_drift_even_after_empty_snapshot_is_rejected(self):
        self.enumerator.return_value = []
        self.hash.side_effect = [CHECKSUM, "b" * 64]
        with self.assertRaises(FoundryError) as caught:
            self.run_discovery()
        self.assertEqual("wrong_executable", caught.exception.code)

    def test_selected_path_becoming_linked_is_rejected_on_recheck(self):
        self.path.side_effect = [SELECTED, FoundryError("linked_path", "Changed path")]
        with self.assertRaises(FoundryError) as caught:
            self.run_discovery()
        self.assertEqual("linked_path", caught.exception.code)

    def test_enumerator_or_observer_is_not_retried_after_unexpected_failure(self):
        self.observer.side_effect = RuntimeError("Fixture unexpected failure")
        with self.assertRaises(RuntimeError):
            self.run_discovery()
        self.enumerator.assert_called_once()
        self.observer.assert_called_once()

    def test_defaults_resolve_native_enumerator_and_passive_observer_at_call_time(self):
        with mock.patch.object(discovery, "candidate_pids", return_value=[42]) as enum, \
                mock.patch.object(discovery.windows, "inspect", return_value=identity()) as observe:
            self.assertEqual("one_observed", discovery.discover(SELECTED, CHECKSUM)["status"])
        enum.assert_called_once()
        observe.assert_called_once_with(42, SELECTED, CHECKSUM)


class SelectedFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / "TS4_x64.exe"
        self.exe.write_bytes(b"synthetic-not-executable")
        self.enum = mock.Mock(return_value=[])
        self.observe = mock.Mock()

    def discover(self, executable=None, checksum=None):
        return discovery.discover(self.exe if executable is None else executable,
                                  sha256(self.exe) if checksum is None else checksum,
                                  enumerator=self.enum, observer=self.observe)

    def test_real_file_hash_checked_without_writes_or_execution(self):
        before = {str(p): sha256(p) for p in self.root.iterdir()}
        result = self.discover()
        self.assertEqual("none_observed", result["status"])
        self.assertEqual(before, {str(p): sha256(p) for p in self.root.iterdir()})
        self.observe.assert_not_called()

    def test_invalid_hash_is_refused_before_enumeration(self):
        for value in ("", "a" * 63, "a" * 65, "A" * 64, "g" * 64, 12, True):
            with self.subTest(value=value), self.assertRaises(FoundryError):
                self.discover(checksum=value)
            self.enum.assert_not_called()

    def test_wrong_name_or_hash_refused_before_enumeration(self):
        other = self.root / "TS4.exe"
        other.write_bytes(self.exe.read_bytes())
        for executable, checksum in ((other, sha256(other)), (self.exe, "0" * 64)):
            with self.subTest(executable=executable), self.assertRaises(FoundryError) as caught:
                self.discover(executable, checksum)
            self.assertEqual("wrong_executable", caught.exception.code)
            self.enum.assert_not_called()

    def test_missing_selected_file_refused_before_enumeration(self):
        with self.assertRaises(FoundryError) as caught:
            self.discover(self.root / "missing" / "TS4_x64.exe", "a" * 64)
        self.assertEqual("executable_unavailable", caught.exception.code)
        self.enum.assert_not_called()

    def test_selected_symlink_is_rejected_before_enumeration(self):
        directory = self.root / "linked"
        directory.mkdir()
        link = directory / "TS4_x64.exe"
        try:
            link.symlink_to(self.exe)
        except OSError as exc:
            self.skipTest("Test symlinks unavailable: " + str(exc))
        with self.assertRaises(FoundryError) as caught:
            self.discover(link)
        self.assertEqual("linked_path", caught.exception.code)
        self.enum.assert_not_called()

    def test_selected_parent_symlink_is_rejected(self):
        directory = self.root / "linked-parent"
        try:
            directory.symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest("Test symlinks unavailable: " + str(exc))
        with self.assertRaises(FoundryError) as caught:
            self.discover(directory / "TS4_x64.exe")
        self.assertEqual("linked_path", caught.exception.code)
        self.enum.assert_not_called()

    def test_reparse_attribute_is_rejected_before_enumeration(self):
        original = Path.lstat
        selected = self.exe
        def lstat(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400) if path == selected else info
        with mock.patch.object(Path, "lstat", lstat), self.assertRaises(FoundryError) as caught:
            self.discover(checksum="a" * 64)
        self.assertEqual("linked_path", caught.exception.code)
        self.enum.assert_not_called()

    def test_source_contains_no_native_action_or_process_scan_api(self):
        tree = ast.parse(Path(discovery.__file__).read_text())
        forbidden = {"SendInput", "PostMessageW", "SetForegroundWindow", "SetCursorPos", "TerminateProcess",
                     "CreateProcessW", "ShellExecuteW", "WriteProcessMemory", "OpenProcess", "EnumProcesses",
                     "AdjustTokenPrivileges", "RegOpenKeyExW"}
        literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertTrue(forbidden.isdisjoint(literals))


@unittest.skipUnless(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true",
                     "Native enumeration is restricted to disposable GitHub Windows runners")
class NativeEnumerationTests(unittest.TestCase):
    def test_current_python_process_in_native_name_filtered_snapshot(self):
        # No fixture spawn, individual process inspection, window query or input.
        # The current interpreter is enough to validate Toolhelp's native ABI.
        self.assertEqual(2, ctypes.sizeof(ctypes.c_wchar))
        expected_size = 568 if ctypes.sizeof(ctypes.c_void_p) == 8 else 556
        self.assertEqual(expected_size, ctypes.sizeof(discovery._ProcessEntry32W))
        pids = discovery._snapshot_image_pids(Path(sys.executable).name)
        self.assertIn(os.getpid(), pids)
        self.assertEqual(len(pids), len(set(pids)))
        self.assertTrue(all(type(pid) is int and 1 <= pid <= 0xffffffff for pid in pids))


if __name__ == "__main__":
    unittest.main()
