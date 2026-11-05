"""Receipt-bound visual readback tests use synthetic frames only."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from sims4foundry_cli import visual_readback, wgc_record_cli
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import sha256
from test_wgc_record_cli import FakeRecorder
from test_windows_cli import identity, png
from test_visual_probe import rgba


class VisualReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.recording=self.root/'recording'
        self.fake=FakeRecorder()
        result=wgc_record_cli.record_frames(identity(),'ffmpeg.exe','a'*64,self.recording,frames=2,
                                             recorder_factory=lambda *args:self.fake)
        self.assertTrue(result['ok'])
        self.receipt_hash=sha256(self.recording/'recording-complete.json')
        self.reference=self.root/'reference.png'
        self.reference.write_bytes(png())
        self.ref_hash=sha256(self.reference)
    def inspect(self, **kwargs):
        return visual_readback.inspect_recording(self.recording,self.receipt_hash,identity(),
               self.reference,self.ref_hash,(0,0,2,2),**kwargs)
    def test_receipt_bound_last_frame_matches_without_semantic_claim(self):
        result=self.inspect()
        self.assertTrue(result['matched'])
        self.assertEqual(2,result['frame_sequence'])
        self.assertFalse(result['caused_by_input_verified'])
        self.assertFalse(result['capture_time_verified'])
        self.assertFalse(result['semantic_state_verified'])
    def test_different_reference_is_a_valid_mismatch(self):
        self.reference.write_bytes(rgba(2,2,(0,0,0,255)))
        self.ref_hash=sha256(self.reference)
        result=self.inspect()
        self.assertFalse(result['matched'])
        self.assertEqual(4,result['mismatched_pixels'])
    def test_foreign_process_and_receipt_hash_are_refused(self):
        foreign=deepcopy(identity())
        foreign['creation_filetime'] += 1
        with self.assertRaises(FoundryError):
            visual_readback.inspect_recording(self.recording,self.receipt_hash,foreign,
                                              self.reference,self.ref_hash,(0,0,2,2))
        self.receipt_hash='b'*64
        with self.assertRaises(FoundryError): self.inspect()
    def test_frame_or_metadata_or_intent_tamper_refused(self):
        for name in ('frame-0002.png','frame-0002.json','recording-intent.json'):
            with self.subTest(name=name):
                path=self.recording/name
                original=path.read_bytes()
                path.write_bytes(original+b' ')
                try:
                    with self.assertRaises(FoundryError): self.inspect()
                finally:
                    path.write_bytes(original)
    def test_cli_writes_new_readback_and_refuses_overwrite(self):
        identity_path=self.root/'identity.json'
        identity_path.write_text(json.dumps(identity()))
        output=self.root/'readback.json'
        args=['--recording',str(self.recording),'--completion-sha256',self.receipt_hash,
              '--identity',str(identity_path),'--reference',str(self.reference),
              '--reference-sha256',self.ref_hash,'--region','0','0','2','2','--output',str(output)]
        stdout=io.StringIO()
        with redirect_stdout(stdout): code=visual_readback.main(args)
        self.assertEqual(0,code)
        self.assertTrue(json.loads(output.read_text())['matched'])
        with redirect_stdout(io.StringIO()): code=visual_readback.main(args)
        self.assertEqual(2,code)
