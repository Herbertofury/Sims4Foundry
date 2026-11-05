"""Owned schema-3 socket fixtures; public fabricated identities, no game access.

This helper imports only companion and test fixture code, never host CLI modules.
Importing it does not start a listener. ProcessFixture requires an explicitly
configured actual Python 3.7 interpreter; source compatibility is not runtime proof.
"""
import sys

# Check the selected runtime before importing any companion or fixture modules.
if __name__ == '__main__':
    assert sys.version_info[:2] == (3, 7), 'Expected actual Python 3.7.'

from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'companion-src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion import live_wire, peer_auth, transport
from foundry_companion.live_executor import LiveQueueExecutor
from foundry_companion.queue import LiveQueueRequest, ProcessEpoch
from test_companion_live_observation import Harness, ProbeIterator, ProbeRow

KEY = bytes(range(32))  # Public TEST ONLY; never production key material.
SESSION = '2' * 32
EPOCH = ProcessEpoch(123, 456)
BOUND = 4
CONTEXT = ('10', '20', '5', 4, '99', 1, 'live')
MAX_LINE = 1024 * 1024
MAX_STDERR = 65536


def assert_no_game_imports():
    forbidden = {'services', 'sims4', 'interactions', 'zone', 'sims',
                 'simulation', 'objects', 'households', 'venues'}
    if any(name.split('.', 1)[0] in forbidden for name in sys.modules):
        raise AssertionError('Actual game module imported by isolated fixture.')


def disk_records(root):
    return [json.loads(path.read_text()) for path in
            sorted((Path(root) / 'records').glob('*.json'))]


def matches(request):
    if type(request) is not LiveQueueRequest:
        return False
    LiveQueueRequest.validate(request)
    ctx = request.expected_context
    return (request.session_id == SESSION
            and request.process_epoch == EPOCH
            and request.arguments.actor_sim_id == '30'
            and (ctx.zone_id, ctx.household_id, ctx.client_id, ctx.save_slot,
                 ctx.save_guid, ctx.zone_generation, ctx.mode) == CONTEXT)


class OwnerHarness(Harness):
    def __init__(self, rows, owner):
        self.owner, self.reader_threads = owner, []
        super().__init__()
        self.queued = ProbeIterator(self, 'queued',
                                    [ProbeRow(self, 100 + i, 200 + i) for i in range(rows)])
        # The inherited admission is fabricated and limited by the strict
        # submit/dispatch checks and LiveQueueObserver's exact binding checks.

    def hook(self, stage, value):
        thread = threading.current_thread()
        self.reader_threads.append(thread)
        if thread is not self.owner:
            raise AssertionError('Fabricated reader called outside its owner.')
        return super().hook(stage, value)


class FabricatedPolicy(transport.DenyHostPolicy):
    def __init__(self, root):
        self.root, self.owner = root, threading.current_thread()
        self.auth = peer_auth.SessionKeyPeer(KEY)
        self.submissions, self.admissions, self.disk = [], [], []
        self.auth_stages = []
        self.auth_codes = []
        self.auth_call_errors = []
        self.auth_deadline_reasons = []

    def note(self, collection, value):
        if len(collection) < 64:
            collection.append(value)

    def authorize_listener(self, binding):
        return binding.session_id == SESSION and binding.process_epoch == EPOCH

    def authorize_peer(self, connection, peer, binding, deadline):
        # Existing four-frame protocol and hook remain unchanged.
        self.note(self.auth_stages, 'start')
        try:
            result = self.auth.authorize_peer(connection, peer, binding, deadline)
        except peer_auth.PeerAuthError:
            self.note(self.auth_stages, 'auth_error')
            raise
        except OSError:
            self.note(self.auth_stages, 'os_error')
            raise
        except Exception:
            self.note(self.auth_stages, 'other_error')
            raise
        self.note(self.auth_stages, 'accepted' if result is True else 'refused')
        return result

    def allowed(self, item, binding):
        return (type(item) is live_wire.Submission
                and binding.session_id == SESSION and binding.process_epoch == EPOCH
                and matches(item.request)
                and item.canonical_payload == live_wire.canonical_payload(item.request)
                and item.request_sha256 == live_wire.request_sha256(item.request))

    def authorize_submit(self, item, binding, deadline):
        self.submissions.append((item, threading.current_thread()))
        self.disk.append(disk_records(self.root))
        return threading.current_thread() is not self.owner and self.allowed(item, binding)

    def authorize_dispatch(self, item, binding):
        self.admissions.append(threading.current_thread())
        return threading.current_thread() is self.owner and self.allowed(item, binding)


