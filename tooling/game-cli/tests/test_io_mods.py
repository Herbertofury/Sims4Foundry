import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import (MAX_JSON, digest, external_path, loads, lock, new_json,
                                plain_path, read_json, replace_json, safe_relative, sha256)
from sims4foundry_cli import mods


class IOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_strict_json(self):
        for raw in ('{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '{'):
            with self.subTest(raw=raw), self.assertRaises(FoundryError):
                loads(raw)

    def test_writer_and_reader_share_bound(self):
        target = self.root / "data.json"
        new_json(target, {"valid": True})
        with self.assertRaises(FoundryError):
            replace_json(target, {"x": "x" * MAX_JSON})
        self.assertEqual({"valid": True}, read_json(target))
        self.assertEqual([target], list(self.root.iterdir()))

    def test_receipts_never_overwrite(self):
        target = self.root / "receipt.json"
        new_json(target, {"first": True})
        with self.assertRaises(FileExistsError):
            new_json(target, {"other": True})
        self.assertEqual({"first": True}, read_json(target))

    def test_publish_failure_has_no_partial_receipt(self):
        target = self.root / "receipt.json"
        with mock.patch("sims4foundry_cli.io.os.link", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                new_json(target, {"x": "content"})
        self.assertFalse(target.exists())
        self.assertEqual([], list(self.root.iterdir()))

    def test_concurrent_lock_fails(self):
        path = self.root / "lock"
        with lock(path):
            with self.assertRaises(FoundryError) as caught:
                with lock(path):
                    self.fail("second lock acquired")
        self.assertEqual("busy", caught.exception.code)
        with lock(path):
            pass

    def test_process_exit_releases_lock(self):
        path = self.root / "lock"
        code = "from sims4foundry_cli.io import lock; import os,sys\nwith lock(sys.argv[1]): os._exit(0)"
        subprocess.run([sys.executable, "-c", code, str(path)], check=True, timeout=10)
        with lock(path):
            pass

    def test_symlink_paths_are_rejected(self):
        target = self.root / "target"
        target.mkdir()
        linked = self.root / "linked"
        try:
            linked.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks are not available on this runner")
        with self.assertRaises(FoundryError):
            plain_path(linked / "out.json")

    def test_game_profile_destinations_rejected(self):
        for name in ("The Sims 4", "The Sims 4 DO NOT TOUCH", "The Sims 4 - protected", "Game"):
            with self.subTest(name=name), self.assertRaises(FoundryError):
                external_path(self.root / name / "new")

    def test_windows_relative_names(self):
        for name in ("CON.package", "nul.ts4script", "COM1.package", "COM¹.package", "LPT².package", "folder./a.package", "folder /a.package",
                     "a\x00.package", "a?/b.package", "C:/a.package", "../a.package", "a\\b.package", "a//b.package"):
            with self.subTest(name=name):
                self.assertFalse(safe_relative(name))
        self.assertTrue(safe_relative("Some Mod/subfolder/Test.package"))


class ModTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.package = self.source / "test.package"
        self.package.write_bytes(b"DBPF" + b"fixture" * 10)
        self.script = self.source / "test.ts4script"
        with zipfile.ZipFile(self.script, "w") as archive:
            archive.writestr("module.py", "raise RuntimeError('must never execute')")

    def test_inventory_checks_headers_and_zip_without_running_code(self):
        result = mods.inventory(self.source)
        self.assertEqual(2, len(result["artifacts"]))
        self.assertFalse(result["game_modified"])
        rows = {row["name"]: row for row in result["artifacts"]}
        self.assertEqual("signature_only", rows["test.package"]["validation"])
        self.assertFalse(rows["test.ts4script"]["code_executed"])

    def test_invalid_package_rejected(self):
        self.package.write_bytes(b"not a package")
        with self.assertRaises(FoundryError):
            mods.inspect_artifact(self.package)

    def test_zip_traversal_and_devices_rejected(self):
        for name in ("../evil.py", "CON.py", "folder./evil.py", "/absolute.py", "a\\b.py"):
            with self.subTest(name=name):
                with zipfile.ZipFile(self.script, "w") as archive:
                    archive.writestr(name.replace("\\", "/"), "content")
                if "\\" in name:
                    # ZipInfo normalizes separators on Windows; patch both
                    # same-length member-name fields to exercise hostile ZIP bytes.
                    raw = self.script.read_bytes()
                    self.script.write_bytes(raw.replace(name.replace("\\", "/").encode(), name.encode()))
                with self.assertRaises(FoundryError):
                    mods.inspect_artifact(self.script)

    def test_archive_case_collision_rejected(self):
        with zipfile.ZipFile(self.script, "w") as archive:
            archive.writestr("A.py", "1")
            archive.writestr("a.py", "2")
        with self.assertRaises(FoundryError):
            mods.inspect_artifact(self.script)

    def test_stage_dry_run_does_not_write(self):
        manifest = mods.inventory(self.source)
        destination = self.root / "stage"
        result = mods.stage(manifest, destination)
        self.assertFalse(result["executed"])
        self.assertFalse(destination.exists())

    def test_staging_copies_verified_bytes_only(self):
        manifest = mods.inventory(self.source)
        hashes = {p.name: sha256(p) for p in self.source.iterdir()}
        destination = self.root / "stage"
        result = mods.stage(manifest, destination, execute=True)
        self.assertTrue(result["staged"])
        self.assertFalse(result["installed"])
        self.assertTrue((destination / "staging-complete.json").is_file())
        for row in manifest["artifacts"]:
            self.assertEqual(row["sha256"], sha256(destination / "artifacts" / row["relative"]))
            self.assertEqual(hashes[row["name"]], sha256(self.source / row["name"]))
        with self.assertRaises(FoundryError):
            mods.stage(manifest, destination, execute=True)

    def test_source_change_aborts_before_destination(self):
        manifest = mods.inventory(self.source)
        self.package.write_bytes(b"DBPFchanged")
        destination = self.root / "stage"
        with self.assertRaises(FoundryError) as caught:
            mods.stage(manifest, destination, execute=True)
        self.assertEqual("source_changed", caught.exception.code)
        self.assertFalse(destination.exists())

    def test_manifest_tampering_rejected(self):
        manifest = mods.inventory(self.source)
        manifest["artifacts"][0]["relative"] = "../escape.package"
        with self.assertRaises(FoundryError):
            mods.stage(manifest, self.root / "stage", execute=True)
        self.assertFalse((self.root / "stage").exists())

    def test_manifest_rows_must_have_typed_sources(self):
        for invalid in (None, 4, ""):
            manifest = mods.inventory(self.source)
            manifest["artifacts"][0]["source"] = invalid
            manifest["manifest_sha256"] = digest({k:v for k,v in manifest.items() if k != "manifest_sha256"})
            with self.assertRaises(FoundryError):
                mods.stage(manifest, self.root / "stage")

    def test_compare_added_removed_changed(self):
        before = mods.inventory(self.source)
        self.package.write_bytes(b"DBPFnewbytes")
        self.script.unlink()
        (self.source / "new.package").write_bytes(b"DBPFnew")
        result = mods.compare(before, mods.inventory(self.source))
        self.assertEqual(["new.package"], result["added"])
        self.assertEqual(["test.ts4script"], result["removed"])
        self.assertEqual(["test.package"], result["changed"])
        self.assertFalse(result["compatibility_verified"])


if __name__ == "__main__":
    unittest.main()
