"""Schema-3 host exchange: fake sockets first, then isolated authenticated fixtures.

All identities/readers/keys are fabricated. No real game, native service, profile,
production credentials or CLI enablement; source/fixture success is not evidence.
"""
import copy
import errno
import hashlib
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PACKAGE / 'src'), str(PACKAGE / 'companion-src')]
from sims4foundry_cli import companion_protocol as wire
from sims4foundry_cli import companion_live_protocol as live
from sims4foundry_cli import companion_live_transport as client
from sims4foundry_cli import companion_transport as legacy
from sims4foundry_cli import companion_journal as ledger, companion_auth as auth
from sims4foundry_cli.errors import FoundryError
from test_companion_live_protocol import submit_value, submission as companion_item, result_value


def submission(ttl=5.0, **changes):
    value = submit_value()
    value['ttl_seconds'] = ttl
    value.update(changes)
    return live.parse_live_submit(value, value['session_id'], wire.ProcessEpoch(**value['process_epoch']))


def receipt(item, state='accepted', reason=None):
    value = live.LiveQueueSubmission.to_wire(item)
    result = None
    if state == 'adapter_returned':
        result = result_value(companion_item(value).request)
    return dict(schema=3, kind='receipt', request_id=value['request_id'], session_id=value['session_id'],
                process_epoch=value['process_epoch'], request_sha256=item.request_sha256,
                state=state, reason=reason, adapter_result=result, independent_evidence=False)


class Clock:
    def __init__(self): self.value = 100.0
    def __call__(self): return self.value


class FakeSocket:
    """Scripted socket only; never calls a network API."""
    def __init__(self, response, callback=None, fragment=None):
        self.response = bytearray(response)
        self.callback = callback or (lambda stage: None)
        self.fragment = fragment
        self.sent, self.timeouts, self.addresses, self.shutdowns, self.read_sizes = [], [], [], [], []
        self.closed = False
        self.received = 0
    def __enter__(self): return self
    def __exit__(self, *_): self.closed = True
    def settimeout(self, value): self.timeouts.append(value)
    def connect(self, address): self.addresses.append(address); self.callback('connect')
    def sendall(self, frame): self.sent.append(frame); self.callback('send')
    def shutdown(self, how): self.shutdowns.append(how); self.callback('shutdown')
    def recv(self, size):
        self.read_sizes.append(size)
        self.callback('eof' if not self.response else 'prefix' if self.received < 4 else 'payload')
        size = min(size, self.fragment or size)
        part = bytes(self.response[:size])
        del self.response[:size]
        self.received += len(part)
        return part


