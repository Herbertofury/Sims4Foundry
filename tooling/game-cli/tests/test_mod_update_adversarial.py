"""Bounded fabricated mod-update cases, never game/profile data or execution.

These checks exercise ordinary local validation and failure ordering. They do
not qualify hostile concurrent path swapping, Windows crash durability, mod
compatibility, or installation into a game profile.
"""

import copy
from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
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

    @staticmethod
    def tracked_scanner():
        real_scandir = os.scandir
        events = {name: [] for name in ("opened", "yielded", "statted", "closed")}

        class Entry:
            def __init__(self, original):
                self.original = original
                self.path = original.path
                events["yielded"].append(Path(self.path))

            def stat(self, *, follow_symlinks):
                if follow_symlinks:
                    raise AssertionError("inventory must not follow entries")
                events["statted"].append(Path(self.path))
                return self.original.stat(follow_symlinks=False)

        @contextmanager
        def scanner(path):
            events["opened"].append(Path(path))
            try:
                with real_scandir(path) as entries:
                    yield (Entry(entry) for entry in entries)
            finally:
                events["closed"].append(Path(path))

        return scanner, events

    def test_inventory_bounds_irrelevant_entries_before_materializing_scan(self):
        source = self.root / "irrelevant"
        source.mkdir()
        before = {}
        for n in range(5):
            path = source / ("ignored%d.txt" % n)
            path.write_bytes(b"fabricated irrelevant file")
            before[path] = path.read_bytes()
        scanner, events = self.tracked_scanner()
        with mock.patch.object(mods, "MAX_SCAN_ENTRIES", 2), \
                mock.patch.object(mods.os, "scandir", side_effect=scanner), \
                mock.patch.object(mods, "inspect_artifact") as inspect:
            with self.assertRaises(FoundryError) as caught:
                mods.inventory(source)
        self.assertEqual("inventory_scan_bound", caught.exception.code)
        self.assertEqual(2, caught.exception.details["maximum"])
        self.assertEqual(3, len(events["yielded"]))  # Exactly one overflow probe.
        self.assertEqual(2, len(events["statted"]))
        self.assertEqual([source], events["opened"])
        self.assertEqual(events["opened"], events["closed"])
        inspect.assert_not_called()
        self.assertEqual(before, {path: path.read_bytes() for path in source.iterdir()})

    def test_inventory_scan_budget_counts_directories_and_is_cumulative(self):
        source = self.root / "cumulative"
        for name in ("one", "two"):
            child = source / name
            child.mkdir(parents=True)
            (child / "ignored.txt").write_bytes(b"fabricated")
        scanner, events = self.tracked_scanner()
        with mock.patch.object(mods, "MAX_SCAN_ENTRIES", 3), \
                mock.patch.object(mods.os, "scandir", side_effect=scanner):
            with self.assertRaises(FoundryError) as caught:
                mods.inventory(source)
        self.assertEqual("inventory_scan_bound", caught.exception.code)
        self.assertEqual(4, len(events["yielded"]))
        self.assertEqual(3, len(events["statted"]))
        self.assertEqual(events["opened"], events["closed"])
        with mock.patch.object(mods, "MAX_SCAN_ENTRIES", 4):
            self.assertEqual([], mods.inventory(source)["artifacts"])
        self.assertEqual(b"fabricated", (source / "one" / "ignored.txt").read_bytes())
        self.assertEqual(b"fabricated", (source / "two" / "ignored.txt").read_bytes())

    def test_inventory_depth_refuses_before_over_limit_descent(self):
        source = self.root / "depth"
        deepest = source / "one" / "two"
        deepest.mkdir(parents=True)
        artifact = deepest / "fixture.package"
        artifact.write_bytes(b"DBPFfabricated")
        with mock.patch.object(mods, "MAX_SCAN_DEPTH", 2):
            accepted = mods.inventory(source)
        self.assertEqual(["one/two/fixture.package"], [row["relative"] for row in accepted["artifacts"]])
        beyond = deepest / "three"
        beyond.mkdir()
        scanner, events = self.tracked_scanner()
        with mock.patch.object(mods, "MAX_SCAN_DEPTH", 2), \
                mock.patch.object(mods.os, "scandir", side_effect=scanner), \
                mock.patch.object(mods, "inspect_artifact") as inspect:
            with self.assertRaises(FoundryError) as caught:
                mods.inventory(source)
        self.assertEqual("inventory_depth_bound", caught.exception.code)
        self.assertEqual(2, caught.exception.details["maximum"])
        self.assertEqual([source, source / "one", deepest], events["opened"])
        self.assertNotIn(beyond, events["opened"])
        self.assertEqual(events["opened"], events["closed"])
        inspect.assert_not_called()
        self.assertEqual(b"DBPFfabricated", artifact.read_bytes())

    def test_inventory_never_descends_into_observed_symlink(self):
        source, outside = self.root / "linked-scan", self.root / "outside"
        source.mkdir()
        outside.mkdir()
        sentinel = outside / "untouched.package"
        sentinel.write_bytes(b"DBPFoutside fabricated sentinel")
        alias = source / "alias"
        try:
            alias.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks are unavailable on this runner")
        scanner, events = self.tracked_scanner()
        with mock.patch.object(mods.os, "scandir", side_effect=scanner), \
                mock.patch.object(mods, "inspect_artifact") as inspect:
            with self.assertRaises(FoundryError) as caught:
                mods.inventory(source)
        self.assertEqual("linked_path", caught.exception.code)
        self.assertEqual([source], events["opened"])
        self.assertEqual(events["opened"], events["closed"])
        inspect.assert_not_called()
        self.assertTrue(alias.is_symlink())
        self.assertEqual(b"DBPFoutside fabricated sentinel", sentinel.read_bytes())

    def test_inventory_reparse_attribute_refuses_before_descendant_scan(self):
        # Portable branch coverage for Windows junction metadata, not a claim
        # of a live Windows filesystem test on this runner.
        entry = mock.Mock()
        entry.path = str(self.source / "fabricated-junction")
        entry.stat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700,
                                                 st_file_attributes=0x400)
        entries = mock.MagicMock()
        entries.__enter__.return_value = iter([entry])
        with mock.patch.object(mods.os, "scandir", return_value=entries) as scanner, \
                mock.patch.object(mods, "inspect_artifact") as inspect:
            with self.assertRaises(FoundryError) as caught:
                mods.inventory(self.source)
        self.assertEqual("linked_path", caught.exception.code)
        scanner.assert_called_once_with(self.source)
        entry.stat.assert_called_once_with(follow_symlinks=False)
        entries.__exit__.assert_called_once()
        inspect.assert_not_called()
        self.assert_sources_unchanged()

    def test_inventory_artifact_cap_is_preserved_before_inspection(self):
        self.assertEqual(4096, mods.MAX_INVENTORY_ARTIFACTS)
        scanner, events = self.tracked_scanner()
        with mock.patch.object(mods, "MAX_INVENTORY_ARTIFACTS", 2), \
                mock.patch.object(mods.os, "scandir", side_effect=scanner), \
                mock.patch.object(mods, "inspect_artifact") as inspect:
            with self.assertRaises(FoundryError) as caught:
                mods.inventory(self.source)
        self.assertEqual("inventory_bound", caught.exception.code)
        self.assertEqual(3, len(events["yielded"]))
        self.assertEqual(events["opened"], events["closed"])
        inspect.assert_not_called()
        self.assert_sources_unchanged()

    def test_inventory_is_deterministic_across_directory_enumeration_order(self):
        nested = self.source / "Alpha" / "nested.package"
        nested.parent.mkdir()
        nested.write_bytes(b"DBPFnested fabricated")
        before = {path: path.read_bytes() for path in self.source.rglob("*") if path.is_file()}
        expected = mods.inventory(self.source)
        real_scandir = os.scandir

        @contextmanager
        def reversed_scan(path):
            with real_scandir(path) as entries:
                # Only this explicitly tiny fabricated tree is materialized.
                rows = list(entries)
            yield iter(reversed(rows))

        with mock.patch.object(mods.os, "scandir", side_effect=reversed_scan):
            actual = mods.inventory(self.source)
        self.assertEqual(expected, actual)
        relative = [row["relative"] for row in actual["artifacts"]]
        self.assertEqual(sorted(relative), relative)
        self.assertEqual(before, {path: path.read_bytes() for path in self.source.rglob("*") if path.is_file()})

    def assert_manifest_refused(self, manifest, code):
        original_manifest = copy.deepcopy(manifest)
        for execute in (False, True):
            destination = self.root / ("execute" if execute else "dry-run")
            with self.subTest(execute=execute), mock.patch.object(mods, "inspect_artifact") as inspect, \
                    mock.patch.object(Path, "mkdir", side_effect=AssertionError("must refuse before mkdir")):
                with self.assertRaises(FoundryError) as caught:
                    mods.stage(manifest, destination, execute=execute)
                self.assertEqual(code, caught.exception.code)
                inspect.assert_not_called()
            self.assertFalse(destination.exists())
            self.assert_sources_unchanged()
            self.assertEqual(original_manifest, manifest)

    def test_manifest_relative_depth_is_bounded_before_parent_prefixes(self):
        manifest = self.manifest(("first.package",))
        row = manifest["artifacts"][0]
        row["relative"] = "a/" * mods.MAX_SCAN_DEPTH + row["name"]
        self.signed(manifest)
        self.assertEqual(manifest, mods._manifest(manifest))
        self.assertFalse(mods.stage(manifest, self.root / "within-depth")["executed"])
        self.assertFalse((self.root / "within-depth").exists())
        row["relative"] = "a/" + row["relative"]
        self.signed(manifest)
        with mock.patch.object(mods, "PurePosixPath", side_effect=AssertionError("must bound before Path.parents")):
            self.assert_manifest_refused(manifest, "invalid_manifest")

    def test_manifest_relative_component_and_total_lengths_refuse_before_io(self):
        for relative in ("x" * 248 + ".package", "\U0001F642" * 124 + ".package",
                         "a" * 120 + "/" + "b" * 128 + "/first.package"):
            with self.subTest(relative_length=len(relative)):
                manifest = self.manifest(("first.package",))
                manifest["artifacts"][0].update(name=relative.rsplit("/", 1)[-1], relative=relative)
                self.assert_manifest_refused(self.signed(manifest), "invalid_manifest")

    def test_full_staging_target_path_bound_refuses_before_source_io_or_mkdir(self):
        manifest = self.manifest(("first.package",))
        destination = self.root / "long-target"
        prefix_units = len(str(destination / "artifacts").encode("utf-16-le")) // 2 + 1
        relative_length = mods.MAX_MOD_PATH_UNITS - prefix_units + 1
        relative = "x" * (relative_length - len(".package")) + ".package"
        manifest["artifacts"][0].update(name=relative, relative=relative)
        self.signed(manifest)
        self.assertEqual(manifest, mods._manifest(manifest))
        for execute in (False, True):
            with self.subTest(execute=execute), mock.patch.object(mods, "inspect_artifact") as inspect, \
                    mock.patch.object(mods, "external_path", side_effect=AssertionError("must refuse before filesystem guard")), \
                    mock.patch.object(Path, "mkdir", side_effect=AssertionError("must refuse before mkdir")):
                with self.assertRaises(FoundryError) as caught:
                    mods.stage(manifest, destination, execute=execute)
            self.assertEqual("staging_path_bound", caught.exception.code)
            inspect.assert_not_called()
            self.assertFalse(destination.exists())
        self.assert_sources_unchanged()

    def test_receipt_publication_paths_are_bounded_even_for_an_empty_inventory(self):
        manifest = self.manifest(())
        pending_name = ".staging-complete.json." + "0" * 32 + ".pending"
        suffix_units = len("/" + pending_name)
        prefix_units = len(str(self.root).encode("utf-16-le")) // 2 + 1
        fitting_name = "d" * (mods.MAX_MOD_PATH_UNITS - prefix_units - suffix_units)
        fitting = self.root / fitting_name
        self.assertFalse(mods.stage(manifest, fitting)["executed"])
        self.assertFalse(fitting.exists())
        destination = self.root / (fitting_name + "d")
        final_units = len(str(destination / "staging-complete.json").encode("utf-16-le")) // 2
        self.assertLessEqual(final_units, mods.MAX_MOD_PATH_UNITS)
        for execute in (False, True):
            with self.subTest(execute=execute), \
                    mock.patch.object(mods, "external_path", side_effect=AssertionError("must refuse before filesystem guard")), \
                    mock.patch.object(Path, "mkdir", side_effect=AssertionError("must refuse before mkdir")):
                with self.assertRaises(FoundryError) as caught:
                    mods.stage(manifest, destination, execute=execute)
            self.assertEqual("staging_path_bound", caught.exception.code)
            self.assertFalse(destination.exists())
        self.assert_sources_unchanged()

    def test_created_directory_bound_is_checked_separately_from_file_path(self):
        manifest = self.manifest(("first.package",))
        destination = self.root / "directory-length"
        prefix_units = len(str(destination / "artifacts").encode("utf-16-le")) // 2 + 1
        folder = "d" * (mods.MAX_MOD_DIRECTORY_UNITS - prefix_units)
        row = manifest["artifacts"][0]
        row.update(name="a.package", relative=folder + "/a.package")
        self.signed(manifest)
        self.assertFalse(mods.stage(manifest, destination)["executed"])
        self.assertFalse(destination.exists())
        row["relative"] = folder + "d/a.package"
        self.signed(manifest)
        target_units = len(str(destination / "artifacts" / row["relative"]).encode("utf-16-le")) // 2
        self.assertLessEqual(target_units, mods.MAX_MOD_PATH_UNITS)
        for execute in (False, True):
            with mock.patch.object(mods, "inspect_artifact") as inspect, \
                    mock.patch.object(Path, "mkdir", side_effect=AssertionError("must refuse before mkdir")):
                with self.assertRaises(FoundryError) as caught:
                    mods.stage(manifest, destination, execute=execute)
            self.assertEqual("staging_path_bound", caught.exception.code)
            inspect.assert_not_called()
            self.assertFalse(destination.exists())
        self.assert_sources_unchanged()

    def test_utf8_component_byte_limit_is_distinct_from_windows_units(self):
        manifest = self.manifest(("first.package",))
        relative = "\U0001F642" * 64 + ".package"
        self.assertLess(len(relative.encode("utf-16-le")) // 2, mods.MAX_MOD_COMPONENT_UNITS)
        self.assertGreater(len(relative.encode("utf-8")), mods.MAX_MOD_COMPONENT_BYTES)
        manifest["artifacts"][0].update(name=relative, relative=relative)
        self.assert_manifest_refused(self.signed(manifest), "invalid_manifest")

    def test_utf8_destination_component_refuses_before_filesystem_io(self):
        manifest = self.manifest(("first.package",))
        destination = self.root / ("\U0001F642" * 64)
        before = list(self.root.iterdir())
        for execute in (False, True):
            with mock.patch.object(mods, "inspect_artifact") as inspect, \
                    mock.patch.object(mods, "external_path", side_effect=AssertionError("must refuse before filesystem guard")), \
                    mock.patch.object(Path, "mkdir", side_effect=AssertionError("must refuse before mkdir")):
                with self.assertRaises(FoundryError) as caught:
                    mods.stage(manifest, destination, execute=execute)
            self.assertEqual("staging_path_bound", caught.exception.code)
            inspect.assert_not_called()
        self.assertEqual(before, list(self.root.iterdir()))
        self.assert_sources_unchanged()

    def test_oversized_destination_component_refuses_lexically(self):
        manifest = self.manifest(("first.package",))
        before = list(self.root.iterdir())
        destination = self.root / ("d" * 256)
        for execute in (False, True):
            with self.subTest(execute=execute), mock.patch.object(mods, "inspect_artifact") as inspect, \
                    mock.patch.object(mods, "external_path", side_effect=AssertionError("must refuse before filesystem guard")), \
                    mock.patch.object(Path, "mkdir", side_effect=AssertionError("must refuse before mkdir")):
                with self.assertRaises(FoundryError) as caught:
                    mods.stage(manifest, destination, execute=execute)
            self.assertEqual("staging_path_bound", caught.exception.code)
            inspect.assert_not_called()
        self.assertEqual(before, list(self.root.iterdir()))
        self.assert_sources_unchanged()

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
