"""Passive same-recorder frame evidence using only synthetic providers."""
import hashlib
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from sims4foundry_cli import input_frame_evidence, input_journal, live_input
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import ClientBounds, Point, encode_click
from sims4foundry_cli.wgc_recorder import Frame
from test_guarded_input import identity
from test_live_input import FakeApi, FakeLease, FakeRecorder
from test_windows_cli import png


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
        return self.history.execute_once('2'*32,self.packets,backend,expected_observation={'mode':'cas'})
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
        self.assertEqual('awaiting_readback',self.history.status('2'*32)['state'])
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
