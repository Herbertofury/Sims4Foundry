"""Owned compiler/archive fixtures only; isolated imports never touch a game."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

from sims4foundry_cli import companion_build as build
from sims4foundry_cli import launch
from sims4foundry_cli.errors import FoundryError


def compiler_response(request):
    result = []
    for row in json.loads(request):
        source = base64.b64decode(row["source"])
        source_hash = hashlib.sha256(source).digest()[:8]
        # An intentionally opaque fake body; the parent never unmarshals it.
        pyc = build.PYC_PREFIX + source_hash + b"fake-compiler-body"
        result.append({"module": row["module"][:-3] + ".pyc",
                       "source_sha256": hashlib.sha256(source).hexdigest(),
                       "source_hash": source_hash.hex(),
                       "payload": base64.b64encode(pyc).decode("ascii")})
    return {"python": "3.7.17", "magic": "420d0d0a", "modules": result}


class CompanionBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "companion-src"
        for index, name in enumerate(build.MODULES):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("VALUE = %d\nraise RuntimeError('must never execute')\n" % index).encode("ascii"))
        self.compiler = self.root / "explicit-python37"
        self.compiler.write_bytes(b"fixture executable, mocked and never run")
        self.pin = hashlib.sha256(self.compiler.read_bytes()).hexdigest()
        self.output = self.root / "dist" / "FoundryCompanion.ts4script"

    def fake_run(self, compiler, request, scratch):
        self.assertEqual(self.compiler, compiler)
        self.assertTrue(Path(scratch).is_dir())
        return json.dumps(compiler_response(request)).encode("ascii")

    def run_build(self, **kwargs):
        return build.build_companion(kwargs.pop("source_root", self.source),
                                     kwargs.pop("output", self.output),
                                     kwargs.pop("python37", self.compiler),
                                     kwargs.pop("compiler_sha256", self.pin), **kwargs)

    def refused(self, code, **kwargs):
        with self.assertRaises(FoundryError) as caught:
            self.run_build(**kwargs)
        self.assertEqual(code, caught.exception.code, str(caught.exception))
        return caught.exception

    def test_exact_allowlist_checked_hash_headers_manifest_and_no_execution(self):
        with mock.patch.object(build, "_run_compiler", side_effect=self.fake_run) as run:
            manifest = self.run_build()
        self.assertEqual(1, run.call_count)
        self.assertEqual(42, manifest["member_count"])
        self.assertEqual(list(build.MODULES), [row["module"] for row in manifest["modules"]])
        raw = self.output.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["sha256"])
        manifest_path = self.output.with_name(self.output.name + ".manifest.json")
        self.assertEqual(manifest, json.loads(manifest_path.read_bytes()))
        for field in ("installed", "profile_modified", "game_contacted", "companion_code_executed", "live_qualified"):
            self.assertIs(False, manifest[field])
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(sorted(archive.namelist()), archive.namelist())
            self.assertIsNone(archive.testzip())
            self.assertEqual(b"", archive.comment)
            for item in archive.infolist():
                self.assertEqual((1980, 1, 1, 0, 0, 0), item.date_time)
                self.assertEqual(zipfile.ZIP_STORED, item.compress_type)
                self.assertEqual(0o100644 << 16, item.external_attr)
                self.assertEqual(b"", item.extra)
                if item.filename.endswith(".py"):
                    self.assertEqual((self.source / item.filename).read_bytes(), archive.read(item))
                else:
                    self.assertEqual(build.PYC_PREFIX, archive.read(item)[:8])
        self.assertEqual([], list(self.output.parent.glob(".foundry-*")))

    def test_build_adapters_are_in_the_exact_package_allowlist(self):
        self.assertEqual({
            'build_create', 'build_destroy', 'build_native_readers',
            'build_observation', 'build_transform', 'build_variant',
            'build_executor', 'build_request', 'build_schema', 'build_wire'},
            {Path(name).stem for name in build.MODULES
             if Path(name).stem.startswith('build_')})
        self.assertEqual(21, len(build.MODULES))

    def test_deterministic_under_source_root_mtime_and_output_directory_changes(self):
        with mock.patch.object(build, "_run_compiler", side_effect=self.fake_run):
            first = self.run_build()
            moved = self.root / "moved-source"
            shutil.copytree(self.source, moved)
            for name in build.MODULES:
                os.utime(moved / name, (1000000000, 1000000000))
            second_path = self.root / "other-dist" / self.output.name
            second = self.run_build(source_root=moved, output=second_path)
        self.assertEqual(first, second)
        self.assertEqual(self.output.read_bytes(), second_path.read_bytes())

    def test_explicit_manifest_destination(self):
        destination = self.root / "evidence" / "release.json"
        with mock.patch.object(build, "_run_compiler", side_effect=self.fake_run):
            result = self.run_build(manifest_output=destination)
        self.assertEqual(result, json.loads(destination.read_bytes()))
        self.assertFalse(self.output.with_name(self.output.name + ".manifest.json").exists())

    def test_invalid_pin_refused_before_filesystem(self):
        for pin in (None, "", "a" * 63, "g" * 64, "a" * 64 + "\n", 3):
            with self.subTest(pin=pin), mock.patch.object(build, "plain_path", side_effect=AssertionError("filesystem reached")):
                self.refused("build_compiler_hash", compiler_sha256=pin)

    def test_mismatched_pin_never_executes_or_creates_output_parent(self):
        with mock.patch.object(build, "_run_compiler", side_effect=AssertionError("compiler reached")):
            self.refused("build_compiler_hash", compiler_sha256="0" * 64)
        self.assertFalse(self.output.parent.exists())

    def test_missing_unknown_or_nested_sources_are_refused(self):
        target = self.source / build.MODULES[0]
        original = target.read_bytes()
        target.unlink()
        self.refused("build_source_layout")
        target.write_bytes(original)
        extra = self.source / "foundry_companion" / "unreviewed.py"
        extra.write_text("raise RuntimeError('untrusted')")
        self.refused("build_source_layout")
        extra.unlink()
        extra.mkdir()
        self.refused("build_source_layout")
        extra.rmdir()
        (self.source / "other_package").mkdir()
        self.refused("build_source_layout")

    def test_bytecode_cache_is_not_read_or_packaged(self):
        cache = self.source / "foundry_companion" / "__pycache__"
        cache.mkdir()
        (cache / "untrusted.pyc").write_bytes(b"not Python")
        with mock.patch.object(build, "_run_compiler", side_effect=self.fake_run):
            self.run_build()
        with zipfile.ZipFile(self.output) as archive:
            self.assertFalse(any("__pycache__" in name for name in archive.namelist()))

    def test_linked_inputs_and_ancestors_are_refused(self):
        link = self.root / "linked-source"
        try:
            link.symlink_to(self.source, target_is_directory=True)
        except OSError:
            self.skipTest("Host cannot create symbolic links")
        self.refused("linked_path", source_root=link)
        linked_output = self.root / "linked-output"
        linked_output.symlink_to(self.root, target_is_directory=True)
        self.refused("linked_path", output=linked_output / "new.ts4script")
        compiler_link = self.root / "compiler-link"
        compiler_link.symlink_to(self.compiler)
        self.refused("linked_path", python37=compiler_link)
        source_file = self.source / build.MODULES[0]
        source_file.unlink()
        source_file.symlink_to(self.compiler)
        self.refused("linked_path")

    def test_hardlinked_source_and_compiler_are_refused(self):
        link = self.root / "hard-link"
        try:
            os.link(self.compiler, link)
        except OSError:
            self.skipTest("Host cannot create hard links")
        self.refused("build_input_type")
        link.unlink()
        os.link(self.source / build.MODULES[0], link)
        self.refused("build_input_type")

    def test_source_file_directory_and_compiler_read_bounds(self):
        with mock.patch.object(build, "MAX_SOURCE_BYTES", 1):
            self.refused("build_input_bounds")
        with mock.patch.object(build, "MAX_TOTAL_SOURCE_BYTES", 1):
            self.refused("build_input_bounds")
        with mock.patch.object(build, "MAX_COMPILER_BYTES", 1):
            self.refused("build_input_bounds")
        with mock.patch.object(build, "MAX_DIRECTORY_ENTRIES", 1):
            self.refused("build_source_bounds")

    def test_windows_cross_api_ctime_and_executable_mode_keep_same_api_drift_checks(self):
        path = self.root / "python.exe"
        path.write_bytes(b"original fixture, not executed")
        original_lstat = Path.lstat
        original_fstat = os.fstat
        for change_descriptor in (False, True):
            calls = []
            def synthetic(info, descriptor=False):
                values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                values.update(st_mode=stat.S_IFREG | (0o666 if descriptor else 0o777),
                              st_birthtime_ns=123,
                              st_ctime_ns=info.st_ctime_ns + (1 if descriptor else 0))
                if descriptor:
                    calls.append(True)
                    if change_descriptor and len(calls) > 1:
                        values["st_ctime_ns"] += 1
                return SimpleNamespace(**values)
            def lstat(candidate, *args, **kwargs):
                info = original_lstat(candidate, *args, **kwargs)
                return synthetic(info) if candidate == path else info
            with self.subTest(drift=change_descriptor), mock.patch.object(launch, "_WINDOWS", True), \
                    mock.patch.object(Path, "lstat", lstat), \
                    mock.patch.object(build.os, "fstat", side_effect=lambda fd: synthetic(original_fstat(fd), True)):
                if change_descriptor:
                    with self.assertRaises(FoundryError) as caught:
                        build._regular(path, 4096)
                    self.assertEqual("build_input_changed", caught.exception.code)
                else:
                    self.assertEqual(b"original fixture, not executed", build._regular(path, 4096)[0])

    def test_file_descriptor_closes_on_wrapper_failure(self):
        opened = []
        original_open = os.open
        def opening(*args, **kwargs):
            descriptor = original_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor
        with mock.patch.object(build.os, "open", side_effect=opening), \
                mock.patch.object(build.os, "fdopen", side_effect=ValueError("wrapper failed")):
            with self.assertRaises(ValueError):
                build._regular(self.compiler, 4096)
        self.assertEqual(1, len(opened))
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_input_read_failure_and_symlink_swap_do_not_reach_compiler(self):
        with mock.patch.object(build.os, "open", side_effect=OSError("read denied")):
            with self.assertRaises(FoundryError) as caught:
                build._regular(self.compiler, 4096)
            self.assertEqual("build_input_read", caught.exception.code)
        if not getattr(os, "O_NOFOLLOW", 0):
            return
        original_open = os.open
        def swapped(path, flags):
            self.compiler.unlink()
            self.compiler.symlink_to(self.source / build.MODULES[0])
            return original_open(path, flags)
        with mock.patch.object(build.os, "open", side_effect=swapped):
            with self.assertRaises(FoundryError) as caught:
                build._regular(self.compiler, 4096)
            self.assertEqual("build_input_read", caught.exception.code)

    def test_profile_source_and_wrong_extension_outputs_are_refused(self):
        for output in (self.root / "The Sims 4 disposable" / "x.ts4script",
                       self.root / "Mods" / "x.ts4script",
                       self.root / "Tray" / "x.ts4script"):
            with self.subTest(output=output):
                self.refused("protected_path", output=output)
        renamed = self.root / "renamed-profile"
        renamed.mkdir()
        (renamed / "Options.ini").write_text("marker")
        self.refused("protected_path", output=renamed / "artifacts" / "x.ts4script")
        self.refused("build_output_path", output=self.source / "x.ts4script")
        self.refused("build_output_path", output=self.root / "x.zip")
        self.refused("build_output_path", manifest_output=self.root / "x.txt")
        self.refused("protected_path", manifest_output=self.root / "The Sims 4" / "x.json")

    def test_existing_archive_or_manifest_is_never_overwritten(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"existing archive")
        self.refused("build_output_exists")
        self.assertEqual(b"existing archive", self.output.read_bytes())
        self.output.unlink()
        manifest_path = self.output.with_name(self.output.name + ".manifest.json")
        manifest_path.write_bytes(b"existing manifest")
        self.refused("build_output_exists")
        self.assertEqual(b"existing manifest", manifest_path.read_bytes())

    def test_outputs_cannot_be_each_others_parent(self):
        self.refused("build_output_path", manifest_output=self.output / "manifest.json")
        self.assertFalse(self.output.parent.exists())
        parent = self.root / "manifest.json"
        self.refused("build_output_path", output=parent / "archive.ts4script", manifest_output=parent)
        self.assertFalse(parent.exists())

    def test_late_output_creation_is_not_replaced(self):
        def competing_publish(*args):
            response = self.fake_run(*args)
            self.output.write_bytes(b"other writer")
            return response
        with mock.patch.object(build, "_run_compiler", side_effect=competing_publish):
            self.refused("build_output_exists")
        self.assertEqual(b"other writer", self.output.read_bytes())

    def test_manifest_publication_failure_leaves_explicit_incomplete_archive(self):
        with mock.patch.object(build, "_run_compiler", side_effect=self.fake_run), \
                mock.patch.object(build, "new_json", side_effect=OSError("storage failure")):
            error = self.refused("build_publication_incomplete")
        self.assertEqual(str(self.output), error.details["artifact"])
        self.assertTrue(self.output.exists())
        self.assertFalse(self.output.with_name(self.output.name + ".manifest.json").exists())

    def test_pth_isolation_is_refused_without_running(self):
        (self.compiler.parent / "python37._pth").write_text(".")
        with mock.patch.object(build, "_run_compiler", side_effect=AssertionError("compiler reached")):
            self.refused("build_compiler_isolation")

    def test_compiler_response_schema_version_count_members_and_headers(self):
        mutations = [lambda r: r.update(python="3.12.0"),
                     lambda r: r.update(magic="00000000"),
                     lambda r: r["modules"].pop(),
                     lambda r: r["modules"].append(r["modules"][0]),
                     lambda r: r["modules"][0].update(module="../escape.pyc"),
                     lambda r: r["modules"][1].update(module=r["modules"][0]["module"]),
                     lambda r: r["modules"][0].update(source_sha256="0" * 64),
                     lambda r: r["modules"][0].update(source_hash="0" * 16),
                     lambda r: r["modules"][0].update(payload="%%%"),
                     lambda r: r["modules"][0].update(payload=base64.b64encode(bytes(32)).decode("ascii")),
                     lambda r: r.update(extra="unexpected")]
        for change in mutations:
            def reply(compiler, request, scratch):
                result = compiler_response(request)
                change(result)
                return json.dumps(result).encode("ascii")
            with self.subTest(change=change), mock.patch.object(build, "_run_compiler", side_effect=reply):
                self.refused("build_compiler_response")
            self.assertFalse(self.output.exists())

    def test_compiled_archive_and_response_bounds(self):
        with mock.patch.object(build, "_run_compiler", side_effect=self.fake_run):
            with mock.patch.object(build, "MAX_COMPILED_BYTES", 16):
                self.refused("build_compiler_response")
            with mock.patch.object(build, "MAX_TOTAL_COMPILED_BYTES", 32):
                self.refused("build_compiler_response")
            with mock.patch.object(build, "MAX_ARCHIVE_BYTES", 32):
                self.refused("build_archive_bounds")
            with mock.patch.object(build, "MAX_STDOUT_BYTES", 16):
                self.refused("build_compile_output_bounds")

    def test_environment_is_minimal_not_inherited(self):
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/evil", "PYTHONHOME": "/evil", "LD_PRELOAD": "/evil", "SECRET_KEY": "secret", "PYTHONHASHSEED": "random"}):
            env = build._compiler_environment(self.compiler)
        self.assertEqual("0", env["PYTHONHASHSEED"])
        self.assertEqual("utf-8", env["PYTHONIOENCODING"])
        for name in ("PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "SECRET_KEY"):
            self.assertNotIn(name, env)

    def test_bounded_subprocess_arguments_stdin_and_pipes(self):
        process = mock.Mock()
        process.stdout = io.BytesIO(b"bounded response")
        process.stderr = io.BytesIO()
        process.returncode = 0
        with mock.patch.object(build.subprocess, "Popen", return_value=process) as popen:
            self.assertEqual(b"bounded response", build._run_compiler(self.compiler, b"[]", str(self.root)))
        args, kwargs = popen.call_args
        self.assertEqual(str(self.compiler), args[0][0])
        self.assertEqual(["-B", "-S", "-s", "-c"], args[0][1:5])
        self.assertEqual(build.COMPILER_DRIVER, args[0][5])
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(str(self.root), kwargs["cwd"])
        process.wait.assert_called_once_with(timeout=build.COMPILE_TIMEOUT_SECONDS)

    def test_subprocess_overflow_failure_diagnostics_and_timeout(self):
        cases = ((b"x" * 17, b"", 0, "build_compile_output_bounds"),
                 (b"", b"x" * 17, 0, "build_compile_output_bounds"),
                 (b"", b"refused", 1, "build_compile_failed"),
                 (b"valid", b"warning", 0, "build_compile_failed"))
        for stdout, stderr, code, expected in cases:
            process = mock.Mock(stdout=io.BytesIO(stdout), stderr=io.BytesIO(stderr), returncode=code)
            with self.subTest(expected=expected), mock.patch.object(build.subprocess, "Popen", return_value=process), \
                    mock.patch.object(build, "MAX_STDOUT_BYTES", 16), mock.patch.object(build, "MAX_STDERR_BYTES", 16):
                with self.assertRaises(FoundryError) as caught:
                    build._run_compiler(self.compiler, b"[]", str(self.root))
                self.assertEqual(expected, caught.exception.code)
        process = mock.Mock(stdout=io.BytesIO(), stderr=io.BytesIO(), returncode=-9)
        process.wait.side_effect = [subprocess.TimeoutExpired("compiler", 60), -9]
        with mock.patch.object(build.subprocess, "Popen", return_value=process):
            with self.assertRaises(FoundryError) as caught:
                build._run_compiler(self.compiler, b"[]", str(self.root))
        self.assertEqual("build_compile_timeout", caught.exception.code)
        process.kill.assert_called_once()

    def test_partial_reader_start_failure_kills_reaps_and_closes_owned_child(self):
        process = mock.Mock(stdout=io.BytesIO(), stderr=io.BytesIO(), returncode=-9)
        first = mock.Mock()
        second = mock.Mock()
        second.start.side_effect = RuntimeError("no thread resources")
        with mock.patch.object(build.subprocess, "Popen", return_value=process), \
                mock.patch.object(build.threading, "Thread", side_effect=[first, second]):
            with self.assertRaises(FoundryError) as caught:
                build._run_compiler(self.compiler, b"[]", str(self.root))
        self.assertEqual("build_compile_failed", caught.exception.code)
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
        first.join.assert_called_once_with(timeout=5)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_wait_interrupts_reap_child_and_close_pipes_before_propagating(self):
        for interruption in (KeyboardInterrupt(), SystemExit(7)):
            process = mock.Mock(stdout=io.BytesIO(), stderr=io.BytesIO(), returncode=-9)
            process.wait.side_effect = [interruption, -9]
            with self.subTest(interruption=type(interruption).__name__), \
                    mock.patch.object(build.subprocess, "Popen", return_value=process):
                with self.assertRaises(type(interruption)):
                    build._run_compiler(self.compiler, b"[]", str(self.root))
            process.kill.assert_called_once()
            self.assertEqual(2, process.wait.call_count)
            self.assertTrue(process.stdout.closed)
            self.assertTrue(process.stderr.closed)

    def test_reader_join_failure_still_closes_pipes_after_child_reaped(self):
        process = mock.Mock(stdout=io.BytesIO(), stderr=io.BytesIO(), returncode=0)
        first, second = mock.Mock(), mock.Mock()
        first.join.side_effect = RuntimeError("join failed")
        with mock.patch.object(build.subprocess, "Popen", return_value=process), \
                mock.patch.object(build.threading, "Thread", side_effect=[first, second]):
            with self.assertRaises(FoundryError) as caught:
                build._run_compiler(self.compiler, b"[]", str(self.root))
        self.assertEqual("build_compile_cleanup", caught.exception.code)
        process.wait.assert_called_once_with(timeout=build.COMPILE_TIMEOUT_SECONDS)
        second.join.assert_called_once_with(timeout=5)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)


class ActualPython37BuildTests(unittest.TestCase):
    # Reuse fixtures, not mock tests that modify their disposable compiler.
    run_build = CompanionBuildTests.run_build
    refused = CompanionBuildTests.refused

    def setUp(self):
        interpreter = os.environ.get("FOUNDRY_COMPANION_PYTHON37")
        if not interpreter:
            self.skipTest("Set FOUNDRY_COMPANION_PYTHON37 to an explicitly trusted CPython 3.7 executable")
        CompanionBuildTests.setUp(self)
        self.compiler = Path(interpreter)
        self.pin = hashlib.sha256(self.compiler.read_bytes()).hexdigest()

    def test_actual_python37_deterministic_checked_hash_archive(self):
        # This original fixture would throw if imported. Compilation must not.
        first = self.run_build()
        second_path = self.root / "other" / self.output.name
        with mock.patch.dict(os.environ, {"PYTHONHASHSEED": "random", "PYTHONPATH": str(self.source), "PYTHONHOME": "invalid"}):
            second = self.run_build(output=second_path)
        self.assertEqual(first, second)
        self.assertEqual("3.7", first["compiler"]["version"][:3])
        self.assertEqual(self.output.read_bytes(), second_path.read_bytes())
        with zipfile.ZipFile(self.output) as archive:
            for name in archive.namelist():
                if name.endswith(".pyc"):
                    self.assertEqual(bytes.fromhex("420d0d0a03000000"), archive.read(name)[:8])
                    self.assertNotIn(str(self.root).encode(), archive.read(name))

    def test_actual_python37_rejects_new_syntax_without_output(self):
        (self.source / build.MODULES[0]).write_text("value := 1\n")
        self.refused("build_compile_failed")
        self.assertFalse(self.output.exists())

    def test_actual_neutral_repository_source_compiles(self):
        source = Path(__file__).resolve().parents[1] / "companion-src"
        result = self.run_build(source_root=source)
        self.assertEqual(42, result["member_count"])
        self.assertFalse(result["live_qualified"])

    def test_actual_repository_archive_imports_build_adapters_inertly(self):
        source = Path(__file__).resolve().parents[1] / "companion-src"
        self.run_build(source_root=source)
        script = r'''
import builtins, importlib, socket, sys, threading
archive = sys.argv[1]
sys.path.insert(0, archive)
original_import = builtins.__import__
forbidden = {'services', 'sims4', 'objects', 'build_buy', 'zone', 'apex'}
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in forbidden:
        raise AssertionError('game/private import attempted: ' + name)
    return original_import(name, *args, **kwargs)
def refuse(*args, **kwargs):
    raise AssertionError('import attempted activation')
builtins.__import__ = guarded_import
socket.socket = refuse
threading.Thread.start = refuse
for name in ('build_native_readers', 'build_transform', 'build_create',
             'build_destroy', 'build_variant'):
    module = importlib.import_module('foundry_companion.' + name)
    assert module.__file__.startswith(archive + '/'), module.__file__
assert not forbidden.intersection(sys.modules)
assert not any(name in sys.modules for name in ('build_transform', 'build_create',
    'build_destroy', 'build_variant', 'build_native_readers'))
print('owned archive imports inert')
'''
        # Only this fixture imports known repository code, after compilation.
        # No installation, game bootstrap, service discovery or native dispatch.
        for compiler in (self.compiler, Path(__import__('sys').executable)):
            result = subprocess.run([str(compiler), '-I', '-B', '-S', '-c', script,
                                     str(self.output)], cwd=str(self.root),
                                    env=build._compiler_environment(compiler),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=30, check=False)
            self.assertEqual(0, result.returncode, result.stderr.decode())
            self.assertEqual(b'owned archive imports inert\n', result.stdout)


if __name__ == "__main__":
    unittest.main()
