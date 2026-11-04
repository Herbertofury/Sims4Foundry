"""Fabricated ephemeral loopback sockets and PUBLIC TEST-ONLY keys, never a game.

Portable server/helper cases run with isolated Python 3.7. Current-client cases
and a separate-process 3.7 integration run under the CLI interpreter.
"""
import ast
import errno
from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path
import pickle
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'companion-src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion import peer_auth as auth, transport, wire
from foundry_companion.queue import ProcessEpoch

# Public, deterministic fixtures only. Never use these as a real session key.
KEY = bytes(range(32))
WRONG_KEY = b'public-wrong-test-key' + b'x' * 12
SESSION = 'a' * 32
EPOCH = ProcessEpoch(1234, 133000000000000000)
CLIENT_NONCE = bytes(range(32, 64)).hex()
SERVER_NONCE = bytes(range(64, 96)).hex()
CONTEXT = {'session_id': SESSION, 'process_epoch': {'pid': EPOCH.pid, 'creation_filetime': EPOCH.creation_filetime},
           'connection': {'client_port': 49152, 'server_port': 54321}}


def closed_socket_error(error):
    # Negative fixtures may be closed before send/shutdown/recv completes on
    # either Windows or POSIX. Never suppress unrelated socket failures.
    return (getattr(error, 'errno', None) in
            (errno.ECONNRESET, errno.ECONNABORTED, errno.ENOTCONN, errno.EPIPE)
            or getattr(error, 'winerror', None) in (10053, 10054, 10057, 10058))


def send_rejected(connection, payload):
    try:
        connection.sendall(payload)
        connection.shutdown(socket.SHUT_WR)
    except OSError as error:
        if not closed_socket_error(error):
            raise


def read_closed(connection):
    try:
        return connection.recv(1)
    except OSError as error:
        if not closed_socket_error(error):
            raise
        return b''


def submission(number=1):
    return {'schema': 2, 'kind': 'submit', 'ttl_seconds': 5.0, 'expected_context': None,
            'request': {'schema': 1, 'kind': 'request', 'request_id': '{:032x}'.format(number),
                        'session_id': SESSION, 'process_epoch': wire.epoch_wire(EPOCH),
                        'operation': 'observe', 'arguments': {}}}


class FabricatedPolicy(transport.DenyHostPolicy):
    def __init__(self):
        self.auth = auth.SessionKeyPeer(KEY)
        self.submits = []

    def authorize_listener(self, binding):
        return True  # Isolated fabricated sockets only, never a deployable policy.

    def authorize_peer(self, connection, peer, binding, deadline):
        return self.auth.authorize_peer(connection, peer, binding, deadline)

    def authorize_submit(self, item, binding, deadline):
        self.submits.append(item)
        return True

    # authorize_dispatch intentionally remains denied.


class BytesSocket:
    """Deterministic fragmented-frame fixture; never used for peer identity."""
    def __init__(self, data, width=1):
        self.data, self.width, self.timeouts = data, width, []

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, size):
        count = min(size, self.width)
        result, self.data = self.data[:count], self.data[count:]
        return result


# GOLDEN_TRANSCRIPT is pinned below as full canonical length-prefixed frames.
GOLDEN_TRANSCRIPT = (
    b'\x00\x00\x01${"auth_schema":1,"client_nonce":"202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f","connection":{"client_port":49152,"server_port":54321},"kind":"client_hello","process_epoch":{"creation_filetime":133000000000000000,"pid":1234},"session_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
    b'\x00\x00\x01\xc1{"auth_schema":1,"client_nonce":"202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f","connection":{"client_port":49152,"server_port":54321},"kind":"server_proof","process_epoch":{"creation_filetime":133000000000000000,"pid":1234},"proof":"5034788be00d932cc07ceb54798039c211d79487640f49c85c5266be083d21bb","server_nonce":"404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f","session_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
    b'\x00\x00\x01\xc1{"auth_schema":1,"client_nonce":"202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f","connection":{"client_port":49152,"server_port":54321},"kind":"client_proof","process_epoch":{"creation_filetime":133000000000000000,"pid":1234},"proof":"3cceaebf5657f57e120007c7ee59faee0f498432a9b301305217632dd8261480","server_nonce":"404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f","session_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
    b'\x00\x00\x01\xc1{"auth_schema":1,"client_nonce":"202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f","connection":{"client_port":49152,"server_port":54321},"kind":"server_ready","process_epoch":{"creation_filetime":133000000000000000,"pid":1234},"proof":"a42f2a3e0ca7a95bb718e9479a323121818591f1cce7fa665dd882c87e9b65b0","server_nonce":"404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f","session_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
)


