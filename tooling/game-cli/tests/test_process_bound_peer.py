"""Fabricated integration only: no sockets, native tables, desktop or game I/O."""

from contextlib import ExitStack
import copy
import inspect
import os
from pathlib import Path
import pickle
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from sims4foundry_cli import companion_auth as auth
from sims4foundry_cli import companion_live_protocol as live
from sims4foundry_cli import companion_live_transport as live_transport
from sims4foundry_cli import companion_protocol as wire
from sims4foundry_cli import companion_transport as transport
from sims4foundry_cli import loopback_owner as owners
from sims4foundry_cli import process_bound_peer as bound
from sims4foundry_cli import process_lease as leases
from sims4foundry_cli.errors import FoundryError


# Public deterministic fixture material, never a real session credential.
KEY = bytes(range(32))
EPOCH = wire.ProcessEpoch(42, 123)
SESSION = "a" * 32


def identity():
    return dict(schema=1, kind="foundry_windows_identity", pid=42,
                creation_filetime=123, executable=r"C:\Fixtures\TS4_x64.exe",
                executable_sha256="a" * 64, hwnd=100, thread_id=200,
                window_class="Canvas-Fixture", client_width=640, client_height=480,
                client_origin=[0, 0], dpi=96, integrity=8192, observed_at=time.time())


def submission(schema=2):
    if schema == 2:
        return transport.Submission(wire.Request("b" * 32, SESSION,
            wire.ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime),
            wire.Operation.OBSERVE, wire.ObserveArguments()))
    return live.LiveQueueSubmission(live.LiveQueueRequest("b" * 32, SESSION,
        wire.ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime), live.LiveQueueArguments("7"),
        live.LiveQueueExpectedContext("1", "2", "3", 4, "5", 6)))


def receipt(item, schema):
    return dict(schema=schema, kind="receipt", request_id=item.request.request_id,
        session_id=item.request.session_id, process_epoch=item.request.process_epoch.to_wire(),
        request_sha256=item.request_sha256, state="not_found", reason="history_missing",
        adapter_result=None, independent_evidence=False)


class FabricatedSocket:
    """No OS descriptor: only the module's private test binding admits this."""

    def __init__(self, events=None, *, response=None):
        self.family, self.type, self.kind = socket.AF_INET, socket.SOCK_STREAM, socket.SOCK_STREAM
        self.local, self.remote = ("127.0.0.1", 49152), ("127.0.0.1", 54321)
        self.descriptor = 900
        self.events = events if events is not None else []
        self.sent, self.timeouts, self.incoming = [], [], b""
        self.response = response
        self.handshake = False
        self.proof_key = KEY
        self.connect_count = 0
        self.closed = False

    def fileno(self):
        return self.descriptor

    def getsockopt(self, level, name):
        assert (level, name) == (socket.SOL_SOCKET, socket.SO_TYPE)
        return self.kind

    def getsockname(self):
        return self.local

    def getpeername(self):
        self.events.append("socket")
        return self.remote

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        assert address == self.remote
        self.connect_count += 1

    def sendall(self, frame):
        value = wire.decode_payload(frame[4:])
        self.sent.append(value)
        self.events.append("send:" + value["kind"])
        if self.handshake and value["kind"] in ("client_hello", "client_proof"):
            context = {key: value[key] for key in ("session_id", "process_epoch", "connection")}
            kind = "server_proof" if value["kind"] == "client_hello" else "server_ready"
            reply = auth._signed(self.proof_key, kind, context, value["client_nonce"], "f" * 64)
            self.incoming += wire.encode_frame(reply)
        elif value["kind"] in ("submit", "receipt_read") and self.response is not None:
            self.incoming += wire.encode_frame(self.response)

    def recv(self, size):
        # Exercise fragmented reads through the real auth and sender codecs.
        count = min(size, 11)
        result, self.incoming = self.incoming[:count], self.incoming[count:]
        return result

    def shutdown(self, direction):
        assert direction == socket.SHUT_WR
        self.events.append("write_eof")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        self.descriptor = -1


class ProcessBoundPeerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.events = []
        self.api = mock.Mock()
        self.api.open_target.return_value = "target"
        self.api.create_mutex.return_value = "mutex"
        self.api.acquire.return_value = False
        self.api.current_security_context.return_value = ("S-1-5-21-123", 1)
        self.api.security_context.return_value = ("S-1-5-21-123", 1)
        self.stack.enter_context(mock.patch.object(leases, "_WindowsApi", return_value=self.api))
        self.stack.enter_context(mock.patch.object(leases, "_held_names", set()))
        self.inspector = self.stack.enter_context(mock.patch.object(
            leases.windows, "inspect", side_effect=self.inspect_identity))
        self.stack.enter_context(mock.patch.object(bound, "_SOCKET_TYPE", FabricatedSocket))
        self.clock = self.stack.enter_context(mock.patch.object(bound, "monotonic", return_value=10.0))
        self.stack.enter_context(mock.patch.object(auth, "monotonic", return_value=10.0))
        self.actual_owner = owners.observe_connection_owner
        self.owner = self.stack.enter_context(mock.patch.object(
            owners, "observe_connection_owner", side_effect=self.observe_owner))
        self.actual_auth = auth.SessionKeyPeer.__call__
        self.auth = self.stack.enter_context(mock.patch.object(
            auth.SessionKeyPeer, "__call__", side_effect=self.authenticate))
        self.held = self.stack.enter_context(ExitStack())
        self.lease = self.held.enter_context(leases.process_lease(identity()))

    def inspect_identity(self, *args):
        self.events.append("lease")
        return identity()

    def observe_owner(self, pid, server_port, client_port):
        self.events.append("owner")
        return owners.ConnectionOwnerObservation(pid, server_port, client_port)

    def authenticate(self, peer, connection, deadline, item):
        self.events.append("auth")
        return True

    def peer(self):
        return bound.ProcessBoundPeer(self.lease, KEY)

    def assert_code(self, code, action):
        with self.assertRaises((FoundryError, wire.ProtocolError)) as caught:
            action()
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_exact_lifecycle_and_oriented_owner_tuple_for_both_schemas(self):
        for schema in (2, 3):
            with self.subTest(schema=schema):
                peer, item, connection = self.peer(), submission(schema), FabricatedSocket(self.events)
                self.events.clear()
                self.assertIs(True, peer(connection, 12.0, item))
                self.assertEqual(["lease", "socket", "owner", "lease", "socket", "auth", "lease", "socket"],
                                 self.events)
                self.owner.assert_called_with(42, 54321, 49152)
                auth_peer, actual_socket, deadline, detached = self.auth.call_args.args
                self.assertIsInstance(auth_peer, auth.SessionKeyPeer)
                self.assertIs(actual_socket, connection)
                self.assertEqual(12.0, deadline)
                self.assertIsNot(item, detached)
                self.assertIsNot(item.request, detached.request)
                self.assertIsNot(item.request.process_epoch, detached.request.process_epoch)
                self.assertEqual(bound._snapshot(item)[1], bound._snapshot(detached)[1])
                self.assertFalse(self.lease.mutation_permitted)
                self.assertTrue(self.lease.reconciliation_required)
                self.api.release.assert_not_called()
                self.api.close.assert_not_called()

    def test_constructor_requires_exact_active_lease_not_duck_type_or_subclass(self):
        class Duck:
            def observe(self):
                return identity()
        class Derived(leases._ProcessLease):
            pass
        for candidate in (None, identity(), Duck(), mock.Mock(spec=leases._ProcessLease), Derived(identity())):
            with self.subTest(candidate=type(candidate).__name__):
                self.assert_code("process_peer_lease", lambda: bound.ProcessBoundPeer(candidate, KEY))
        self.assert_code("lease_context", lambda: bound.ProcessBoundPeer(leases.process_lease(identity()), KEY))
        self.owner.assert_not_called()
        self.auth.assert_not_called()

    def test_constructor_and_call_cannot_use_an_exited_lease(self):
        peer = self.peer()
        self.held.close()
        self.assert_code("lease_context", lambda: bound.ProcessBoundPeer(self.lease, KEY))
        self.assert_code("lease_context", lambda: peer(FabricatedSocket(), 12.0, submission()))
        self.owner.assert_not_called()
        self.auth.assert_not_called()
        self.api.release.assert_called_once_with("mutex")

    def test_instance_observe_shadow_does_not_replace_genuine_lease_observation(self):
        self.lease.observe = mock.Mock(side_effect=AssertionError("shadow"))
        peer = self.peer()
        self.assertIs(True, peer(FabricatedSocket(), 12.0, submission()))
        self.lease.observe.assert_not_called()

    def test_constructor_rejects_wrong_thread_and_callback_is_consumed_on_wrong_thread(self):
        peer, errors = self.peer(), []
        def worker():
            for action in (self.peer, lambda: peer(FabricatedSocket(), 12.0, submission())):
                try:
                    action()
                except FoundryError as error:
                    errors.append(error.code)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(["lease_thread", "lease_thread"], errors)
        self.assert_code("process_peer_used", lambda: peer(FabricatedSocket(), 12.0, submission()))
        self.owner.assert_not_called()
        self.auth.assert_not_called()

    def test_strict_key_and_no_key_generation_loading_saving_or_socket_creation(self):
        for key in (None, "x" * 32, bytearray(32), b"", bytes(31), bytes(65), True):
            with self.subTest(key_type=type(key).__name__):
                self.assert_code("auth_key", lambda: bound.ProcessBoundPeer(self.lease, key))
        with mock.patch.object(auth.os, "urandom", side_effect=AssertionError("key generation")), \
                mock.patch("builtins.open", side_effect=AssertionError("file access")), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("socket creation")):
            for length in (32, 48, 64):
                bound.ProcessBoundPeer(self.lease, bytes(length))

    def test_redacted_and_not_copyable_or_serializable(self):
        peer = self.peer()
        self.assertEqual("ProcessBoundPeer(<redacted>)", repr(peer))
        self.assertFalse(hasattr(peer, "__dict__"))
        for operation in (pickle.dumps, copy.copy, copy.deepcopy):
            with self.subTest(operation=operation.__name__), self.assertRaises(TypeError):
                operation(peer)

    def test_public_signature_has_no_policy_observer_key_source_or_backend(self):
        self.assertEqual(["lease", "session_key"], list(inspect.signature(bound.ProcessBoundPeer).parameters))
        for argument in ("policy", "observer", "owner_source", "authenticate", "backend", "clock", "socket_factory"):
            with self.subTest(argument=argument), self.assertRaises(TypeError):
                bound.ProcessBoundPeer(self.lease, KEY, **{argument: lambda: True})

    def test_import_does_not_load_native_apis_create_sockets_or_generate_keys(self):
        program = """
from unittest import mock
with mock.patch('socket.socket', side_effect=AssertionError('socket creation')), \\
     mock.patch('ctypes.WinDLL', create=True, side_effect=AssertionError('native load')), \\
     mock.patch('os.urandom', side_effect=AssertionError('random generation')):
    from sims4foundry_cli import process_bound_peer
"""
        result = subprocess.run([sys.executable, "-B", "-c", program],
            env=dict(os.environ, PYTHONPATH=str(Path(bound.__file__).resolve().parents[1])),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_non_windows_owner_path_fails_closed_without_authentication(self):
        # Explicitly exercise the unavailable-platform guard, not the native API.
        self.owner.side_effect = self.actual_owner
        with mock.patch.object(owners.os, "name", "posix"), \
                mock.patch.object(owners, "_load_native_api", side_effect=AssertionError("native call")):
            self.assert_code("connection_owner_unavailable", lambda: self.peer()(FabricatedSocket(), 12.0, submission()))
        self.auth.assert_not_called()

    def test_all_callback_attempts_are_single_use(self):
        for first in (submission(), None):
            peer = self.peer()
            if first is not None:
                self.assertTrue(peer(FabricatedSocket(), 12.0, first))
            else:
                self.assert_code("invalid_request", lambda: peer(FabricatedSocket(), 12.0, first))
            self.assert_code("process_peer_used", lambda: peer(FabricatedSocket(), 12.0, submission()))

    def test_submission_and_nested_subclasses_or_receipt_objects_are_not_callbacks_inputs(self):
        class Subclass(transport.Submission):
            pass
        class RequestSubclass(wire.Request):
            pass
        valid = submission()
        derived_request = RequestSubclass(valid.request.request_id, SESSION, EPOCH,
                                          wire.Operation.OBSERVE, wire.ObserveArguments())
        bad_nested = submission()
        object.__setattr__(bad_nested, "request", derived_request)
        for item in (None, {}, wire.Request, transport.ReceiptRead(valid),
                     live.LiveQueueReceiptRead(submission(3)), Subclass(valid.request), bad_nested):
            with self.subTest(item_type=type(item).__name__):
                self.assert_code("invalid_request", lambda: self.peer()(FabricatedSocket(), 12.0, item))
        self.owner.assert_not_called()
        self.auth.assert_not_called()

    def test_corrupted_fields_and_bool_numeric_values_fail_before_owner_or_auth(self):
        cases = (("ttl_seconds", float("nan")), ("ttl_seconds", True), ("ttl_seconds", 31),
                 ("request", None), ("expected_context", object()))
        for field, value in cases:
            item = submission()
            object.__setattr__(item, field, value)
            with self.subTest(field=field, value_type=type(value).__name__), \
                    self.assertRaises((FoundryError, wire.ProtocolError)):
                self.peer()(FabricatedSocket(), 12.0, item)
        for schema in (2, 3):
            item = submission(schema)
            object.__setattr__(item.request.process_epoch, "pid", True)
            with self.subTest(schema=schema), self.assertRaises((FoundryError, wire.ProtocolError)):
                self.peer()(FabricatedSocket(), 12.0, item)
        self.owner.assert_not_called()
        self.auth.assert_not_called()

    def test_schema2_mutation_arguments_and_context_are_strictly_detached(self):
        for operation, arguments in ((wire.Operation.SET_SPEED, wire.SetSpeedArguments(2)),
                                     (wire.Operation.ENTER_CAS, wire.EnterCasArguments("7")),
                                     (wire.Operation.REQUEST_EXIT_MENU, wire.RequestExitMenuArguments())):
            item = transport.Submission(wire.Request("b" * 32, SESSION, EPOCH, operation, arguments),
                                         5, transport.ExpectedContext("1", "2", 3, "4"))
            self.assertTrue(self.peer()(FabricatedSocket(), 12.0, item))
            detached = self.auth.call_args.args[3]
            self.assertIsNot(detached.request.arguments, arguments)
            self.assertIsNot(detached.expected_context, item.expected_context)
            self.assertIs(type(detached.ttl_seconds), int)
            self.assertEqual(item.to_wire(), detached.to_wire())
            self.assertFalse(self.lease.mutation_permitted)

    def test_shadowed_instance_serializers_are_not_called(self):
        for schema in (2, 3):
            item = submission(schema)
            for target in (item, item.request, item.request.process_epoch, item.request.arguments):
                object.__setattr__(target, "to_wire", mock.Mock(side_effect=AssertionError("shadow serializer")))
            self.assertTrue(self.peer()(FabricatedSocket(), 12.0, item))
            for target in (item, item.request, item.request.process_epoch, item.request.arguments):
                target.to_wire.assert_not_called()

    def test_wrong_pid_or_creation_filetime_is_refused_before_owner_and_hmac(self):
        for schema in (2, 3):
            for epoch in (wire.ProcessEpoch(43, 123), wire.ProcessEpoch(42, 124)):
                item = submission(schema)
                object.__setattr__(item.request, "process_epoch", epoch)
                self.assert_code("process_peer_identity", lambda: self.peer()(FabricatedSocket(), 12.0, item))
        self.owner.assert_not_called()
        self.auth.assert_not_called()

    def test_bad_fresh_lease_observation_is_refused(self):
        peer = self.peer()
        self.inspector.side_effect = lambda *args: dict(identity(), creation_filetime=124)
        self.assert_code("identity_changed", lambda: peer(FabricatedSocket(), 12.0, submission()))
        self.owner.assert_not_called()
        self.auth.assert_not_called()

    def test_process_exit_during_owner_query_prevents_hmac(self):
        peer = self.peer()
        def exited(*args):
            self.api.validate_lifetime.side_effect = FoundryError("process_gone", "fixture exit")
            return self.observe_owner(*args)
        self.owner.side_effect = exited
        self.assert_code("process_gone", lambda: peer(FabricatedSocket(), 12.0, submission()))
        self.auth.assert_not_called()

    def test_process_exit_during_authentication_prevents_success(self):
        def exited(*args):
            self.api.validate_lifetime.side_effect = FoundryError("process_gone", "fixture exit")
            return True
        self.auth.side_effect = exited
        self.assert_code("process_gone", lambda: self.peer()(FabricatedSocket(), 12.0, submission()))
        self.owner.assert_called_once()
        self.auth.assert_called_once()

    def test_socket_must_be_exact_type_not_subclass_or_wrapper(self):
        class DerivedSocket(FabricatedSocket):
            pass
        for candidate in (object(), mock.Mock(spec=FabricatedSocket), DerivedSocket()):
            self.assert_code("process_peer_socket", lambda: self.peer()(candidate, 12.0, submission()))
        self.owner.assert_not_called()
        self.auth.assert_not_called()

    def test_socket_family_stream_descriptor_and_exact_loopback_tuple(self):
        invalid = (("family", socket.AF_INET6), ("type", socket.SOCK_DGRAM),
                   ("kind", socket.SOCK_DGRAM), ("descriptor", -1),
                   ("remote", ("127.0.0.2", 54321)), ("local", ("0.0.0.0", 49152)),
                   ("remote", ("localhost", 54321)), ("local", ("::1", 49152)),
                   ("remote", ["127.0.0.1", 54321]), ("remote", ("127.0.0.1", 54321, 0)),
                   ("remote", ("127.0.0.1", True)), ("local", ("127.0.0.1", 0)),
                   ("remote", ("127.0.0.1", 65536)), ("remote", ("127.0.0.1", 49152)))
        for field, value in invalid:
            connection = FabricatedSocket()
            setattr(connection, field, value)
            with self.subTest(field=field, value=value):
                self.assert_code("process_peer_socket", lambda: self.peer()(connection, 12.0, submission()))
        self.owner.assert_not_called()
        self.auth.assert_not_called()

    def test_unconnected_socket_os_error_is_preserved(self):
        error = OSError("fixture not connected")
        with mock.patch.object(FabricatedSocket, "getpeername", side_effect=error):
            with self.assertRaises(OSError) as caught:
                self.peer()(FabricatedSocket(), 12.0, submission())
        self.assertIs(error, caught.exception)
        self.owner.assert_not_called()
        self.auth.assert_not_called()

    def test_owner_denial_error_or_cancellation_is_not_swallowed_or_retried(self):
        for error in (FoundryError("connection_owner_denied", "fixture denial"),
                      TimeoutError("uncertain fixture query"), KeyboardInterrupt()):
            peer = self.peer()
            self.owner.reset_mock()
            self.owner.side_effect = error
            with self.subTest(error_type=type(error).__name__), self.assertRaises(type(error)) as caught:
                peer(FabricatedSocket(), 12.0, submission())
            self.assertIs(error, caught.exception)
            self.owner.assert_called_once_with(42, 54321, 49152)
            self.assert_code("process_peer_used", lambda: peer(FabricatedSocket(), 12.0, submission()))
        self.auth.assert_not_called()

    def test_no_listener_or_inverse_tuple_or_truthy_owner_fallback(self):
        for result in (None, True, {}, owners.ConnectionOwnerObservation(43, 54321, 49152),
                       owners.ConnectionOwnerObservation(42, 49152, 54321),
                       owners.ConnectionOwnerObservation(True, 54321, 49152)):
            self.owner.side_effect = None
            self.owner.return_value = result
            self.assert_code("process_peer_owner", lambda: self.peer()(FabricatedSocket(), 12.0, submission()))
        self.auth.assert_not_called()

    def test_socket_endpoint_or_handle_change_after_owner_prevents_hmac(self):
        for field, value in (("remote", ("127.0.0.1", 54322)), ("local", ("127.0.0.1", 49153)),
                             ("descriptor", 901)):
            connection = FabricatedSocket()
            def changed(*args):
                setattr(connection, field, value)
                return self.observe_owner(*args)
            self.owner.side_effect = changed
            self.assert_code("process_peer_socket_changed", lambda: self.peer()(connection, 12.0, submission()))
        self.auth.assert_not_called()

    def test_socket_endpoint_or_handle_change_after_auth_prevents_success(self):
        for field, value in (("remote", ("127.0.0.1", 54322)), ("local", ("127.0.0.1", 49153)),
                             ("descriptor", 901)):
            connection = FabricatedSocket()
            def changed(*args):
                setattr(connection, field, value)
                return True
            self.auth.side_effect = changed
            self.assert_code("process_peer_socket_changed", lambda: self.peer()(connection, 12.0, submission()))

    def test_closed_socket_after_auth_prevents_success(self):
        connection = FabricatedSocket()
        def closed(*args):
            connection.descriptor = -1
            return True
        self.auth.side_effect = closed
        self.assert_code("process_peer_socket", lambda: self.peer()(connection, 12.0, submission()))

    def test_intent_changes_during_owner_or_auth_do_not_authorize_send(self):
        for schema in (2, 3):
            for phase in ("owner", "auth_original", "auth_detached"):
                item = submission(schema)
                self.owner.side_effect = self.observe_owner
                self.auth.side_effect = self.authenticate
                if phase == "owner":
                    def changed_owner(*args):
                        object.__setattr__(item.request, "request_id", "c" * 32)
                        return self.observe_owner(*args)
                    self.owner.side_effect = changed_owner
                else:
                    def changed_auth(peer, connection, deadline, detached):
                        changed = detached if phase == "auth_detached" else item
                        object.__setattr__(changed.request, "request_id", "c" * 32)
                        return True
                    self.auth.side_effect = changed_auth
                with self.subTest(schema=schema, phase=phase):
                    self.assert_code("process_peer_intent_changed", lambda: self.peer()(FabricatedSocket(), 12.0, item))

    def test_auth_requires_exact_true_and_preserves_exception_or_uncertain_outcome(self):
        for result in (False, None, 1, "allowed"):
            self.auth.side_effect = None
            self.auth.return_value = result
            self.assert_code("process_peer_auth", lambda: self.peer()(FabricatedSocket(), 12.0, submission()))
        for error in (auth.PeerAuthError("auth_proof", "fixture mismatch"), TimeoutError("uncertain fixture write"),
                      FoundryError("denied", "fixture refusal"), KeyboardInterrupt()):
            self.auth.side_effect = error
            with self.subTest(error_type=type(error).__name__), self.assertRaises(type(error)) as caught:
                self.peer()(FabricatedSocket(), 12.0, submission())
            self.assertIs(error, caught.exception)

    def test_deadline_rejects_invalid_expired_boolean_and_renewed_budget(self):
        for deadline in (None, True, "12", float("nan"), float("inf"), 0, 10, 9, 15.001):
            with self.subTest(deadline=deadline):
                self.assert_code("process_peer_deadline", lambda: self.peer()(FabricatedSocket(), deadline, submission()))
        self.owner.assert_not_called()
        self.auth.assert_not_called()

    def test_deadline_expiry_during_each_blocking_phase_prevents_later_work(self):
        for phase in ("lease", "owner", "post_owner_lease", "auth", "post_auth_lease"):
            self.clock.return_value = 10.0
            self.inspector.side_effect = self.inspect_identity
            self.owner.side_effect = self.observe_owner
            self.auth.side_effect = self.authenticate
            peer = self.peer()
            self.owner.reset_mock()
            self.auth.reset_mock()
            counts = [0]
            def observed(*args):
                counts[0] += 1
                target = {"lease": 1, "post_owner_lease": 2, "post_auth_lease": 3}.get(phase)
                if counts[0] == target:
                    self.clock.return_value = 12.0
                return self.inspect_identity(*args)
            self.inspector.side_effect = observed
            def owned(*args):
                if phase == "owner":
                    self.clock.return_value = 12.0
                return self.observe_owner(*args)
            self.owner.side_effect = owned
            def authenticated(*args):
                if phase == "auth":
                    self.clock.return_value = 12.0
                return self.authenticate(*args)
            self.auth.side_effect = authenticated
            with self.subTest(phase=phase):
                self.assert_code("process_peer_deadline", lambda: peer(FabricatedSocket(), 12.0, submission()))
                self.assertEqual(int(phase != "lease"), self.owner.call_count)
                self.assertEqual(int(phase in ("auth", "post_auth_lease")), self.auth.call_count)

    def test_real_hmac_composes_with_both_senders_and_receipt_reads_without_real_sockets(self):
        for schema, sender in ((2, transport), (3, live_transport)):
            for query in (False, True):
                item = submission(schema)
                expected = receipt(item, schema)
                message = ((transport.ReceiptRead(item) if schema == 2 else live.LiveQueueReceiptRead(item))
                           if query else item)
                connection = FabricatedSocket(self.events, response=expected)
                connection.handshake = True
                peer = self.peer()
                self.events.clear()
                with self.subTest(schema=schema, query=query), \
                        mock.patch.object(auth.SessionKeyPeer, "__call__", self.actual_auth), \
                        mock.patch.object(auth.os, "urandom", return_value=bytes(range(32))), \
                        mock.patch.object(sender, "monotonic", return_value=10.0), \
                        mock.patch.object(socket, "socket", return_value=connection) as factory:
                    self.assertEqual(expected, sender.exchange_once(54321, message, authorize_peer=peer))
                factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
                self.assertEqual(1, connection.connect_count)
                self.assertTrue(connection.closed)
                self.assertEqual(["client_hello", "client_proof", "receipt_read" if query else "submit"],
                                 [value["kind"] for value in connection.sent])
                self.assertLess(self.events.index("owner"), self.events.index("send:client_hello"))
                app = "send:receipt_read" if query else "send:submit"
                self.assertLess(max(index for index, event in enumerate(self.events) if event == "lease"),
                                self.events.index(app))
                self.assertTrue(all(0 < timeout <= 2 for timeout in connection.timeouts))

    def test_failed_owner_sends_no_hmac_or_application_and_sender_never_reconnects(self):
        for sender, schema in ((transport, 2), (live_transport, 3)):
            connection = FabricatedSocket()
            connection.handshake = True
            peer = self.peer()
            denial = FoundryError("connection_owner_denied", "fixture denial")
            self.owner.side_effect = denial
            with mock.patch.object(socket, "socket", return_value=connection) as factory, \
                    mock.patch.object(sender, "monotonic", return_value=10.0):
                self.assert_code("connection_owner_denied", lambda: sender.exchange_once(
                    54321, submission(schema), authorize_peer=peer))
            factory.assert_called_once()
            self.assertEqual([], connection.sent)
            self.assertEqual(1, connection.connect_count)
            self.assertTrue(connection.closed)
        self.auth.assert_not_called()

    def test_wrong_hmac_key_and_post_auth_process_exit_never_send_application(self):
        for schema, sender in ((2, transport), (3, live_transport)):
            for failure in ("wrong_key", "process_exit"):
                connection = FabricatedSocket()
                connection.handshake = True
                connection.proof_key = b"x" * 32 if failure == "wrong_key" else KEY
                self.api.validate_lifetime.side_effect = None
                peer = self.peer()
                def authenticate_then_exit(*args):
                    result = self.actual_auth(*args)
                    self.api.validate_lifetime.side_effect = FoundryError("process_gone", "fixture exit")
                    return result
                authentication = authenticate_then_exit if failure == "process_exit" else self.actual_auth
                with self.subTest(schema=schema, failure=failure), \
                        mock.patch.object(auth.SessionKeyPeer, "__call__", authentication), \
                        mock.patch.object(auth.os, "urandom", return_value=bytes(range(32))), \
                        mock.patch.object(sender, "monotonic", return_value=10.0), \
                        mock.patch.object(socket, "socket", return_value=connection):
                    code = "auth_proof" if failure == "wrong_key" else "process_gone"
                    self.assert_code(code, lambda: sender.exchange_once(54321, submission(schema), authorize_peer=peer))
                self.assertTrue(connection.closed)
                self.assertNotIn("submit", [value["kind"] for value in connection.sent])
                self.assertEqual(1, connection.connect_count)


if __name__ == "__main__":
    unittest.main()
