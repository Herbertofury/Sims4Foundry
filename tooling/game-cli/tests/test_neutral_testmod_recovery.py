"""Owned temporary directories only; all native process checks are mocked."""
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from sims4foundry_cli import neutral_profile, neutral_testmods
from sims4foundry_cli import neutral_testmod_recovery as recovery
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import new_json
from sims4foundry_cli.neutral_profile_lock import profile_lock
import test_neutral_testmods as fixtures


class RetirementRecoveryTests(unittest.TestCase):
    check = fixtures.TestModWorkflow.check
    install = fixtures.TestModWorkflow.install

    def setUp(self):
        fixtures.TestModWorkflow.setUp(self)
        platform = patch.object(recovery, '_WINDOWS', True)
        platform.start()
        self.addCleanup(platform.stop)
        rename = patch.object(recovery, '_rename_directory', side_effect=self.fake_os.rename)
        self.rename = rename.start()
        self.addCleanup(rename.stop)

    def prepare(self, moved=False, bound=False):
        if bound:
            self.profile_session = self.root / 'profile-session'
            neutral_profile.activate(self.profile, self.profile_session, process_check=self.check)
        self.install()
        self.record = json.loads((self.session / 'install-complete.json').read_bytes())
        new_json(self.session / 'retire-intent.json', self.record)
        self.target = self.mods / 'FoundryTest'
        self.retired = self.profile / ('.foundry-retired-' + self.record['session_id'])
        if moved:
            os.rename(self.target, self.retired)

    def recover(self, check=None):
        return recovery.recover_retirement(self.session, process_check=check or self.check)

    def assert_code(self, code, action):
        with self.assertRaises(FoundryError) as caught:
            action()
        self.assertEqual(code, caught.exception.code)

    def assert_preserved(self):
        self.assertEqual(self.record, json.loads((self.session / 'retire-intent.json').read_bytes()))
        self.assertFalse((self.session / 'retire-complete.json').exists())

    def test_original_finishes_once_and_repeated_recovery_never_replays(self):
        self.prepare()
        before = {p.name: p.read_bytes() for p in self.session.iterdir()}
        result = self.recover()
        self.assertTrue(result['move_performed'])
        self.assertTrue(result['retired'])
        self.assertFalse(result['deleted'])
        self.assertEqual(1, self.rename.call_count)
        self.assertEqual(b'DBPF candidate fixture', (self.retired / 'candidate.package').read_bytes())
        self.assertEqual(b'DBPF original fixture', (self.mods / 'original.package').read_bytes())
        self.assertTrue(self.recover()['already_recorded'])
        self.assertEqual(1, self.rename.call_count)
        for name, raw in before.items():
            self.assertEqual(raw, (self.session / name).read_bytes())
        self.assertEqual('retired_recorded', neutral_testmods.status(self.session)['state'])

    def test_moved_records_without_rename(self):
        self.prepare(moved=True)
        result = self.recover()
        self.assertTrue(result['ok'])
        self.assertFalse(result['move_performed'])
        self.rename.assert_not_called()

    def test_actual_interruption_before_first_move_is_recoverable(self):
        self.install()
        calls = []
        def interrupt():
            calls.append(None)
            if len(calls) == 2:
                raise OSError('interrupted after intent')
        with self.assertRaises(OSError):
            neutral_testmods.retire(self.session, process_check=interrupt)
        self.assertEqual('retirement_unresolved', neutral_testmods.status(self.session)['state'])
        self.assertTrue(self.recover()['ok'])

    def test_actual_lost_retirement_receipt_is_recoverable(self):
        self.install()
        original = neutral_testmods.new_json
        def fail(path, value):
            if path.name == 'retire-complete.json':
                raise OSError('lost receipt')
            return original(path, value)
        with patch.object(neutral_testmods, 'new_json', side_effect=fail):
            self.assertEqual('unresolved', neutral_testmods.retire(self.session, process_check=self.check)['outcome'])
        self.assertTrue(self.recover()['ok'])
        self.rename.assert_not_called()

    def test_lost_move_acknowledgment_stays_unresolved_then_records_without_replay(self):
        self.prepare()
        def move_then_fail(source, destination):
            self.fake_os.rename(source, destination)
            raise OSError('lost acknowledgment')
        with patch.object(recovery, '_rename_directory', side_effect=move_then_fail) as move:
            self.assertEqual('unresolved', self.recover()['outcome'])
            self.assertEqual(1, move.call_count)
        self.assert_preserved()
        self.assertTrue(self.recover()['ok'])
        self.rename.assert_not_called()

    def test_failed_move_without_mutation_can_finish_on_explicit_recovery(self):
        self.prepare()
        with patch.object(recovery, '_rename_directory', side_effect=PermissionError('denied')):
            self.assertEqual('unresolved', self.recover()['outcome'])
        self.assert_preserved()
        self.assertTrue(self.target.is_dir())
        self.assertTrue(self.recover()['move_performed'])

    def test_lost_receipt_acknowledgment_is_not_rewritten(self):
        self.prepare(moved=True)
        def write_then_fail(path, value):
            new_json(path, value)
            raise OSError('lost receipt acknowledgment')
        with patch.object(recovery, 'new_json', side_effect=write_then_fail):
            self.assertEqual('unresolved', self.recover()['outcome'])
        with patch.object(recovery, 'new_json', side_effect=AssertionError('must not rewrite')):
            self.assertTrue(self.recover()['already_recorded'])
        self.rename.assert_not_called()

    def test_both_neither_and_staging_refuse(self):
        self.prepare()
        self.retired.mkdir()
        self.assert_code('retirement_recovery_refused', self.recover)
        self.retired.rmdir()
        preserved = self.root / 'preserved'
        os.rename(self.target, preserved)
        self.assert_code('retirement_recovery_refused', self.recover)
        os.rename(preserved, self.target)
        (self.profile / ('.foundry-stage-' + self.record['session_id'])).mkdir()
        self.assert_code('retirement_recovery_refused', self.recover)
        self.rename.assert_not_called()
        self.assert_preserved()

    def test_each_replaced_directory_refuses_even_same_bytes(self):
        self.prepare()
        for path in (self.target, self.mods, self.profile):
            with self.subTest(path=path):
                saved = self.root / 'saved'
                os.rename(path, saved)
                shutil.copytree(saved, path)
                self.assert_code('directory_changed', self.recover)
                shutil.rmtree(path)
                os.rename(saved, path)
        self.rename.assert_not_called()
        self.assert_preserved()

    def test_changed_moved_bytes_and_completed_history_are_not_current_proof(self):
        self.prepare(moved=True)
        self.assertTrue(self.recover()['ok'])
        (self.retired / 'candidate.package').write_bytes(b'DBPF changed')
        self.assert_code('directory_changed', self.recover)
        self.rename.assert_not_called()

    def test_missing_mismatched_and_nonregular_receipts_refuse(self):
        self.prepare()
        for name in ('install-intent.json', 'install-complete.json', 'retire-intent.json', 'install-identities.json'):
            path = self.session / name
            raw = path.read_bytes()
            path.unlink()
            with self.assertRaises((FoundryError, FileNotFoundError)):
                self.recover()
            path.mkdir()
            with self.assertRaises(FoundryError):
                self.recover()
            path.rmdir()
            path.write_text('{}')
            with self.assertRaises(FoundryError):
                self.recover()
            path.write_bytes(raw)
        self.rename.assert_not_called()
        self.assert_preserved()

    def test_completion_with_boolean_schema_or_unknown_fields_refuses(self):
        self.prepare(moved=True)
        expected = recovery._completion(self.record, self.retired)
        path = self.session / 'retire-complete.json'
        for changed in ({'schema': True}, {'extra': 1}, {'recovered': 'other'}):
            path.write_text(json.dumps(dict(expected, **changed)))
            self.assert_code('invalid_record', self.recover)
        self.rename.assert_not_called()

    def test_denied_location_metadata_is_not_absence(self):
        self.prepare()
        real = Path.lstat
        def denied(path, *args, **kwargs):
            if path == self.retired:
                raise PermissionError('owned fixture denial')
            return real(path, *args, **kwargs)
        with patch.object(Path, 'lstat', denied):
            with self.assertRaises(PermissionError):
                self.recover()
        self.rename.assert_not_called()
        self.assert_preserved()

    def test_running_or_unobservable_game_refuses(self):
        self.prepare()
        for error in (FoundryError('game_running', 'fixture'), PermissionError('scan denied')):
            def refused():
                raise error
            with self.assertRaises(type(error)):
                self.recover(refused)
        self.rename.assert_not_called()
        self.assert_preserved()

    def test_fresh_scan_changes_are_checked_before_move(self):
        self.prepare()
        calls = []
        def changed():
            calls.append(None)
            if len(calls) == 2:
                (self.target / 'candidate.package').write_bytes(b'DBPF edited')
        self.assert_code('directory_changed', lambda: self.recover(changed))
        self.rename.assert_not_called()
        self.assert_preserved()

    def test_fresh_scan_changed_receipts_refuse(self):
        self.prepare()
        def changed():
            (self.session / 'retire-intent.json').write_text('{}')
        self.assert_code('invalid_record', lambda: self.recover(changed))
        self.rename.assert_not_called()
        self.assertFalse((self.session / 'retire-complete.json').exists())

    def test_shared_profile_lock_blocks_and_is_held_during_checks(self):
        self.prepare()
        with profile_lock(self.profile):
            self.assert_code('busy', self.recover)
        def interleave():
            self.assert_code('busy', lambda: neutral_profile.activate(
                self.profile, self.root / 'other-session', process_check=self.check))
        self.assertTrue(self.recover(interleave)['ok'])
        self.assertFalse((self.root / 'other-session').exists())

    def test_bound_profile_pending_ea_handoff_refuses_even_when_moved(self):
        self.prepare(moved=True, bound=True)
        with neutral_profile.active_profile_guard(self.profile_session):
            neutral_profile.reserve_launch(self.profile_session, self.root / 'launch-session', 'a' * 32)
        self.assert_code('launch_pending', self.recover)
        self.assert_preserved()
        self.rename.assert_not_called()

    def test_bound_profile_missing_marker_refuses(self):
        self.prepare(bound=True)
        (self.profile / '.foundry-test-profile.json').unlink()
        self.assert_code('profile_unverified', self.recover)
        self.assert_preserved()

    def test_pending_launch_appearing_during_final_scan_refuses(self):
        self.prepare(bound=True)
        calls = []
        def reserve():
            calls.append(None)
            if len(calls) == 2:
                neutral_profile.reserve_launch(self.profile_session, self.root / 'launch-session', 'b' * 32)
        self.assert_code('launch_pending', lambda: self.recover(reserve))
        self.rename.assert_not_called()
        self.assert_preserved()

    def test_post_move_scan_failure_preserves_unresolved_then_recovers(self):
        self.prepare()
        calls = []
        def scan():
            calls.append(None)
            if len(calls) == 3:
                raise PermissionError('post-move scan denied')
        self.assertEqual('unresolved', self.recover(scan)['outcome'])
        self.assertTrue(self.retired.is_dir())
        self.assert_preserved()
        self.assertTrue(self.recover()['ok'])
        self.assertEqual(1, self.rename.call_count)

    def test_completed_receipt_with_original_location_refuses(self):
        self.prepare()
        new_json(self.session / 'retire-complete.json', recovery._completion(self.record, self.retired))
        self.assert_code('retirement_recovery_refused', self.recover)
        self.rename.assert_not_called()

    def test_session_replaced_during_scan_refuses(self):
        self.prepare()
        def replace():
            saved = self.root / 'saved-session'
            os.rename(self.session, saved)
            shutil.copytree(saved, self.session)
        self.assert_code('invalid_record', lambda: self.recover(replace))
        self.rename.assert_not_called()
        self.assert_preserved()

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX owned FIFO fixture')
    def test_fifo_completion_is_not_absent(self):
        self.prepare()
        os.mkfifo(self.session / 'retire-complete.json')
        self.assert_code('build_input_type', self.recover)
        self.rename.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX owned symlink fixture')
    def test_broken_destination_link_is_not_absent(self):
        self.prepare()
        self.retired.symlink_to(self.root / 'missing', target_is_directory=True)
        self.assert_code('linked_path', self.recover)
        self.rename.assert_not_called()
        self.assert_preserved()

    def test_nonwindows_refuses_before_read(self):
        with patch.object(recovery, '_WINDOWS', False):
            self.assert_code('windows_required', self.recover)


if __name__ == '__main__':
    unittest.main()