class FakeExchangeTests(unittest.TestCase):
    def run_exchange(self, item=None, *, query=False, response=None, callback=None,
                     factory_callback=None, authorize=None, clock=None, fragment=None):
        item = submission() if item is None else item
        message = live.LiveQueueReceiptRead(item) if query else item
        response = wire.encode_frame(receipt(item)) if response is None else response
        sock = FakeSocket(response, callback, fragment)
        self.sock, self.views, self.deadlines = sock, [], []
        def peer(connection, deadline, view):
            self.assertIs(sock, connection)
            self.views.append(view); self.deadlines.append(deadline)
            return authorize(view) if authorize else True
        def create(*args):
            self.assertEqual((socket.AF_INET, socket.SOCK_STREAM), args)
            if factory_callback: factory_callback(message)
            return sock
        with patch.object(client.socket, 'socket', side_effect=create) as factory, patch.object(
                client, 'monotonic', clock or Clock()):
            try:
                return client.exchange_once(12345, message, authorize_peer=peer)
            finally:
                self.assertEqual(1, factory.call_count)
                self.assertTrue(sock.closed)
                self.assertLessEqual(len(sock.addresses), 1)
                self.assertLessEqual(len(sock.sent), 1)
                self.assertLessEqual(len(sock.shutdowns), 1)
                self.assertTrue(all(0 < timeout <= 2 for timeout in sock.timeouts))

    def test_exact_submission_and_receipt_query_frames(self):
        for ttl in (5, 5.0):
            for query in (False, True):
                item = submission(ttl)
                self.assertEqual(receipt(item), self.run_exchange(item, query=query, fragment=1))
                expected = (live.LiveQueueReceiptRead.to_wire(live.LiveQueueReceiptRead(item))
                            if query else live.LiveQueueSubmission.to_wire(item))
                self.assertEqual([wire.encode_frame(expected)], self.sock.sent)
                self.assertEqual([('127.0.0.1', 12345)], self.sock.addresses)
                self.assertEqual([socket.SHUT_WR], self.sock.shutdowns)
                self.assertEqual(1, self.sock.read_sizes[-1])
                self.assertEqual([102.0], self.deadlines)
                self.assertIsNot(item, self.views[0])

    def test_default_denial_invalid_ports_timeouts_and_exact_message_types(self):
        class Integer(int): pass
        class Submit(live.LiveQueueSubmission): pass
        class Read(live.LiveQueueReceiptRead): pass
        invalid = [dict(port=x) for x in (True, 0, -1, 65536, 1.0, Integer(2))]
        invalid += [dict(timeout_seconds=x) for x in (True, 0, -1, 5.1, float('nan'), float('inf'), Integer(2))]
        old = legacy.Submission(wire.Request('8' * 32, '2' * 32, wire.ProcessEpoch(123, 456),
                                wire.Operation.OBSERVE, wire.ObserveArguments()))
        invalid += [dict(message=x) for x in (None, {}, old, legacy.ReceiptRead(old),
                                              object.__new__(Submit), object.__new__(Read))]
        invalid += [dict(authorize_peer=x) for x in (None, True, 1)]
        with patch.object(client.socket, 'socket', side_effect=AssertionError('No socket permitted')):
            for changed in invalid:
                args = dict(port=12345, message=submission(), authorize_peer=lambda *_: True)
                args.update(changed)
                with self.subTest(changed=changed), self.assertRaises(wire.ProtocolError):
                    client.exchange_once(**args)
            with self.assertRaises(wire.ProtocolError) as caught:
                client.exchange_once(12345, submission())
            self.assertEqual('host_policy_required', caught.exception.code)

    def test_exact_nested_types_checked_before_io_even_for_receipt_read(self):
        cases = [('request', object()), ('arguments', object()), ('expected_context', object()),
                 ('process_epoch', object()), ('operation', 'observe'), ('ttl_seconds', True)]
        for query in (False, True):
            for field, value in cases:
                item = submission()
                message = live.LiveQueueReceiptRead(item) if query else item
                target = message.submission if query else message
                object.__setattr__(target if field == 'request' else target.request, field, value)
                with self.subTest(query=query, field=field), patch.object(client.socket, 'socket',
                        side_effect=AssertionError('No socket permitted')), self.assertRaises(wire.ProtocolError):
                    client.exchange_once(12345, message, authorize_peer=lambda *_: True)

    def test_original_captured_before_socket_constructor_for_submit_and_query(self):
        for query in (False, True):
            item = submission(5)
            expected = receipt(item, 'adapter_returned')
            def mutate(message):
                original = message.submission if query else message
                object.__setattr__(original.request.arguments, 'actor_sim_id', '31')
                object.__setattr__(original.request.expected_context, 'client_id', '6')
                object.__setattr__(original.request.process_epoch, 'creation_filetime', 999)
                object.__setattr__(original.request, 'session_id', '9' * 32)
                object.__setattr__(original.request, 'ttl_seconds', 5.0)
            parsed = []
            actual_parse = live.parse_live_receipt
            def parse(value, view): parsed.append(view); return actual_parse(value, view)
            with patch.object(live, 'parse_live_receipt', side_effect=parse):
                self.assertEqual(expected, self.run_exchange(item, query=query, factory_callback=mutate,
                                                            response=wire.encode_frame(expected)))
            baseline = submission(5)
            expected_frame = (live.LiveQueueReceiptRead(baseline).to_wire() if query else baseline.to_wire())
            self.assertEqual([wire.encode_frame(expected_frame)], self.sock.sent)
            for view in (self.views[0], parsed[0]):
                self.assertEqual(submission(5).canonical_payload, view.canonical_payload)
            for child in ('request',):
                self.assertIsNot(getattr(self.views[0], child), getattr(parsed[0], child))
            for child in ('arguments', 'expected_context', 'process_epoch'):
                self.assertIsNot(getattr(self.views[0].request, child), getattr(parsed[0].request, child))

    def test_every_persistent_peer_change_prevents_application_send(self):
        changes = [(None, 'request', submission().request), ('request', 'request_id', '9' * 32),
                   ('request', 'session_id', '9' * 32), ('request', 'ttl_seconds', 5),
                   ('request', 'operation', 'observe'), ('arguments', 'actor_sim_id', '31'),
                   ('process_epoch', 'pid', 124), ('process_epoch', 'creation_filetime', 457)]
        changes += [('expected_context', name, value) for name, value in
                    [('zone_id', '11'), ('household_id', '21'), ('client_id', '6'), ('save_slot', 5),
                     ('save_guid', '100'), ('zone_generation', 2), ('mode', 'cas')]]
        changes += [('request', name, object()) for name in ('arguments', 'expected_context', 'process_epoch')]
        # A replacement with unchanged exact intent is harmless; use a changed child.
        object.__setattr__(changes[0][2].arguments, 'actor_sim_id', '31')
        for query in (False, True):
            for section, name, value in changes:
                def mutate(view):
                    target = view if section is None else view.request if section == 'request' else getattr(view.request, section)
                    object.__setattr__(target, name, value)
                    return True
                with self.subTest(query=query, field=(section, name)), self.assertRaises(wire.ProtocolError) as caught:
                    self.run_exchange(query=query, authorize=mutate)
                self.assertEqual('peer_intent_changed', caught.exception.code)
                self.assertEqual([], self.sock.sent)

    def test_shadowed_serializers_never_execute(self):
        forbidden = Mock(side_effect=AssertionError('Shadow serializer executed'))
        item = submission()
        def shadow(view):
            for target in (view, view.request, view.request.expected_context, view.request.process_epoch):
                object.__setattr__(target, 'to_wire', forbidden)
            return True
        shadow(item)
        for query in (False, True):
            self.assertEqual('accepted', self.run_exchange(item, query=query, authorize=shadow)['state'])
        forbidden.assert_not_called()

    def test_retained_policy_alias_cannot_redirect_parser_at_any_later_stage(self):
        for query in (False, True):
            for stage in ('send', 'shutdown', 'prefix', 'payload', 'eof', 'parse'):
                item = submission()
                expected = receipt(item, 'adapter_returned')
                changed = submission()
                object.__setattr__(changed.request.arguments, 'actor_sim_id', '31')
                object.__setattr__(changed.request.expected_context, 'client_id', '6')
                for forged in (False, True):
                    def mutate():
                        object.__setattr__(self.views[0], 'request', changed.request)
                    def callback(at):
                        if at == stage: mutate()
                    actual_parse = live.parse_live_receipt
                    def parse(value, view):
                        if stage == 'parse': mutate()
                        self.assertIsNot(view, self.views[0])
                        return actual_parse(value, view)
                    response = receipt(changed, 'adapter_returned') if forged else expected
                    with self.subTest(query=query, stage=stage, forged=forged), patch.object(
                            live, 'parse_live_receipt', side_effect=parse):
                        if forged:
                            with self.assertRaises(wire.ProtocolError):
                                self.run_exchange(item, query=query, response=wire.encode_frame(response), callback=callback)
                        else:
                            self.assertEqual(expected, self.run_exchange(item, query=query,
                                            response=wire.encode_frame(response), callback=callback))

    def test_only_exact_true_peer_policy_can_send_and_exceptions_propagate(self):
        for verdict in (False, None, 1, 'yes'):
            with self.assertRaises(wire.ProtocolError) as caught:
                self.run_exchange(authorize=lambda _: verdict)
            self.assertEqual('peer_denied', caught.exception.code)
            self.assertEqual([], self.sock.sent)
        failure = RuntimeError('test-only callback failure')
        def fail(_): raise failure
        with self.assertRaises(RuntimeError) as caught: self.run_exchange(authorize=fail)
        self.assertIs(failure, caught.exception)
        self.assertEqual([], self.sock.sent)

    def test_one_deadline_checked_after_every_blocking_or_validation_stage(self):
        for stage in ('constructor', 'connect', 'auth', 'send', 'shutdown', 'prefix', 'payload', 'eof', 'decode', 'parse'):
            clock = Clock()
            item = submission()
            response = wire.encode_frame(receipt(item))
            def expire(at):
                if at == stage: clock.value = 102.0
            decode, parse = wire.decode_payload, live.parse_live_receipt
            def decoding(raw):
                result = decode(raw)
                if raw == response[4:]: expire('decode')
                return result
            def parsing(value, view):
                result = parse(value, view); expire('parse'); return result
            def authorize(_): expire('auth'); return True
            with self.subTest(stage=stage), patch.object(wire, 'decode_payload', side_effect=decoding), patch.object(
                    live, 'parse_live_receipt', side_effect=parsing), self.assertRaises(wire.ProtocolError) as caught:
                self.run_exchange(item, response=response, callback=expire, clock=clock, fragment=1,
                                  factory_callback=lambda _: expire('constructor'), authorize=authorize)
            self.assertEqual('connection_deadline', caught.exception.code)
            if stage in ('constructor', 'connect', 'auth'):
                self.assertEqual([], self.sock.sent)
            self.assertEqual(sorted(self.sock.timeouts, reverse=True), self.sock.timeouts)

    def test_fragmented_reads_never_renew_deadline(self):
        clock = Clock()
        def fragment(stage):
            if stage in ('prefix', 'payload'): clock.value += .125
        with self.assertRaises(wire.ProtocolError) as caught:
            self.run_exchange(clock=clock, callback=fragment, fragment=1)
        self.assertEqual('connection_deadline', caught.exception.code)
        self.assertEqual(16, len(self.sock.read_sizes))
        self.assertEqual(sorted(self.sock.timeouts, reverse=True), self.sock.timeouts)

    def test_exact_frame_length_eof_codec_bounds_and_no_second_reply(self):
        frame = wire.encode_frame(receipt(submission()))
        cases = [(b'', 'truncated_frame'), (b'\0\0', 'truncated_frame'), (frame[:-1], 'truncated_frame'),
                 (struct.pack('>I', 0), 'invalid_frame'),
                 (struct.pack('>I', wire.MAX_FRAME_BYTES + 1), 'invalid_frame'),
                 (frame + b'x', 'extra_frame'), (frame + frame, 'extra_frame')]
        for response, code in cases:
            with self.subTest(code=code), self.assertRaises(wire.ProtocolError) as caught:
                self.run_exchange(response=response)
            self.assertEqual(code, caught.exception.code)
            if code == 'invalid_frame': self.assertEqual([4], self.sock.read_sizes)
        raw = frame[4:].replace(b'"schema":3', b'"schema":3,"schema":3')
        with self.assertRaises(wire.ProtocolError):
            self.run_exchange(response=struct.pack('>I', len(raw)) + raw)

    def test_old_peer_errors_are_strict_uncertain_failures_not_fallback(self):
        for version in (2, 3):
            value = dict(schema=version, kind='error', code='peer_rejected', outcome_known=False)
            with self.assertRaises(wire.ProtocolError) as caught:
                self.run_exchange(response=wire.encode_frame(value))
            self.assertEqual('peer_rejected', caught.exception.code)
            for key, changed in [('outcome_known', True), ('schema', 3.0), ('code', 'x' * 65),
                                 ('code', 'secret detail'), ('extra', None)]:
                bad = dict(value); bad[key] = changed
                with self.subTest(version=version, key=key), self.assertRaises(wire.ProtocolError) as caught:
                    self.run_exchange(response=wire.encode_frame(bad))
                self.assertEqual('invalid_response', caught.exception.code)
            frame = wire.encode_frame(value)
            with self.assertRaises(wire.ProtocolError) as caught:
                self.run_exchange(response=frame[:-1])
            self.assertEqual('truncated_frame', caught.exception.code)
        value = receipt(submission()); value['schema'] = 2
        with self.assertRaises(wire.ProtocolError): self.run_exchange(response=wire.encode_frame(value))

    def test_socket_failures_propagate_without_retry_or_second_frame(self):
        for stage in ('connect', 'send', 'shutdown', 'prefix', 'payload', 'eof'):
            failure = ConnectionResetError(errno.ECONNRESET, 'public fixture reset')
            def fail(at):
                if at == stage: raise failure
            with self.subTest(stage=stage), self.assertRaises(ConnectionResetError) as caught:
                self.run_exchange(callback=fail)
            self.assertIs(failure, caught.exception)
            if stage == 'connect': self.assertEqual([], self.sock.sent)

    def test_exact_large_integer_binding_and_legacy_sender_unchanged(self):
        for integer in (2**53, 2**53 + 1, 2**64 - 1):
            value = submit_value()
            value['process_epoch']['creation_filetime'] = integer
            value['arguments']['actor_sim_id'] = str(integer)
            value['expected_context'].update(save_slot=integer, zone_generation=integer)
            item = live.parse_live_submit(value, value['session_id'], wire.ProcessEpoch(**value['process_epoch']))
            self.assertEqual(receipt(item), self.run_exchange(item))
        with patch.object(legacy.socket, 'socket', side_effect=AssertionError('No legacy socket')):
            for item in (submission(), live.LiveQueueReceiptRead(submission())):
                with self.assertRaises(wire.ProtocolError) as caught:
                    legacy.exchange_once(12345, item, authorize_peer=lambda *_: True)
                self.assertEqual('invalid_request', caught.exception.code)


