"""Fabricated localhost sockets only. No game, credentials, profile or provider.

This file also runs under isolated Python 3.7. Tests prove transport behavior,
not authentication or game qualification; FixturePolicy is intentionally fake.
"""
import ast
from copy import deepcopy
from pathlib import Path
import os
import subprocess
import socket
import struct
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'companion-src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion import transport, wire
from foundry_companion.queue import ProcessEpoch, QueueError, QueueBusy

SESSION = 'a' * 32
EPOCH = ProcessEpoch(1234, 133000000000000000)


def submission(number=1, operation='live.set_speed', ttl=5.0):
    args = {'observe': {}, 'live.set_speed': {'speed': 2}, 'cas.enter': {'sim_id': '9007199254740993'},
            'game.request_exit_menu': {}}[operation]
    return {'schema': 2, 'kind': 'submit', 'ttl_seconds': ttl,
            'request': {'schema': 1, 'kind': 'request', 'request_id': '{:032x}'.format(number),
                        'session_id': SESSION, 'process_epoch': wire.epoch_wire(EPOCH),
                        'operation': operation, 'arguments': args},
            'expected_context': None if operation == 'observe' else {
                'zone_id': '10', 'household_id': '20', 'save_slot': 4, 'save_guid': '99'}}


def query(value):
    parsed = wire.parse_submit(value, SESSION, EPOCH)
    return {'schema': 2, 'kind': 'receipt_read', 'request_id': parsed.request.request_id,
            'session_id': SESSION, 'process_epoch': wire.epoch_wire(EPOCH),
            'request_sha256': parsed.request_sha256}


def verified(_):
    return {'outcome': 'verified', 'fixture_only': True, 'game_qualified': False}


