"""Tiny fabricated sources only: no EA/game access, launches or native senders."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sims4foundry_cli import launch
from sims4foundry_cli.errors import FoundryError


class LaunchAdversarialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / "installation"
        (self.game / "Game" / "Bin").mkdir(parents=True)
        (self.game / "__Installer").mkdir()
        self.exe = self.game / "Game" / "Bin" / "TS4_x64.exe"
        self.launcher = self.exe.with_name("TS4_Launcher_x64.exe")
        self.manifest = self.game / "__Installer" / "installerdata.xml"
        self.exe.write_bytes(b"fabricated-executable-bytes")
        self.launcher.write_bytes(b"fabricated-launcher-bytes")
        self.manifest.write_bytes(b'<root><contentIDs><contentID>123</contentID></contentIDs></root>')
        self.expected = hashlib.sha256(self.exe.read_bytes()).hexdigest()
        self.log = self.root / "fabricated.log"
        self.success = b'Successful launch. offerKey.offerId=[fixture-offer] slug=[the-sims-4]'
        self.request = (f'Processing launch request: requestSource[Client] offerId[fixture-offer] '
                        f'contentId[123] exe[{self.launcher}]').encode()
        self.log.write_bytes(self.success + b"\n" + self.request + b"\n")

    def prepare(self, logs=None):
        return launch.prepare(self.game, [self.log] if logs is None else logs, self.expected)

    def assert_refused(self, code, logs=None):
        with self.assertRaises(FoundryError) as caught:
            self.prepare(logs)
        self.assertEqual(code, caught.exception.code)

    def wrap_reads(self, target, callback, *, short=False):
        """Change a tiny source during a real unbuffered read; account for bytes."""
        fdopen = os.fdopen
        target_stat = target.stat()
        reads = []
        class Reader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def read(self, count):
                if count < 0:
                    raise AssertionError("Unbounded read")
                reads.append(count)
                callback(self.stream, len(reads))
                return self.stream.read(min(count, 3) if short else count)
        def opened(descriptor, *args, **kwargs):
            stream = fdopen(descriptor, *args, **kwargs)
            actual = os.fstat(descriptor)
            if (actual.st_dev, actual.st_ino) == (target_stat.st_dev, target_stat.st_ino):
                return Reader(stream)
            return stream
        return mock.patch.object(launch.os, "fdopen", opened), reads

    def test_all_installation_bounds_precede_any_open(self):
        cases = ((self.exe, "MAX_BINARY_BYTES", "executable_bound"),
                 (self.launcher, "MAX_BINARY_BYTES", "launcher_bound"),
                 (self.manifest, "MAX_MANIFEST_BYTES", "manifest_bound"))
        for target, constant, code in cases:
            with self.subTest(source=target.name):
                if target == self.launcher:
                    self.exe.write_bytes(b"x")
                with mock.patch.object(launch, constant, target.stat().st_size - 1), \
                        mock.patch.object(launch.os, "open", side_effect=AssertionError("Read before bounds")):
                    self.assert_refused(code)

    def test_empty_manifest_rejected_before_any_open(self):
        self.manifest.write_bytes(b"")
        with mock.patch.object(launch.os, "open", side_effect=AssertionError("Read before bounds")):
            self.assert_refused("manifest_bound")

    def test_log_collection_limit_precedes_any_open(self):
        with mock.patch.object(launch.os, "open", side_effect=AssertionError("Read before validation")):
            for logs in ([], [self.log] * 3, str(self.log)):
                self.assert_refused("launch_evidence_required", logs)

    def test_nonregular_log_rejected_before_any_open(self):
        with mock.patch.object(launch.os, "open", side_effect=AssertionError("Read before validation")):
            self.assert_refused("invalid_launch_log", [self.root])

    def test_growing_sources_are_bounded_to_original_extent_plus_probe(self):
        for target in (self.exe, self.launcher, self.manifest, self.log):
            with self.subTest(source=target.name):
                original = target.read_bytes()
                def grow(stream, call):
                    if call == 1:
                        with target.open("ab") as output:
                            output.write(b"growth")
                patched, reads = self.wrap_reads(target, grow)
                with patched:
                    self.assert_refused("log_changed" if target == self.log else "installation_changed")
                self.assertEqual([len(original), 1], reads)
                target.write_bytes(original)

    def test_shrinking_sources_are_rejected_even_below_read_cap(self):
        for target in (self.exe, self.launcher, self.manifest, self.log):
            with self.subTest(source=target.name):
                original = target.read_bytes()
                def shrink(stream, call):
                    if call == 1:
                        target.write_bytes(original[:-1])
                patched, reads = self.wrap_reads(target, shrink)
                with patched:
                    self.assert_refused("log_changed" if target == self.log else "installation_changed")
                self.assertEqual([len(original), 1], reads)
                target.write_bytes(original)

    def test_same_size_edit_is_rejected_even_if_expected_hash_still_matches(self):
        original_stat = self.exe.stat()
        def edit_after_read(stream, call):
            if call == 2:  # EOF probe, after the original expected bytes were read.
                self.exe.write_bytes(b"z" * original_stat.st_size)
                os.utime(self.exe, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1000000))
        patched, _ = self.wrap_reads(self.exe, edit_after_read)
        with patched:
            self.assert_refused("installation_changed")

    def test_same_size_log_edit_is_rejected(self):
        original = self.log.read_bytes()
        before = self.log.stat()
        def edit(stream, call):
            if call == 1:
                self.log.write_bytes(original.replace(b"fixture-offer", b"altered-offer"))
                os.utime(self.log, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000))
        patched, _ = self.wrap_reads(self.log, edit)
        with patched:
            self.assert_refused("log_changed")

    def test_final_hash_recheck_detects_log_drift_with_unchanged_stat(self):
        snapshot = launch._snapshot
        edited = False
        def reading(path, *args, **kwargs):
            nonlocal edited
            result = snapshot(path, *args, **kwargs)
            if path == self.log and not edited:
                original = self.log.stat()
                self.log.write_bytes(self.log.read_bytes().replace(b"fixture-offer", b"altered-offer"))
                os.utime(self.log, ns=(original.st_atime_ns, original.st_mtime_ns))
                edited = True
            return result
        # Fabricate a filesystem with no ctime visibility; SHA-256 still detects drift.
        stamp = launch._stamp
        with mock.patch.object(launch, "_stamp", side_effect=lambda info: dict(stamp(info), ctime_ns=0)), \
                mock.patch.object(launch, "_snapshot", side_effect=reading):
            self.assert_refused("log_changed")

    def test_short_reads_are_supported_with_exact_source_hashes(self):
        patched, _ = self.wrap_reads(self.manifest, lambda *_: None, short=True)
        with patched:
            result = self.prepare()
        self.assertEqual(hashlib.sha256(self.manifest.read_bytes()).hexdigest(), result["manifest_sha256"])

    def test_raw_request_and_success_hashes_preserve_utf8_bytes(self):
        success = "préfix ".encode() + self.success
        request = b"\xef\xbb\xbf" + self.request + " suffix é".encode()
        self.log.write_bytes(success + b"\r\n" + request + b"\r\n")
        selected = self.prepare()["account_launch"]
        self.assertEqual(hashlib.sha256(request).hexdigest(), selected["record_sha256"])
        self.assertEqual(hashlib.sha256(success).hexdigest(), selected["success_record_sha256"])

    def test_invalid_utf8_cannot_be_selected_as_either_record(self):
        for invalid_success, invalid_request in ((True, False), (False, True)):
            with self.subTest(success=invalid_success):
                self.log.write_bytes(self.success + (b"\xff" if invalid_success else b"") + b"\n"
                                     + self.request + (b"\xff" if invalid_request else b"") + b"\n")
                self.assert_refused("missing_successful_play")

    def test_unrelated_invalid_utf8_does_not_block_valid_evidence(self):
        self.log.write_bytes(b"unrelated \xff\n" + self.log.read_bytes())
        self.assertTrue(self.prepare()["ok"])

    def test_invalid_lines_keep_their_positions_in_predecessor_window(self):
        self.log.write_bytes(self.success + b"\n" + b"\xff\n" * 30 + self.request + b"\n")
        self.assert_refused("missing_successful_play")

    def test_large_log_tail_discards_only_split_leading_line(self):
        records = self.log.read_bytes()
        for prefix, limit in ((b"\xc3\xa9partial\n", len(records) + len(b"\xa9partial\n")),
                              (b"ignored\n", len(records)),
                              (b"ignored\r", len(records)),
                              (b"ignored\r", len(records) + 1)):
            with self.subTest(prefix=prefix, limit=limit):
                self.log.write_bytes(prefix + records)
                with mock.patch.object(launch, "MAX_LOG_TAIL_BYTES", limit):
                    result = self.prepare()
                self.assertEqual("123", result["account_launch"]["content_id"])
                self.assertEqual(hashlib.sha256((prefix + records)[-limit:]).hexdigest(), result["log_tail_sha256"][0])

    def test_cut_leading_success_fragment_is_not_reused(self):
        self.log.write_bytes(b"not-a-complete-line " + self.success + b"\n" + self.request + b"\n")
        with mock.patch.object(launch, "MAX_LOG_TAIL_BYTES", len(self.success + b"\n" + self.request + b"\n")):
            self.assert_refused("missing_successful_play")

    def test_blank_line_flood_uses_lazy_records_and_fixed_window(self):
        raw = self.success + b"\n" + b"\n" * 10000 + self.request + b"\n"
        records = launch._log_records(raw)
        self.assertIs(iter(records), records)
        self.log.write_bytes(raw)
        self.assert_refused("missing_successful_play")

    def test_latest_matching_successful_request_remains_selected(self):
        self.manifest.write_bytes(b'<root><contentIDs><contentID>123</contentID><contentID>456</contentID></contentIDs></root>')
        next_request = self.request.replace(b"contentId[123]", b"contentId[456]")
        self.log.write_bytes(self.log.read_bytes() + self.success + b"\n" + next_request + b"\n")
        result = self.prepare()
        self.assertEqual("456", result["account_launch"]["content_id"])
        self.assertEqual(hashlib.sha256(next_request).hexdigest(), result["account_launch"]["record_sha256"])

    def test_windows_cross_api_ctime_and_same_api_drift(self):
        expected = dict(launch._stamp(self.exe.stat()), birthtime_ns=123)
        descriptor = dict(expected, ctime_ns=expected["ctime_ns"] + 1)
        with mock.patch.object(launch, "_WINDOWS", True):
            self.assertTrue(launch._same_open_source(expected, descriptor))
            for key in expected:
                if key != "ctime_ns":
                    self.assertFalse(launch._same_open_source(expected, dict(descriptor, **{key: descriptor[key] + 1})), key)
            self.assertFalse(launch._same_open_source(dict(expected, birthtime_ns=None), dict(descriptor, birthtime_ns=None)))
        self.assertNotEqual(expected, descriptor)

    def test_descriptor_is_closed_if_wrapper_construction_fails(self):
        opened = []
        os_open = os.open
        def opening(*args, **kwargs):
            descriptor = os_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor
        with mock.patch.object(launch.os, "open", side_effect=opening), \
                mock.patch.object(launch.os, "fdopen", side_effect=ValueError("fabricated failure")):
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertEqual(1, len(opened))
        with self.assertRaises(OSError):
            os.fstat(opened[0])
