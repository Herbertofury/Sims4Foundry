"""Executable/helper hashes accept regular files, never streams/devices."""
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from sims4foundry_cli import io
from sims4foundry_cli.errors import FoundryError


class HashRegularInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_directory_refused_before_open(self):
        with patch.object(Path, 'open', side_effect=AssertionError('must not open')):
            with self.assertRaises(FoundryError) as error:
                io.sha256(self.root)
        self.assertEqual(error.exception.code, 'invalid_hash_file')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO fixture')
    def test_fifo_refused_before_potentially_blocking_open(self):
        path = self.root / 'input.json'
        os.mkfifo(path)
        with patch.object(Path, 'open', side_effect=AssertionError('would block')):
            with self.assertRaises(FoundryError) as error:
                io.sha256(path)
        self.assertEqual(error.exception.code, 'invalid_hash_file')

    def test_descriptor_rechecked_before_read(self):
        path = self.root / 'input.json'
        path.write_text('{"valid":true}')
        with patch.object(io.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFIFO)):
            with self.assertRaises(FoundryError) as error:
                io.sha256(path)
        self.assertEqual(error.exception.code, 'invalid_hash_file')
        self.assertEqual(path.read_text(), '{"valid":true}')

    def test_regular_file_hash_matches_exact_bytes(self):
        import hashlib
        path = self.root / 'helper.exe'
        raw = b'fixture only, never executed'
        path.write_bytes(raw)
        self.assertEqual(io.sha256(path), hashlib.sha256(raw).hexdigest())
        self.assertEqual(path.read_bytes(), raw)