class AuditedServer(transport.LoopbackTransport):
    def __init__(self, policy):
        super().__init__(SESSION, EPOCH, policy=policy, connection_seconds=5)
        self.messages, self.worker_errors = [], []
        self.first_worker_done = threading.Event()

    def _serve(self, connection, peer, deadline):
        try:
            return super()._serve(connection, peer, deadline)
        except BaseException as error:
            self.worker_errors.append(error)
        finally:
            self.first_worker_done.set()

    def _handle(self, value, deadline):
        self.messages.append(deepcopy(value))
        return super()._handle(value, deadline)


class Fixture(object):
    def __init__(self, root, fault=None, rows=1):
        if fault not in (None, 'drop', 'timeout', 'auth_missing_ready', 'auth_invalid_ready'):
            raise ValueError('Unknown fixture fault.')
        if type(rows) is not int or not 0 <= rows <= 50:
            raise ValueError('Fixture row count must be 0 through 50.')
        self.policy = FabricatedPolicy(root)
        self.harness = OwnerHarness(rows, self.policy.owner)
        self.executor = LiveQueueExecutor(
            self.harness.read_context, self.harness.read_info,
            self.harness.read_instance, self.harness.admission, SESSION, EPOCH)
        self.server = AuditedServer(self.policy)
        self.ready, self.release, self.finished = (threading.Event() for _ in range(3))
        self.first_worker_done = self.server.first_worker_done
        self.executions, self.failures, self.last_result = [], [], None
        self.fault, self.armed, self.closed = fault, fault is not None, False
        self.port = None
        self.auth_ready_fault = 0
        original_require = peer_auth._require
        def traced_require(condition, code='peer_auth_failed'):
            if not condition:
                allowed = {'auth_binding','auth_socket','auth_deadline','auth_random','auth_envelope',
                           'auth_nonce','auth_proof','auth_truncated','auth_size','auth_depth',
                           'peer_auth_failed'}
                self.policy.note(self.policy.auth_codes, code if type(code) is str and code in allowed else 'other')
                if code == 'auth_deadline':
                    # Only bounded static categories leave the fixture. This
                    # reads the exact failing check's local value, not a later
                    # clock sample, and never logs times or exception text.
                    frame = sys._getframe(1)
                    left = frame.f_locals.get('left') if frame.f_code.co_name == '_remaining' else None
                    reason = ('expired' if type(left) in (int,float) and left <= 0 else
                              'above_limit' if type(left) in (int,float) and left > peer_auth.MAX_DEADLINE_SECONDS else
                              'invalid_or_other')
                    self.policy.note(self.policy.auth_deadline_reasons, reason)
            return original_require(condition, code)
        self.trace_patch = patch.object(peer_auth, '_require', traced_require)
        self.trace_patch.start()
        self.call_patches = []
        def wrap_call(original, label):
            def traced(*args, **kwargs):
                try:
                    return original(*args, **kwargs)
                except peer_auth.PeerAuthError:
                    self.policy.note(self.policy.auth_call_errors, label + '_auth')
                    raise
                except OSError:
                    self.policy.note(self.policy.auth_call_errors, label + '_os')
                    raise
                except Exception:
                    self.policy.note(self.policy.auth_call_errors, label + '_other')
                    raise
            return traced
        for label in ('_context','_read','_send','_validate','_verify'):
            item = patch.object(peer_auth, label, wrap_call(getattr(peer_auth,label),label[1:]))
            item.start()
            self.call_patches.append(item)
        self.original_auth_send = peer_auth._send
        self.auth_patch = (patch.object(peer_auth, '_send', self.auth_send)
                           if fault in ('auth_missing_ready', 'auth_invalid_ready') else None)
        self.original_send = transport._send_one
        self.patch = (patch.object(transport, '_send_one', self.send)
                      if fault in ('drop', 'timeout') else None)
        if self.patch:
            self.patch.start()
        if self.auth_patch:
            self.auth_patch.start()
        try:
            self.port = self.server.start()[1]
        except BaseException:
            self.close()
            raise

    @property
    def disk(self):
        return deepcopy(self.policy.disk)

    def auth_send(self, connection, value, deadline):
        owned = connection.getsockname()[1] == self.port
        if not self.armed or not owned or value.get('kind') != 'server_ready':
            return self.original_auth_send(connection, value, deadline)
        self.armed = False
        self.auth_ready_fault += 1
        self.ready.set()
        try:
            if self.fault == 'auth_missing_ready':
                connection.close()
                raise OSError('Fabricated missing final auth confirmation.')
            changed = deepcopy(value)
            # Flip one valid hexadecimal digit: structurally exact, wrong MAC.
            proof = changed['proof']
            changed['proof'] = ('0' if proof[0] != '0' else '1') + proof[1:]
            return self.original_auth_send(connection, changed, deadline)
        finally:
            self.finished.set()

    def send(self, connection, value, deadline):
        owned = connection.getsockname()[1] == self.port
        if not self.armed or not owned or value.get('kind') != 'receipt':
            return self.original_send(connection, value, deadline)
        self.armed = False
        self.ready.set()
        try:
            if not self.release.wait(BOUND):
                failure = AssertionError('Fixture reply gate was not released.')
                self.failures.append(failure)
                raise failure
            if self.fault == 'drop':
                connection.close()
            return self.original_send(connection, value, deadline)
        finally:
            self.finished.set()

    def execute(self, request):
        self.executions.append((request, threading.current_thread()))
        self.last_result = deepcopy(self.executor.execute(request))
        return deepcopy(self.last_result)

    def tick(self):
        receipt = self.server.owner_tick(self.execute)
        return receipt.state if receipt else None

    def stats(self):
        state = self.server.queue_status()
        owner = self.policy.owner
        return dict(messages=deepcopy(self.server.messages),
                    auth_stages=list(self.policy.auth_stages),
                    auth_codes=list(self.policy.auth_codes),
                    auth_call_errors=list(self.policy.auth_call_errors),
                    submissions=len(self.policy.submissions), executions=len(self.executions),
                    reader_calls=len(self.harness.calls), auth_ready_fault=self.auth_ready_fault,
                    owner_only=(all(thread is owner for _, thread in self.executions)
                                and all(thread is owner for thread in self.policy.admissions)
                                and all(thread is owner for thread in self.harness.reader_threads)),
                    worker_only=all(thread is not owner for _, thread in self.policy.submissions),
                    pending=state.pending, retained=state.retained,
                    blocked=state.mutation_blocked_reason)

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
            if self.auth_patch:
                self.auth_patch.stop()
            for item in reversed(self.call_patches):
                item.stop()
            self.trace_patch.stop()
        if not clean:
            raise AssertionError('Owned fixture transport failed to shut down.')
        if self.server.worker_errors:
            raise self.server.worker_errors[0]
        if self.failures:
            raise self.failures[0]


