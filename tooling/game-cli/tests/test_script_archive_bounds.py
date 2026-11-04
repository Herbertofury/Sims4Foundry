"""Tiny generated ZIPs exercise limits before stdlib metadata allocation.

No imported/extracted archive members, game/profile data, or large ZIP bombs.
"""
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

from sims4foundry_cli import mods
from sims4foundry_cli.errors import FoundryError


class ScriptArchiveBoundsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "fixture.ts4script"

    @staticmethod
    def archive(count=1, comment=b"", extra=b"", method=zipfile.ZIP_STORED,
                stream=None, force_zip64=False):
        stream = io.BytesIO() if stream is None else stream
        with zipfile.ZipFile(stream, "w", compression=method) as archive:
            for number in range(count):
                row = zipfile.ZipInfo("%d.py" % number)
                row.extra, row.compress_type = extra, method
                if force_zip64:
                    with archive.open(row, "w", force_zip64=True) as member:
                        member.write(b"raise RuntimeError('must never execute')")
                else:
                    archive.writestr(row, b"raise RuntimeError('must never execute')")
            archive.comment = comment
        return stream.getvalue()

    @staticmethod
    def end(raw):
        return raw.rfind(b"PK\x05\x06")

    @classmethod
    def central(cls, raw):
        return struct.unpack_from("<I", raw, cls.end(raw) + 16)[0]

    @classmethod
    def end_field(cls, raw, offset, value, fmt="H"):
        raw = bytearray(raw)
        struct.pack_into("<" + fmt, raw, cls.end(raw) + offset, value)
        return bytes(raw)

    @classmethod
    def central_field(cls, raw, offset, value, fmt="H"):
        raw = bytearray(raw)
        struct.pack_into("<" + fmt, raw, cls.central(raw) + offset, value)
        return bytes(raw)

    def assert_refused_before_parser(self, raw, code="invalid_script_archive"):
        self.path.write_bytes(raw)
        with mock.patch.object(mods.zipfile, "ZipFile", side_effect=AssertionError("parser reached")) as parser:
            with self.assertRaises(FoundryError) as caught:
                mods.inspect_artifact(self.path)
        self.assertEqual(code, caught.exception.code)
        parser.assert_not_called()
        self.assertEqual(raw, self.path.read_bytes())

    def test_reproduce_stdlib_allocates_entries_before_caller_count_limit(self):
        # A three-entry archive and pretend caller cap of two reproduce the
        # original ordering without allocating thousands of ZipInfo instances.
        raw = self.archive(3)
        reads, created = [], []

        class TrackedStream(io.BytesIO):
            def read(self, size=-1):
                reads.append(size)
                return super().read(size)

        class TrackedInfo(zipfile.ZipInfo):
            def __init__(self, *args, **kwargs):
                created.append(args[0])
                super().__init__(*args, **kwargs)

        with mock.patch.object(zipfile, "ZipInfo", TrackedInfo):
            with zipfile.ZipFile(TrackedStream(raw)) as archive:
                self.assertEqual(3, len(created))
                self.assertGreater(len(archive.infolist()), 2)
        self.assertLess(len(raw), 512)
        self.assertIn(self.end(raw) - self.central(raw), reads)

    def test_advertised_entry_bound_precedes_zipfile(self):
        with mock.patch.object(mods, "MAX_SCRIPT_ENTRIES", 2):
            self.assert_refused_before_parser(self.archive(3), "archive_bounds")

    def test_actual_entry_bound_does_not_trust_declared_count(self):
        raw = self.end_field(self.end_field(self.archive(3), 8, 1), 10, 1)
        with mock.patch.object(mods, "MAX_SCRIPT_ENTRIES", 2):
            self.assert_refused_before_parser(raw, "archive_bounds")

    def test_actual_count_must_match_both_eocd_counts(self):
        raw = self.archive(2)
        for candidate in (self.end_field(raw, 8, 1),
                          self.end_field(self.end_field(raw, 8, 1), 10, 1),
                          self.end_field(self.end_field(raw, 8, 3), 10, 3)):
            with self.subTest(candidate=candidate[-22:]):
                self.assert_refused_before_parser(candidate)

    def test_metadata_and_entry_exact_limits_accept_one_over_refuses(self):
        raw = self.archive(2)
        self.path.write_bytes(raw)
        central_size = self.end(raw) - self.central(raw)
        with mock.patch.object(mods, "MAX_SCRIPT_ENTRIES", 2), \
                mock.patch.object(mods, "MAX_SCRIPT_METADATA_BYTES", central_size):
            self.assertEqual(2, mods.inspect_artifact(self.path)["member_count"])
        with mock.patch.object(mods, "MAX_SCRIPT_METADATA_BYTES", central_size - 1):
            self.assert_refused_before_parser(raw, "archive_bounds")

    def test_forged_large_metadata_extent_refuses_without_large_read(self):
        raw = self.end_field(self.archive(), 12, mods.MAX_SCRIPT_METADATA_BYTES + 1, "I")
        reads = []

        class TrackedStream(io.BytesIO):
            def read(self, size=-1):
                reads.append(size)
                if not 0 <= size <= len(raw):
                    raise AssertionError("unbounded read request")
                return super().read(size)

        with self.assertRaises(FoundryError) as caught:
            mods._script_archive_view(TrackedStream(raw), len(raw))
        self.assertEqual("archive_bounds", caught.exception.code)
        self.assertEqual([len(raw)], reads)

    def test_empty_archive_refuses_before_zipfile(self):
        self.assert_refused_before_parser(self.archive(0), "archive_bounds")

    def test_missing_truncated_or_inexact_end_record_refuses(self):
        raw = self.archive(comment=b"comment")
        cases = (b"not a script", raw[:self.end(raw) + 10], raw[:-1],
                 raw + b"trailer", self.end_field(raw, 20, 0), self.end_field(raw, 20, 8))
        for candidate in cases:
            with self.subTest(length=len(candidate)):
                self.assert_refused_before_parser(candidate)

    def test_embedded_end_signature_in_comment_is_not_reinterpreted(self):
        self.assert_refused_before_parser(self.archive(comment=b"comment PK\x05\x06"))
        # A complete fake last EOCD also cannot redirect to out-of-extent CD.
        false_end = struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 1, 1, 50, 0, 0)
        self.assert_refused_before_parser(self.archive(comment=false_end))

    def test_maximum_standard_comment_is_bounded_and_preserved(self):
        raw = self.archive(comment=b"c" * 65535)
        self.assertLess(len(raw), 66000)
        self.path.write_bytes(raw)
        self.assertEqual(1, mods.inspect_artifact(self.path)["member_count"])
        with self.path.open("rb") as stream:
            view = mods._script_archive_view(stream, len(raw))
            with zipfile.ZipFile(view) as archive:
                self.assertEqual(b"c" * 65535, archive.comment)
        self.assertEqual(raw, self.path.read_bytes())

    def test_disk_and_zip64_end_sentinels_refuse(self):
        raw = self.archive()
        for offset, value, fmt in ((4, 1, "H"), (6, 1, "H"), (8, 65535, "H"),
                                   (10, 65535, "H"), (12, 0xffffffff, "I"),
                                   (16, 0xffffffff, "I")):
            with self.subTest(offset=offset):
                self.assert_refused_before_parser(self.end_field(raw, offset, value, fmt))

    def test_zip64_end_records_without_sentinels_refuse(self):
        raw = self.archive()
        end, central = self.end(raw), self.central(raw)
        zip64_end = struct.pack("<4sQ2H2I4Q", b"PK\x06\x06", 44, 45, 45, 0, 0,
                                1, 1, end - central, central)
        locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, end, 1)
        self.assert_refused_before_parser(raw[:end] + zip64_end + locator + raw[end:])

    def test_coincidental_zip64_locator_signature_is_not_followed(self):
        # CPython probes exactly 20 bytes before EOCD even for a classic ZIP.
        raw = bytearray(self.archive())
        raw[self.end(raw) - 20:self.end(raw) - 16] = b"PK\x06\x07"
        self.assert_refused_before_parser(bytes(raw))

    def test_central_offset_and_member_extent_must_be_exact(self):
        raw = self.archive()
        central, end = self.central(raw), self.end(raw)
        candidates = (self.end_field(raw, 16, central - 1, "I"),
                      self.end_field(raw, 16, central + 1, "I"),
                      self.end_field(raw, 12, end - central - 1, "I"),
                      self.central_field(raw, 28, 65535),
                      self.central_field(raw, 30, 65535),
                      self.central_field(raw, 32, 65535),
                      self.central_field(raw, 42, central - 29, "I"))
        for candidate in candidates:
            with self.subTest(candidate=candidate[central:central + 46]):
                self.assert_refused_before_parser(candidate)

    def test_central_zip64_sentinels_and_split_members_refuse(self):
        raw = self.archive()
        for offset, value, fmt in ((20, 0xffffffff, "I"), (24, 0xffffffff, "I"),
                                   (42, 0xffffffff, "I"), (34, 1, "H")):
            with self.subTest(offset=offset):
                self.assert_refused_before_parser(self.central_field(raw, offset, value, fmt))

    def test_central_extra_fields_are_bounded_and_zip64_refuses(self):
        for extra in (b"x", b"xyz", struct.pack("<HH", 99, 3) + b"x",
                      struct.pack("<HH", 1, 0)):
            with self.subTest(extra=extra):
                self.assert_refused_before_parser(self.archive(extra=extra))
        self.path.write_bytes(self.archive(extra=struct.pack("<HH", 99, 3) + b"xyz"))
        self.assertEqual(1, mods.inspect_artifact(self.path)["member_count"])

    def test_non_file_central_record_refuses(self):
        raw = self.archive()
        end, central = self.end(raw), self.central(raw)
        # A ZIP central-directory digital signature is a legitimate optional
        # structure, but deliberately unsupported by this bounded subset.
        signature = struct.pack("<4sH", b"PK\x05\x05", 0)
        candidate = raw[:end] + signature + raw[end:]
        candidate = self.end_field(candidate, 12, end - central + len(signature), "I")
        self.assert_refused_before_parser(candidate)

    def test_prefixes_refuse_with_relative_or_adjusted_offsets(self):
        raw = self.archive()
        prefix = b"prefix"
        self.assert_refused_before_parser(prefix + raw)
        raw = self.central_field(raw, 42, len(prefix), "I")
        raw = self.end_field(raw, 16, self.central(raw) + len(prefix), "I")
        self.assert_refused_before_parser(prefix + raw)

    def test_stored_deflate_and_data_descriptors_remain_supported(self):
        class NonSeekable(io.BytesIO):
            def seek(self, *args):
                raise OSError("non-seekable fixture writer")

        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            for descriptor in (False, True):
                with self.subTest(method=method, descriptor=descriptor):
                    raw = self.archive(method=method, stream=NonSeekable() if descriptor else None)
                    self.path.write_bytes(raw)
                    self.assertFalse(mods.inspect_artifact(self.path)["code_executed"])
                    self.assertEqual(raw, self.path.read_bytes())

    def test_local_only_force_zip64_is_bounded_by_classic_central_metadata(self):
        # force_zip64 may produce local-only ZIP64 headers with normal classic
        # CD/EOCD. This specific case is supported, not mistaken for ZIP64 CD.
        raw = self.archive(force_zip64=True)
        self.assertEqual(0xffffffff, struct.unpack_from("<I", raw, 18)[0])
        self.assertNotIn(b"PK\x06\x06", raw)
        self.path.write_bytes(raw)
        self.assertEqual(1, mods.inspect_artifact(self.path)["member_count"])

    def test_bzip2_lzma_and_unknown_methods_refuse_before_decompressors(self):
        for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA, 99):
            with self.subTest(method=method):
                # Patch a tiny stored member instead of constructing an LZMA
                # stream or instantiating any potentially large dictionary.
                raw = self.central_field(self.archive(), 10, method)
                with mock.patch.object(zipfile, "_get_decompressor", side_effect=AssertionError("decompressor reached")):
                    self.assert_refused_before_parser(raw, "archive_compression")

    def test_invalid_utf8_is_a_structured_refusal(self):
        raw = bytearray(self.archive())
        central = self.central(raw)
        struct.pack_into("<H", raw, central + 8, 0x800)
        raw[central + 46] = 0xff
        self.path.write_bytes(raw)
        with self.assertRaises(FoundryError) as caught:
            mods.inspect_artifact(self.path)
        self.assertEqual("invalid_script_archive", caught.exception.code)

    def test_malformed_deflate_is_a_static_structured_refusal(self):
        from sims4foundry_cli.cli import main

        raw = bytearray(self.archive(method=zipfile.ZIP_DEFLATED))
        name_size, extra_size = struct.unpack_from("<HH", raw, 26)
        # BTYPE=3 is reserved/invalid in a Deflate block header.
        raw[30 + name_size + extra_size] = 0xff
        self.path.write_bytes(raw)
        with self.assertRaises(FoundryError) as caught:
            mods.inspect_artifact(self.path)
        self.assertEqual("invalid_script_archive", caught.exception.code)
        self.assertEqual("Unsupported or invalid script ZIP.", str(caught.exception))
        self.assertEqual(bytes(raw), self.path.read_bytes())
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["mods", "check", "--file", str(self.path)])
        self.assertEqual(2, result)
        self.assertEqual({"ok": False, "code": "invalid_script_archive",
                          "message": "Unsupported or invalid script ZIP."}, json.loads(output.getvalue()))

    def test_growth_and_shrink_before_preflight_refuse(self):
        raw = self.archive()
        for current in (raw + b"growth", raw[:-1]):
            with self.subTest(length=len(current)), self.assertRaises(FoundryError) as caught:
                mods._script_archive_view(io.BytesIO(current), len(raw))
            self.assertEqual("artifact_changed", caught.exception.code)

    def test_metadata_change_between_tail_and_snapshot_refuses(self):
        raw = self.archive()

        class ChangingStream(io.BytesIO):
            reads = 0

            def read(self, size=-1):
                result = super().read(size)
                self.reads += 1
                if self.reads == 1:
                    position = self.tell()
                    self.seek(len(raw) - 2)
                    self.write(b"\x01\x00")
                    self.seek(position)
                return result

        with self.assertRaises(FoundryError) as caught:
            mods._script_archive_view(ChangingStream(raw), len(raw))
        self.assertEqual("artifact_changed", caught.exception.code)

    def test_frozen_metadata_prevents_count_and_size_drift_before_constructor(self):
        raw = self.archive()
        source = io.BytesIO(raw)
        view = mods._script_archive_view(source, len(raw))
        malicious = self.end_field(raw, 12, 0xfffffffe, "I")
        malicious = self.end_field(self.end_field(malicious, 8, 4097), 10, 4097)
        malicious = self.central_field(malicious, 10, zipfile.ZIP_LZMA)
        source.seek(0)
        source.write(malicious + b"growth")
        # ZipFile must see only the original validated bytes and fixed EOF.
        with zipfile.ZipFile(view) as archive:
            self.assertEqual(["0.py"], archive.namelist())
            self.assertEqual(zipfile.ZIP_STORED, archive.infolist()[0].compress_type)
            self.assertIsNone(archive.testzip())
        view.seek(-22, os.SEEK_END)
        self.assertEqual(raw[-22:], view.read())
        self.assertEqual(b"", view.read())
        with self.assertRaises(FoundryError):
            mods._script_size(source, len(raw))

    def test_growth_after_preflight_never_accepts_artifact(self):
        raw = self.archive()
        self.path.write_bytes(raw)
        original = mods._script_archive_view

        def grow(stream, size):
            view = original(stream, size)
            with self.path.open("ab") as target:
                target.write(b"growth")
            return view

        with mock.patch.object(mods, "_script_archive_view", side_effect=grow):
            with self.assertRaises(FoundryError) as caught:
                mods.inspect_artifact(self.path)
        self.assertEqual("artifact_changed", caught.exception.code)

    @unittest.skipIf(os.name == "nt", "Windows may disallow replacing an open source handle")
    def test_path_replacement_after_preflight_does_not_reopen_for_zipfile(self):
        raw = self.archive()
        self.path.write_bytes(raw)
        replacement = self.root / "replacement"
        replacement.write_bytes(self.end_field(raw, 12, 0xfffffffe, "I"))
        original = mods._script_archive_view
        real_parser = zipfile.ZipFile
        observations = []

        def replace(stream, size):
            view = original(stream, size)
            os.replace(replacement, self.path)
            return view

        def parser(view, *args, **kwargs):
            self.assertIsInstance(view, mods._ScriptArchiveView)
            archive = real_parser(view, *args, **kwargs)
            observations.append(len(archive.infolist()))
            return archive

        with mock.patch.object(mods, "_script_archive_view", side_effect=replace), \
                mock.patch.object(zipfile, "ZipFile", side_effect=parser):
            with self.assertRaises(FoundryError) as caught:
                mods.inspect_artifact(self.path)
        self.assertEqual([1], observations)
        self.assertEqual("artifact_changed", caught.exception.code)

    def test_view_bounds_seeks_read_sizes_and_short_source_reads(self):
        raw = self.archive()
        source = io.BytesIO(raw)
        view = mods._script_archive_view(source, len(raw))
        for offset, whence in ((-1, os.SEEK_SET), (1, os.SEEK_END)):
            with self.subTest(offset=offset), self.assertRaises(zipfile.BadZipFile):
                view.seek(offset, whence)
        with self.assertRaises(ValueError):
            view.seek(0, 99)
        # A direct unexpected read-to-EOF is refused before touching payload.
        fake = mods._ScriptArchiveView(source, 100000, 99999, b"x")
        with mock.patch.object(mods, "MAX_SCRIPT_METADATA_BYTES", 1), \
                mock.patch.object(mods, "READ_CHUNK_BYTES", 7):
            with self.assertRaises(FoundryError) as caught:
                fake.read()
        self.assertEqual("archive_bounds", caught.exception.code)
        source.truncate(0)
        with self.assertRaises(FoundryError) as caught:
            view.read(1)
        self.assertEqual("artifact_changed", caught.exception.code)

    def test_short_positive_metadata_reads_and_chunk_caps(self):
        raw = self.archive(comment=b"comment", extra=struct.pack("<HH", 99, 3) + b"xyz")
        requests = []

        class ShortStream(io.BytesIO):
            def read(self, size=-1):
                requests.append(size)
                if not 0 <= size <= 7:
                    raise AssertionError("metadata/source read exceeded patched chunk cap")
                return super().read(min(size, 3))

        with mock.patch.object(mods, "READ_CHUNK_BYTES", 7):
            view = mods._script_archive_view(ShortStream(raw), len(raw))
            with zipfile.ZipFile(view) as archive:
                self.assertIsNone(archive.testzip())
        self.assertGreater(len(requests), 10)


if __name__ == "__main__":
    unittest.main()
