"""Portable owned-directory regressions. No real discovery or Windows calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sims4foundry_cli import neutral_profile as np
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import new_json
import test_neutral_profile as supplied


class ProfileSafetyTests(unittest.TestCase):
    setUp = supplied.ProfileTests.setUp
    closed = supplied.ProfileTests.closed
    activate = supplied.ProfileTests.activate

    def assert_error(self, code, action):
        with self.assertRaises(FoundryError) as caught:
            action()
        self.assertEqual(code, caught.exception.code)

    def interrupt_activation(self):
        rename = np._rename_directory
        calls = []
        def interrupt(source, destination):
            calls.append(source)
            if len(calls) == 2:
                raise PermissionError('owned fixture sharing violation before move')
            rename(source, destination)
        with patch.object(np, '_rename_directory', side_effect=interrupt):
            self.assertEqual('unresolved', self.activate()['outcome'])
        self.assertEqual(2, len(calls))
        return np.status(self.session)

    def interrupt_restore(self):
        self.activate()
        rename = np._rename_directory
        def lost(source, destination):
            rename(source, destination)
            raise OSError('owned fixture lost acknowledgment')
        with patch.object(np, '_rename_directory', side_effect=lost):
            self.assertEqual('unresolved', np.restore(self.session, process_check=self.closed)['outcome'])
        return np.status(self.session)

    def test_restore_intent_before_first_move_can_recover(self):
        self.activate()
        (self.profile / 'test-save.dat').write_bytes(b'test bytes')
        with patch.object(np, '_rename_directory', side_effect=PermissionError('fixture')) as rename:
            self.assertEqual('unresolved', np.restore(self.session, process_check=self.closed)['outcome'])
        self.assertEqual(1, rename.call_count)
        self.assertEqual('restore_unresolved', np.status(self.session)['state'])
        # A normal restore does not recreate/replay an already-published intent.
        self.assert_error('profile_changed', lambda: np.restore(self.session, process_check=self.closed))
        restored = np.recover(self.session, process_check=self.closed)
        self.assertTrue(restored['restored'])
        self.assertEqual(b'original user save', (self.profile / 'save.dat').read_bytes())
        self.assertEqual(b'test bytes', (Path(restored['test_profile_preserved']) / 'test-save.dat').read_bytes())

    def test_process_refusal_after_restore_intent_can_recover(self):
        self.activate()
        checks = []
        def starts():
            checks.append(None)
            if len(checks) == 2:
                raise FoundryError('game_running', 'owned fixture process result')
        self.assert_error('game_running', lambda: np.restore(self.session, process_check=starts))
        self.assertEqual('restore_unresolved', np.status(self.session)['state'])
        self.assertTrue(np.recover(self.session, process_check=self.closed)['restored'])

    def test_recovery_during_preparation_is_busy(self):
        checks, refusals = [], []
        def check():
            checks.append(None)
            if len(checks) == 2:
                self.assert_error('busy', lambda: np.recover_activation(self.session, process_check=self.closed))
                refusals.append(None)
        self.assertTrue(np.activate(self.profile, self.session, process_check=check)['activated'])
        self.assertEqual(1, len(refusals))
        self.assertFalse((self.session / 'activation-aborted.json').exists())
        self.assertEqual('activation_recorded', np.status(self.session)['state'])

    def test_recovery_before_activation_receipt_is_busy(self):
        writer = np.new_json
        attempts = []
        def receipt(path, value):
            if path.name == 'activation-complete.json':
                attempts.append(None)
                self.assert_error('busy', lambda: np.recover_activation(self.session, process_check=self.closed))
            return writer(path, value)
        with patch.object(np, 'new_json', side_effect=receipt):
            self.assertTrue(self.activate()['activated'])
        self.assertEqual(1, len(attempts))
        self.assertFalse((self.session / 'activation-aborted.json').exists())
        self.assertTrue(np.status(self.session)['test_at_profile'])

    def test_independent_activation_sessions_share_profile_lock(self):
        second = self.root / 'second-session'
        def check():
            self.assert_error('busy', lambda: np.activate(self.profile, second, process_check=self.closed))
        self.assertTrue(np.activate(self.profile, self.session, process_check=check)['activated'])
        self.assertFalse(second.exists())

    def test_lock_excludes_another_process_and_is_not_unlinked(self):
        self.activate()
        program = """
