"""Bounded fabricated mod-update cases, never game/profile data or execution.

These checks exercise ordinary local validation and failure ordering. They do
not qualify hostile concurrent path swapping, Windows crash durability, mod
compatibility, or installation into a game profile.
"""

import copy
import hashlib
import io
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

from sims4foundry_cli import mods
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import digest, new_json, read_json, sha256


class ModUpdateAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.first = self.source / "first.package"
        self.second = self.source / "second.package"
        self.first.write_bytes(b"DBPFfirst fabricated package")
        self.second.write_bytes(b"DBPFsecond fabricated package")
        self.script = self.source / "script.ts4script"
        self.write_script([("module.py", b"raise RuntimeError('must not execute')")])
        self.original = {path: path.read_bytes() for path in self.source.iterdir()}

    def write_script(self, members):
        with zipfile.ZipFile(self.script, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, content in members:
                archive.writestr(name, content)

    def manifest(self, names=("first.package", "second.package")):
        result = mods.inventory(self.source)
        result["artifacts"] = [row for row in result["artifacts"] if row["name"] in names]
        return self.signed(result)

    @staticmethod
    def signed(manifest):
        manifest["manifest_sha256"] = digest({key: value for key, value in manifest.items()
                                             if key != "manifest_sha256"})
        return manifest

    def assert_sources_unchanged(self):
        self.assertEqual(self.original, {path: path.read_bytes() for path in self.source.iterdir()})

    def assert_manifest_refused(self, manifest, code):
        original_manifest = copy.deepcopy(manifest)
        for execute in (False, True):
            destination = self.root / ("execute" if execute else "dry-run")
            with self.subTest(execute=execute), mock.patch.object(mods, "inspect_artifact") as inspect:
                with self.assertRaises(FoundryError) as caught:
                    mods.stage(manifest, destination, execute=execute)
                self.assertEqual(code, caught.exception.code)
                inspect.assert_not_called()
            self.assertFalse(destination.exists())
            self.assert_sources_unchanged()
            self.assertEqual(original_manifest, manifest)

    def test_source_destination_extension_confusion_refuses_before_io(self):
        for source, relative in (("first.package", "renamed.ts4script"),
                                 ("script.ts4script", "renamed.package")):
            with self.subTest(source=source):
                manifest = self.manifest((source,))
                manifest["artifacts"][0].update(name=relative, relative=relative)
                self.assert_manifest_refused(self.signed(manifest), "invalid_manifest")

    def test_safe_rename_and_extension_case_remain_supported(self):
        for source, relative in (("first.package", "renamed.PACKAGE"),
                                 ("script.ts4script", "renamed.TS4SCRIPT")):
            with self.subTest(source=source):
                manifest = self.manifest((source,))
                manifest["artifacts"][0].update(name=relative, relative="nested/" + relative)
                self.signed(manifest)
                destination = self.root / source
                result = mods.stage(manifest, destination, execute=True)
                self.assertTrue(result["staged"])
                self.assertFalse(result["installed"])
                staged = destination / "artifacts" / "nested" / relative
                checked = mods.inspect_artifact(staged)
                self.assertEqual(manifest["artifacts"][0]["sha256"], checked["sha256"])
                self.assertEqual(result, read_json(destination / "staging-complete.json"))
                self.assert_sources_unchanged()

    def test_file_ancestor_conflicts_refuse_in_either_order_and_case(self):
        for ancestor, descendant in (("root.package", "root.package/second.package"),
                                     ("Root.PACKAGE", "root.package/second.package"),
                                     ("nested/root.package", "NESTED/ROOT.PACKAGE/deep/second.package")):
            for reverse in (False, True):
                with self.subTest(ancestor=ancestor, reverse=reverse):
                    manifest = self.manifest()
                    for row, relative in zip(manifest["artifacts"], (ancestor, descendant)):
                        row.update(name=relative.rsplit("/", 1)[-1], relative=relative)
                    if reverse:
                        manifest["artifacts"].reverse()
                    self.assert_manifest_refused(self.signed(manifest), "artifact_path_conflict")

    def test_shared_directory_is_not_a_file_ancestor_conflict(self):
        manifest = self.manifest()
        for row in manifest["artifacts"]:
            row["relative"] = "shared.package/subdirectory/" + row["name"]
        self.signed(manifest)
        destination = self.root / "shared"
        result = mods.stage(manifest, destination, execute=True)
        self.assertTrue(result["staged"])
        for row in manifest["artifacts"]:
            self.assertEqual(row["sha256"], sha256(destination / "artifacts" / row["relative"]))
        self.assert_sources_unchanged()

    @unittest.skipIf(os.name == "nt", "A case-sensitive source fixture is required")
    def test_inventory_rejects_casefolded_file_directory_collision(self):
        alias = self.source / "FIRST.PACKAGE"
        alias.mkdir()
        (alias / "nested.package").write_bytes(b"DBPFnested fabricated package")
        before = {path: path.read_bytes() for path in self.source.rglob("*") if path.is_file()}
        with self.assertRaises(FoundryError) as caught:
            mods.inventory(self.source)
        self.assertEqual("artifact_path_conflict", caught.exception.code)
        self.assertEqual(before, {path: path.read_bytes() for path in self.source.rglob("*") if path.is_file()})

    def test_mid_copy_failure_never_publishes_completion_or_overwrites_on_retry(self):
        manifest = self.manifest()
        destination = self.root / "partial"

        def interrupted(source, target, expected_size):
            target.write(source.read(5))
            raise OSError("fabricated interrupted copy")

        with mock.patch.object(mods, "_copy_artifact", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "fabricated interrupted copy"):
                mods.stage(manifest, destination, execute=True)
        self.assertTrue((destination / "staging-intent.json").is_file())
        self.assertFalse((destination / "staging-complete.json").exists())
        copied = destination / "artifacts" / "first.package"
        self.assertEqual(self.original[self.first][:5], copied.read_bytes())
        partial = {path: path.read_bytes() for path in destination.rglob("*") if path.is_file()}
        for execute in (False, True):
            with self.assertRaises(FoundryError) as caught:
                mods.stage(manifest, destination, execute=execute)
            self.assertEqual("destination_exists", caught.exception.code)
            self.assertEqual(partial, {path: path.read_bytes() for path in destination.rglob("*") if path.is_file()})
        self.assert_sources_unchanged()

    def test_corrupted_copy_never_publishes_completion(self):
        destination = self.root / "corrupted"

        def corrupted(source, target, expected_size):
            raw = source.read()
            target.write(raw[:-1] + bytes([raw[-1] ^ 1]))

        with mock.patch.object(mods, "_copy_artifact", side_effect=corrupted):
            with self.assertRaises(FoundryError) as caught:
                mods.stage(self.manifest(), destination, execute=True)
        self.assertEqual("copy_changed", caught.exception.code)
        self.assertTrue((destination / "staging-intent.json").is_file())
        self.assertFalse((destination / "staging-complete.json").exists())
        self.assert_sources_unchanged()

    def test_hash_and_copy_reads_are_bounded_and_never_write_growth_probe(self):
        class TrackedStream(io.BytesIO):
            def __init__(self, raw):
                super().__init__(raw)
                self.requests = []

            def read(self, size=-1):
                self.requests.append(size)
                if not 0 <= size <= mods.READ_CHUNK_BYTES:
                    raise AssertionError("unbounded source read")
                return super().read(size)

        # A small patched chunk size exercises multiple reads without creating
        # large files. Short reads are valid until the declared extent is read.
        expected = b"DBPFfabricated bytes"
        with mock.patch.object(mods, "READ_CHUNK_BYTES", 7):
            for raw in (expected, expected[:-1], expected + b"growing source tail"):
                for operation in ("hash", "copy"):
                    with self.subTest(length=len(raw), operation=operation):
                        source, target = TrackedStream(raw), io.BytesIO()
                        if operation == "hash":
                            checksum = hashlib.sha256()

                            def run():
                                for chunk in mods._bounded_chunks(source, len(expected), "artifact_changed"):
                                    checksum.update(chunk)
                        else:
                            def run():
                                mods._copy_artifact(source, target, len(expected))
                        if raw == expected:
                            run()
                            if operation == "hash":
                                self.assertEqual(hashlib.sha256(expected).hexdigest(), checksum.hexdigest())
                            else:
                                self.assertEqual(expected, target.getvalue())
                        else:
                            with self.assertRaises(FoundryError) as caught:
                                run()
                            self.assertEqual("artifact_changed" if operation == "hash" else "copy_changed", caught.exception.code)
                        self.assertLessEqual(source.tell(), len(expected) + 1)
                        self.assertLessEqual(len(target.getvalue()), len(expected))
                        if len(raw) >= len(expected):
                            self.assertEqual(1, source.requests[-1])

    def test_inspection_growth_during_first_hash_refuses_at_declared_extent(self):
        original_hash = mods._hash_artifact
        expected = self.original[self.first]
        appended = b"ordinary concurrent update tail"

        def grow(path, expected_size, code="artifact_changed"):
            with self.first.open("ab") as stream:
                stream.write(appended)
            return original_hash(path, expected_size, code)

        with mock.patch.object(mods, "_hash_artifact", side_effect=grow):
            with self.assertRaises(FoundryError) as caught:
                mods.inspect_artifact(self.first)
        self.assertEqual("artifact_changed", caught.exception.code)
        self.assertEqual(expected + appended, self.first.read_bytes())
        self.assertEqual(self.original[self.second], self.second.read_bytes())
        self.assertEqual(self.original[self.script], self.script.read_bytes())

    def test_source_growth_after_preflight_writes_only_declared_bytes(self):
        manifest = self.manifest(("first.package",))
        destination = self.root / "growth"
        original_copy = mods._copy_artifact
        expected = self.original[self.first]
        appended = b"ordinary concurrent update tail"

        def grow(source, target, expected_size):
            with self.first.open("ab") as stream:
                stream.write(appended)
            return original_copy(source, target, expected_size)

        with mock.patch.object(mods, "_copy_artifact", side_effect=grow):
            with self.assertRaises(FoundryError) as caught:
                mods.stage(manifest, destination, execute=True)
        self.assertEqual("copy_changed", caught.exception.code)
        self.assertEqual(expected, (destination / "artifacts" / "first.package").read_bytes())
        self.assertFalse((destination / "staging-complete.json").exists())
        self.assertEqual(expected + appended, self.first.read_bytes())
        self.assertEqual(self.original[self.second], self.second.read_bytes())
        self.assertEqual(self.original[self.script], self.script.read_bytes())

    def test_symlink_source_and_destination_ancestors_refuse(self):
        manifest = self.manifest(("first.package",))
        alias = self.root / "alias"
        try:
            alias.symlink_to(self.source, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks are unavailable on this runner")
        for execute in (False, True):
            with self.assertRaises(FoundryError) as caught:
                mods.stage(manifest, alias / "stage", execute=execute)
            self.assertEqual("linked_path", caught.exception.code)
            self.assertFalse((self.source / "stage").exists())
        manifest["artifacts"][0]["source"] = str(alias / "first.package")
        self.signed(manifest)
        for execute in (False, True):
            with self.assertRaises(FoundryError) as caught:
                mods.stage(manifest, self.root / "stage", execute=execute)
            self.assertEqual("linked_path", caught.exception.code)
            self.assertFalse((self.root / "stage").exists())
        self.assert_sources_unchanged()

    def assert_script_refused(self, code):
        raw = self.script.read_bytes()
        with self.assertRaises(FoundryError) as caught:
            mods.inspect_artifact(self.script)
        self.assertEqual(code, caught.exception.code)
        self.assertEqual(raw, self.script.read_bytes())

    def test_wire_zip_nul_truncation_cannot_hide_member_name(self):
        self.write_script([("aXb.py", b"fabricated")])
        self.script.write_bytes(self.script.read_bytes().replace(b"aXb.py", b"a\x00b.py"))
        self.assert_script_refused("archive_path")

    def test_zip_crc_and_symlink_members_refuse_without_execution(self):
        self.write_script([("module.py", b"unique fabricated member content")])
        self.script.write_bytes(self.script.read_bytes().replace(b"unique fabricated member content", b"changed fabricated member data!!"))
        self.assert_script_refused("archive_crc")
        row = zipfile.ZipInfo("module.py")
        row.create_system = 3
        row.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.write_script([(row, b"target.py")])
        self.assert_script_refused("archive_member")

    def test_zip_cumulative_declared_expansion_refuses_before_member_reads(self):
        # Nine tiny empty entries claim 32 MiB each in the central directory.
        # No large allocation/decompression is needed to test the aggregate bound.
        self.write_script([("member%02d.py" % n, b"") for n in range(9)])
        raw = bytearray(self.script.read_bytes())
        position = 0
        while True:
            position = raw.find(b"PK\x01\x02", position)
            if position < 0:
                break
            struct.pack_into("<I", raw, position + 24, 32 * 1024 * 1024)
            position += 4
        self.script.write_bytes(raw)
        with mock.patch.object(zipfile.ZipFile, "testzip", side_effect=AssertionError("must reject before member reads")):
            self.assert_script_refused("archive_bounds")

    def test_zip_entry_count_bound_uses_small_real_archive(self):
        self.write_script([("member%04d.py" % n, b"") for n in range(4097)])
        self.assertLess(self.script.stat().st_size, 1024 * 1024)
        with mock.patch.object(zipfile.ZipFile, "testzip", side_effect=AssertionError("must reject before member reads")):
            self.assert_script_refused("archive_bounds")

    def test_post_publish_sync_failure_leaves_whole_receipt_and_refuses_overwrite(self):
        target = self.root / "receipt.json"
        with mock.patch("sims4foundry_cli.io.sync_directory", side_effect=OSError("fabricated sync failure")):
            with self.assertRaisesRegex(OSError, "fabricated sync failure"):
                new_json(target, {"complete": True})
        self.assertEqual({"complete": True}, read_json(target))
        self.assertEqual([target], [path for path in self.root.iterdir() if path.is_file()])
        with self.assertRaises(FileExistsError):
            new_json(target, {"replacement": True})
        self.assertEqual({"complete": True}, read_json(target))
        self.assert_sources_unchanged()


if __name__ == "__main__":
    unittest.main()
