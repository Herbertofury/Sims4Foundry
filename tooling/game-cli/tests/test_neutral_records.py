"""Owned regular files and special-file refusals; no game/profile operations."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sims4foundry_cli import companion_build as reader
from sims4foundry_cli import neutral_records as records
from sims4foundry_cli.errors import FoundryError


class NeutralRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'record.json'

    def test_regular_strict_json_without_execution(self):
        self.path.write_bytes(b'{"state":"fixture","value":42}')
        with mock.patch.object(reader, '_run_compiler', side_effect=AssertionError('No compiler')):
            self.assertEqual({'state': 'fixture', 'value': 42}, records.read_json(self.path))

    def test_duplicates_nonfinite_truncated_and_wrong_encoding_refuse(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{', b'\xff'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(FoundryError):
                    records.read_json(self.path)

    def test_extent_bound_before_open(self):
        self.path.write_bytes(b' ' * 65)
        with mock.patch.object(records, 'MAX_JSON', 64), mock.patch.object(reader.os, 'open', side_effect=AssertionError('No oversized open')):
            with self.assertRaises(FoundryError) as caught:
                records.read_json(self.path)
        self.assertEqual('build_input_bounds', caught.exception.code)

    def test_missing_record_does_not_create_or_retry(self):
        with self.assertRaises(FileNotFoundError):
            records.read_json(self.path)
        self.assertEqual([], list(self.root.iterdir()))

    def test_directory_refused_before_open(self):
        self.path.mkdir()
        with mock.patch.object(reader.os, 'open', side_effect=AssertionError('No directory open')):
            with self.assertRaises(FoundryError):
                records.read_json(self.path)

    def test_symlink_and_hardlink_refused(self):
        target = self.root / 'target.json'
        target.write_bytes(b'{}')
        try:
            self.path.symlink_to(target)
        except OSError:
            self.skipTest('Host cannot create symlinks')
        with self.assertRaises(FoundryError):
            records.read_json(self.path)
        self.path.unlink()
        os.link(target, self.path)
        with self.assertRaises(FoundryError):
            records.read_json(self.path)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX owned FIFO fixture')
    def test_fifo_refuses_without_open_or_writer(self):
        os.mkfifo(self.path)
        with mock.patch.object(reader.os, 'open', side_effect=AssertionError('No FIFO open')):
            with self.assertRaises(FoundryError) as caught:
                records.read_json(self.path)
        self.assertEqual('build_input_type', caught.exception.code)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX owned FIFO swap fixture')
    def test_file_swapped_to_fifo_before_open_cannot_block(self):
        self.path.write_bytes(b'{}')
        real_open = os.open
        calls = []
        def open_swapped(path, flags, *args, **kwargs):
            calls.append(flags)
            self.path.unlink()
            os.mkfifo(self.path)
            self.assertTrue(flags & os.O_NONBLOCK)
            return real_open(path, flags, *args, **kwargs)
        with mock.patch.object(reader.os, 'open', side_effect=open_swapped):
            with self.assertRaises(FoundryError):
                records.read_json(self.path)
        self.assertEqual(1, len(calls))

    def test_access_denied_propagates_without_fallback(self):
        self.path.write_bytes(b'{}')
        with mock.patch.object(reader.os, 'open', side_effect=PermissionError('fixture')) as opening:
            with self.assertRaises(FoundryError) as caught:
                records.read_json(self.path)
        self.assertEqual('build_input_read', caught.exception.code)
        self.assertEqual(1, opening.call_count)


if __name__ == '__main__':
    unittest.main()
