"""Owned directories and fabricated launch callbacks only; no native calls."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from sims4foundry_cli import neutral_launch, neutral_launch_recovery as recovery, neutral_profile
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import digest, new_json
import test_neutral_launch as supplied


class RecoveryTests(unittest.TestCase):
    setUp = supplied.NeutralLaunchTests.setUp
    pause = supplied.NeutralLaunchTests.pause
    execute = supplied.NeutralLaunchTests.execute

    def fail_before_dispatch(self):
        reserve = neutral_profile.reserve_launch
        def refuse(*args):
            reserve(*args)
            raise RuntimeError('owned fixture failure before dispatch')
        with patch.object(neutral_profile, 'reserve_launch', side_effect=refuse):
            with self.assertRaises(RuntimeError):
                self.execute()
        self.assertEqual([], self.dispatched)

    def reconcile(self):
        return recovery.reconcile_not_dispatched(self.profile_session, self.launch_session)

    def test_proven_predispatch_failure_allows_preserving_restore(self):
        self.fail_before_dispatch()
        (self.profile / 'fixture-test.dat').write_bytes(b'preserve test bytes')
        self.assertTrue(self.reconcile()['ok'])
        self.assertEqual('resolved_before_dispatch', neutral_profile.status(self.profile_session)['launch_state'])
        restored = neutral_profile.restore(self.profile_session, process_check=lambda:None)
        self.assertEqual(b'original', (self.profile / 'save.dat').read_bytes())
        self.assertEqual(b'preserve test bytes', (Path(restored['test_profile_preserved']) / 'fixture-test.dat').read_bytes())
        self.assertTrue((self.profile_session / 'launch-reservation.json').is_file())
        self.assertTrue((self.launch_session / 'launch-not-dispatched.json').is_file())
        self.assertEqual([], self.dispatched)

    def test_repeat_reconciliation_reads_without_writing(self):
        self.fail_before_dispatch()
        self.reconcile()
        with patch.object(recovery, 'new_json', side_effect=AssertionError('must not rewrite')):
            self.assertTrue(self.reconcile()['already_recorded'])

    def test_resolved_reservation_cannot_authorize_consent_or_replay(self):
        self.fail_before_dispatch(); self.reconcile()
        with neutral_profile.active_profile_guard(self.profile_session):
            value = neutral_profile.status(self.profile_session)['launch_reservation']
            with self.assertRaises(FoundryError) as caught:
                neutral_profile.validate_launch_reservation(self.profile_session, self.launch_session, value['request_id'])
            self.assertEqual('launch_resolved', caught.exception.code)
        self.launch_session = self.root / 'new-launch-session'
        with self.assertRaises(FoundryError):
            self.execute()
        self.assertFalse(self.launch_session.exists())
        self.assertEqual([], self.dispatched)

    def test_acknowledged_dispatch_does_not_resolve(self):
        self.execute()
        with self.assertRaises((FoundryError, OSError)):
            self.reconcile()
        self.assertFalse((self.profile_session / recovery.RESOLUTION_NAME).exists())

    def test_uncertain_dispatch_does_not_resolve(self):
        def uncertain(url):
            self.dispatched.append(url)
            raise OSError('lost acknowledgment')
        self.assertEqual('unresolved', self.execute(dispatch=uncertain)['outcome'])
        with self.assertRaises((FoundryError, OSError)):
            self.reconcile()
        self.assertEqual(1, len(self.dispatched))

    def test_conflicting_evidence_refuses_even_if_empty_or_nonregular(self):
        self.fail_before_dispatch()
        for name in ('launch-handoff.json', 'launch-observation.json', 'ea-consent-intent.json', 'ea-consent-result.json'):
            with self.subTest(name=name):
                target = self.launch_session / name
                target.mkdir()
                try:
                    with self.assertRaises(FoundryError):
                        self.reconcile()
                finally:
                    target.rmdir()

    def test_malformed_no_dispatch_evidence_refuses(self):
        self.fail_before_dispatch()
        path = self.launch_session / 'launch-not-dispatched.json'
        original = json.loads(path.read_text())
        variants = [None, {}, dict(original, schema=True), dict(original, dispatch_attempted=0),
                    dict(original, dispatch_attempted=True), dict(original, request_id='f'*32),
                    dict(original, extra=True)]
        for value in variants:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                with self.assertRaises(FoundryError):
                    self.reconcile()
        self.assertFalse((self.profile_session / recovery.RESOLUTION_NAME).exists())

    def test_forged_binding_hash_cannot_select_another_activation(self):
        self.fail_before_dispatch()
        path = self.launch_session / 'launch-intent.json'
        value = json.loads(path.read_text())
        value['profile_binding']['test_identity']['inode'] += 1
        value['record_sha256'] = digest({k:v for k,v in value.items() if k != 'record_sha256'})
        path.write_text(json.dumps(value))
        with self.assertRaises(FoundryError): self.reconcile()

    def test_wrong_launch_session_refuses(self):
        self.fail_before_dispatch()
        with self.assertRaises(FoundryError):
            recovery.reconcile_not_dispatched(self.profile_session, self.root / 'different')

    def test_proof_change_after_resolution_blocks_restore(self):
        self.fail_before_dispatch(); self.reconcile()
        (self.launch_session / 'launch-not-dispatched.json').write_text('{}')
        with self.assertRaises(FoundryError):
            neutral_profile.restore(self.profile_session, process_check=lambda:None)
        self.assertTrue(self.profile.is_dir())
        self.assertFalse((self.profile_session / 'restore-intent.json').exists())

    def test_optional_null_resolution_is_not_absence(self):
        self.fail_before_dispatch()
        (self.profile_session / recovery.RESOLUTION_NAME).write_text('null')
        with self.assertRaises(FoundryError): self.reconcile()
        with self.assertRaises(FoundryError): neutral_profile.status(self.profile_session)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX owned FIFO fixture')
    def test_fifo_resolution_refuses_without_opening(self):
        self.fail_before_dispatch()
        os.mkfifo(self.profile_session / recovery.RESOLUTION_NAME)
        with self.assertRaises(FoundryError): self.reconcile()

    def test_lost_resolution_acknowledgment_is_preserved_then_observed(self):
        self.fail_before_dispatch()
        writer = recovery.new_json
        def lost(path, value):
            writer(path, value)
            raise OSError('lost acknowledgment after durable receipt')
        with patch.object(recovery, 'new_json', side_effect=lost):
            self.assertEqual('unresolved', self.reconcile()['outcome'])
        self.assertTrue((self.profile_session / recovery.RESOLUTION_NAME).exists())
        self.assertTrue(self.reconcile()['already_recorded'])

    def test_resolution_holds_the_shared_profile_lock(self):
        self.fail_before_dispatch()
        writer = recovery.new_json
        attempts = []
        def checked(path, value):
            with self.assertRaises(FoundryError) as caught:
                neutral_profile.restore(self.profile_session, process_check=lambda:None)
            attempts.append(caught.exception.code)
            writer(path, value)
        with patch.object(recovery, 'new_json', side_effect=checked):
            self.assertTrue(self.reconcile()['ok'])
        self.assertEqual(['busy'], attempts)

    def test_no_native_discovery_is_needed_to_resolve_never_dispatched(self):
        self.fail_before_dispatch()
        with patch.object(neutral_launch.discovery, 'discover', side_effect=AssertionError('no native calls')):
            self.assertTrue(self.reconcile()['ok'])

    def test_missing_reservation_cannot_resolve(self):
        self.fail_before_dispatch()
        (self.profile_session / 'launch-reservation.json').unlink()
        with self.assertRaises(FoundryError): self.reconcile()

    def test_resolution_without_preserved_reservation_blocks_status(self):
        self.fail_before_dispatch(); self.reconcile()
        (self.profile_session / 'launch-reservation.json').unlink()
        with self.assertRaises(FoundryError): neutral_profile.status(self.profile_session)


if __name__ == '__main__':
    unittest.main()
