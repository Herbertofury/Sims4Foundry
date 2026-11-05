"""Fake-only launcher/handshake tests; no pipe, child or game is opened."""
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

if not os.path.isabs(__file__): raise ImportError('Absolute lexical path required.')
src = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src'))
for name, module in tuple(sys.modules.items()):
    if name == 'sims4foundry_cli' or name.startswith('sims4foundry_cli.'):
        pieces = name.split('.')
        expected = (os.path.join(src, *pieces, '__init__.py') if len(pieces) == 1
                    else os.path.join(src, *pieces) + '.py')
        path = getattr(module, '__file__', None)
        if type(path) is not str or os.path.normcase(os.path.normpath(path)) != os.path.normcase(os.path.normpath(expected)):
            raise ImportError('Cached Foundry module from a different lexical checkout.')
sys.path.insert(0, src)
from sims4foundry_cli import cli, fixture_launcher as subject, independent_owner, independent_session
from sims4foundry_cli.errors import FoundryError

SESSION, OWNER, OP = 'a' * 32, 'b' * 32, 'c' * 32
SECRET = b's' * 32


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.owner = object.__new__(independent_owner.FixtureOwner)
        self.broker = subject.FixtureBroker(self.owner, Path('synthetic'), SESSION, OWNER)
        self.request = {'schema': 1, 'session_id': SESSION, 'owner_id': OWNER, 'operation_id': OP,
                        'op': 'fixture_click', 'payload': {'x': 1, 'y': 2, 'expectation': {'x': 1}}}
        def write(path, row):
            key = str(path)
            if key in self.store: raise FileExistsError(key)
            self.store[key] = row
        def read(path, kind, fields):
            if str(path) not in self.store: raise FileNotFoundError(str(path))
            row = self.store[str(path)]
            if row['kind'] != kind: raise FoundryError('invalid', 'bad fake record')
            return row
        self.writer = patch.object(subject, 'new_json', side_effect=write)
        self.reader = patch.object(independent_session, '_checked', side_effect=read)
        self.writer.start(); self.reader.start()
        self.addCleanup(self.writer.stop); self.addCleanup(self.reader.stop)

    def test_receipt_replayed_without_second_dispatch_and_id_conflict_refused(self):
        with patch.object(independent_owner.FixtureOwner, 'handle', return_value={'outcome': 'awaiting_readback'}) as dispatch:
            one = self.broker.handle(self.request)
            repeated = self.broker.handle(self.request)
            self.assertEqual(repeated['outcome'], one['outcome'])
            self.assertFalse(one['receipt_replayed'])
            self.assertTrue(repeated['receipt_replayed'])
            changed = dict(self.request, payload={'x': 9})
            with self.assertRaises(FoundryError): self.broker.handle(changed)
            dispatch.assert_called_once()
        self.assertEqual(len(self.store), 2)

    def test_mutation_then_error_leaves_intent_and_no_replay(self):
        with patch.object(independent_owner.FixtureOwner, 'handle', side_effect=RuntimeError('lost reply')) as dispatch:
            with self.assertRaises(RuntimeError): self.broker.handle(self.request)
            with self.assertRaises(FoundryError): self.broker.handle(self.request)
            replacement_view = subject.FixtureBroker(self.owner, Path('synthetic'), SESSION, OWNER)
            with self.assertRaisesRegex(FoundryError, 'never dispatch again'):
                replacement_view.handle(self.request)
            dispatch.assert_called_once()
        self.assertEqual(len(self.store), 1)

    def test_lost_intent_ack_blocks_dispatch_and_never_replays(self):
        def lost(path, row):
            self.store[str(path)] = row
            raise RuntimeError('publication outcome unknown')
        with patch.object(subject, 'new_json', side_effect=lost), \
             patch.object(independent_owner.FixtureOwner, 'handle') as dispatch:
            with self.assertRaises(RuntimeError): self.broker.handle(self.request)
            with self.assertRaises(FoundryError): self.broker.handle(self.request)
            dispatch.assert_not_called()

    def test_wrong_owner_rejected_without_record_or_dispatch(self):
        with patch.object(independent_owner.FixtureOwner, 'handle') as dispatch:
            with self.assertRaises(FoundryError): self.broker.handle(dict(self.request, owner_id='d' * 32))
            dispatch.assert_not_called()
        self.assertEqual(self.store, {})

    def test_interactive_broker_rejects_stop_before_record_or_dispatch(self):
        broker = subject.FixtureBroker(self.owner, Path('synthetic'), SESSION, OWNER, allow_stop=False)
        stop = dict(self.request, op='stop', payload={'abandon_original_witness': True})
        with patch.object(independent_owner.FixtureOwner, 'handle') as dispatch:
            with self.assertRaises(FoundryError): broker.handle(stop)
            dispatch.assert_not_called()
        self.assertEqual(self.store, {})

    def test_pending_unresolved_or_unknown_result_fences_new_operations(self):
        for op, outcome in (('status', 'unresolved'), ('status', 'unexpected'),
                            ('fixture_click', 'awaiting_readback')):
            with self.subTest(op=op, outcome=outcome):
                self.store.clear()
                broker = subject.FixtureBroker(self.owner, Path('synthetic'), SESSION, OWNER, allow_stop=False)
                request = dict(self.request, op=op)
                later = dict(self.request, operation_id='d' * 32, op='status', payload={})
                with patch.object(independent_owner.FixtureOwner, 'handle', return_value={'outcome': outcome}) as dispatch:
                    self.assertEqual(broker.handle(request)['outcome'], outcome)
                    self.assertEqual(broker.handle(request)['outcome'], outcome)
                    with self.assertRaises(FoundryError): broker.handle(later)
                    dispatch.assert_called_once()
                self.assertEqual(len(self.store), 2)

    def test_observed_status_requires_explicit_nonpending_flag(self):
        self.assertFalse(subject._status_allows_continuation('input_status', {'outcome': 'observed', 'registry_pending': False}))
        for result in ({'outcome': 'observed', 'registry_pending': True}, {'outcome': 'observed'},
                       {'outcome': 'observed', 'registry_pending': 0}):
            with self.subTest(result=result):
                self.store.clear()
                broker = subject.FixtureBroker(self.owner, Path('synthetic'), SESSION, OWNER, allow_stop=False)
                first = dict(self.request, op='status', payload={})
                later = dict(first, operation_id='d' * 32)
                with patch.object(independent_owner.FixtureOwner, 'handle', return_value=result) as dispatch:
                    broker.handle(first)
                    with self.assertRaises(FoundryError): broker.handle(later)
                    dispatch.assert_called_once()

    def test_reused_status_receipt_is_point_in_time_and_fences(self):
        broker = subject.FixtureBroker(self.owner, Path('synthetic'), SESSION, OWNER, allow_stop=False)
        first = dict(self.request, op='status', payload={})
        later = dict(first, operation_id='d' * 32)
        with patch.object(independent_owner.FixtureOwner, 'handle',
                          return_value={'outcome': 'observed', 'registry_pending': False}) as dispatch:
            self.assertFalse(broker.handle(first)['receipt_replayed'])
            self.assertTrue(broker.handle(first)['receipt_replayed'])
            with self.assertRaises(FoundryError): broker.handle(later)
            dispatch.assert_called_once()

    def test_used_registry_refuses_first_fixture_owner_before_reservation(self):
        intent = {'identity': subject.FIXTURE_IDENTITY}
        for events in ([], [{'kind': 'session'}, {'kind': 'attempt'}], [{'kind': 'session'}, {'kind': 'resolved'}]):
            with self.subTest(events=events), \
                 patch.object(independent_session, '_open', return_value=(intent, None, None, {'events': events})), \
                 patch.object(subject, 'external_path') as path:
                with self.assertRaises(FoundryError): subject._fixture_owner('a', 's')
                path.assert_not_called()

    def test_foreground_serve_uses_same_broker_and_never_prints_secret(self):
        seen = []
        class Pipe:
            name = 'synthetic-endpoint'
            def __init__(self, broker, secret):
                self.broker = broker; seen.append(secret)
            def serve_once(self): seen.append(self.broker.handle(self_request))
            def close(self): seen.append('closed')
        self_request = self.request
        output = io.StringIO()
        with patch.object(subject, '_fixture_owner', return_value=self.broker), \
             patch.object(independent_owner.FixtureOwner, 'handle', return_value={'outcome': 'awaiting_readback'}) as dispatch:
            result = subject.serve('fake-authority', 'fake-session', input_stream=io.BytesIO(SECRET),
                                   output_stream=output, count=2, pipe_factory=Pipe)
        self.assertEqual(seen[0], SECRET)
        self.assertNotIn(SECRET.decode(), output.getvalue())
        self.assertEqual(seen[-1], 'closed')
        dispatch.assert_called_once()
        self.assertFalse(result['game_qualified'])
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertTrue(result['original_witness_will_be_lost_on_process_exit'])

    def test_client_does_not_retry_lost_response(self):
        calls = []
        def lost(*args):
            calls.append(True)
            raise RuntimeError('response lost')
        with self.assertRaises(RuntimeError):
            subject.client('synthetic-endpoint', self.request, input_stream=io.BytesIO(SECRET),
                           sender=lost)
        self.assertEqual(calls, [True])

    def test_nonfixture_session_refused_before_owner_reservation(self):
        intent = {'identity': dict(subject.FIXTURE_IDENTITY, executable='C:\\Elsewhere\\TS4_x64.exe')}
        with patch.object(independent_session, '_open', return_value=(intent, None, None, None)), \
             patch.object(subject, 'external_path') as path:
            with self.assertRaises(FoundryError): subject._fixture_owner('a', 's')
            path.assert_not_called()

    def test_pipe_construction_failure_retains_original_fixture_broker(self):
        subject._UNRESOLVED_OWNERS.clear()
        with patch.object(subject, '_fixture_owner', return_value=self.broker):
            with self.assertRaises(RuntimeError):
                subject.serve('fake', 'fake-session', input_stream=io.BytesIO(SECRET),
                              output_stream=io.StringIO(),
                              pipe_factory=lambda *_: (_ for _ in ()).throw(RuntimeError('construction interrupted')))
        self.assertIn(self.broker, subject._UNRESOLVED_OWNERS)
        subject._UNRESOLVED_OWNERS.clear()

    def test_bounded_reader_refuses_oversized_fake_output(self):
        reader = subject._BoundedReader(io.BytesIO(b'x' * (subject.MAX_FRAME + 1)))
        with self.assertRaises(FoundryError): reader.finish()

    def test_fixture_initialization_uses_only_builtin_identity(self):
        with patch.object(independent_session, 'initialize_session', return_value={'ok': True}) as initialize:
            args = cli.parser().parse_args(['independent-session', 'fixture-initialize',
                                           '--authority', 'fake', '--session', 'fake-session'])
            self.assertTrue(cli.run(args)['ok'])
            initialize.assert_called_once_with('fake', 'fake-session', subject.FIXTURE_IDENTITY)

    def test_receipt_inspection_never_contacts_owner_or_replays(self):
        root = Path('fake-session') / 'fixture-owner'
        self.broker.directory = root / 'operations'
        owner_row = independent_session._record('foundry_fixture_owner_intent', session_id=SESSION,
                                                  owner_id=OWNER, lifetime_sha256='d' * 64)
        self.store[str(root / 'owner-intent.json')] = owner_row
        intent = {'session_id': SESSION, 'identity': subject.FIXTURE_IDENTITY, 'lifetime_sha256': 'd' * 64}
        with patch.object(independent_session, '_open', return_value=(intent, None, None, None)), \
             patch.object(subject, 'external_path', return_value=Path('fake-session')), \
             patch.object(independent_owner.FixtureOwner, 'handle', return_value={'outcome': 'awaiting_readback'}) as dispatch:
            missing = subject.inspect_receipt('fake', 'fake-session', OWNER, OP)
            self.assertEqual(missing['outcome'], 'unresolved')
            self.broker.handle(self.request)
            result = subject.inspect_receipt('fake', 'fake-session', OWNER, OP)
            self.assertEqual(result['outcome'], 'original_fixture_receipt_observed')
            self.assertFalse(result['replayed'])
            self.assertFalse(result['owner_liveness_proved'])
            dispatch.assert_called_once()

    def test_supervisor_uses_private_secret_and_bound_distinct_clients(self):
        created, supplied = [], []
        class Input:
            def write(self, value): supplied.append(value); return len(value)
            def close(self): pass
        class Child:
            def __init__(self, args, **kw):
                self.args = args; self.stdin = Input(); self.stderr = io.BytesIO(); created.append(self)
                if '--request' in args:
                    self.returncode = 0
                    req = json.loads(args[args.index('--request') + 1])
                    result = {'outcome': 'fixture_response_received', 'response': {
                        'session_id': req['session_id'], 'owner_id': req['owner_id'], 'operation_id': req['operation_id']}}
                else:
                    self.returncode = 3
                    result = {'outcome': 'unresolved', 'reason': 'bounded_fixture_owner_ending'}
                self.stdout = io.BytesIO(json.dumps(result).encode())
            def wait(self, timeout=None): return self.returncode
        banner = {'fixture_endpoint': r'\\.\pipe\foundry-fixture-fake', 'session_id': SESSION, 'owner_id': OWNER,
                  'game_qualified': False, 'live_dispatch_available': False}
        requests = [{'operation_id': OP, 'op': 'status', 'payload': {}},
                    {'operation_id': 'd' * 32, 'op': 'status', 'payload': {}}]
        with patch.object(subject.secrets, 'token_bytes', return_value=SECRET):
            result = subject.launch('fake-authority', 'fake-session', requests,
                                    popen=Child, banner_reader=lambda _: banner)
        self.assertEqual(len(created), 3)
        self.assertEqual(supplied, [SECRET, SECRET, SECRET])
        self.assertTrue(all(SECRET.decode() not in ' '.join(child.args) for child in created))
        self.assertEqual(len(result['responses']), 2)
        self.assertFalse(result['game_qualified'])
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertTrue(result['original_fixture_witness_lost'])

    def test_supervisor_lost_client_response_is_unresolved_without_retry(self):
        made = []
        subject._UNRESOLVED_CHILDREN.clear()
        class Input:
            def write(self, value): return len(value)
            def close(self): pass
        class Child:
            def __init__(self, args, **kw):
                self.args = args; self.stdin = Input(); self.stdout = io.BytesIO(); self.stderr = io.BytesIO(); made.append(self)
            def wait(self, **kwargs): raise TimeoutError('fake lost response')
        banner = {'fixture_endpoint': r'\\.\pipe\foundry-fixture-fake', 'session_id': SESSION, 'owner_id': OWNER,
                  'game_qualified': False, 'live_dispatch_available': False}
        requests = [{'operation_id': OP, 'op': 'status', 'payload': {}}]
        with self.assertRaises(FoundryError) as caught:
            subject.launch('fake', 'fake-session', requests, popen=Child, banner_reader=lambda _: banner)
        self.assertEqual(caught.exception.details['operation_id'], OP)
        self.assertEqual(caught.exception.details['outcome'], 'unresolved')
        self.assertEqual(len(made), 2)
        self.assertEqual([item[0] for item in subject._UNRESOLVED_CHILDREN], ['client', 'owner'])
        subject._UNRESOLVED_CHILDREN.clear()

    def test_cli_routes_fixture_and_keeps_live_input_denied(self):
        with patch.object(subject, 'serve', return_value={'ok': True, 'game_qualified': False}) as serve:
            args = cli.parser().parse_args(['fixture-owner', 'serve', '--authority', 'fake', '--session', 'fake-session'])
            self.assertFalse(cli.run(args)['game_qualified'])
            serve.assert_called_once()
        args = cli.parser().parse_args(['independent-session', 'input-once', '--authority', 'fake'])
        with self.assertRaises(FoundryError): cli.run(args)


if __name__ == '__main__': unittest.main()
