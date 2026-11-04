"""Durable recovery over real authenticated owned loopback sockets.

All keys, context, process identities, adapters and admission are fabricated.
No game/profile/desktop access, production policy or live qualification.
The fixture-server branch also runs in an isolated Python 3.7 subprocess.
"""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'companion-src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion import peer_auth, transport
from foundry_companion.queue import ProcessEpoch, QueueError

KEY = bytes(range(32))  # PUBLIC TEST-ONLY KEY, never provisioned to a real host.
SESSION = 'a' * 32
EPOCH = ProcessEpoch(1234, 133000000000000000)
CONTEXT = ('10', '20', 4, '99')
BOUND = 4


def disk_records(root):
    return [json.loads(path.read_text()) for path in sorted((Path(root) / 'records').glob('*.json'))]


class FabricatedPolicy(transport.DenyHostPolicy):
    """Explicit test admission, including a FAKE current-context comparison."""
    def __init__(self, root):
        self.auth, self.root = peer_auth.SessionKeyPeer(KEY), root
        self.owner = threading.current_thread()
        self.current_context, self.current_epoch = CONTEXT, EPOCH
        self.submissions, self.admissions, self.disk = [], [], []

    def authorize_listener(self, binding):
        return True

    def authorize_peer(self, connection, peer, binding, deadline):
        return self.auth.authorize_peer(connection, peer, binding, deadline)

    def authorize_submit(self, item, binding, deadline):
        self.submissions.append((item, threading.current_thread()))
        self.disk.append(disk_records(self.root))
        return True

    def authorize_dispatch(self, item, binding):
        self.admissions.append(threading.current_thread())
        request = item.request
        expected = (request.expected_zone_id, request.expected_household_id,
                    request.expected_save_slot, request.expected_save_guid)
        return (threading.current_thread() is self.owner
                and request.process_epoch == self.current_epoch
                and (request.operation == 'observe' or expected == self.current_context))


class AuditedServer(transport.LoopbackTransport):
    def __init__(self, policy):
        super().__init__(SESSION, EPOCH, policy=policy, connection_seconds=5)
        self.messages, self.worker_errors = [], []
        self.first_worker_done = threading.Event()

    def _serve(self, connection, peer, deadline):
        try:
            return super()._serve(connection, peer, deadline)
        except BaseException as error:
            # Unittest cannot assert failures left only on background stderr.
            self.worker_errors.append(error)
        finally:
            self.first_worker_done.set()

    def _handle(self, value, deadline):
        self.messages.append(deepcopy(value))
        return super()._handle(value, deadline)


class Fixture:
    """Actual server; a scoped fault affects only the first receipt write."""
    def __init__(self, root, fault=None):
        self.policy = FabricatedPolicy(root)
        self.server = AuditedServer(self.policy)
        self.executions, self.failures = [], []
        self.ready, self.release, self.finished = threading.Event(), threading.Event(), threading.Event()
        self.fault, self.armed, self.closed = fault, fault is not None, False
        self.original_send = transport._send_one
        self.patch = patch.object(transport, '_send_one', self.send) if fault else None
        if self.patch:
            self.patch.start()
        try:
            self.port = self.server.start()[1]
        except BaseException:
            self.close()
            raise

    def send(self, connection, value, deadline):
        if not self.armed or value.get('kind') != 'receipt':
            return self.original_send(connection, value, deadline)
        self.armed = False
        self.ready.set()  # Authenticated submission has reached the real queue.
        try:
            if not self.release.wait(BOUND):
                error = AssertionError('Fixture reply gate was not released.')
                self.failures.append(error)
                raise error
            if self.fault == 'drop':
                connection.close()  # Real EOF and failed reply write, not a fake receipt.
            return self.original_send(connection, value, deadline)
        finally:
            self.finished.set()

    def execute(self, request):
        self.executions.append((request, threading.current_thread()))
        return {'outcome': 'verified', 'fixture_only': True, 'game_qualified': False}

    def tick(self):
        result = self.server.owner_tick(self.execute)
        return result.state if result else None

    def stats(self):
        status = self.server.queue_status()
        return {'messages': deepcopy(self.server.messages),
                'submissions': len(self.policy.submissions), 'executions': len(self.executions),
                'owner_only': all(thread is self.policy.owner for _, thread in self.executions)
                              and all(thread is self.policy.owner for thread in self.policy.admissions),
                'worker_only': all(thread is not self.policy.owner for _, thread in self.policy.submissions),
                'pending': status.pending, 'retained': status.retained,
                'blocked': status.mutation_blocked_reason}

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.release.set()
        try:
            clean = self.server.shutdown(timeout=BOUND)
        finally:
            if self.patch:
                self.patch.stop()
        if not clean:
            raise AssertionError('Owned transport threads/sockets did not shut down cleanly.')
        if self.server.worker_errors:
            raise self.server.worker_errors[0]
        if self.failures:
            raise self.failures[0]


