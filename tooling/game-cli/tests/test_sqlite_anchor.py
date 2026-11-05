"""Real SQLite anchor fixtures; no game, profile, registry or native input."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.session_registry import SessionRegistry
from sims4foundry_cli.sqlite_anchor import SQLiteAnchor
from test_guarded_input import identity


class SQLiteAnchorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'anchor.sqlite'
        self.anchor = SQLiteAnchor.create(self.path)
    def test_compare_and_swap_persists_and_rejects_stale_head(self):
        self.assertIsNone(self.anchor.read('a'*64))
        self.assertTrue(self.anchor.compare_and_swap('a'*64, None, 'b'*64))
        self.assertFalse(SQLiteAnchor(self.path).compare_and_swap('a'*64, None, 'c'*64))
        self.assertEqual('b'*64, SQLiteAnchor(self.path).read('a'*64))
        self.assertTrue(self.anchor.compare_and_swap('a'*64, 'b'*64, 'c'*64))
        self.assertEqual('c'*64, self.anchor.read('a'*64))
    def test_second_process_observes_and_advances_same_anchor(self):
        self.assertTrue(self.anchor.compare_and_swap('a'*64, None, 'b'*64))
        script = ('import sys; from sims4foundry_cli.sqlite_anchor import SQLiteAnchor; '
                  'a=SQLiteAnchor(sys.argv[1]); '
                  'assert a.read("a"*64)=="b"*64; '
                  'assert a.compare_and_swap("a"*64,"b"*64,"c"*64)')
        result = subprocess.run([sys.executable, '-c', script, str(self.path)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors='replace'))
        self.assertEqual('c'*64, self.anchor.read('a'*64))
    def test_registry_reopens_and_detects_removed_chain_tail(self):
        root = self.root/'registry'
        root.mkdir()
        registry = SessionRegistry(root, identity(), b'k'*32, self.anchor)
        registry.open_session('1'*32, 'a'*64)
        registry.begin('2'*32, 'input', 'b'*64)
        other = SessionRegistry(root, identity(), b'k'*32, SQLiteAnchor(self.path))
        self.assertEqual('2'*32, other.inspect()['events'][-1]['request_id'])
        next(root.glob('*/record-000001.json')).unlink()
        with self.assertRaises(FoundryError): other.inspect()
    def test_existing_or_missing_database_is_not_recreated(self):
        with self.assertRaises(FoundryError): SQLiteAnchor.create(self.path)
        self.path.unlink()
        with self.assertRaises(FoundryError): self.anchor.read('a'*64)
        self.assertFalse(self.path.exists())


if __name__ == '__main__': unittest.main()