class OwnedCall:
    """Bounded owned client worker; never lose an exception on background stderr."""
    def __init__(self, callback):
        self.result, self.error, self.done = None, None, threading.Event()
        def run():
            try: self.result = callback()
            except BaseException as error: self.error = error
            finally: self.done.set()
        self.thread = threading.Thread(target=run, name='foundry-live-fixture-client')
        self.thread.start()
    def join(self):
        self.thread.join(5)
        if self.thread.is_alive(): raise AssertionError('Owned Live client worker failed to stop.')


class ExchangeScenarios:
    process = False
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'journal'
        self.journal = ledger.create_journal(self.root)
        self.item = submission()
        self.calls, self.errors, self.replies, self.before_socket, self.before_auth = [], [], [], [], []
        self.admissions = []

    def fixture(self, fault=None, rows=1):
        from live_exchange_fixture import Fixture, ProcessFixture
        fixture = ProcessFixture(self, self.root, fault, rows) if self.process else Fixture(self.root, fault, rows)
        if not self.process:
            from live_exchange_fixture import assert_no_game_imports
            assert_no_game_imports()
            self.addCleanup(assert_no_game_imports)
        if not self.process: self.addCleanup(fixture.close)
        return fixture

    def records(self):
        return [json.loads(path.read_text()) for path in sorted((self.root / 'records').glob('*.json'))]

    def error_details(self):
        error = self.errors[-1] if self.errors else None
        return 'fixture callback: type=%s code=%s errno=%s' % (
            type(error).__name__, getattr(error, 'code', None), getattr(error, 'errno', None))

    def admit(self, message, ticket):
        self.admissions.append(self.records())
        return True

    def exchange(self, fixture, timeout=4, key=None):
        from live_exchange_fixture import KEY
        peer = auth.SessionKeyPeer(KEY if key is None else key)
        def authorize(connection, deadline, view):
            self.before_auth.append(self.records())
            return peer(connection, deadline, view)
        def exchange(message, ticket):
            self.calls.append((message, ticket))
            self.before_socket.append(self.records())
            try:
                reply = client.exchange_once(fixture.port, message, authorize_peer=authorize, timeout_seconds=timeout)
                self.replies.append(reply)
                return reply
            except BaseException as error:
                self.errors.append(error)
                raise
        return exchange

    def submit(self, fixture, **settings):
        return self.journal.submit_once(self.item, authorize=self.admit, exchange=self.exchange(fixture, **settings))

    def reconcile(self, fixture):
        return ledger.Journal(self.root).reconcile(self.item.request.request_id,
                    authorize=self.admit, exchange=self.exchange(fixture))

    def no_replay(self):
        forbidden = Mock(side_effect=AssertionError('Original intent was submitted twice'))
        reopened = ledger.Journal(self.root)
        expected = reopened.status(self.item.request.request_id)
        self.assertEqual(expected, reopened.submit_once(self.item, authorize=forbidden, exchange=forbidden))
        forbidden.assert_not_called()
        return expected

    def assert_intent(self, records):
        self.assertEqual(['intent', 'attempt'], [row['kind'] for row in records])
        stored = records[0]['data']
        self.assertEqual(self.item.canonical_payload, wire.encode_frame(stored['submission'])[4:])
        self.assertEqual(self.item.request_sha256, stored['request_sha256'])
        self.assertEqual(self.calls[0][1].to_wire(), records[1]['data'])

    def assert_summary(self):
        for row in self.records():
            if row['kind'] == 'receipt':
                snapshot = row['data']['snapshot']
                self.assertEqual({'state', 'reason', 'adapter_outcome', 'receipt_sha256'}, set(snapshot))
        for path in (self.root / 'records').glob('*.json'):
            self.assertLess(path.stat().st_size, ledger.MAX_RECORD_BYTES)
            saved = path.read_text()
            for body_field in ('observation', 'read_status', 'tuning_guid64', 'runtime_instance_id'):
                self.assertNotIn('"' + body_field + '"', saved)

    def assert_receipt_only(self, fixture, expected_count=2):
        stats = fixture.stats()
        self.assertEqual(['submit'] + ['receipt_read'] * (expected_count - 1),
                         [m['kind'] for m in stats['messages']], self.error_details())
        self.assertEqual(1, stats['submissions'])
        self.assertEqual(1, stats['executions'])
        self.assertTrue(stats['owner_only']); self.assertTrue(stats['worker_only'])
        for message, ticket in self.calls[1:]:
            self.assertIs(type(message), live.LiveQueueReceiptRead)
            self.assertEqual(self.item.canonical_payload, message.submission.canonical_payload)
            self.assertEqual(self.item.request_sha256, ticket.request_sha256)
            self.assertEqual('receipt_read', ticket.kind)
        self.assert_summary()

    def happy(self, ttl, rows):
        self.item = submission(ttl)
        fixture = self.fixture(rows=rows)
        try:
            status = self.submit(fixture)
        except FoundryError as error:
            raise AssertionError('Unexpected fixture submission failure: ' + self.error_details()) from error
        self.assertEqual('accepted', status.state)
        self.assertFalse(status.mutation)
        self.assertEqual(['intent'], [row['kind'] for row in self.admissions[0]])
        for records in (self.before_socket[0], self.before_auth[0], fixture.disk[0]): self.assert_intent(records)
        self.assertEqual(self.item.canonical_payload, wire.encode_frame(fixture.stats()['messages'][0])[4:])
        self.assertEqual(0, fixture.stats()['reader_calls'])
        self.assertEqual(0, fixture.stats()['executions'])
        self.assertEqual('unverified', fixture.tick())
        self.assertIsNone(fixture.tick())
        result = fixture.last_result
        count = fixture.stats()['reader_calls']
        for _ in range(2):
            try:
                status = self.reconcile(fixture)
            except FoundryError as error:
                raise AssertionError('Unexpected fixture reconciliation failure: ' + self.error_details()) from error
            self.assertEqual('adapter_returned', status.state)
            self.assertEqual('unverified', status.receipt.adapter_outcome)
            self.assertFalse(status.mutation)
            self.assertFalse(status.independent_evidence); self.assertFalse(status.live_qualified)
            response = self.replies[-1]
            self.assertEqual(result, response['adapter_result'])
            self.assertEqual(response, live.parse_live_receipt(response, self.item))
            self.assertEqual(hashlib.sha256(wire.encode_frame(response)[4:]).hexdigest(), status.receipt.receipt_sha256)
            self.assertFalse(result['game_qualified'])
            self.assertIsNone(fixture.tick())
            self.assertEqual(count, fixture.stats()['reader_calls'])
        if rows == 49:
            self.assertEqual('observed', result['read_status'])
            self.assertEqual(49, len(result['observation']['queued']['rows']))
            self.assertEqual([], result['observation']['running']['rows'])
            self.assertFalse(result['observation']['live_qualified'])
            self.assertFalse(result['observation']['independent_evidence'])
        else:
            self.assertEqual(dict(schema_version=1, outcome='unverified', game_qualified=False,
                read_status='unavailable', observation=None, reason='row_limit_exceeded'), result)
        self.no_replay()
        self.assert_receipt_only(fixture, 3)
        fixture.close()

    def test_authenticated_integer_ttl_49_rows(self): self.happy(5, 49)
    def test_authenticated_float_ttl_50_rows_whole_read_unavailable(self): self.happy(5.0, 50)

    def lost_reply(self, fault):
        fixture = self.fixture(fault)
        call = OwnedCall(lambda: self.submit(fixture, timeout=1 if fault == 'timeout' else 3))
        self.addCleanup(call.join)
        try:
            if self.process: self.assertEqual({'ready': True}, fixture.command('ready'))
            else: self.assertTrue(fixture.ready.wait(4), 'Authenticated admission did not reach the reply gate')
            self.assertEqual(1, fixture.stats()['pending'])
            self.assertEqual(0, fixture.stats()['reader_calls'])
            self.assertEqual('unverified', fixture.tick())
            self.assertIsNone(fixture.tick())
            self.assertEqual(1, fixture.stats()['executions'])
            if fault == 'timeout': self.assertTrue(call.done.wait(4), 'Actual client timeout did not occur')
        finally:
            if self.process: self.assertEqual({'released': True}, fixture.command('release'))
            else: fixture.release.set()
        call.join()
        if not self.process:
            self.assertTrue(fixture.finished.wait(4)); self.assertTrue(fixture.first_worker_done.wait(4))
        self.assertIsInstance(call.error, FoundryError)
        self.assertEqual('exchange_uncertain', call.error.code)
        self.assertIsNone(call.result)
        self.assertEqual(1, len(self.errors))
        error = self.errors[0]
        if fault == 'timeout':
            self.assertIsInstance(error, (socket.timeout, wire.ProtocolError))
            if isinstance(error, wire.ProtocolError): self.assertEqual('connection_deadline', error.code)
        elif isinstance(error, wire.ProtocolError): self.assertEqual('truncated_frame', error.code)
        else:
            self.assertIsInstance(error, OSError)
            self.assertNotIsInstance(error, (socket.timeout, TimeoutError))
            self.assertIn(error.errno, (errno.ECONNRESET, errno.ECONNABORTED, errno.ENOTCONN, errno.EPIPE, 10053, 10054, 10057))
        self.assertEqual([], self.replies)
        uncertain = self.no_replay()
        self.assertEqual('unknown', uncertain.state)
        self.assertTrue(uncertain.uncertainty_latched); self.assertFalse(uncertain.mutation)
        self.assertEqual('possible', uncertain.transmission)
        result = fixture.last_result
        try:
            status = self.reconcile(fixture)
        except FoundryError as error:
            underlying = self.errors[-1] if self.errors else None
            raise AssertionError('Receipt-only fixture reconciliation failed: type=%s code=%s errno=%s' %
                                 (type(underlying).__name__, getattr(underlying, 'code', None),
                                  getattr(underlying, 'errno', None))) from error
        self.assertEqual('adapter_returned', status.state)
        self.assertEqual(result, self.replies[-1]['adapter_result'])
        self.assertTrue(status.uncertainty_latched)
        self.assertFalse(status.independent_evidence); self.assertFalse(status.live_qualified)
        self.assert_receipt_only(fixture)
        self.assert_intent(fixture.disk[0])
        if fault == 'drop': self.assertEqual('response_lost', fixture.stats()['blocked'])
        self.no_replay()
        fixture.close()

    def test_actual_reply_loss_receipt_only_recovery(self): self.lost_reply('drop')
    def test_actual_timeout_receipt_only_recovery(self): self.lost_reply('timeout')

    def test_callback_failure_after_exact_accepted_reply_and_owner_read(self):
        fixture = self.fixture()
        exchange = self.exchange(fixture)
        failure, milestones = RuntimeError('PUBLIC-TEST-POST-REPLY-FAULT'), []
        def fail(message, ticket):
            result = exchange(message, ticket)
            milestones.append(result['state'])
            milestones.append(fixture.tick())
            raise failure
        with self.assertRaises(FoundryError) as caught:
            self.journal.submit_once(self.item, authorize=self.admit, exchange=fail)
        self.assertEqual('exchange_uncertain', caught.exception.code)
        self.assertIs(failure, caught.exception.__context__)
        self.assertEqual(['accepted', 'unverified'], milestones)
        self.assertEqual([], self.errors)
        self.assertIsNone(fixture.stats()['blocked'])
        self.assertTrue(self.no_replay().uncertainty_latched)
        self.assertEqual('adapter_returned', self.reconcile(fixture).state)
        self.assert_receipt_only(fixture)
        self.assertNotIn('PUBLIC-TEST-POST-REPLY-FAULT', json.dumps(self.records()))
        fixture.close()

    def auth_failure(self, kind):
        fixture = self.fixture(kind if kind.startswith('auth_') else None)
        key = None
        if kind == 'key': key = b'x' * 32
        if kind == 'session': self.item = submission(session_id='9' * 32)
        if kind == 'epoch': self.item = submission(process_epoch=dict(pid=123, creation_filetime=457))
        with self.assertRaises(FoundryError) as caught: self.submit(fixture, key=key)
        self.assertEqual('exchange_uncertain', caught.exception.code)
        self.assertEqual(1, len(self.errors))
        self.assertIsInstance(self.errors[0], auth.PeerAuthError)
        if self.process: self.assertEqual({'worker_done': True}, fixture.command('worker_done'))
        else: self.assertTrue(fixture.first_worker_done.wait(4))
        stats = fixture.stats()
        self.assertEqual([], stats['messages'])
        self.assertEqual(0, stats['submissions']); self.assertEqual(0, stats['reader_calls'])
        self.assertIsNone(fixture.tick())
        if kind.startswith('auth_'): self.assertEqual(1, stats['auth_ready_fault'], self.error_details())
        self.assertTrue(self.no_replay().uncertainty_latched)
        self.assertFalse(self.no_replay().mutation)
        fixture.close()

    def test_wrong_key_no_application_admission(self): self.auth_failure('key')
    def test_wrong_session_no_application_admission(self): self.auth_failure('session')
    def test_wrong_epoch_no_application_admission(self): self.auth_failure('epoch')
    def test_missing_final_auth_confirmation_no_application_admission(self): self.auth_failure('auth_missing_ready')
    def test_invalid_final_auth_confirmation_no_application_admission(self): self.auth_failure('auth_invalid_ready')

    def test_lost_history_is_not_permission_to_resubmit(self):
        old = self.fixture()
        self.submit(old)
        old.close()
        replacement = self.fixture()
        status = self.reconcile(replacement)
        self.assertEqual('not_found', status.state)
        self.assertEqual('history_missing', status.receipt.reason)
        self.assertTrue(status.uncertainty_latched)
        self.assertEqual(['receipt_read'], [m['kind'] for m in replacement.stats()['messages']])
        self.assertEqual(0, replacement.stats()['executions'])
        self.assertEqual(0, replacement.stats()['submissions'])
        self.assertEqual('history_missing', replacement.stats()['blocked'])
        self.no_replay()
        replacement.close()


