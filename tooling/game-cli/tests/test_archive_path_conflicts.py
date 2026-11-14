"""CRC-valid offline archives; no extraction or execution."""
from pathlib import Path
import tempfile
import unittest
import zipfile
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.mods import inspect_artifact


class ArchivePathConflictTests(unittest.TestCase):
    def inspect(self, names):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.ts4script'
            with zipfile.ZipFile(path, 'w') as archive:
                for name in names:
                    archive.writestr(name, b'' if name.endswith('/') else b'fixture')
            original = path.read_bytes()
            try:
                return inspect_artifact(path)
            finally:
                self.assertEqual(path.read_bytes(), original)

    def test_reject_file_directory_conflicts_in_either_order(self):
        for names in [('mod', 'mod/a.py'), ('MOD', 'mod/'), ('mod/a.py', 'MOD'), ('mod/', 'MOD')]:
            with self.subTest(names=names):
                with self.assertRaises(FoundryError) as error:
                    self.inspect(names)
                self.assertEqual(error.exception.code, 'archive_path_conflict')

    def test_reject_repeated_directory_separators(self):
        with self.assertRaises(FoundryError) as error:
            self.inspect(['mod//'])
        self.assertEqual(error.exception.code, 'archive_path')

    def test_accept_explicit_directory_and_child_in_either_order(self):
        for names in [('mod/', 'mod/a.py'), ('mod/a.py', 'mod/'), ('a.py', 'b.py')]:
            with self.subTest(names=names):
                self.assertEqual(self.inspect(names)['member_count'], 2)
