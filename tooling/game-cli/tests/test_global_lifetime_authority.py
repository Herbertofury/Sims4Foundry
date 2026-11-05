"""Cross-workspace reservation tests with an owned synthetic OS location."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from sims4foundry_cli import cli, global_lifetime_authority as authority, independent_session, input_journal, registered_input
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.session_registry import SessionRegistry
from sims4foundry_cli.session_witness import SessionWitness
from sims4foundry_cli.retained_process_witness import RetainedProcessRegistry
from test_guarded_input import identity
from test_session_registry import FakeAnchor
from test_session_witness import FakeApi as FakeWitnessApi


class GlobalLifetimeAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = mock.patch.object(authority,'_local_appdata',return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        authority.initialize()

    def claim(self, session='1'*32, witness='2'*32):
        return authority.claim(identity(),session,witness,'a'*64)

    def test_distinct_workspaces_share_exact_lifetime_reservation(self):
        first = self.claim()
        observed = authority.inspect(identity())
        self.assertEqual(first['reservation_sha256'],observed['reservation_sha256'])
        self.assertEqual('1'*32,observed['session_id'])
        self.assertFalse(observed['same_user_authenticated'])
        self.assertFalse(observed['rollback_protected'])
        with self.assertRaises(FoundryError) as error: self.claim('3'*32,'4'*32)
        self.assertEqual('global_lifetime_reserved',error.exception.code)
        self.assertTrue(error.exception.details['no_replay'])
        changed = deepcopy(identity())
        changed['executable_sha256'] = 'b'*64
        with self.assertRaises(FoundryError):
            authority.claim(changed,'3'*32,'4'*32,'b'*64)
        with self.assertRaises(FoundryError): authority.inspect(changed)

    def test_simultaneous_claims_have_one_winner(self):
        def attempt(number):
            try:
                self.claim(str(number)*32,str(number+2)*32)
                return 'won'
            except FoundryError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt,(1,2)))
        self.assertEqual(['global_lifetime_reserved','won'],sorted(results))

    def test_lost_claim_reply_is_inspected_not_recreated(self):
        publish = authority.new_json
        def lose_reply(path, row):
            publish(path,row)
            raise OSError('lost fixture reply')
        with mock.patch.object(authority,'new_json',side_effect=lose_reply):
            with self.assertRaises(FoundryError) as error: self.claim()
        self.assertEqual('global_claim_unresolved',error.exception.code)
        self.assertTrue(error.exception.details['no_replay'])
        self.assertEqual('1'*32,authority.inspect(identity())['session_id'])
        with self.assertRaises(FoundryError): self.claim('3'*32,'4'*32)

    def test_separate_processes_cannot_claim_same_lifetime(self):
        script = ("import sys,json; from pathlib import Path; "
                  "from sims4foundry_cli import global_lifetime_authority as a; "
                  "from sims4foundry_cli.errors import FoundryError; "
                  "a._local_appdata=lambda:Path(sys.argv[1]); "
                  "exec(\"try:\\n a.claim(json.loads(sys.argv[4]),sys.argv[2]*32,sys.argv[3]*32,'a'*64)\\n print('won')\\n"
                  "except FoundryError as e:\\n print(e.code)\")")
        outputs = []
        for session,witness in (('1','2'),('3','4')):
            completed = subprocess.run([sys.executable,'-c',script,str(self.root),session,witness,json.dumps(identity())],
                                       check=True,capture_output=True,text=True,timeout=15)
            outputs.append(completed.stdout.strip())
        self.assertEqual(['won','global_lifetime_reserved'],outputs)

    def test_initialization_never_replaces_existing_authority(self):
        with self.assertRaises(FoundryError) as error: authority.initialize()
        self.assertEqual('global_authority_exists',error.exception.code)

    def test_cli_status_and_inspection_use_the_same_authority(self):
        self.claim()
        original = self.root/'identity.json'
        original.write_text(json.dumps(identity()),encoding='utf-8')
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(0,cli.main(['global-authority','status']))
        self.assertEqual('global_authority_observed',json.loads(output.getvalue())['outcome'])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0,cli.main(['global-authority','inspect','--identity',str(original)]))
        self.assertEqual('global_reservation_observed',json.loads(output.getvalue())['outcome'])
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(2,cli.main(['global-authority','initialize']))
        self.assertEqual('global_authority_exists',json.loads(output.getvalue())['code'])

    def test_claim_session_binds_existing_original_journal_and_registry(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        binding = registered_input.journal_binding(journal)
        registry.open_session('1'*32,binding)
        original = {'session_id':'1'*32,'identity':identity()}
        with mock.patch.object(independent_session,'_open',return_value=(original,registry,journal,registry.inspect())):
            result = authority.claim_session(self.root/'old-authority',self.root/'old-session','2'*32)
        observed = authority.inspect(identity())
        self.assertEqual(result['reservation_sha256'],observed['reservation_sha256'])
        self.assertEqual(binding,observed['journal_sha256'])

    def test_claim_session_rejects_prior_journal_history(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        original = {'session_id':'1'*32,'identity':identity()}
        with mock.patch.object(independent_session,'_open',return_value=(original,registry,journal,registry.inspect())), \
             mock.patch.object(journal,'_runs',return_value=['2'*32]):
            with self.assertRaises(FoundryError) as error:
                authority.claim_session(self.root/'old-authority',self.root/'old-session','2'*32)
        self.assertEqual('global_session_unresolved',error.exception.code)
        self.assertFalse(list((self.root/'Sims4Foundry'/'live-authority-v1'/'lifetimes').iterdir()))

    def test_attach_requires_original_current_witness_and_registry(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        provider = RetainedProcessRegistry(FakeWitnessApi(),lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,provider,process,'2'*32)
        ticket = authority.claim_bound(registry,journal,witness)
        publish = authority.new_json
        def try_input(path,row):
            with self.assertRaises(FoundryError) as error:
                registry.begin('3'*32,'input','b'*64)
            self.assertEqual('busy',error.exception.code)
            return publish(path,row)
        with mock.patch.object(authority,'new_json',side_effect=try_input):
            lease,result = authority.acquire_bound(ticket)
        self.assertEqual('global_witness_attached',result['outcome'])
        self.assertFalse(result['atomic_snapshot'])
        self.assertFalse(result['game_qualified'])
        with witness.guard_for_dispatch(registry) as check_live: check_live()
        self.assertEqual(process,witness.recover().snapshot())
        replacement = SessionWitness(registry,provider,process,'2'*32)
        with self.assertRaises(FoundryError): replacement.recover()
        with self.assertRaises(FoundryError): authority.acquire_bound(ticket)
        lease.close()
        with self.assertRaises(FoundryError): authority.acquire_bound(ticket)
        observed = authority.inspect_attachment(identity())
        self.assertEqual(result['attachment_sha256'],observed['attachment_sha256'])
        self.assertFalse(observed['current_witness_verified'])
        self.assertFalse(observed['registry_gate_verified'])

    def test_claim_excludes_reentrant_and_foreign_thread_acquisition(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        provider = RetainedProcessRegistry(FakeWitnessApi(),lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,provider,process,'2'*32)
        def foreign_claim():
            with self.assertRaises(FoundryError) as error: authority.claim_bound(registry,journal,witness)
            return error.exception.code
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual('witness_busy',pool.submit(foreign_claim).result())
        self.assertEqual(1,len(registry.inspect()['events']))
        publish = authority.new_json
        def reenter(path,row):
            with self.assertRaises(FoundryError) as error: witness.acquire()
            self.assertEqual('witness_busy',error.exception.code)
            with self.assertRaises(FoundryError) as error: registry.inspect()
            self.assertEqual('busy',error.exception.code)
            return publish(path,row)
        with mock.patch.object(authority,'new_json',side_effect=reenter):
            ticket = authority.claim_bound(registry,journal,witness)
        self.assertIs(ticket._witness,witness)
        self.assertEqual(2,len(registry.inspect()['events']))

    def test_claim_fences_different_witness_on_same_registry(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,RetainedProcessRegistry(FakeWitnessApi(),lambda view:True),process,'2'*32)
        other = SessionWitness(registry,RetainedProcessRegistry(FakeWitnessApi(),lambda view:True),process,'3'*32)
        publish = authority.new_json
        def attempt_other(path,row):
            with self.assertRaises(FoundryError) as error: other.acquire()
            self.assertEqual('busy',error.exception.code)
            return publish(path,row)
        with mock.patch.object(authority,'new_json',side_effect=attempt_other):
            authority.claim_bound(registry,journal,witness)
        self.assertEqual(2,len(registry.inspect()['events']))

    def test_failed_global_publication_keeps_acquisition_attempt_pending(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,RetainedProcessRegistry(FakeWitnessApi(),lambda view:True),process,'2'*32)
        other = SessionWitness(registry,RetainedProcessRegistry(FakeWitnessApi(),lambda view:True),process,'3'*32)
        with mock.patch.object(authority,'claim',side_effect=OSError('fixture lost publication')):
            with self.assertRaises(FoundryError) as error: authority.claim_bound(registry,journal,witness)
        self.assertTrue(error.exception.details['no_replay'])
        self.assertEqual('attempt',registry.inspect()['events'][-1]['kind'])
        with self.assertRaises(FoundryError): other.acquire()

    def test_lost_claim_lock_cleanup_retains_pending_acquisition(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,RetainedProcessRegistry(FakeWitnessApi(),lambda view:True),process,'2'*32)
        held = registry._hold_events
        @contextmanager
        def lose_cleanup():
            with held() as state: yield state
            raise OSError('lost fixture lock cleanup')
        with mock.patch.object(registry,'_hold_events',side_effect=lose_cleanup):
            with self.assertRaises(FoundryError) as error: authority.claim_bound(registry,journal,witness)
        self.assertEqual('global_claim_unresolved',error.exception.code)
        self.assertTrue(error.exception.details['no_replay'])
        self.assertEqual('attempt',registry.inspect()['events'][-1]['kind'])
        self.assertEqual('global_reservation_observed',authority.inspect(identity())['outcome'])
        with self.assertRaises(FoundryError): witness.acquire()

    def test_lost_acquisition_reply_cannot_advance_global_attachment(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        api = FakeWitnessApi()
        provider = RetainedProcessRegistry(api,lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,provider,process,'2'*32)
        ticket = authority.claim_bound(registry,journal,witness)
        acquire = provider.acquire
        def lose_reply(*args):
            acquire(*args)
            raise OSError('lost fixture acquisition reply')
        with mock.patch.object(provider,'acquire',side_effect=lose_reply):
            with self.assertRaises(FoundryError) as error: authority.acquire_bound(ticket)
        self.assertTrue(error.exception.details['no_replay'])
        self.assertEqual('attempt',registry.inspect()['events'][-1]['kind'])
        with self.assertRaises(FoundryError): witness.recover()
        with self.assertRaises(FoundryError): authority.acquire_bound(ticket)
        self.assertEqual(1,api.opens)

    def test_lost_global_resolution_reply_keeps_owner_fenced(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        anchor = FakeAnchor()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,anchor)
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        api = FakeWitnessApi()
        provider = RetainedProcessRegistry(api,lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,provider,process,'2'*32)
        ticket = authority.claim_bound(registry,journal,witness)
        publish = authority.new_json
        def lose_resolution(path,row):
            publish(path,row)
            anchor.fail = 'after'
        with mock.patch.object(authority,'new_json',side_effect=lose_resolution):
            with self.assertRaises(FoundryError) as error: authority.acquire_bound(ticket)
        self.assertTrue(error.exception.details['no_replay'])
        anchor.fail = None
        self.assertEqual('resolved',registry.inspect()['events'][-1]['kind'])
        self.assertEqual('global_attachment_observed',authority.inspect_attachment(identity())['outcome'])
        with self.assertRaises(FoundryError): witness.recover()
        with self.assertRaises(FoundryError):
            with witness.guard_for_dispatch(registry): pass
        other = SessionWitness(registry,RetainedProcessRegistry(FakeWitnessApi(),lambda view:True),process,'3'*32)
        with self.assertRaises(FoundryError): other.acquire()
        ticket._lease.close()

    def test_lost_attachment_lock_cleanup_does_not_authorize_dispatch(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        provider = RetainedProcessRegistry(FakeWitnessApi(),lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,provider,process,'2'*32)
        ticket = authority.claim_bound(registry,journal,witness)
        held = registry._hold_events
        @contextmanager
        def lose_cleanup():
            with held() as state:
                yield state
            raise OSError('lost fixture lock cleanup')
        with mock.patch.object(registry,'_hold_events',side_effect=lose_cleanup):
            with self.assertRaises(FoundryError) as error: authority.acquire_bound(ticket)
        self.assertEqual('global_witness_unresolved',error.exception.code)
        self.assertTrue(error.exception.details['no_replay'])
        self.assertEqual('global_attachment_observed',authority.inspect_attachment(identity())['outcome'])
        with self.assertRaises(FoundryError):
            with witness.guard_for_dispatch(registry): pass
        with self.assertRaises(FoundryError): authority.acquire_bound(ticket)
        ticket._lease.close()

    def test_wrong_witness_cannot_attach_to_reserved_session(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        provider = RetainedProcessRegistry(FakeWitnessApi(),lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,provider,process,'2'*32)
        ticket = authority.claim_bound(registry,journal,witness)
        other_provider = RetainedProcessRegistry(FakeWitnessApi(),lambda view:True)
        other = SessionWitness(registry,other_provider,process,'2'*32)
        with self.assertRaises(FoundryError): other.acquire()
        self.assertFalse(ticket._acquisition_attempted)
        self.assertEqual(1,len(list((self.root/'Sims4Foundry'/'live-authority-v1'/'lifetimes').iterdir())))

    def test_lost_attachment_reply_inspects_original_without_repeat(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        provider = RetainedProcessRegistry(FakeWitnessApi(),lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,provider,process,'2'*32)
        ticket = authority.claim_bound(registry,journal,witness)
        publish = authority.new_json
        def lose_reply(path, row):
            publish(path,row)
            raise OSError('lost fixture reply')
        with mock.patch.object(authority,'new_json',side_effect=lose_reply):
            with self.assertRaises(FoundryError) as error: authority.acquire_bound(ticket)
        self.assertEqual('global_witness_unresolved',error.exception.code)
        self.assertTrue(error.exception.details['no_replay'])
        self.assertEqual('global_attachment_observed',authority.inspect_attachment(identity())['outcome'])
        with self.assertRaises(FoundryError): authority.acquire_bound(ticket)
        ticket._lease.close()

    def test_reconstructed_wrapper_cannot_resolve_failed_global_attachment(self):
        journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        registry.open_session('1'*32,registered_input.journal_binding(journal))
        api = FakeWitnessApi()
        provider = RetainedProcessRegistry(api,lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        witness = SessionWitness(registry,provider,process,'2'*32)
        ticket = authority.claim_bound(registry,journal,witness)
        with mock.patch.object(authority,'new_json',side_effect=OSError('fixture attachment failed')):
            with self.assertRaises(FoundryError): authority.acquire_bound(ticket)
        self.assertEqual('global_acquire',registry.inspect()['events'][-1]['operation'])
        replacement = SessionWitness(registry,provider,process,'2'*32)
        with self.assertRaises(FoundryError): replacement.recover()
        with self.assertRaises(FoundryError):
            with replacement.guard_for_dispatch(registry): pass
        self.assertEqual('attempt',registry.inspect()['events'][-1]['kind'])
        self.assertEqual(1,api.opens)
        ticket._lease.close()

    def test_tampered_reservation_is_refused_without_replacement(self):
        result = self.claim()
        path = self.root/'Sims4Foundry'/'live-authority-v1'/'lifetimes'/(result['scope']+'.json')
        path.write_text('{}',encoding='utf-8')
        with self.assertRaises(FoundryError): authority.inspect(identity())
        with self.assertRaises(FoundryError): self.claim('3'*32,'4'*32)
        self.assertEqual('{}',path.read_text())


if __name__ == '__main__': unittest.main()
