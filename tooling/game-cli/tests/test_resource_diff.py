"""Offline synthetic resource comparisons; no candidate decoding or game access."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from sims4foundry_cli import cli, package_resources as resources, resource_diff as diff
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import canonical, loads
from test_package_resources import KEY, entry, package, patch_u32


def tgi(key):
    return f"{key[0]:08X}:{key[1]:08X}:{key[2]:016X}"


class ResourceDiffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.before = self.write("old/example.package", package())
        self.after = self.write("new/example.package", package())

    def write(self, name, raw):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return target

    def compare(self):
        report = diff.compare_resources(self.before, self.after)
        self.assertEqual(report["before_count"], report["removed_count"] + report["shared_count"])
        self.assertEqual(report["after_count"], report["added_count"] + report["shared_count"])
        self.assertEqual(report["shared_count"], report["changed_count"] + report["unchanged_count"])
        for name in ("added", "removed", "changed"):
            self.assertEqual(len(report[name]), report[name + "_count"])
            self.assertEqual(sorted(row["tgi"] for row in report[name]), [row["tgi"] for row in report[name]])
        for name in ("stored_bytes", "metadata", "layout"):
            self.assertEqual(sum(row[name + "_changed"] for row in report["changed"]), report[name + "_changed_count"])
        return report

    def refused(self, code, function=None):
        with self.assertRaises(FoundryError) as caught:
            (function or self.compare)()
        self.assertEqual(code, caught.exception.code)

    def run_cli(self, *args):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = cli.main(["mods", "resource-diff", "--before", str(self.before),
                               "--after", str(self.after), *args])
        return status, json.loads(stdout.getvalue())

    def test_identical_files_omit_common_rows_and_preserve_counts_and_schemas(self):
        report = self.compare()
        self.assertEqual("foundry_package_resource_diff", report["kind"])
        self.assertTrue(report["package_bytes_equal"])
        self.assertEqual("sha256_and_length", report["equality_basis"])
        self.assertFalse(report["index_layout_changed"])
        self.assertEqual([], report["changed"])
        self.assertEqual(1, report["unchanged_count"])
        self.assertEqual(report, self.compare())
        for side, path in (("before", self.before), ("after", self.after)):
            original = resources.inspect_resources(path)
            self.assertEqual({field: original[field] for field in diff._SOURCE_FIELDS}, report[side])
            self.assertNotIn("resources", report[side])
        for field in ("resource_contents_validated", "decoded_bytes_compared", "decompressed", "code_executed",
                      "game_modified", "compatibility_verified", "load_order_inferred"):
            self.assertIs(False, report[field])
        for field in ("live_qualified", "safe_update", "pure_repack", "compatible", "load_order"):
            self.assertNotIn(field, report)

    def test_exact_identity_all_key_parts_and_orientation(self):
        k = KEY
        keys = [(k[0] + 1, k[1], k[2]), (k[0], k[1] - 1, k[2]),
                (k[0], k[1], k[2] ^ (1 << 32)), (k[0], k[1], k[2] + 1)]
        self.after.write_bytes(package([entry(key, body=bytes([i])) for i, key in enumerate(keys)]))
        report = self.compare()
        self.assertEqual([tgi(KEY)], [row["tgi"] for row in report["removed"]])
        self.assertEqual(sorted(map(tgi, keys)), [row["tgi"] for row in report["added"]])
        for row in report["added"]:
            i = keys.index(next(key for key in keys if tgi(key) == row["tgi"]))
            self.assertEqual(hashlib.sha256(bytes([i])).hexdigest(), row["stored_sha256"])
        reverse = diff.compare_resources(self.after, self.before)
        self.assertEqual(report["added"], reverse["removed"])
        self.assertEqual(report["removed"], reverse["added"])
        self.assertEqual(0, report["shared_count"])

    def test_same_length_body_only_change(self):
        self.before.write_bytes(package([entry(body=b"old")]))
        self.after.write_bytes(package([entry(body=b"new")]))
        report = self.compare()
        row = report["changed"][0]
        self.assertEqual((True, False, False), tuple(row[k] for k in
                         ("stored_bytes_changed", "metadata_changed", "layout_changed")))
        self.assertFalse(report["index_layout_changed"])
        self.assertEqual(hashlib.sha256(b"old").hexdigest(), row["before"]["stored_sha256"])
        self.assertEqual(hashlib.sha256(b"new").hexdigest(), row["after"]["stored_sha256"])

    def test_lengths_and_zero_transitions_preserve_independent_flags(self):
        for left, right in ((b"a", b"long"), (b"", b"x"), (b"x", b"")):
            with self.subTest(left=left, right=right):
                self.before.write_bytes(package([entry(body=left)]))
                self.after.write_bytes(package([entry(body=right)]))
                row = self.compare()["changed"][0]
                self.assertTrue(row["stored_bytes_changed"])
                self.assertTrue(row["metadata_changed"])
                self.assertTrue(row["layout_changed"])
                for side, body in (("before", left), ("after", right)):
                    self.assertEqual(len(body), row[side]["stored_bytes"])
                    self.assertEqual(hashlib.sha256(body).hexdigest(), row[side]["stored_sha256"])

    def test_metadata_fields_change_independently(self):
        cases = [({"compression": 0x5A42}, {"compression": 0x5A42, "memory_size": 40}, "memory_bytes"),
                 ({}, {"compression": 0x5A42}, "compression"),
                 ({"committed": 0}, {"committed": 1}, "committed"),
                 ({"extended": False}, {"extended": True, "committed": 0}, "extended_compression")]
        for left, right, field in cases:
            with self.subTest(field=field):
                self.before.write_bytes(package([entry(**left)]))
                self.after.write_bytes(package([entry(**right)]))
                row = self.compare()["changed"][0]
                self.assertFalse(row["stored_bytes_changed"])
                self.assertTrue(row["metadata_changed"])
                self.assertNotEqual(row["before"][field], row["after"][field])
                if field == "extended_compression":
                    self.assertIsNone(row["before"]["committed"])
                    self.assertEqual(0, row["after"]["committed"])
                else:
                    self.assertFalse(row["layout_changed"])

    def test_stored_and_metadata_changes_are_not_mutually_exclusive(self):
        self.before.write_bytes(package([entry(body=b"old")]))
        self.after.write_bytes(package([entry(body=b"new", committed=0)]))
        report = self.compare()
        self.assertEqual((1, 1, 1, 0), tuple(report[key] for key in
                         ("changed_count", "stored_bytes_changed_count", "metadata_changed_count", "layout_changed_count")))

    def test_all_recognized_compression_markers_hash_invalid_compressed_bytes(self):
        for method in resources.COMPRESSION_METHODS:
            with self.subTest(compression=method):
                self.before.write_bytes(package([]))
                self.after.write_bytes(package([entry(body=b"invalid compressed data", compression=method)]))
                # Invalid compressed bodies must remain opaque, including when
                # a codec would refuse or execute candidate-controlled data.
                with mock.patch("zlib.decompress", side_effect=AssertionError("decoded candidate")):
                    row = self.compare()["added"][0]
                self.assertEqual(hashlib.sha256(b"invalid compressed data").hexdigest(), row["stored_sha256"])
                self.assertEqual(f"0x{method:04X}", row["compression"])

    def test_all_shared_flags_and_mixed_widths_are_layout_not_stored_changes(self):
        entries = [entry(), entry((KEY[0], KEY[1], KEY[2] + 1), extended=False)]
        self.before.write_bytes(package(entries))
        for flags in range(8):
            with self.subTest(flags=flags):
                self.after.write_bytes(package(entries, flags=flags))
                report = self.compare()
                self.assertEqual(0, report["stored_bytes_changed_count"])
                self.assertEqual(0, report["metadata_changed_count"])
                self.assertEqual(0 if flags == 0 else 2, report["layout_changed_count"])
                self.assertEqual(flags != 0, report["index_layout_changed"])

    def test_index_first_and_legacy_position_are_not_payload_changes(self):
        for index_first, legacy in ((True, False), (False, True), (True, True)):
            with self.subTest(index_first=index_first, legacy=legacy):
                self.after.write_bytes(package(index_first=index_first, legacy=legacy))
                report = self.compare()
                self.assertTrue(report["index_layout_changed"])
                self.assertEqual(0, report["stored_bytes_changed_count"])
                self.assertEqual(0, report["metadata_changed_count"])
                self.assertEqual(int(index_first), report["layout_changed_count"])
                self.assertEqual(int(not index_first), report["unchanged_count"])

    def test_index_record_order_is_independent_of_physical_order(self):
        rows = [entry((1, 2, n), body=bytes([n]) * n) for n in (3, 1, 2)]
        raw = package(rows)
        self.before.write_bytes(raw)
        start = struct.unpack_from("<Q", raw, 64)[0] + 4
        records = [raw[start + i * 32:start + (i + 1) * 32] for i in range(3)]
        self.after.write_bytes(raw[:start] + b"".join(reversed(records)))
        with mock.patch.object(resources, "HASH_CHUNK_BYTES", 5):
            report = self.compare()
        self.assertEqual(2, report["layout_changed_count"])
        self.assertEqual(1, report["unchanged_count"])
        self.assertEqual(0, report["stored_bytes_changed_count"])
        self.assertFalse(report["index_layout_changed"])
        for row in report["changed"]:
            self.assertEqual(row["before"]["offset"], row["after"]["offset"])

    def test_unindexed_header_gap_and_trailing_changes_do_not_invent_resource_changes(self):
        raw = package([entry(body=b"abcPAD", stored_size=3, memory_size=3)]) + b"tail"
        self.before.write_bytes(raw)
        for offset in (12, 16, 24, 28, 99, len(raw) - 1):
            with self.subTest(offset=offset):
                altered = bytearray(raw)
                altered[offset] ^= 1
                self.after.write_bytes(altered)
                report = self.compare()
                self.assertFalse(report["package_bytes_equal"])
                self.assertEqual([], report["changed"])
                self.assertEqual(1, report["unchanged_count"])
                self.assertFalse(report["index_layout_changed"])

    def test_empty_packages_both_encodings_and_empty_vs_nonempty(self):
        encodings = (package([]), patch_u32(patch_u32(package([])[:96], 44, 0), 64, 0))
        for raw in encodings:
            self.before.write_bytes(raw)
            self.after.write_bytes(raw)
            report = self.compare()
            self.assertTrue(report["package_bytes_equal"])
            self.assertEqual(0, report["shared_count"])
            self.after.write_bytes(package())
            self.assertEqual(1, self.compare()["added_count"])
        self.before.write_bytes(encodings[0])
        self.after.write_bytes(encodings[1])
        report = self.compare()
        self.assertTrue(report["index_layout_changed"])
        self.assertFalse(report["package_bytes_equal"])
        self.assertEqual([], report["changed"])

    def test_empty_extents_at_index_eof_and_inside_nonempty_extents(self):
        # Five entries, one body of four bytes, followed by a 164-byte index.
        eof = 96 + 4 + 4 + 5 * 32
        rows = [entry((1, 2, 1), body=b"data"), entry((1, 2, 2), body=b"", offset=97),
                entry((1, 2, 3), body=b"", offset=100), entry((1, 2, 4), body=b"", offset=eof),
                entry((1, 2, 5), body=b"", offset=97)]
        self.before.write_bytes(package([]))
        self.after.write_bytes(package(rows))
        with mock.patch.object(resources, "HASH_CHUNK_BYTES", 3):
            added = self.compare()["added"]
        self.assertEqual(hashlib.sha256(b"data").hexdigest(), added[0]["stored_sha256"])
        self.assertTrue(all(row["stored_sha256"] == hashlib.sha256(b"").hexdigest() for row in added[1:]))

    def test_same_path_normalized_path_and_hardlink_are_valid_version_selections(self):
        for other in (self.before, self.before.parent / ".." / self.before.parent.name / self.before.name):
            report = diff.compare_resources(self.before, other)
            self.assertTrue(report["package_bytes_equal"])
            self.assertEqual(1, report["unchanged_count"])
        alias = self.root / "alias.package"
        os.link(self.before, alias)
        report = diff.compare_resources(self.before, alias)
        self.assertTrue(report["package_bytes_equal"])
        self.assertEqual([], report["changed"])
        for paths in ([self.before, self.before], [self.before, alias]):
            self.refused("duplicate_package_input", lambda: resources.audit_overlaps(paths))

    def test_preflight_both_sources_before_hashing_either(self):
        self.after.write_bytes(b"short")
        with mock.patch.object(resources, "_hash_source") as hashing:
            self.refused("package_size")
        hashing.assert_not_called()

    def test_second_pass_only_original_handle_and_no_per_resource_reads(self):
        original_hash = resources._hash_source
        calls = []

        def hashed(stream, size, *callback):
            calls.append((stream, size, len(callback)))
            return original_hash(stream, size, *callback)

        with mock.patch.object(resources, "_open_source", wraps=resources._open_source) as opened, \
                mock.patch.object(resources, "_read_exact", wraps=resources._read_exact) as read, \
                mock.patch.object(resources, "_hash_source", side_effect=hashed):
            report, hashes = resources._inspect_resources(self.before, diff._RangeCollector)
        self.assertEqual(1, opened.call_count)
        self.assertEqual(2, len(calls))
        self.assertIs(calls[0][0], calls[1][0])
        self.assertEqual([0, 1], [call[2] for call in calls])
        self.assertEqual([(0, 96), (0, 96), (110, 36)], [(call.args[1], call.args[2]) for call in read.call_args_list])
        self.assertEqual(hashlib.sha256(b"synthetic body").hexdigest(), hashes[tgi(KEY)])
        self.assertNotIn("stored_sha256", report["resources"][0])

    def test_public_inspection_uses_original_two_argument_hash_calls_and_report(self):
        expected = resources.inspect_resources(self.before)
        with mock.patch.object(resources, "_hash_source", wraps=resources._hash_source) as hashing:
            actual = resources.inspect_resources(self.before)
        self.assertEqual(expected, actual)
        self.assertEqual([2, 2], [len(call.args) for call in hashing.call_args_list])
        private, details = resources._inspect_resources(self.before)
        self.assertEqual(expected, private)
        self.assertIsNone(details)

    def test_comparison_retains_three_whole_file_passes_and_final_verification(self):
        with mock.patch.object(resources, "_hash_source", wraps=resources._hash_source) as hashing, \
                mock.patch.object(resources, "_open_source", wraps=resources._open_source) as opened, \
                mock.patch.object(resources, "_verify_snapshot", wraps=resources._verify_snapshot) as verified:
            self.compare()
        self.assertEqual([2, 3, 2, 3, 2, 2], [len(call.args) for call in hashing.call_args_list])
        self.assertEqual(4, opened.call_count)
        self.assertEqual([str(self.before), str(self.after)], [call.args[0]["source"] for call in verified.call_args_list])

    def test_chunk_boundaries_short_positive_reads_gaps_and_touching_ranges(self):
        rows = [entry((1, 2, 1), body=b"abcdefgh"), entry((1, 2, 2), body=b"i"),
                entry((1, 2, 3), body=b"jkl"), entry((1, 2, 4), body=b"mnop" + b"_" * 37,
                                                     stored_size=4, memory_size=4),
                entry((1, 2, 5), body=b"q" * 29), entry((1, 2, 6), body=b""),
                entry((1, 2, 7), body=b"rst")]
        raw = package(rows)
        self.before.write_bytes(raw)
        parsed = resources.inspect_resources(self.before)["resources"]
        expected = {tgi(row["key"]): hashlib.sha256(row["body"][:row.get("stored_size", len(row["body"]))]).hexdigest()
                    for row in rows}
        for chunk_size in (1, 3, 4, 8, 97, len(raw)):
            for short in (False, True):
                with self.subTest(chunk_size=chunk_size, short=short):
                    class Bounded(io.BytesIO):
                        def read(self, size=-1):
                            if not 0 <= size <= chunk_size:
                                raise AssertionError("unbounded hash read")
                            return super().read(min(size, 3) if short else size)
                    collector = diff._RangeCollector(list(reversed(parsed)))
                    with mock.patch.object(resources, "HASH_CHUNK_BYTES", chunk_size):
                        checksum = resources._hash_source(Bounded(raw), len(raw), collector.feed)
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), checksum)
                    self.assertEqual(expected, collector.finish())

    def test_collector_refuses_incomplete_or_nonsequential_feed(self):
        rows = resources.inspect_resources(self.before)["resources"]
        collector = diff._RangeCollector(rows)
        self.refused("package_changed", collector.finish)
        self.refused("package_changed", lambda: collector.feed(1, b"x"))

    def test_callback_hashing_still_refuses_early_eof_and_extra_growth_byte(self):
        for raw, size in ((b"abc", 4), (b"abcde", 4)):
            callback = mock.Mock()
            with self.subTest(raw=raw):
                self.refused("package_changed", lambda: resources._hash_source(io.BytesIO(raw), size, callback))

    def test_malformed_or_duplicate_sources_never_create_collector(self):
        cases = [
            (b"DBPP" + package()[4:], "invalid_package"),
            (package(version=(2, 0)), "unsupported_dbpf_version"),
            (patch_u32(package(), 60, 2), "unsupported_dbpf_layout"),
            (patch_u32(package(), 36, resources.MAX_ENTRIES + 1), "package_index_bound"),
            (patch_u32(package(), 64, 95), "package_index_range"),
            (package(flags=8), "unsupported_index_flags"),
            (package([entry(), entry()]), "duplicate_resource_key"),
            (package([entry(body=b"12345"), entry((1, 2, 3), body=b"ab", offset=97)]), "resource_overlap"),
            (package([entry(body=b"12345"), entry((1, 2, 3), body=b"12345", offset=96)]), "resource_overlap"),
            (package([entry(offset=110)]), "resource_index_overlap"),
            (package([entry(compression=9)]), "unsupported_index_entry"),
            (package([entry(compression=0xFFE0)]), "unsupported_deleted_resource"),
        ]
        for raw, code in cases:
            with self.subTest(code=code):
                self.before.write_bytes(raw)
                factory = mock.Mock()
                self.refused(code, lambda: resources._inspect_resources(self.before, factory))
                factory.assert_not_called()

    def test_invalid_path_types_links_and_reparse_points_refuse(self):
        for target in (self.root / "wrong.ts4script", self.root / "folder.package"):
            if target.suffix == ".package":
                target.mkdir()
            else:
                target.write_bytes(package())
            with self.subTest(target=target):
                self.refused("unsupported_package", lambda: diff.compare_resources(self.before, target))
        with mock.patch("sims4foundry_cli.io.stat.S_ISLNK", return_value=True):
            self.refused("linked_path")
        real_lstat = Path.lstat

        def reparse(path):
            value = real_lstat(path)
            if path == self.before:
                return type("ReparseStat", (), {"st_mode": value.st_mode, "st_file_attributes": 0x400})()
            return value

        with mock.patch.object(Path, "lstat", reparse):
            self.refused("linked_path")

    def test_collection_drift_discards_details_before_finish(self):
        original = diff._RangeCollector.feed
        changed = False

        def feed(collector, offset, chunk):
            nonlocal changed
            original(collector, offset, chunk)
            if not changed:
                changed = True
                info = self.before.stat()
                os.utime(self.before, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))

        with mock.patch.object(diff._RangeCollector, "feed", feed), \
                mock.patch.object(diff._RangeCollector, "finish") as finish:
            self.refused("package_changed")
        finish.assert_not_called()

    def test_same_length_body_drift_with_hidden_stat_still_fails_hash(self):
        original = resources._parse_index

        def parse(*args):
            rows = original(*args)
            raw = self.before.read_bytes()
            self.before.write_bytes(raw[:96] + b"X" + raw[97:])
            return rows

        with mock.patch.object(resources, "_parse_index", side_effect=parse), mock.patch.object(resources, "_check_source"), \
                mock.patch.object(diff._RangeCollector, "finish") as finish:
            self.refused("package_changed")
        finish.assert_not_called()

    def test_temporary_index_change_restored_before_second_hash_is_bound_to_parsed_slice(self):
        original = resources._read_exact
        raw = self.before.read_bytes()
        index_offset = struct.unpack_from("<Q", raw, 64)[0]

        def read(stream, offset, size):
            if offset == index_offset:
                # First full hash saw the original. Parse valid changed metadata,
                # then restore the original before its second complete hash.
                altered = bytearray(raw)
                struct.pack_into("<H", altered, index_offset + 4 + 30, 0)
                self.before.write_bytes(altered)
                value = original(stream, offset, size)
                self.before.write_bytes(raw)
                return value
            return original(stream, offset, size)

        with mock.patch.object(resources, "_read_exact", side_effect=read), mock.patch.object(resources, "_check_source"), \
                mock.patch.object(diff._RangeCollector, "finish") as finish:
            self.refused("package_changed")
        finish.assert_not_called()

    def test_header_change_during_second_hash_refuses_before_collector_finalization(self):
        original = resources._parse_index

        def parse(*args):
            rows = original(*args)
            self.before.write_bytes(patch_u32(self.before.read_bytes(), 12, 1))
            return rows

        with mock.patch.object(resources, "_parse_index", side_effect=parse), mock.patch.object(resources, "_check_source"), \
                mock.patch.object(diff._RangeCollector, "finish") as finish:
            self.refused("package_changed")
        finish.assert_not_called()

    def test_source_growth_and_shrink_before_collection_refuse(self):
        original = resources._parse_index
        for delta in (-1, 1):
            self.before.write_bytes(package())

            def parse(*args):
                rows = original(*args)
                raw = self.before.read_bytes()
                self.before.write_bytes(raw[:-1] if delta < 0 else raw + b"x")
                return rows

            with self.subTest(delta=delta), mock.patch.object(resources, "_parse_index", side_effect=parse):
                self.refused("package_changed")

    def test_identical_content_source_replacement_refuses(self):
        original = resources._parse_index

        def parse(*args):
            rows = original(*args)
            replacement = self.write("replacement.package", self.before.read_bytes())
            try:
                os.replace(replacement, self.before)
            except PermissionError:
                self.skipTest("Runner prevents replacement of an open source")
            return rows

        with mock.patch.object(resources, "_parse_index", side_effect=parse):
            self.refused("package_changed")

    def test_source_disappearance_refuses_without_publishing(self):
        original = diff._bounded_result

        def bounded(report):
            self.before.unlink()
            return original(report)

        output = self.root / "report.json"
        with mock.patch.object(diff, "_bounded_result", side_effect=bounded):
            status, result = self.run_cli("--output", str(output))
        self.assertEqual(2, status)
        self.assertFalse(result["ok"])
        self.assertFalse(output.exists())

    def test_earlier_source_drift_while_later_inspected_refuses(self):
        original = resources._inspect_resources

        def inspect(path, factory):
            result = original(path, factory)
            if path == self.after:
                self.before.write_bytes(package([entry(body=b"different body")]))
            return result

        with mock.patch.object(resources, "_inspect_resources", side_effect=inspect):
            self.refused("package_changed")

    def test_drift_after_report_size_check_reverifies_both_sources(self):
        original = diff._bounded_result
        for target in (self.before, self.after):
            self.before.write_bytes(package())
            self.after.write_bytes(package())

            def bounded(report):
                result = original(report)
                target.write_bytes(package([entry(body=b"different body")]))
                return result

            output = self.root / "report.json"
            with self.subTest(target=target), mock.patch.object(diff, "_bounded_result", side_effect=bounded):
                status, result = self.run_cli("--output", str(output))
            self.assertEqual((2, "package_changed"), (status, result["code"]))
            self.assertFalse(output.exists())

    def test_same_path_aliases_do_not_skip_cross_source_verification(self):
        original = resources._inspect_resources
        calls = 0

        def inspect(path, factory):
            nonlocal calls
            result = original(path, factory)
            calls += 1
            if calls == 2:
                path.write_bytes(package([entry(body=b"different body")]))
            return result

        with mock.patch.object(resources, "_inspect_resources", side_effect=inspect):
            self.refused("package_changed", lambda: diff.compare_resources(self.before, self.before))

    def test_boundaries_use_small_configured_limits(self):
        raw = package()
        size = len(raw)
        for name, exact in (("MAX_PACKAGE_BYTES", size), ("MAX_RESOURCE_BYTES", 14),
                            ("MAX_INDEX_BYTES", 36), ("MAX_ENTRIES", 1)):
            with self.subTest(bound=name):
                with mock.patch.object(resources, name, exact):
                    self.compare()
                with mock.patch.object(resources, name, exact - 1):
                    self.refused({"MAX_PACKAGE_BYTES": "package_size", "MAX_RESOURCE_BYTES": "resource_size",
                                  "MAX_INDEX_BYTES": "package_index_bound", "MAX_ENTRIES": "package_index_bound"}[name])

    def test_two_maximum_entry_packages_are_allowed_when_unchanged_report_fits(self):
        raw = package([entry((1, 2, n), body=b"") for n in range(8192)])
        self.before.write_bytes(raw)
        self.after.write_bytes(raw)
        # Overlap-audit combined caps are deliberately not version-diff caps.
        with mock.patch.object(resources, "MAX_AUDIT_ENTRIES", 0), mock.patch.object(resources, "MAX_AUDIT_BYTES", 0):
            report = self.compare()
        self.assertEqual(8192, report["unchanged_count"])
        self.assertEqual([], report["changed"])
        self.assertLess(len(json.dumps(report, indent=2)), 10000)

    def test_maximum_changed_input_refuses_only_at_complete_json_bound(self):
        self.before.write_bytes(package([entry((1, 2, n), body=b"a") for n in range(8192)]))
        self.after.write_bytes(package([entry((1, 2, n), body=b"b") for n in range(8192)]))
        original = diff._bounded_result
        examined = []

        def bounded(report):
            examined.append(report["changed_count"])
            return original(report)

        output = self.root / "report.json"
        with mock.patch.object(diff, "_bounded_result", side_effect=bounded):
            status, result = self.run_cli("--output", str(output))
        self.assertEqual([8192], examined)
        self.assertEqual((2, "oversized_json"), (status, result["code"]))
        self.assertNotIn("changed", result)
        self.assertNotIn("fewer packages", result["message"])
        self.assertFalse(output.exists())

    def test_actual_pretty_json_ascii_and_final_newline_limit(self):
        self.after = self.write("unicode/\u00e9.package", package([entry(body=b"changed body")]))
        expected = self.compare()
        raw = json.dumps(expected, ensure_ascii=True, allow_nan=False, indent=2).encode("utf-8") + b"\n"
        with mock.patch.object(diff, "MAX_JSON", len(raw)):
            self.assertEqual(expected, self.compare())
        with mock.patch.object(diff, "MAX_JSON", len(raw) - 1), \
                mock.patch.object(resources, "_verify_snapshot") as verify:
            self.refused("oversized_json")
        verify.assert_not_called()
        self.assertEqual(expected, loads(canonical(expected)))

    def test_cli_changed_success_canonical_output_and_no_source_mutation(self):
        self.after.write_bytes(package([entry(body=b"changed")]))
        originals = {path: path.read_bytes() for path in (self.before, self.after)}
        output = self.root / "report.json"
        status, result = self.run_cli("--output", str(output))
        self.assertEqual(0, status)
        self.assertEqual(1, result["changed_count"])
        self.assertEqual(canonical(result) + b"\n", output.read_bytes())
        self.assertEqual(originals, {path: path.read_bytes() for path in originals})
        self.assertFalse(list(self.root.glob("*.pending")))
        status, failure = self.run_cli("--output", str(output))
        self.assertEqual((2, "local_failure", "FileExistsError"), (status, failure["code"], failure["error_type"]))
        self.assertEqual(canonical(result) + b"\n", output.read_bytes())

    def test_cli_cannot_replace_source_or_write_protected_directory(self):
        original = self.before.read_bytes()
        status, result = self.run_cli("--output", str(self.before))
        self.assertEqual((2, "local_failure"), (status, result["code"]))
        self.assertEqual(original, self.before.read_bytes())
        output = self.root / "The Sims 4" / "report.json"
        status, result = self.run_cli("--output", str(output))
        self.assertEqual((2, "protected_path"), (status, result["code"]))
        self.assertFalse(output.exists())

    def test_cli_parser_requires_paths_and_refuses_execution_switches(self):
        for arguments in (["mods", "resource-diff"], ["mods", "resource-diff", "--before", str(self.before)],
                          ["mods", "resource-diff", "--before", str(self.before), "--after", str(self.after), "--execute"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    cli.parser().parse_args(arguments)
                self.assertEqual(2, caught.exception.code)

    def test_cli_malformed_and_wrong_extension_are_structured_failures(self):
        self.after.write_bytes(package(flags=8))
        status, result = self.run_cli()
        self.assertEqual((2, "unsupported_index_flags"), (status, result["code"]))
        self.after = self.write("bad.ts4script", package())
        status, result = self.run_cli()
        self.assertEqual((2, "unsupported_package"), (status, result["code"]))


if __name__ == "__main__":
    unittest.main()