import sys
from pathlib import Path
from sims4foundry_cli import neutral_profile as p
from sims4foundry_cli.errors import FoundryError
try:
    with p._profile_lock(Path(sys.argv[1])):
        print('acquired')
except FoundryError as error:
    print(error.code)
"""
        def run():
            result = subprocess.run([sys.executable, '-c', program, str(self.profile)],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(0, result.returncode, result.stderr)
            return result.stdout.strip()
        with np.active_profile_guard(self.session):
            self.assertEqual('busy', run())
        self.assertEqual('acquired', run())
        self.assertTrue((self.root / '.foundry-profile-lifecycle.lock').is_file())

    def test_recovery_rechecks_backup_after_final_process_check(self):
        state = self.interrupt_activation()
        backup = Path(state['original_backup'])
        relocated = self.root / 'preserved-original'
        calls = []
        def replace():
            calls.append(None)
            if len(calls) == 2:
                os.rename(backup, relocated)
                backup.mkdir()
                (backup / 'unrelated.dat').write_bytes(b'unrelated')
        with patch.object(np, '_rename_directory', wraps=np._rename_directory) as rename:
            result = np.recover_activation(self.session, process_check=replace)
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual(0, rename.call_count)
        self.assertFalse(self.profile.exists())
        self.assertEqual(b'unrelated', (backup / 'unrelated.dat').read_bytes())
        self.assertEqual(b'original user save', (relocated / 'save.dat').read_bytes())
        self.assertFalse((self.session / 'activation-aborted.json').exists())

    def test_restore_rechecks_backup_before_first_move(self):
        state = self.activate()
        backup = Path(state['original_preserved'])
        calls = []
        def replace():
            calls.append(None)
            if len(calls) == 2:
                os.rename(backup, self.root / 'original-elsewhere')
                backup.mkdir()
        with patch.object(np, '_rename_directory') as rename:
            self.assert_error('profile_changed', lambda: np.restore(self.session, process_check=replace))
        rename.assert_not_called()
        self.assertTrue(np.status(self.session)['test_at_profile'])

    def test_restore_rechecks_backup_before_second_move(self):
        state = self.activate()
        backup = Path(state['original_preserved'])
        calls = []
        def replace():
            calls.append(None)
            if len(calls) == 3:
                os.rename(backup, self.root / 'original-elsewhere')
                backup.mkdir()
                (backup / 'unrelated').write_bytes(b'unrelated')
        with patch.object(np, '_rename_directory', wraps=np._rename_directory) as rename:
            result = np.restore(self.session, process_check=replace)
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual(1, rename.call_count)
        self.assertFalse(self.profile.exists())
        self.assertEqual(b'unrelated', (backup / 'unrelated').read_bytes())
        self.assertTrue(np.status(self.session)['test_at_recovery'])

    def test_recover_rechecks_backup_after_process_check(self):
        state = self.interrupt_restore()
        backup = Path(state['original_backup'])
        calls = []
        def replace():
            calls.append(None)
            if len(calls) == 2:
                os.rename(backup, self.root / 'original-elsewhere')
                backup.mkdir()
        with patch.object(np, '_rename_directory') as rename:
            result = np.recover(self.session, process_check=replace)
        self.assertEqual('unresolved', result['outcome'])
        rename.assert_not_called()
        self.assertFalse(self.profile.exists())

    def test_recovery_unknown_move_outcome_never_rolls_back(self):
        self.interrupt_restore()
        rename = np._rename_directory
        def lost(source, destination):
            rename(source, destination)
            raise OSError('unknown acknowledgment')
        with patch.object(np, '_rename_directory', side_effect=lost) as moves:
            result = np.recover(self.session, process_check=self.closed)
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual(1, moves.call_count)
        self.assertFalse((self.session / 'restore-complete.json').exists())
        self.assertEqual(b'original user save', (self.profile / 'save.dat').read_bytes())
        with patch.object(np, '_rename_directory') as moves:
            self.assertTrue(np.recover(self.session, process_check=self.closed)['restored'])
        moves.assert_not_called()

    def test_recovery_of_pending_first_move_lost_ack_stops_immediately(self):
        self.activate()
        with patch.object(np, '_rename_directory', side_effect=OSError('no move')):
            np.restore(self.session, process_check=self.closed)
        rename = np._rename_directory
        def lost(source, destination):
            rename(source, destination)
            raise OSError('lost acknowledgment')
        with patch.object(np, '_rename_directory', side_effect=lost) as moves:
            result = np.recover(self.session, process_check=self.closed)
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual(1, moves.call_count)
        self.assertFalse(self.profile.exists())
        self.assertTrue(np.status(self.session)['test_at_recovery'])
        self.assertTrue(np.recover(self.session, process_check=self.closed)['restored'])

    def test_contradictory_terminal_receipts_refuse_status_and_mutation(self):
        self.activate()
        record = np._record(self.session)
        new_json(self.session / 'activation-aborted.json', {'record_sha256': record['record_sha256']})
        self.assert_error('invalid_profile_record', lambda: np.status(self.session))
        with patch.object(np, '_rename_directory') as moves:
            self.assert_error('invalid_profile_record', lambda: np.restore(self.session, process_check=self.closed))
        moves.assert_not_called()

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX special-file regression')
    def test_status_fifo_intent_refuses_without_waiting_for_writer(self):
        self.session.mkdir()
        os.mkfifo(self.session / 'activation-intent.json')
        code = ('import sys; from sims4foundry_cli import neutral_profile as p; '
                'from sims4foundry_cli.errors import FoundryError; '
                "exec('try: p.status(sys.argv[1])\\nexcept FoundryError as e: print(e.code)')")
        result = subprocess.run([sys.executable, '-c', code, str(self.session)],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('invalid_profile_record', result.stdout.strip())

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX special-file regression')
    def test_optional_fifo_receipt_is_not_silently_absent(self):
        self.activate()
        os.mkfifo(self.session / 'restore-intent.json')
        self.assert_error('invalid_profile_record', lambda: np.status(self.session))

    @unittest.skipUnless(hasattr(os, 'mkfifo') and hasattr(os, 'O_NONBLOCK'), 'POSIX open-race regression')
    def test_swapped_fifo_is_opened_nonblocking_then_rejected_by_descriptor(self):
        self.activate()
        target = self.session / 'activation-intent.json'
        fake_os = SimpleNamespace(**vars(os))
        calls = []
        def replace(path, flags, *args):
            calls.append(None)
            self.assertEqual(target, path)
            self.assertTrue(flags & os.O_NONBLOCK)
            target.unlink()
            os.mkfifo(target)
            return os.open(path, flags, *args)
        fake_os.open = replace
        with patch.object(np, 'os', fake_os):
            self.assert_error('invalid_profile_record', lambda: np.status(self.session))
        self.assertEqual(1, len(calls))

    def test_record_access_error_has_no_fallback(self):
        self.activate()
        fake_os = SimpleNamespace(**vars(os))
        fake_os.open = unittest.mock.Mock(side_effect=PermissionError('fixture access denied'))
        with patch.object(np, 'os', fake_os), patch.object(Path, 'open', side_effect=AssertionError('fallback forbidden')):
            with self.assertRaises(PermissionError):
                np.status(self.session)
        self.assertEqual(1, fake_os.open.call_count)

    def test_hardlinked_intent_is_rejected(self):
        self.activate()
        os.link(self.session / 'activation-intent.json', self.root / 'other-link')
        self.assert_error('invalid_profile_record', lambda: np.status(self.session))

    def test_nonregular_optional_record_is_rejected(self):
        self.activate()
        (self.session / 'restore-intent.json').mkdir()
        self.assert_error('invalid_profile_record', lambda: np.status(self.session))

    def test_production_nonwindows_mutations_refuse_before_checks_or_files(self):
        with patch.object(np, '_WINDOWS', False), patch.object(np, '_profile_lock') as lock:
            for operation in (lambda: self.activate(),
                              lambda: np.restore(self.session, process_check=self.closed),
                              lambda: np.recover(self.session, process_check=self.closed),
                              lambda: np.recover_activation(self.session, process_check=self.closed)):
                self.assert_error('windows_required', operation)
        lock.assert_not_called()
        self.assertEqual(0, self.checks)
        self.assertFalse(self.session.exists())

    def test_pending_launch_blocks_restore_even_without_a_process(self):
        self.activate()
        launch = self.root / 'launch-session'
        request = 'a' * 32
        with np.active_profile_guard(self.session) as binding:
            self.assertEqual(np._identity(self.profile), binding['test_identity'])
            reservation = np.reserve_launch(self.session, launch, request)
            self.assertEqual(reservation, np.validate_launch_reservation(self.session, launch, request))
        before = self.checks
        for action in (np.restore, np.recover, np.recover_activation):
            self.assert_error('launch_pending', lambda: action(self.session, process_check=self.closed))
        self.assertEqual(before, self.checks)
        state = np.status(self.session)
        self.assertEqual('activation_recorded', state['state'])
        self.assertEqual('pending', state['launch_state'])
        self.assertEqual(reservation, state['launch_reservation'])
        self.assertEqual(b'original user save', (Path(state['original_backup']) / 'save.dat').read_bytes())

    def test_pending_launch_allows_exact_guarded_consent_not_another_launch(self):
        self.activate()
        launch = self.root / 'launch-session'
        request = 'b' * 32
        with np.active_profile_guard(self.session):
            np.reserve_launch(self.session, launch, request)
        with np.active_profile_guard(self.session):
            np.validate_launch_reservation(self.session, launch, request)
            self.assert_error('launch_pending', lambda: np.reserve_launch(self.session, launch, request))
            self.assert_error('launch_pending', lambda: np.validate_launch_reservation(self.session, launch, 'c' * 32))
            self.assert_error('busy', lambda: np.restore(self.session, process_check=self.closed))

    def test_corrupt_launch_reservation_never_unblocks_mutation(self):
        self.activate()
        (self.session / 'launch-reservation.json').write_text('{}')
        self.assert_error('invalid_profile_record', lambda: np.status(self.session))
        self.assert_error('launch_pending', lambda: np.restore(self.session, process_check=self.closed))

    def test_activation_original_replacement_after_process_check_is_not_moved(self):
        checks = []
        relocated = self.root / 'untouched-original'
        def replace():
            checks.append(None)
            if len(checks) == 2:
                os.rename(self.profile, relocated)
                self.profile.mkdir()
                (self.profile / 'unrelated.dat').write_bytes(b'unrelated')
        with patch.object(np, '_rename_directory') as moves:
            self.assert_error('profile_changed', lambda: np.activate(self.profile, self.session, process_check=replace))
        moves.assert_not_called()
        self.assertEqual(b'unrelated', (self.profile / 'unrelated.dat').read_bytes())
        self.assertEqual(b'original user save', (relocated / 'save.dat').read_bytes())

    def test_activation_recovery_lost_original_move_ack_is_not_replayed(self):
        self.interrupt_activation()
        rename = np._rename_directory
        def lost(source, destination):
            rename(source, destination)
            raise RuntimeError('unknown move acknowledgment')
        with patch.object(np, '_rename_directory', side_effect=lost) as moves:
            self.assertEqual('unresolved', np.recover_activation(self.session, process_check=self.closed)['outcome'])
        self.assertEqual(1, moves.call_count)
        self.assertFalse((self.session / 'activation-aborted.json').exists())
        with patch.object(np, '_rename_directory') as moves:
            self.assertTrue(np.recover_activation(self.session, process_check=self.closed)['activation_aborted'])
        moves.assert_not_called()

    def test_lost_restore_receipt_ack_is_status_only_not_another_move(self):
        self.activate()
        writer = np.new_json
        def lost(path, value):
            writer(path, value)
            if path.name == 'restore-complete.json':
                raise OSError('unknown receipt acknowledgment')
        with patch.object(np, 'new_json', side_effect=lost):
            self.assertEqual('unresolved', np.restore(self.session, process_check=self.closed)['outcome'])
        self.assertEqual('restored_recorded', np.status(self.session)['state'])
        with patch.object(np, '_rename_directory') as moves:
            self.assert_error('already_restored', lambda: np.recover(self.session, process_check=self.closed))
        moves.assert_not_called()

    def test_lost_activation_receipt_ack_cannot_create_abort_receipt(self):
        writer = np.new_json
        def lost(path, value):
            writer(path, value)
            if path.name == 'activation-complete.json':
                raise OSError('unknown receipt acknowledgment')
        with patch.object(np, 'new_json', side_effect=lost):
            self.assertEqual('unresolved', self.activate()['outcome'])
        self.assertEqual('activation_recorded', np.status(self.session)['state'])
        with patch.object(np, '_rename_directory') as moves:
            self.assert_error('recovery_unavailable', lambda: np.recover_activation(self.session, process_check=self.closed))
        moves.assert_not_called()
        self.assertFalse((self.session / 'activation-aborted.json').exists())

    def test_session_record_change_while_acquiring_lock_is_rejected(self):
        self.activate()
        original_lock = np._profile_lock
        from contextlib import contextmanager
        @contextmanager
        def changed(profile):
            with original_lock(profile):
                value = json.loads((self.session / 'activation-intent.json').read_text())
                value['original_identity']['inode'] += 1
                from sims4foundry_cli.io import digest
                value['record_sha256'] = digest({k: v for k, v in value.items() if k != 'record_sha256'})
                (self.session / 'activation-intent.json').write_text(json.dumps(value))
                yield
        with patch.object(np, '_profile_lock', changed), patch.object(np, '_rename_directory') as moves:
            self.assert_error('invalid_profile_record', lambda: np.restore(self.session, process_check=self.closed))
        moves.assert_not_called()

    def test_changed_regular_record_during_read_is_rejected(self):
        self.activate()
        target = self.session / 'activation-intent.json'
        fake_os = SimpleNamespace(**vars(os))
        from contextlib import contextmanager
        @contextmanager
        def changing_stream(fd, *args, **kwargs):
            with os.fdopen(fd, *args, **kwargs) as stream:
                class Reader:
                    def read(self, size):
                        raw = stream.read(size)
                        target.write_bytes(b'{}')
                        return raw
                yield Reader()
        fake_os.fdopen = changing_stream
        with patch.object(np, 'os', fake_os):
            self.assert_error('invalid_profile_record', lambda: np.status(self.session))

    def test_same_bytes_replaced_record_before_open_is_rejected(self):
        self.activate()
        target = self.session / 'activation-intent.json'
        raw = target.read_bytes()
        fake_os = SimpleNamespace(**vars(os))
        def replace(path, flags, *args):
            os.rename(target, self.root / 'preserved-intent.json')
            target.write_bytes(raw)
            return os.open(path, flags, *args)
        fake_os.open = replace
        with patch.object(np, 'os', fake_os):
            self.assert_error('invalid_profile_record', lambda: np.status(self.session))

    def test_lost_launch_reservation_ack_remains_pending(self):
        self.activate()
        writer = np.new_json
        def lost(path, value):
            writer(path, value)
            if path.name == 'launch-reservation.json':
                raise OSError('unknown reservation acknowledgment')
        with np.active_profile_guard(self.session), patch.object(np, 'new_json', side_effect=lost):
            with self.assertRaises(OSError):
                np.reserve_launch(self.session, self.root / 'launch-session', 'a' * 32)
        self.assertEqual('pending', np.status(self.session)['launch_state'])
        with patch.object(np, '_rename_directory') as moves:
            self.assert_error('launch_pending', lambda: np.restore(self.session, process_check=self.closed))
        moves.assert_not_called()

    def test_status_remains_read_only_and_does_not_create_a_lock(self):
        self.activate()
        (self.root / '.foundry-profile-lifecycle.lock').unlink()
        before = {str(p.relative_to(self.root)): p.stat().st_mtime_ns for p in self.root.rglob('*')}
        with patch.object(np, '_profile_lock', side_effect=AssertionError('status must not lock or write')):
            self.assertEqual('activation_recorded', np.status(self.session)['state'])
        after = {str(p.relative_to(self.root)): p.stat().st_mtime_ns for p in self.root.rglob('*')}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
