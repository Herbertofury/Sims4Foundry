"""CLI recording uses synthetic frames and a fake recorder, never a game."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sims4foundry_cli import wgc_record_cli as cli
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.wgc_recorder import Frame
from test_windows_cli import identity, png


class FakeRecorder:
    def __init__(self):
        self.started, self.closed, self.close_result = 0, 0, True
        self.sequence, self.bad, self.interrupt = 0, False, False
    def start(self): self.started += 1
    def sample(self):
        if self.interrupt: raise KeyboardInterrupt()
        self.sequence += 1
        raw = png()
        if self.bad: raw = raw[:40] + b'X' + raw[41:]
        return Frame(object(), self.sequence, 1.0, hashlib.sha256(raw).hexdigest(), 2, 2, raw)
    def close(self):
        self.closed += 1
        return self.close_result


class RecordingCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.recorder = FakeRecorder()
        self.now = 0.
    def factory(self, *args): return self.recorder
    def clock(self): return self.now
    def sleep(self, seconds): self.now += seconds
    def run_record(self, **kwargs):
        return cli.record_frames(identity(), 'ffmpeg.exe', 'a'*64, self.root/'output',
                                 recorder_factory=self.factory, clock=self.clock, sleep=self.sleep, **kwargs)
    def test_records_bounded_frames_and_completion(self):
        result = self.run_record(frames=2)
        self.assertTrue(result['ok'])
        self.assertEqual(2,len(result['frames']))
        self.assertFalse(result['semantic_state_verified'])
        self.assertFalse(result['capture_time_verified'])
        self.assertEqual(1,self.recorder.closed)
        self.assertTrue((self.root/'output'/'recording-complete.json').is_file())
        self.assertEqual(png(),(self.root/'output'/'frame-0001.png').read_bytes())
        with self.assertRaises(FoundryError): self.run_record()
    def test_bad_frame_does_not_write_completion(self):
        self.recorder.bad = True
        result = self.run_record(frames=1)
        self.assertEqual('incomplete',result['outcome'])
        self.assertEqual(1,self.recorder.closed)
        self.assertFalse((self.root/'output'/'recording-complete.json').exists())
    def test_cleanup_failure_never_writes_completion(self):
        self.recorder.close_result = False
        result = self.run_record(frames=1)
        self.assertEqual('unresolved',result['outcome'])
        self.assertEqual('recording_cleanup_unresolved',result['code'])
        self.assertFalse((self.root/'output'/'recording-complete.json').exists())
    def test_user_interrupt_still_closes_owned_recorder(self):
        self.recorder.interrupt = True
        with self.assertRaises(KeyboardInterrupt): self.run_record()
        self.assertEqual(1,self.recorder.closed)
        self.assertFalse((self.root/'output'/'recording-complete.json').exists())
    def test_stalled_or_duplicate_frame_times_out(self):
        self.recorder.sample = lambda: (_ for _ in ()).throw(FoundryError('recording_unavailable','fixture'))
        result = self.run_record(frames=1,seconds=.1)
        self.assertEqual('recording_timeout',result['code'])
        self.assertEqual(1,self.recorder.closed)
    def test_invalid_arguments_create_no_output(self):
        for options in ({'frames':0},{'frames':11},{'seconds':0},{'seconds':float('nan')}):
            with self.subTest(options=options), self.assertRaises(FoundryError):
                self.run_record(**options)
        self.assertFalse((self.root/'output').exists())
    def test_factory_cannot_rebind_recorded_target(self):
        def rebind(value,*args):
            value['hwnd'] = 999
            return self.recorder
        with self.assertRaises(FoundryError):
            cli.record_frames(identity(),'ffmpeg.exe','a'*64,self.root/'output',
                              recorder_factory=rebind)
        self.assertFalse((self.root/'output').exists())
        self.assertEqual(0,self.recorder.started)
    def test_module_cli_prints_structured_result(self):
        source = self.root/'identity.json'
        source.write_text(json.dumps(identity()))
        text = io.StringIO()
        with mock.patch.object(cli, 'WgcRecorder', side_effect=self.factory), redirect_stdout(text):
            code = cli.main(['--identity',str(source),'--ffmpeg','ffmpeg.exe',
                             '--ffmpeg-sha256','a'*64,'--output',str(self.root/'output'),
                             '--frames','1'])
        self.assertEqual(0,code)
        self.assertEqual('frames_recorded',json.loads(text.getvalue())['outcome'])
