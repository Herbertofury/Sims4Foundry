"""Fabricated schema-3 data only; no game, credentials, sockets or installed provider.

Companion tests run on actual Python 3.7. Host checks are explicitly gated.
"""
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PACKAGE / 'companion-src'), str(PACKAGE / 'src')]
from foundry_companion import queue as q
from foundry_companion import wire as cw
from foundry_companion import live_wire as live

HOST = sys.version_info >= (3, 11)
if HOST:
    from sims4foundry_cli import companion_protocol as hw
    from sims4foundry_cli import companion_transport as ht
    from sims4foundry_cli import companion_live_protocol as host

SESSION, REQUEST = '2' * 32, '1' * 32
EPOCH = q.ProcessEpoch(123, 456)
U64 = (1 << 64) - 1


def submit_value():
    return dict(schema=3, kind='submit', request_id=REQUEST, session_id=SESSION,
                process_epoch=dict(pid=123, creation_filetime=456), operation='live.observe_queue',
                arguments=dict(actor_sim_id='30'), ttl_seconds=5.0,
                expected_context=dict(zone_id='10', household_id='20', client_id='5',
                                      save_slot=4, save_guid='99', zone_generation=1, mode='live'))


def submission(value=None):
    value = submit_value() if value is None else value
    return live.parse_submit(value, value['session_id'], q.ProcessEpoch(**value['process_epoch']))


def result_value(request=None, rows=1, unavailable=False, split=None):
    request = submission().request if request is None else request
    original = live.request_wire(request)
    context = dict(original['expected_context'])
    del context['mode']
    context['save_slot'] = str(context['save_slot'])
    context['zone_generation'] = str(context['zone_generation'])
    context['session_id'] = request.session_id
    context['process_epoch'] = {key: str(value) for key, value in original['process_epoch'].items()}
    occurrences = []
    for index in range(rows):
        field = (dict(status='unavailable', value=None, reason='reader_exception') if unavailable else
                 dict(status='available', value=str(U64)))
        occurrences.append(dict(index=index, tuning_guid64=field,
                                runtime_instance_id=copy.deepcopy(field)))
    split = rows if split is None else split
    queued, running = occurrences[:split], occurrences[split:]
    for index, row in enumerate(running):
        row['index'] = index
    dto = dict(schema_version=1, source='source_only_injected_readers', source_only=True,
               read_only=True, live_qualified=False, independent_evidence=False, atomic_snapshot=False,
               context_source='injected_host_declaration', identity_scope='this_call_only',
               selection_authority=False, cancellation_authority=False, action_outcome='unknown',
               request_id=request.request_id, context=context, actor_sim_id=request.arguments.actor_sim_id,
               actor=dict(status='observed'), queued=dict(status='observed', rows=queued),
               running=dict(status='observed', rows=running))
    return dict(schema_version=1, outcome='unverified', game_qualified=False,
                read_status='observed', observation=dto, reason=None)


def unavailable_result(reason='row_limit_exceeded'):
    return dict(schema_version=1, outcome='unverified', game_qualified=False,
                read_status='unavailable', observation=None, reason=reason)


def node_count(value):
    if type(value) is dict:
        return 1 + sum(node_count(key) + node_count(item) for key, item in value.items())
    if type(value) is list:
        return 1 + sum(node_count(item) for item in value)
    return 1


