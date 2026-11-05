"""Fake-only persistent-owner and cancellation state-machine checks."""
import ctypes
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

if not os.path.isabs(__file__): raise ImportError('Absolute lexical path required.')
src = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src'))
for name, module in tuple(sys.modules.items()):
    if name == 'sims4foundry_cli' or name.startswith('sims4foundry_cli.'):
        parts = name.split('.')
        expected = os.path.join(src, *parts, '__init__.py') if len(parts) == 1 else os.path.join(src, *parts) + '.py'
        actual = getattr(module, '__file__', None)
        if type(actual) is not str or os.path.normcase(os.path.normpath(actual)) != os.path.normcase(os.path.normpath(expected)):
            raise ImportError('Cached Foundry module from another lexical checkout.')
sys.path.insert(0, src)
from sims4foundry_cli import fixture_interactive as subject, fixture_named_pipe as pipe_module
from sims4foundry_cli.errors import FoundryError

SESSION, OWNER, SECRET = 'a' * 32, 'b' * 32, b's' * 32


class Checks(unittest.TestCase):
    def setUp(self):
        if not hasattr(ctypes,'get_last_error'):
            state = {'error':0}
            for item in (patch.object(ctypes,'set_last_error',side_effect=lambda value:state.update(error=value),create=True),
                         patch.object(ctypes,'get_last_error',side_effect=lambda:state['error'],create=True)):
                item.start()
                self.addCleanup(item.stop)

    def test_only_confirmed_preconnect_timeout_rearms(self):
        class Pipe:
            _uncertain = []
            def _operation(self, *args): raise FoundryError('fixture_pipe_timeout', 'canceled')
        endpoint = object.__new__(pipe_module.FixturePipe)
        endpoint._pipe, endpoint._busy, endpoint._failed, endpoint._count = Pipe(), False, False, 0
        self.assertEqual(endpoint.serve_once(), {'fixture_idle': True})
        self.assertEqual(endpoint.serve_once(), {'fixture_idle': True})
        self.assertFalse(endpoint._failed)

    def test_timeout_after_connect_fences_without_rearm(self):
        class Pipe:
            _uncertain = []
            def _operation(self, *args): return b''
            def write(self, *args): raise FoundryError('fixture_pipe_timeout', 'after connect')
        endpoint = object.__new__(pipe_module.FixturePipe)
        endpoint._pipe, endpoint._busy, endpoint._failed, endpoint._count = Pipe(), False, False, 0
        with self.assertRaises(FoundryError): endpoint.serve_once()
        self.assertTrue(endpoint._failed)
        with self.assertRaises(FoundryError): endpoint.serve_once()

    def test_success_racing_cancel_is_unresolved_not_idle(self):
        class Api:
            def event(self, *args): return 456
            def connect(self, *args): ctypes.set_last_error(997); return False
            def wait(self, *args):
                self.count = getattr(self, 'count', 0) + 1
                return 258 if self.count == 1 else 0
            def cancel(self, *args): return True
            def complete(self, *args): return True
            def close_once(self, handle): pass
        pipe = pipe_module._Pipe(123, Api())
        with self.assertRaises(FoundryError) as caught:
            pipe._operation('connect', None, pipe_module.time.monotonic() + 1)
        self.assertEqual(caught.exception.code, 'fixture_pipe_unresolved')

    def test_cancel_not_found_and_successful_completion_is_unresolved(self):
        class Api:
            def event(self, *args): return 456
            def connect(self, *args): ctypes.set_last_error(997); return False
            def wait(self, *args):
                self.count = getattr(self, 'count', 0) + 1
                return 258 if self.count == 1 else 0
            def cancel(self, *args): ctypes.set_last_error(1168); return False
            def complete(self, *args): return True
            def close_once(self, handle): pass
        pipe = pipe_module._Pipe(123, Api())
        with self.assertRaises(FoundryError) as caught:
            pipe._operation('connect', None, pipe_module.time.monotonic() + 1)
        self.assertEqual(caught.exception.code, 'fixture_pipe_unresolved')

    def test_persistent_owner_rearms_idle_and_keeps_same_broker(self):
        class Control:
            value = None
            stopped = False
            def done(self): return self.stopped
            def wait(self, seconds): pass
        control = Control()
        broker = type('FakeBroker', (), {'session_id': SESSION, 'owner_id': OWNER})()
        used = []
        class Pipe:
            name = 'synthetic-endpoint'
            def __init__(self, owner, secret): used.append(owner)
            def serve_once(self):
                used.append('serve')
                if used.count('serve') == 1: return {'fixture_idle': True}
                control.value, control.stopped = b'Q', True
                return {'outcome': 'observed'}
            def close(self): used.append('closed')
        with patch.object(subject.base, '_fixture_owner', return_value=broker):
            result = subject.serve('fake', 'fake-session', input_stream=io.BytesIO(SECRET),
                                   output_stream=io.StringIO(), pipe_factory=Pipe,
                                   control_factory=lambda _: control)
        self.assertIs(used[0], broker)
        self.assertEqual(used.count('serve'), 2)
        self.assertEqual(used[-1], 'closed')
        self.assertTrue(result['control_was_explicit_abandon'])
        self.assertEqual(result['outcome'], 'unresolved')

    def test_transport_uncertainty_fences_until_abandon(self):
        class Control:
            value = None
            stopped = False
            def done(self): return self.stopped
            def wait(self, seconds): self.value, self.stopped = b'Q', True
        control = Control()
        broker = type('FakeBroker', (), {'session_id': SESSION, 'owner_id': OWNER})()
        calls = []
        class Pipe:
            name = 'synthetic-endpoint'
            def __init__(self, *args): pass
            def serve_once(self): calls.append('serve'); raise FoundryError('fixture_pipe_unresolved', 'unknown')
            def close(self): calls.append('close')
        with patch.object(subject.base, '_fixture_owner', return_value=broker):
            result = subject.serve('fake', 'fake-session', input_stream=io.BytesIO(SECRET), output_stream=io.StringIO(),
                                   pipe_factory=Pipe, control_factory=lambda _: control)
        self.assertEqual(calls, ['serve', 'close'])
        self.assertEqual(result['reason'], 'fixture_transport_unresolved')

    def test_permission_denial_fences_once_and_retains_until_abandon(self):
        class Control:
            value = None
            stopped = False
            def done(self): return self.stopped
            def wait(self, seconds): self.value, self.stopped = b'Q', True
        control = Control()
        broker = type('FakeBroker', (), {'session_id': SESSION, 'owner_id': OWNER})()
        calls = []
        class Pipe:
            name = 'synthetic-endpoint'
            def __init__(self, *args): pass
            def serve_once(self): calls.append('serve'); raise PermissionError('synthetic denial')
            def close(self): calls.append('close')
        with patch.object(subject.base, '_fixture_owner', return_value=broker):
            result = subject.serve('fake', 'fake-session', input_stream=io.BytesIO(SECRET), output_stream=io.StringIO(),
                                   pipe_factory=Pipe, control_factory=lambda _: control)
        self.assertEqual(calls, ['serve', 'close'])
        self.assertEqual(result['reason'], 'fixture_transport_permission_denied')
        self.assertTrue(result['control_was_explicit_abandon'])

    def test_owner_interruption_reports_possible_loss_without_retry(self):
        class Control:
            value = None
            def done(self): return False
            def wait(self, seconds): pass
        broker = type('FakeBroker', (), {'session_id': SESSION, 'owner_id': OWNER})()
        calls = []
        class Pipe:
            name = 'synthetic-endpoint'
            def __init__(self, *args): pass
            def serve_once(self): calls.append('serve'); raise KeyboardInterrupt()
        output = io.StringIO()
        with patch.object(subject.base, '_fixture_owner', return_value=broker):
            with self.assertRaises(KeyboardInterrupt):
                subject.serve('fake', 'fake-session', input_stream=io.BytesIO(SECRET), output_stream=output,
                              pipe_factory=Pipe, control_factory=lambda _: Control())
        self.assertEqual(calls, ['serve'])
        self.assertIn('"owner_witness_may_be_lost":true', output.getvalue())

    def test_interactive_supervisor_retains_one_owner_for_two_clients(self):
        created, writes = [], []
        class Input:
            def write(self, value): writes.append(value); return len(value)
            def flush(self): pass
            def close(self): pass
        class Child:
            def __init__(self, args, **kw):
                self.args = args; self.stdin = Input(); self.stderr = io.BytesIO(); created.append(self)
                if '--request' in args:
                    self.returncode = 0; request = json.loads(args[args.index('--request') + 1])
                    result = {'ok': True, 'outcome': 'fixture_response_received', 'semantic_success_established': False,
                        'game_qualified': False, 'live_dispatch_available': False, 'response': {
                        'session_id': request['session_id'], 'owner_id': request['owner_id'],
                        'operation_id': request['operation_id'], 'outcome': 'observed', 'registry_pending': False,
                        'receipt_replayed': False}}
                else:
                    self.returncode = 3; result = {'outcome': 'unresolved'}
                self.stdout = io.BytesIO(json.dumps(result).encode())
            def wait(self, timeout=None): return self.returncode
        banner = {'fixture_endpoint': r'\\.\pipe\foundry-fixture-fake', 'session_id': SESSION, 'owner_id': OWNER,
                  'game_qualified': False, 'live_dispatch_available': False}
        commands = [{'operation_id': 'c' * 32, 'op': 'status', 'payload': {}},
                    {'operation_id': 'd' * 32, 'op': 'status', 'payload': {}}, {'command': 'abandon'}]
        incoming = io.BytesIO(b''.join(json.dumps(row).encode() + b'\n' for row in commands))
        output = io.StringIO()
        with patch.object(subject.secrets, 'token_bytes', return_value=SECRET):
            result = subject.launch('fake', 'fake-session', input_stream=incoming, output_stream=output,
                                    popen=Child, banner_reader=lambda _: banner)
        self.assertEqual(len(created), 3)
        self.assertEqual(writes, [SECRET, SECRET, SECRET, b'Q'])
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertTrue(result['original_fixture_witness_lost'])
        self.assertNotIn(SECRET.decode(), output.getvalue())

    def test_unknown_and_pending_receipts_fence_without_second_client(self):
        for op, observed, replayed in (('status', 'unresolved', False), ('status', 'unexpected', False),
                                       ('fixture_click', 'awaiting_readback', False), ('status', 'observed', True)):
            with self.subTest(op=op, observed=observed, replayed=replayed):
                created = []
                class Input:
                    def write(self, value): return len(value)
                    def flush(self): pass
                    def close(self): pass
                class Child:
                    def __init__(self, args, **kw):
                        self.stdin = Input(); self.stderr = io.BytesIO(); created.append(self)
                        if '--request' in args:
                            self.returncode = 0; request = json.loads(args[args.index('--request') + 1])
                            row = {'session_id': request['session_id'], 'owner_id': request['owner_id'],
                                   'operation_id': request['operation_id'], 'outcome': observed,
                                   'registry_pending': False, 'receipt_replayed': replayed}
                            result = {'ok': True, 'outcome': 'fixture_response_received',
                                      'semantic_success_established': False, 'game_qualified': False,
                                      'live_dispatch_available': False, 'response': row}
                        else: self.returncode = 3; result = {'outcome': 'unresolved'}
                        self.stdout = io.BytesIO(json.dumps(result).encode())
                    def wait(self, timeout=None): return self.returncode
                banner = {'fixture_endpoint': r'\\.\pipe\foundry-fixture-fake', 'session_id': SESSION,
                          'owner_id': OWNER, 'game_qualified': False, 'live_dispatch_available': False}
                payload = {'x': 1, 'y': 2, 'expectation': {'fixture': 'next'}} if op == 'fixture_click' else {}
                commands = [{'operation_id': 'c' * 32, 'op': op, 'payload': payload},
                            {'operation_id': 'd' * 32, 'op': 'status', 'payload': {}}, {'command': 'abandon'}]
                incoming = io.BytesIO(b''.join(json.dumps(row).encode() + b'\n' for row in commands))
                output = io.StringIO()
                with patch.object(subject.secrets, 'token_bytes', return_value=SECRET):
                    result = subject.launch('fake', 'fake-session', input_stream=incoming, output_stream=output,
                                            popen=Child, banner_reader=lambda _: banner)
                self.assertEqual(len(created), 2)
                self.assertIn('fixture_input_fenced', output.getvalue())
                self.assertIn('"supervisor_fenced":true', output.getvalue())
                self.assertEqual(result['outcome'], 'unresolved')


if __name__ == '__main__': unittest.main()