def fixture_server():
    """Bounded command protocol; ticks execute only on this process's owner."""
    assert sys.version_info[:2] == (3, 7), 'Expected the optional actual Python 3.7 interpreter.'
    fixture = Fixture(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    def emit(value):
        print(json.dumps(value), flush=True)
    try:
        emit({'port': fixture.port, 'version': list(sys.version_info[:2])})
        for line in sys.stdin:
            command = line.strip()
            if command == 'stats':
                emit(fixture.stats())
            elif command == 'tick':
                emit({'state': fixture.tick()})
            elif command == 'ready':
                if not fixture.ready.wait(BOUND):
                    raise AssertionError('No authenticated submission reached the reply gate.')
                emit({'ready': True})
            elif command == 'release':
                fixture.release.set()
                if not fixture.finished.wait(BOUND) or not fixture.server.first_worker_done.wait(BOUND):
                    raise AssertionError('Reply fault and its owned worker did not complete.')
                emit({'released': True})
            elif command == 'disk':
                emit(fixture.policy.disk)
            elif command == 'stop':
                break
            else:
                raise AssertionError('Unexpected fixture command.')
    finally:
        fixture.close()


if __name__ == '__main__' and '--fixture-server' in sys.argv:
    fixture_server()
    sys.exit(0)

# The durable CLI journal is intentionally NOT a Python 3.7 module.
from sims4foundry_cli import companion_auth, companion_journal as ledger, companion_protocol as v1
from sims4foundry_cli import companion_transport as client
from sims4foundry_cli.errors import FoundryError


def submission(number=1, epoch=None):
    return client.Submission(v1.Request('{:032x}'.format(number), SESSION,
                             epoch or v1.ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime),
                             v1.Operation.SET_SPEED, v1.SetSpeedArguments(2)),
                             5.0, client.ExpectedContext(*CONTEXT))


def admit(_message, _ticket):
    return True  # Fabricated local admission only.


def callback_diagnostic(error):
    """Bounded fabricated-fixture type/code/errno fields, no exception messages."""
    name = type(error).__name__
    if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,63}', name) is None:
        name = 'Exception'
    fields = ['type=' + name]
    code = getattr(error, 'code', None)
    if type(code) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', code):
        fields.append('code=' + code)
    for name in ('errno', 'winerror'):
        value = getattr(error, name, None)
        if type(value) is int and -(2**31) <= value < 2**31:
            fields.append('{}={}'.format(name, value))
    return ', '.join(fields)


@contextmanager
def elapsed_after_receipt(seconds):
    """Advance only the client clock after a real authenticated reply and EOF.

    A deterministic scheduling-budget probe, not a sleep or fake socket reply.
    The client view is anchored at its exchange start, then advances exactly by
    seconds at receipt parsing; wall runtime cannot consume the probe's margin.
    Server/auth clocks and deadlines, socket I/O and peer checks stay real.
    """
    original_parse, original_clock = client.parse_receipt, client.monotonic
    started, elapsed = [None], [0]
    def clock():
        if started[0] is None:
            started[0] = original_clock()
        return started[0] + elapsed[0]
    def parse(value, item):
        result = original_parse(value, item)
        elapsed[0] = seconds
        return result
    with patch.object(client, 'parse_receipt', parse), patch.object(
            client, 'monotonic', clock):
        yield


