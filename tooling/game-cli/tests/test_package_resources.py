"""Synthetic DBPF 2.1 wire fixtures; no game assets, mods or saves are read.

Fixture structure independently follows S4PI's header/index field order and
LlamaLogic's conditional extended-compression field. Bodies are arbitrary test
bytes, deliberately not actual compressed resources or executable game data.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from sims4foundry_cli import mods, package_resources as resources
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import MAX_JSON, canonical, loads, new_json


KEY = (0x12345678, 0x89ABCDEF, 0xFEDCBA9876543210)


def entry(key=KEY, body=b"synthetic body", compression=0, extended=True, **fields):
    return {"key": key, "body": body, "compression": compression, "extended": extended, **fields}


def package(entries=None, flags=0, version=(2, 1), index_first=False, legacy=False):
    """Original byte fixture builder, independent of the parser under test."""
    if entries is None:
        entries = [entry()]
    shared_count = (flags & 7).bit_count()
    index_size = 4 + 4 * shared_count + sum(28 - 4 * shared_count + (4 if row["extended"] else 0) for row in entries)
    first_body = 96 + index_size if index_first else 96
    bodies, offsets = bytearray(), []
    for row in entries:
        offsets.append(first_body + len(bodies))
        bodies.extend(row["body"])
    index_offset = 96 if index_first else 96 + len(bodies)
    index = bytearray(struct.pack("<I", flags))
    first_key = entries[0]["key"] if entries else KEY
    shared_values = (first_key[0], first_key[1], first_key[2] >> 32)
    for bit in range(3):
        if flags & (1 << bit):
            index.extend(struct.pack("<I", shared_values[bit]))
    for row, offset in zip(entries, offsets):
        kind, group, instance = row["key"]
        for bit, value in enumerate((kind, group, instance >> 32)):
            if not flags & (1 << bit):
                index.extend(struct.pack("<I", value))
        size = row.get("stored_size", len(row["body"])) | (0x80000000 if row["extended"] else 0)
        index.extend(struct.pack("<4I", instance & 0xFFFFFFFF, row.get("offset", offset), size,
                                 row.get("memory_size", len(row["body"]))))
        if row["extended"]:
            index.extend(struct.pack("<HH", row["compression"], row.get("committed", 1)))
    header = bytearray(96)
    header[:4] = b"DBPF"
    struct.pack_into("<II", header, 4, *version)
    struct.pack_into("<I", header, 36, len(entries))
    struct.pack_into("<I", header, 44, len(index))
    struct.pack_into("<I", header, 60, 3)
    struct.pack_into("<I" if legacy else "<Q", header, 40 if legacy else 64, index_offset)
    return bytes(header + (index + bodies if index_first else bodies + index))


def patch_u32(raw, offset, value):
    raw = bytearray(raw)
    struct.pack_into("<I", raw, offset, value)
    return bytes(raw)


class PackageResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.write("example.package", package())

    def write(self, name, raw):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return target

    def test_stream_wrapper_failure_closes_owned_descriptor(self):
        opened = []
        real_open = os.open
        def opened_source(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd
        with mock.patch.object(resources.os, "open", side_effect=opened_source), \
                mock.patch.object(resources.os, "fdopen", side_effect=OSError("wrapper failed")):
            with self.assertRaises(OSError): resources.inspect_resources(self.path)
        self.assertEqual(1, len(opened))
        with self.assertRaises(OSError): os.fstat(opened[0])

    def refused(self, raw, code):
        self.path.write_bytes(raw)
        with self.assertRaises(FoundryError) as caught:
            resources.inspect_resources(self.path)
        self.assertEqual(code, caught.exception.code)

    def test_golden_96_byte_header_and_full_index(self):
        raw = package()
        report = resources.inspect_resources(self.path)
        self.assertEqual("2.1", report["dbpf_version"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), report["sha256"])
        self.assertEqual("bounded_header_and_index", report["validation"])
        self.assertEqual({"entry_count": 1, "offset": 110, "bytes": 36, "version_marker": 3,
                          "position_field": "offset64", "shared_flags": "0x00000000", "shared_fields": {}}, report["index"])
        row = report["resources"][0]
        self.assertEqual("0x12345678", row["type"])
        self.assertEqual("0x89ABCDEF", row["group"])
        self.assertEqual("0xFEDCBA9876543210", row["instance"])
        self.assertEqual("12345678:89ABCDEF:FEDCBA9876543210", row["tgi"])
        self.assertEqual(32, row["index_entry_bytes"])
        self.assertEqual(114, row["index_entry_offset"])
        self.assertEqual(96, row["offset"])
        for flag in ("resource_contents_validated", "decompressed", "code_executed", "game_modified", "compatibility_verified"):
            self.assertIs(False, report[flag])
        self.assertEqual(raw, self.path.read_bytes())

    def test_all_eight_shared_flag_combinations_and_mixed_entry_widths(self):
        rows = [entry(), entry((KEY[0], KEY[1], KEY[2] + 1), extended=False)]
        for flags in range(8):
            with self.subTest(flags=flags):
                self.path.write_bytes(package(rows, flags=flags))
                report = resources.inspect_resources(self.path)
                self.assertEqual(f"0x{flags:08X}", report["index"]["shared_flags"])
                self.assertEqual(2, report["resource_count"])
                self.assertEqual("0xFEDCBA9876543211", report["resources"][1]["instance"])
                self.assertEqual(32 - 4 * flags.bit_count(), report["resources"][0]["index_entry_bytes"])
                self.assertEqual(28 - 4 * flags.bit_count(), report["resources"][1]["index_entry_bytes"])
                self.assertIsNone(report["resources"][1]["committed"])

    def test_zero_shared_values_are_not_treated_as_missing(self):
        self.path.write_bytes(package([entry((0, 0, 1)), entry((0, 0, 2))], flags=7))
        report = resources.inspect_resources(self.path)
        self.assertEqual("00000000:00000000:0000000000000002", report["resources"][1]["tgi"])

    def test_known_compression_metadata_is_not_body_validation(self):
        for compression, name in resources.COMPRESSION_METHODS.items():
            with self.subTest(compression=compression):
                # These bytes are NOT valid compressed data. No decompressor is
                # called, no decoded output is returned, and no safety is claimed.
                self.path.write_bytes(package([entry(body=b"not compressed", compression=compression)]))
                report = resources.inspect_resources(self.path)
                self.assertEqual(name, report["resources"][0]["compression_method"])
                self.assertFalse(report["resource_contents_validated"])

    def test_empty_index_with_flags_and_header_only_are_explicit(self):
        for raw in (package([]), patch_u32(patch_u32(package([])[:96], 44, 0), 64, 0)):
            with self.subTest(length=len(raw)):
                self.path.write_bytes(raw)
                report = resources.inspect_resources(self.path)
                self.assertEqual([], report["resources"])
                self.assertEqual(0, report["resource_count"])

    def test_legacy_position_and_resources_after_index(self):
        for index_first in (False, True):
            for legacy in (False, True):
                with self.subTest(index_first=index_first, legacy=legacy):
                    self.path.write_bytes(package(index_first=index_first, legacy=legacy))
                    report = resources.inspect_resources(self.path)
                    self.assertEqual("legacy_offset32" if legacy else "offset64", report["index"]["position_field"])
                    self.assertEqual(1, report["resource_count"])

    def test_unknown_version_signature_or_reserved_layout_refuses(self):
        for version in ((1, 1), (2, 0), (2, 2), (3, 0)):
            with self.subTest(version=version):
                self.refused(package(version=version), "unsupported_dbpf_version")
        self.refused(b"DBPP" + package()[4:], "invalid_package")
        for offset in (20, 32, 48, 52, 56, 60, 72, 92):
            with self.subTest(offset=offset):
                self.refused(patch_u32(package(), offset, 4), "unsupported_dbpf_layout")

    def test_signature_only_contract_is_unchanged(self):
        self.path.write_bytes(b"DBPFfake")
        signature = mods.inspect_artifact(self.path)
        self.assertEqual("signature_only", signature["validation"])
        self.assertFalse(signature["resources_validated"])
        with self.assertRaises(FoundryError):
            resources.inspect_resources(self.path)

    def test_short_header_and_all_truncated_index_suffixes_refuse(self):
        for length in (0, 4, 95):
            with self.subTest(length=length):
                self.refused(package()[:length], "package_size")
        raw = package()
        for trim in range(1, 36):
            with self.subTest(trim=trim):
                self.refused(raw[:-trim], "package_index_range")
        # A self-consistent shortened index still cannot silently drop the
        # optional compression word that its entry's size bit requires.
        self.refused(patch_u32(raw[:-4], 44, 32), "package_index_truncated")

    def test_short_read_raises_machine_readable_error(self):
        with self.assertRaises(FoundryError) as caught:
            resources._read_exact(io.BytesIO(b"abc"), 0, 4)
        self.assertEqual("package_truncated", caught.exception.code)

    def test_unknown_shared_flag_and_unexplained_index_bytes_refuse(self):
        for flag in (8, 16, 0x80000000, 0xFFFFFFFF):
            with self.subTest(flag=flag):
                self.refused(package(flags=flag), "unsupported_index_flags")
        raw = package()
        self.refused(patch_u32(raw + b"extra", 44, 41), "package_index_size")
        self.refused(patch_u32(raw, 36, 0), "package_index_size")

    def test_huge_counts_and_sizes_rejected_before_index_read_or_hash(self):
        for offset, value in ((36, 0xFFFFFFFF), (44, 0xFFFFFFFF), (36, resources.MAX_ENTRIES + 1)):
            self.path.write_bytes(patch_u32(package(), offset, value))
            with self.subTest(offset=offset), mock.patch.object(resources, "_hash_source") as hashing:
                with self.assertRaises(FoundryError) as caught:
                    resources.inspect_resources(self.path)
                self.assertEqual("package_index_bound", caught.exception.code)
                hashing.assert_not_called()
        self.refused(patch_u32(package(), 36, 3), "package_index_count")

    def test_package_file_size_bound(self):
        with mock.patch.object(resources, "MAX_PACKAGE_BYTES", 100):
            with self.assertRaises(FoundryError) as caught:
                resources.inspect_resources(self.path)
            self.assertEqual("package_size", caught.exception.code)

    def test_header_index_extents_and_64bit_overflow_do_not_wrap(self):
        raw = package()
        for offset in (0, 1, 95, len(raw), 0xFFFFFFFF):
            with self.subTest(offset=offset):
                self.refused(patch_u32(raw, 64, offset), "package_index_range")
        self.refused(patch_u32(raw, 68, 1), "package_index_range")
        self.refused(patch_u32(raw, 40, 123), "ambiguous_index_offset")
        self.refused(patch_u32(raw, 44, 0), "package_index_range")

    def test_resource_ranges_header_index_eof_and_integer_overflow(self):
        for offset in (0, 95, 0xFFFFFFFF):
            with self.subTest(offset=offset):
                self.refused(package([entry(offset=offset)]), "resource_range")
        self.refused(package([entry(offset=110)]), "resource_index_overlap")
        self.refused(package([entry(offset=105)]), "resource_index_overlap")
        self.refused(package([entry(offset=145, stored_size=2, memory_size=2)]), "resource_range")
        self.refused(package([entry(memory_size=0xFFFFFFFF)]), "resource_size")
        self.refused(package([entry(stored_size=0x7FFFFFFF)]), "resource_size")

    def test_resource_overlap_alias_and_containment_refuse(self):
        other = (KEY[0], KEY[1], KEY[2] + 1)
        for offset in (96, 97, 105):
            with self.subTest(offset=offset):
                self.refused(package([entry(body=b"1234567890"), entry(other, body=b"123", offset=offset)]), "resource_overlap")

    def test_touching_extents_and_zero_length_resources_are_allowed(self):
        rows = [entry(body=b"first"), entry((1, 2, 3), body=b"second"), entry((1, 2, 4), body=b"")]
        self.path.write_bytes(package(rows))
        report = resources.inspect_resources(self.path)
        self.assertEqual(3, report["resource_count"])
        self.assertEqual(0, report["resources"][2]["stored_bytes"])

    def test_duplicate_exact_keys_within_package_refuse(self):
        self.refused(package([entry(), entry(body=b"different")]), "duplicate_resource_key")

    def test_unknown_compression_committed_deleted_and_size_mismatch(self):
        for fields in ({"compression": 7}, {"committed": 2}):
            with self.subTest(fields=fields):
                self.refused(package([entry(**fields)]), "unsupported_index_entry")
        self.refused(package([entry(compression=0xFFE0)]), "unsupported_deleted_resource")
        self.refused(package([entry(memory_size=99)]), "resource_size_mismatch")

    def test_regular_portable_unlinked_paths_only(self):
        for path in (self.root / "folder.package", self.root / "wrong.ts4script", self.root / "CON.package"):
            if path.name == "folder.package":
                path.mkdir()
            elif path.name != "CON.package" or os.name != "nt":
                path.write_bytes(package())
            with self.subTest(path=path), self.assertRaises(FoundryError):
                resources.inspect_resources(path)
        link = self.root / "linked.package"
        try:
            link.symlink_to(self.path)
        except OSError:
            return  # This part is unavailable on some Windows CI runners.
        with self.assertRaises(FoundryError) as caught:
            resources.inspect_resources(link)
        self.assertEqual("linked_path", caught.exception.code)

    def test_stat_drift_during_index_parse_refuses(self):
        original = resources._parse_index

        def altered(*args):
            rows = original(*args)
            info = self.path.stat()
            os.utime(self.path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            return rows

        with mock.patch.object(resources, "_parse_index", side_effect=altered):
            with self.assertRaises(FoundryError) as caught:
                resources.inspect_resources(self.path)
        self.assertEqual("package_changed", caught.exception.code)

    def test_replaced_source_inode_refuses_even_when_contents_match(self):
        original = resources._parse_index

        def replaced(*args):
            rows = original(*args)
            replacement = self.write("replacement.package", self.path.read_bytes())
            try:
                os.replace(replacement, self.path)
            except PermissionError:
                self.skipTest("This runner prevents replacement of an open file")
            return rows

        with mock.patch.object(resources, "_parse_index", side_effect=replaced):
            with self.assertRaises(FoundryError) as caught:
                resources.inspect_resources(self.path)
        self.assertEqual("package_changed", caught.exception.code)

    def test_same_length_body_change_detected_by_hash_even_if_stat_is_hidden(self):
        original = resources._parse_index

        def altered(*args):
            rows = original(*args)
            raw = self.path.read_bytes()
            self.path.write_bytes(raw[:96] + b"X" + raw[97:])
            return rows

        with mock.patch.object(resources, "_parse_index", side_effect=altered), mock.patch.object(resources, "_check_source"):
            with self.assertRaises(FoundryError) as caught:
                resources.inspect_resources(self.path)
        self.assertEqual("package_changed", caught.exception.code)

    def test_hashing_detects_shrink_and_growth_and_limits_read_size(self):
        for raw, expected in ((b"123", 4), (b"12345", 4)):
            with self.subTest(raw=raw), self.assertRaises(FoundryError) as caught:
                resources._hash_source(io.BytesIO(raw), expected)
            self.assertEqual("package_changed", caught.exception.code)

        class BoundedStream(io.BytesIO):
            def read(self, size=-1):
                if not 0 <= size <= resources.HASH_CHUNK_BYTES:
                    raise AssertionError("unbounded hash read")
                return super().read(size)

        raw = b"a" * (resources.HASH_CHUNK_BYTES + 123)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), resources._hash_source(BoundedStream(raw), len(raw)))

    def test_only_header_and_index_are_parsed_source_hash_is_opaque(self):
        calls = []
        original = resources._read_exact

        def record(stream, offset, size):
            calls.append((offset, size))
            return original(stream, offset, size)

        with mock.patch.object(resources, "_read_exact", side_effect=record), mock.patch("zlib.decompress", side_effect=AssertionError("must not decode")):
            report = resources.inspect_resources(self.path)
        self.assertEqual([(0, 96), (0, 96), (110, 36)], calls)
        self.assertEqual("opaque_stream_for_source_hash_only", report["body_handling"])

    def test_json_read_write_and_pretty_output_share_bound(self):
        report = resources.inspect_resources(self.path)
        pretty_size = len(json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2).encode("utf-8")) + 1
        self.assertLess(pretty_size, MAX_JSON)
        self.assertEqual(report, loads(canonical(report)))
        output = self.root / "report.json"
        new_json(output, report)
        self.assertEqual(report, json.loads(output.read_text()))
        with mock.patch.object(resources, "MAX_JSON", pretty_size - 1):
            with self.assertRaises(FoundryError) as caught:
                resources.inspect_resources(self.path)
        self.assertEqual("oversized_json", caught.exception.code)
        with self.assertRaises(FileExistsError):
            new_json(output, report)

    def test_maximum_supported_entry_count_uses_bounded_real_wire_fixture(self):
        self.path.write_bytes(package([entry((1, 2, i), body=b"") for i in range(resources.MAX_ENTRIES)], flags=7))
        report = resources.inspect_resources(self.path)
        self.assertEqual(resources.MAX_ENTRIES, report["resource_count"])
        self.assertLessEqual(len(json.dumps(report, ensure_ascii=True, indent=2).encode("utf-8")) + 1, MAX_JSON)


class PackageOverlapTests(unittest.TestCase):
    setUp = PackageResourceTests.setUp
    write = PackageResourceTests.write

    def test_exact_tgi_overlap_lists_every_owner_even_equal_basenames(self):
        same = self.write("other/example.package", package([entry(body=b"different body")]))
        third = self.write("third.package", package([entry(body=b"third body")]))
        before = {path: path.read_bytes() for path in (self.path, same, third)}
        report = resources.audit_overlaps([self.path, same, third])
        self.assertEqual(1, report["overlap_count"])
        row = report["overlaps"][0]
        self.assertEqual("override_candidate", row["classification"])
        self.assertIsNone(row["resource_bytes_equal"])
        self.assertEqual({str(path) for path in before}, {owner["source"] for owner in row["owners"]})
        self.assertEqual(3, len(row["owners"]))
        self.assertFalse(report["load_order_inferred"])
        self.assertFalse(report["compatibility_verified"])
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_type_group_high_and_low_instance_are_all_part_of_identity(self):
        k = KEY
        rows = [entry((k[0] + 1, k[1], k[2])), entry((k[0], k[1] - 1, k[2])),
                entry((k[0], k[1], k[2] ^ (1 << 32))), entry((k[0], k[1], k[2] + 1))]
        other = self.write("distinct.package", package(rows))
        report = resources.audit_overlaps([self.path, other])
        self.assertEqual(0, report["overlap_count"])
        self.assertEqual(5, report["unique_tgi_count"])
        self.assertFalse(report["compatibility_verified"])

    def test_identical_package_bytes_are_distinct_from_override_candidates(self):
        identical = self.write("duplicate.package", self.path.read_bytes())
        report = resources.audit_overlaps([self.path, identical])
        self.assertEqual(1, len(report["duplicate_package_groups"]))
        self.assertEqual(2, len(report["duplicate_package_groups"][0]["owners"]))
        row = report["overlaps"][0]
        self.assertEqual("identical_package_bytes", row["classification"])
        self.assertTrue(row["resource_bytes_equal"])
        self.assertEqual("whole_package_sha256", row["equality_basis"])
        self.assertFalse(report["compatibility_verified"])

    def test_identical_resource_body_in_different_package_is_not_claimed_equal(self):
        other = self.write("two-entries.package", package([entry(), entry((1, 2, 3))]))
        report = resources.audit_overlaps([self.path, other])
        self.assertEqual("override_candidate", report["overlaps"][0]["classification"])
        self.assertIsNone(report["overlaps"][0]["resource_bytes_equal"])

    def test_duplicate_package_group_preserved_with_third_override_owner(self):
        same = self.write("same.package", self.path.read_bytes())
        other = self.write("other.package", package([entry(body=b"changed")]))
        report = resources.audit_overlaps([self.path, same, other])
        self.assertEqual(2, len(report["duplicate_package_groups"][0]["owners"]))
        self.assertEqual(3, len(report["overlaps"][0]["owners"]))
        self.assertEqual("override_candidate", report["overlaps"][0]["classification"])

    def test_repeated_input_and_hard_link_alias_refuse(self):
        with self.assertRaises(FoundryError) as caught:
            resources.audit_overlaps([self.path, self.path])
        self.assertEqual("duplicate_package_input", caught.exception.code)
        alias = self.root / "alias.package"
        os.link(self.path, alias)
        with self.assertRaises(FoundryError) as caught:
            resources.audit_overlaps([self.path, alias])
        self.assertEqual("duplicate_package_input", caught.exception.code)

    def test_audit_input_count_aggregate_bytes_entries_and_json_bounds(self):
        for paths in ([], str(self.path), iter([self.path]), [self.path] * 129):
            with self.subTest(paths=type(paths).__name__), self.assertRaises(FoundryError) as caught:
                resources.audit_overlaps(paths)
            self.assertEqual("package_audit_bound", caught.exception.code)
        for name in ("MAX_AUDIT_BYTES", "MAX_AUDIT_ENTRIES"):
            with self.subTest(bound=name), mock.patch.object(resources, name, 0):
                with self.assertRaises(FoundryError) as caught:
                    resources.audit_overlaps([self.path])
                self.assertEqual("package_audit_bound", caught.exception.code)
        other = self.write("other.package", package())
        inspect_size = len(json.dumps(resources.inspect_resources(self.path), indent=2)) + 1
        with mock.patch.object(resources, "MAX_JSON", inspect_size + 50):
            with self.assertRaises(FoundryError) as caught:
                resources.audit_overlaps([self.path, other])
            self.assertEqual("oversized_json", caught.exception.code)

    def test_earlier_source_changed_while_later_source_inspected_refuses(self):
        other = self.write("other.package", package([entry((1, 2, 3))]))
        inspect = resources.inspect_resources

        def changed(path):
            result = inspect(path)
            if path == other:
                raw = self.path.read_bytes()
                self.path.write_bytes(raw[:96] + b"X" + raw[97:])
            return result

        with mock.patch.object(resources, "inspect_resources", side_effect=changed):
            with self.assertRaises(FoundryError) as caught:
                resources.audit_overlaps([self.path, other])
        self.assertEqual("package_changed", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
