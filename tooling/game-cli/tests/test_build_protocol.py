"""Owned synthetic schema-4 fixtures. No game/profile/native access."""
import copy
from dataclasses import replace
from pathlib import Path
import sys
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PACKAGE / 'companion-src'), str(PACKAGE / 'src')]
from foundry_companion import queue as q
from foundry_companion import wire
from foundry_companion import build_request as b
from foundry_companion import build_wire as w
HOST = sys.version_info >= (3, 11)
if HOST:
    from sims4foundry_cli import companion_build_protocol as h


def request(transform=False):
    args = b.BuildCaptureArguments(str(2**64-1), '2', (0, 1.0, -0.0), (0, 0, 0, 1), b.BuildSurface('0', 0, 0))
    if transform:
        args = b.BuildTransformArguments(args.object_id, args.definition_id, args.before_position,
               args.before_orientation, args.before_surface, 'd'*32, 'e'*64, (1, 2, 3), 1.0, 0)
    return b.BuildRequest('a'*32, 'b'*32, q.ProcessEpoch(1, 2), args,
            b.BuildExpectedContext('1', '2', '3', 0, '0', 1), 'c'*64, 5.0,
            'build.transform_selected' if transform else 'build.capture_transform')


class BuildProtocolTests(unittest.TestCase):
    def test_schema_source_parity(self):
        self.assertEqual((PACKAGE/'companion-src/foundry_companion/build_schema.py').read_bytes(),
                         (PACKAGE/'src/sims4foundry_cli/companion_build_schema.py').read_bytes())

    def test_both_operations_and_retained_baseline(self):
        for transform in (False, True):
            original = request(transform)
            key = b.request_key(original)
            self.assertIs(type(key), bytes)
            rebuilt = b.request_from_key(key)
            self.assertEqual(b.request_key(rebuilt), key)
            self.assertIsNot(original.arguments, rebuilt.arguments)
            self.assertIsNot(original.arguments.before_surface, rebuilt.arguments.before_surface)
            self.assertEqual(rebuilt.arguments.object_id, str(2**64-1))
            parsed = w.parse_submit(b.request_wire(original), original.session_id, original.process_epoch)
            self.assertEqual(parsed.request_sha256, w.request_sha256(original))

    def test_canonical_numeric_kind(self):
        a=request(); i=replace(a, ttl_seconds=5)
        self.assertNotEqual(w.request_sha256(a), w.request_sha256(i))
        v=b.request_wire(a); v['arguments']['before_position'][2]=0.0
        other=w.parse_submit(v,a.session_id,a.process_epoch).request
        self.assertNotEqual(w.request_sha256(a),w.request_sha256(other))
        result=w.make_result(a,'captured'); result['selection']['before_position'][2]=0.0
        with self.assertRaises(wire.ProtocolError):w.validate_result(result,a)

    def test_bad_arguments_and_context(self):
        edits=[('object_id','01'),('object_id',str(2**64)),('definition_id',True),
               ('before_position',[True,0,0]),('before_position',[0,float('inf'),0]),
               ('before_orientation',[0,0,0,0]),('before_surface',dict(primary='0',level=True,type=0))]
        for key,value in edits:
            data=b.request_wire(request());data['arguments'][key]=value
            with self.assertRaises(wire.ProtocolError):w.parse_submit(data,'b'*32,q.ProcessEpoch(1,2))
        for field,value in [('mode','live'),('save_slot',True),('zone_generation',0)]:
            data=b.request_wire(request());data['expected_context'][field]=value
            with self.assertRaises(wire.ProtocolError):w.parse_submit(data,'b'*32,q.ProcessEpoch(1,2))
        for field,value in [('yaw',True),('yaw',7),('level',33),('capture_request_sha256','e'*63)]:
            data=b.request_wire(request(True));data['arguments'][field]=value
            with self.assertRaises(wire.ProtocolError):w.parse_submit(data,'b'*32,q.ProcessEpoch(1,2))

    def test_result_dispatch_claims_and_binding(self):
        r=request(True)
        for status,attempt,reason in [('dispatched',True,None),('placement_refused',False,'placement_invalid'),
                                       ('unresolved',True,'readback_unavailable'),('unresolved',False,'capture_missing')]:
            result=w.make_result(r,status,reason,attempt)
            w.preview_result_receipt(result,r)
            for field,value in [('request_sha256','f'*64),('deployment_sha256','f'*64),
                                ('transaction_verified',True),('undo_recorded',True),('game_qualified',True),
                                ('may_have_applied',not attempt)]:
                bad=copy.deepcopy(result);bad[field]=value
                with self.assertRaises(wire.ProtocolError):w.preview_result_receipt(bad,r)
        with self.assertRaises(wire.ProtocolError):w.make_result(r,'captured')
        with self.assertRaises(wire.ProtocolError):w.make_result(request(),'dispatched',dispatch_attempted=True)

    def test_complete_receipt_budget_and_exact_dataclasses(self):
        from unittest.mock import patch
        r=request(); result=w.make_result(r,'captured')
        # The result itself fits, but the complete receipt must also fit.
        result_size=len(wire.encode_frame(result))-4
        receipt_size=len(wire.encode_frame(w.preview_result_receipt(result,r)))-4
        self.assertGreater(receipt_size,result_size)
        with patch.object(wire,'MAX_FRAME_BYTES',result_size):
            with self.assertRaises(w.ResultBudgetExceeded):w.preview_result_receipt(result,r)
        class Impostor(b.BuildExpectedContext):pass
        with self.assertRaises(wire.ProtocolError):Impostor('1','2','3',0,'0',1)
        with self.assertRaises(wire.ProtocolError):replace(r,arguments=object())

    def test_detached_result_and_request(self):
        r=request(); after=dict(position=[0,0,0],orientation=[0,0,0,1],surface=dict(primary='0',level=0,type=0))
        result=w.make_result(r,'captured',after=after);after['position'][0]=9
        self.assertEqual(result['after']['position'][0],0)
        baseline=b.request_key(r);object.__setattr__(r.arguments.before_surface,'primary','9')
        self.assertNotEqual(b.request_key(r),baseline)
        self.assertEqual(b.request_from_key(baseline).arguments.before_surface.primary,'0')

    @unittest.skipUnless(HOST,'Host requires Python 3.11+')
    def test_host_roundtrip_and_capture_projection(self):
        r=request(); original=b.request_wire(r)
        hs=h.parse_build_submit(original,r.session_id,h.ProcessEpoch(1,2))
        self.assertEqual(hs.request_sha256,w.request_sha256(r))
        self.assertEqual(h.BuildReceiptRead(hs).to_wire()['request_sha256'],hs.request_sha256)
        receipt=w.preview_result_receipt(w.make_result(r,'captured'),r)
        ready=h.capture_ready(receipt,hs)
        self.assertEqual(ready['capture_request_sha256'],hs.request_sha256)
        self.assertEqual(ready['object_id'],r.arguments.object_id)
        receipt['adapter_result']['selection']['object_id']='3'
        with self.assertRaises(h.wire.ProtocolError):h.capture_ready(receipt,hs)
        missing=w.receipt(r.request_id,w.request_sha256(r),r.session_id,r.process_epoch)
        with self.assertRaises(h.wire.ProtocolError):h.capture_ready(missing,hs)

    @unittest.skipUnless(HOST,'Host requires Python 3.11+')
    def test_unknown_is_not_reset_or_success(self):
        r=request(True);hs=h.parse_build_submit(b.request_wire(r),r.session_id,h.ProcessEpoch(1,2))
        receipt=w.receipt(r.request_id,w.request_sha256(r),r.session_id,r.process_epoch,
                          q.Snapshot(r.request_id,r.session_id,'unknown',reason='dispatch_uncertain'))
        self.assertEqual(h.parse_build_receipt(receipt,hs)['state'],'unknown')
        for state in ('verified','succeeded','reset'):
            bad=dict(receipt,state=state)
            with self.assertRaises(h.wire.ProtocolError):h.parse_build_receipt(bad,hs)

if __name__=='__main__':unittest.main()
