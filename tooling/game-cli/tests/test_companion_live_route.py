"""Actual handler/owner routes using fabricated policy and no socket creation.

Host-only assertions run on supported current Python; the final fixture exchanges
real canonical frames through stdin/stdout with the isolated Python 3.7 binary.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from time import monotonic
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'companion-src'
if str(SOURCE) not in sys.path: sys.path.insert(0, str(SOURCE))
from foundry_companion import queue as q, wire, live_wire
from foundry_companion.transport import LoopbackTransport
from test_companion_live_executor import request, executor, diagnostic, redirect_legacy
import test_companion_live_observation as fixtures

HOST = sys.version_info >= (3, 11)
if HOST:
    from sims4foundry_cli import companion_live_protocol as host
    from sims4foundry_cli import companion_protocol as host_wire
    from sims4foundry_cli import companion_transport as old_host
    from sims4foundry_cli import companion_journal as ledger
    from sims4foundry_cli.errors import FoundryError


def query(req, schema=3):
    return dict(schema=schema, kind='receipt_read', request_id=req.request_id,
                session_id=req.session_id, process_epoch=wire.epoch_wire(req.process_epoch),
                request_sha256=live_wire.request_sha256(req))


def legacy(number=9, mutation=False):
    return dict(schema=2, kind='submit', ttl_seconds=5.0,
                expected_context=dict(zone_id='10', household_id='20', save_slot=4, save_guid='99') if mutation else None,
                request=dict(schema=1, kind='request', request_id='{:032x}'.format(number),
                             session_id='2' * 32, process_epoch=dict(pid=123, creation_filetime=456),
                             operation='live.set_speed' if mutation else 'observe',
                             arguments=dict(speed=1) if mutation else {}))


class Policy:
    """Synthetic test-only admission. Never a production security provider."""
    def __init__(self):
        self.submits, self.dispatches = [], []
        self.submit_hook = self.dispatch_hook = None
    def authorize_submit(self, submission, binding, deadline):
        self.submits.append((submission, binding))
        if self.submit_hook: return self.submit_hook(submission, binding)
        return True
    def authorize_dispatch(self, submission, binding):
        self.dispatches.append((submission, binding))
        if self.dispatch_hook: return self.dispatch_hook(submission, binding)
        return True


class LiveRouteTests(unittest.TestCase):
    def setUp(self):
        self.no_sockets = patch('socket.socket', side_effect=AssertionError('socket creation forbidden'))
        self.no_sockets.start()
        self.addCleanup(self.no_sockets.stop)
        self.policy = Policy()
        self.server = LoopbackTransport('2' * 32, q.ProcessEpoch(123, 456), policy=self.policy)
        self.harness = fixtures.Harness()
        self.execute = executor(self.harness).execute

    def handle(self, value):
        # Both real codec boundaries are used, rather than passing provider objects.
        decoded = wire.decode_payload(wire.encode_frame(value)[4:])
        response, admitted = self.server._handle(decoded, monotonic() + 2)
        return wire.decode_payload(wire.encode_frame(response)[4:]), admitted

    def test_actual_handle_owner_tick_and_receipt_read_preserve_complete_dto(self):
        req = request()
        accepted, admitted = self.handle(live_wire.request_wire(req))
        self.assertEqual(('accepted', True), (accepted['state'], admitted))
        self.assertEqual([], self.harness.calls)
        done = self.server.owner_tick(self.execute)
        self.assertEqual('unverified', done.state)
        response, admitted = self.handle(query(req))
        self.assertEqual((3, 'adapter_returned', True), (response['schema'], response['state'], admitted))
        self.assertEqual('observed', response['adapter_result']['read_status'])
        self.assertEqual('30', response['adapter_result']['observation']['actor_sim_id'])
        self.assertIsNone(self.server.address)
        self.assertEqual(1, len(self.policy.submits))
        self.assertEqual(1, len(self.policy.dispatches))
        self.assertIsNot(self.policy.submits[0][0], self.policy.dispatches[0][0])

    def test_default_submit_and_dispatch_denial_do_not_query_native_state(self):
        self.server = LoopbackTransport('2' * 32, q.ProcessEpoch(123, 456))
        refused, admitted = self.handle(live_wire.request_wire(request()))
        self.assertEqual(('refused', 'host_admission_denied', False),
                         (refused['state'], refused['reason'], admitted))
        self.assertIsNone(self.server.owner_tick(self.execute))
        self.server = LoopbackTransport('2' * 32, q.ProcessEpoch(123, 456), policy=self.policy)
        self.policy.dispatch_hook = lambda submission, binding: False
        self.handle(live_wire.request_wire(request()))
        self.assertEqual('refused', self.server.owner_tick(self.execute).state)
        self.assertEqual([], self.harness.calls)

    def test_every_policy_submission_and_binding_change_refuses_before_start(self):
        changes = [lambda sub, binding: object.__setattr__(sub.request.arguments, 'actor_sim_id', '31'),
                   lambda sub, binding: object.__setattr__(sub.request.expected_context, 'client_id', '6'),
                   lambda sub, binding: object.__setattr__(sub.request.process_epoch, 'creation_filetime', 457),
                   lambda sub, binding: object.__setattr__(sub.request, 'ttl_seconds', 5),
                   lambda sub, binding: object.__setattr__(sub, 'canonical_payload', b'{}'),
                   lambda sub, binding: object.__setattr__(sub, 'request_sha256', 'f' * 64),
                   lambda sub, binding: object.__setattr__(binding, 'session_id', '3' * 32),
                   lambda sub, binding: object.__setattr__(binding.process_epoch, 'pid', 124),
                   lambda sub, binding: object.__setattr__(binding.process_epoch, 'creation_filetime', 457)]
        for stage in ('submit', 'dispatch'):
            for change in changes:
                self.policy = Policy()
                self.server = LoopbackTransport('2' * 32, q.ProcessEpoch(123, 456), policy=self.policy)
                def mutate(sub, binding): change(sub, binding); return True
                setattr(self.policy, stage + '_hook', mutate)
                response, _ = self.handle(live_wire.request_wire(request()))
                done = self.server.owner_tick(self.execute)
                with self.subTest(stage=stage, change=change):
                    if stage == 'submit': self.assertEqual('refused', response['state'])
                    else: self.assertEqual('refused', done.state)
                    self.assertEqual([], self.harness.calls)
                    self.assertEqual(123, self.server.binding.process_epoch.pid)
                    self.assertEqual(456, self.server.binding.process_epoch.creation_filetime)

    def test_retained_views_cannot_redirect_later_parse_or_dispatch(self):
        req = request()
        self.handle(live_wire.request_wire(req))
        prior, binding = self.policy.submits[0]
        object.__setattr__(prior.request.arguments, 'actor_sim_id', '31')
        object.__setattr__(prior.request.expected_context, 'zone_id', '11')
        object.__setattr__(prior, 'canonical_payload', b'{}')
        object.__setattr__(binding.process_epoch, 'pid', 124)
        self.assertEqual('unverified', self.server.owner_tick(self.execute).state)
        view, binding = self.policy.dispatches[0]
        object.__setattr__(view.request.arguments, 'actor_sim_id', '32')
        object.__setattr__(binding.process_epoch, 'creation_filetime', 457)
        response, _ = self.handle(query(req))
        self.assertEqual('30', response['adapter_result']['observation']['actor_sim_id'])
        self.assertEqual(123, response['process_epoch']['pid'])
        self.assertEqual(456, response['process_epoch']['creation_filetime'])
        self.assertEqual('accepted', self.handle(live_wire.request_wire(request(2)))[0]['state'])

    def test_changed_raw_input_or_record_submission_alias_does_not_change_baseline(self):
        raw = live_wire.request_wire(request())
        def change(submission, binding): raw['arguments']['actor_sim_id'] = '31'; return True
        self.policy.submit_hook = change
        self.server._handle(raw, monotonic() + 2)
        retained = self.server._records[request().request_id].submission
        object.__setattr__(retained.request.arguments, 'actor_sim_id', '32')
        object.__setattr__(retained, 'canonical_payload', b'{}')
        self.assertEqual('unverified', self.server.owner_tick(self.execute).state)
        self.assertEqual('30', self.handle(query(request()))[0]['adapter_result']['observation']['actor_sim_id'])

    def test_duplicates_never_rerun_or_renew_before_started_or_after_terminal(self):
        req = request()
        accepted, _ = self.handle(live_wire.request_wire(req))
        first = self.server._queue.status(req.request_id)
        self.assertEqual(accepted, self.handle(live_wire.request_wire(req))[0])
        self.assertEqual(first.expires_at, self.server._queue.status(req.request_id).expires_at)
        def execute(view):
            self.assertEqual('dispatched', self.handle(live_wire.request_wire(req))[0]['state'])
            self.assertEqual('dispatched', self.handle(query(req))[0]['state'])
            return diagnostic()
        self.server.owner_tick(execute)
        for _ in range(3):
            response, _ = self.handle(live_wire.request_wire(req))
            self.assertEqual('unavailable', response['adapter_result']['read_status'])
            self.assertIsNone(self.server.owner_tick(lambda view: self.fail('replayed')))
        self.assertEqual(1, len(self.policy.submits))
        self.assertEqual(1, len(self.policy.dispatches))

    def test_cross_schema_queries_and_submits_conflict_and_keep_latch(self):
        self.handle(live_wire.request_wire(request()))
        for value in (query(request(), schema=2), legacy(number=1)):
            with self.assertRaises(wire.ProtocolError) as caught: self.handle(value)
            self.assertEqual('request_conflict', caught.exception.code)
        self.assertEqual('request_conflict', self.server.queue_status().mutation_blocked_reason)
        self.assertEqual('unverified', self.server.owner_tick(self.execute).state)
        self.assertEqual('request_conflict', self.server.queue_status().mutation_blocked_reason)
        self.assertEqual('refused', self.handle(legacy(mutation=True))[0]['state'])
        self.server = LoopbackTransport('2' * 32, q.ProcessEpoch(123, 456), policy=self.policy)
        old = legacy(number=1)
        self.handle(old)
        v3_query = query(request())
        v3_query['request_sha256'] = wire.parse_submit(old, '2' * 32, q.ProcessEpoch(123, 456)).request_sha256
        for value in (v3_query, live_wire.request_wire(request())):
            with self.assertRaises(wire.ProtocolError) as caught: self.handle(value)
            self.assertEqual('request_conflict', caught.exception.code)

    def test_changed_actor_context_and_ttl_kind_conflict_without_read(self):
        self.handle(live_wire.request_wire(request()))
        for changed in (request(arguments=q.LiveQueueArguments('31')),
                        request(expected_context=replace(request().expected_context, zone_generation=2)),
                        request(ttl_seconds=5)):
            with self.assertRaises(wire.ProtocolError) as caught: self.handle(live_wire.request_wire(changed))
            self.assertEqual('request_conflict', caught.exception.code)
        self.assertEqual([], self.harness.calls)
        self.assertEqual('request_conflict', self.server.queue_status().mutation_blocked_reason)

    def test_unknown_read_and_old_mutation_barriers_remain_distinct(self):
        self.handle(legacy(mutation=True))
        self.server.owner_tick(lambda req: {})
        self.assertEqual('unresolved_mutation', self.server.queue_status().mutation_blocked_reason)
        self.handle(live_wire.request_wire(request()))
        done = self.server.owner_tick(lambda req: {})
        self.assertEqual(('unknown', 'invalid_execution_result', None), (done.state, done.reason, done.result))
        response, _ = self.handle(query(request()))
        self.assertIsNone(response['adapter_result'])
        self.assertEqual('unresolved_mutation', self.server.queue_status().mutation_blocked_reason)
        self.assertEqual('unresolved_mutation', self.handle(legacy(number=8, mutation=True))[0]['reason'])

    def test_legacy_policy_submission_and_binding_aliases_are_checked_before_dispatch(self):
        changes = [lambda sub, binding: redirect_legacy(sub.request, sub.request.operation == 'observe'),
                   lambda sub, binding: object.__setattr__(sub.request, 'ttl_seconds', 5),
                   lambda sub, binding: object.__setattr__(sub.request.process_epoch, 'creation_filetime', 457),
                   lambda sub, binding: object.__setattr__(sub, 'canonical_payload', b'{}'),
                   lambda sub, binding: object.__setattr__(sub, 'request_sha256', 'f' * 64),
                   lambda sub, binding: object.__setattr__(binding.process_epoch, 'pid', 124)]
        for stage in ('submit', 'dispatch'):
            for mutation in (False, True):
                for change in changes:
                    self.policy = Policy()
                    self.server = LoopbackTransport('2' * 32, q.ProcessEpoch(123, 456), policy=self.policy)
                    def mutate(sub, binding): change(sub, binding); return True
                    setattr(self.policy, stage + '_hook', mutate)
                    response, _ = self.handle(legacy(mutation=mutation))
                    final = self.server.owner_tick(lambda req: self.fail('changed legacy policy view dispatched'))
                    if stage == 'submit': self.assertEqual('refused', response['state'])
                    else: self.assertEqual('refused', final.state)
                    self.assertEqual(123, self.server.binding.process_epoch.pid)

    def test_retained_legacy_policy_views_never_redirect_original_intent(self):
        raw = legacy()
        self.handle(raw)
        submitted, binding = self.policy.submits[0]
        redirect_legacy(submitted.request, True)
        object.__setattr__(submitted, 'canonical_payload', b'{}')
        object.__setattr__(binding.process_epoch, 'pid', 124)
        self.server._queue.block_mutations()
        def execute(req):
            dispatched, binding = self.policy.dispatches[0]
            self.assertIsNot(submitted, dispatched)
            self.assertIsNot(dispatched.request, req)
            redirect_legacy(dispatched.request, True)
            object.__setattr__(binding.process_epoch, 'creation_filetime', 457)
            self.assertEqual('observe', req.operation)
            return {'outcome': 'unverified'}
        final = self.server.owner_tick(execute)
        self.assertEqual('unverified', final.state)
        self.assertEqual('unresolved_mutation', self.server.queue_status().mutation_blocked_reason)
        response, _ = self.handle(raw)
        self.assertEqual('adapter_returned', response['state'])
        self.assertEqual(123, response['process_epoch']['pid'])
        self.assertEqual(456, response['process_epoch']['creation_filetime'])

    def test_public_binding_and_input_epoch_aliases_do_not_redirect_legacy_parsing(self):
        epoch = q.ProcessEpoch(123, 456)
        self.server = LoopbackTransport('2' * 32, epoch, policy=self.policy)
        object.__setattr__(epoch, 'pid', 124)
        view = self.server.binding
        object.__setattr__(view.process_epoch, 'creation_filetime', 457)
        self.assertEqual('accepted', self.handle(legacy())[0]['state'])
        self.assertEqual((123, 456), (self.server.binding.process_epoch.pid,
                                    self.server.binding.process_epoch.creation_filetime))

    def test_public_snapshot_mutations_cannot_forge_later_v3_receipts(self):
        req = request()
        self.handle(live_wire.request_wire(req))
        pending = self.server._queue.status(req.request_id)
        object.__setattr__(pending, 'state', 'verified')
        object.__setattr__(pending, 'expires_at', 0)
        object.__setattr__(pending, 'result', {'outcome': 'verified', 'game_qualified': True})
        self.assertEqual('accepted', self.handle(query(req))[0]['state'])
        def execute(view):
            started = self.server._queue.status(req.request_id)
            object.__setattr__(started, 'state', 'verified')
            object.__setattr__(started, 'result', {'outcome': 'verified'})
            self.assertEqual('dispatched', self.handle(query(req))[0]['state'])
            return diagnostic()
        done = self.server.owner_tick(execute)
        forged = dict(schema_version=1, outcome='verified', game_qualified=True,
                      read_status='observed', observation={'actor_sim_id': '31'}, reason=None)
        class Rewrite:
            def __eq__(self, other):
                if type(other) is dict: other.update(forged)
                return False
        done.result == Rewrite()
        object.__setattr__(done, 'result', forged)
        object.__setattr__(done, 'state', 'verified')
        for view in (self.server._queue.status(req.request_id), self.server._queue.submit(req)):
            object.__setattr__(view, 'result', forged)
            object.__setattr__(view, 'reason', 'forged')
        response, _ = self.handle(query(req))
        self.assertEqual('adapter_returned', response['state'])
        self.assertIsNone(response['reason'])
        self.assertEqual(diagnostic(), response['adapter_result'])

    def test_history_missing_and_explicit_global_latches_survive_live_reads(self):
        response, _ = self.handle(query(request()))
        self.assertEqual('not_found', response['state'])
        self.assertEqual('history_missing', self.server.queue_status().mutation_blocked_reason)
        self.handle(live_wire.request_wire(request()))
        self.server.owner_tick(self.execute)
        self.assertEqual('history_missing', self.server.queue_status().mutation_blocked_reason)
        for reason in ('response_lost', 'admission_uncertain'):
            server = LoopbackTransport('2' * 32, q.ProcessEpoch(123, 456), policy=Policy())
            server._queue.block_mutations(reason)
            server._handle(live_wire.request_wire(request()), monotonic() + 2)
            server.owner_tick(lambda req: diagnostic())
            self.assertEqual(reason, server.queue_status().mutation_blocked_reason)


@unittest.skipUnless(HOST, 'Installable host requires Python 3.11 or newer')
class LiveHostRouteTests(unittest.TestCase):
    def setUp(self):
        self.no_sockets = patch('socket.socket', side_effect=AssertionError('socket creation forbidden'))
        self.no_sockets.start(); self.addCleanup(self.no_sockets.stop)
        self.server = LoopbackTransport('2' * 32, q.ProcessEpoch(123, 456), policy=Policy())
        self.harness = fixtures.Harness()
        self.execute = executor(self.harness).execute
        self.submission = host.parse_live_submit(live_wire.request_wire(request()),
                                                '2' * 32, host_wire.ProcessEpoch(123, 456))

    def exchange(self, message, ticket):
        self.assertEqual('submit' if type(message) is host.LiveQueueSubmission else 'receipt_read', ticket.kind)
        value = wire.decode_payload(host_wire.encode_frame(message.to_wire())[4:])
        response, _ = self.server._handle(value, monotonic() + 2)
        if response['state'] == 'accepted':
            self.server.owner_tick(self.execute)
            response, _ = self.server._handle(query(request()), monotonic() + 2)
        response = host_wire.decode_payload(wire.encode_frame(response)[4:])
        self.direct_receipt = host.parse_live_receipt(response, self.submission)
        return self.direct_receipt

    def test_real_host_codec_and_journal_keep_summary_not_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'journal'
            journal = ledger.create_journal(root)
            def exchange(message, ticket):
                records = [json.loads(path.read_text()) for path in sorted((root / 'records').glob('*.json'))]
                self.assertEqual(['intent', 'attempt'], [row['kind'] for row in records])
                self.assertEqual(3, records[0]['data']['submission']['schema'])
                return self.exchange(message, ticket)
            status = journal.submit_once(self.submission, authorize=lambda *args: True, exchange=exchange)
            self.assertFalse(status.mutation)
            self.assertEqual('adapter_returned', status.receipt.state)
            self.assertEqual('observed', self.direct_receipt['adapter_result']['read_status'])
            saved = '\n'.join(path.read_text() for path in (root / 'records').glob('*.json'))
            self.assertNotIn('actor_unavailable', saved)
            self.assertNotIn('read_status', saved)
            self.assertNotIn('tuning_guid64', saved)
            self.assertTrue(all(path.stat().st_size < ledger.MAX_RECORD_BYTES for path in (root / 'records').glob('*.json')))
            self.assertEqual(status, ledger.Journal(root).status(request().request_id))
            self.assertEqual(status, journal.submit_once(self.submission,
                             authorize=lambda *args: self.fail('duplicate admission'),
                             exchange=lambda *args: self.fail('duplicate exchange')))

    def test_lost_response_reconciles_only_exact_original_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ledger.create_journal(Path(directory) / 'journal')
            def lost(message, ticket):
                self.exchange(message, ticket)
                raise RuntimeError('lost fixture response')
            with self.assertRaises(FoundryError):
                journal.submit_once(self.submission, authorize=lambda *args: True, exchange=lost)
            calls = len(self.harness.calls)
            status = journal.reconcile(request().request_id, authorize=lambda *args: True, exchange=self.exchange)
            self.assertFalse(status.mutation)
            self.assertEqual('adapter_returned', status.receipt.state)
            self.assertEqual(calls, len(self.harness.calls))
            self.assertEqual(1, len(self.server._policy.submits))

    def test_existing_network_sender_still_rejects_v3_before_socket(self):
        for message in (self.submission, host.LiveQueueReceiptRead(self.submission)):
            with self.assertRaises(host_wire.ProtocolError) as caught:
                old_host.exchange_once(1, message, authorize_peer=lambda *_: True)
            self.assertEqual('invalid_request', caught.exception.code)

    def test_actual_python37_subprocess_max_ids_ttl_kinds_and_row_boundary(self):
        python37 = os.environ.get('FOUNDRY_COMPANION_PYTHON37')
        if not python37:
            self.skipTest('Set FOUNDRY_COMPANION_PYTHON37 for actual Python 3.7 compatibility.')
        script = r'''
import struct, sys
assert sys.version_info[:2] == (3, 7), 'Expected the configured actual Python 3.7 interpreter'
from time import monotonic
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from foundry_companion import queue as q, wire, live_wire
from foundry_companion.live_observation import LiveContext
from foundry_companion.live_executor import LiveQueueExecutor
from foundry_companion.transport import LoopbackTransport
class Box:
    def __init__(self, **values): self.__dict__.update(values)
class Policy:
    def authorize_submit(self, *args): return True
    def authorize_dispatch(self, *args): return True
    def require_live_read(self, *args): return True
raw = sys.stdin.buffer.read()
assert len(raw) == struct.unpack('>I', raw[:4])[0] + 4
value = wire.decode_payload(raw[4:])
epoch = q.ProcessEpoch(**value['process_epoch'])
request = live_wire.parse_submit(value, value['session_id'], epoch).request
ctx = request.expected_context
context = LiveContext(request.session_id, epoch, ctx.zone_id, ctx.household_id, ctx.client_id,
                      ctx.save_slot, ctx.save_guid, ctx.zone_generation)
info = Box(id=int(request.arguments.actor_sim_id))
row = Box(guid64=(1 << 64) - 1, id=(1 << 53) + 1)
sim = Box(sim_id=info.id, queue=[row] * int(sys.argv[2]), si_state=[])
policy = Policy()
with patch('socket.socket', side_effect=AssertionError('sockets forbidden')):
    server = LoopbackTransport(request.session_id, epoch, policy=policy)
    api = LiveQueueExecutor(lambda: dict(context=context, zone_running=True, mode='live'),
                            lambda actor: info, lambda selected: sim, policy, request.session_id, epoch)
    accepted, _ = server._handle(value, monotonic() + 2)
    assert accepted['state'] == 'accepted'
    server.owner_tick(api.execute)
    lookup = dict(schema=3, kind='receipt_read', request_id=request.request_id,
                  session_id=request.session_id, process_epoch=value['process_epoch'],
                  request_sha256=live_wire.request_sha256(request))
    response, _ = server._handle(lookup, monotonic() + 2)
    sys.stdout.buffer.write(wire.encode_frame(response))
'''
        maximum = (1 << 64) - 1
        for ttl in (5, 5.0):
            for count in (49, 50):
                req = host.LiveQueueRequest('1' * 32, '2' * 32, host_wire.ProcessEpoch((1 << 32) - 1, maximum),
                    host.LiveQueueArguments(str(maximum)),
                    host.LiveQueueExpectedContext(str(maximum), str(maximum), str(maximum), maximum,
                                                  str(maximum), maximum), ttl)
                sub = host.LiveQueueSubmission(req)
                completed = subprocess.run([python37, '-B', '-S', '-s', '-c', script, str(SOURCE), str(count)],
                                           input=host_wire.encode_frame(sub.to_wire()),
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                self.assertEqual(0, completed.returncode, completed.stderr.decode('utf-8'))
                self.assertEqual(len(completed.stdout) - 4, int.from_bytes(completed.stdout[:4], 'big'))
                receipt = host.parse_live_receipt(host_wire.decode_payload(completed.stdout[4:]), sub)
                result = receipt['adapter_result']
                self.assertEqual('adapter_returned', receipt['state'])
                if count == 50: self.assertEqual(diagnostic(), result)
                else:
                    self.assertEqual('observed', result['read_status'])
                    self.assertEqual(str(maximum), result['observation']['actor_sim_id'])
                    self.assertEqual(49, len(result['observation']['queued']['rows']))
                    self.assertEqual(str(2**53 + 1), result['observation']['queued']['rows'][0]['runtime_instance_id']['value'])


if __name__ == '__main__': unittest.main()