class LiveProtocolTests(unittest.TestCase):
    def test_mirrored_schema_source_identical(self):
        self.assertEqual((PACKAGE / 'src/sims4foundry_cli/companion_live_schema.py').read_bytes(),
                         (PACKAGE / 'companion-src/foundry_companion/live_schema.py').read_bytes())

    def test_companion_roundtrip_complete_canonical_intent(self):
        item = submission()
        payload = cw.encode_frame(submit_value())[4:]
        self.assertEqual(payload, item.canonical_payload)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), item.request_sha256)
        self.assertEqual(submit_value(), live.request_wire(item.request))
        self.assertEqual(payload, live.canonical_payload(item.request))
        self.assertEqual(item.request_sha256, live.request_sha256(item.request))
        self.assertEqual(payload, submission(cw.decode_payload(payload)).canonical_payload)

    def test_every_full_intent_field_changes_digest_including_ttl_kind(self):
        original = submit_value()
        variants = [('request_id', '3' * 32), ('session_id', '4' * 32), ('ttl_seconds', 5), ('ttl_seconds', 6.0)]
        for name, value in variants:
            changed = copy.deepcopy(original)
            changed[name] = value
            self.assertNotEqual(submission().request_sha256, submission(changed).request_sha256)
        for section, changes in [('arguments', {'actor_sim_id': '31'}),
                ('process_epoch', {'pid': 124, 'creation_filetime': 457}),
                ('expected_context', {'zone_id': '11', 'household_id': '21', 'client_id': '6',
                                      'save_slot': 5, 'save_guid': '100', 'zone_generation': 2})]:
            for name, value in changes.items():
                changed = copy.deepcopy(original)
                changed[section][name] = value
                self.assertNotEqual(submission().request_sha256, submission(changed).request_sha256)
        for mode in ('build', 'cas', '', None, False):
            changed = copy.deepcopy(original)
            changed['expected_context']['mode'] = mode
            with self.assertRaises(cw.ProtocolError):
                submission(changed)

    def test_strict_malformed_submission_matrix(self):
        changes = []
        original = submit_value()
        for field in original:
            value = copy.deepcopy(original); del value[field]; changes.append(value)
        value = copy.deepcopy(original); value['extra'] = None; changes.append(value)
        for section in ('arguments', 'expected_context', 'process_epoch'):
            for field in original[section]:
                value = copy.deepcopy(original); del value[section][field]; changes.append(value)
            value = copy.deepcopy(original); value[section]['extra'] = True; changes.append(value)
        for name, options in {'schema': [True, 1, 2, 3.0], 'kind': [None, 'request'],
                              'operation': ['observe', 'cas.enter', 'live.set_speed'],
                              'request_id': ['A' * 32, 'x', None, 1],
                              'ttl_seconds': [None, True, 0, -1, 31, float('inf'), float('nan')]}.items():
            for option in options:
                value = copy.deepcopy(original); value[name] = option; changes.append(value)
        for section, name, options in [
                ('arguments', 'actor_sim_id', [0, '0', '01', '+1', '\u0661', str(U64 + 1), None]),
                ('process_epoch', 'pid', [True, 0, 2**32, '123']),
                ('process_epoch', 'creation_filetime', [True, 0, U64 + 1, '456']),
                ('expected_context', 'save_slot', [True, -1, U64 + 1, '4']),
                ('expected_context', 'zone_generation', [True, 0, U64 + 1, '1']),
                ('expected_context', 'save_guid', ['00', None, U64])]:
            for option in options:
                value = copy.deepcopy(original); value[section][name] = option; changes.append(value)
        for index, value in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(cw.ProtocolError):
                live.parse_submit(value, SESSION, EPOCH)

    def test_exact_plain_types_and_subclass_refusal(self):
        class Text(str): pass
        class Number(int): pass
        class Object(dict): pass
        for field, value in [('operation', Text('live.observe_queue')), ('ttl_seconds', Number(5)),
                             ('arguments', Object(actor_sim_id='30'))]:
            original = submit_value(); original[field] = value
            with self.assertRaises(cw.ProtocolError):
                submission(original)
        with self.assertRaises(cw.ProtocolError):
            submission(Object(submit_value()))
        request = submission().request
        class SubRequest(q.LiveQueueRequest): pass
        with self.assertRaises((q.QueueError, cw.ProtocolError)):
            live.request_wire(SubRequest(request.request_id, request.session_id, request.process_epoch,
                                          request.arguments, request.expected_context))
        with self.assertRaises(cw.ProtocolError):
            live.request_wire(q.Request(REQUEST, SESSION, 'observe', q.ObserveArguments(), process_epoch=EPOCH))

    def test_distinct_v2_and_v3_parsers_no_downgrade(self):
        with self.assertRaises(cw.ProtocolError):
            cw.parse_submit(submit_value(), SESSION, EPOCH)
        value = dict(schema=2, kind='submit', request=dict(schema=1, kind='request', request_id=REQUEST,
                session_id=SESSION, process_epoch=dict(pid=123, creation_filetime=456),
                operation='observe', arguments={}), ttl_seconds=5.0, expected_context=None)
        cw.parse_submit(value, SESSION, EPOCH)
        with self.assertRaises(cw.ProtocolError):
            live.parse_submit(value, SESSION, EPOCH)

    def test_duplicate_keys_and_nonfinite_codec_refusal(self):
        raw = live.canonical_payload(submission().request)
        with self.assertRaises(cw.ProtocolError):
            cw.decode_payload(raw.replace(b'"schema":3', b'"schema":3,"schema":3'))
        for value in (b'NaN', b'Infinity', b'1e999'):
            with self.assertRaises(cw.ProtocolError):
                cw.decode_payload(raw.replace(b'"ttl_seconds":5.0', b'"ttl_seconds":' + value))

    def test_exact_process_lifetime_and_session_binding(self):
        for session_id, epoch in [('3' * 32, EPOCH), (SESSION, q.ProcessEpoch(124, 456)),
                                  (SESSION, q.ProcessEpoch(123, 457))]:
            with self.assertRaises(cw.ProtocolError):
                live.parse_submit(submit_value(), session_id, epoch)

    def test_submission_copies_nested_aliases(self):
        request = submission().request
        item = live.Submission(request, live.request_sha256(request), live.canonical_payload(request))
        baseline = item.canonical_payload
        object.__setattr__(request.arguments, 'actor_sim_id', '31')
        object.__setattr__(request.process_epoch, 'pid', 124)
        object.__setattr__(request.expected_context, 'client_id', '6')
        self.assertEqual(baseline, live.canonical_payload(item.request))
        with self.assertRaises(cw.ProtocolError):
            live.Submission(request, item.request_sha256, item.canonical_payload)
        object.__setattr__(item.request, 'operation', 'observe')
        with self.assertRaises(cw.ProtocolError):
            live.canonical_payload(item.request)

    def test_exact_receipt_lookup_and_cross_schema_refusal(self):
        item = submission()
        query = dict(schema=3, kind='receipt_read', request_id=REQUEST, session_id=SESSION,
                     process_epoch=dict(pid=123, creation_filetime=456), request_sha256=item.request_sha256)
        self.assertEqual((REQUEST, item.request_sha256), live.parse_receipt_read(query, SESSION, EPOCH))
        with self.assertRaises(cw.ProtocolError):
            cw.parse_receipt_read(query, SESSION, EPOCH)
        for name, value in [('schema', 2), ('request_sha256', 'A' * 64), ('ttl_seconds', 30), ('extra', None)]:
            changed = dict(query); changed[name] = value
            with self.assertRaises(cw.ProtocolError):
                live.parse_receipt_read(changed, SESSION, EPOCH)

    def test_valid_results_all_split_points_and_availability_masks(self):
        request = submission().request
        for count in (0, 1, 49):
            for split in range(count + 1):
                for unavailable in (False, True):
                    value = result_value(request, count, unavailable, split)
                    envelope = live.preview_result_receipt(value, request)
                    self.assertEqual(value, envelope['adapter_result'])
                    self.assertEqual(3, envelope['schema'])
                    self.assertEqual('adapter_returned', envelope['state'])
                    self.assertFalse(envelope['independent_evidence'])
                    cw.decode_payload(cw.encode_frame(envelope)[4:])
        for mask in range(4):
            value = result_value(request)
            row = value['observation']['queued']['rows'][0]
            for bit, name in enumerate(('tuning_guid64', 'runtime_instance_id')):
                if mask & (1 << bit):
                    row[name] = dict(status='unavailable', value=None, reason='missing_field')
            live.preview_result_receipt(value, request)

    def test_unavailable_actor_and_channels_are_not_empty(self):
        request = submission().request
        for reason in ('not_found', 'not_instanced', 'reader_exception', 'identity_mismatch_or_unavailable'):
            value = result_value(request, 0)
            value['observation']['actor'] = dict(status='unavailable', reason=reason)
            for channel in ('queued', 'running'):
                value['observation'][channel] = dict(status='unavailable', rows=None, reason='actor_unavailable')
            live.preview_result_receipt(value, request)
            value['observation']['queued'] = dict(status='observed', rows=[])
            with self.assertRaises(cw.ProtocolError):
                live.validate_result(value, request)
        for reason in ('missing_field', 'reader_exception', 'null_value', 'not_iterable'):
            value = result_value(request)
            value['observation']['running'] = dict(status='unavailable', rows=None, reason=reason)
            live.preview_result_receipt(value, request)
            value['observation']['running']['rows'] = []
            with self.assertRaises(cw.ProtocolError):
                live.validate_result(value, request)

    def test_fixed_whole_read_diagnostics(self):
        request = submission().request
        for reason in ('row_limit_exceeded', 'result_budget_exceeded'):
            value = unavailable_result(reason)
            self.assertEqual(value, live.preview_result_receipt(value, request)['adapter_result'])
        for name, content in [('reason', 'exception_details'), ('observation', {}),
                              ('outcome', 'verified'), ('read_status', 'empty')]:
            value = unavailable_result(); value[name] = content
            with self.assertRaises(cw.ProtocolError):
                live.validate_result(value, request)

    def test_result_rejects_forged_flags_wrong_binding_extra_keys(self):
        request = submission().request
        values = []
        for name, content in [('schema_version', True), ('outcome', 'verified'), ('game_qualified', True),
                              ('read_status', 'success'), ('reason', 'anything'), ('extra', False)]:
            value = result_value(request); value[name] = content; values.append(value)
        for name in ('source_only', 'read_only', 'live_qualified', 'independent_evidence',
                     'atomic_snapshot', 'selection_authority', 'cancellation_authority'):
            value = result_value(request); value['observation'][name] = not value['observation'][name]; values.append(value)
        for name, content in [('actor_sim_id', '31'), ('request_id', '3' * 32), ('extra', None),
                              ('action_outcome', 'verified'), ('identity_scope', 'persistent')]:
            value = result_value(request); value['observation'][name] = content; values.append(value)
        for name in ('zone_id', 'household_id', 'client_id', 'save_guid', 'save_slot', 'zone_generation', 'session_id'):
            value = result_value(request); value['observation']['context'][name] = '9'; values.append(value)
        for name in ('pid', 'creation_filetime'):
            value = result_value(request); value['observation']['context']['process_epoch'][name] = '9'; values.append(value)
        for index, value in enumerate(values):
            with self.subTest(index=index), self.assertRaises(cw.ProtocolError):
                live.preview_result_receipt(value, request)

    def test_bad_rows_ids_indices_statuses_and_bounds(self):
        request = submission().request
        for field in ('index', 'tuning_guid64', 'runtime_instance_id'):
            value = result_value(request); del value['observation']['queued']['rows'][0][field]
            with self.assertRaises(cw.ProtocolError): live.validate_result(value, request)
        for index in (True, -1, 1, '0'):
            value = result_value(request); value['observation']['queued']['rows'][0]['index'] = index
            with self.assertRaises(cw.ProtocolError): live.validate_result(value, request)
        for ident in ('0', '01', str(U64 + 1), U64, None, True):
            value = result_value(request)
            value['observation']['queued']['rows'][0]['tuning_guid64']['value'] = ident
            with self.assertRaises(cw.ProtocolError): live.validate_result(value, request)
        value = result_value(request, 50, True)
        with self.assertRaises(cw.ProtocolError) as caught:
            live.preview_result_receipt(value, request)
        self.assertNotIsInstance(caught.exception, live.ResultBudgetExceeded)
        value = result_value(request)
        value['observation']['queued']['rows'][0]['runtime_instance_id'] = dict(status='unavailable', value=None, reason='private_text')
        with self.assertRaises(cw.ProtocolError): live.validate_result(value, request)

    def test_actual_complete_receipt_and_queue_budgets(self):
        request = submission().request
        value = result_value(request, 49, True)
        value['observation']['running'] = dict(status='unavailable', rows=None, reason='reader_exception')
        self.assertEqual(1012, node_count(value))
        envelope = live.preview_result_receipt(value, request)
        size = len(cw.encode_frame(envelope)) - 4
        result_size = len(cw.encode_frame(value)) - 4
        self.assertGreater(size, result_size)
        with patch.object(cw, 'MAX_FRAME_BYTES', size - 1):
            cw.encode_frame(value)  # A result-only estimate would wrongly accept.
            with self.assertRaises(live.ResultBudgetExceeded):
                live.preview_result_receipt(value, request)
        with patch.object(q, 'MAX_RESULT_NODES', 1011):
            with self.assertRaises(live.ResultBudgetExceeded):
                live.preview_result_receipt(value, request)
        # The dedicated budget class must never hide malformed data.
        value['outcome'] = 'verified'
        with self.assertRaises(cw.ProtocolError) as caught:
            live.preview_result_receipt(value, request)
        self.assertNotIsInstance(caught.exception, live.ResultBudgetExceeded)

    def test_snapshot_identity_and_verified_state_refusal(self):
        item = submission()
        for snapshot in (q.Snapshot('3' * 32, SESSION, 'pending'),
                         q.Snapshot(REQUEST, SESSION, 'verified', result=result_value(item.request))):
            with self.assertRaises(cw.ProtocolError):
                live.receipt(REQUEST, item.request_sha256, SESSION, EPOCH, snapshot)