class AuthCodecTests(unittest.TestCase):
    def test_keys_strictly_bounded_and_no_implicit_provisioning(self):
        for key in (None, '', 'x' * 32, bytearray(32), memoryview(bytes(32)), b'', bytes(31), bytes(65), True):
            with self.subTest(type=type(key).__name__), self.assertRaises(auth.PeerAuthError):
                auth.SessionKeyPeer(key)
        for length in (32, 48, 64):
            self.assertIsInstance(auth.SessionKeyPeer(bytes(length)), auth.SessionKeyPeer)
        with patch.object(socket, 'socket', side_effect=AssertionError('socket')), \
             patch.object(auth.os, 'urandom', side_effect=AssertionError('key generation')):
            auth.SessionKeyPeer(KEY)

    def test_key_repr_serialization_and_error_redaction(self):
        peer = auth.SessionKeyPeer(KEY)
        self.assertEqual('SessionKeyPeer(<redacted>)', repr(peer))
        self.assertNotIn(KEY.hex(), repr(peer))
        with self.assertRaises(TypeError):
            pickle.dumps(peer)
        sentinel = b'not-a-real-key-sensitive-marker' * 4
        try:
            auth.SessionKeyPeer(sentinel)
        except Exception:
            rendered = traceback.format_exc()
        self.assertNotIn(sentinel.decode(), rendered)
        self.assertFalse(hasattr(peer, '__dict__'))

    def test_mirrored_core_and_game_python37_syntax(self):
        game = (SOURCE / 'foundry_companion/peer_auth.py').read_text()
        cli = (ROOT / 'src/sims4foundry_cli/companion_auth.py').read_text()
        def core(text):
            return text[text.index('import hashlib'):text.index('    def authorize_peer(') if '    def authorize_peer(' in text else text.index('    def __call__(')]
        self.assertEqual(core(game), core(cli))
        if sys.version_info >= (3, 8):
            ast.parse(game, feature_version=(3, 7))
        else:
            ast.parse(game)

    def test_pinned_full_golden_transcript(self):
        values = [auth._envelope('client_hello', CONTEXT, CLIENT_NONCE)]
        values += [auth._signed(KEY, kind, CONTEXT, CLIENT_NONCE, SERVER_NONCE)
                   for kind in ('server_proof', 'client_proof', 'server_ready')]
        self.assertEqual(4, len(GOLDEN_TRANSCRIPT))
        self.assertEqual(GOLDEN_TRANSCRIPT, tuple(wire.encode_frame(value) for value in values))
        for frame, value in zip(GOLDEN_TRANSCRIPT, values):
            self.assertEqual(len(frame) - 4, struct.unpack('>I', frame[:4])[0])
            self.assertEqual(value, auth._read(BytesSocket(frame), time.monotonic() + 1))
        self.assertEqual(3, len({value['proof'] for value in values[1:]}))

    def test_hmac_matches_independently_serialized_transcript(self):
        for kind in ('server_proof', 'client_proof', 'server_ready'):
            unsigned = dict(CONTEXT, auth_schema=1, kind=kind,
                            client_nonce=CLIENT_NONCE, server_nonce=SERVER_NONCE)
            raw = json.dumps(unsigned, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf8')
            expected = hmac.new(KEY, b'Foundry companion peer authentication\x00v1\x00' + raw, hashlib.sha256).hexdigest()
            actual = auth._signed(KEY, kind, CONTEXT, CLIENT_NONCE, SERVER_NONCE)
            self.assertEqual(expected, actual['proof'])
            with patch.object(auth.hmac, 'compare_digest', wraps=hmac.compare_digest) as compare:
                auth._verify(KEY, actual)
                self.assertEqual(1, compare.call_count)

    def test_schema_nonce_binding_and_type_substitutions_rejected(self):
        original = auth._signed(KEY, 'server_proof', CONTEXT, CLIENT_NONCE, SERVER_NONCE)
        mutations = [('auth_schema', True), ('auth_schema', 1.0), ('auth_schema', 2),
                     ('kind', 'client_proof'), ('kind', ['server_proof']),
                     ('session_id', 'b' * 32), ('client_nonce', '0' * 64),
                     ('client_nonce', CLIENT_NONCE.upper()), ('server_nonce', CLIENT_NONCE),
                     ('server_nonce', 'g' * 64), ('proof', '0' * 63), ('proof', 'G' * 64),
                     ('process_epoch', {'pid': True, 'creation_filetime': EPOCH.creation_filetime}),
                     ('process_epoch', {'pid': EPOCH.pid, 'creation_filetime': EPOCH.creation_filetime + 1}),
                     ('connection', {'client_port': 49152.0, 'server_port': 54321}),
                     ('connection', {'client_port': 49153, 'server_port': 54321})]
        for field, replacement in mutations:
            changed = deepcopy(original); changed[field] = replacement
            with self.subTest(field=field, replacement=replacement), self.assertRaises(auth.PeerAuthError):
                auth._validate(changed, 'server_proof', CONTEXT, CLIENT_NONCE, SERVER_NONCE)
        for changed in (dict(original, extra=True), {k: v for k, v in original.items() if k != 'proof'}):
            with self.assertRaises(auth.PeerAuthError):
                auth._validate(changed, 'server_proof', CONTEXT, CLIENT_NONCE, SERVER_NONCE)

    def test_domain_roles_wrong_key_and_every_binding_field_change_mac(self):
        original = auth._signed(KEY, 'server_proof', CONTEXT, CLIENT_NONCE, SERVER_NONCE)
        changed_values = []
        for field, value in (('kind', 'client_proof'), ('auth_schema', 2), ('session_id', 'b' * 32),
                             ('client_nonce', '1' * 64), ('server_nonce', '2' * 64)):
            changed = deepcopy(original); changed[field] = value; changed_values.append(changed)
        for nested, field in (('process_epoch', 'pid'), ('process_epoch', 'creation_filetime'),
                              ('connection', 'client_port'), ('connection', 'server_port')):
            changed = deepcopy(original); changed[nested][field] += 1; changed_values.append(changed)
        for changed in changed_values:
            with self.assertRaises(auth.PeerAuthError):
                auth._verify(KEY, changed)
        with self.assertRaises(auth.PeerAuthError):
            auth._verify(WRONG_KEY, original)
        with patch.object(auth, '_DOMAIN', b'other protocol\x00'):
            with self.assertRaises(auth.PeerAuthError):
                auth._verify(KEY, original)

    def test_frame_bounds_malformed_json_depth_duplicates_truncation(self):
        for size in (0, auth.MAX_AUTH_BYTES + 1, 2**32 - 1):
            with self.assertRaises(auth.PeerAuthError):
                auth._read(BytesSocket(struct.pack('>I', size)), time.monotonic() + 1)
        bad = (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":18446744073709551616}',
               b'{"a":"\\ud800"}', b'{"a":{"b":{}}}', b'{"a":[[]]}', b'{}{}', b'\xff', b'[]')
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(wire.ProtocolError):
                auth._read(BytesSocket(struct.pack('>I', len(payload)) + payload), time.monotonic() + 1)
        frame = GOLDEN_TRANSCRIPT[0]
        for cut in range(len(frame)):
            with self.assertRaises(auth.PeerAuthError):
                auth._read(BytesSocket(frame[:cut]), time.monotonic() + 1)

    def test_deadline_invalid_expired_or_far_future_never_reads(self):
        for deadline in (True, None, float('nan'), float('inf'), time.monotonic() - 1, time.monotonic() + 60):
            source = BytesSocket(GOLDEN_TRANSCRIPT[0])
            with self.assertRaises(auth.PeerAuthError):
                auth._read(source, deadline)
            self.assertEqual([], source.timeouts)

    def test_fragmentation_does_not_renew_deadline(self):
        source = BytesSocket(GOLDEN_TRANSCRIPT[0])
        with patch.object(auth, 'monotonic', side_effect=(100, 100.1, 100.5, 101.1)):
            with self.assertRaises(auth.PeerAuthError):
                auth._read(source, 101)
        self.assertEqual([1, .9000000000000057, .5], source.timeouts)


class AuthSocketTests(unittest.TestCase):
    def setUp(self):
        self.policy = FabricatedPolicy()
        self.server = transport.LoopbackTransport(SESSION, EPOCH, policy=self.policy, connection_seconds=.8)
        self.addCleanup(self.server.shutdown)
        self.server.start()

    def connect(self):
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(1.5)
        connection.connect(self.server.address)
        self.addCleanup(connection.close)
        return connection

    def hello(self, connection, client_nonce=CLIENT_NONCE):
        context = auth._context(connection, SESSION, EPOCH, False)
        auth._send(connection, auth._envelope('client_hello', context, client_nonce), time.monotonic() + 1)
        proof = auth._read(connection, time.monotonic() + 1)
        auth._validate(proof, 'server_proof', context, client_nonce)
        auth._verify(KEY, proof)
        return context, proof

    def handshake(self, connection):
        context, proof = self.hello(connection)
        client = auth._signed(KEY, 'client_proof', context, CLIENT_NONCE, proof['server_nonce'])
        auth._send(connection, client, time.monotonic() + 1)
        ready = auth._read(connection, time.monotonic() + 1)
        auth._validate(ready, 'server_ready', context, CLIENT_NONCE, proof['server_nonce'])
        auth._verify(KEY, ready)
        return client

    def assert_closed_without_admission(self, connection):
        self.assertEqual(b'', read_closed(connection))
        self.assertEqual([], self.policy.submits)
        self.assertEqual(0, self.server.queue_status().retained)
        self.assertEqual({}, self.server._records)

    def test_authenticated_submission_and_receipt_but_owner_dispatch_stays_denied(self):
        connection = self.connect(); self.handshake(connection)
        connection.sendall(wire.encode_frame(submission())); connection.shutdown(socket.SHUT_WR)
        reply = auth._read(connection, time.monotonic() + 1)
        self.assertEqual('accepted', reply['state'])
        self.assertEqual(b'', connection.recv(1))
        self.assertEqual(1, len(self.policy.submits))
        self.assertEqual('refused', self.server.owner_tick(lambda _: self.fail('default dispatch bypass')).state)
        connection = self.connect(); self.handshake(connection)
        parsed = wire.parse_submit(submission(), SESSION, EPOCH)
        query = {'schema': 2, 'kind': 'receipt_read', 'request_id': parsed.request.request_id,
                 'session_id': SESSION, 'process_epoch': wire.epoch_wire(EPOCH), 'request_sha256': parsed.request_sha256}
        connection.sendall(wire.encode_frame(query)); connection.shutdown(socket.SHUT_WR)
        self.assertEqual('refused', auth._read(connection, time.monotonic() + 1)['state'])
        self.assertEqual(1, len(self.policy.submits))

    def test_hook_alone_cannot_authorize_listener_or_submission_or_dispatch(self):
        peer = auth.SessionKeyPeer(KEY)
        self.assertFalse(hasattr(peer, 'authorize_listener'))
        self.assertFalse(hasattr(peer, 'authorize_submit'))
        self.assertFalse(hasattr(peer, 'authorize_dispatch'))
        class PeerOnly(transport.DenyHostPolicy):
            authorize_peer = peer.authorize_peer
        server = transport.LoopbackTransport(SESSION, EPOCH, policy=PeerOnly())
        self.addCleanup(server.shutdown)
        with self.assertRaises(ValueError):
            server.start()

    def test_no_application_or_receipt_access_without_authentication(self):
        for value in (submission(), {'schema': 2, 'kind': 'receipt_read'}):
            connection = self.connect()
            send_rejected(connection, wire.encode_frame(value))
            self.assert_closed_without_admission(connection)
        self.assertIsNone(self.server.queue_status().mutation_blocked_reason)

    def test_wrong_key_client_cannot_access_application(self):
        connection = self.connect(); context, proof = self.hello(connection)
        wrong = auth._signed(WRONG_KEY, 'client_proof', context, CLIENT_NONCE, proof['server_nonce'])
        send_rejected(connection, wire.encode_frame(wrong) + wire.encode_frame(submission()))
        self.assert_closed_without_admission(connection)

    def test_reflection_and_role_relabel_fail_even_with_valid_server_mac(self):
        for relabel in (False, True):
            connection = self.connect(); _, proof = self.hello(connection)
            if relabel:
                proof['kind'] = 'client_proof'
            send_rejected(connection, wire.encode_frame(proof))
            self.assert_closed_without_admission(connection)

    def test_old_proof_rejected_under_new_server_nonce_even_same_test_client_nonce(self):
        first = self.connect(); context, proof = self.hello(first)
        replay = auth._signed(KEY, 'client_proof', context, CLIENT_NONCE, proof['server_nonce'])
        first.close()
        second = self.connect(); fresh_context, fresh = self.hello(second)
        self.assertNotEqual(proof['server_nonce'], fresh['server_nonce'])
        # Port equality is deliberately restored so this tests nonce freshness too.
        replay['connection'] = fresh_context['connection']
        send_rejected(second, wire.encode_frame(replay))
        self.assert_closed_without_admission(second)

    def test_duplicate_early_or_unexpected_handshake_frames_fail_closed(self):
        for kind in ('client_hello', 'server_ready', 'submit'):
            connection = self.connect(); context, proof = self.hello(connection)
            value = (submission() if kind == 'submit' else auth._envelope('client_hello', context, CLIENT_NONCE)
                     if kind == 'client_hello' else auth._signed(KEY, kind, context, CLIENT_NONCE, proof['server_nonce']))
            send_rejected(connection, wire.encode_frame(value))
            self.assert_closed_without_admission(connection)

    def test_surplus_proof_after_authentication_prevents_all_admission(self):
        connection = self.connect(); client = self.handshake(connection)
        connection.sendall(wire.encode_frame(client) + wire.encode_frame(submission()))
        connection.shutdown(socket.SHUT_WR)
        # Transport may emit its bounded generic error, but cannot access records.
        reply = auth._read(connection, time.monotonic() + 1)
        self.assertEqual('error', reply['kind'])
        self.assertEqual('extra_frame', reply['code'])
        self.assertEqual([], self.policy.submits)
        self.assertEqual({}, self.server._records)

    def test_wrong_session_pid_epoch_and_socket_binding_fail_before_server_proof(self):
        for field in ('session_id', 'pid', 'creation_filetime', 'client_port', 'server_port'):
            connection = self.connect(); context = auth._context(connection, SESSION, EPOCH, False)
            if field == 'session_id':
                context[field] = 'b' * 32
            else:
                context['process_epoch' if field in ('pid', 'creation_filetime') else 'connection'][field] += 1
            send_rejected(connection, wire.encode_frame(auth._envelope('client_hello', context, CLIENT_NONCE)))
            self.assert_closed_without_admission(connection)

    def test_truncated_hello_and_client_proof_fail_without_application_access(self):
        for stage in ('hello', 'client_proof'):
            for cut in (0, 1, 3, 8, -1):
                connection = self.connect()
                if stage == 'hello':
                    context = auth._context(connection, SESSION, EPOCH, False)
                    frame = wire.encode_frame(auth._envelope('client_hello', context, CLIENT_NONCE))
                else:
                    context, proof = self.hello(connection)
                    frame = wire.encode_frame(auth._signed(KEY, 'client_proof', context, CLIENT_NONCE, proof['server_nonce']))
                send_rejected(connection, frame[:cut])
                self.assert_closed_without_admission(connection)

    def test_timeout_and_slow_drip_do_not_refresh_transport_deadline(self):
        connection = self.connect()
        context = auth._context(connection, SESSION, EPOCH, False)
        frame = wire.encode_frame(auth._envelope('client_hello', context, CLIENT_NONCE))
        start = time.monotonic()
        for byte in frame[:5]:
            try:
                connection.sendall(bytes([byte]))
            except OSError:
                break
            time.sleep(.18)
        self.assert_closed_without_admission(connection)
        self.assertLess(time.monotonic() - start, 1.5)

    def test_rng_failure_and_peer_tuple_mismatch_are_silent_denials(self):
        with patch.object(auth.os, 'urandom', side_effect=OSError('fake private diagnostics')):
            connection = self.connect()
            context = auth._context(connection, SESSION, EPOCH, False)
            connection.sendall(wire.encode_frame(auth._envelope('client_hello', context, CLIENT_NONCE)))
            self.assert_closed_without_admission(connection)
        class DummySocket:
            family = socket.AF_INET
            type = socket.SOCK_STREAM
            def getsockname(self): return ('127.0.0.1', 50000)
            def getpeername(self): return ('127.0.0.1', 50001)
        binding = transport.Binding(SESSION, EPOCH)
        self.assertIs(False, self.policy.auth.authorize_peer(DummySocket(), ('127.0.0.1', 1), binding,
                                                           time.monotonic() + 1))


@unittest.skipUnless(sys.version_info >= (3, 11), 'Current CLI uses Python 3.11+ syntax.')
class CurrentClientAuthTests(unittest.TestCase):
    def setUp(self):
        from sims4foundry_cli import companion_auth, companion_transport, companion_protocol
        self.auth, self.client, self.v1 = companion_auth, companion_transport, companion_protocol
        request = self.v1.parse_request(submission()['request'], session_id=SESSION,
                                       process_epoch=self.v1.ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime))
        self.item = self.client.Submission(request)

    def test_cross_codec_golden_transcript_exact_bytes(self):
        values = [self.auth._envelope('client_hello', CONTEXT, CLIENT_NONCE)]
        values += [self.auth._signed(KEY, kind, CONTEXT, CLIENT_NONCE, SERVER_NONCE)
                   for kind in ('server_proof', 'client_proof', 'server_ready')]
        self.assertEqual(GOLDEN_TRANSCRIPT, tuple(self.v1.encode_frame(value) for value in values))
        for frame in GOLDEN_TRANSCRIPT:
            self.assertEqual(wire.decode_payload(frame[4:]), self.v1.decode_payload(frame[4:]))

    def test_real_current_client_server_success_and_wrong_key(self):
        policy = FabricatedPolicy()
        server = transport.LoopbackTransport(SESSION, EPOCH, policy=policy)
        self.addCleanup(server.shutdown); server.start()
        with self.assertRaises(self.auth.PeerAuthError):
            self.client.exchange_once(server.address[1], self.item, authorize_peer=self.auth.SessionKeyPeer(WRONG_KEY))
        self.assertEqual([], policy.submits)
        result = self.client.exchange_once(server.address[1], self.item, authorize_peer=self.auth.SessionKeyPeer(KEY))
        self.assertEqual('accepted', result['state'])
        result = self.client.exchange_once(server.address[1], self.client.ReceiptRead(self.item),
                                           authorize_peer=self.auth.SessionKeyPeer(KEY))
        self.assertEqual('accepted', result['state'])
        self.assertEqual(1, len(policy.submits))

    def fake_server(self, callback):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(2)
        self.addCleanup(listener.close)
        failures = []
        def serve():
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(1)
                    callback(connection)
            except Exception as exc:
                failures.append(exc)
        worker = threading.Thread(target=serve); worker.start()
        self.addCleanup(worker.join, 2)
        return listener.getsockname()[1], worker, failures

    def test_invalid_server_proof_or_ready_never_sends_application_request(self):
        for stage in ('proof', 'ready'):
            for variation in ('wrong_key', 'reflection', 'duplicate', 'extra_field', 'truncated', 'eof', 'wrong_nonce', 'wrong_epoch'):
                observed = []
                def fake(connection):
                    hello = auth._read(connection, time.monotonic() + 1)
                    context = auth._context(connection, SESSION, EPOCH, True)
                    proof = auth._signed(KEY, 'server_proof', context, hello['client_nonce'], SERVER_NONCE)
                    if stage == 'ready':
                        auth._send(connection, proof, time.monotonic() + 1)
                        value = auth._read(connection, time.monotonic() + 1)
                        observed.append(value['kind'])
                        proof = auth._signed(KEY, 'server_ready', context, hello['client_nonce'], SERVER_NONCE)
                    if variation == 'wrong_key':
                        proof['proof'] = '0' * 64
                    elif variation == 'reflection':
                        proof['kind'] = 'client_proof'
                    elif variation == 'duplicate':
                        proof['kind'] = 'client_hello' if stage == 'proof' else 'server_proof'
                    elif variation == 'extra_field':
                        proof['extra'] = True
                    elif variation == 'wrong_nonce':
                        proof['client_nonce'] = '1' * 64
                    elif variation == 'wrong_epoch':
                        proof['process_epoch']['creation_filetime'] += 1
                    frame = wire.encode_frame(proof)
                    connection.sendall(b'' if variation == 'eof' else frame[:-1] if variation == 'truncated' else frame)
                    connection.shutdown(socket.SHUT_WR)
                    observed.append(read_closed(connection))
                port, worker, failures = self.fake_server(fake)
                with self.subTest(stage=stage, variation=variation), self.assertRaises(self.auth.PeerAuthError):
                    self.client.exchange_once(port, self.item, authorize_peer=self.auth.SessionKeyPeer(KEY))
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual([], failures)
                self.assertEqual((['client_proof'] if stage == 'ready' else []) + [b''], observed)

    def test_actual_two_connection_proxy_cannot_relay_handshake(self):
        policy = FabricatedPolicy()
        target = transport.LoopbackTransport(SESSION, EPOCH, policy=policy)
        self.addCleanup(target.shutdown); target.start()
        observed = []
        def relay(connection):
            hello = auth._read(connection, time.monotonic() + 1)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as outgoing:
                outgoing.settimeout(1); outgoing.connect(target.address)
                # Forward the exact actual-client transcript, not invented identity.
                outgoing.sendall(wire.encode_frame(hello)); outgoing.shutdown(socket.SHUT_WR)
                observed.append(read_closed(outgoing))
        port, worker, failures = self.fake_server(relay)
        with self.assertRaises(self.auth.PeerAuthError):
            self.client.exchange_once(port, self.item, authorize_peer=self.auth.SessionKeyPeer(KEY))
        worker.join(2)
        self.assertEqual([], failures)
        self.assertEqual([b''], observed)
        self.assertEqual([], policy.submits)
        self.assertEqual(0, target.queue_status().retained)

    def test_client_error_diagnostics_do_not_expose_key_or_underlying_exception(self):
        class BrokenSocket:
            @property
            def family(self):
                raise OSError('PRIVATE-MARKER-' + KEY.hex())
        with self.assertRaises(self.auth.PeerAuthError):
            try:
                self.auth.SessionKeyPeer(KEY)(BrokenSocket(), time.monotonic() + 1, self.item)
            except self.auth.PeerAuthError:
                text = traceback.format_exc()
                self.assertNotIn(KEY.hex(), text)
                self.assertNotIn('PRIVATE-MARKER', text)
                raise
        self.assertEqual('SessionKeyPeer(<redacted>)', repr(self.auth.SessionKeyPeer(KEY)))
        with self.assertRaises(TypeError):
            pickle.dumps(self.auth.SessionKeyPeer(KEY))

    def test_actual_isolated_python37_server_success_failure_and_golden(self):
        interpreter = os.environ.get('FOUNDRY_COMPANION_PYTHON37')
        if not interpreter:
            self.skipTest('Set FOUNDRY_COMPANION_PYTHON37 for separate-process integration.')
        script = r'''
import sys
sys.path.insert(0, sys.argv[1])
assert sys.version_info[:2] == (3, 7)
from foundry_companion import peer_auth as auth, wire, transport
from foundry_companion.queue import ProcessEpoch
KEY = bytes(range(32))  # PUBLIC TEST-ONLY KEY, never provisioned.
SESSION = 'a' * 32
EPOCH = ProcessEpoch(1234, 133000000000000000)
class FabricatedPolicy(transport.DenyHostPolicy):
    def authorize_listener(self, binding): return True
    authorize_peer = auth.SessionKeyPeer(KEY).authorize_peer
    def authorize_submit(self, item, binding, deadline): return True
context = {'session_id': SESSION, 'process_epoch': wire.epoch_wire(EPOCH),
           'connection': {'client_port': 49152, 'server_port': 54321}}
values = [auth._envelope('client_hello', context, bytes(range(32, 64)).hex())]
values += [auth._signed(KEY, kind, context, bytes(range(32, 64)).hex(), bytes(range(64, 96)).hex())
           for kind in ('server_proof', 'client_proof', 'server_ready')]
for value in values:
    print(wire.encode_frame(value).hex(), flush=True)
server = transport.LoopbackTransport(SESSION, EPOCH, policy=FabricatedPolicy())
try:
    print(server.start()[1], flush=True)
    for command in sys.stdin:
        if command.strip() == 'count':
            print(server.queue_status().retained, flush=True)
        elif command.strip() == 'stop': break
finally:
    server.shutdown()
'''
        process = subprocess.Popen([interpreter, '-B', '-S', '-s', '-c', script, str(SOURCE)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   universal_newlines=True)
        def cleanup():
            if process.poll() is None:
                process.terminate(); process.wait(timeout=3)
            process.stdin.close(); process.stdout.close(); process.stderr.close()
        self.addCleanup(cleanup)
        actual = tuple(bytes.fromhex(process.stdout.readline().strip()) for _ in range(4))
        self.assertEqual(GOLDEN_TRANSCRIPT, actual)
        port = int(process.stdout.readline())
        with self.assertRaises(self.auth.PeerAuthError):
            self.client.exchange_once(port, self.item, authorize_peer=self.auth.SessionKeyPeer(WRONG_KEY))
        process.stdin.write('count\n'); process.stdin.flush()
        self.assertEqual('0', process.stdout.readline().strip())
        result = self.client.exchange_once(port, self.item, authorize_peer=self.auth.SessionKeyPeer(KEY))
        self.assertEqual('accepted', result['state'])
        result = self.client.exchange_once(port, self.client.ReceiptRead(self.item), authorize_peer=self.auth.SessionKeyPeer(KEY))
        self.assertEqual('accepted', result['state'])
        self.assertIs(result['independent_evidence'], False)
        process.stdin.write('count\n'); process.stdin.flush()
        self.assertEqual('1', process.stdout.readline().strip())
        process.stdin.write('stop\n'); process.stdin.flush()
        self.assertEqual(0, process.wait(timeout=3), process.stderr.read())


if __name__ == '__main__':
    unittest.main()