class AuthenticatedExchangeTests(ExchangeScenarios, unittest.TestCase):
    def test_uncertain_live_read_never_clears_existing_mutation_barrier(self):
        # A preexisting mutation intent remains unresolved across unrelated reads.
        mutation = legacy.Submission(wire.Request('9' * 32, self.item.request.session_id,
            wire.ProcessEpoch(123, 456), wire.Operation.SET_SPEED, wire.SetSpeedArguments(1)),
            5.0, legacy.ExpectedContext('10', '20', 4, '99'))
        self.journal.record_intent(mutation)
        fixture = self.fixture()
        fixture.server._queue.block_mutations('unresolved_mutation')
        exchange = self.exchange(fixture)
        def fail(message, ticket): exchange(message, ticket); raise RuntimeError('fixture fault')
        with self.assertRaises(FoundryError):
            self.journal.submit_once(self.item, authorize=self.admit, exchange=fail)
        self.assertFalse(self.no_replay().mutation)
        self.assertEqual('unverified', fixture.tick(), self.error_details())
        self.assertEqual('adapter_returned', self.reconcile(fixture).state)
        self.assertEqual('unresolved_mutation', fixture.stats()['blocked'])
        forbidden = Mock(side_effect=AssertionError('Replacement mutation reached a callback'))
        replacement = legacy.Submission(wire.Request('8' * 32, '7' * 32, wire.ProcessEpoch(124, 457),
            wire.Operation.SET_SPEED, wire.SetSpeedArguments(1)), 5.0, legacy.ExpectedContext('10', '20', 4, '99'))
        with self.assertRaises(FoundryError) as caught:
            ledger.Journal(self.root).submit_once(replacement, authorize=forbidden, exchange=forbidden)
        self.assertEqual('mutation_unresolved', caught.exception.code)
        forbidden.assert_not_called()
        fixture.close()

    def test_uncertain_live_read_alone_creates_no_journal_mutation_barrier(self):
        fixture = self.fixture()
        exchange = self.exchange(fixture)
        def fail(message, ticket): exchange(message, ticket); raise RuntimeError('fixture fault')
        with self.assertRaises(FoundryError):
            self.journal.submit_once(self.item, authorize=self.admit, exchange=fail)
        self.assertFalse(self.no_replay().mutation)
        self.assertIsNone(fixture.stats()['blocked'])
        mutation = legacy.Submission(wire.Request('9' * 32, '2' * 32, wire.ProcessEpoch(123, 456),
            wire.Operation.SET_SPEED, wire.SetSpeedArguments(1)), 5.0, legacy.ExpectedContext('10', '20', 4, '99'))
        # Persist only: no additional dispatch or authorization is implied.
        self.assertEqual('intent_recorded', ledger.Journal(self.root).record_intent(mutation).state)
        fixture.close()

    def test_refusal_after_uncertainty_cannot_establish_non_dispatch(self):
        fixture = self.fixture()
        exchange = self.exchange(fixture)
        def fail(message, ticket): exchange(message, ticket); raise RuntimeError('fixture fault')
        with self.assertRaises(FoundryError):
            self.journal.submit_once(self.item, authorize=self.admit, exchange=fail)
        fixture.policy.authorize_dispatch = lambda *_: False
        self.assertEqual('refused', fixture.tick())
        with self.assertRaises(FoundryError) as caught: self.reconcile(fixture)
        self.assertEqual('receipt_uncertain', caught.exception.code)
        self.assertTrue(self.no_replay().uncertainty_latched)
        self.assertEqual(0, fixture.stats()['reader_calls'])
        self.assertEqual(['submit', 'receipt_read'], [m['kind'] for m in fixture.stats()['messages']])
        fixture.close()

    def test_authenticated_old_peer_error_never_downgrades_or_retries(self):
        fixture = self.fixture()
        original = fixture.server._handle
        def old_peer(value, deadline):
            original(value, deadline)
            return dict(schema=2, kind='error', code='unsupported_schema', outcome_known=False), False
        fixture.server._handle = old_peer
        with self.assertRaises(FoundryError) as caught: self.submit(fixture)
        self.assertEqual('exchange_uncertain', caught.exception.code)
        self.assertEqual('unsupported_schema', self.errors[0].code)
        self.assertEqual(['submit'], [m['kind'] for m in fixture.stats()['messages']])
        self.assertEqual(0, fixture.stats()['reader_calls'])
        self.no_replay()
        fixture.close()

    def test_full_result_wrong_actor_context_digest_or_malformed_body_is_uncertain(self):
        fixture = self.fixture()
        self.submit(fixture); self.assertEqual('unverified', fixture.tick())
        original = fixture.server._handle
        changes = [lambda value: value.update(schema=2),
                   lambda value: value.update(request_sha256='0' * 64),
                   lambda value: value.update(request_id='9' * 32),
                   lambda value: value['adapter_result']['observation'].update(actor_sim_id='31'),
                   lambda value: value['adapter_result']['observation']['context'].update(client_id='6'),
                   lambda value: value['adapter_result']['observation']['queued']['rows'][0].update(index=True)]
        for change in changes:
            def altered(value, deadline):
                result, admitted = original(value, deadline)
                result = copy.deepcopy(result); change(result)
                return result, admitted
            fixture.server._handle = altered
            with self.subTest(change=change), self.assertRaises(FoundryError) as caught: self.reconcile(fixture)
            self.assertEqual('exchange_uncertain', caught.exception.code)
            self.assertIsInstance(self.errors[-1], wire.ProtocolError)
            self.assertTrue(self.no_replay().uncertainty_latched)
        fixture.server._handle = original
        self.assertEqual('adapter_returned', self.reconcile(fixture).state)
        self.assert_receipt_only(fixture, 8)
        fixture.close()


class Python37ExchangeTests(ExchangeScenarios, unittest.TestCase):
    process = True


if __name__ == '__main__':
    unittest.main()
