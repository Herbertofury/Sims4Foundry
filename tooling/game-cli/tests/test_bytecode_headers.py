"""Opaque generated headers only: no candidate import, marshal or execution."""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import marshal
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

from sims4foundry_cli import bytecode_headers as headers, cli, mods
from sims4foundry_cli.errors import FoundryError


MAGIC = bytes.fromhex("420d0d0a")


def header(flags=0, first=0xffffffff, second=0x80000000):
    return MAGIC + struct.pack("<III", flags, first, second)


def archive_bytes(rows, method=zipfile.ZIP_STORED, comment=b""):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=method) as archive:
        for name, data in rows:
            archive.writestr(name, data)
        archive.comment = comment
    return stream.getvalue()


class BytecodeHeaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "candidate.ts4script"

    def write(self, rows=None, method=zipfile.ZIP_STORED, comment=b""):
        raw = archive_bytes(rows if rows is not None else [("fixture.pyc", header() + b"opaque body")], method, comment)
        self.path.write_bytes(raw)
        return raw

    def inspect(self, target=None):
        return headers.inspect_headers(self.path, target)

    def refused(self, code, target=None):
        with self.assertRaises(FoundryError) as caught:
            self.inspect(target)
        self.assertEqual(code, caught.exception.code)

    def run_cli(self, *args):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = cli.main(["mods", "bytecode", "--file", str(self.path), *args])
        return status, json.loads(stdout.getvalue())

    def test_timestamp_unsigned_fields_and_opaque_body_do_not_assert_validity(self):
        raw = self.write()
        report = self.inspect("420D0D0A")
        row = report["members"][0]
        self.assertEqual("420d0d0a", report["target_magic_hex"])
        self.assertEqual("reference_header_complete", row["header_status"])
        self.assertEqual("timestamp", row["reference_interpretation"])
        self.assertEqual((0xffffffff, 0x80000000), (row["mtime_u32"], row["source_size_u32"]))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), report["sha256"])
        self.assertEqual(16, row["prefix_bytes"])
        self.assertEqual(header().hex(), row["prefix_hex"])
        self.assertEqual(raw, self.path.read_bytes())
        for field in ("code_executed", "members_extracted", "marshal_body_validated", "source_hash_verified",
                      "importability_verified", "compatibility_verified", "game_modified"):
            self.assertIs(report[field], False)
        for field in ("valid_bytecode", "importable", "compatible", "malware_safe", "game_version", "live_qualified"):
            self.assertNotIn(field, report)
            self.assertNotIn(field, row)

    def test_every_prefix_length_stored_and_deflate(self):
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            for length in range(17):
                with self.subTest(method=method, length=length):
                    self.write([("fixture.pyc", header()[:length])], method)
                    row = self.inspect("420d0d0a")["members"][0]
                    self.assertEqual(length, row["prefix_bytes"])
                    self.assertEqual(length, row["declared_bytes"])
                    expected = ("truncated_magic" if length < 4 else
                                "truncated_reference_header" if length < 16 else "reference_header_complete")
                    self.assertEqual(expected, row["header_status"])
                    self.assertEqual("unavailable" if length < 4 else "match", row["target_comparison"])
                    self.assertEqual(0 if length == 16 else None, row["flags_u32"])
                    self.assertFalse(self.inspect()["marshal_body_validated"])

    def test_reference_hash_flags_are_raw_and_never_verified(self):
        for flags, kind in ((1, "unchecked_hash"), (3, "checked_hash")):
            with self.subTest(flags=flags):
                self.write([("fixture.pyc", header(flags))])
                report = self.inspect()
                row = report["members"][0]
                self.assertEqual(kind, row["reference_interpretation"])
                self.assertEqual("ffffffff00000080", row["source_hash_hex"])
                self.assertIsNone(row["mtime_u32"])
                self.assertIsNone(row["source_size_u32"])
                self.assertTrue(row["hash_based_bit"])
                self.assertEqual(flags == 3, row["check_source_bit"])
                self.assertFalse(report["source_hash_verified"])

    def test_flags_two_follows_importlib_timestamp_but_records_loader_divergence(self):
        self.write([("fixture.pyc", header(2))])
        row = self.inspect()["members"][0]
        self.assertEqual("timestamp_bit_clear", row["reference_interpretation"])
        self.assertEqual(["flags_2_loader_divergence"], row["findings"])
        self.assertEqual(headers.REFERENCE_LAYOUT, row["layout_reference"])
        self.assertFalse(row["hash_based_bit"])
        self.assertTrue(row["check_source_bit"])
        self.assertEqual(0xffffffff, row["mtime_u32"])
        self.assertIsNone(row["source_hash_hex"])

    def test_flags_outside_reference_mask_have_no_universal_invalidity_claim(self):
        for flags in (4, 5, 6, 7, 0xffffffff):
            with self.subTest(flags=flags):
                self.write([("fixture.pyc", header(flags))])
                row = self.inspect()["members"][0]
                self.assertEqual(flags, row["flags_u32"])
                self.assertEqual(["outside_reference_flag_mask"], row["findings"])
                for key in ("reference_interpretation", "source_hash_hex", "mtime_u32", "source_size_u32"):
                    self.assertIsNone(row[key])

    def test_independently_generated_cpython_3717_prefixes(self):
        # Generated with official CPython 3.7.17 from an original harmless
        # 67-byte source, never from candidate code. Only the prefix is retained.
        cases = (("420d0d0a00000000d202964943000000", "timestamp"),
                 ("420d0d0a030000001e0abfad9440f912", "checked_hash"),
                 ("420d0d0a010000001e0abfad9440f912", "unchecked_hash"))
        for prefix, kind in cases:
            with self.subTest(kind=kind):
                self.write([("original.pyc", bytes.fromhex(prefix))])
                row = self.inspect()["members"][0]
                self.assertEqual(kind, row["reference_interpretation"])
                if kind == "timestamp":
                    self.assertEqual((1234567890, 67), (row["mtime_u32"], row["source_size_u32"]))
                else:
                    self.assertEqual("1e0abfad9440f912", row["source_hash_hex"])

    def test_unknown_legacy_and_prerelease_magic_are_unparsed_but_comparable(self):
        for prefix in (b"abcd", b"ABCD" + bytes(8), bytes.fromhex("410d0d0a") + bytes(12)):
            with self.subTest(prefix=prefix):
                self.write([("fixture.pyc", prefix)])
                row = self.inspect(prefix[:4].hex())["members"][0]
                self.assertEqual("match", row["target_comparison"])
                self.assertEqual("unsupported_layout", row["header_status"])
                for key in ("layout_reference", "flags_u32", "hash_based_bit", "check_source_bit", "reference_interpretation"):
                    self.assertIsNone(row[key])
        self.write()
        row = self.inspect("00000000")["members"][0]
        self.assertEqual("mismatch", row["target_comparison"])
        self.assertEqual("timestamp", row["reference_interpretation"])

    def test_comparison_summary_is_not_vacuously_matching(self):
        for target, rows, status, counts in (
                (None, [("x.pyc", b"")], "not_requested", (0, 0, 0)),
                ("420d0d0a", [("x.py", b"source is not inspected")], "no_bytecode", (0, 0, 0)),
                (None, [("x.txt", b"")], "not_requested", (0, 0, 0)),
                ("420d0d0a", [("x.pyc", b"")], "incomplete", (0, 0, 1)),
                ("420d0d0a", [("x.pyc", header())], "match", (1, 0, 0)),
                ("420d0d0a", [("a.pyc", MAGIC), ("b.pyc", b"ABCD"), ("c.pyc", b"x")], "mismatch", (1, 1, 1)),
                ("420d0d0a", [("a.pyc", MAGIC), ("b.pyc", b"x")], "incomplete", (1, 0, 1))):
            with self.subTest(target=target, status=status, counts=counts):
                self.write(rows)
                report = self.inspect(target)
                self.assertEqual(status, report["comparison"]["status"])
                self.assertEqual(counts, tuple(report["comparison"][key + "_count"] for key in ("match", "mismatch", "unavailable")))
                self.assertTrue(report["ok"])

    def test_archive_order_case_insensitive_suffix_and_directory_exclusion(self):
        names = ["z.pyc", "Nested/Mod.PYC", "__pycache__/x.cpython-37.pyc", "empty.pyc/", "s.py", "y.txt"]
        self.write([(name, b"" if name.endswith("/") else header()) for name in names])
        report = self.inspect()
        self.assertEqual(6, report["member_count"])
        self.assertEqual(3, report["bytecode_count"])
        self.assertEqual(1, report["directory_count"])
        self.assertEqual(1, report["python_source_count"])
        self.assertEqual(1, report["other_file_count"])
        self.assertEqual(names[:3], [row["member"] for row in report["members"]])

    def test_bad_target_rejected_before_any_path_or_artifact_operation(self):
        for target in ("", "0x420d0d0a", " 420d0d0a", "420d0d0a\n", "420d0d0", "420d0d0aa", "gggggggg", "４２０d0d0a", 1234, b"420d0d0a"):
            with self.subTest(target=target), \
                    mock.patch.object(headers.os.path, "abspath", side_effect=AssertionError("path reached")), \
                    mock.patch.object(mods, "_inspect_artifact", side_effect=AssertionError("artifact reached")):
                self.refused("invalid_target_magic", target)

    def test_only_selected_regular_ts4script_is_supported(self):
        for name in ("x.pyc", "x.package", "x.zip"):
            with self.subTest(name=name), mock.patch.object(mods, "_inspect_artifact", side_effect=AssertionError("artifact reached")):
                with self.assertRaises(FoundryError) as caught:
                    headers.inspect_headers(self.root / name)
                self.assertEqual("unsupported_bytecode_artifact", caught.exception.code)
        self.path.mkdir()
        self.refused("unsupported_artifact")
        self.path.rmdir()
        self.write()
        upper = self.path.with_suffix(".TS4SCRIPT")
        self.path.rename(upper)
        self.assertEqual(1, headers.inspect_headers(upper)["bytecode_count"])

    def test_observed_link_source_refuses(self):
        self.write()
        link = self.root / "link.ts4script"
        try:
            link.symlink_to(self.path)
        except OSError:
            self.skipTest("Host cannot create symbolic links")
        with self.assertRaises(FoundryError) as caught:
            headers.inspect_headers(link)
        self.assertEqual("linked_path", caught.exception.code)

    def test_full_crc_then_prefixes_use_same_archive_infos_and_bounded_short_reads(self):
        raw = self.write([("a.pyc", header() + bytes(range(256)) * 32), ("b.PYC", b"abc"), ("c.txt", b"opaque")], zipfile.ZIP_DEFLATED)
        events, requests, views, originals = [], [], [], []

        class ShortReader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def read(self, size):
                requests.append(size)
                return self.stream.read(min(size, 3))

        class TrackedArchive(zipfile.ZipFile):
            phase = "before_crc"
            def __init__(self, view):
                views.append(view)
                super().__init__(view)
                originals.extend(self.infolist())
            def testzip(self):
                events.append("crc_begin")
                self.phase = "crc"
                result = super().testzip()
                self.phase = "prefix"
                events.append("crc_end")
                return result
            def open(self, row, mode="r", *args, **kwargs):
                if self.phase == "prefix":
                    events.append(row.orig_filename)
                    assert any(row is item for item in originals)
                    assert self.fp is views[0]
                    return ShortReader(super().open(row, mode, *args, **kwargs))
                return super().open(row, mode, *args, **kwargs)

        with mock.patch.object(mods.zipfile, "ZipFile", TrackedArchive):
            report = self.inspect()
        self.assertEqual(["crc_begin", "crc_end", "a.pyc", "b.PYC"], events)
        self.assertEqual(1, len(views))
        self.assertIsInstance(views[0], mods._ScriptArchiveView)
        self.assertTrue(all(1 <= size <= 16 for size in requests))
        self.assertEqual([16, 3], [row["prefix_bytes"] for row in report["members"]])
        self.assertEqual(raw, self.path.read_bytes())

    def test_unexpected_prefix_eof_refuses_instead_of_returning_partial_report(self):
        self.write()
        original = headers._prefix
        def short_prefix(archive, row):
            reader = mock.MagicMock()
            reader.__enter__.return_value.read.return_value = b""
            with mock.patch.object(archive, "open", return_value=reader):
                return original(archive, row)
        with mock.patch.object(headers, "_prefix", side_effect=short_prefix):
            self.refused("bytecode_prefix_truncated")

    def test_corrupt_tail_or_later_non_pyc_prevents_prefix_collection(self):
        for broken_name in ("a.pyc", "later.txt"):
            with self.subTest(broken=broken_name):
                raw = self.write([("a.pyc", header() + b"a" * 8000), ("later.txt", b"non-bytecode-tail")])
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    row = archive.getinfo(broken_name)
                data_start = row.header_offset + 30 + len(row.filename.encode("utf-8")) + len(row.extra)
                corrupted = bytearray(raw)
                corrupted[data_start + row.file_size - 1] ^= 1
                self.path.write_bytes(corrupted)
                with mock.patch.object(headers, "_collect_headers", side_effect=AssertionError("prefix reached")):
                    self.refused("archive_crc", "420d0d0a")

    def test_persistent_change_between_crc_and_prefixes_is_rejected(self):
        raw = self.write(comment=b"comment")
        original = zipfile.ZipFile.testzip
        def change(archive):
            result = original(archive)
            changed = bytearray(raw)
            changed[-1] ^= 1
            self.path.write_bytes(changed)
            return result
        with mock.patch.object(zipfile.ZipFile, "testzip", change):
            self.refused("artifact_changed")

    def test_change_after_prefixes_before_final_hash_has_no_output(self):
        raw = self.write(comment=b"comment")
        original = headers._collect_headers
        def change(*args):
            result = original(*args)
            changed = bytearray(raw)
            changed[-1] ^= 1
            self.path.write_bytes(changed)
            return result
        output = self.root / "report.json"
        with mock.patch.object(headers, "_collect_headers", side_effect=change):
            status, report = self.run_cli("--output", str(output))
        self.assertEqual(2, status)
        self.assertEqual("artifact_changed", report["code"])
        self.assertFalse(output.exists())

    def test_no_candidate_body_deserialization_import_compilation_or_extraction(self):
        raw = self.write([("never_import_me.pyc", header() + b"not marshal"), ("never_import_me.py", b"raise AssertionError('must not execute')")])
        # Test setup and codecs are loaded before disabling execution helpers.
        with ExitStack() as stack:
            for owner, attribute in ((marshal, "load"), (marshal, "loads"), (zipfile.ZipFile, "extract"), (zipfile.ZipFile, "extractall")):
                stack.enter_context(mock.patch.object(owner, attribute, side_effect=AssertionError(attribute)))
            for attribute in ("compile", "eval", "exec"):
                stack.enter_context(mock.patch("builtins." + attribute, side_effect=AssertionError(attribute)))
            report = self.inspect()
        self.assertTrue(report["ok"])
        self.assertEqual([self.path], list(self.root.iterdir()))
        self.assertEqual(raw, self.path.read_bytes())

    def test_count_and_name_budgets_exact_limit_and_one_over_before_prefix_reads(self):
        names = ["é.pyc", "x.pyc"]
        self.write([(name, header()) for name in names])
        limits = {
            "MAX_BYTECODE_MEMBERS": len(names),
            "MAX_MEMBER_NAME_CHARS": max(map(len, names)),
            "MAX_MEMBER_NAME_UTF8_BYTES": max(len(name.encode("utf-8")) for name in names),
            "MAX_MEMBER_NAME_JSON_BYTES": max(len(json.dumps(name, ensure_ascii=True).encode("ascii")) for name in names),
            "MAX_TOTAL_NAME_UTF8_BYTES": sum(len(name.encode("utf-8")) for name in names),
            "MAX_TOTAL_NAME_JSON_BYTES": sum(len(json.dumps(name, ensure_ascii=True).encode("ascii")) for name in names)}
        for constant, limit in limits.items():
            with self.subTest(constant=constant):
                with mock.patch.object(headers, constant, limit):
                    self.assertEqual(names, [row["member"] for row in self.inspect()["members"]])
                with mock.patch.object(headers, constant, limit - 1), \
                        mock.patch.object(headers, "_prefix", side_effect=AssertionError("prefix reached")):
                    self.refused("bytecode_report_bounds")
                    self.assertEqual(2, mods.inspect_artifact(self.path)["member_count"])

    def test_character_bound_precedes_name_encoding_and_prefix_collection(self):
        class NoEncoding(str):
            def encode(self, *args, **kwargs):
                raise AssertionError("encoding reached")
        row = mock.Mock(orig_filename=NoEncoding("oversized.pyc"))
        row.is_dir.return_value = False
        with mock.patch.object(headers, "MAX_MEMBER_NAME_CHARS", 3):
            with self.assertRaises(FoundryError) as caught:
                headers._preflight_report_names([row])
        self.assertEqual("bytecode_report_bounds", caught.exception.code)

    def test_source_path_budget_exact_and_one_over_before_artifact_reads(self):
        self.write()
        with mock.patch.object(headers, "MAX_SOURCE_PATH_CHARS", len(str(self.path))):
            self.assertTrue(self.inspect()["ok"])
        with mock.patch.object(headers, "MAX_SOURCE_PATH_CHARS", len(str(self.path)) - 1), \
                mock.patch.object(mods, "_inspect_artifact", side_effect=AssertionError("artifact reached")):
            self.refused("bytecode_report_bounds")

    def test_actual_indented_ascii_json_including_newline_is_bounded_before_output(self):
        self.write([("é.pyc", header())])
        report = self.inspect()
        size = len(json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2).encode("ascii")) + 1
        with mock.patch.object(headers, "MAX_JSON", size):
            self.assertEqual(report, self.inspect())
            status, actual = self.run_cli()
            self.assertEqual((0, report), (status, actual))
        output = self.root / "report.json"
        with mock.patch.object(headers, "MAX_JSON", size - 1):
            self.refused("oversized_json")
            status, failure = self.run_cli("--output", str(output))
        self.assertEqual(2, status)
        self.assertEqual("oversized_json", failure["code"])
        self.assertFalse(output.exists())

    def test_cli_reports_mismatch_and_findings_with_exit_zero_no_overwrite(self):
        self.write([("a.pyc", header(4))])
        output = self.root / "report.json"
        status, report = self.run_cli("--target-magic", "00000000", "--output", str(output))
        self.assertEqual(0, status)
        self.assertEqual("mismatch", report["comparison"]["status"])
        self.assertEqual(report, json.loads(output.read_text()))
        saved = output.read_bytes()
        status, error = self.run_cli("--output", str(output))
        self.assertEqual(2, status)
        self.assertEqual("local_failure", error["code"])
        self.assertEqual(saved, output.read_bytes())
        self.assertFalse(list(self.root.glob("*.pending")))

    def test_cli_output_profile_guard_and_operational_error(self):
        self.write()
        protected = self.root / "The Sims 4" / "report.json"
        status, error = self.run_cli("--output", str(protected))
        self.assertEqual((2, "protected_path"), (status, error["code"]))
        self.assertFalse(protected.parent.exists())
        status, error = self.run_cli("--target-magic", "bad")
        self.assertEqual((2, "invalid_target_magic"), (status, error["code"]))

    def test_original_inspection_inventory_and_staging_contracts_are_unchanged(self):
        self.write()
        expected = {"name", "source", "bytes", "sha256", "kind", "validation", "member_count", "code_executed"}
        before = mods.inspect_artifact(self.path)
        inventory = mods.inventory(self.root)
        self.inspect("00000000")
        self.assertEqual(expected, set(before))
        self.assertEqual(before, mods.inspect_artifact(self.path))
        self.assertEqual(inventory, mods.inventory(self.root))
        destination = self.root.parent / (self.root.name + "-staging")
        plan = mods.stage(inventory, destination)
        self.assertEqual(1, plan["artifact_count"])
        self.assertFalse(plan["executed"])
        self.assertFalse(destination.exists())

    def test_pre_parser_metadata_bounds_and_method_refusal_still_apply(self):
        raw = self.write()
        end = raw.rfind(b"PK\x05\x06")
        central = struct.unpack_from("<I", raw, end + 16)[0]
        with mock.patch.object(mods, "MAX_SCRIPT_METADATA_BYTES", 1), \
                mock.patch.object(mods.zipfile, "ZipFile", side_effect=AssertionError("parser reached")):
            self.refused("archive_bounds")
        forged = bytearray(raw)
        struct.pack_into("<H", forged, central + 10, zipfile.ZIP_LZMA)
        self.path.write_bytes(forged)
        with mock.patch.object(mods.zipfile, "ZipFile", side_effect=AssertionError("parser reached")):
            self.refused("archive_compression")

    def test_existing_path_collision_alias_link_and_encryption_guards_apply(self):
        for rows, code in (([("../escape.pyc", header())], "archive_path"),
                           ([("a.pyc", header()), ("A.pyc", header())], "archive_duplicate")):
            with self.subTest(code=code):
                self.write(rows)
                self.refused(code)
        self.write([("badx.pyc", header())])
        self.path.write_bytes(self.path.read_bytes().replace(b"badx.pyc", b"bad\x00.pyc"))
        self.refused("archive_path")
        row = zipfile.ZipInfo("linked.pyc")
        row.create_system = 3
        row.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.write([(row, header())])
        self.refused("archive_member")
        raw = bytearray(self.write())
        central = struct.unpack_from("<I", raw, raw.rfind(b"PK\x05\x06") + 16)[0]
        struct.pack_into("<H", raw, central + 8, 1)
        self.path.write_bytes(raw)
        self.refused("archive_member")


if __name__ == "__main__":
    unittest.main()
