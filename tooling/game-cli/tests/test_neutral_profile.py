import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sims4foundry_cli import discovery, neutral_profile
from sims4foundry_cli.errors import FoundryError


class ProfileTests(unittest.TestCase):
    def setUp(self):
        platform = patch.object(neutral_profile, '_WINDOWS', True)
        platform.start()
        self.addCleanup(platform.stop)
        def no_replace(source, destination):
            if os.path.lexists(destination):
                raise FileExistsError(str(destination))
            os.rename(source, destination)
        rename = patch.object(neutral_profile, '_rename_directory', side_effect=no_replace)
        rename.start()
        self.addCleanup(rename.stop)
        native = patch.object(discovery, '_snapshot_image_pids',
                              side_effect=AssertionError('No native discovery in profile fixtures'))
        native.start()
        self.addCleanup(native.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / 'The Sims 4'
        self.profile.mkdir()
        (self.profile / 'save.dat').write_bytes(b'original user save')
        (self.profile / 'Options.ini').write_bytes(b'[options]\\nfixture = 1\\n')
        self.session = self.root / 'session'
        self.checks = 0

    def closed(self):
        self.checks += 1

    def activate(self):
        return neutral_profile.activate(self.profile, self.session, process_check=self.closed)

    def test_activate_restore_preserves_original_and_test_changes(self):
        result = self.activate()
        self.assertTrue(result['ok'])
        self.assertTrue(Path(result['original_preserved']).is_dir())
        state = neutral_profile.status(self.session)
        self.assertEqual('activation_recorded', state['state'])
        self.assertTrue(state['test_at_profile'])
        self.assertTrue(state['original_at_backup'])
        self.assertTrue(state['test_at_profile'])
        self.assertEqual(b'[options]\\nfixture = 1\\n', (self.profile / 'Options.ini').read_bytes())
        self.assertIn(b'PackedFile */*.package', (self.profile / 'Mods' / 'Resource.cfg').read_bytes())
        (self.profile / 'new-save.dat').write_bytes(b'test session save')
        restored = neutral_profile.restore(self.session, process_check=self.closed)
        self.assertTrue(restored['restored'])
        self.assertEqual(b'original user save', (self.profile / 'save.dat').read_bytes())
        self.assertEqual(b'test session save', (Path(restored['test_profile_preserved']) / 'new-save.dat').read_bytes())
        self.assertFalse(restored['deleted'])
        self.assertEqual('restored_recorded', neutral_profile.status(self.session)['state'])

    def test_prepare_failure_leaves_original_and_staging_intact(self):
        original_write = neutral_profile.new_json
        def fail_prepared(path, value):
            if path.name == 'activation-prepared.json':
                raise OSError('fixture')
            return original_write(path, value)
        with patch.object(neutral_profile, 'new_json', side_effect=fail_prepared):
            with self.assertRaises(OSError):
                self.activate()
        self.assertEqual(b'original user save', (self.profile / 'save.dat').read_bytes())
        state = neutral_profile.status(self.session)
        self.assertEqual('activation_unresolved', state['state'])
        self.assertTrue(state['original_at_profile'])
        recovered = neutral_profile.recover_activation(self.session, process_check=self.closed)
        self.assertTrue(recovered['activation_aborted'])
        self.assertTrue(Path(recovered['test_staging_preserved']).is_dir())

    def test_lost_second_activation_ack_preserves_test_and_original(self):
        original = neutral_profile._rename_directory
        calls = [0]
        def second_move_then_fail(source, destination):
            calls[0] += 1
            original(source, destination)
            if calls[0] == 2:
                raise OSError('lost acknowledgment')
        with patch.object(neutral_profile, '_rename_directory', side_effect=second_move_then_fail):
            result = self.activate()
        self.assertEqual('unresolved', result['outcome'])
        state = neutral_profile.status(self.session)
        self.assertTrue(state['original_at_backup'])
        self.assertTrue(state['test_at_profile'])
        recovered = neutral_profile.recover_activation(self.session, process_check=self.closed)
        self.assertTrue(recovered['activation_aborted'])
        self.assertEqual(b'original user save', (self.profile / 'save.dat').read_bytes())
        self.assertTrue(Path(recovered['test_profile_preserved']).is_dir())

    def test_marker_change_refuses_restoration(self):
        self.activate()
        marker = self.profile / '.foundry-test-profile.json'
        marker.write_text('{}')
        with self.assertRaises(FoundryError):
            neutral_profile.restore(self.session, process_check=self.closed)
        self.assertTrue(marker.exists())
        self.assertFalse((self.session / 'restore-intent.json').exists())

    def test_lost_activation_move_ack_is_recovered_without_data_loss(self):
        original = neutral_profile._rename_directory
        def move_then_fail(source, destination):
            original(source, destination)
            raise OSError('lost acknowledgment')
        with patch.object(neutral_profile, '_rename_directory', side_effect=move_then_fail):
            result = self.activate()
        self.assertEqual('unresolved', result['outcome'])
        state = neutral_profile.status(self.session)
        self.assertTrue(state['original_at_backup'])
        self.assertFalse(self.profile.exists())
        result = neutral_profile.recover_activation(self.session, process_check=self.closed)
        self.assertTrue(result['activation_aborted'])
        self.assertEqual(b'original user save', (self.profile / 'save.dat').read_bytes())
        self.assertEqual('activation_aborted', neutral_profile.status(self.session)['state'])

    def test_lost_first_restore_move_ack_can_resume(self):
        self.activate()
        (self.profile / 'new-save.dat').write_bytes(b'test session save')
        original = neutral_profile._rename_directory
        def move_then_fail(source, destination):
            original(source, destination)
            raise OSError('lost acknowledgment')
        with patch.object(neutral_profile, '_rename_directory', side_effect=move_then_fail):
            result = neutral_profile.restore(self.session, process_check=self.closed)
        self.assertEqual('unresolved', result['outcome'])
        state = neutral_profile.status(self.session)
        self.assertTrue(state['original_at_backup'])
        self.assertTrue(state['test_at_recovery'])
        recovered = neutral_profile.recover(self.session, process_check=self.closed)
        self.assertTrue(recovered['restored'])
        self.assertEqual(b'original user save', (self.profile / 'save.dat').read_bytes())
        self.assertEqual(b'test session save', (Path(recovered['test_profile_preserved']) / 'new-save.dat').read_bytes())

    def test_lost_second_restore_move_ack_only_records_completion(self):
        self.activate()
        original = neutral_profile._rename_directory
        count = [0]
        def second_move_then_fail(source, destination):
            count[0] += 1
            original(source, destination)
            if count[0] == 2:
                raise OSError('lost acknowledgment')
        with patch.object(neutral_profile, '_rename_directory', side_effect=second_move_then_fail):
            result = neutral_profile.restore(self.session, process_check=self.closed)
        self.assertEqual('unresolved', result['outcome'])
        state = neutral_profile.status(self.session)
        self.assertTrue(state['original_at_profile'])
        self.assertTrue(state['test_at_recovery'])
        self.assertFalse(state['original_at_backup'])
        recovered = neutral_profile.recover(self.session, process_check=self.closed)
        self.assertTrue(recovered['restored'])
        self.assertEqual(b'original user save', (self.profile / 'save.dat').read_bytes())

    def test_game_starts_after_test_move_leaves_recoverable_state(self):
        self.activate()
        (self.profile / 'new-save.dat').write_bytes(b'test session save')
        count = [0]
        def starts_during_restore():
            count[0] += 1
            if count[0] == 3:
                raise FoundryError('game_running', 'fixture')
        result = neutral_profile.restore(self.session, process_check=starts_during_restore)
        self.assertEqual('unresolved', result['outcome'])
        state = neutral_profile.status(self.session)
        self.assertTrue(state['original_at_backup'])
        self.assertTrue(state['test_at_recovery'])
        recovered = neutral_profile.recover(self.session, process_check=self.closed)
        self.assertTrue(recovered['restored'])
        self.assertEqual(b'original user save', (self.profile / 'save.dat').read_bytes())

    def test_receipt_failure_after_restore_can_be_finalized(self):
        self.activate()
        original_write = neutral_profile.new_json
        def fail_completion(path, value):
            if path.name == 'restore-complete.json':
                raise OSError('fixture failure')
            return original_write(path, value)
        with patch.object(neutral_profile, 'new_json', side_effect=fail_completion):
            result = neutral_profile.restore(self.session, process_check=self.closed)
        self.assertEqual('unresolved', result['outcome'])
        state = neutral_profile.status(self.session)
        self.assertTrue(state['original_at_profile'])
        self.assertTrue(state['test_at_recovery'])
        self.assertTrue(neutral_profile.recover(self.session, process_check=self.closed)['restored'])

    def test_running_game_refuses_before_any_change(self):
        def running():
            self.checks += 1
            raise FoundryError('game_running', 'fixture')
        with self.assertRaises(FoundryError) as caught:
            neutral_profile.activate(self.profile, self.session, process_check=running)
        self.assertEqual('game_running', caught.exception.code)
        self.assertEqual(1, self.checks)
        self.assertEqual(b'original user save', (self.profile / 'save.dat').read_bytes())
        self.assertFalse(self.session.exists())


if __name__ == '__main__':
    unittest.main()