class FixturePolicy(transport.DenyHostPolicy):
    """Fabricated authorization ONLY for isolated loopback tests, not security."""
    def __init__(self):
        self.allow_listener = self.allow_peer = self.allow_submit = self.allow_dispatch = True
        self.submits = []
        self.dispatches = []
        self.peers = []
        self.on_dispatch = None
        self.peer_seen = threading.Event()

    def authorize_listener(self, binding):
        return self.allow_listener

    def authorize_peer(self, connection, peer, binding, deadline):
        self.peers.append((peer, threading.current_thread()))
        self.peer_seen.set()
        return self.allow_peer

    def authorize_submit(self, item, binding, deadline):
        self.submits.append((item, threading.current_thread()))
        return self.allow_submit

    def authorize_dispatch(self, item, binding):
        self.dispatches.append((item, threading.current_thread()))
        if self.on_dispatch:
            self.on_dispatch(item)
        return self.allow_dispatch


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.policy = FixturePolicy()
        self.server = self.make_server(policy=self.policy)

    def make_server(self, **kwargs):
        server = transport.LoopbackTransport(SESSION, EPOCH, **kwargs)
        self.addCleanup(server.shutdown)
        return server

    def start(self, **kwargs):
        if kwargs:
            self.server = self.make_server(policy=self.policy, **kwargs)
        self.server.start()
        return self.server

    def connect(self):
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(1)
        connection.connect(self.server.address)
        self.addCleanup(connection.close)
        return connection

    def read_reply(self, connection):
        result = bytearray()
        try:
            while True:
                chunk = connection.recv(65540 - len(result))
                if not chunk:
                    break
                result.extend(chunk)
                self.assertLessEqual(len(result), 65540)
        except ConnectionResetError:
            pass
        if not result:
            return None
        self.assertGreaterEqual(len(result), 4)
        self.assertEqual(len(result) - 4, struct.unpack('>I', result[:4])[0])
        return wire.decode_payload(bytes(result[4:]))

    def send(self, value=None, raw=None, chunks=None):
        connection = self.connect()
        payload = wire.encode_frame(value) if raw is None else raw
        if chunks:
            for start in range(0, len(payload), chunks):
                connection.sendall(payload[start:start + chunks])
        else:
            connection.sendall(payload)
        connection.shutdown(socket.SHUT_WR)
        return self.read_reply(connection)

    def assert_code(self, code, callback, *args, **kwargs):
        with self.assertRaises((QueueError, wire.ProtocolError)) as caught:
            callback(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def wait_until(self, check):
        until = time.monotonic() + 2
        while not check() and time.monotonic() < until:
            time.sleep(.002)
        self.assertTrue(check(), 'Fixture event did not settle within two seconds.')

    def test_import_and_construction_do_not_open_sockets_default_start_denied(self):
        with patch.object(transport.socket, 'socket', side_effect=AssertionError('unexpected socket')):
            denied = self.make_server()
            self.assertIsNone(denied.address)
            self.assert_code('host_policy_required', denied.start)
        self.assertEqual(0, denied.queue_status().retained)
        self.assert_code('lifecycle_closed', denied.start)

    def test_bind_only_ipv4_loopback_and_explicit_single_start(self):
        self.start()
        self.assertEqual('127.0.0.1', self.server.address[0])
        self.assertEqual(socket.AF_INET, self.server._listener.family)
        self.assert_code('lifecycle_closed', self.server.start)
        self.assertFalse(any(hasattr(self.server, key) for key in ('reset', 'rebind', 'cancel', 'restart')))
        with self.assertRaises(AttributeError):
            self.server.binding = None

    def test_configuration_bounds_and_strict_types(self):
        for changes in ({'max_connections': 0}, {'max_connections': 9}, {'max_connections': True},
                        {'connection_seconds': 0}, {'connection_seconds': float('nan')},
                        {'connection_seconds': float('inf')}, {'connection_seconds': 5.01}):
            with self.subTest(changes=changes), self.assertRaises(QueueError):
                self.make_server(**changes)
        for port in (True, -1, 65536, '0'):
            self.assert_code('invalid_port', self.server.start, port)

    def test_four_operations_preserve_exact_immutable_queue_binding(self):
        self.start()
        for number, operation in enumerate(('observe', 'live.set_speed', 'cas.enter', 'game.request_exit_menu'), 1):
            value = submission(number, operation)
            response = self.send(value)
            self.assertEqual('accepted', response['state'])
            calls = []
            self.server.owner_tick(lambda req: calls.append(req) or verified(req))
            req = calls[0]
            self.assertEqual(EPOCH, req.process_epoch)
            self.assertEqual(SESSION, req.session_id)
            self.assertEqual(value['request']['request_id'], req.request_id)
            self.assertEqual(value['ttl_seconds'], req.ttl_seconds)
            if operation != 'observe':
                self.assertEqual(('10', '20', 4, '99'), (req.expected_zone_id, req.expected_household_id,
                                                       req.expected_save_slot, req.expected_save_guid))
            response = self.send(query(value))
            self.assertEqual('adapter_returned', response['state'])
            self.assertEqual('verified', response['adapter_result']['outcome'])
            self.assertIs(response['independent_evidence'], False)
            self.assertNotIn('evidence', response)

    def test_workers_never_dispatch_native_and_tick_is_owner_only(self):
        self.start()
        self.send(submission())
        self.assertEqual([], self.policy.dispatches)
        self.assertIsNot(self.policy.submits[0][1], threading.current_thread())
        failures = []
        def wrong_thread():
            try:
                self.server.owner_tick(verified)
            except QueueError as error:
                failures.append(error.code)
        worker = threading.Thread(target=wrong_thread)
        worker.start(); worker.join(1)
        self.assertEqual(['wrong_thread'], failures)
        self.server.owner_tick(verified)
        self.assertIs(self.policy.dispatches[0][1], threading.current_thread())

    def test_owner_tick_never_needs_network_locks_or_holds_queue_lock_in_callback(self):
        self.start(); self.send(submission())
        def execute(req):
            self.assertTrue(self.server._queue._lock.acquire(False))
            self.server._queue._lock.release()
            return verified(req)
        with self.server._operation_lock, self.server._network_lock:
            self.assertEqual('verified', self.server.owner_tick(execute).state)

    def test_owner_queue_contention_is_failfast(self):
        self.start(); self.send(submission())
        with self.server._queue._lock:
            start = time.monotonic()
            with self.assertRaises(QueueBusy):
                self.server.owner_tick(verified)
            self.assertLess(time.monotonic() - start, .1)

    def test_peer_and_new_submission_default_denial_never_dispatch(self):
        self.start()
        self.policy.allow_peer = False
        self.assertIsNone(self.send(submission()))
        self.assertEqual(0, self.server.queue_status().retained)
        self.policy.allow_peer = True; self.policy.allow_submit = False
        response = self.send(submission())
        self.assertEqual(('refused', 'host_admission_denied'), (response['state'], response['reason']))
        self.policy.allow_submit = True
        self.assertEqual(response, self.send(submission()))
        self.assertIsNone(self.server.owner_tick(verified))
        self.assertEqual(1, len(self.policy.submits))

    def test_owner_preflight_refusal_is_never_visible_as_dispatched(self):
        self.start(); self.send(submission())
        receipts = []
        def inspect(_):
            worker = threading.Thread(target=lambda: receipts.append(self.send(query(submission()))))
            worker.start(); worker.join(1)
            self.assertFalse(worker.is_alive())
        self.policy.on_dispatch = inspect
        self.policy.allow_dispatch = False
        native = Mock(side_effect=verified)
        final = self.server.owner_tick(native)
        self.assertEqual('accepted', receipts[0]['state'])
        self.assertEqual(('refused', None), (final.state, final.started_at))
        self.assertEqual('refused', self.send(query(submission()))['state'])
        native.assert_not_called()

    def test_receipt_during_executor_sees_dispatched_exception_stays_unknown(self):
        self.start(); self.send(submission())
        observed = []
        def execute(_):
            worker = threading.Thread(target=lambda: observed.append(self.send(query(submission()))))
            worker.start(); worker.join(1)
            self.assertFalse(worker.is_alive())
            raise QueueError('host_dispatch_denied', 'Even this exception is unknown after start.')
        self.assertEqual('unknown', self.server.owner_tick(execute).state)
        self.assertEqual('dispatched', observed[0]['state'])
        self.assertEqual('unknown', self.send(query(submission()))['state'])

    def test_byte_fragmentation_and_utf8_result(self):
        self.start()
        self.assertEqual('accepted', self.send(submission(), chunks=1)['state'])
        self.server.owner_tick(lambda _: {'outcome': 'verified', 'text': '\u96ea\U0001f600'})
        self.assertEqual('\u96ea\U0001f600', self.send(query(submission()))['adapter_result']['text'])

    def test_coalesced_or_trailing_partial_frames_reject_entire_connection(self):
        self.start()
        frame = wire.encode_frame(submission())
        for suffix in (frame, b'\x00', b'junk'):
            reply = self.send(raw=frame + suffix)
            self.assertEqual('extra_frame', reply['code'])
            self.assertEqual(0, self.server.queue_status().retained)

    def test_complete_frame_without_write_eof_is_never_admitted(self):
        self.start(connection_seconds=.07)
        connection = self.connect()
        connection.sendall(wire.encode_frame(submission()))
        self.assertIsNone(self.read_reply(connection))
        self.assertEqual(0, self.server.queue_status().retained)

    def test_truncated_and_oversized_frames_have_no_admission(self):
        self.start()
        frame = wire.encode_frame(submission())
        for raw in (b'', b'\x00', frame[:12], struct.pack('>I', 0), struct.pack('>I', 65537)):
            reply = self.send(raw=raw)
            self.assertTrue(reply is None or reply['kind'] == 'error')
            self.assertEqual(0, self.server.queue_status().retained)

    def test_strict_json_rejects_malformed_duplicate_deep_nonfinite_and_surrogate(self):
        self.start()
        for payload in (b'{', b'[]', b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e9999}',
                        b'{"x":' + b'[' * 40 + b'0' + b']' * 40 + b'}',
                        b'{"x":18446744073709551616}', b'{"x":"\\ud800"}', b'{"x":"\xff"}'):
            reply = self.send(raw=struct.pack('>I', len(payload)) + payload)
            self.assertEqual('invalid_json', reply['code'])
        self.assertEqual(0, self.server.queue_status().retained)

    def test_slow_drip_cannot_extend_absolute_deadline(self):
        self.start(connection_seconds=.09)
        connection = self.connect()
        start = time.monotonic()
        for part in (b'\x00', b'\x00', b'\x01', b'\x00'):
            try:
                connection.sendall(part)
            except OSError:
                break
            time.sleep(.035)
        self.assertIsNone(self.read_reply(connection))
        self.assertLess(time.monotonic() - start, .4)
        self.assertEqual(0, self.server.queue_status().retained)

    def test_wrong_session_epoch_and_missing_or_wrong_context_refused(self):
        self.start()
        variants = []
        for key, item in (('session_id', 'b' * 32), ('process_epoch', {'pid': EPOCH.pid, 'creation_filetime': EPOCH.creation_filetime + 1})):
            value = submission(); value['request'][key] = item; variants.append(value)
        for key in ('zone_id', 'household_id', 'save_slot', 'save_guid'):
            value = submission(); value['expected_context'][key] = None; variants.append(value)
        value = submission(); value['expected_context']['save_guid'] = '099'; variants.append(value)
        for value in variants:
            self.assertEqual('error', self.send(value)['kind'])
        self.assertEqual(0, self.server.queue_status().retained)
        self.assertEqual([], self.policy.submits)

    def test_v1_alone_and_unversioned_or_extra_fields_fail_closed(self):
        self.start()
        values = [submission()['request'], dict(submission(), schema=1), dict(submission(), auth=True)]
        for value in values:
            self.assertEqual('invalid_envelope', self.send(value)['code'])
        self.assertEqual(0, self.server.queue_status().retained)

    def test_duplicate_submission_and_receipts_never_admit_requeue_or_renew_ttl(self):
        self.start(); value = submission(ttl=.5)
        first = self.send(value)
        old = self.server._queue.status(value['request']['request_id'])
        for _ in range(4):
            self.assertEqual(first, self.send(value))
            self.assertEqual(first, self.send(query(value)))
            self.assertIs(old, self.server._queue.status(old.request_id))
        self.assertEqual(1, len(self.policy.submits))
        calls = Mock(side_effect=verified)
        self.server.owner_tick(calls)
        for _ in range(3):
            self.assertEqual('adapter_returned', self.send(query(value))['state'])
            self.assertEqual('adapter_returned', self.send(value)['state'])
            self.assertIsNone(self.server.owner_tick(calls))
        calls.assert_called_once()

    def test_ttl_expiry_is_not_refreshed_by_read(self):
        self.start(); value = submission(ttl=.02)
        self.send(value); time.sleep(.03)
        self.assertEqual('accepted', self.send(query(value))['state'])
        native = Mock()
        self.assertEqual('expired', self.server.owner_tick(native).reason)
        native.assert_not_called()
        self.assertEqual('refused', self.send(query(value))['state'])

    def test_unknown_receipt_never_creates_request_and_latches_missing_history(self):
        self.start()
        response = self.send(query(submission()))
        self.assertEqual(('not_found', 'history_missing'), (response['state'], response['reason']))
        self.assertEqual(0, self.server.queue_status().retained)
        self.assertEqual({}, self.server._records)
        self.assertIsNone(self.server.owner_tick(verified))
        self.assertEqual('history_missing', self.send(submission(2))['reason'])
        self.assertEqual([], self.policy.submits)

    def test_conflicting_submit_or_receipt_latches_before_pending_dispatch(self):
        for receipt_only in (False, True):
            self.server = self.make_server(policy=self.policy)
            self.server.start(); self.send(submission())
            value = query(submission()) if receipt_only else submission()
            if receipt_only:
                value['request_sha256'] = 'f' * 64
            else:
                value['expected_context']['save_guid'] = '100'
            self.assertEqual('request_conflict', self.send(value)['code'])
            native = Mock()
            self.assertEqual('request_conflict', self.server.owner_tick(native).reason)
            native.assert_not_called()
            self.assertEqual('request_conflict', self.send(submission(2))['reason'])

    def test_unknown_and_unverified_block_queued_and_new_mutations_but_allow_observation(self):
        for callback in (Mock(side_effect=RuntimeError('fixture')), lambda _: {'outcome': 'unverified'}):
            self.server = self.make_server(policy=self.policy)
            self.server.start(); self.send(submission()); self.send(submission(2))
            self.server.owner_tick(callback)
            self.assertEqual('unresolved_mutation', self.send(submission(3))['reason'])
            self.assertEqual('unresolved_mutation', self.server.owner_tick(verified).reason)
            self.assertEqual('accepted', self.send(submission(4, 'observe'))['state'])
            self.server.owner_tick(verified)
            self.assertEqual('unresolved_mutation', self.send(submission(5))['reason'])

    def test_bounded_non_evicting_ledger_and_queue_capacity(self):
        self.start(pending_capacity=1, history_capacity=3)
        self.assertEqual('accepted', self.send(submission(1))['state'])
        self.assertEqual('pending_full', self.send(submission(2))['reason'])
        self.server.owner_tick(verified)
        self.assertEqual('pending_full', self.send(submission(2))['reason'])
        self.assertEqual('accepted', self.send(submission(3))['state'])
        self.server.owner_tick(verified)
        for number in range(4, 20):
            self.assertEqual('history_full', self.send(submission(number))['reason'])
        self.assertEqual(3, len(self.server._records))
        self.assertEqual(3, self.server.queue_status().retained)
        self.assertEqual('adapter_returned', self.send(query(submission(1)))['state'])

    def test_connection_count_is_bounded_and_extra_connections_not_admitted(self):
        self.start(max_connections=1)
        first = self.connect()
        self.assertTrue(self.policy.peer_seen.wait(1))
        second = self.connect()
        second.sendall(wire.encode_frame(submission()))
        try:
            second.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        self.assertIsNone(self.read_reply(second))
        self.assertEqual(1, len(self.policy.peers))
        self.assertEqual(0, self.server.queue_status().retained)
        first.close()

    def test_lost_response_after_admission_retains_result_and_blocks_later_mutation(self):
        self.start(connection_seconds=.08)
        sending, release = threading.Event(), threading.Event()
        original = transport._send_one
        def delayed(connection, value, deadline):
            sending.set(); release.wait(1)
            return original(connection, value, deadline)
        with patch.object(transport, '_send_one', side_effect=delayed):
            connection = self.connect()
            connection.sendall(wire.encode_frame(submission()))
            connection.shutdown(socket.SHUT_WR)
            self.assertTrue(sending.wait(1))
            self.assertEqual('verified', self.server.owner_tick(verified).state)
            connection.settimeout(.1)
            with self.assertRaises(socket.timeout):
                connection.recv(4)
            release.set()
            self.wait_until(lambda: not self.server._workers)
        response = self.send(query(submission()))
        self.assertEqual('adapter_returned', response['state'])
        self.assertEqual('response_lost', self.send(submission(2))['reason'])
        self.assertIsNone(self.server.owner_tick(verified))

    def test_response_encoding_failure_on_receipt_preserves_uncertainty_latch(self):
        self.start(); self.send(submission())
        # Valid bounded queue text can exceed the wire byte limit once escaped.
        self.server.owner_tick(lambda _: {'outcome': 'verified', 'text': '\x00' * 16000})
        response = self.send(query(submission()))
        self.assertEqual('oversized_frame', response['code'])
        self.assertEqual('response_lost', self.send(submission(2))['reason'])
        self.assertEqual('verified', self.server._queue.status(submission()['request']['request_id']).state)

    def test_shutdown_closes_partial_peers_and_pending_never_executes_or_restarts(self):
        self.start(); self.send(submission())
        connection = self.connect(); connection.sendall(b'\x00')
        self.assertTrue(self.server.shutdown())
        native = Mock()
        final = self.server.owner_tick(native)
        self.assertEqual('refused', final.state)
        native.assert_not_called()
        self.assertIsNotNone(self.server._queue.status(submission()['request']['request_id']))
        self.assert_code('lifecycle_closed', self.server.start)
        self.assertTrue(self.server.shutdown())

    def test_shutdown_during_started_call_keeps_unknown_and_does_not_retry(self):
        self.start(); self.send(submission())
        def execute(req):
            worker = threading.Thread(target=self.server.shutdown)
            worker.start(); worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual('started', self.server._queue.status(req.request_id).state)
            raise RuntimeError('fixture interrupted operation')
        self.assertEqual('unknown', self.server.owner_tick(execute).state)
        self.assertIsNone(self.server.owner_tick(verified))

    def test_accept_thread_start_failure_closes_socket_and_preserves_startup_uncertainty(self):
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('fixture thread failure')):
            with self.assertRaises(RuntimeError):
                self.server.start()
        self.assertFalse(self.server.shutdown())
        self.assertIsNone(self.server._accept_thread.ident)
        self.assertTrue(self.server._startup_uncertain.is_set())
        self.assertEqual(-1, self.server._listener.fileno())

    def test_peer_thread_start_failure_cleans_socket_and_stops_ingress(self):
        self.start()
        original = threading.Thread.start
        def start(thread):
            if thread.name == 'foundry-loopback-peer':
                raise RuntimeError('fixture worker failure')
            return original(thread)
        with patch.object(threading.Thread, 'start', start):
            connection = self.connect()
            self.wait_until(self.server._stop.is_set)
            self.assertIsNone(self.read_reply(connection))
        self.assertEqual(1, len(self.server._workers))
        self.assertFalse(self.server.shutdown())
        self.assertTrue(self.server._startup_uncertain.is_set())

    def test_fatal_accept_error_latches_pending_dispatch_off(self):
        # Feed a fabricated accepted intent, then force the listener failure.
        self.server._handle(submission(), time.monotonic() + 1)
        listener = Mock()
        listener.accept.side_effect = OSError("fixture lost listener")
        self.server._listener = listener
        self.server._accept()
        self.assertTrue(self.server._stop.is_set())
        listener.close.assert_called_once()
        native = Mock()
        self.assertEqual("refused", self.server.owner_tick(native).state)
        native.assert_not_called()
        self.assertEqual(1, len(self.server._records))

    def test_peer_thread_constructor_failure_closes_socket_and_latches_stop(self):
        self.start()
        original = threading.Thread
        def construct(*args, **kwargs):
            if kwargs.get('name') == 'foundry-loopback-peer':
                raise RuntimeError('fixture construction failure')
            return original(*args, **kwargs)
        with patch.object(threading, 'Thread', side_effect=construct):
            connection = self.connect()
            self.wait_until(self.server._stop.is_set)
            self.assertIsNone(self.read_reply(connection))
        self.assertEqual({}, self.server._workers)
        self.assertTrue(self.server.shutdown())

    def test_peer_daemon_assignment_failure_closes_socket_and_latches_stop(self):
        self.start()
        original = threading.Thread
        class BrokenThread:
            ident = None
            @property
            def daemon(self):
                return False
            @daemon.setter
            def daemon(self, value):
                raise RuntimeError('fixture daemon failure')
        def construct(*args, **kwargs):
            if kwargs.get('name') == 'foundry-loopback-peer':
                return BrokenThread()
            return original(*args, **kwargs)
        with patch.object(threading, 'Thread', side_effect=construct):
            connection = self.connect()
            self.wait_until(self.server._stop.is_set)
            self.assertIsNone(self.read_reply(connection))
        self.assertEqual({}, self.server._workers)
        self.assertTrue(self.server.shutdown())

    def test_shutdown_never_waits_for_queue_lock_even_on_control_worker(self):
        self.start(); self.send(submission())
        outcomes = []
        with self.server._queue._lock:
            worker = threading.Thread(target=lambda: outcomes.append(self.server.shutdown()))
            worker.start(); worker.join(.5)
            self.assertFalse(worker.is_alive())
        self.assertEqual([True], outcomes)
        native = Mock()
        self.assertEqual('refused', self.server.owner_tick(native).state)
        native.assert_not_called()

    def test_socket_close_error_still_removes_worker_and_reports_unclean_shutdown(self):
        self.policy.allow_peer = False
        connection = Mock()
        connection.close.side_effect = OSError('fixture close failure')
        self.server._workers[connection] = threading.current_thread()
        self.server._serve(connection, ('127.0.0.1', 1), time.monotonic() + 1)
        self.assertEqual({}, self.server._workers)
        self.assertTrue(self.server._stop.is_set())
        self.assertTrue(self.server._cleanup_failed.is_set())
        self.assertFalse(self.server.shutdown())

    def test_windows_requires_exclusive_bind_before_listener_bind(self):
        listener = Mock()
        with patch.object(transport.sys, 'platform', 'win32'), \
             patch.object(socket, 'SO_EXCLUSIVEADDRUSE', 999, create=True), \
             patch.object(socket, 'socket', return_value=listener), \
             patch.object(threading.Thread, 'start', side_effect=RuntimeError('fixture stop before worker')):
            with self.assertRaises(RuntimeError):
                self.server.start()
        self.assertEqual('setsockopt', listener.mock_calls[0][0])
        listener.setsockopt.assert_called_once_with(socket.SOL_SOCKET, 999, 1)
        listener.bind.assert_called_once_with(('127.0.0.1', 0))

    def test_windows_exclusive_bind_absence_or_failure_never_binds_and_closes(self):
        for absent in (True, False):
            self.server = self.make_server(policy=self.policy)
            listener = Mock()
            if not absent:
                listener.setsockopt.side_effect = OSError('fixture unsupported exclusive bind')
            with patch.object(transport.sys, 'platform', 'win32'), \
                 patch.object(socket, 'SO_EXCLUSIVEADDRUSE', None if absent else 999, create=True), \
                 patch.object(socket, 'socket', return_value=listener):
                with self.assertRaises((QueueError, OSError)):
                    self.server.start()
            listener.bind.assert_not_called()
            listener.close.assert_called_once()
            self.assertTrue(self.server._stop.is_set())

    @unittest.skipUnless(sys.platform == 'win32', 'Real exclusive-bind behavior is Windows-specific.')
    def test_windows_exclusive_listener_rejects_second_reuseaddr_socket(self):
        self.start()
        competitor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(competitor.close)
        competitor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with self.assertRaises(OSError):
            competitor.bind(self.server.address)
        self.assertEqual('accepted', self.send(submission())['state'])

    def test_start_failure_after_os_thread_started_is_not_reported_clean(self):
        original = threading.Thread.start
        def start_then_fail(thread):
            original(thread)
            raise RuntimeError('fixture exception after real thread start')
        with patch.object(threading.Thread, 'start', start_then_fail):
            with self.assertRaises(RuntimeError):
                self.server.start()
        self.assertFalse(self.server.shutdown())
        self.assertFalse(self.server._accept_thread.is_alive())
        self.assertTrue(self.server._startup_uncertain.is_set())

    def test_companion_files_parse_as_python37(self):
        for source in (SOURCE / 'foundry_companion').glob('*.py'):
            if sys.version_info[:2] == (3, 7):
                ast.parse(source.read_text(), filename=str(source))
            else:
                ast.parse(source.read_text(), filename=str(source), feature_version=(3, 7))