def fixture_server():
    fixture = Fixture(sys.argv[1], None if sys.argv[2] == '-' else sys.argv[2], int(sys.argv[3]))
    def emit(value):
        assert_no_game_imports()
        encoded = json.dumps(value)
        if len(encoded) > MAX_LINE:
            raise AssertionError('Fixture response exceeded bound.')
        print(encoded, flush=True)
    try:
        emit(dict(port=fixture.port, version=list(sys.version_info[:2])))
        while True:
            line = sys.stdin.readline(65)
            if not line:
                break
            if len(line) > 64 or not line.endswith('\n'):
                raise AssertionError('Fixture command exceeded bound.')
            command = line.strip()
            if command == 'tick':
                emit(dict(state=fixture.tick()))
            elif command == 'stats':
                emit(fixture.stats())
            elif command == 'disk':
                emit(fixture.disk)
            elif command == 'result':
                emit(fixture.last_result)
            elif command == 'ready':
                if not fixture.ready.wait(BOUND):
                    raise AssertionError('Authenticated reply gate not reached.')
                emit(dict(ready=True))
            elif command == 'worker_done':
                if not fixture.first_worker_done.wait(BOUND):
                    raise AssertionError('Owned first worker did not finish.')
                emit(dict(worker_done=True))
            elif command == 'release':
                fixture.release.set()
                if not fixture.finished.wait(BOUND) or not fixture.first_worker_done.wait(BOUND):
                    raise AssertionError('Owned reply fault worker did not finish.')
                emit(dict(released=True))
            elif command == 'stop':
                break
            else:
                raise AssertionError('Unknown fixture command.')
    finally:
        try:
            fixture.close()
        finally:
            assert_no_game_imports()


