import json
import os
import shutil
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sims4foundry_cli import mods, neutral_profile, neutral_testmods
from sims4foundry_cli.errors import FoundryError


class TestModWorkflow(unittest.TestCase):
    def setUp(self):
        # Only this module sees a fabricated Windows API surface. Pathlib,
        # other modules and the real process platform stay unchanged.
        real_os = neutral_testmods.os
        def no_replace(source, destination):
            if os.path.lexists(destination):
                raise FileExistsError(str(destination))
            os.rename(source, destination)
        self.fake_os = SimpleNamespace(name='nt', scandir=real_os.scandir,
                                       fsync=real_os.fsync, rename=no_replace)
        profile_platform = patch.object(neutral_profile, '_WINDOWS', True)
        profile_platform.start()
        self.addCleanup(profile_platform.stop)
        profile_move = patch.object(neutral_profile, '_rename_directory', side_effect=no_replace)
        profile_move.start()
        self.addCleanup(profile_move.stop)
        platform = patch.object(neutral_testmods, 'os', self.fake_os)
        platform.start()
        self.addCleanup(platform.stop)
        native = patch.object(neutral_testmods.discovery, '_snapshot_image_pids',
                              side_effect=AssertionError('No native process observation in fixture'))
        native.start()
        self.addCleanup(native.stop)
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
                                        profile_session=getattr(self, 'profile_session', None), process_check=self.check)

    def test_default_game_check_stops_on_candidate(self):
        with patch.object(neutral_testmods.discovery, '_snapshot_image_pids', return_value=[123]) as observe:
            with self.assertRaises(FoundryError) as caught:
                neutral_testmods._no_game_candidates()
        self.assertEqual('game_running', caught.exception.code)
        self.assertEqual(1, observe.call_count)

    def test_game_and_launcher_candidates_each_block(self):
        candidates = ('TS4_x64.exe', 'TS4_DX9_x64.exe', 'TS4.exe',
                      'TS4_DX9.exe', 'TS4_Launcher.exe', 'TS4_Launcher_x64.exe')
        for name in candidates:
            with self.subTest(image=name):
                def snapshot(image):
                    return [123] if image.casefold() == name.casefold() else []
                with patch.object(neutral_testmods.discovery, '_snapshot_image_pids', side_effect=snapshot):
                    with self.assertRaises(FoundryError) as caught:
                        neutral_testmods._no_game_candidates()
                self.assertEqual('game_running', caught.exception.code)

    def test_process_snapshot_error_is_not_treated_as_no_game(self):
        with patch.object(neutral_testmods.discovery, '_snapshot_image_pids', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                neutral_testmods._no_game_candidates()

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
        with self.assertRaises(FoundryError) as caught:
            self.install()
        self.assertEqual('source_changed', caught.exception.code)
        self.assertFalse(self.session.exists())
        (self.source / 'candidate.package').write_bytes(b'DBPF candidate fixture')
        def running():
            raise FoundryError('game_running', 'fixture')
        with self.assertRaises(FoundryError) as caught:
            neutral_testmods.install(self.manifest, self.profile, self.session,
                                     process_check=running)
        self.assertEqual('game_running', caught.exception.code)
        self.assertFalse(self.session.exists())

    def test_nonwindows_public_operations_refuse_before_work(self):
        self.fake_os.name = 'posix'
        for action in (self.install, lambda: neutral_testmods.retire(self.session)):
            with self.assertRaises(FoundryError) as caught:
                action()
            self.assertEqual('windows_required', caught.exception.code)
        self.assertFalse(self.session.exists())

    def test_optional_record_directory_is_rejected_not_absent(self):
        self.install()
        path = self.session / 'retire-intent.json'
        path.mkdir()
        with self.assertRaises(FoundryError) as caught:
            neutral_testmods.status(self.session)
        self.assertEqual('build_input_type', caught.exception.code)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX owned FIFO receipt fixtures')
    def test_each_optional_fifo_record_is_rejected_not_absent(self):
        self.install()
        for name in ('install-complete.json', 'retire-intent.json', 'retire-complete.json'):
            with self.subTest(name=name):
                path = self.session / name
                previous = path.read_bytes() if path.exists() else None
                if previous is not None:
                    path.unlink()
                os.mkfifo(path)
                with self.assertRaises(FoundryError) as caught:
                    neutral_testmods.status(self.session)
                self.assertEqual('build_input_type', caught.exception.code)
                path.unlink()
                if previous is not None:
                    path.write_bytes(previous)

    def test_optional_metadata_denial_is_not_hidden_as_absent(self):
        self.install()
        selected = self.session / 'retire-intent.json'
        original = Path.lstat
        def denied(path, *args, **kwargs):
            if path == selected:
                raise PermissionError('fixture metadata denial')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', denied):
            with self.assertRaises(PermissionError):
                neutral_testmods.status(self.session)

    def test_retirement_receipt_requires_exact_schema_and_recovery_path(self):
        self.install()
        neutral_testmods.retire(self.session, process_check=self.check)
        path = self.session / 'retire-complete.json'
        original = json.loads(path.read_bytes())
        for change in ({'recovered': 'unrelated'}, {'schema': True}, {'extra': 'unexpected'}):
            with self.subTest(change=change):
                path.write_text(json.dumps(dict(original, **change)))
                with self.assertRaises(FoundryError) as caught:
                    neutral_testmods.status(self.session)
                self.assertEqual('invalid_record', caught.exception.code)
        path.write_text(json.dumps(original))

    def test_completed_retirement_without_intent_is_not_terminal_proof(self):
        self.install()
        neutral_testmods.retire(self.session, process_check=self.check)
        (self.session / 'retire-intent.json').unlink()
        with self.assertRaises(FoundryError) as caught:
            neutral_testmods.status(self.session)
        self.assertEqual('invalid_record', caught.exception.code)

    def test_added_staging_file_during_final_scan_is_not_published(self):
        def check():
            self.check_calls += 1
            if self.check_calls == 3:
                staging = next(self.profile.glob('.foundry-stage-*'))
                (staging / 'unselected.package').write_bytes(b'DBPF unselected')
        with self.assertRaises(FoundryError) as caught:
            neutral_testmods.install(self.manifest, self.profile, self.session, process_check=check)
        self.assertEqual('directory_changed', caught.exception.code)
        self.assertFalse((self.mods / 'FoundryTest').exists())

    def test_duplicate_during_final_scan_is_not_published(self):
        def check():
            self.check_calls += 1
            if self.check_calls == 3:
                (self.mods / 'CANDIDATE.package').write_bytes(b'DBPF user duplicate')
        with self.assertRaises(FoundryError) as caught:
            neutral_testmods.install(self.manifest, self.profile, self.session, process_check=check)
        self.assertEqual('duplicate_mod_name', caught.exception.code)
        self.assertFalse((self.mods / 'FoundryTest').exists())

    def test_edit_during_final_retire_scan_is_preserved_in_place(self):
        self.install()
        self.check_calls = 0
        candidate = self.mods / 'FoundryTest' / 'candidate.package'
        def check():
            self.check_calls += 1
            if self.check_calls == 3:
                candidate.write_bytes(b'DBPF final scan edit')
        with self.assertRaises(FoundryError) as caught:
            neutral_testmods.retire(self.session, process_check=check)
        self.assertEqual('directory_changed', caught.exception.code)
        self.assertEqual(b'DBPF final scan edit', candidate.read_bytes())

    def test_edit_during_publication_reports_unresolved_without_receipt(self):
        rename = self.fake_os.rename
        def move_then_edit(source, destination):
            rename(source, destination)
            (destination / 'candidate.package').write_bytes(b'DBPF changed after move')
        self.fake_os.rename = move_then_edit
        result = self.install()
        self.assertEqual('unresolved', result['outcome'])
        self.assertTrue(result['installed'])
        self.assertFalse((self.session / 'install-complete.json').exists())
        self.assertEqual(b'DBPF changed after move',
                         (self.mods / 'FoundryTest' / 'candidate.package').read_bytes())

    def test_edit_during_retirement_reports_unresolved_and_preserves_bytes(self):
        self.install()
        rename = self.fake_os.rename
        def move_then_edit(source, destination):
            rename(source, destination)
            (destination / 'candidate.package').write_bytes(b'DBPF changed during retirement')
        self.fake_os.rename = move_then_edit
        result = neutral_testmods.retire(self.session, process_check=self.check)
        self.assertEqual('unresolved', result['outcome'])
        self.assertTrue(result['retired'])
        self.assertFalse((self.session / 'retire-complete.json').exists())
        self.assertEqual(b'DBPF changed during retirement',
                         (Path(result['recovered']) / 'candidate.package').read_bytes())

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

    def assert_code(self, code, action):
        with self.assertRaises(FoundryError) as caught:
            action()
        self.assertEqual(code, caught.exception.code)

    def activate_profile(self):
        self.profile_session = self.root / 'profile-session'
        return neutral_profile.activate(self.profile, self.profile_session, process_check=self.check)

    def test_install_excludes_profile_activation_for_entire_operation(self):
        profile_session = self.root / 'profile-session'
        checks = []
        def interleave():
            checks.append(None)
            self.assert_code('busy', lambda: neutral_profile.activate(
                self.profile, profile_session, process_check=self.check))
        result = neutral_testmods.install(self.manifest, self.profile, self.session, process_check=interleave)
        self.assertTrue(result['installed'])
        self.assertEqual(3, len(checks))
        self.assertFalse(profile_session.exists())
        self.assertEqual(b'DBPF original fixture', (self.mods / 'original.package').read_bytes())

    def test_profile_activation_excludes_test_mod_install(self):
        attempts = []
        def interleave():
            attempts.append(None)
            self.assert_code('busy', self.install)
        result = neutral_profile.activate(self.profile, self.root / 'profile-session', process_check=interleave)
        self.assertTrue(result['activated'])
        self.assertEqual(3, len(attempts))
        self.assertFalse(self.session.exists())
        self.assertFalse((self.mods / 'FoundryTest').exists())

    def test_install_excludes_profile_restore_and_preserves_published_mods(self):
        self.activate_profile()
        def interleave():
            self.assert_code('busy', lambda: neutral_profile.restore(self.profile_session, process_check=self.check))
        installed = neutral_testmods.install(self.manifest, self.profile, self.session, profile_session=self.profile_session, process_check=interleave)
        self.assertTrue(installed['installed'])
        self.assertFalse((self.profile_session / 'restore-intent.json').exists())
        restored = neutral_profile.restore(self.profile_session, process_check=self.check)
        preserved = Path(restored['test_profile_preserved'])
        self.assertEqual(b'DBPF candidate fixture', (preserved / 'Mods' / 'FoundryTest' / 'candidate.package').read_bytes())
        self.assertEqual(b'DBPF original fixture', (self.mods / 'original.package').read_bytes())

    def test_retire_excludes_profile_restore_and_then_allows_it(self):
        self.activate_profile()
        self.install()
        def interleave():
            self.assert_code('busy', lambda: neutral_profile.restore(self.profile_session, process_check=self.check))
        retired = neutral_testmods.retire(self.session, process_check=interleave)
        self.assertTrue(retired['retired'])
        self.assertFalse((self.profile_session / 'restore-intent.json').exists())
        restored = neutral_profile.restore(self.profile_session, process_check=self.check)
        preserved = Path(restored['test_profile_preserved']) / Path(retired['recovered']).name
        self.assertEqual(b'DBPF candidate fixture', (preserved / 'candidate.package').read_bytes())

    def test_restore_excludes_retire_at_every_process_checkpoint(self):
        self.activate_profile()
        self.install()
        attempts = []
        def interleave():
            attempts.append(None)
            self.assert_code('busy', lambda: neutral_testmods.retire(self.session, process_check=self.check))
        restored = neutral_profile.restore(self.profile_session, process_check=interleave)
        self.assertTrue(restored['restored'])
        self.assertEqual(3, len(attempts))
        self.assertFalse((self.session / 'retire-intent.json').exists())
        self.assert_code('directory_changed', lambda: neutral_testmods.retire(self.session, process_check=self.check))

    def test_launch_guard_excludes_install_and_retire(self):
        self.activate_profile()
        with neutral_profile.active_profile_guard(self.profile_session):
            self.assert_code('busy', self.install)
        self.install()
        with neutral_profile.active_profile_guard(self.profile_session):
            self.assert_code('busy', lambda: neutral_testmods.retire(self.session, process_check=self.check))
        self.assertFalse((self.session / 'retire-intent.json').exists())

    def test_install_excludes_launch_guard(self):
        self.activate_profile()
        def interleave():
            with self.assertRaises(FoundryError) as caught:
                with neutral_profile.active_profile_guard(self.profile_session):
                    self.fail('launch guard must not enter during install')
            self.assertEqual('busy', caught.exception.code)
        self.assertTrue(neutral_testmods.install(self.manifest, self.profile, self.session, profile_session=self.profile_session, process_check=interleave)['installed'])

    def test_same_bytes_replaced_profile_is_not_used_by_stale_retirement(self):
        self.install()
        original = self.root / 'preserved-profile'
        os.rename(self.profile, original)
        shutil.copytree(original, self.profile)
        self.assert_code('directory_changed', lambda: neutral_testmods.retire(self.session, process_check=self.check))
        self.assertFalse((self.session / 'retire-intent.json').exists())
        self.assertEqual(b'DBPF candidate fixture', (self.mods / 'FoundryTest' / 'candidate.package').read_bytes())
        self.assertEqual(b'DBPF candidate fixture', (original / 'Mods' / 'FoundryTest' / 'candidate.package').read_bytes())

    def test_install_rechecks_mods_identity_after_final_process_check(self):
        checks = []
        def replace():
            checks.append(None)
            if len(checks) == 3:
                os.rename(self.mods, self.root / 'preserved-mods')
                self.mods.mkdir()
                (self.mods / 'unrelated.package').write_bytes(b'DBPF unrelated')
        self.assert_code('directory_changed', lambda: neutral_testmods.install(
            self.manifest, self.profile, self.session, process_check=replace))
        self.assertFalse((self.mods / 'FoundryTest').exists())
        self.assertEqual(b'DBPF unrelated', (self.mods / 'unrelated.package').read_bytes())

    def test_install_rechecks_staging_identity_even_when_bytes_match(self):
        checks = []
        def replace():
            checks.append(None)
            if len(checks) == 3:
                staging = next(self.profile.glob('.foundry-stage-*'))
                preserved = self.root / 'preserved-staging'
                os.rename(staging, preserved)
                shutil.copytree(preserved, staging)
        self.assert_code('directory_changed', lambda: neutral_testmods.install(
            self.manifest, self.profile, self.session, process_check=replace))
        self.assertFalse((self.mods / 'FoundryTest').exists())

    def test_retire_rechecks_target_identity_even_when_bytes_match(self):
        self.install()
        checks = []
        target = self.mods / 'FoundryTest'
        def replace():
            checks.append(None)
            if len(checks) == 3:
                preserved = self.root / 'preserved-target'
                os.rename(target, preserved)
                shutil.copytree(preserved, target)
        self.assert_code('directory_changed', lambda: neutral_testmods.retire(self.session, process_check=replace))
        self.assertEqual(b'DBPF candidate fixture', (target / 'candidate.package').read_bytes())
        self.assertFalse((self.session / 'retire-complete.json').exists())
        self.assertFalse(any(self.profile.glob('.foundry-retired-*')))

    def test_install_rechecks_profile_before_first_staging_write(self):
        preserved = self.root / 'preserved-profile'
        def replace():
            os.rename(self.profile, preserved)
            shutil.copytree(preserved, self.profile)
        self.assert_code('directory_changed', lambda: neutral_testmods.install(
            self.manifest, self.profile, self.session, process_check=replace))
        self.assertFalse(self.session.exists())
        self.assertFalse(any(self.profile.glob('.foundry-stage-*')))

    def test_historical_session_without_identity_evidence_is_readable_but_not_retirable(self):
        self.install()
        (self.session / 'install-identities.json').unlink()
        self.assertEqual('install_recorded', neutral_testmods.status(self.session)['state'])
        self.assert_code('identity_unavailable', lambda: neutral_testmods.retire(self.session, process_check=self.check))
        self.assertFalse((self.session / 'retire-intent.json').exists())

    def test_identity_receipt_corruption_refuses_retirement(self):
        self.install()
        path = self.session / 'install-identities.json'
        original = json.loads(path.read_bytes())
        for changed in ({'schema': True}, {'record_sha256': '0' * 64},
                        {'test_identity': {'device': True, 'inode': 1}}, {'extra': 1}):
            with self.subTest(changed=changed):
                path.write_text(json.dumps(dict(original, **changed)))
                self.assert_code('invalid_record', lambda: neutral_testmods.retire(self.session, process_check=self.check))
        self.assertFalse((self.session / 'retire-intent.json').exists())

    def test_install_lock_is_released_after_prepublication_failure(self):
        with patch.object(neutral_testmods, 'new_json', side_effect=OSError('owned fixture')):
            with self.assertRaises(OSError):
                self.install()
        # A different already-authorized operation can acquire the same lock.
        self.assertTrue(self.activate_profile()['activated'])


    def test_marked_profile_requires_explicit_session_before_writes(self):
        self.activate_profile()
        self.assert_code('profile_session_required', lambda: neutral_testmods.install(
            self.manifest, self.profile, self.session, process_check=self.check))
        self.assertFalse(self.session.exists())
        self.assertFalse((self.mods / 'FoundryTest').exists())

    def test_pending_launch_after_guard_release_blocks_bound_install(self):
        self.activate_profile()
        with neutral_profile.active_profile_guard(self.profile_session):
            neutral_profile.reserve_launch(self.profile_session, self.root / 'launch-session', 'a' * 32)
        before = self.check_calls
        self.assert_code('launch_pending', self.install)
        self.assertEqual(before, self.check_calls)
        self.assertFalse(self.session.exists())
        self.assertFalse(any(self.profile.glob('.foundry-stage-*')))

    def test_pending_launch_after_guard_release_blocks_bound_retirement(self):
        self.activate_profile()
        self.install()
        identities = json.loads((self.session / 'install-identities.json').read_bytes())
        self.assertEqual(str(self.profile_session), identities['profile_binding']['profile_session'])
        with neutral_profile.active_profile_guard(self.profile_session):
            neutral_profile.reserve_launch(self.profile_session, self.root / 'launch-session', 'b' * 32)
        before = self.check_calls
        self.assert_code('launch_pending', lambda: neutral_testmods.retire(self.session, process_check=self.check))
        self.assertEqual(before, self.check_calls)
        self.assertFalse((self.session / 'retire-intent.json').exists())
        self.assertEqual(b'DBPF candidate fixture', (self.mods / 'FoundryTest' / 'candidate.package').read_bytes())

    def test_bound_install_missing_or_corrupt_marker_never_becomes_plain(self):
        self.activate_profile()
        marker = self.profile / '.foundry-test-profile.json'
        original = marker.read_bytes()
        marker.unlink()
        self.assert_code('profile_unverified', self.install)
        marker.write_text('{}')
        self.assert_code('profile_unverified', self.install)
        marker.write_text('{invalid')
        self.assert_code('invalid_json', self.install)
        marker.write_bytes(original)
        self.assertFalse(self.session.exists())

    def test_bound_retirement_missing_marker_refuses_without_intent(self):
        self.activate_profile()
        self.install()
        (self.profile / '.foundry-test-profile.json').unlink()
        self.assert_code('profile_unverified', lambda: neutral_testmods.retire(self.session, process_check=self.check))
        self.assertFalse((self.session / 'retire-intent.json').exists())

    def test_missing_bound_session_never_falls_back_to_plain_profile(self):
        self.activate_profile()
        with self.assertRaises(FileNotFoundError):
            neutral_testmods.install(self.manifest, self.profile, self.session,
                                     profile_session=self.root / 'missing-session', process_check=self.check)
        self.assertFalse(self.session.exists())

    def test_stale_activation_session_refuses_install(self):
        self.activate_profile()
        old_session = self.profile_session
        neutral_profile.restore(old_session, process_check=self.check)
        self.profile_session = self.root / 'new-profile-session'
        neutral_profile.activate(self.profile, self.profile_session, process_check=self.check)
        self.assert_code('profile_unverified', lambda: neutral_testmods.install(
            self.manifest, self.profile, self.session, profile_session=old_session, process_check=self.check))
        self.assertFalse(self.session.exists())

    def test_wrong_profile_session_refuses_before_process_checks(self):
        self.activate_profile()
        other_root = self.root / 'other-user'
        other = other_root / 'The Sims 4'
        other.mkdir(parents=True)
        other_session = self.root / 'other-profile-session'
        neutral_profile.activate(other, other_session, process_check=self.check)
        before = self.check_calls
        self.assert_code('profile_unverified', lambda: neutral_testmods.install(
            self.manifest, self.profile, self.session, profile_session=other_session, process_check=self.check))
        self.assertEqual(before, self.check_calls)
        self.assertFalse(self.session.exists())

    def test_corrupt_stored_binding_is_not_plain_profile_admission(self):
        self.activate_profile()
        self.install()
        path = self.session / 'install-identities.json'
        original = json.loads(path.read_bytes())
        variants = [dict(original, profile_binding=None), dict(original, profile_binding={}),
                    dict(original, profile_binding=dict(original['profile_binding'], session_id='f' * 32))]
        for value in variants:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                with self.assertRaises(FoundryError):
                    neutral_testmods.retire(self.session, process_check=self.check)
        self.assertFalse((self.session / 'retire-intent.json').exists())

    def test_new_marker_on_plain_install_refuses_retirement_without_binding(self):
        self.install()
        (self.profile / '.foundry-test-profile.json').write_text('{}')
        self.assert_code('profile_session_required', lambda: neutral_testmods.retire(self.session, process_check=self.check))
        self.assertFalse((self.session / 'retire-intent.json').exists())

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX marker fixture')
    def test_nonregular_marker_is_not_plain_profile_absence(self):
        os.mkfifo(self.profile / '.foundry-test-profile.json')
        self.assert_code('build_input_type', self.install)
        self.assertFalse(self.session.exists())

    def test_reservation_appearing_in_final_scan_blocks_install_publication(self):
        self.activate_profile()
        checks = []
        def reserved():
            checks.append(None)
            if len(checks) == 3:
                # The fixture owns the same lock, simulating newly visible
                # durable reservation evidence before the pending move.
                neutral_profile.reserve_launch(self.profile_session, self.root / 'launch-session', 'c' * 32)
        self.assert_code('launch_pending', lambda: neutral_testmods.install(
            self.manifest, self.profile, self.session, profile_session=self.profile_session, process_check=reserved))
        self.assertFalse((self.mods / 'FoundryTest').exists())
        self.assertFalse((self.session / 'install-complete.json').exists())

    def test_reservation_appearing_in_final_scan_blocks_retirement(self):
        self.activate_profile()
        self.install()
        checks = []
        def reserved():
            checks.append(None)
            if len(checks) == 3:
                neutral_profile.reserve_launch(self.profile_session, self.root / 'launch-session', 'd' * 32)
        self.assert_code('launch_pending', lambda: neutral_testmods.retire(self.session, process_check=reserved))
        self.assertTrue((self.mods / 'FoundryTest' / 'candidate.package').is_file())
        self.assertFalse((self.session / 'retire-complete.json').exists())


# Exercise the actual pre-dispatch resolution record validator, with the same
# fabricated launch callbacks used by the dedicated recovery fixtures.
import test_neutral_launch_recovery as recovery_fixtures


class TestModResolvedLaunch(unittest.TestCase):
    pause = recovery_fixtures.RecoveryTests.pause
    execute = recovery_fixtures.RecoveryTests.execute
    fail_before_dispatch = recovery_fixtures.RecoveryTests.fail_before_dispatch
    reconcile = recovery_fixtures.RecoveryTests.reconcile

    def setUp(self):
        recovery_fixtures.RecoveryTests.setUp(self)
        fake = SimpleNamespace(name='nt', scandir=os.scandir, fsync=os.fsync,
                               rename=neutral_profile._rename_directory)
        platform = patch.object(neutral_testmods, 'os', fake)
        platform.start()
        self.addCleanup(platform.stop)
        self.mod_source = self.root / 'mod-source'
        self.mod_source.mkdir()
        (self.mod_source / 'candidate.package').write_bytes(b'DBPF resolved fixture')
        self.mod_manifest = mods.inventory(self.mod_source)
        self.mod_session = self.root / 'mod-session'

    def install_mod(self):
        return neutral_testmods.install(self.mod_manifest, self.profile, self.mod_session,
                                        profile_session=self.profile_session, process_check=lambda: None)

    def test_verified_never_dispatched_resolution_allows_install_and_retire(self):
        self.fail_before_dispatch()
        with self.assertRaises(FoundryError) as caught:
            self.install_mod()
        self.assertEqual('launch_pending', caught.exception.code)
        self.assertTrue(self.reconcile()['ok'])
        self.assertTrue(self.install_mod()['installed'])
        retired = neutral_testmods.retire(self.mod_session, process_check=lambda: None)
        self.assertTrue(retired['retired'])
        self.assertEqual([], self.dispatched)
        self.assertEqual(b'DBPF resolved fixture', (Path(retired['recovered']) / 'candidate.package').read_bytes())

    def test_uncertain_handoff_with_empty_process_snapshot_blocks_retirement(self):
        self.install_mod()
        def uncertain(url):
            self.dispatched.append(url)
            raise OSError('unknown handoff acknowledgment')
        self.assertEqual('unresolved', self.execute(dispatch=uncertain)['outcome'])
        with self.assertRaises(FoundryError) as caught:
            neutral_testmods.retire(self.mod_session, process_check=lambda: None)
        self.assertEqual('launch_pending', caught.exception.code)
        self.assertFalse((self.mod_session / 'retire-intent.json').exists())
        self.assertEqual(1, len(self.dispatched))



if __name__ == '__main__':
    unittest.main()
