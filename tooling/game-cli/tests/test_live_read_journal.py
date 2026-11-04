"""Current-host schema-3 journal integration using fabricated injected exchanges.

No sockets, process/profile/save access, receipt-body store, sender or game claims.
"""
import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

HOST = sys.version_info >= (3, 11)
if not HOST:
    raise unittest.SkipTest('Host journal requires Python 3.11+.')

from test_companion_live_protocol import submit_value, result_value, submission, unavailable_result
from foundry_companion import live_wire as companion
from sims4foundry_cli import companion_journal as ledger
from sims4foundry_cli import companion_live_protocol as live
from sims4foundry_cli import companion_protocol as wire
from sims4foundry_cli.companion_transport import Submission, ExpectedContext, ReceiptRead
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import read_json


def host_item(number=1, ttl=5.0):
    value = submit_value()
    value['request_id'] = '{:032x}'.format(number)
    value['ttl_seconds'] = ttl
    return live.parse_live_submit(value, value['session_id'], wire.ProcessEpoch(**value['process_epoch']))


def mutation(number=2):
    return Submission(wire.Request('{:032x}'.format(number), '2' * 32, wire.ProcessEpoch(123, 456),
                      wire.Operation.SET_SPEED, wire.SetSpeedArguments(2)), 5.0,
                      ExpectedContext('10', '20', 4, '99'))


def receipt(message, state='adapter_returned', diagnostic=None):
    item = message.submission if type(message) is live.LiveQueueReceiptRead else message
    original = submission(live.LiveQueueSubmission.to_wire(item))
    if state == 'adapter_returned':
        value = unavailable_result(diagnostic) if diagnostic else result_value(original.request)
        return companion.preview_result_receipt(value, original.request)
    envelope = companion.receipt(item.request.request_id, item.request_sha256,
                                 item.request.session_id, original.request.process_epoch)
    envelope.update(state=state, reason=('execution_exception' if state == 'unknown' else
                                        'host_admission_denied' if state == 'refused' else None))
    return envelope


def admit(*_):
    return True


class LiveReadJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger'
        self.journal = ledger.create_journal(self.path)
        self.item = host_item()

    def records(self):
        return [read_json(path) for path in sorted((self.path / 'records').glob('*.json'))]

    def code(self, expected, call):
        with self.assertRaises(FoundryError) as caught:
            call()
        self.assertEqual(expected, caught.exception.code)

    def test_full_intent_before_hooks_and_explicit_submit_event(self):
        returned = []
        def authorize(message, attempt):
            self.assertIs(type(message), live.LiveQueueSubmission)
            self.assertEqual('submit', attempt.kind)
            rows = self.records()
            self.assertEqual(['intent'], [row['kind'] for row in rows])
            self.assertEqual(self.item.to_wire(), rows[0]['data']['submission'])
            return True
        def exchange(message, attempt):
            self.assertEqual(['intent', 'attempt'], [row['kind'] for row in self.records()])
            self.assertEqual('submit', self.records()[1]['data']['kind'])
            returned.append(receipt(message))
            return returned[-1]
        status = self.journal.submit_once(self.item, authorize=authorize, exchange=exchange)
        self.assertFalse(status.mutation)
        self.assertTrue(status.unresolved)
        self.assertFalse(status.live_qualified)
        self.assertFalse(status.independent_evidence)
        self.assertEqual('adapter_returned', status.state)
        self.assertEqual('unverified', status.receipt.adapter_outcome)
        self.assertEqual('observed', returned[0]['adapter_result']['read_status'])
        self.assertEqual(status, ledger.Journal(self.path).status(self.item.request.request_id))
        self.assertEqual(self.item.request_sha256, status.request_sha256)
        # Only immutable intent and receipt summary persist, never observation rows.
        saved = self.records()[-1]['data']['snapshot']
        self.assertEqual({'state', 'reason', 'adapter_outcome', 'receipt_sha256'}, set(saved))
        self.assertNotIn('read_status', json.dumps(self.records()))
        self.assertNotIn('runtime_instance_id', json.dumps(self.records()))

    def test_reopen_full_ttl_kind_and_exact_receipt_read(self):
        self.journal.submit_once(self.item, authorize=admit, exchange=lambda message, _: receipt(message, 'accepted'))
        def read(message, attempt):
            self.assertIs(type(message), live.LiveQueueReceiptRead)
            self.assertEqual('receipt_read', attempt.kind)
            self.assertEqual(self.item.to_wire(), message.submission.to_wire())
            self.assertEqual(live.LiveQueueReceiptRead(self.item).to_wire(), message.to_wire())
            self.assertIs(type(message.submission.request.ttl_seconds), float)
            return receipt(message)
        status = ledger.Journal(self.path).reconcile(self.item.request.request_id, authorize=admit, exchange=read)
        self.assertEqual('adapter_returned', status.state)
        self.assertEqual(['submit', 'receipt_read'], [row['data']['kind'] for row in self.records() if row['kind'] == 'attempt'])
        self.assertFalse(status.mutation)
        self.assertEqual(2, status.attempt_count)

    def test_digest_conflicts_all_context_actor_and_numeric_kind(self):
        self.journal.record_intent(self.item)
        variants = [replace(self.item.request, ttl_seconds=5), replace(self.item.request, ttl_seconds=6.0),
                    replace(self.item.request, arguments=live.LiveQueueArguments('31')),
                    replace(self.item.request, session_id='3' * 32),
                    replace(self.item.request, process_epoch=wire.ProcessEpoch(124, 456)),
                    replace(self.item.request, process_epoch=wire.ProcessEpoch(123, 457))]
        for name, value in [('zone_id', '11'), ('household_id', '21'), ('client_id', '6'),
                            ('save_slot', 5), ('save_guid', '100'), ('zone_generation', 2)]:
            variants.append(replace(self.item.request, expected_context=replace(self.item.request.expected_context, **{name: value})))
        for request in variants:
            self.code('intent_conflict', lambda: self.journal.submit_once(live.LiveQueueSubmission(request)))
        self.assertEqual(1, len(self.records()))

    def test_uncertain_read_never_replays_but_reconciles_exact_original(self):
        calls = []
        def fail(message, attempt):
            calls.append(attempt.kind)
            raise OSError('fabricated lost response')
        self.code('exchange_uncertain', lambda: self.journal.submit_once(self.item, authorize=admit, exchange=fail))
        status = ledger.Journal(self.path).submit_once(self.item, authorize=admit, exchange=fail)
        self.assertEqual(['submit'], calls)
        self.assertFalse(status.mutation)
        self.assertTrue(status.uncertainty_latched)
        def reconcile(message, attempt):
            calls.append(attempt.kind)
            self.assertIs(type(message), live.LiveQueueReceiptRead)
            return receipt(message)
        status = self.journal.reconcile(self.item.request.request_id, authorize=admit, exchange=reconcile)
        self.assertEqual(['submit', 'receipt_read'], calls)
        self.assertTrue(status.uncertainty_latched)
        self.assertFalse(status.mutation)
        self.assertTrue(status.unresolved)
        # A read never becomes an unresolved mutation barrier.
        self.assertTrue(self.journal.record_intent(mutation()).mutation)

    def test_read_does_not_resolve_or_block_prior_mutation_uncertainty(self):
        prior = self.journal.record_intent(mutation())
        self.assertTrue(prior.mutation)
        for number, diagnostic in ((1, None), (3, 'row_limit_exceeded'), (4, 'result_budget_exceeded')):
            item = host_item(number)
            status = self.journal.submit_once(item, authorize=admit,
                    exchange=lambda message, _, reason=diagnostic: receipt(message, diagnostic=reason))
            self.assertFalse(status.mutation)
            self.assertEqual('adapter_returned', status.state)
        self.assertEqual(prior, self.journal.status(mutation().request.request_id))
        self.code('mutation_unresolved', lambda: ledger.Journal(self.path).record_intent(mutation(5)))
        self.assertEqual([True, False, False, False], [status.mutation for status in self.journal.status()])

    def test_unavailable_summary_is_not_observation_persistence(self):
        seen = []
        def exchange(message, _):
            value = receipt(message, diagnostic='row_limit_exceeded'); seen.append(value); return value
        status = self.journal.submit_once(self.item, authorize=admit, exchange=exchange)
        self.assertEqual('unavailable', seen[0]['adapter_result']['read_status'])
        self.assertEqual('unverified', status.receipt.adapter_outcome)
        snapshot = self.records()[-1]['data']['snapshot']
        self.assertEqual(None, snapshot['reason'])
        self.assertNotIn('row_limit_exceeded', json.dumps(self.records()))
        self.assertFalse(hasattr(status.receipt, 'observation'))
        self.assertFalse(hasattr(status.receipt, 'read_status'))

    def test_default_admission_and_local_false_never_exchange(self):
        self.code('host_policy_required', lambda: self.journal.submit_once(self.item))
        callback = Mock(side_effect=AssertionError('not admitted'))
        status = self.journal.submit_once(self.item, authorize=lambda *_: False, exchange=callback)
        self.assertEqual('not_dispatched', status.state)
        self.assertFalse(status.mutation)
        self.assertFalse(status.unresolved)
        self.assertEqual(['intent', 'local_refusal'], [row['kind'] for row in self.records()])
        callback.assert_not_called()

    def test_admission_changed_full_context_never_transmits(self):
        callback = Mock()
        def authorize(message, _):
            object.__setattr__(message.request.expected_context, 'client_id', '6')
            return True
        self.code('admission_changed', lambda: self.journal.submit_once(self.item, authorize=authorize, exchange=callback))
        callback.assert_not_called()
        self.assertEqual(self.item.to_wire(), self.records()[0]['data']['submission'])
        self.assertFalse(self.journal.status()[0].mutation)

    def test_shadowed_method_cannot_hide_changed_actor(self):
        callback = Mock()
        def authorize(message, _):
            baseline = message.to_wire()
            object.__setattr__(message, 'to_wire', lambda: baseline)
            object.__setattr__(message.request.arguments, 'actor_sim_id', '31')
            return True
        self.code('admission_changed', lambda: self.journal.submit_once(self.item, authorize=authorize, exchange=callback))
        callback.assert_not_called()

    def test_mutated_exchange_and_wrong_nested_receipt_remain_uncertain(self):
        def exchange(message, _):
            value = receipt(message)
            value['adapter_result']['observation']['actor_sim_id'] = '31'
            return value
        self.code('receipt_uncertain', lambda: self.journal.submit_once(self.item, authorize=admit, exchange=exchange))
        self.assertTrue(ledger.Journal(self.path).status()[0].uncertainty_latched)
        self.assertEqual('invalid_receipt', self.records()[-1]['data']['reason'])
        callback = Mock(side_effect=AssertionError('no replay'))
        self.journal.submit_once(self.item, authorize=callback, exchange=callback)
        callback.assert_not_called()

    def test_old_peer_error_is_uncertain_without_fallback(self):
        messages = []
        def old_peer(message, _):
            messages.append(type(message))
            return dict(schema=2, kind='error', code='unsupported_schema', outcome_known=False)
        self.code('receipt_uncertain', lambda: self.journal.submit_once(self.item, authorize=admit, exchange=old_peer))
        self.journal.submit_once(self.item, authorize=admit, exchange=old_peer)
        self.assertEqual([live.LiveQueueSubmission], messages)
        self.assertTrue(self.journal.status()[0].uncertainty_latched)
        def read(message, _):
            messages.append(type(message)); return receipt(message, 'unknown')
        self.journal.reconcile(self.item.request.request_id, authorize=admit, exchange=read)
        self.assertEqual([live.LiveQueueSubmission, live.LiveQueueReceiptRead], messages)

    def test_v2_success_cannot_satisfy_v3_even_with_copied_digest(self):
        def exchange(message, _):
            value = receipt(message); value['schema'] = 2; return value
        self.code('receipt_uncertain', lambda: self.journal.submit_once(self.item, authorize=admit, exchange=exchange))
        self.assertEqual('unknown', self.journal.status()[0].state)

    def test_record_schema_hash_chain_and_four_kib_bound_unchanged(self):
        self.journal.submit_once(self.item, authorize=admit, exchange=lambda message, _: receipt(message))
        previous = None
        for record, path in zip(self.records(), sorted((self.path / 'records').glob('*.json'))):
            self.assertEqual(1, record['schema'])
            self.assertEqual({'schema', 'journal_id', 'sequence', 'previous_sha256', 'kind', 'request_id', 'data'}, set(record))
            self.assertEqual(previous, record['previous_sha256'])
            previous = ledger._sha(record)
            self.assertLessEqual(path.stat().st_size, 4096)
        self.assertEqual(previous, read_json(self.path / 'head.json')['record_sha256'])
        self.assertEqual(4096, ledger.MAX_RECORD_BYTES)
        self.assertEqual(ledger.Journal(self.path).status(), self.journal.status())

    def test_exact_types_unknown_versions_and_prior_intent_no_replay(self):
        class Duck:
            def to_wire(self): return self.item.to_wire()
        self.code('invalid_request', lambda: self.journal.record_intent(Duck()))
        value = self.item.to_wire(); value['schema'] = 4
        self.code('journal_corrupt', lambda: ledger._submission(value))
        initial = self.journal.record_intent(self.item)
        callback = Mock(side_effect=AssertionError('prior intent never submits'))
        self.assertEqual(initial, ledger.Journal(self.path).submit_once(self.item, authorize=callback, exchange=callback))
        callback.assert_not_called()
        self.assertIs(type(ledger._freeze(self.item)), live.LiveQueueSubmission)
        self.assertIs(type(ledger._freeze(mutation())), Submission)


if __name__ == '__main__':
    unittest.main()
