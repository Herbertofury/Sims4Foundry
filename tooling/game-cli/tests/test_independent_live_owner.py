"""Live-owner orchestration with fabricated process, recorder and native API."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sims4foundry_cli import independent_live_owner, input_journal, live_input, registered_input
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import Message
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
                     mock.patch.object(independent_live_owner,'_fixture_pid',return_value=identity()['pid']),
                     mock.patch.object(live_input.process_lease,'process_lease',return_value=FakeLease()),
                     mock.patch.object(live_input,'WgcRecorder',PngRecorder),
                     mock.patch.object(live_input,'os',SimpleNamespace(name='nt'))):
            item.start()
            self.addCleanup(item.stop)
        self.owner = independent_live_owner.LiveOwner(self.registry,self.journal,self.witness,'ffmpeg.exe','a'*64,
                           _fixture_admission=independent_live_owner._OWNED_FIXTURE_ADMISSION)
        def close_original():
            if self.owner._backend_open and not self.owner._close_attempted:
                self.owner.close_sender()
        self.addCleanup(close_original)

    def click(self, request='2'*32, output='evidence'):
        return self.owner.click_once(request,20,30,expected_observation={'mode':'cas'},
                                     evidence_directory=self.root/output)

    def test_live_admission_is_denied_and_fixture_is_same_process_only(self):
        with self.assertRaises(FoundryError) as error:
            independent_live_owner.LiveOwner(self.registry,self.journal,self.witness,'ffmpeg.exe','a'*64)
        self.assertEqual('live_authority_unqualified',error.exception.code)
        with mock.patch.object(independent_live_owner,'_fixture_pid',return_value=999):
            with self.assertRaises(FoundryError) as error:
                independent_live_owner.LiveOwner(self.registry,self.journal,self.witness,'ffmpeg.exe','a'*64,
                    _fixture_admission=independent_live_owner._OWNED_FIXTURE_ADMISSION)
        self.assertEqual('live_fixture_only',error.exception.code)
        self.assertEqual([],self.api.posts)

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
        with mock.patch.object(independent_live_owner.input_frame_evidence,'capture',
                               side_effect=FoundryError('output_exists','fixture')):
            with self.assertRaises(FoundryError) as error: self.click()
        self.assertEqual('after_admission_entry',error.exception.details['phase'])
        self.assertTrue(error.exception.details['input_may_have_been_sent'])
        self.assertTrue(error.exception.details['no_replay'])
        self.assertEqual('output_exists',error.exception.details['original_error_code'])
        self.assertEqual('awaiting_readback',self.journal.status('2'*32)['state'])
        with self.assertRaises(FoundryError): self.click(output='other')
        self.assertEqual(2,len(self.api.posts))

    def test_setup_failure_leaves_shared_attempt_fenced(self):
        with mock.patch.object(PngRecorder,'start',side_effect=OSError('fixture setup failure')):
            with self.assertRaises(FoundryError) as error: self.click()
        self.assertTrue(error.exception.details['no_replay'])
        self.assertEqual('attempt',self.registry.inspect()['events'][-1]['kind'])
        self.assertEqual([],self.api.posts)
        new_owner = independent_live_owner.LiveOwner(self.registry,self.journal,self.witness,'ffmpeg.exe','a'*64,
                           _fixture_admission=independent_live_owner._OWNED_FIXTURE_ADMISSION)
        with mock.patch.object(independent_live_owner,'LiveInputBackend') as backend:
            with self.assertRaises(FoundryError):
                new_owner.click_once('3'*32,20,30,expected_observation={'mode':'cas'},
                                     evidence_directory=self.root/'other')
            backend.assert_not_called()

    def test_unresolved_input_result_exposes_no_replay_and_uncertainty(self):
        self.api.on_post = lambda packet: False
        result = self.click()
        self.assertEqual('input_unresolved',result['outcome'])
        self.assertEqual('after_admission_entry',result['phase'])
        self.assertTrue(result['input_may_have_been_sent'])
        self.assertTrue(result['no_replay'])

    def test_bounded_drag_sends_original_down_move_and_release(self):
        result = self.owner.drag_once('2'*32,[[20,30],[40,50]],5,
                                     expected_observation={'mode':'build'},evidence_directory=self.root/'evidence')
        self.assertEqual('frame_recorded',result['outcome'])
        self.assertEqual([Message.LEFT_DOWN,Message.MOUSE_MOVE,Message.LEFT_UP],
                         [packet.message for packet in self.api.packets])
        self.assertTrue(result['no_replay'])

    def test_navigation_key_uses_runtime_mapping_and_original_release(self):
        self.api.mapping = lambda key,layout:15
        result = self.owner.key_once('2'*32,'TAB',15,False,
                                    expected_observation={'mode':'cas'},evidence_directory=self.root/'evidence')
        self.assertEqual('frame_recorded',result['outcome'])
        self.assertEqual([Message.KEY_DOWN,Message.KEY_UP],[packet.message for packet in self.api.packets])
        self.assertTrue(result['no_replay'])

    def test_right_click_uses_bound_button_release(self):
        result = self.owner.click_once('2'*32,20,30,button='right',
                                       expected_observation={'mode':'live'},evidence_directory=self.root/'evidence')
        self.assertEqual('frame_recorded',result['outcome'])
        self.assertEqual([Message.RIGHT_DOWN,Message.RIGHT_UP],[packet.message for packet in self.api.packets])

    def test_wheel_is_one_screen_bound_packet(self):
        origin = identity()['client_origin']
        result = self.owner.wheel_once('2'*32,origin[0]+20,origin[1]+30,120,
                                       expected_observation={'mode':'cas'},evidence_directory=self.root/'evidence')
        self.assertEqual('frame_recorded',result['outcome'])
        self.assertEqual([Message.MOUSE_WHEEL],[packet.message for packet in self.api.packets])
        self.assertTrue(result['no_replay'])

    def test_interrupt_after_sender_enter_keeps_original_sender_quarantined(self):
        original = live_input.LiveInputBackend.__enter__
        def interrupt(backend):
            original(backend)
            raise KeyboardInterrupt()
        with mock.patch.object(live_input.LiveInputBackend,'__enter__',interrupt):
            with self.assertRaises(KeyboardInterrupt): self.click()
        backend = self.owner._backend
        self.assertTrue(any(item is backend for item in independent_live_owner._UNRESOLVED_SENDERS))
        self.assertFalse(self.owner._backend_open)
        self.assertEqual('attempt',self.registry.inspect()['events'][-1]['kind'])
        with self.assertRaises(FoundryError): self.owner.close_sender()
        # This fixture knows the injected interruption happened after entry;
        # its fake API/recorder can be closed directly without native I/O.
        backend.__exit__(None,None,None)
        independent_live_owner._UNRESOLVED_SENDERS.remove(backend)

    def test_lost_journal_reply_can_recover_only_original_retained_sender(self):
        original = self.journal.execute_once
        def lose_reply(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('lost fixture reply')
        with mock.patch.object(self.journal,'execute_once',side_effect=lose_reply):
            with self.assertRaises(FoundryError): self.click()
        result = self.owner.recover_frame_binding('2'*32)
        self.assertEqual('same_owner_frame_binding_available',result['state'])
        captured = self.owner.capture_frame('2'*32,self.root/'recovered-evidence')
        self.assertEqual('frame_recorded',captured['outcome'])
        self.assertEqual(2,len(self.api.posts))
        self.assertFalse(result['acknowledged'])

    def test_existing_request_is_refused_before_opening_backend(self):
        self.click()
        new_owner = independent_live_owner.LiveOwner(self.registry,self.journal,self.witness,'ffmpeg.exe','a'*64,
                           _fixture_admission=independent_live_owner._OWNED_FIXTURE_ADMISSION)
        with mock.patch.object(independent_live_owner,'LiveInputBackend') as backend:
            with self.assertRaises(FoundryError):
                new_owner.click_once('2'*32,20,30,expected_observation={'mode':'cas'},
                                     evidence_directory=self.root/'other')
            backend.assert_not_called()
        self.assertEqual(2,len(self.api.posts))


if __name__ == '__main__': unittest.main()
