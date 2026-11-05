"""Live-owner orchestration with fabricated process, recorder and native API."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sims4foundry_cli import independent_live_owner, input_journal, live_input, registered_input
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.retained_process_witness import RetainedProcessRegistry
from sims4foundry_cli.session_registry import SessionRegistry
from sims4foundry_cli.session_witness import SessionWitness
from test_guarded_input import identity
from test_input_frame_evidence import PngRecorder
from test_live_input import FakeApi, FakeLease
from test_session_registry import FakeAnchor
from test_session_witness import FakeApi as FakeWitnessApi


class IndependentLiveOwnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        self.registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        self.registry.open_session('1'*32,registered_input.journal_binding(self.journal))
        self.provider = RetainedProcessRegistry(FakeWitnessApi(),lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        self.witness = SessionWitness(self.registry,self.provider,process,'4'*32)
        self.witness.acquire()
        self.api = FakeApi()
        for item in (mock.patch.object(live_input,'_NativeApi',return_value=self.api),
                     mock.patch.object(live_input.process_lease,'process_lease',return_value=FakeLease()),
                     mock.patch.object(live_input,'WgcRecorder',PngRecorder),
                     mock.patch.object(live_input,'os',SimpleNamespace(name='nt'))):
            item.start()
            self.addCleanup(item.stop)
        self.owner = independent_live_owner.LiveOwner(self.registry,self.journal,self.witness,'ffmpeg.exe','a'*64)

    def click(self, request='2'*32, output='evidence'):
        return self.owner.click_once(request,20,30,expected_observation={'mode':'cas'},
                                     evidence_directory=self.root/output)

    def test_click_records_frame_without_ack_and_blocks_replay_and_next_input(self):
        result = self.click()
        self.assertEqual('frame_recorded',result['outcome'])
        self.assertFalse(result['acknowledged'])
        self.assertFalse(result['semantic_state_verified'])
        self.assertFalse(result['game_qualified'])
        self.assertEqual('awaiting_readback',self.journal.status('2'*32)['state'])
        self.assertEqual(2,len(self.api.posts))
        with self.assertRaises(FoundryError): self.click(output='other')
        with self.assertRaises(FoundryError): self.click('3'*32,'other')
        self.assertEqual(2,len(self.api.posts))

    def test_lost_evidence_result_never_reposts_original_click(self):
        with mock.patch.object(independent_live_owner.input_frame_evidence,'capture',side_effect=OSError('lost reply')):
            with self.assertRaises(FoundryError): self.click()
        self.assertEqual('awaiting_readback',self.journal.status('2'*32)['state'])
        with self.assertRaises(FoundryError): self.click(output='other')
        self.assertEqual(2,len(self.api.posts))

    def test_existing_request_is_refused_before_opening_backend(self):
        self.click()
        new_owner = independent_live_owner.LiveOwner(self.registry,self.journal,self.witness,'ffmpeg.exe','a'*64)
        with mock.patch.object(independent_live_owner,'LiveInputBackend') as backend:
            with self.assertRaises(FoundryError):
                new_owner.click_once('2'*32,20,30,expected_observation={'mode':'cas'},
                                     evidence_directory=self.root/'other')
            backend.assert_not_called()
        self.assertEqual(2,len(self.api.posts))


if __name__ == '__main__': unittest.main()
