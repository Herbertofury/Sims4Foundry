"""Bounded JSON accepts regular files, never streams/devices."""
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from sims4foundry_cli import io
from sims4foundry_cli.errors import FoundryError


class JsonRegularInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_directory_refused_before_open(self):
        with patch.object(Path, 'open', side_effect=AssertionError('must not open')):
            with self.assertRaises(FoundryError) as error:
                io.read_json(self.root)
        self.assertEqual(error.exception.code, 'invalid_json_file')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO fixture')
    def test_fifo_refused_before_potentially_blocking_open(self):
        path = self.root / 'input.json'
        os.mkfifo(path)
        with patch.object(Path, 'open', side_effect=AssertionError('would block')):
            with self.assertRaises(FoundryError) as error:
                io.read_json(path)
        self.assertEqual(error.exception.code, 'invalid_json_file')

    def test_descriptor_rechecked_before_read(self):
        path = self.root / 'input.json'
        path.write_text('{"valid":true}')
        with patch.object(io.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFIFO)):
            with self.assertRaises(FoundryError) as error:
                io.read_json(path)
        self.assertEqual(error.exception.code, 'invalid_json_file')
        self.assertEqual(path.read_text(), '{"valid":true}')

    def test_regular_file_still_uses_strict_json(self):
        path = self.root / 'input.json'
        path.write_text('{"valid":true}')
        self.assertEqual(io.read_json(path), {'valid': True})
        path.write_text('{"valid":true,"valid":false}')
        with self.assertRaises(FoundryError) as error:
            io.read_json(path)
        self.assertEqual(error.exception.code, 'invalid_json')
