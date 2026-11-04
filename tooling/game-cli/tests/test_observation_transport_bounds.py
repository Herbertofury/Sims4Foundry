"""Hypothetical observation wrappers, not an installed adapter or route.

Fabricated readers, pure codecs and temporary journals only: no sockets, game,
process or profile access. Companion tests support Python 3.7; host tests 3.11+.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS

PACKAGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PACKAGE / 'companion-src'), str(PACKAGE / 'src')]
from foundry_companion.build_observation import BuildContext, BuildObserver
from foundry_companion.live_observation import LiveContext, LiveQueueObserver
from foundry_companion.native import DenyAdmission
from foundry_companion import queue as q
from foundry_companion import wire as cw

U64 = q.MAX_UINT64
SESSION, REQUEST = '2' * 32, '1' * 32
EPOCH = q.ProcessEpoch(2**32-1, U64)
REQ = q.Request(REQUEST, SESSION, 'observe', q.ObserveArguments(), process_epoch=EPOCH)
HOST = sys.version_info >= (3, 11)
if HOST:
    from sims4foundry_cli import companion_protocol as hw
    from sims4foundry_cli import companion_transport as ht
    from sims4foundry_cli import companion_journal as hj
    SUB = ht.Submission(hw.Request(
        REQUEST, SESSION, hw.ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime),
        hw.Operation.OBSERVE, hw.ObserveArguments()))
    DIGEST = SUB.request_sha256
else:
    DIGEST = '3'*64

class Admission(DenyAdmission):
    """Fabricated narrow read permission, never a production host provider."""
    def require_build_read(self, request, context, selected): return True
    def require_live_read(self, request, context, actor): return True

class ExceptionRow:
    @property
    def guid64(self): raise RuntimeError('not copied')
    @property
    def id(self): raise RuntimeError('not copied')

class ExceptionSim:
    def __init__(self, rows): self.sim_id, self.queue = U64, rows
    @property
    def si_state(self): raise RuntimeError('not copied')

class ExceptionBuild:
    def __init__(self, ident): self.id = ident
    @property
    def definition(self): raise RuntimeError('not copied')
    @property
    def position(self): raise RuntimeError('not copied')
    @property
    def orientation(self): raise RuntimeError('not copied')
    @property
    def routing_surface(self): raise RuntimeError('not copied')

def context(cls):
    return cls(SESSION, EPOCH, str(U64), str(U64), str(U64), U64, str(U64), U64)

def build(n, state='available', mode=True, event=False, unavailable_mask=0):
    ctx = context(BuildContext)
    objects = {}
    for i in range(n):
        ident = U64-i
        if state == 'available':
            # Lengthy bounded floats and integers; orientation has unit norm.
            obj = NS(id=ident, definition=NS(id=U64),
                     position=NS(x=-1.1234567890123456e-100, y=-1.1234567890123456e-100, z=-1.1234567890123456e-100),
                     orientation=NS(x=-1.1234567890123456e-100, y=-1.1234567890123456e-100, z=-1.1234567890123456e-100, w=-1.0),
                     routing_surface=NS(primary_id=U64, secondary_id=-2**31, type=2**31-1))
            for bit, name in enumerate(('definition', 'position', 'orientation', 'routing_surface')):
                if unavailable_mask & (1 << bit): delattr(obj, name)
        elif state == 'missing': obj = NS(id=ident)
        elif state == 'exception': obj = ExceptionBuild(ident)
        elif state == 'definition_missing':
            obj = NS(id=ident, position=NS(x=0, y=0, z=0), orientation=NS(x=0, y=0, z=0, w=1), routing_surface=NS(primary_id=U64, secondary_id=-2**31, type=2**31-1))
        else: obj = None
        objects[ident] = obj
    api = BuildObserver(lambda: {'context': ctx, 'zone_running': True, 'in_build_buy': mode}, objects.get, admission=Admission(), session_id=SESSION, process_epoch=EPOCH)
    if event: api.record_transition(REQ, ctx, U64, 'entered')
    return api.observe(REQ, ctx, [str(i) for i in objects])

def live(n, unavailable_fields=0, state='missing', split=None, other_unavailable=False):
    ctx = context(LiveContext)
    info = NS(id=U64)
    rows = []
    for i in range(n):
        if state == 'exception' and unavailable_fields >= 2:
            row = ExceptionRow()
            unavailable_fields -= 2
        else:
            fields = {'guid64': U64, 'id': U64-1}
            for name in ('guid64', 'id'):
                if unavailable_fields:
                    if state == 'null': fields[name] = None
                    elif state == 'invalid': fields[name] = 0
                    else: del fields[name]
                    unavailable_fields -= 1
            row = NS(**fields)
        rows.append(row)
    assert not unavailable_fields
    split = n if split is None else split
    sim = ExceptionSim(rows) if other_unavailable == 'exception' else NS(sim_id=U64, queue=rows[:split], si_state=None if other_unavailable else rows[split:])
    api = LiveQueueObserver(lambda: {'context': ctx, 'zone_running': True, 'mode': 'live'}, lambda ident: info, lambda inf: sim, admission=Admission(), session_id=SESSION, process_epoch=EPOCH)
    return api.observe(REQ, ctx, str(U64))

def hypothetical_native_wrapper(dto, native_style=True):
    """Explicit comparison shape, not an existing Build/Live adapter."""
    result = {'outcome': 'unverified', 'observation': dto}
    if native_style: result['game_qualified'] = False
    return result

def stats(value):
    values = {'nodes': 0, 'text_chars': 0, 'max_depth': 0, 'max_container_depth': 0}
    def visit(v, d, cd):
        values['nodes'] += 1
        values['max_depth'] = max(values['max_depth'], d)
        if type(v) is str: values['text_chars'] += len(v)
        elif type(v) in (dict, list):
            cd += 1
            values['max_container_depth'] = max(values['max_container_depth'], cd)
            if type(v) is dict:
                for k,c in v.items(): visit(k,d+1,cd); visit(c,d+1,cd)
            else:
                for c in v: visit(c,d+1,cd)
    visit(value, 0, 0)
    values['json_bytes'] = len(json.dumps(value, separators=(',', ':'), sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf8'))
    return values


class ObservationTransportBoundsTests(unittest.TestCase):
    def execute(self, raw):
        pending = q.CompanionQueue(SESSION, clock=lambda: 0)
        self.assertEqual('pending', pending.submit(REQ).state)
        states = []
        def execute(request):
            states.append(pending.status(request.request_id).state)
            return raw
        final = pending.drain(execute)
        self.assertEqual(['started'], states)
        return pending, final

    def receipt(self, final):
        return cw.receipt(REQUEST, DIGEST, SESSION, EPOCH, final)

    def assert_queue_result(self, raw, nodes, accepted):
        self.assertEqual(nodes, stats(raw)['nodes'])
        pending, final = self.execute(raw)
        self.assertEqual('unverified' if accepted else 'unknown', final.state)
        self.assertEqual(None if accepted else 'invalid_execution_result', final.reason)
        if accepted:
            self.assertEqual(raw, self.receipt(final)['adapter_result'])
        else:
            self.assertIsNone(final.result)
            self.assertIsNone(pending.status(REQUEST).result)
        return final

    def test_hypothetical_wrapper_maximum_source_dtos_exceed_nodes_not_bytes(self):
        cases = ((build(32), 1755, 19211, 1761),
                 (build(32, mode=None, event=True, unavailable_mask=1), 1821, 19556, 1827),
                 (build(32, 'missing'), 1371, 13995, 1377),
                 (live(64), 1027, 11058, 1033), (live(64, 128), 1283, 12210, 1289))
        for dto, source_nodes, source_bytes, result_nodes in cases:
            with self.subTest(source_nodes=source_nodes):
                self.assertEqual(source_nodes, stats(dto)['nodes'])
                self.assertEqual(source_bytes, stats(dto)['json_bytes'])
                self.assertLess(source_bytes, 32 * 1024)
                raw = hypothetical_native_wrapper(dto)
                self.assertLess(stats(raw)['text_chars'], q.MAX_RESULT_TEXT)
                self.assertLessEqual(stats(raw)['max_depth'], q.MAX_RESULT_DEPTH)
                self.assert_queue_result(raw, result_nodes, False)

    def test_hypothetical_build_wrapper_available_boundary(self):
        for count, nodes, accepted in ((18, 1019, True), (19, 1072, False)):
            with self.subTest(count=count):
                self.assert_queue_result(hypothetical_native_wrapper(build(count)), nodes, accepted)

        self.assert_queue_result(hypothetical_native_wrapper(build(32, 'absent')), 353, True)

    def test_hypothetical_build_wrapper_worst_field_mask_boundary(self):
        counts = [stats(hypothetical_native_wrapper(build(32, mode=None, event=True,
                  unavailable_mask=mask)))['nodes'] for mask in range(16)]
        self.assertEqual(1827, max(counts))
        self.assertEqual(max(counts), counts[1])
        for count, nodes, accepted in ((17, 1002, True), (18, 1057, False)):
            with self.subTest(count=count):
                self.assert_queue_result(hypothetical_native_wrapper(build(count, mode=None,
                    event=True, unavailable_mask=1)), nodes, accepted)

    def test_hypothetical_live_wrapper_available_boundary(self):
        for count, nodes, accepted in ((63, 1018, True), (64, 1033, False)):
            with self.subTest(count=count):
                self.assert_queue_result(hypothetical_native_wrapper(live(count)), nodes, accepted)

    def test_hypothetical_live_wrapper_unavailable_channel_changes_boundary(self):
        for count, other, nodes, accepted in ((50, False, 1023, True),
                (51, False, 1042, False), (49, True, 1006, True), (50, True, 1025, False)):
            with self.subTest(count=count, other_unavailable=other):
                self.assert_queue_result(hypothetical_native_wrapper(live(count, 2*count,
                    other_unavailable=other)), nodes, accepted)
        self.assert_queue_result(hypothetical_native_wrapper(live(50, 100,
            other_unavailable=True), native_style=False), 1023, True)

    def test_hypothetical_live_wrapper_longest_reasons_still_fail_nodes(self):
        raw = hypothetical_native_wrapper(live(64, 128, 'exception', other_unavailable='exception'))
        self.assertEqual(8748, stats(raw)['text_chars'])
        self.assert_queue_result(raw, 1291, False)
        self.assertNotIn('not copied', json.dumps(raw))
        build_raw = hypothetical_native_wrapper(build(32, 'exception', mode=None, event=True))
        self.assertEqual(10199, stats(build_raw)['text_chars'])
        self.assert_queue_result(build_raw, 1379, False)

    def test_generic_queue_node_bound_and_extension_metadata(self):
        raw = hypothetical_native_wrapper(live(63))
        raw['future_extension'] = {'unknown': None, 'opaque_uint64': str(U64)}
        final = self.assert_queue_result(raw, 1024, True)
        receipt = self.receipt(final)
        frame = cw.encode_frame(receipt)
        self.assertEqual(raw, cw.decode_payload(frame[4:])['adapter_result'])
        if HOST:
            self.assertEqual(raw, ht.parse_receipt(hw.decode_payload(frame[4:]), SUB)['adapter_result'])
        raw['extra'] = False
        self.assert_queue_result(raw, 1026, False)

    def test_generic_queue_depth_bound_counts_extra_wrappers(self):
        for dto, allowed_extra in ((build(1), 1), (live(1), 2)):
            for extra in (allowed_extra, allowed_extra + 1):
                with self.subTest(source=dto.get('schema_version'), extra=extra):
                    nested = dto
                    for _ in range(extra): nested = {'data': nested}
                    raw = hypothetical_native_wrapper(nested)
                    self.assertEqual(8 if extra == allowed_extra else 9, stats(raw)['max_depth'])
                    self.assert_queue_result(raw, stats(raw)['nodes'], extra == allowed_extra)

    def test_generic_queue_text_bound_is_not_wire_byte_bound(self):
        raw = hypothetical_native_wrapper(build(0))
        raw['metadata'] = ''
        raw['metadata'] = '\x00' * (q.MAX_RESULT_TEXT-stats(raw)['text_chars'])
        self.assertEqual(16384, stats(raw)['text_chars'])
        final = self.assert_queue_result(raw, 67, True)
        receipt = self.receipt(final)
        self.assertEqual(96113, stats(receipt)['json_bytes'])
        with self.assertRaises(cw.ProtocolError) as caught: cw.encode_frame(receipt)
        self.assertEqual('oversized_frame', caught.exception.code)
        if HOST:
            with self.assertRaises(hw.ProtocolError) as caught: hw.encode_frame(receipt)
            self.assertEqual('oversized_frame', caught.exception.code)
        raw['metadata'] += 'x'
        self.assertEqual(16385, stats(raw)['text_chars'])
        self.assert_queue_result(raw, 67, False)

    def test_rejected_result_is_not_retained_and_same_request_never_reexecutes(self):
        pending, final = self.execute(hypothetical_native_wrapper(live(64)))
        self.assertEqual(('unknown', 'invalid_execution_result'), (final.state, final.reason))
        self.assertIsNotNone(final.started_at)
        self.assertIsNone(final.result)
        self.assertIsNone(pending.status(REQUEST).result)
        self.assertIsNone(pending._entries[REQUEST].snapshot.result)
        self.assertEqual(0, len(pending._completed))
        self.assertEqual(final, pending.submit(REQ))
        self.assertIsNone(pending.drain(lambda _: self.fail('No replay may execute.')))
        with self.assertRaises(q.QueueError) as caught: pending.submit(replace(REQ, ttl_seconds=6.0))
        self.assertEqual('request_conflict', caught.exception.code)
        receipt = self.receipt(final)
        self.assertEqual('unknown', receipt['state'])
        self.assertIsNone(receipt['adapter_result'])
        self.assertEqual(390, len(cw.encode_frame(receipt))-4)
        self.assertNotIn('observation', json.dumps(receipt))
        self.assertEqual(receipt, cw.decode_payload(cw.encode_frame(receipt)[4:]))

    def test_companion_codec_preserves_small_unavailable_markers(self):
        for dto in (build(1, 'missing', mode=None), live(1, 2, other_unavailable=True)):
            with self.subTest(source=dto.get('schema_version')):
                raw = hypothetical_native_wrapper(dto)
                _, final = self.execute(raw)
                decoded = cw.decode_payload(cw.encode_frame(self.receipt(final))[4:])
                self.assertEqual(raw, decoded['adapter_result'])
                self.assertIn('unavailable', json.dumps(decoded))
                self.assertIn('missing_field', json.dumps(decoded))
                self.assertIsNone(dto['mode']['value'] if 'mode' in dto else
                                  dto['queued']['rows'][0]['tuning_guid64']['value'])

    def test_companion_codec_preserves_exact_uint64_types(self):
        for dto in (build(1), live(1)):
            _, final = self.execute(hypothetical_native_wrapper(dto))
            decoded = cw.decode_payload(cw.encode_frame(self.receipt(final))[4:])
            observed = decoded['adapter_result']['observation']
            self.assertEqual(dto, observed)
            self.assertIs(type(observed['context']['zone_id']), str)
            self.assertEqual(str(U64), observed['context']['zone_id'])
            if 'objects' in observed:
                obj = observed['objects'][0]
                self.assertIs(type(obj['object_id']), str)
                self.assertEqual(str(U64), obj['fields']['definition_id']['value'])
                primary = obj['fields']['routing_surface']['value']['primary_id']
                self.assertIs(type(primary), int)
                self.assertEqual(U64, primary)
                self.assertIs(type(observed['context']['zone_generation']), int)
            else:
                self.assertIs(type(observed['context']['zone_generation']), str)
                for name, expected in (('tuning_guid64', U64), ('runtime_instance_id', U64-1)):
                    value = observed['queued']['rows'][0][name]['value']
                    self.assertIs(type(value), str)
                    self.assertEqual(str(expected), value)
            self.assertIs(type(decoded['process_epoch']['creation_filetime']), int)
            self.assertEqual(U64, decoded['process_epoch']['creation_filetime'])

    @unittest.skipUnless(HOST, 'Host CLI requires Python 3.11+.')
    def test_both_codecs_preserve_accepted_unknowns_ids_and_extension(self):
        for dto in (build(1, 'missing', mode=None), live(1, 2, other_unavailable=True),
                    build(1), live(1)):
            with self.subTest(source=dto.get('schema_version'), unavailable='missing_field' in json.dumps(dto)):
                raw = hypothetical_native_wrapper(dto)
                raw['future_extension'] = {'unknown': None, 'opaque_uint64': str(U64)}
                _, final = self.execute(raw)
                receipt = self.receipt(final)
                frame = cw.encode_frame(receipt)
                parsed = ht.parse_receipt(hw.decode_payload(frame[4:]), SUB)
                self.assertEqual(receipt, parsed)
                self.assertEqual(raw, parsed['adapter_result'])
                self.assertEqual(frame, hw.encode_frame(parsed))
                self.assertEqual(receipt, cw.decode_payload(hw.encode_frame(parsed)[4:]))
                self.assertEqual(str(U64), parsed['adapter_result']['future_extension']['opaque_uint64'])

    @unittest.skipUnless(HOST, 'Host CLI requires Python 3.11+.')
    def test_direct_hypothetical_full_receipts_fit_codecs_not_queue(self):
        for dto, nodes, size in ((build(32), 1785, 19646),
                (build(32, mode=None, event=True, unavailable_mask=1), 1851, 19991),
                (live(64), 1057, 11493)):
            with self.subTest(nodes=nodes):
                raw = hypothetical_native_wrapper(dto)
                # Bypass only to test the separate wire bound, never queue success.
                fabricated = q.Snapshot(REQUEST, SESSION, 'unverified', result=raw)
                receipt = self.receipt(fabricated)
                self.assertEqual(nodes, stats(receipt)['nodes'])
                self.assertEqual(size, stats(receipt)['json_bytes'])
                self.assertEqual(373, stats(receipt)['json_bytes']-stats(raw)['json_bytes'])
                frame = cw.encode_frame(receipt)
                parsed = ht.parse_receipt(hw.decode_payload(frame[4:]), SUB)
                self.assertEqual(receipt, parsed)
                self.assertEqual(frame, hw.encode_frame(parsed))
                self.assert_queue_result(raw, stats(raw)['nodes'], False)

    @unittest.skipUnless(HOST, 'Host CLI requires Python 3.11+.')
    def test_journal_keeps_digest_only_not_accepted_observation_or_extensions(self):
        raw = hypothetical_native_wrapper(live(49, 98, other_unavailable=True))
        raw['future_extension'] = {'unknown': None, 'audit_marker': 'bounded_extension_marker'}
        _, final = self.execute(raw)
        self.assertEqual('unverified', final.state)
        receipt = self.receipt(final)
        digest = hashlib.sha256(hw.encode_frame(receipt)[4:]).hexdigest()
        snapshot = hj._snapshot(receipt, SUB)
        self.assertEqual({'state', 'reason', 'adapter_outcome', 'receipt_sha256'}, set(snapshot.to_wire()))
        self.assertEqual(digest, snapshot.receipt_sha256)
        changed = copy.deepcopy(receipt)
        changed['adapter_result']['future_extension']['audit_marker'] = 'other_bounded_marker'
        self.assertNotEqual(digest, hj._snapshot(changed, SUB).receipt_sha256)
        self.assertEqual(157, stats(snapshot.to_wire())['json_bytes'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'ledger'
            ledger = hj.create_journal(root)
            status = ledger.submit_once(SUB, authorize=lambda *_: True, exchange=lambda *_: receipt)
            self.assertEqual('adapter_returned', status.state)
            self.assertEqual('unverified', status.receipt.adapter_outcome)
            self.assertEqual(digest, status.receipt.receipt_sha256)
            self.assertEqual(status, hj.Journal(root).status(REQUEST))
            records = list((root/'records').glob('*.json'))
            self.assertEqual(3, len(records))
            for path in records:
                data = path.read_bytes()
                self.assertLess(len(data), hj.MAX_RECORD_BYTES)
                for marker in (b'"observation"', b'future_extension', b'bounded_extension_marker',
                               b'missing_field', b'"adapter_result"'):
                    self.assertNotIn(marker, data)

    @unittest.skipUnless(HOST, 'Host CLI requires Python 3.11+.')
    def test_rejected_result_never_reaches_host_or_journal_and_is_not_replayed(self):
        _, final = self.execute(hypothetical_native_wrapper(live(64, 128)))
        receipt = self.receipt(final)
        parsed = ht.parse_receipt(hw.decode_payload(cw.encode_frame(receipt)[4:]), SUB)
        self.assertIsNone(parsed['adapter_result'])
        self.assertEqual('unknown', parsed['state'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'ledger'
            ledger = hj.create_journal(root)
            status = ledger.submit_once(SUB, authorize=lambda *_: True, exchange=lambda *_: parsed)
            self.assertEqual('unknown', status.state)
            self.assertEqual('invalid_execution_result', status.receipt.reason)
            self.assertIsNone(status.receipt.adapter_outcome)
            reopened = hj.Journal(root)
            self.assertEqual(status, reopened.status(REQUEST))
            self.assertEqual(status, reopened.submit_once(SUB,
                authorize=lambda *_: self.fail('Duplicate intent must not authorize.'),
                exchange=lambda *_: self.fail('Duplicate intent must not send.')))
            for path in (root/'records').glob('*.json'):
                data = path.read_bytes()
                for marker in (b'"observation"', b'tuning_guid64', b'missing_field', b'"adapter_result"'):
                    self.assertNotIn(marker, data)


if __name__ == '__main__':
    unittest.main()
