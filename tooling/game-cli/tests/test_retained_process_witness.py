"""Query-only fabricated witnesses and one freshly spawned owned child."""
from copy import deepcopy
import hashlib
import os
import subprocess
import sys
from unittest.mock import Mock, patch
import unittest

if not os.path.isabs(__file__):
    raise ImportError('Absolute lexical test path required.')
_src = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src'))
_package = os.path.join(_src, 'sims4foundry_cli')
_modules = [('sims4foundry_cli', os.path.join(_package, '__init__.py'))]
_modules += [('sims4foundry_cli.' + stem, os.path.join(_package, stem + '.py'))
             for stem in ('retained_process_witness', 'process_exit', 'windows', 'launch', 'io', 'errors', 'path_identity')]
def matches(module, filename):
    actual = getattr(module, '__file__', None)
    return (type(actual) is str and os.path.isabs(actual) and
            os.path.normcase(os.path.normpath(actual)) == os.path.normcase(os.path.normpath(filename)))
for name, filename in _modules:
    if name in sys.modules and not matches(sys.modules[name], filename):
        raise ImportError('Refusing a foreign cached Foundry module: ' + name)
sys.path.insert(0, _src)
from sims4foundry_cli import retained_process_witness as witnesses, process_exit
from sims4foundry_cli.errors import FoundryError
for name, filename in _modules:
    if name in sys.modules and not matches(sys.modules[name], filename):
        raise ImportError('Loaded Foundry module has an unexpected lexical origin: ' + name)


IDENTITY = {'pid': 123, 'creation_filetime': 456, 'executable': r'C:\Fixture\owned.exe',
            'executable_sha256': 'a' * 64}


def cleanup_owned_child(child):
    """Bounded cleanup for the exact fixture child, never a game process."""
    try:
        if child.stdin and not child.stdin.closed:
            child.stdin.close()
        if child.poll() is None:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()  # only the Popen child created by this fixture
                child.wait(timeout=10)
                raise AssertionError('Owned fixture child required forced cleanup.')
    finally:
        if child.stderr:
            child.stderr.close()


