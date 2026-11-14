"""Offline directory-publication ordering fixtures, not power-loss proof."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sims4foundry_cli import mods
from sims4foundry_cli.io import new_json, sync_directory


class StagingDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        (self.source / 'nested').mkdir(parents=True)
        (self.source / 'nested' / 'mod.package').write_bytes(b'DBPF' + b'fixture' * 10)
        self.manifest = mods.inventory(self.source)
        self.dest = self.root / 'new-parent' / 'stage'

    def test_directories_flush_child_first_before_completion(self):
        events = []
        def synced(path):
            events.append(Path(path))
            sync_directory(path)
        def published(path, value):
            if Path(path).name == 'staging-complete.json':
                expected = [self.dest / 'artifacts' / 'nested', self.dest / 'artifacts',
                            self.dest, self.dest.parent, self.root]
                self.assertEqual(events, expected)
            return new_json(path, value)
        with patch.object(mods, 'sync_directory', side_effect=synced, create=True), patch.object(mods, 'new_json', side_effect=published):
            self.assertTrue(mods.stage(self.manifest, self.dest, execute=True)['staged'])

    def test_sync_failure_retains_staging_without_completion(self):
        with patch.object(mods, 'sync_directory', side_effect=OSError('flush failed'), create=True):
            with self.assertRaises(OSError):
                mods.stage(self.manifest, self.dest, execute=True)
        self.assertTrue((self.dest / 'staging-intent.json').exists())
        self.assertTrue((self.dest / 'artifacts/nested/mod.package').exists())
        self.assertFalse((self.dest / 'staging-complete.json').exists())

    def test_plan_does_not_sync_or_create(self):
        with patch.object(mods, 'sync_directory', create=True) as synced:
            self.assertFalse(mods.stage(self.manifest, self.dest)['executed'])
            synced.assert_not_called()
        self.assertFalse(self.dest.parent.exists())