@unittest.skipIf(sys.version_info < (3, 11), 'Client targets Python 3.11+, server tested separately on 3.7.')
class CrossClientTests(TransportTests):
    # Do not inherit all server tests a second time.
    def test_cross_client_v2_roundtrip_preserves_v1_and_exact_fingerprint(self):
        from sims4foundry_cli import companion_protocol as v1
        from sims4foundry_cli import companion_transport as client
        self.start()
        original = submission()
        request = v1.parse_request(original['request'], session_id=SESSION,
                                   process_epoch=v1.ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime))
        item = client.Submission(request, expected_context=client.ExpectedContext(**original['expected_context']))
        self.assertEqual(original, item.to_wire())
        parsed = wire.parse_submit(item.to_wire(), SESSION, EPOCH)
        self.assertEqual(item.request_sha256, parsed.request_sha256)
        fake_server_auth = lambda *_: True  # synthetic fixture, NOT authentication
        reply = client.exchange_once(self.server.address[1], item, authorize_peer=fake_server_auth)
        self.assertEqual('accepted', reply['state'])
        self.server.owner_tick(verified)
        reply = client.exchange_once(self.server.address[1], client.ReceiptRead(item), authorize_peer=fake_server_auth)
        self.assertEqual('adapter_returned', reply['state'])
        self.assertIs(reply['independent_evidence'], False)
        with self.assertRaises(v1.ProtocolError):
            v1.parse_request(item.to_wire(), session_id=SESSION, process_epoch=request.process_epoch)
        with self.assertRaises(v1.ProtocolError):
            v1.parse_response(reply, request=request)
        self.assertEqual(original['request'], request.to_wire())

    def test_client_default_denied_and_spoofed_verified_receipt_rejected(self):
        from sims4foundry_cli import companion_protocol as v1
        from sims4foundry_cli import companion_transport as client
        original = submission()
        request = v1.parse_request(original['request'], session_id=SESSION,
                                   process_epoch=v1.ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime))
        item = client.Submission(request, expected_context=client.ExpectedContext(**original['expected_context']))
        with patch.object(socket, 'socket', side_effect=AssertionError('client must deny before connect')):
            with self.assertRaises(v1.ProtocolError) as caught:
                client.exchange_once(1234, item)
            self.assertEqual('host_policy_required', caught.exception.code)
        self.start()
        reply = self.send(item.to_wire())
        for key, value in (('state', 'verified'), ('independent_evidence', True), ('request_sha256', 'f' * 64)):
            changed = dict(reply); changed[key] = value
            with self.assertRaises(v1.ProtocolError):
                client.parse_receipt(changed, item)

    def test_client_codec_matches_companion_for_all_operations_and_rejects_context_drift(self):
        from sims4foundry_cli import companion_protocol as v1
        from sims4foundry_cli import companion_transport as client
        for operation in ('observe', 'live.set_speed', 'cas.enter', 'game.request_exit_menu'):
            value = submission(operation=operation)
            request = v1.parse_request(value['request'], session_id=SESSION,
                                       process_epoch=v1.ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime))
            context = client.ExpectedContext(**value['expected_context']) if value['expected_context'] else None
            item = client.Submission(request, expected_context=context)
            self.assertEqual(wire.encode_frame(item.to_wire()), v1.encode_frame(item.to_wire()))
            self.assertEqual(item.request_sha256, wire.parse_submit(item.to_wire(), SESSION, EPOCH).request_sha256)
        for changes in ({'zone_id': '010'}, {'household_id': 20}, {'save_slot': True},
                        {'save_guid': '099'}, {'save_guid': str(2**64)}):
            context = dict(submission()['expected_context']); context.update(changes)
            with self.assertRaises(v1.ProtocolError):
                client.ExpectedContext(**context)

    def test_actual_python37_server_with_current_python_client(self):
        from sims4foundry_cli import companion_protocol as v1
        from sims4foundry_cli import companion_transport as client
        interpreter = os.environ.get('FOUNDRY_COMPANION_PYTHON37')
        if not interpreter:
            self.skipTest('Set FOUNDRY_COMPANION_PYTHON37 for a real cross-runtime socket test.')
        script = r"""
import sys
sys.path.insert(0, sys.argv[1])
assert sys.version_info[:2] == (3, 7)
from foundry_companion.transport import LoopbackTransport, DenyHostPolicy
from foundry_companion.queue import ProcessEpoch
class FabricatedPolicy(DenyHostPolicy):
    def authorize_listener(self, binding): return True
    def authorize_peer(self, connection, peer, binding, deadline): return True
    def authorize_submit(self, submission, binding, deadline): return True
    def authorize_dispatch(self, submission, binding): return True
server = LoopbackTransport('a' * 32, ProcessEpoch(1234, 133000000000000000), policy=FabricatedPolicy())
try:
    print(server.start()[1], flush=True)
    for command in sys.stdin:
        if command.strip() == 'tick':
            print(server.owner_tick(lambda _: {'outcome': 'verified', 'fixture_only': True}).state, flush=True)
        elif command.strip() == 'stop':
            break
finally:
    server.shutdown()
"""
        process = subprocess.Popen([interpreter, '-B', '-S', '-s', '-c', script, str(SOURCE)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   universal_newlines=True)
        def cleanup():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            process.stdin.close(); process.stdout.close(); process.stderr.close()
        self.addCleanup(cleanup)
        port = int(process.stdout.readline())
        value = submission()
        request = v1.parse_request(value['request'], session_id=SESSION,
                                   process_epoch=v1.ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime))
        item = client.Submission(request, expected_context=client.ExpectedContext(**value['expected_context']))
        synthetic_auth = lambda *_: True
        self.assertEqual('accepted', client.exchange_once(port, item, authorize_peer=synthetic_auth)['state'])
        process.stdin.write('tick\n'); process.stdin.flush()
        self.assertEqual('verified', process.stdout.readline().strip())
        result = client.exchange_once(port, client.ReceiptRead(item), authorize_peer=synthetic_auth)
        self.assertEqual('adapter_returned', result['state'])
        self.assertIs(result['independent_evidence'], False)
        process.stdin.write('stop\n'); process.stdin.flush()
        self.assertEqual(0, process.wait(timeout=3), process.stderr.read())


# Keep shared helpers, but run server cases once instead of through inheritance.
for _name in list(TransportTests.__dict__):
    if _name.startswith('test_'):
        setattr(CrossClientTests, _name, None)

if __name__ == '__main__':
    unittest.main()