class OwnedCall:
    """Capture and later assert every worker exception, including assertions."""
    def __init__(self, callback):
        self.result, self.error, self.done = None, None, threading.Event()
        def run():
            try:
                self.result = callback()
            except BaseException as error:
                self.error = error
            finally:
                self.done.set()
        self.thread = threading.Thread(target=run, name='foundry-recovery-client')
        self.thread.start()

    def join(self):
        self.thread.join(BOUND)
        if self.thread.is_alive():
            raise AssertionError('Owned client worker did not finish within its bound.')


class ProcessFixture:
    """Owned Python 3.7 server with bounded reads and strict reader cleanup."""
    def __init__(self, testcase, root, fault):
        interpreter = os.environ.get('FOUNDRY_COMPANION_PYTHON37')
        if not interpreter:
            testcase.skipTest('Set FOUNDRY_COMPANION_PYTHON37 for actual separate-process recovery.')
        args = [interpreter, '-B', '-S', '-s', str(Path(__file__).resolve()), '--fixture-server', str(root), fault]
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, universal_newlines=True)
        self.lines, self.errors, self.stderr = queue.Queue(), [], []
        self.readers, self.closed = [], False
        testcase.addCleanup(self.close)
        def read_stdout():
            try:
                for line in self.process.stdout:
                    self.lines.put(line)
            except BaseException as error:
                self.errors.append(error)
            finally:
                self.lines.put(None)
        def read_stderr():
            try:
                self.stderr.append(self.process.stderr.read())
            except BaseException as error:
                self.errors.append(error)
        for target, name in ((read_stdout, 'stdout'), (read_stderr, 'stderr')):
            thread = threading.Thread(target=target, name='foundry-recovery-' + name)
            self.readers.append(thread)
            thread.start()
        hello = self.read()
        testcase.assertEqual([3, 7], hello['version'])
        self.port = hello['port']

    def read(self):
        try:
            line = self.lines.get(timeout=BOUND + 1)
        except queue.Empty:
            raise AssertionError('Owned Python 3.7 server did not answer within its bound.') from None
        if line is None:
            raise AssertionError('Owned Python 3.7 server closed unexpectedly: ' + ''.join(self.stderr))
        return json.loads(line)

    def command(self, value):
        self.process.stdin.write(value + '\n')
        self.process.stdin.flush()
        return self.read()

    def tick(self):
        return self.command('tick')['state']

    def stats(self):
        return self.command('stats')

    def close(self):
        if self.closed:
            return
        self.closed = True
        process, failure = self.process, None
        if process.poll() is None:
            try:
                process.stdin.write('stop\n')
                process.stdin.flush()
                process.wait(timeout=BOUND + 1)
            except BaseException as error:
                # Cleanup is mandatory even for an unexpected pipe failure;
                # preserve and re-raise it instead of treating it as success.
                failure = error
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=BOUND)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=BOUND)
        for thread in self.readers:
            if thread.ident is not None:
                thread.join(BOUND)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        if any(thread.is_alive() for thread in self.readers):
            raise AssertionError('Owned process pipe-reader thread did not stop.')
        if self.errors:
            raise self.errors[0]
        if failure is not None:
            raise failure
        if process.returncode != 0 or ''.join(self.stderr):
            raise AssertionError('Owned Python 3.7 server failed: ' + ''.join(self.stderr))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'journal'
        self.journal = ledger.create_journal(self.root)
        self.item = submission()
        self.calls, self.before_socket, self.exchange_errors = [], [], []

    def fixture(self, fault=None, root=None):
        fixture = Fixture(root or self.root, fault)
        self.addCleanup(fixture.close)
        return fixture

    def exchange(self, fixture, timeout=BOUND, key=KEY):
        # Happy paths use the fixture's four-second scheduling budget, within
        # the real server's five-second deadline. Fault cases pass their own
        # shorter deadlines; there is still exactly one exchange and no retry.
        peer = companion_auth.SessionKeyPeer(key)
        def exchange(message, ticket):
            self.calls.append((message, ticket))
            try:
                self.before_socket.append((disk_records(self.root), json.loads((self.root / 'head.json').read_text())))
                return client.exchange_once(fixture.port, message, authorize_peer=peer, timeout_seconds=timeout)
            except BaseException as error:
                self.exchange_errors.append(error)
                raise
        return exchange

    def submit(self, fixture, **kwargs):
        return self.with_callback_diagnostics(lambda: self.journal.submit_once(
            self.item, authorize=admit, exchange=self.exchange(fixture, **kwargs)))

    def reconcile(self, fixture, **kwargs):
        return self.with_callback_diagnostics(lambda: ledger.Journal(self.root).reconcile(
            self.item.request.request_id, authorize=admit, exchange=self.exchange(fixture, **kwargs)))

    def with_callback_diagnostics(self, callback):
        before = len(self.exchange_errors)
        try:
            return callback()
        except FoundryError as error:
            if len(self.exchange_errors) > before:
                # Preserve the journal's original fail-closed exception/code.
                # Only this call's last callback error is relevant, not an
                # earlier intentional auth/EOF/timeout fault in the same test.
                error.add_note('Owned loopback fixture callback: '
                               + callback_diagnostic(self.exchange_errors[-1]))
            raise

    def assert_code(self, expected, callback):
        with self.assertRaises(FoundryError) as caught:
            callback()
        self.assertEqual(expected, caught.exception.code)

    def assert_blocked(self):
        forbidden = Mock(side_effect=AssertionError('A replacement mutation reached transport.'))
        self.assert_code('mutation_unresolved', lambda: ledger.Journal(self.root).submit_once(
            submission(2), authorize=admit, exchange=forbidden))
        forbidden.assert_not_called()

    def assert_no_replay(self):
        forbidden = Mock(side_effect=AssertionError('Identical intent was transmitted again.'))
        old = ledger.Journal(self.root).status(self.item.request.request_id)
        same = ledger.Journal(self.root).submit_once(self.item, authorize=forbidden, exchange=forbidden)
        self.assertEqual(old, same)
        forbidden.assert_not_called()
        return same

    def assert_durable_submission(self, rows):
        self.assertEqual(['intent', 'attempt'], [row['kind'] for row in rows])
        self.assertEqual(self.item.to_wire(), rows[0]['data']['submission'])
        self.assertEqual(self.item.request_sha256, rows[0]['data']['request_sha256'])
        self.assertEqual(self.calls[0][1].to_wire(), rows[1]['data'])
        self.assertEqual('submit', rows[1]['data']['kind'])

    def assert_verified_is_unresolved(self, status):
        self.assertEqual('adapter_returned', status.state)
        self.assertEqual('verified', status.receipt.adapter_outcome)
        self.assertTrue(status.unresolved)
        self.assertFalse(status.independent_evidence)
        self.assertFalse(status.live_qualified)
        self.assert_blocked()

    def test_exact_durable_intent_precedes_socket_and_owner_dispatch(self):
        fixture = self.fixture()
        self.assertEqual('accepted', self.submit(fixture).state)
        rows, head = self.before_socket[0]
        self.assert_durable_submission(rows)
        self.assertEqual(2, head['sequence'])
        self.assert_durable_submission(fixture.policy.disk[0])
        self.assertEqual(self.item.to_wire(), fixture.server.messages[0])
        self.assertEqual(0, fixture.stats()['executions'])
        self.assertEqual('verified', fixture.tick())
        self.assertIsNone(fixture.tick())
        self.assert_verified_is_unresolved(self.reconcile(fixture))
        self.assert_no_replay()
        stats = fixture.stats()
        self.assertEqual(['submit', 'receipt_read'], [x['kind'] for x in stats['messages']])
        self.assertEqual(1, stats['submissions'])
        self.assertEqual(1, stats['executions'])
        self.assertTrue(stats['worker_only'])
        self.assertTrue(stats['owner_only'])
        self.assertIs(type(self.calls[1][0]), client.ReceiptRead)
        self.assertEqual(self.item.to_wire(), self.calls[1][0].submission.to_wire())
        self.assertEqual('receipt_read', self.calls[1][1].kind)
        self.assertEqual(2, self.calls[1][1].sequence)

    def test_reply_loss_after_one_dispatch_reopens_as_uncertain_receipt_only(self):
        self.run_lost_reply('drop')

    def test_actual_socket_timeout_after_one_dispatch_never_replays(self):
        self.run_lost_reply('timeout')

    def run_lost_reply(self, fault, process=False):
        fixture = ProcessFixture(self, self.root, fault) if process else self.fixture(fault)
        # Timeout is a real socket deadline, never a synchronization sleep.
        call = OwnedCall(lambda: self.submit(fixture, timeout=1 if fault == 'timeout' else 3))
        self.addCleanup(call.join)
        try:
            if process:
                self.assertEqual({'ready': True}, fixture.command('ready'))
            else:
                self.assertTrue(fixture.ready.wait(BOUND), 'No authenticated admission reached the reply gate.')
            self.assertEqual(1, fixture.stats()['pending'])
            self.assertEqual('verified', fixture.tick())
            self.assertIsNone(fixture.tick())
            if fault == 'timeout':
                self.assertTrue(call.done.wait(BOUND), 'Client deadline failed to expire.')
        finally:
            if process:
                self.assertEqual({'released': True}, fixture.command('release'))
            else:
                fixture.release.set()
        call.join()
        if not process:
            self.assertTrue(fixture.server.first_worker_done.wait(BOUND), 'Faulted server worker did not finish.')
        self.assertIsInstance(call.error, FoundryError)
        self.assertEqual('exchange_uncertain', call.error.code)
        self.assertEqual(1, len(self.exchange_errors))
        error = self.exchange_errors[0]
        if fault == 'timeout':
            self.assertIsInstance(error, (socket.timeout, v1.ProtocolError))
            if isinstance(error, v1.ProtocolError):
                self.assertEqual('connection_deadline', error.code)
        elif isinstance(error, v1.ProtocolError):
            self.assertEqual('truncated_frame', error.code)
        else:
            self.assertIsInstance(error, OSError)
            self.assertNotIsInstance(error, (socket.timeout, TimeoutError))
            self.assertTrue(error.errno in (errno.ECONNRESET, errno.ECONNABORTED, errno.ENOTCONN,
                                          errno.EPIPE, 10053, 10054, 10057)
                            or getattr(error, 'winerror', None) in (10053, 10054, 10057))
        self.assertIsNone(call.result)
        status = self.assert_no_replay()
        self.assertEqual('unknown', status.state)
        self.assertTrue(status.uncertainty_latched)
        self.assertEqual('possible', status.transmission)
        self.assert_blocked()
        self.assert_verified_is_unresolved(self.reconcile(fixture))
        self.assertTrue(ledger.Journal(self.root).status()[0].uncertainty_latched)
        self.assert_no_replay()
        stats = fixture.stats()
        self.assertEqual(1, stats['executions'])
        self.assertEqual(1, stats['submissions'])
        if fault == 'drop':
            self.assertEqual('response_lost', stats['blocked'])
        self.assertEqual(['submit', 'receipt_read'], [x['kind'] for x in stats['messages']])
        self.assertTrue(stats['owner_only'])
        rows = fixture.command('disk')[0] if process else fixture.policy.disk[0]
        self.assert_durable_submission(rows)

    def test_callback_failure_after_actual_reply_and_dispatch_never_resubmits(self):
        fixture = self.fixture()
        exchange, replies = self.exchange(fixture), []
        def fail_after_reply(message, ticket):
            replies.append(exchange(message, ticket))
            fixture.tick()
            raise RuntimeError('FABRICATED-CALLBACK-FAILURE')
        self.assert_code('exchange_uncertain', lambda: self.journal.submit_once(
            self.item, authorize=admit, exchange=fail_after_reply))
        self.assertEqual('accepted', replies[0]['state'])
        self.assertEqual([], self.exchange_errors)
        self.assertEqual(1, fixture.stats()['executions'])
        self.assertTrue(self.assert_no_replay().uncertainty_latched)
        self.assert_verified_is_unresolved(self.reconcile(fixture))
        self.assertIsNone(fixture.tick())
        self.assertEqual(1, fixture.stats()['executions'])
        self.assertEqual(['submit', 'receipt_read'], [x['kind'] for x in fixture.stats()['messages']])
        self.assertNotIn('FABRICATED-CALLBACK-FAILURE', ''.join(p.read_text() for p in (self.root / 'records').iterdir()))

    def test_adapter_exception_is_unknown_and_receipt_reads_never_dispatch(self):
        fixture = self.fixture()
        self.submit(fixture)
        attempts = []
        def failed(request):
            attempts.append(request)
            raise RuntimeError('FABRICATED-ADAPTER-FAILURE')
        self.assertEqual('unknown', fixture.server.owner_tick(failed).state)
        for _ in range(2):
            status = self.reconcile(fixture)
            self.assertEqual('unknown', status.state)
            self.assertTrue(status.unresolved)
            self.assertFalse(status.independent_evidence)
            self.assertIsNone(fixture.server.owner_tick(failed))
        self.assertEqual(1, len(attempts))
        self.assert_no_replay()
        self.assert_blocked()

    def test_lost_server_history_cannot_authorize_replay_or_new_mutation(self):
        original = self.fixture()
        self.submit(original)
        self.assertEqual('verified', original.tick())
        original.close()
        # Deliberate volatile-history loss, NOT a real-host restart permission.
        replacement = self.fixture()
        status = self.reconcile(replacement)
        self.assertEqual('not_found', status.state)
        self.assertEqual('history_missing', status.receipt.reason)
        self.assertTrue(status.uncertainty_latched)
        self.assertTrue(status.unresolved)
        self.assert_no_replay()
        self.assert_blocked()
        self.assertIsNone(replacement.tick())
        stats = replacement.stats()
        self.assertEqual(['receipt_read'], [x['kind'] for x in stats['messages']])
        self.assertEqual(0, stats['submissions'])
        self.assertEqual(0, stats['executions'])
        self.assertEqual('history_missing', stats['blocked'])

    def test_each_fabricated_current_context_mismatch_refuses_before_dispatch(self):
        for index, changed in enumerate(('11', '21', 5, '100')):
            with self.subTest(field=index):
                root = Path(self.temp.name) / ('context-' + str(index))
                journal = ledger.create_journal(root)
                fixture = self.fixture(root=root)
                journal.submit_once(self.item, authorize=admit, exchange=self.exchange(fixture))
                current = list(CONTEXT); current[index] = changed
                fixture.policy.current_context = tuple(current)
                self.assertEqual('refused', fixture.tick())
                status = journal.reconcile(self.item.request.request_id, authorize=admit, exchange=self.exchange(fixture))
                self.assertEqual('host_dispatch_denied', status.receipt.reason)
                self.assertFalse(status.unresolved)
                self.assertEqual(0, fixture.stats()['executions'])
                self.assertIsNone(fixture.tick())
                fixture.close()

    def test_changed_fabricated_process_epoch_at_dispatch_is_refused(self):
        fixture = self.fixture()
        self.submit(fixture)
        fixture.policy.current_epoch = ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime + 1)
        self.assertEqual('refused', fixture.tick())
        status = self.reconcile(fixture)
        self.assertEqual('host_dispatch_denied', status.receipt.reason)
        self.assertFalse(status.unresolved)
        self.assertEqual(0, fixture.stats()['executions'])

    def test_wrong_process_epoch_fails_authentication_before_application_submission(self):
        fixture = self.fixture()
        self.item = submission(epoch=v1.ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime + 1))
        self.assert_code('exchange_uncertain', lambda: self.submit(fixture))
        self.assertEqual(1, len(self.exchange_errors))
        self.assertIsInstance(self.exchange_errors[0], companion_auth.PeerAuthError)
        self.assertTrue(self.assert_no_replay().uncertainty_latched)
        self.assert_blocked()
        self.assertIsNone(fixture.tick())
        stats = fixture.stats()
        self.assertEqual([], stats['messages'])
        self.assertEqual(0, stats['submissions'])
        self.assertEqual(0, stats['retained'])
        self.assertEqual(0, stats['executions'])

    def test_wrong_key_is_uncertain_locally_but_never_reaches_queue(self):
        fixture = self.fixture()
        self.assert_code('exchange_uncertain', lambda: self.submit(fixture, key=b'x' * 32))
        self.assertEqual(1, len(self.exchange_errors))
        self.assertIsInstance(self.exchange_errors[0], companion_auth.PeerAuthError)
        self.assertTrue(self.assert_no_replay().unresolved)
        self.assert_blocked()
        self.assertIsNone(fixture.tick())
        self.assertEqual([], fixture.stats()['messages'])
        self.assertEqual(0, fixture.stats()['retained'])

    def test_changed_intent_same_id_never_enters_socket_after_restart(self):
        fixture = self.fixture()
        self.submit(fixture)
        changed = replace(self.item, expected_context=client.ExpectedContext('11', '20', 4, '99'))
        forbidden = Mock(side_effect=AssertionError('Conflicting intent reached the socket.'))
        self.assert_code('intent_conflict', lambda: ledger.Journal(self.root).submit_once(
            changed, authorize=admit, exchange=forbidden))
        forbidden.assert_not_called()
        self.assertEqual(1, len(fixture.stats()['messages']))
        self.assertEqual('verified', fixture.tick())
        self.assertIsNone(fixture.tick())
        self.assertEqual(1, fixture.stats()['executions'])

    def test_nonowner_tick_cannot_dispatch_an_authenticated_journal_request(self):
        fixture = self.fixture()
        self.submit(fixture)
        call = OwnedCall(fixture.tick)
        self.addCleanup(call.join)
        call.join()
        self.assertIsInstance(call.error, QueueError)
        self.assertEqual('wrong_thread', call.error.code)
        self.assertEqual(0, fixture.stats()['executions'])
        self.assertEqual('verified', fixture.tick())
        self.assert_verified_is_unresolved(self.reconcile(fixture))

    def test_happy_fixture_allows_bounded_scheduling_after_receipt_eof(self):
        fixture = self.fixture()
        self.submit(fixture)
        self.assertEqual('verified', fixture.tick())
        with elapsed_after_receipt(2.5):
            self.assert_verified_is_unresolved(self.reconcile(fixture))
        self.assertEqual([], self.exchange_errors)
        self.assertEqual(['submit', 'receipt_read'], [x['kind'] for x in fixture.stats()['messages']])
        self.assertEqual(1, fixture.stats()['executions'])

    def test_explicit_short_budget_after_receipt_eof_remains_uncertain(self):
        fixture = self.fixture()
        self.submit(fixture)
        self.assertEqual('verified', fixture.tick())
        with elapsed_after_receipt(2.5), self.assertRaises(FoundryError) as caught:
            self.reconcile(fixture, timeout=2)
        self.assertEqual('exchange_uncertain', caught.exception.code)
        self.assertEqual(['Owned loopback fixture callback: type=ProtocolError, code=connection_deadline'],
                         caught.exception.__notes__)
        self.assertEqual(1, len(self.exchange_errors))
        self.assertIsInstance(self.exchange_errors[0], v1.ProtocolError)
        self.assertEqual('connection_deadline', self.exchange_errors[0].code)
        self.assertTrue(self.assert_no_replay().uncertainty_latched)
        self.assert_blocked()
        self.assertIsNone(fixture.tick())
        self.assertEqual(['submit', 'receipt_read'], [x['kind'] for x in fixture.stats()['messages']])
        self.assertEqual(1, fixture.stats()['executions'])

    def test_callback_diagnostics_exclude_messages_and_invalid_codes(self):
        private = 'FABRICATED-PRIVATE-DATA-' * 1000
        error = v1.ProtocolError(private, private)
        self.assertEqual('type=ProtocolError', callback_diagnostic(error))
        error = OSError(errno.ECONNRESET, private)
        self.assertEqual('type=ConnectionResetError, errno={}'.format(errno.ECONNRESET),
                         callback_diagnostic(error))

    def test_actual_python37_reply_loss_recovery(self):
        self.run_lost_reply('drop', process=True)

    def test_actual_python37_timeout_recovery(self):
        self.run_lost_reply('timeout', process=True)


if __name__ == '__main__':
    unittest.main()