class FakeApi:
    def __init__(self):
        self.calls = []
        self.state = process_exit.WAIT_TIMEOUT
    def open(self, pid):
        self.calls.append(('open', pid))
        return 'owned-fake-handle'
    def identity(self, handle):
        self.calls.append(('identity', handle))
        return IDENTITY['executable'], IDENTITY['creation_filetime']
    def wait(self, handle, milliseconds):
        self.calls.append(('wait', handle, milliseconds))
        return self.state
    def close(self, handle):
        self.calls.append(('close', handle))


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.registry = witnesses.RetainedProcessRegistry(self.api, lambda identity: True)
        self.token = 'b' * 32

    def test_lost_caller_reply_recovers_same_handle_without_reopen(self):
        self.registry.acquire(self.token, IDENTITY)  # caller discards the returned wrapper
        recovered = self.registry.recover(self.token, IDENTITY)
        self.assertEqual(recovered.snapshot(), IDENTITY)
        self.assertTrue(recovered.is_running())
        self.assertEqual([x for x in self.api.calls if x[0] == 'open'], [('open', 123)])
        self.api.state = process_exit.WAIT_OBJECT_0
        self.assertTrue(recovered.wait_exit(0)['terminated'])
        released = recovered.close()
        self.assertEqual(released['outcome'], 'provider_reported_closed')
        self.assertFalse(released['os_release_verified'])
        with self.assertRaises(FoundryError):
            self.registry.recover(self.token, IDENTITY)

    def test_open_then_raise_latches_unknown_and_never_reopens(self):
        opened = []
        def acquired_then_throw(pid):
            opened.append(pid)
            raise OSError('fixture open acknowledgment lost')
        self.api.open = acquired_then_throw
        with self.assertRaises(OSError):
            self.registry.acquire(self.token, IDENTITY)
        self.assertEqual(self.registry.status(self.token)['state'], 'acquisition_unconfirmed')
        with self.assertRaises(FoundryError):
            self.registry.recover(self.token, IDENTITY)
        with self.assertRaises(FoundryError):
            self.registry.acquire(self.token, IDENTITY)
        other = deepcopy(IDENTITY)
        with self.assertRaises(FoundryError):
            self.registry.acquire('c' * 32, other)
        self.assertEqual(opened, [123])

    def test_foreign_or_unverified_returned_handle_is_not_reused_or_closed(self):
        self.api.identity = lambda handle: (IDENTITY['executable'], 999)
        with self.assertRaises(FoundryError):
            self.registry.acquire(self.token, IDENTITY)
        self.assertEqual(self.registry.status(self.token)['state'], 'returned_unverified')
        with self.assertRaises(FoundryError):
            self.registry.recover(self.token, IDENTITY)
        self.assertFalse(any(row[0] == 'close' for row in self.api.calls))

    def test_close_then_raise_is_not_retried(self):
        lease = self.registry.acquire(self.token, IDENTITY)
        calls = []
        def close_then_throw(handle):
            calls.append(handle)
            raise OSError('fixture release acknowledgment lost')
        self.api.close = close_then_throw
        with self.assertRaises(OSError):
            lease.close()
        self.assertEqual(self.registry.status(self.token)['state'], 'release_unconfirmed')
        with self.assertRaises(FoundryError):
            lease.close()
        self.assertEqual(calls, ['owned-fake-handle'])

    def test_permission_error_stops_and_does_not_reopen(self):
        calls = []
        def denied(pid):
            calls.append(pid)
            raise PermissionError('fixture access denied')
        self.api.open = denied
        with self.assertRaises(PermissionError):
            self.registry.acquire(self.token, IDENTITY)
        self.assertEqual(self.registry.status(self.token)['state'], 'acquisition_unconfirmed')
        with self.assertRaises(FoundryError):
            self.registry.recover(self.token, IDENTITY)
        self.assertEqual(calls, [123])

    def test_mutated_executable_verifier_view_is_refused_before_open(self):
        def changed(view):
            view['pid'] = 999
            return True
        registry = witnesses.RetainedProcessRegistry(self.api, changed)
        with self.assertRaises(FoundryError):
            registry.acquire(self.token, IDENTITY)
        self.assertFalse(any(row[0] == 'open' for row in self.api.calls))

    def test_stalled_clock_and_immediate_waits_are_bounded(self):
        registry = witnesses.RetainedProcessRegistry(self.api, lambda identity: True, clock=lambda: 1.0)
        lease = registry.acquire(self.token, IDENTITY)
        with self.assertRaisesRegex(FoundryError, 'observation bound'):
            lease.wait_exit(1)
        self.assertEqual(sum(row[0] == 'wait' for row in self.api.calls), 257)

    def test_false_handle_does_not_become_a_witness(self):
        self.api.open = lambda pid: False
        with self.assertRaises(FoundryError):
            self.registry.acquire(self.token, IDENTITY)
        self.assertEqual(self.registry.status(self.token)['state'], 'acquisition_unconfirmed')
        with self.assertRaises(FoundryError):
            self.registry.recover(self.token, IDENTITY)

    def test_noop_close_is_only_a_provider_report_not_os_proof(self):
        lease = self.registry.acquire(self.token, IDENTITY)
        self.api.close = lambda handle: None  # injected no-op, not an OS close
        result = lease.close()
        self.assertEqual(result['outcome'], 'provider_reported_closed')
        self.assertFalse(result['os_release_verified'])
        self.assertNotIn('released', result)
        self.assertFalse(self.registry.status(self.token)['os_release_verified'])
        self.assertFalse(self.registry.status(self.token)['loaded_image_hash_verified'])
        with self.assertRaises(FoundryError):
            lease.close()

    def test_owned_fixture_timeout_kills_and_reaps_only_its_child(self):
        child = Mock()
        child.stdin.closed = False
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('owned-fixture', 10), 0]
        with self.assertRaisesRegex(AssertionError, 'required forced cleanup'):
            cleanup_owned_child(child)
        child.stdin.close.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)
        child.stderr.close.assert_called_once()


class OwnedChildTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows retained-handle fixture only')
    def test_owned_child_signals_original_handle_and_closes_once(self):
        # No discovery or enumeration. This exact child belongs to this test;
        # it blocks on its own pipe until the retained handle is established.
        child = subprocess.Popen([sys.executable, '-B', '-c', 'import sys; sys.stdin.buffer.read(1)'],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        lease = None
        close_attempted = False
        try:
            api = process_exit._WindowsApi()
            image, created = api.identity(int(child._handle))
            with open(image, 'rb') as source:
                checksum = hashlib.sha256(source.read()).hexdigest()
            identity = {'pid': child.pid, 'creation_filetime': created, 'executable': image,
                        'executable_sha256': checksum}
            def verify(value):
                with open(value['executable'], 'rb') as source:
                    return hashlib.sha256(source.read()).hexdigest() == value['executable_sha256']
            registry = witnesses.RetainedProcessRegistry(api, verify)
            lease = registry.acquire('f' * 32, identity)
            self.assertTrue(lease.is_running())
            recovered = registry.recover('f' * 32, identity)
            child.stdin.write(b'x')
            child.stdin.flush()
            observed = recovered.wait_exit(10)
            self.assertTrue(observed['terminated'])
            self.assertEqual(observed['creation_filetime'], created)
            self.assertEqual(child.wait(timeout=10), 0)
            close_attempted = True
            result = recovered.close()
            self.assertEqual(result['outcome'], 'provider_reported_closed')
            self.assertFalse(result['os_release_verified'])
            self.assertEqual(registry.status('f' * 32)['state'], 'provider_reported_closed')
            with self.assertRaises(FoundryError):
                recovered.close()
        finally:
            try:
                if lease is not None and not close_attempted:
                    # A single cleanup attempt for this owned fixture only. A lost
                    # close acknowledgment is never retried.
                    close_attempted = True
                    lease.close()
            finally:
                cleanup_owned_child(child)


if __name__ == '__main__':
    unittest.main(verbosity=2)