class ProcessFixture(object):
    """Explicit actual-3.7 child, bounded protocol and mandatory pipe joins."""
    def __init__(self, testcase, root, fault=None, rows=1):
        interpreter = os.environ.get('FOUNDRY_COMPANION_PYTHON37')
        if not interpreter:
            testcase.skipTest('Set FOUNDRY_COMPANION_PYTHON37 for actual Python 3.7 exchange.')
        args = [interpreter, '-B', '-S', '-s', str(Path(__file__).resolve()),
                str(root), fault or '-', str(rows)]
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, universal_newlines=True)
        self.lines = queue.Queue(maxsize=64)
        self.stderr, self.errors, self.readers = [], [], []
        self.closed = False
        testcase.addCleanup(self.close)

        def enqueue(value):
            try:
                self.lines.put_nowait(value)
            except queue.Full:
                self.errors.append(AssertionError('Owned stdout queue exceeded bound.'))

        def read_stdout():
            try:
                while True:
                    line = self.process.stdout.readline(MAX_LINE + 2)
                    if not line:
                        break
                    if len(line) > MAX_LINE + 1 or not line.endswith('\n'):
                        raise AssertionError('Owned stdout frame exceeded bound.')
                    enqueue(line)
            except BaseException as error:
                self.errors.append(error)
            finally:
                enqueue(None)

        def read_stderr():
            retained, overflow = 0, False
            try:
                while True:
                    chunk = self.process.stderr.read(4096)
                    if not chunk:
                        break
                    if retained < MAX_STDERR:
                        self.stderr.append(chunk[:MAX_STDERR - retained])
                        retained += len(self.stderr[-1])
                    elif not overflow:
                        self.errors.append(AssertionError('Owned stderr exceeded bound.'))
                        overflow = True
            except BaseException as error:
                self.errors.append(error)

        for callback, name in ((read_stdout, 'stdout'), (read_stderr, 'stderr')):
            thread = threading.Thread(target=callback, name='live-fixture-' + name)
            self.readers.append(thread)
            thread.start()
        hello = self.read()
        testcase.assertEqual([3, 7], hello['version'])
        self.port = hello['port']

    def read(self):
        try:
            line = self.lines.get(timeout=BOUND + 1)
        except queue.Empty:
            raise AssertionError('Owned Python 3.7 fixture did not answer.') from None
        if line is None:
            raise AssertionError('Owned Python 3.7 fixture exited: ' + ''.join(self.stderr))
        return json.loads(line)

    def command(self, command):
        if command not in ('tick', 'stats', 'ready', 'release', 'disk', 'result', 'worker_done'):
            raise ValueError('Unknown fixture command.')
        self.process.stdin.write(command + '\n')
        self.process.stdin.flush()
        return self.read()

    def tick(self):
        return self.command('tick')['state']

    def stats(self):
        return self.command('stats')

    @property
    def disk(self):
        return self.command('disk')

    @property
    def last_result(self):
        return self.command('result')

    def close(self):
        if self.closed:
            return
        self.closed = True
        process, failure = self.process, None
        try:
            if process.poll() is None:
                try:
                    process.stdin.write('stop\n')
                    process.stdin.flush()
                    process.wait(timeout=BOUND + 1)
                except BaseException as error:
                    failure = error
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=BOUND)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=BOUND)
        finally:
            for thread in self.readers:
                if thread.ident is not None:
                    thread.join(BOUND)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        if any(thread.is_alive() for thread in self.readers):
            raise AssertionError('Owned process pipe reader failed to stop.')
        if self.errors:
            raise self.errors[0]
        if failure is not None:
            raise failure
        if process.returncode != 0 or self.stderr:
            raise AssertionError('Owned Python 3.7 fixture failed: ' + ''.join(self.stderr))


if __name__ == '__main__':
    fixture_server()
