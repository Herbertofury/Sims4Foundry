"""Foreground owner protocol tests with fabricated native and recorder APIs."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sims4foundry_cli import cli, independent_live_owner, independent_live_service, input_journal, live_input, native_input, process_exit, registered_input
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.session_registry import SessionRegistry
from test_guarded_input import identity
from test_input_frame_evidence import PngRecorder
from test_live_input import FakeApi, FakeLease
from test_session_registry import FakeAnchor
from test_session_witness import FakeApi as FakeWitnessApi


class LiveServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = self.root/'session'
        self.session.mkdir()
        self.journal = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        self.registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,FakeAnchor())
        self.registry.open_session('1'*32,registered_input.journal_binding(self.journal))
        self.api = FakeApi()
        original = {'identity':identity(),'session_id':'1'*32}
        for item in (mock.patch.object(independent_live_service.independent_session,'_open',
                                       return_value=(original,self.registry,self.journal,self.registry.inspect())),
                     mock.patch.object(independent_live_service,'_fixture_pid',return_value=identity()['pid']),
                     mock.patch.object(independent_live_owner,'_fixture_pid',return_value=identity()['pid']),
                     mock.patch.object(native_input,'_NativeApi',return_value=self.api),
                     mock.patch.object(process_exit,'_WindowsApi',return_value=FakeWitnessApi()),
                     mock.patch.object(live_input,'_NativeApi',return_value=self.api),
                     mock.patch.object(live_input.process_lease,'process_lease',return_value=FakeLease()),
                     mock.patch.object(live_input,'WgcRecorder',PngRecorder),
                     mock.patch.object(live_input,'os',SimpleNamespace(name='nt'))):
            item.start()
            self.addCleanup(item.stop)

    def run_service(self, commands, sink=None):
        raw = b''.join(json.dumps(command).encode()+b'\n' for command in commands)
        sink = sink or io.StringIO()
        result = independent_live_service.serve(self.root/'authority',self.session,'4'*32,'ffmpeg.exe','a'*64,
                                                input_stream=io.BytesIO(raw),output_stream=sink,
                                                _fixture_admission=independent_live_service._OWNED_FIXTURE_ADMISSION)
        return result,[json.loads(row) for row in sink.getvalue().splitlines()]

    def test_single_owner_click_status_and_explicit_stop(self):
        click = {'op':'click','request_id':'2'*32,'x':20,'y':30,'expectation':{'mode':'cas'},
                 'evidence_directory':str(self.root/'evidence')}
        result, rows = self.run_service([{'op':'status'},click,click,
                                         {'op':'input_status','request_id':'2'*32},
                                         {'op':'stop','abandon':True}])
        self.assertEqual('experimental_owner_ready',rows[0]['outcome'])
        self.assertEqual('frame_recorded',rows[2]['outcome'])
        self.assertTrue(rows[2]['no_replay'])
        self.assertEqual('live_owner_unresolved',rows[3]['code'])
        self.assertEqual('awaiting_readback',rows[4]['input']['state'])
        self.assertEqual('owner_stop_reported',rows[5]['outcome'])
        self.assertEqual('owner_stopped',result['outcome'])
        self.assertEqual(2,len(self.api.posts))
        self.assertTrue((self.session/'live-owner-intent.json').exists())
        self.assertTrue((self.session/'live-owner-stop-receipt.json').exists())
        with mock.patch.object(native_input,'_NativeApi') as api:
            with self.assertRaises(FoundryError): self.run_service([])
            api.assert_not_called()

    def test_service_routes_drag_with_pending_gate(self):
        command = {'op':'drag','request_id':'2'*32,'path':[[20,30],[40,50]],'duration_ms':5,
                   'expectation':{'mode':'build'},'evidence_directory':str(self.root/'evidence')}
        result, rows = self.run_service([command,{'op':'stop','abandon':True}])
        self.assertEqual('frame_recorded',rows[1]['outcome'])
        self.assertTrue(rows[1]['no_replay'])
        self.assertEqual(3,len(self.api.posts))
        self.assertEqual('owner_stopped',result['outcome'])

    def test_service_routes_navigation_key(self):
        self.api.mapping = lambda key,layout:15
        command = {'op':'key','request_id':'2'*32,'key':'TAB','scan_code':15,'extended':False,
                   'expectation':{'mode':'cas'},'evidence_directory':str(self.root/'evidence')}
        result, rows = self.run_service([command,{'op':'stop','abandon':True}])
        self.assertEqual('frame_recorded',rows[1]['outcome'])
        self.assertEqual(2,len(self.api.posts))
        self.assertEqual('owner_stopped',result['outcome'])

    def test_eof_retains_unresolved_owner_without_closing_or_reacquiring(self):
        result, rows = self.run_service([])
        self.assertEqual('owner_input_closed',result['outcome'])
        self.assertFalse(result['cleanup_verified'])
        retained = independent_live_service._UNRESOLVED[-1]
        self.assertIsNotNone(retained['lease'])
        self.assertFalse((self.session/'live-owner-stop-receipt.json').exists())
        # These are fabricated API handles; explicitly clean this known fixture.
        retained['lease'].close()
        retained['api'].close(retained['image'])
        independent_live_service._UNRESOLVED.remove(retained)

    def test_lost_click_reply_keeps_original_owner_and_blocks_restart(self):
        class LostSink(io.StringIO):
            def __init__(self): super().__init__(); self.count = 0
            def write(self, value):
                self.count += 1
                if self.count == 2: raise OSError('lost fixture reply')
                return super().write(value)
        click = {'op':'click','request_id':'2'*32,'x':20,'y':30,'expectation':{'mode':'cas'},
                 'evidence_directory':str(self.root/'evidence')}
        with self.assertRaises(OSError): self.run_service([click],LostSink())
        self.assertEqual(2,len(self.api.posts))
        self.assertEqual('awaiting_readback',self.journal.status('2'*32)['state'])
        retained = independent_live_service._UNRESOLVED[-1]
        self.assertTrue(retained['owner']._backend_open)
        with mock.patch.object(native_input,'_NativeApi') as api:
            with self.assertRaises(FoundryError): self.run_service([])
            api.assert_not_called()
        # Controlled fake fixture cleanup; this does not resolve the journal.
        retained['owner'].close_sender()
        retained['lease'].close()
        retained['api'].close(retained['image'])
        independent_live_service._UNRESOLVED.remove(retained)

    def test_failed_start_stays_reserved_and_reports_no_replay(self):
        with mock.patch.object(self.api,'pin_image',side_effect=OSError('fixture failure')):
            with self.assertRaises(FoundryError) as error: self.run_service([])
        self.assertEqual('live_owner_start_unresolved',error.exception.code)
        self.assertEqual('after_owner_intent',error.exception.details['phase'])
        self.assertTrue(error.exception.details['no_replay'])
        self.assertTrue((self.session/'live-owner-intent.json').exists())
        retained = independent_live_service._UNRESOLVED[-1]
        with mock.patch.object(native_input,'_NativeApi') as api:
            with self.assertRaises(FoundryError): self.run_service([])
            api.assert_not_called()
        independent_live_service._UNRESOLVED.remove(retained)

    def test_cli_requires_explicit_experimental_opt_in(self):
        output = io.StringIO()
        args = ['experimental-live-owner','--authority',str(self.root/'authority'),'--session',str(self.session),
                '--witness-id','4'*32,'--ffmpeg','ffmpeg.exe','--ffmpeg-sha256','a'*64]
        with redirect_stdout(output), mock.patch.object(native_input,'_NativeApi') as api:
            self.assertEqual(2,cli.main(args))
            api.assert_not_called()
        self.assertEqual('live_owner_unqualified',json.loads(output.getvalue())['code'])
        self.assertFalse((self.session/'live-owner-intent.json').exists())
        output = io.StringIO()
        with redirect_stdout(output), mock.patch.object(native_input,'_NativeApi') as api:
            self.assertEqual(2,cli.main([*args,'--allow-experimental']))
            api.assert_not_called()
        self.assertEqual('live_authority_unqualified',json.loads(output.getvalue())['code'])
        self.assertFalse((self.session/'live-owner-intent.json').exists())

    def test_lost_stop_intent_reply_fences_later_click_and_capture(self):
        write = independent_live_service.new_json
        def lose_stop_reply(path, value):
            write(path,value)
            if path.name == 'live-owner-stop-intent.json': raise OSError('lost fixture reply')
        click = {'op':'click','request_id':'2'*32,'x':20,'y':30,'expectation':{'mode':'cas'},
                 'evidence_directory':str(self.root/'evidence')}
        with mock.patch.object(independent_live_service,'new_json',side_effect=lose_stop_reply):
            result, rows = self.run_service([{'op':'stop','abandon':True},click,
                                             {'op':'capture_frame','request_id':'2'*32,
                                              'evidence_directory':str(self.root/'frame')},
                                             {'op':'status'}])
        self.assertEqual('live_owner_unresolved',rows[1]['code'])
        self.assertEqual('live_owner_stopping',rows[2]['code'])
        self.assertEqual('live_owner_stopping',rows[3]['code'])
        self.assertEqual('owner_status',rows[4]['outcome'])
        self.assertTrue(rows[4]['stop_attempted'])
        self.assertEqual([],self.api.posts)
        self.assertEqual('owner_input_closed',result['outcome'])
        retained = independent_live_service._UNRESOLVED[-1]
        retained['lease'].close()
        retained['api'].close(retained['image'])
        independent_live_service._UNRESOLVED.remove(retained)


if __name__ == '__main__': unittest.main()
