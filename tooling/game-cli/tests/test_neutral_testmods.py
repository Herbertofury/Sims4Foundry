import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sims4foundry_cli import mods, neutral_testmods
from sims4foundry_cli.errors import FoundryError


class TestModWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / 'The Sims 4'
        self.mods = self.profile / 'Mods'
        self.mods.mkdir(parents=True)
        self.source = self.root / 'input'
        self.source.mkdir()
        (self.source / 'candidate.package').write_bytes(b'DBPF candidate fixture')
        (self.mods / 'original.package').write_bytes(b'DBPF original fixture')
        self.manifest = mods.inventory(self.source)
        self.session = self.root / 'session'
        self.check_calls = 0

    def check(self):
        self.check_calls += 1

    def install(self):
        return neutral_testmods.install(self.manifest, self.profile, self.session,
                                        process_check=self.check)

    def test_default_game_check_stops_on_candidate(self):
        with patch.object(neutral_testmods.discovery, '_snapshot_image_pids', return_value=[123]) as observe:
            with self.assertRaises(FoundryError) as caught:
                neutral_testmods._no_game_candidates()
        self.assertEqual('game_running', caught.exception.code)
        self.assertEqual(1, observe.call_count)

    def test_install_then_retire_preserves_original_and_test_bytes(self):
        result = self.install()
        target = self.mods / 'FoundryTest' / 'candidate.package'
        self.assertTrue(result['ok'])
        self.assertEqual('install_recorded', neutral_testmods.status(self.session)['state'])
        self.assertEqual(b'DBPF candidate fixture', target.read_bytes())
        self.assertEqual(b'DBPF original fixture', (self.mods / 'original.package').read_bytes())
        self.assertEqual(3, self.check_calls)
        retired = neutral_testmods.retire(self.session, process_check=self.check)
        self.assertTrue(retired['ok'])
        self.assertFalse((self.mods / 'FoundryTest').exists())
        self.assertEqual(b'DBPF candidate fixture', (Path(retired['recovered']) / 'candidate.package').read_bytes())
        self.assertEqual(b'DBPF original fixture', (self.mods / 'original.package').read_bytes())
        self.assertFalse(retired['deleted'])
        self.assertEqual('retired_recorded', neutral_testmods.status(self.session)['state'])

    def test_duplicate_existing_filename_refuses_without_writes(self):
        nested = self.mods / 'Existing'
        nested.mkdir()
        (nested / 'CANDIDATE.package').write_bytes(b'DBPF existing')
        with self.assertRaises(FoundryError) as caught:
            self.install()
        self.assertEqual('duplicate_mod_name', caught.exception.code)
        self.assertFalse(self.session.exists())
        self.assertFalse((self.mods / 'FoundryTest').exists())

    def test_changed_or_extra_file_refuses_retirement(self):
        self.install()
        target = self.mods / 'FoundryTest' / 'candidate.package'
        target.write_bytes(b'DBPF changed by user')
        with self.assertRaises(FoundryError):
            neutral_testmods.retire(self.session, process_check=self.check)
        self.assertEqual(b'DBPF changed by user', target.read_bytes())
        self.assertFalse((self.session / 'retire-intent.json').exists())

    def test_stale_source_and_running_game_refuse(self):
        (self.source / 'candidate.package').write_bytes(b'DBPF changed source')
        with self.assertRaises(FoundryError):
            self.install()
        self.assertFalse(self.session.exists())
        (self.source / 'candidate.package').write_bytes(b'DBPF candidate fixture')
        def running():
            raise FoundryError('game_running', 'fixture')
        with self.assertRaises(FoundryError):
            neutral_testmods.install(self.manifest, self.profile, self.session,
                                     process_check=running)
        self.assertFalse(self.session.exists())

    def test_completion_receipt_failure_reports_uncertain_install(self):
        real = neutral_testmods.new_json
        def output(path, value):
            if path.name == 'install-complete.json':
                raise OSError('fixture')
            return real(path, value)
        with patch.object(neutral_testmods, 'new_json', side_effect=output):
            result = self.install()
        self.assertEqual('unresolved', result['outcome'])
        self.assertTrue(result['installed'])
        self.assertTrue((self.mods / 'FoundryTest' / 'candidate.package').is_file())
        self.assertTrue((self.session / 'install-intent.json').is_file())
        self.assertEqual('installation_unresolved', neutral_testmods.status(self.session)['state'])

    def test_new_conflict_before_publish_does_not_replace_user_file(self):
        def racing_check():
            self.check_calls += 1
            if self.check_calls == 2:
                (self.mods / 'CANDIDATE.package').write_bytes(b'DBPF user file')
        with self.assertRaises(FoundryError):
            neutral_testmods.install(self.manifest, self.profile, self.session,
                                     process_check=racing_check)
        self.assertEqual(b'DBPF user file', (self.mods / 'CANDIDATE.package').read_bytes())
        self.assertFalse((self.mods / 'FoundryTest').exists())
        self.assertEqual('installation_unresolved', neutral_testmods.status(self.session)['state'])

    def test_change_during_retirement_preserves_edit(self):
        self.install()
        candidate = self.mods / 'FoundryTest' / 'candidate.package'
        count = [0]
        def racing_check():
            count[0] += 1
            if count[0] == 2:
                candidate.write_bytes(b'DBPF user edit')
        with self.assertRaises(FoundryError):
            neutral_testmods.retire(self.session, process_check=racing_check)
        self.assertEqual(b'DBPF user edit', candidate.read_bytes())
        self.assertEqual('retirement_unresolved', neutral_testmods.status(self.session)['state'])

    def test_lost_install_rename_ack_does_not_replay(self):
        original = neutral_testmods.os.rename
        def move_then_fail(source, destination):
            original(source, destination)
            raise OSError('fixture lost acknowledgment')
        with patch.object(neutral_testmods.os, 'rename', side_effect=move_then_fail):
            result = self.install()
        self.assertEqual('unresolved', result['outcome'])
        state = neutral_testmods.status(self.session)
        self.assertEqual('installation_unresolved', state['state'])
        self.assertTrue(state['target_present'])
        self.assertFalse(state['staging_present'])
        self.assertEqual(b'DBPF candidate fixture',
                         (self.mods / 'FoundryTest' / 'candidate.package').read_bytes())

    def test_lost_retire_rename_ack_keeps_recovery(self):
        installed = self.install()
        original = neutral_testmods.os.rename
        def move_then_fail(source, destination):
            original(source, destination)
            raise OSError('fixture lost acknowledgment')
        with patch.object(neutral_testmods.os, 'rename', side_effect=move_then_fail):
            result = neutral_testmods.retire(self.session, process_check=self.check)
        self.assertEqual('unresolved', result['outcome'])
        state = neutral_testmods.status(self.session)
        self.assertEqual('retirement_unresolved', state['state'])
        self.assertFalse(state['target_present'])
        self.assertTrue(state['recovery_present'])
        self.assertEqual(b'DBPF candidate fixture',
                         (Path(state['recovery']) / 'candidate.package').read_bytes())

    def test_mismatched_intent_refuses_retirement(self):
        self.install()
        intent = self.session / 'install-intent.json'
        intent.write_text('{}')
        with self.assertRaises(FoundryError):
            neutral_testmods.retire(self.session, process_check=self.check)
        self.assertTrue((self.mods / 'FoundryTest' / 'candidate.package').is_file())


if __name__ == '__main__':
    unittest.main()
