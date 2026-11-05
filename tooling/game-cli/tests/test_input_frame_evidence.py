"""Passive same-recorder frame evidence using only synthetic providers."""
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from sims4foundry_cli import frame_evidence_cli, input_frame_evidence, input_journal, live_input, registered_input, visual_probe
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import ClientBounds, Point, encode_click
from sims4foundry_cli.wgc_recorder import Frame
from test_guarded_input import identity
from test_live_input import FakeApi, FakeLease, FakeRecorder
from test_windows_cli import png
from sims4foundry_cli.session_registry import SessionRegistry
from sims4foundry_cli.session_witness import SessionWitness
from sims4foundry_cli.retained_process_witness import RetainedProcessRegistry
from test_session_registry import FakeAnchor
from test_session_witness import FakeApi as FakeWitnessApi
from test_visual_probe import rgba


class PngRecorder(FakeRecorder):
    def sample(self):
        self.sequence += 1
        raw = png()
        return Frame(self.epoch,self.sequence,time.monotonic(),hashlib.sha256(raw).hexdigest(),2,2,raw)


class InputFrameEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = input_journal.InputJournal.create(self.root/'journal','1'*32,identity())
        (self.root/'registry').mkdir()
        self.anchor = FakeAnchor()
        self.registry = SessionRegistry(self.root/'registry',identity(),b'k'*32,self.anchor)
        self.registry.open_session('1'*32,registered_input.journal_binding(self.history))
        self.provider = RetainedProcessRegistry(FakeWitnessApi(),lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        self.witness = SessionWitness(self.registry,self.provider,process,'4'*32)
        self.lease = self.witness.acquire()
        self.packets = encode_click(Point(20,30),ClientBounds(500,500))
        self.api = FakeApi()
        for patcher in (mock.patch.object(live_input,'_NativeApi',return_value=self.api),
                        mock.patch.object(live_input.process_lease,'process_lease',return_value=FakeLease()),
                        mock.patch.object(live_input,'WgcRecorder',PngRecorder),
                        mock.patch.object(live_input,'os',SimpleNamespace(name='nt'))):
            patcher.start(); self.addCleanup(patcher.stop)
    def backend(self):
        return live_input.LiveInputBackend(identity(),self.packets,'ffmpeg.exe','a'*64)
    def dispatch(self, backend):
        return registered_input.execute_once(self.registry,self.history,'2'*32,self.packets,backend,
                                             expected_observation={'mode':'cas'},witness=self.witness)
    def test_different_backend_for_same_target_is_refused(self):
        with self.backend() as original:
            self.dispatch(original)
            with self.backend() as foreign:
                with self.assertRaises(FoundryError):
                    input_frame_evidence.capture(self.history,'2'*32,foreign,self.root/'evidence')
        self.assertFalse((self.root/'evidence').exists())
    def test_replaced_recorder_or_epoch_is_refused(self):
        with self.backend() as backend:
            self.dispatch(backend)
            original = backend._recorder
            backend._recorder = object()
            with self.assertRaises(FoundryError):
                input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
            backend._recorder = original
            epoch = backend._frame_epoch
            backend._frame_epoch = object()
            with self.assertRaises(FoundryError):
                input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
            backend._frame_epoch = epoch
        self.assertFalse((self.root/'evidence').exists())
    def test_direct_journal_dispatch_has_no_registered_binding(self):
        with self.backend() as backend:
            self.history.execute_once('2'*32,self.packets,backend,expected_observation={'mode':'cas'})
            with self.assertRaises(FoundryError):
                input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
        self.assertFalse((self.root/'evidence').exists())
    def test_changed_plan_after_dispatch_is_refused(self):
        with self.backend() as backend:
            self.dispatch(backend)
            backend._packets = encode_click(Point(40,40),ClientBounds(500,500))
            with self.assertRaises(FoundryError):
                input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
        self.assertFalse((self.root/'evidence').exists())
    def test_plan_mismatch_refused_before_registry_attempt(self):
        with self.backend() as backend:
            backend._packets = encode_click(Point(40,40),ClientBounds(500,500))
            with self.assertRaises(FoundryError): self.dispatch(backend)
        self.assertEqual([],self.api.posts)
        self.assertEqual('resolved',self.registry.inspect()['events'][-1]['kind'])
    def test_same_owner_recovers_frame_binding_after_lost_journal_reply(self):
        with self.backend() as backend:
            original = self.history.execute_once
            def lose_reply(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError('lost fixture reply')
            with mock.patch.object(self.history,'execute_once',side_effect=lose_reply):
                with self.assertRaises(OSError): self.dispatch(backend)
            self.assertEqual('awaiting_readback',self.history.status('2'*32)['state'])
            posts = list(self.api.posts)
            with self.assertRaises(FoundryError):
                input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
            recovered = registered_input.reconcile_frame_binding(
                self.registry,self.history,'2'*32,backend,witness=self.witness)
            self.assertEqual('same_owner_frame_binding_available',recovered['state'])
            self.assertFalse(recovered['input_sent'])
            self.assertFalse(recovered['acknowledged'])
            input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
            self.assertEqual(posts,self.api.posts)
            self.assertEqual('awaiting_readback',self.history.status('2'*32)['state'])
    def test_recovery_refuses_foreign_sender_or_closed_witness(self):
        with self.backend() as backend:
            original = self.history.execute_once
            def lose_reply(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError('lost fixture reply')
            with mock.patch.object(self.history,'execute_once',side_effect=lose_reply):
                with self.assertRaises(OSError): self.dispatch(backend)
            posts = list(self.api.posts)
            with self.backend() as foreign:
                with self.assertRaises(FoundryError):
                    registered_input.reconcile_frame_binding(self.registry,self.history,'2'*32,foreign,witness=self.witness)
            self.lease.close()
            with self.assertRaises(FoundryError):
                registered_input.reconcile_frame_binding(self.registry,self.history,'2'*32,backend,witness=self.witness)
            self.assertEqual(posts,self.api.posts)
    def test_recovery_refuses_a_different_held_witness_for_the_same_lifetime(self):
        second_provider = RetainedProcessRegistry(FakeWitnessApi(),lambda view:True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        second_witness = SessionWitness(self.registry,second_provider,process,'5'*32)
        second_witness.acquire()
        with self.backend() as backend:
            original = self.history.execute_once
            def lose_reply(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError('lost fixture reply')
            with mock.patch.object(self.history,'execute_once',side_effect=lose_reply):
                with self.assertRaises(OSError): self.dispatch(backend)
            posts = list(self.api.posts)
            self.lease.close()
            with self.assertRaises(FoundryError):
                registered_input.reconcile_frame_binding(self.registry,self.history,'2'*32,backend,witness=second_witness)
            with self.assertRaises(FoundryError):
                input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
            self.assertEqual(posts,self.api.posts)
    def test_failed_admission_cannot_recover_frame_binding(self):
        with self.backend() as backend:
            self.anchor.fail = 'before'
            with self.assertRaises(FoundryError): self.dispatch(backend)
            with self.assertRaises(FoundryError):
                registered_input.reconcile_frame_binding(self.registry,self.history,'2'*32,backend,witness=self.witness)
            self.assertEqual([],self.api.posts)
    def test_recovery_requires_original_attempt_to_remain_pending(self):
        with self.backend() as backend:
            original = self.history.execute_once
            def lose_reply(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError('lost fixture reply')
            with mock.patch.object(self.history,'execute_once',side_effect=lose_reply):
                with self.assertRaises(OSError): self.dispatch(backend)
            current = self.history.status('2'*32)
            self.registry.resolve('2'*32,current['intent_sha256'],'a'*64,'acknowledged')
            posts = list(self.api.posts)
            with self.assertRaises(FoundryError):
                registered_input.reconcile_frame_binding(self.registry,self.history,'2'*32,backend,witness=self.witness)
            self.assertEqual(posts,self.api.posts)
    def test_frame_is_saved_without_acknowledging_or_claiming_causality(self):
        with self.backend() as backend:
            self.assertEqual('awaiting_readback',self.dispatch(backend)['state'])
            result=input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
            self.assertEqual('frame_recorded',result['outcome'])
            self.assertFalse(result['capture_time_verified'])
            self.assertFalse(result['action_caused_verified'])
            self.assertFalse(result['semantic_state_verified'])
            self.assertFalse(result['acknowledged'])
            self.assertEqual(png(),(self.root/'evidence'/'frame.png').read_bytes())
            self.assertEqual('awaiting_readback',self.history.status('2'*32)['state'])
            inspected = input_frame_evidence.inspect(self.history,'2'*32,self.root/'evidence')
            self.assertEqual('matching_files_observed',inspected['state'])
            self.assertFalse(inspected['atomic_snapshot'])
            self.assertFalse(inspected['acknowledged'])
            with self.assertRaises(FoundryError):
                input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
    def test_unknown_request_never_creates_evidence(self):
        with self.backend() as backend:
            with self.assertRaises(FoundryError):
                input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
        self.assertFalse((self.root/'evidence').exists())
        self.assertEqual([],self.api.posts)
    def test_partial_write_preserves_pending_journal_and_no_completion(self):
        with self.backend() as backend:
            self.dispatch(backend)
            with mock.patch.object(input_frame_evidence.companion_build,'_publish_archive',side_effect=OSError('fixture')):
                with self.assertRaises(OSError):
                    input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
        self.assertTrue((self.root/'evidence'/'evidence-intent.json').exists())
        self.assertFalse((self.root/'evidence'/'evidence-complete.json').exists())
        before = sorted(path.name for path in (self.root/'evidence').iterdir())
        inspected = input_frame_evidence.inspect(self.history,'2'*32,self.root/'evidence')
        self.assertEqual('incomplete_or_unavailable',inspected['state'])
        self.assertEqual([True,False,False],inspected['files_observed'])
        self.assertEqual(before,sorted(path.name for path in (self.root/'evidence').iterdir()))
        self.assertEqual('awaiting_readback',self.history.status('2'*32)['state'])
    def test_inspection_refuses_changed_frame_and_receipt(self):
        with self.backend() as backend:
            self.dispatch(backend)
            input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
        frame = self.root/'evidence'/'frame.png'
        original = frame.read_bytes()
        frame.write_bytes(original+b'x')
        with self.assertRaises(FoundryError):
            input_frame_evidence.inspect(self.history,'2'*32,self.root/'evidence')
        frame.write_bytes(original)
        receipt = self.root/'evidence'/'evidence-complete.json'
        receipt.write_text('{}',encoding='utf-8')
        with self.assertRaises(FoundryError):
            input_frame_evidence.inspect(self.history,'2'*32,self.root/'evidence')
        self.assertEqual('awaiting_readback',self.history.status('2'*32)['state'])
    def test_inspection_of_missing_directory_never_creates_it(self):
        with self.backend() as backend:
            self.dispatch(backend)
        result = input_frame_evidence.inspect(self.history,'2'*32,self.root/'missing')
        self.assertEqual('incomplete_or_unavailable',result['state'])
        self.assertEqual([False,False,False],result['files_observed'])
        self.assertFalse((self.root/'missing').exists())
    def test_inspection_after_lost_completion_reply_does_not_repeat_input(self):
        with self.backend() as backend:
            self.dispatch(backend)
            write = input_frame_evidence.new_json
            def lose_reply(path, value):
                write(path,value)
                if path.name == 'evidence-complete.json':
                    raise OSError('lost fixture reply')
            with mock.patch.object(input_frame_evidence,'new_json',side_effect=lose_reply):
                with self.assertRaises(OSError):
                    input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
            posts = list(self.api.posts)
            result = input_frame_evidence.inspect(self.history,'2'*32,self.root/'evidence')
            self.assertEqual('matching_files_observed',result['state'])
            self.assertEqual(posts,self.api.posts)
            self.assertEqual('awaiting_readback',self.history.status('2'*32)['state'])
    def test_comparison_is_pinned_and_does_not_acknowledge(self):
        with self.backend() as backend:
            self.dispatch(backend)
            input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
            observed = input_frame_evidence.inspect(self.history,'2'*32,self.root/'evidence')
            reference = self.root/'reference.png'
            reference.write_bytes(png())
            pin = hashlib.sha256(reference.read_bytes()).hexdigest()
            args = (self.history,'2'*32,self.root/'evidence',observed['frame_sha256'],
                    observed['evidence_intent_sha256'],reference,pin,(0,0,2,2))
            posts = list(self.api.posts)
            result = input_frame_evidence.compare(*args)
            self.assertTrue(result['matched'])
            self.assertFalse(result['capture_time_verified'])
            self.assertFalse(result['action_caused_verified'])
            self.assertFalse(result['semantic_state_verified'])
            self.assertFalse(result['acknowledged'])
            self.assertEqual(posts,self.api.posts)
            self.assertEqual('awaiting_readback',self.history.status('2'*32)['state'])
            wrong = list(args)
            wrong[4] = 'f'*64
            with self.assertRaises(FoundryError): input_frame_evidence.compare(*wrong)
            wrong = list(args)
            wrong[6] = 'f'*64
            with self.assertRaises(FoundryError): input_frame_evidence.compare(*wrong)
            reference.write_bytes(rgba(2,2,(0,0,0,255)))
            different = list(args)
            different[6] = hashlib.sha256(reference.read_bytes()).hexdigest()
            self.assertFalse(input_frame_evidence.compare(*different)['matched'])
            self.assertEqual(posts,self.api.posts)
            original_compare = visual_probe.compare
            def change_during_compare(*values, **kwargs):
                result = original_compare(*values, **kwargs)
                (self.root/'evidence'/'evidence-complete.json').write_text('{}',encoding='utf-8')
                return result
            with mock.patch.object(visual_probe,'compare',side_effect=change_during_compare):
                with self.assertRaises(FoundryError): input_frame_evidence.compare(*different)
    def test_read_only_cli_inspects_and_compares_original_evidence(self):
        with self.backend() as backend:
            self.dispatch(backend)
            input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
        args = ['--journal',str(self.root/'journal'),'--request-id','2'*32,'--evidence',str(self.root/'evidence')]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0,frame_evidence_cli.main(['inspect',*args]))
        observed = json.loads(output.getvalue())
        self.assertEqual('matching_files_observed',observed['state'])
        reference = self.root/'reference.png'
        reference.write_bytes(png())
        compare_args = ['--frame-sha256',observed['frame_sha256'],
                        '--evidence-intent-sha256',observed['evidence_intent_sha256'],
                        '--reference',str(reference),'--reference-sha256',hashlib.sha256(png()).hexdigest(),
                        '--region','0','0','2','2']
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0,frame_evidence_cli.main(['compare',*args,*compare_args]))
        self.assertTrue(json.loads(output.getvalue())['matched'])
        self.assertEqual('awaiting_readback',self.history.status('2'*32)['state'])
        self.assertEqual(2,len(self.api.posts))
    def test_cli_incomplete_evidence_and_invalid_journal_have_nonzero_results(self):
        with self.backend() as backend:
            self.dispatch(backend)
        args = ['inspect','--journal',str(self.root/'journal'),'--request-id','2'*32,
                '--evidence',str(self.root/'missing')]
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(1,frame_evidence_cli.main(args))
        self.assertEqual('incomplete_or_unavailable',json.loads(output.getvalue())['state'])
        args[2] = str(self.root/'unknown-journal')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(2,frame_evidence_cli.main(args))
    def test_invalid_frame_is_refused_before_output(self):
        with self.backend() as backend:
            self.dispatch(backend)
            with mock.patch.object(backend,'_recording_frame',return_value=Frame(object(),1,time.monotonic(),'a'*64,2,2,b'invalid')):
                with self.assertRaises(FoundryError):
                    input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
        self.assertFalse((self.root/'evidence').exists())
    def test_changed_target_geometry_is_refused_before_output(self):
        with self.backend() as backend:
            self.dispatch(backend)
            original = self.api.geometry
            def changed(expected):
                value = original(expected)
                value['client_width'] += 1
                return value
            self.api.geometry = changed
            with self.assertRaises(FoundryError):
                input_frame_evidence.capture(self.history,'2'*32,backend,self.root/'evidence')
        self.assertFalse((self.root/'evidence').exists())
        self.assertEqual('awaiting_readback',self.history.status('2'*32)['state'])


if __name__ == '__main__': unittest.main()
