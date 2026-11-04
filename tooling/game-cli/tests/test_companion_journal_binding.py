"""Fabricated callback aliases only; no sockets, game, credentials or profile."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

if sys.version_info < (3, 11):
    raise unittest.SkipTest('Host journal requires Python 3.11+.')

from sims4foundry_cli import companion_journal as ledger
from sims4foundry_cli import companion_live_protocol as live
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import read_json
from test_companion_journal import submission as legacy_item, receipt as legacy_receipt
from test_live_read_journal import host_item, receipt as live_receipt


class JournalBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.counter = 0

    def fixture(self, variant):
        self.counter += 1
        path = self.root / ('ledger-' + str(self.counter))
        item = host_item() if variant == 'live' else legacy_item(observe=variant == 'read')
        return ledger.create_journal(path), item, live_receipt if variant == 'live' else legacy_receipt, path

    def code(self, expected, callback):
        with self.assertRaises(FoundryError) as caught:
            callback()
        self.assertEqual(expected, caught.exception.code)

    def test_shadowed_ticket_serializer_cannot_hide_changed_admission_fields(self):
        changes = dict(journal_id='d' * 32, request_id='c' * 32, request_sha256='b' * 64,
                       attempt_id='e' * 32, sequence=2, kind='receipt_read')
        for variant in ('mutation', 'read', 'live'):
            for field, value in changes.items():
                with self.subTest(variant=variant, field=field):
                    journal, item, _, _ = self.fixture(variant)
                    def authorize(message, ticket):
                        original = ledger.Attempt.to_wire(ticket)
                        object.__setattr__(ticket, 'to_wire', lambda: original)
                        object.__setattr__(ticket, field, value)
                        return True
                    exchange = Mock()
                    self.code('admission_changed', lambda: journal.submit_once(item, authorize=authorize, exchange=exchange))
                    exchange.assert_not_called()
                    status = journal.status(item.request.request_id)
                    self.assertEqual('intent_recorded', status.state)
                    self.assertEqual(item.request_sha256, status.request_sha256)

    def test_attempt_class_serializer_checks_all_primitive_types(self):
        ticket = ledger.Attempt('a' * 32, 'b' * 32, 'c' * 64, 'd' * 32, 1, 'submit')
        class Text(str): pass
        class Child(ledger.Attempt): pass
        for field, value in (('journal_id', Text('a' * 32)), ('request_id', None),
                             ('request_sha256', 'z' * 64), ('attempt_id', 1),
                             ('sequence', True), ('sequence', 1.0), ('sequence', 0),
                             ('sequence', ledger.MAX_ATTEMPTS + 1),
                             ('kind', Text('submit')), ('kind', 'unknown')):
            with self.subTest(field=field, value=value):
                self.code('invalid_attempt', lambda: ledger.Attempt.to_wire(replace(ticket, **{field: value})))
        self.code('invalid_attempt', lambda: ledger.Attempt.to_wire(Child(**ledger.Attempt.to_wire(ticket))))

    def test_malformed_ticket_cannot_hide_behind_serializer(self):
        for variant in ('mutation', 'read', 'live'):
            for value in (True, 1.0, None, '1'):
                journal, item, _, _ = self.fixture(variant)
                def authorize(message, ticket):
                    original = ledger.Attempt.to_wire(ticket)
                    object.__setattr__(ticket, 'to_wire', lambda: original)
                    object.__setattr__(ticket, 'sequence', value)
                    return True
                exchange = Mock()
                self.code('admission_changed', lambda: journal.submit_once(item, authorize=authorize, exchange=exchange))
                exchange.assert_not_called()

    def test_nested_serializers_cannot_hide_changed_admission_message(self):
        for variant in ('mutation', 'live'):
            for target in ('request', 'epoch', 'arguments', 'context', 'ttl'):
                with self.subTest(variant=variant, target=target):
                    journal, item, _, _ = self.fixture(variant)
                    def authorize(message, ticket):
                        original = ledger._message_wire(message)
                        object.__setattr__(message, 'to_wire', lambda: original)
                        object.__setattr__(message, '__post_init__', lambda: None)
                        request = message.request
                        object.__setattr__(request, 'to_wire', lambda: original if variant == 'live' else original['request'])
                        if target == 'request':
                            object.__setattr__(request, 'request_id', 'f' * 32)
                        elif target == 'epoch':
                            old = dict(pid=request.process_epoch.pid, creation_filetime=request.process_epoch.creation_filetime)
                            object.__setattr__(request.process_epoch, 'to_wire', lambda: old)
                            object.__setattr__(request.process_epoch, 'creation_filetime', old['creation_filetime'] + 1)
                        elif target == 'arguments':
                            field = 'actor_sim_id' if variant == 'live' else 'speed'
                            old = {field: getattr(request.arguments, field)}
                            object.__setattr__(request.arguments, 'to_wire', lambda: old)
                            object.__setattr__(request.arguments, field, '31' if variant == 'live' else 3)
                        elif target == 'context':
                            context = request.expected_context if variant == 'live' else message.expected_context
                            old = original['expected_context']
                            object.__setattr__(context, 'to_wire', lambda: old)
                            object.__setattr__(context, 'save_guid', '100')
                        else:
                            object.__setattr__(request if variant == 'live' else message, 'ttl_seconds', 6.0)
                        return True
                    exchange = Mock()
                    self.code('admission_changed', lambda: journal.submit_once(item, authorize=authorize, exchange=exchange))
                    exchange.assert_not_called()

    def test_exchange_gets_fresh_views_without_serializer_poison_or_retained_aliases(self):
        for variant in ('mutation', 'read', 'live'):
            with self.subTest(variant=variant):
                journal, item, make_receipt, _ = self.fixture(variant)
                saved = []
                original = ledger._message_wire(item)
                def authorize(message, ticket):
                    saved.extend((message, ticket))
                    object.__setattr__(message, 'to_wire', lambda: {'forged': 'other_actor'})
                    object.__setattr__(message.request, 'to_wire', lambda: {'forged': 'other_request'})
                    object.__setattr__(message.request.process_epoch, 'to_wire', lambda: {'pid': 1, 'creation_filetime': 1})
                    object.__setattr__(message.request.arguments, 'to_wire', lambda: {'forged': 'arguments'})
                    object.__setattr__(ticket, 'to_wire', lambda: {'kind': 'receipt_read'})
                    return True
                def exchange(message, ticket):
                    prior, prior_ticket = saved
                    self.assertIsNot(message, prior)
                    self.assertIsNot(ticket, prior_ticket)
                    self.assertIsNot(message.request, prior.request)
                    self.assertIsNot(message.request.process_epoch, prior.request.process_epoch)
                    self.assertIsNot(message.request.arguments, prior.request.arguments)
                    object.__setattr__(prior.request, 'request_id', 'e' * 32)
                    object.__setattr__(prior.request.process_epoch, 'pid', 42)
                    object.__setattr__(prior_ticket, 'request_id', 'e' * 32)
                    object.__setattr__(prior_ticket, 'kind', 'receipt_read')
                    self.assertEqual(original, message.to_wire())
                    self.assertEqual(item.request.request_id, ticket.request_id)
                    self.assertEqual('submit', ticket.kind)
                    return make_receipt(message, 'accepted')
                self.assertEqual('accepted', journal.submit_once(item, authorize=authorize, exchange=exchange).state)

    def test_changed_exchange_ticket_is_unknown_even_with_saved_serializer(self):
        for variant in ('mutation', 'read', 'live'):
            journal, item, make_receipt, _ = self.fixture(variant)
            def exchange(message, ticket):
                original = ledger.Attempt.to_wire(ticket)
                response = make_receipt(message, 'accepted')
                object.__setattr__(ticket, 'to_wire', lambda: original)
                object.__setattr__(ticket, 'kind', 'receipt_read')
                return response
            self.code('receipt_uncertain', lambda: journal.submit_once(item, authorize=lambda *_: True, exchange=exchange))
            status = journal.status(item.request.request_id)
            self.assertEqual('unknown', status.state)
            self.assertTrue(status.uncertainty_latched)
            again = Mock()
            self.assertEqual(status, journal.submit_once(item, authorize=again, exchange=again))
            again.assert_not_called()

    def test_reconciliation_also_detaches_admission_and_exchange_views(self):
        for variant in ('mutation', 'read', 'live'):
            journal, item, make_receipt, path = self.fixture(variant)
            journal.submit_once(item, authorize=lambda *_: True, exchange=lambda message, _: make_receipt(message, 'accepted'))
            saved = []
            def authorize(message, ticket):
                saved.extend((message, ticket))
                object.__setattr__(message, 'to_wire', lambda: {'kind': 'submit'})
                object.__setattr__(ticket, 'to_wire', lambda: {'kind': 'submit'})
                return True
            def exchange(message, ticket):
                self.assertIsNot(message, saved[0])
                self.assertIsNot(message.submission, saved[0].submission)
                self.assertIsNot(ticket, saved[1])
                object.__setattr__(saved[0].submission.request, 'request_id', 'f' * 32)
                object.__setattr__(saved[1], 'kind', 'submit')
                self.assertEqual('receipt_read', message.to_wire()['kind'])
                self.assertEqual('receipt_read', ticket.kind)
                self.assertEqual(2, ticket.sequence)
                return make_receipt(message, 'accepted')
            self.assertEqual('accepted', journal.reconcile(item.request.request_id, authorize=authorize, exchange=exchange).state)
            rows = [read_json(p) for p in sorted((path / 'records').glob('*.json'))]
            self.assertEqual(['submit', 'receipt_read'], [r['data']['kind'] for r in rows if r['kind'] == 'attempt'])

    def test_reloaded_live_summary_cannot_claim_verified_adapter_outcome(self):
        journal, item, make_receipt, path = self.fixture('live')
        journal.submit_once(item, authorize=lambda *_: True, exchange=lambda message, _: make_receipt(message))
        record_path, head_path = path / 'records/000003.json', path / 'head.json'
        record, head = read_json(record_path), read_json(head_path)
        record['data']['snapshot']['adapter_outcome'] = 'verified'
        record_path.write_text(json.dumps(record))
        head['record_sha256'] = ledger._sha(record)
        head_path.write_text(json.dumps(head))
        self.code('journal_corrupt', lambda: ledger.Journal(path).status())
        # This checks schema consistency, not defense against coherent forgery.


if __name__ == '__main__':
    unittest.main()
