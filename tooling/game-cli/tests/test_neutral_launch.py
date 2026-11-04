import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sims4foundry_cli import neutral_launch, neutral_profile, neutral_ea_consent
from sims4foundry_cli.errors import FoundryError


class NeutralLaunchTests(unittest.TestCase):
    def setUp(self):
        for target, attribute, value in ((neutral_launch, '_is_windows', lambda:True), (neutral_profile, '_WINDOWS', True)):
            fixture = patch.object(target, attribute, value)
            fixture.start()
            self.addCleanup(fixture.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / 'The Sims 4'; self.profile.mkdir()
        (self.profile / 'save.dat').write_bytes(b'original')
        self.profile_session = self.root / 'profile-session'
        neutral_profile.activate(self.profile, self.profile_session, process_check=lambda:None)
        self.game = self.root / 'GameInstall'; self.game.mkdir()
        self.ea = self.root / 'EA Suite' / 'EALauncher.exe'
        self.ea.parent.mkdir()
        self.ea.write_bytes(b'fixture launcher')
        self.ea_hash = hashlib.sha256(self.ea.read_bytes()).hexdigest()
        self.ea_log = self.root / 'ea.log'; self.ea_log.write_bytes(b'old log\n')
        self.game_hash = 'a'*64
        self.launch_session = self.root / 'launch-session'
        self.dispatched = []
        self.plan = {'ok':True, 'launched':False, 'url':'origin2://game/launch?offerIds=123&autoDownload=0',
                     'account_launch':{'offer_id':'OFFER-1','content_id':'123'}}
        self.handler = '"{}" "%1"'.format(self.ea)
        self.observed = {'status':'one_observed', 'matched_count':1,
                         'matches':[{'pid':987, 'fixture':True}]}
        self.now = 0.0

    def pause(self, seconds):
        self.now += seconds

    def execute(self, **extra):
        options = dict(prepare_fn=lambda *args:self.plan, known_profile=lambda:str(self.profile),
                       handler_command=lambda:self.handler, process_check=lambda:None,
                       dispatch=lambda url:self.dispatched.append(url),
                       discover_fn=lambda *args:self.observed,
                       monotonic=lambda:self.now, sleep=self.pause)
        options.update(extra)
        return neutral_launch.execute(self.game, [str(self.ea_log)], self.game_hash,
                                      self.ea, self.ea_hash, self.profile_session,
                                      self.launch_session, seconds=1, **options)

    def test_dispatch_once_and_observe_exact_candidate(self):
        result = self.execute()
        self.assertTrue(result['ok'])
        self.assertEqual('game_observed', result['outcome'])
        self.assertEqual([self.plan['url']], self.dispatched)
        self.assertTrue((self.launch_session / 'launch-intent.json').is_file())
        self.assertTrue((self.launch_session / 'launch-observation.json').is_file())
        self.assertEqual(b'original', (Path(neutral_profile.status(self.profile_session)['original_backup']) / 'save.dat').read_bytes())

    def test_timeout_never_replays(self):
        result = self.execute(discover_fn=lambda *args:{'status':'none_observed'})
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual([self.plan['url']], self.dispatched)
        self.assertFalse(result['action_replayed'])

    def test_lost_handoff_ack_never_replays(self):
        def dispatch(url):
            self.dispatched.append(url)
            raise OSError('fixture lost acknowledgment')
        result = self.execute(dispatch=dispatch)
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual([self.plan['url']], self.dispatched)
        self.assertTrue((self.launch_session / 'launch-intent.json').exists())
        self.assertFalse((self.launch_session / 'launch-observation.json').exists())

    def test_ea_log_context_is_bound_to_the_one_handoff(self):
        launcher = self.game / 'Game' / 'Bin' / 'TS4_Launcher_x64.exe'
        def dispatch(url):
            self.dispatched.append(url)
            stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            prefix = '[' + stamp + '] PID: 42 TID: 7 '
            lines = [
                'Handling game launch request (InitialRequest): IDs=[offerKey.offerId=[], offerId=[], contentIds=[123], slug=[]], offerKey.externalType=[], requestOwner=[EA], requestSource=[RTP], gameArguments=[]',
                '[GAME] Got exePath=[{}] cmdArgs=[] from defaultLauncher.'.format(launcher),
                '[GAME] Issuing launch: workingDirectory[{}] offer[OFFER-1] content[123] gameArgs []'.format(launcher.parent),
                'Successful launch. IDs: offerKey.offerId=[OFFER-1], offerId=[], contentIds=[123], slug=[the-sims-4]',
            ]
            with self.ea_log.open('ab') as stream:
                stream.write(('\n'.join(prefix + row for row in lines) + '\n').encode())
        self.execute(dispatch=dispatch)
        result = neutral_launch.ea_context(self.launch_session)
        self.assertEqual('ea_request_observed', result['outcome'])
        self.assertEqual(42, result['ea_pid'])
        self.assertFalse(result['dialog_acknowledged'])
        self.assertFalse(result['game_started_verified'])
        self.assertEqual(1, len(self.dispatched))

    def test_lost_handoff_can_be_observed_without_dispatching_again(self):
        def dispatch(url):
            self.dispatched.append(url)
            raise OSError('fixture lost acknowledgment')
        first = self.execute(dispatch=dispatch)
        self.assertEqual('unresolved', first['outcome'])
        output = self.root / 'observation.json'
        observed = neutral_launch.observe(self.launch_session, seconds=1, output=output,
                                          discover_fn=lambda *args:self.observed,
                                          monotonic=lambda:self.now, sleep=self.pause)
        self.assertEqual('game_observed', observed['outcome'])
        self.assertTrue(observed['read_only'])
        self.assertEqual([self.plan['url']], self.dispatched)
        self.assertEqual('game_observed', json.loads(output.read_text())['outcome'])

    def test_modified_launch_intent_is_not_observed(self):
        self.execute()
        intent = self.launch_session / 'launch-intent.json'
        value = json.loads(intent.read_text())
        value['game_root'] = str(self.root / 'elsewhere')
        intent.write_text(json.dumps(value))
        with self.assertRaises(FoundryError):
            neutral_launch.observe(self.launch_session, seconds=1,
                                   discover_fn=lambda *args:self.fail('unexpected observation'))
        self.assertEqual([self.plan['url']], self.dispatched)

    def test_non_os_dispatch_error_is_unresolved(self):
        def dispatch(url):
            self.dispatched.append(url)
            raise RuntimeError('fixture uncertain')
        result = self.execute(dispatch=dispatch)
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual(1, len(self.dispatched))

    def test_existing_game_refuses_before_launch_intent(self):
        def running():
            raise FoundryError('game_running', 'fixture')
        with self.assertRaises(FoundryError):
            self.execute(process_check=running)
        self.assertFalse(self.launch_session.exists())
        self.assertEqual([], self.dispatched)

    def test_handler_change_after_intent_refuses_dispatch(self):
        calls = [0]
        def handler():
            calls[0] += 1
            return self.handler if calls[0] == 1 else self.handler + ' --unexpected'
        with self.assertRaises(FoundryError):
            self.execute(handler_command=handler)
        self.assertTrue((self.launch_session / 'launch-intent.json').exists())
        self.assertEqual([], self.dispatched)

    def test_profile_not_default_refuses_before_any_dispatch(self):
        with self.assertRaises(FoundryError):
            self.execute(known_profile=lambda:str(self.root / 'wrong'))
        self.assertFalse(self.launch_session.exists())
        self.assertEqual([], self.dispatched)

    def test_unquoted_handler_rejects_existing_executable_prefix(self):
        collision = self.root / 'EA.exe'
        collision.write_bytes(b'unrelated program')
        unquoted = '{} "%1"'.format(self.ea)
        with self.assertRaises(FoundryError) as caught:
            neutral_launch._validate_handler(unquoted, self.ea, self.ea_hash)
        self.assertEqual('ea_handler_ambiguous', caught.exception.code)

    def test_rejects_handler_arguments_and_wrong_hash(self):
        with self.assertRaises(FoundryError):
            neutral_launch._validate_handler(self.handler + ' --extra', self.ea, self.ea_hash)
        with self.assertRaises(FoundryError):
            neutral_launch._validate_handler(self.handler, self.ea, '0'*64)

    def test_windows_guard_is_not_removed_by_fixture_support(self):
        with patch.object(neutral_launch, '_is_windows', return_value=False):
            with self.assertRaises(FoundryError) as caught: self.execute()
        self.assertEqual('windows_required', caught.exception.code)
        self.assertFalse(self.launch_session.exists())

    def test_sparse_oversize_launcher_never_reaches_hash_read(self):
        with self.ea.open('r+b') as stream:
            stream.truncate(1024 * 1024 * 1024)
        with patch.object(neutral_launch.neutral_ea_process, '_read', side_effect=AssertionError('unexpected read')):
            with self.assertRaises(FoundryError) as caught:
                neutral_launch._validate_handler(self.handler, self.ea, self.ea_hash)
        self.assertEqual('ea_image_bound', caught.exception.code)

    def test_pending_handoff_blocks_restore_and_second_launch_even_with_no_game(self):
        self.execute(discover_fn=lambda *args:{'status':'none_observed'})
        state = neutral_profile.status(self.profile_session)
        self.assertEqual('pending', state['launch_state'])
        self.assertTrue(state['original_at_backup'])
        with self.assertRaises(FoundryError):
            neutral_profile.restore(self.profile_session, process_check=lambda:None)
        self.launch_session = self.root / 'second-launch'
        with self.assertRaises(FoundryError) as caught: self.execute()
        self.assertEqual('launch_pending', caught.exception.code)
        self.assertFalse(self.launch_session.exists())
        self.assertEqual(1, len(self.dispatched))

    def test_post_reservation_failure_records_undispatched_without_clearing(self):
        real = neutral_profile.validate_launch_reservation
        def validate(*args):
            real(*args)
            raise FoundryError('fixture_failure', 'fixture after reservation')
        with patch.object(neutral_profile, 'validate_launch_reservation', side_effect=validate):
            with self.assertRaises(FoundryError): self.execute()
        receipt = json.loads((self.launch_session / 'launch-not-dispatched.json').read_text())
        self.assertIs(receipt['dispatch_attempted'], False)
        self.assertEqual([], self.dispatched)
        self.assertEqual('pending', neutral_profile.status(self.profile_session)['launch_state'])
        with self.assertRaises(FoundryError):
            neutral_profile.restore(self.profile_session, process_check=lambda:None)

    def test_profile_marker_change_after_context_persistence_refuses_dispatch(self):
        real = neutral_launch.new_json
        def write(path, value):
            real(path, value)
            if path.name == 'ea-context-intent.json':
                (self.profile / '.foundry-test-profile.json').write_text('{}')
        with patch.object(neutral_launch, 'new_json', side_effect=write):
            with self.assertRaises(FoundryError): self.execute()
        self.assertEqual([], self.dispatched)

    def test_consent_refuses_withdrawn_profile_before_helper_admission(self):
        self.execute(discover_fn=lambda *args:{'status':'none_observed'})
        state = neutral_profile.status(self.profile_session)
        # Simulate an external actor only in this newly fabricated temp profile.
        self.profile.rename(self.root / 'withdrawn-fixture-test')
        Path(state['original_backup']).rename(self.profile)
        with patch.object(neutral_launch, '_default_profile', return_value=str(self.profile)), \
             patch.object(neutral_launch.neutral_testmods, '_no_game_candidates', return_value=None):
            with self.assertRaises(FoundryError):
                neutral_ea_consent.acknowledge(self.launch_session, 'EA.exe', 'b'*64,
                    context_reader=lambda _:self.fail('unexpected EA log admission'),
                    process_reader=lambda *a:self.fail('unexpected process admission'),
                    helper_factory=lambda _:self.fail('unexpected helper'))
        self.assertFalse((self.launch_session / 'ea-consent-intent.json').exists())
        self.assertEqual(b'original', (self.profile / 'save.dat').read_bytes())

    def test_consent_guard_rechecks_documents_marker_and_reservation(self):
        self.execute(discover_fn=lambda *args:{'status':'none_observed'})
        with neutral_launch.consent_guard(self.launch_session, known_profile=lambda:str(self.profile),
                                         process_check=lambda:None) as validate:
            binding = validate()
            self.assertEqual(str(self.profile), binding['profile'])
            (self.profile / '.foundry-test-profile.json').write_text('{}')
            with self.assertRaises(FoundryError): validate()

    def test_consent_refuses_changed_documents_or_reservation_identity(self):
        self.execute(discover_fn=lambda *args:{'status':'none_observed'})
        with self.assertRaises(FoundryError):
            with neutral_launch.consent_guard(self.launch_session, known_profile=lambda:str(self.root / 'elsewhere'),
                                             process_check=lambda:None):
                self.fail('unexpected consent admission')
        reservation = self.profile_session / 'launch-reservation.json'
        value = json.loads(reservation.read_text())
        value['request_id'] = 'f'*32
        from sims4foundry_cli.io import digest
        value['record_sha256'] = digest({k:v for k,v in value.items() if k != 'record_sha256'})
        reservation.write_text(json.dumps(value))
        with self.assertRaises(FoundryError):
            with neutral_launch.consent_guard(self.launch_session, known_profile=lambda:str(self.profile),
                                             process_check=lambda:None):
                self.fail('unexpected mismatched reservation')

    def test_legacy_intent_is_observable_but_not_consent_authority(self):
        self.execute()
        from sims4foundry_cli.io import digest
        intent = self.launch_session / 'launch-intent.json'
        value = json.loads(intent.read_text())
        value['schema'] = 1
        value.pop('profile_binding')
        value['record_sha256'] = digest({k:v for k,v in value.items() if k != 'record_sha256'})
        intent.write_text(json.dumps(value))
        self.assertEqual('game_observed', neutral_launch.observe(self.launch_session, seconds=1,
                         discover_fn=lambda *a:self.observed)['outcome'])
        with self.assertRaises(FoundryError):
            with neutral_launch.consent_guard(self.launch_session): self.fail('legacy consent admission')


    def test_full_fabricated_handoff_and_consent_preserves_binding(self):
        launcher = self.game / 'Game' / 'Bin' / 'TS4_Launcher_x64.exe'
        def dispatch(url):
            self.dispatched.append(url)
            stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            line = ('[{}] PID: 42 TID: 7 Processing launch request: offerId[OFFER-1] contentId[123] '
                    'exe[{}] cwd[{}] args[] locale[en_US] isTrial[false] isElevated[false] '
                    'requestSource[RTP] isLauncherElevated[false]\n').format(stamp, launcher, launcher.parent)
            with self.ea_log.open('ab') as stream: stream.write(line.encode())
        self.execute(dispatch=dispatch, discover_fn=lambda *args:{'status':'none_observed'})
        context = neutral_launch.ea_context(self.launch_session)
        process = {'pid':42, 'creation_filetime':context['timestamp_utc_filetime']-10000000,
                   'image':'EADesktop.exe', 'image_sha256':'b'*64}
        calls = []
        class Helper:
            phases = iter(('ready','invoke-intent','acknowledged'))
            def event(self, deadline): return next(self.phases)
            def commit(self, nonce): calls.append(nonce)
            def close(self): return True
        guard = lambda session:neutral_launch.consent_guard(session,
                     known_profile=lambda:str(self.profile), process_check=lambda:None)
        result = neutral_ea_consent.acknowledge(self.launch_session, 'EADesktop.exe', 'b'*64,
                    process_reader=lambda *a:dict(process), helper_factory=lambda _:Helper(), guard_factory=guard)
        self.assertEqual('acknowledged', result['outcome'])
        self.assertEqual(1, len(calls))
        self.assertFalse(result['request_specific_dialog_verified'])
        self.assertEqual('pending', neutral_profile.status(self.profile_session)['launch_state'])
        with self.assertRaises(FoundryError):
            neutral_profile.restore(self.profile_session, process_check=lambda:None)
        with self.assertRaises(FoundryError):
            neutral_ea_consent.acknowledge(self.launch_session, 'EADesktop.exe', 'b'*64,
                helper_factory=lambda _:self.fail('replayed helper'))

    def test_abort_receipt_refuses_consent_before_and_during_guard(self):
        self.execute()
        receipt = self.launch_session / 'launch-not-dispatched.json'
        with neutral_launch.consent_guard(self.launch_session, known_profile=lambda:str(self.profile),
                                         process_check=lambda:None) as validate:
            validate()
            receipt.write_text('{}')
            with self.assertRaises(FoundryError) as caught: validate()
            self.assertEqual('launch_not_dispatched', caught.exception.code)
        with self.assertRaises(FoundryError):
            with neutral_launch.consent_guard(self.launch_session): self.fail('unexpected abort admission')
        receipt.write_text('malformed')
        with self.assertRaises(FoundryError):
            with neutral_launch.consent_guard(self.launch_session): self.fail('unexpected malformed abort admission')

    def test_uncertain_dispatch_has_no_consent_receipt_or_replay(self):
        def dispatch(url):
            self.dispatched.append(url)
            raise OSError('fixture lost handoff response')
        result = self.execute(dispatch=dispatch)
        self.assertEqual('unresolved', result['outcome'])
        self.assertFalse((self.launch_session / 'launch-handoff.json').exists())
        with self.assertRaises(FoundryError) as caught:
            with neutral_launch.consent_guard(self.launch_session): self.fail('uncertain dispatch consent')
        self.assertEqual('ea_handoff_unverified', caught.exception.code)
        self.assertEqual(1, len(self.dispatched))

    def test_handoff_receipt_binds_exact_intent_and_is_rechecked(self):
        self.execute()
        receipt = self.launch_session / 'launch-handoff.json'
        recorded = json.loads(receipt.read_text())
        intent = json.loads((self.launch_session / 'launch-intent.json').read_text())
        self.assertIs(recorded['dispatch_returned'], True)
        self.assertEqual(intent['record_sha256'], recorded['launch_intent_sha256'])
        with neutral_launch.consent_guard(self.launch_session, known_profile=lambda:str(self.profile),
                                         process_check=lambda:None) as validate:
            validate()
            recorded['request_id'] = 'f'*32
            receipt.write_text(json.dumps(recorded))
            with self.assertRaises(FoundryError) as caught: validate()
            self.assertEqual('ea_handoff_unverified', caught.exception.code)

    def test_profile_changed_after_dispatch_has_no_accepted_receipt(self):
        def dispatch(url):
            self.dispatched.append(url)
            (self.profile / '.foundry-test-profile.json').write_text('{}')
        result = self.execute(dispatch=dispatch)
        self.assertEqual('unresolved', result['outcome'])
        self.assertFalse((self.launch_session / 'launch-handoff.json').exists())
        self.assertFalse((self.launch_session / 'launch-not-dispatched.json').exists())
        self.assertEqual(1, len(self.dispatched))

    def test_failed_handoff_receipt_never_repeats_dispatch(self):
        real = neutral_launch.new_json
        def write(path, record):
            if path.name == 'launch-handoff.json': raise OSError('fixture receipt failure')
            real(path, record)
        with patch.object(neutral_launch, 'new_json', side_effect=write): result = self.execute()
        self.assertEqual('unresolved', result['outcome'])
        self.assertFalse((self.launch_session / 'launch-not-dispatched.json').exists())
        self.assertEqual(1, len(self.dispatched))
        with self.assertRaises(FoundryError): self.execute()

    def test_relative_game_root_uses_recorded_absolute_identity(self):
        absolute = self.game
        self.game = Path(os.path.relpath(absolute))
        seen = []
        def observe(executable, checksum):
            seen.append(executable)
            return self.observed
        self.execute(discover_fn=observe)
        context = json.loads((self.launch_session / 'ea-context-intent.json').read_text())
        self.assertEqual(str(absolute / 'Game' / 'Bin' / 'TS4_Launcher_x64.exe'), context['expected']['launcher'])
        self.assertEqual([str(absolute / 'Game' / 'Bin' / 'TS4_x64.exe')], seen)


if __name__ == '__main__':
    unittest.main()
