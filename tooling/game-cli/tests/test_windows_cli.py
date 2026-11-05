from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib
from sims4foundry_cli import windows
from sims4foundry_cli.cli import main
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import sha256
from sims4foundry_cli.png import inspect_png


def png(width=2, height=2):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\xff\xff" * width) * height)) + chunk(b"IEND", b""))


def identity():
    return {"schema": 1, "kind": "foundry_windows_identity", "pid": 42, "creation_filetime": 123,
            "executable": "C:\\Games\\The Sims 4\\Game\\Bin\\TS4_x64.exe", "executable_sha256": "a" * 64,
            "hwnd": 100, "thread_id": 200, "window_class": "Canvas-Fixture", "client_width": 2,
            "client_height": 2, "client_origin": [0, 0], "dpi": 96, "integrity": 8192}


class WindowsContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ffmpeg = self.root / "ffmpeg.exe"
        self.ffmpeg.write_bytes(b"fixture-not-executable")
        self.pinned = False
        patcher = mock.patch.object(windows, "retained_capture_image", self.fixture_pin)
        patcher.start()
        self.addCleanup(patcher.stop)

    @contextmanager
    def fixture_pin(self, path, expected):
        if sha256(path) != expected:
            raise FoundryError("capture_tool_identity", "Fixture hash mismatch.")
        self.pinned = True
        try:
            yield
        finally:
            self.pinned = False

    def test_identity_fields_and_reuse_are_checked(self):
        before = identity()
        for key in windows.IDENTITY_KEYS:
            changed = deepcopy(before)
            if isinstance(changed[key], str):
                changed[key] += "x"
            elif isinstance(changed[key], list):
                changed[key][0] += 1
            else:
                changed[key] += 1
            with self.subTest(key=key), self.assertRaises(FoundryError):
                windows.same_identity(before, changed)
        windows.same_identity(before, deepcopy(before))

    def test_wrong_process_name_rejected(self):
        value = identity()
        value["executable"] = "C:\\Windows\\explorer.exe"
        with self.assertRaises(FoundryError):
            windows.validate_identity(value)

    def test_boolean_identity_numbers_rejected(self):
        for key in ("pid", "hwnd", "creation_filetime", "schema", "client_width"):
            value = identity(); value[key] = True
            with self.subTest(key=key), self.assertRaises(FoundryError):
                windows.validate_identity(value)

    @unittest.skipIf(os.name == "nt", "Linux refusal check only")
    def test_non_windows_refuses_passive_native_call(self):
        with self.assertRaises(FoundryError) as caught:
            windows.inspect(42, "none", "a" * 64)
        self.assertEqual("windows_required", caught.exception.code)

    def test_capture_validates_png_but_not_semantics(self):
        def runner(argv, **kwargs):
            self.assertTrue(self.pinned)
            self.assertIn("-n", argv)
            self.assertIn("-nostdin", argv)
            self.assertEqual(30, kwargs["timeout"])
            self.assertFalse(kwargs.get("shell", False))
            Path(argv[-1]).write_bytes(png())
            return mock.Mock(returncode=0)
        result = windows.capture(identity(), self.ffmpeg, sha256(self.ffmpeg), self.root / "capture",
                                 observer=lambda *args: identity(), runner=runner)
        self.assertTrue(result["ok"])
        self.assertFalse(result["semantic_state_verified"])
        self.assertFalse(result["input_sent"])
        self.assertFalse(result["live_input_qualified"])
        self.assertFalse(self.pinned)

    def test_capture_identity_mismatch_never_launches_helper(self):
        changed = identity(); changed["creation_filetime"] += 1
        runner = mock.Mock()
        with self.assertRaises(FoundryError):
            windows.capture(identity(), self.ffmpeg, sha256(self.ffmpeg), self.root / "capture",
                            observer=lambda *args: changed, runner=runner)
        runner.assert_not_called()
        self.assertFalse((self.root / "capture").exists())

    def test_capture_tool_hash_mismatch_never_runs(self):
        runner = mock.Mock()
        with self.assertRaises(FoundryError):
            windows.capture(identity(), self.ffmpeg, "b" * 64, self.root / "capture",
                            observer=lambda *args: identity(), runner=runner)
        runner.assert_not_called()

    def test_capture_identity_changed_after_helper_not_success(self):
        sequence = [identity(), dict(identity(), thread_id=500)]
        def runner(argv, **kwargs):
            Path(argv[-1]).write_bytes(png())
            return mock.Mock(returncode=0)
        with self.assertRaises(FoundryError):
            windows.capture(identity(), self.ffmpeg, sha256(self.ffmpeg), self.root / "capture",
                            observer=lambda *args: sequence.pop(0), runner=runner)
        self.assertFalse((self.root / "capture" / "capture-complete.json").exists())

    def test_pin_cleanup_failure_never_records_capture_complete(self):
        @contextmanager
        def failed_cleanup(path, expected):
            yield
            raise FoundryError("capture_pin_cleanup_failed", "Fixture cleanup failed.")
        def runner(argv, **kwargs):
            Path(argv[-1]).write_bytes(png())
            return mock.Mock(returncode=0)
        with mock.patch.object(windows, "retained_capture_image", failed_cleanup):
            with self.assertRaises(FoundryError) as error:
                windows.capture(identity(), self.ffmpeg, sha256(self.ffmpeg), self.root / "capture",
                                observer=lambda *args: identity(), runner=runner)
        self.assertEqual("capture_pin_cleanup_failed", error.exception.code)
        self.assertTrue((self.root / "capture" / "frame.png").exists())
        self.assertFalse((self.root / "capture" / "capture-complete.json").exists())

    def test_valid_png(self):
        path = self.root / "image.png"; path.write_bytes(png())
        self.assertEqual((2, 2), inspect_png(path))

    def test_truncated_crc_and_extra_data_png_rejected(self):
        raw = png()
        for value in (raw[:24], raw[:-1], raw + b"extra", raw[:40] + b"x" + raw[41:]):
            path = self.root / "image.png"; path.write_bytes(value)
            with self.assertRaises(FoundryError):
                inspect_png(path)

    def test_no_forbidden_native_mutators_in_source(self):
        import ast
        source = Path(windows.__file__).read_text()
        tree = ast.parse(source)
        forbidden = {"SendInput", "PostMessageW", "SetForegroundWindow", "SetCursorPos", "TerminateProcess", "ShellExecuteW", "WriteProcessMemory"}
        literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertTrue(forbidden.isdisjoint(literals))


