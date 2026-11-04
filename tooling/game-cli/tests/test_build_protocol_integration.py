"""Focused Build route tests, fabricated objects and local temporary journal only."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from time import monotonic
import unittest
from unittest.mock import patch
from test_build_protocol import request, b, w, q, wire, HOST, PACKAGE
from foundry_companion.transport import LoopbackTransport, DenyHostPolicy

class FixturePolicy(DenyHostPolicy):
    def authorize_submit(self,*args):return True
    def authorize_dispatch(self,*args):return True

class BuildQueueTests(unittest.TestCase):
    def test_capture_read_transform_sticky_mutation_and_receipt(self):
        queue=q.CompanionQueue('b'*32)
        capture=request(); queue.submit(capture)
        self.assertEqual('unverified',queue.drain(lambda r:w.make_result(r,'captured')).state)
        self.assertIsNone(queue.queue_status().mutation_blocked_reason)
        transform=replace(request(True),request_id='f'*32); queue.submit(transform)
        self.assertEqual('unverified',queue.drain(lambda r:w.make_result(r,'dispatched',dispatch_attempted=True)).state)
        self.assertIsNotNone(queue.queue_status().mutation_blocked_reason)
        self.assertEqual('refused',queue.submit(replace(transform,request_id='1'*32)).state)
        self.assertEqual('pending',queue.submit(replace(capture,request_id='2'*32)).state)
        self.assertEqual('unverified',queue.drain(lambda r:w.make_result(r,'captured')).state)
        self.assertIsNotNone(queue.queue_status().mutation_blocked_reason)

    def test_callback_mutation_cannot_retarget_and_invalid_result_unknown(self):
        queue=q.CompanionQueue('b'*32);r=request();queue.submit(r)
        def admit(view):
            object.__setattr__(view.arguments,'object_id','3');return True
        self.assertEqual('refused',queue.drain(lambda r:self.fail('must not execute'),admit).state)
        r=replace(request(True),request_id='1'*32);queue.submit(r)
        def execute(view):
            result=w.make_result(view,'dispatched',dispatch_attempted=True)
            result['selection']['object_id']='3';return result
        result=queue.drain(execute)
        self.assertEqual(('unknown','invalid_execution_result',None),(result.state,result.reason,result.result))
        self.assertIsNotNone(queue.queue_status().mutation_blocked_reason)

    def test_cross_schema_conflicts_and_bound_receipt_read(self):
        t=LoopbackTransport('b'*32,q.ProcessEpoch(1,2),policy=FixturePolicy())
        original=b.request_wire(request());deadline=monotonic()+2
        response,_=t._handle(original,deadline)
        self.assertEqual('accepted',response['state'])
        t.owner_tick(lambda r:w.make_result(r,'captured'))
        query=dict(schema=4,kind='receipt_read',request_id='a'*32,session_id='b'*32,
                   process_epoch=dict(pid=1,creation_filetime=2),request_sha256=w.request_sha256(request()))
        response,_=t._handle(query,deadline)
        self.assertEqual('captured',response['adapter_result']['action_status'])
        data=dict(schema=3,kind='submit',request_id='a'*32,session_id='b'*32,
                  process_epoch=dict(pid=1,creation_filetime=2),operation='live.observe_queue',
                  arguments=dict(actor_sim_id='1'),ttl_seconds=5.0,
                  expected_context=dict(zone_id='1',household_id='2',client_id='3',save_slot=0,
                                        save_guid='0',zone_generation=1,mode='live'))
        with self.assertRaises(wire.ProtocolError):t._handle(data,deadline)
        self.assertEqual('request_conflict',t.queue_status().mutation_blocked_reason)

    def test_default_transport_denies(self):
        t=LoopbackTransport('b'*32,q.ProcessEpoch(1,2))
        response,_=t._handle(b.request_wire(request()),monotonic()+2)
        self.assertEqual(('refused','host_admission_denied'),(response['state'],response['reason']))

@unittest.skipUnless(HOST,'Host requires Python3.11+')
class BuildHostIntegrationTests(unittest.TestCase):
    def host(self,transform=False):
        from sims4foundry_cli import companion_build_protocol as h
        r=request(transform)
        return h.parse_build_submit(b.request_wire(r),r.session_id,h.ProcessEpoch(1,2))

    def test_journal_reopen_no_replay_sanitized_summary_mutation_barrier(self):
        from sims4foundry_cli import companion_journal as j, companion_build_protocol as h
        from sims4foundry_cli.errors import FoundryError
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'journal';ledger=j.create_journal(path);item=self.host(True);calls=[]
            def exchange(message,attempt):
                calls.append(attempt.kind)
                sub=message.submission if type(message) is h.BuildReceiptRead else message
                r=w.parse_submit(sub.to_wire(),sub.request.session_id,q.ProcessEpoch(1,2)).request
                return w.preview_result_receipt(w.make_result(r,'dispatched',dispatch_attempted=True),r)
            status=ledger.submit_once(item,authorize=lambda *_:True,exchange=exchange)
            self.assertTrue(status.mutation and status.unresolved)
            self.assertEqual('unverified',status.receipt.adapter_outcome)
            ledger=j.Journal(path)
            ledger.submit_once(item,authorize=lambda *_:True,exchange=exchange)
            self.assertEqual(['submit'],calls)
            ledger.reconcile(item.request.request_id,authorize=lambda *_:True,exchange=exchange)
            self.assertEqual(['submit','receipt_read'],calls)
            capture=h.BuildSubmission(replace(self.host().request,request_id='2'*32))
            self.assertFalse(ledger.record_intent(capture).mutation)
            with self.assertRaises(FoundryError):ledger.record_intent(h.BuildSubmission(replace(item.request,request_id='3'*32)))
            records=[json.loads(p.read_text()) for p in sorted((path/'records').glob('*.json'))]
            summaries=[row['data']['snapshot'] for row in records if row['kind']=='receipt']
            self.assertTrue(all(set(row)=={'state','reason','adapter_outcome','receipt_sha256'} for row in summaries))
            self.assertNotIn('action_status',json.dumps(records))

    def test_sender_denies_before_socket_and_sends_one_exact_frame(self):
        from sims4foundry_cli import companion_build_transport as sender
        from sims4foundry_cli import companion_build_protocol as h
        from test_companion_live_exchange import FakeSocket
        item=self.host();r=request();response=w.preview_result_receipt(w.make_result(r,'captured'),r)
        with patch.object(sender.socket,'socket') as factory:
            with self.assertRaises(h.wire.ProtocolError):sender.exchange_once(1234,item)
            factory.assert_not_called()
        for message in (item,h.BuildReceiptRead(item)):
            sock=FakeSocket(wire.encode_frame(response))
            with patch.object(sender.socket,'socket',return_value=sock) as factory:
                actual=sender.exchange_once(1234,message,authorize_peer=lambda *_:True)
            self.assertEqual('captured',actual['adapter_result']['action_status'])
            self.assertEqual(1,factory.call_count);self.assertEqual(1,len(sock.sent))
            sent=wire.decode_payload(sock.sent[0][4:]);self.assertEqual(4,sent['schema'])

    def test_process_peer_snapshot_detaches_schema4(self):
        from sims4foundry_cli import process_bound_peer as peer
        from sims4foundry_cli import companion_build_protocol as h
        item=self.host(True); detached,payload=peer._snapshot(item)
        self.assertIs(type(detached),h.BuildSubmission)
        self.assertEqual(payload,item.canonical_payload)
        object.__setattr__(item.request.arguments,'destination',(9,9,9))
        self.assertNotEqual(item.canonical_payload,payload)
        self.assertEqual(detached.canonical_payload,payload)

    def test_actual37_codec_interoperability(self):
        binary=PACKAGE.parents[2]/'foundry-python37/Python-3.7.17/python'
        if not binary.is_file():self.skipTest('Actual3.7 interpreter unavailable')
        item=self.host();payload=item.canonical_payload
        script="""import sys
sys.path.insert(0,sys.argv[1])
from foundry_companion import wire, build_wire as b
from foundry_companion.queue import ProcessEpoch
v=wire.decode_payload(sys.stdin.buffer.read())
s=b.parse_submit(v,v['session_id'],ProcessEpoch(**v['process_epoch']))
result=b.make_result(s.request,'captured')
sys.stdout.buffer.write(wire.encode_frame(b.preview_result_receipt(result,s.request))[4:])
"""
        completed=subprocess.run([str(binary),'-c',script,str(PACKAGE/'companion-src')],input=payload,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
        from sims4foundry_cli import companion_build_protocol as h
        self.assertEqual(item.request_sha256,h.parse_build_receipt(h.wire.decode_payload(completed.stdout),item)['request_sha256'])

if __name__=='__main__':unittest.main()
