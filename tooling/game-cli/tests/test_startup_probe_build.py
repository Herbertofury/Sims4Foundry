"""Offline build/entry fixtures; no actual game loader, profile, or network."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import ModuleType
import unittest
from unittest import mock
import zipfile

from sims4foundry_cli import companion_build as common
from sims4foundry_cli import startup_probe_build as build
from sims4foundry_cli.cli import parser, run
from sims4foundry_cli.errors import FoundryError
from test_companion_build import compiler_response


class StartupProbeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "diagnostic-src"
        for name in build.MODULES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"raise RuntimeError('source must not run during build')\n")
        self.compiler = self.root / "trusted-python37"
        self.compiler.write_bytes(b"mocked compiler, never executed")
        self.pin = hashlib.sha256(self.compiler.read_bytes()).hexdigest()
        self.output = self.root / "dist" / "FoundryProbe.ts4script"
        self.config = dict(run_id="a" * 32, probe_output=r"C:\FoundryEvidence\probe.json", wait_seconds=1)

    def compile(self, compiler, request, scratch):
        self.assertEqual(self.compiler, compiler)
        self.assertTrue(Path(scratch).is_dir())
        return json.dumps(compiler_response(request)).encode("ascii")

    def build(self, **kwargs):
        config = dict(self.config)
        config.update(kwargs)
        return build.build_startup_probe(config.pop("source_root", self.source),
                                         config.pop("output", self.output),
                                         config.pop("python37", self.compiler),
                                         config.pop("compiler_sha256", self.pin), **config)

    def refused(self, code, **kwargs):
        with self.assertRaises(FoundryError) as caught:
            self.build(**kwargs)
        self.assertEqual(code, caught.exception.code)

    def test_build_has_only_two_modules_fixed_entry_and_checked_pyc(self):
        with mock.patch.object(common, "_run_compiler", side_effect=self.compile):
            report = self.build()
        self.assertEqual(6, report["member_count"])
        self.assertFalse(report["installed"])
        self.assertFalse(report["game_contacted"])
        self.assertFalse(report["credentials_included"])
        self.assertFalse(report["loader_qualified"])
        self.assertFalse(report["diagnostic_code_executed"])
        self.assertEqual(self.config["probe_output"], report["probe_config"]["output_path"])
        self.assertEqual(report, json.loads(Path(str(self.output) + ".manifest.json").read_bytes()))
        with zipfile.ZipFile(self.output) as archive:
            names = set(build.MODULES + (build.ENTRY,))
            self.assertEqual(names | {name[:-3] + ".pyc" for name in names}, set(archive.namelist()))
            for name in names:
                self.assertEqual(common.PYC_PREFIX, archive.read(name[:-3] + ".pyc")[:8])
        self.assertEqual([], list(self.output.parent.glob(".foundry-*")))

    def test_deterministic_and_run_config_changes_artifact(self):
        with mock.patch.object(common, "_run_compiler", side_effect=self.compile):
            first = self.build()
            second_path = self.root / "other" / self.output.name
            second = self.build(output=second_path)
            third = self.build(output=self.root / "third" / self.output.name, run_id="b" * 32)
        self.assertEqual(first, second)
        self.assertEqual(self.output.read_bytes(), second_path.read_bytes())
        self.assertNotEqual(first["sha256"], third["sha256"])

    def test_invalid_config_refused_before_filesystem(self):
        variants = [dict(run_id=value) for value in (None, True, "A" * 32, "a" * 31, "a" * 32 + "\n")]
        variants += [dict(wait_seconds=value) for value in (True, -1, 31, float("nan"), float("inf"), 10**500)]
        variants += [dict(probe_output=value) for value in (
            "", None, "relative.json", r"C:relative.json", "/tmp/report.json", r"\\server\share\report.json",
            r"\\?\C:\x\report.json", r"C:\x\..\report.json", r"C:\x\.\report.json",
            r"C:\x\report.json:stream", r"C:\x\nul.json", r"C:\x\report.json ",
            r"C:\x\NUL .json", r"C:\x\CON   .json", r"C:\x\COM1 .json", r"C:\x\LPT1 .json",
            r"C:\The Sims 4\evidence.json", r"C:\renamed\Mods\evidence.json",
            r"C:\x\report.txt", "C:\\x\\bad\ud800.json", r"C:\x" + "\\" * 2 + "report.json")]
        for values in variants:
            with self.subTest(values=values), mock.patch.object(build, "plain_path", side_effect=AssertionError("filesystem reached")):
                self.refused("probe_config", **values)

    def test_zero_and_thirty_seconds_and_unicode_data(self):
        for seconds in (0, 30):
            config = build.probe_config("a" * 32, "C:\\Foundry Evidence\\\u2603.json", seconds)
            self.assertEqual(float(seconds), config["wait_seconds"])
            self.assertEqual("C:\\Foundry Evidence\\\u2603.json", config["output_path"])

    def test_entry_only_calls_diagnostic_with_data_and_loader_self_report(self):
        calls = []
        module = ModuleType("foundry_diagnostic.runtime")
        module.start = lambda config, **kwargs: calls.append((config, kwargs))
        config = build.probe_config("a" * 32, "C:\\Foundry Evidence\\\u2603.json", 0)
        namespace = {"__file__": "candidate.zip/sims4foundry_startup_probe.py", "__loader__": object()}
        with mock.patch.dict("sys.modules", {"foundry_diagnostic.runtime": module}):
            exec(compile(build.entry_source(config), "owned-generated-fixture", "exec"), namespace)
        self.assertEqual([(config, dict(module_origin=namespace["__file__"], loader_type="object"))], calls)

    def test_entry_start_failure_does_not_abort_loader_or_claim_success(self):
        module = ModuleType("foundry_diagnostic.runtime")
        module.start = mock.Mock(side_effect=RuntimeError("sensitive details omitted"))
        namespace = {}
        config = build.probe_config("a" * 32, r"C:\FoundryEvidence\probe.json", 0)
        with mock.patch.dict("sys.modules", {"foundry_diagnostic.runtime": module}):
            exec(compile(build.entry_source(config), "owned-generated-fixture", "exec"), namespace)
        self.assertIsNone(namespace["_probe_run"])
        self.assertEqual(1, module.start.call_count)

    def test_hostile_looking_filename_remains_data(self):
        module = ModuleType("foundry_diagnostic.runtime")
        module.start = mock.Mock()
        path = r"C:\Evidence\'); __import__('os').system('not-run'); #.json"
        config = build.probe_config("a" * 32, path, 0)
        with mock.patch.dict("sys.modules", {"foundry_diagnostic.runtime": module}), mock.patch("os.system", side_effect=AssertionError("injected source ran")):
            exec(compile(build.entry_source(config), "owned-generated-fixture", "exec"), {})
        self.assertEqual(config, module.start.call_args.args[0])

    def test_archive_created_during_compile_is_preserved_without_manifest(self):
        def racing_compile(*args):
            self.output.write_bytes(b"other writer")
            return self.compile(*args)
        with mock.patch.object(common, "_run_compiler", side_effect=racing_compile):
            self.refused("build_output_exists")
        self.assertEqual(b"other writer", self.output.read_bytes())
        self.assertFalse(Path(str(self.output) + ".manifest.json").exists())

    def test_pin_mismatch_prevents_compiler_and_output_directory(self):
        with mock.patch.object(common, "_run_compiler", side_effect=AssertionError("compiler reached")):
            self.refused("build_compiler_hash", compiler_sha256="0" * 64)
        self.assertFalse(self.output.parent.exists())

    def test_extra_missing_or_nested_source_refused(self):
        extra = self.source / "foundry_diagnostic" / "other.py"
        extra.write_bytes(b"untrusted")
        self.refused("probe_source_layout")
        extra.unlink()
        (self.source / build.MODULES[0]).unlink()
        self.refused("probe_source_layout")

    def test_source_output_overlap_and_existing_output_refused(self):
        self.refused("build_output_path", output=self.source / "probe.ts4script")
        self.output.parent.mkdir()
        self.output.write_bytes(b"existing")
        self.refused("build_output_exists")
        self.assertEqual(b"existing", self.output.read_bytes())

    def test_existing_manifest_refused_before_archive(self):
        self.output.parent.mkdir()
        manifest = Path(str(self.output) + ".manifest.json")
        manifest.write_bytes(b"existing")
        self.refused("build_output_exists")
        self.assertFalse(self.output.exists())

    def test_manifest_failure_keeps_archive_and_reports_incomplete(self):
        with mock.patch.object(common, "_run_compiler", side_effect=self.compile), mock.patch.object(build, "new_json", side_effect=OSError("fixture")):
            self.refused("build_publication_incomplete")
        self.assertTrue(self.output.is_file())
        self.assertFalse(Path(str(self.output) + ".manifest.json").exists())

    def test_archive_publication_failure_does_not_write_manifest(self):
        with mock.patch.object(common, "_run_compiler", side_effect=self.compile), mock.patch.object(common, "_publish_archive", side_effect=OSError("fixture")), mock.patch.object(build, "new_json") as publish:
            self.refused("build_publication_incomplete")
        publish.assert_not_called()

    def test_cli_exact_routing(self):
        argv = ["startup-probe-build", "--source", str(self.source), "--output", str(self.output),
                "--python37", str(self.compiler), "--compiler-sha256", self.pin,
                "--run-id", "a" * 32, "--probe-output", self.config["probe_output"], "--seconds", "1"]
        with mock.patch.object(common, "_run_compiler", side_effect=self.compile):
            report = run(parser().parse_args(argv))
        self.assertEqual("foundry_startup_probe_build", report["kind"])

    @unittest.skipUnless(os.environ.get("FOUNDRY_COMPANION_PYTHON37"), "Explicit trusted Python 3.7 compiler not configured")
    def test_actual_python37_compiles_repository_diagnostic_without_executing(self):
        compiler = Path(os.environ["FOUNDRY_COMPANION_PYTHON37"])
        source = Path(__file__).resolve().parents[1] / "diagnostic-src"
        result = self.build(source_root=source, python37=compiler,
                            compiler_sha256=hashlib.sha256(compiler.read_bytes()).hexdigest())
        self.assertEqual("3.7.17", result["compiler"]["version"])
        self.assertFalse(result["diagnostic_code_executed"])


if __name__ == "__main__":
    unittest.main()