class CLITests(unittest.TestCase):
    def call(self, argv):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main(argv)
        return code, json.loads(stream.getvalue())

    def test_capabilities_json(self):
        code, result = self.call(["capabilities"])
        self.assertEqual(0, code)
        self.assertFalse(result["real_game_control_ready"])

    def test_source_details_do_not_enable_dispatch(self):
        code, result = self.call(["capabilities", "--source-details"])
        self.assertEqual(0, code)
        self.assertEqual(45, len(result["operations"]))
        for operation in result["operations"]:
            self.assertFalse(operation["migration_source"]["live_qualified"])
            self.assertFalse(operation["migration_source"]["dispatch_available"])

    def test_resource_and_overlap_routes_are_read_only(self):
        payload = {"ok": True, "game_modified": False, "compatibility_verified": False}
        with mock.patch("sims4foundry_cli.cli.package_resources.inspect_resources", return_value=payload) as provider:
            code, result = self.call(["mods", "resources", "--file", "one.package"])
        provider.assert_called_once_with("one.package")
        self.assertEqual((0, payload), (code, result))
        with mock.patch("sims4foundry_cli.cli.package_resources.audit_overlaps", return_value=payload) as provider:
            code, result = self.call(["mods", "overlaps", "--file", "one.package", "--file", "two.package"])
        provider.assert_called_once_with(["one.package", "two.package"])
        self.assertEqual((0, payload), (code, result))

    def test_discovery_routes_only_to_passive_provider(self):
        result = {"ok": True, "status": "none_observed", "launch_qualified": False,
                  "real_game_control_ready": False, "input_sent": False, "absence_proven": False}
        with mock.patch("sims4foundry_cli.cli.discovery.discover", return_value=result) as provider:
            code, returned = self.call(["discover", "--executable", "fixture", "--executable-sha256", "a"*64])
        provider.assert_called_once_with("fixture", "a"*64)
        self.assertEqual(0, code)
        self.assertEqual(result, returned)

    def test_discovery_failure_is_structured_nonzero(self):
        with mock.patch("sims4foundry_cli.cli.discovery.discover", side_effect=FoundryError("discovery_incomplete", "No complete snapshot.")):
            code, result = self.call(["discover", "--executable", "fixture", "--executable-sha256", "a"*64])
        self.assertEqual(2, code)
        self.assertEqual("discovery_incomplete", result["code"])

    def test_semantic_command_emits_only_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = str(Path(tmp) / "sim")
            self.call(["session", "create", "--directory", session])
            code, plan = self.call(["game", "launch", "--session", session])
            self.assertEqual(0, code)
            self.assertFalse(plan["executed"])
            code, state = self.call(["session", "status", "--directory", session])
            self.assertEqual("closed", state["state"]["mode"])

    def test_invalid_arguments_nonzero_json(self):
        code, result = self.call(["game", "launch", "--session", "not-read", "--arguments", '{"x":NaN}'])
        self.assertEqual(2, code)
        self.assertEqual("invalid_json", result["code"])

    def test_live_create_nonzero(self):
        code, result = self.call(["session", "create", "--backend", "windows", "--directory", "not-created"])
        self.assertEqual(2, code)
        self.assertEqual("live_backend_unqualified", result["code"])


if __name__ == "__main__":
    unittest.main()