@unittest.skipUnless(HOST, 'Host contract requires Python 3.11+.')
class LiveHostProtocolTests(unittest.TestCase):
    def host_item(self, value=None):
        value = submit_value() if value is None else value
        return host.parse_live_submit(value, value['session_id'], hw.ProcessEpoch(**value['process_epoch']))

    def test_host_companion_golden_roundtrip(self):
        item = self.host_item()
        self.assertEqual(submit_value(), item.to_wire())
        other = submission(hw.decode_payload(hw.encode_frame(item.to_wire())[4:]))
        self.assertEqual(item.request_sha256, other.request_sha256)
        self.assertEqual(item.canonical_payload, other.canonical_payload)
        envelope = live.preview_result_receipt(result_value(other.request), other.request)
        decoded = hw.decode_payload(cw.encode_frame(envelope)[4:])
        self.assertEqual(envelope, host.parse_live_receipt(decoded, item))
        query = host.LiveQueueReceiptRead(item)
        self.assertEqual((REQUEST, item.request_sha256), live.parse_receipt_read(query.to_wire(), SESSION, EPOCH))
        self.assertEqual((REQUEST, item.request_sha256), host.parse_live_receipt_read(
            query.to_wire(), SESSION, hw.ProcessEpoch(123, 456)))

    def test_host_nested_alias_and_shadowed_method_attacks(self):
        item = self.host_item()
        copied = host.LiveQueueSubmission(item.request)
        query = host.LiveQueueReceiptRead(item)
        baseline = item.canonical_payload
        object.__setattr__(item.request.arguments, 'actor_sim_id', '31')
        object.__setattr__(item.request.process_epoch, 'pid', 124)
        object.__setattr__(item.request.expected_context, 'zone_generation', 2)
        self.assertEqual(baseline, copied.canonical_payload)
        self.assertEqual(hashlib.sha256(baseline).hexdigest(), query.to_wire()['request_sha256'])
        object.__setattr__(copied.request, 'to_wire', lambda: {'forged': True})
        object.__setattr__(copied.request.expected_context, 'to_wire', lambda: {'forged': True})
        self.assertEqual(baseline, copied.canonical_payload)
        object.__setattr__(copied.request, 'operation', 'observe')
        with self.assertRaises(hw.ProtocolError):
            host.LiveQueueSubmission.to_wire(copied)

    def test_host_rejects_subclasses_and_old_sender_stays_closed(self):
        item = self.host_item()
        class Args(host.LiveQueueArguments): pass
        class Context(host.LiveQueueExpectedContext): pass
        class Submit(host.LiveQueueSubmission): pass
        class Read(host.LiveQueueReceiptRead): pass
        for callback in (lambda: Args('30'), lambda: Context('10', '20', '5', 4, '99', 1),
                         lambda: Submit(item.request), lambda: Read(item)):
            with self.assertRaises(hw.ProtocolError): callback()
        for message in (item, host.LiveQueueReceiptRead(item)):
            with patch.object(ht.socket, 'socket', side_effect=AssertionError('no socket')):
                with self.assertRaises(hw.ProtocolError) as caught:
                    ht.exchange_once(1234, message, authorize_peer=lambda *_: True)
                self.assertEqual('invalid_request', caught.exception.code)
        inner = dict(schema=1, kind='request', request_id=REQUEST, session_id=SESSION,
                     process_epoch=dict(pid=123, creation_filetime=456), operation='live.observe_queue',
                     arguments=dict(actor_sim_id='30'))
        with self.assertRaises(hw.ProtocolError):
            hw.parse_request(inner, session_id=SESSION, process_epoch=hw.ProcessEpoch(123, 456))

    def test_maximum_and_adjacent_exact_integer_roundtrips(self):
        for ident in (2**53 - 1, 2**53, 2**53 + 1, U64):
            value = submit_value()
            value['process_epoch'] = dict(pid=2**32 - 1, creation_filetime=ident)
            value['arguments']['actor_sim_id'] = str(ident)
            value['expected_context'] = dict(zone_id=str(ident), household_id=str(ident), client_id=str(ident),
                                             save_slot=ident, save_guid=str(ident), zone_generation=ident, mode='live')
            item = self.host_item(value)
            companion = submission(hw.decode_payload(item.canonical_payload))
            envelope = live.preview_result_receipt(result_value(companion.request, 49, True), companion.request)
            host.parse_live_receipt(hw.decode_payload(cw.encode_frame(envelope)[4:]), item)
        value['expected_context']['save_slot'] = 0
        value['expected_context']['save_guid'] = '0'
        self.host_item(value)

    def test_receipt_state_correlation_and_no_old_peer_success(self):
        item = self.host_item()
        original = submission()
        envelope = live.preview_result_receipt(result_value(original.request), original.request)
        for name, value in [('schema', 2), ('request_sha256', '0' * 64), ('request_id', '3' * 32),
                             ('session_id', '4' * 32), ('independent_evidence', True), ('extra', None)]:
            changed = copy.deepcopy(envelope); changed[name] = value
            with self.assertRaises(hw.ProtocolError): host.parse_live_receipt(changed, item)
        for schema in (2, 3):
            with self.assertRaises(hw.ProtocolError) as caught:
                host.parse_live_receipt(dict(schema=schema, kind='error', code='peer_rejected', outcome_known=False), item)
            self.assertEqual('peer_rejected', caught.exception.code)
        for state in ('pending', 'started', 'refused', 'unknown'):
            snap = q.Snapshot(REQUEST, SESSION, state, reason='fixture_reason' if state in ('refused', 'unknown') else None)
            host.parse_live_receipt(live.receipt(REQUEST, original.request_sha256, SESSION, EPOCH, snap), item)
        host.parse_live_receipt(live.receipt(REQUEST, original.request_sha256, SESSION, EPOCH), item)
        malformed = copy.deepcopy(envelope); malformed['adapter_result']['observation']['queued']['rows'][0]['index'] = True
        with self.assertRaises(hw.ProtocolError): host.parse_live_receipt(malformed, item)

    def test_actual_python37_canonical_and_receipt_parity_when_available(self):
        executable = os.environ.get('FOUNDRY_COMPANION_PYTHON37')
        if not executable:
            self.skipTest('Set FOUNDRY_COMPANION_PYTHON37 for actual Python 3.7 compatibility.')
        program = '''import json, sys
assert sys.version_info[:2] == (3, 7), 'Expected the configured actual Python 3.7 interpreter'
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from test_companion_live_protocol import submission, result_value, unavailable_result
from foundry_companion import wire, live_wire
for raw in sys.stdin:
    value = wire.decode_payload(bytes.fromhex(raw.strip()))
    item = submission(value)
    results = [result_value(item.request, 49, True), unavailable_result()]
    print(json.dumps({'digest': item.request_sha256, 'payload': item.canonical_payload.hex(),
        'receipts': [wire.encode_frame(live_wire.preview_result_receipt(result, item.request))[4:].hex() for result in results]}))
'''
        values = []
        for ttl in (5, 5.0):
            value = submit_value(); value['ttl_seconds'] = ttl
            value['process_epoch'] = dict(pid=2**32 - 1, creation_filetime=U64)
            value['arguments']['actor_sim_id'] = str(U64)
            value['expected_context'].update(save_slot=U64, save_guid=str(U64), zone_generation=U64)
            values.append(self.host_item(value))
        result = subprocess.run([executable, '-B', '-S', '-s', '-c', program, str(PACKAGE / 'companion-src'), str(PACKAGE / 'tests')],
                input=''.join(item.canonical_payload.hex() + '\n' for item in values), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        outputs = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(2, len(outputs))
        for item, output in zip(values, outputs):
            self.assertEqual(item.request_sha256, output['digest'])
            self.assertEqual(item.canonical_payload.hex(), output['payload'])
            for frame in output['receipts']:
                host.parse_live_receipt(hw.decode_payload(bytes.fromhex(frame)), item)


if __name__ == '__main__':
    unittest.main()
