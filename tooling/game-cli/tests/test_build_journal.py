"""Local fixture-only journal checks; callbacks do not contact a game."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sims4foundry_cli import build_journal as ledger
from sims4foundry_cli.errors import FoundryError


class BuildJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'history'
        self.journal = ledger.BuildJournal.create(self.root, '2'*32)
        self.intent = {'schema':1, 'request_id':'1'*32, 'session_id':'2'*32,
                       'operation':'create', 'context':{'zone':'10'},
                       'arguments':{'definition_id':'40'}}
        self.calls = []
    def callback(self, value):
        self.calls.append(value)
        return {'outcome':'created', 'object_id':'30', 'position':(1.,2.,3.)}
    def test_persist_return_and_reopen_without_replay(self):
        result = self.journal.execute_once(self.intent, self.callback)
        self.assertEqual('returned', result['state'])
        self.assertEqual([1.,2.,3.], result['value']['position'])
        reopened = ledger.BuildJournal(self.root)
        self.assertEqual(result['value'], reopened.status('1'*32)['value'])
        with self.assertRaises(FoundryError): reopened.execute_once(self.intent, self.callback)
        self.assertEqual(1, len(self.calls))
    def test_same_id_cannot_be_reused_for_another_operation(self):
        self.journal.execute_once(self.intent, self.callback)
        other = dict(self.intent, operation='destroy')
        with self.assertRaises(FoundryError): self.journal.execute_once(other, self.callback)
        self.assertEqual(1, len(self.calls))
    def test_callback_exception_or_mutation_is_unresolved(self):
        def fail(view):
            self.calls.append(view)
            raise RuntimeError('PRIVATE')
        result = self.journal.execute_once(self.intent, fail)
        self.assertEqual('unresolved', result['state'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual('unresolved', ledger.BuildJournal(self.root).status('1'*32)['state'])
        with self.assertRaises(FoundryError): self.journal.execute_once(self.intent, self.callback)
        self.assertEqual(1, len(self.calls))
    def test_changed_callback_view_is_unresolved_and_original_detached(self):
        def mutate(view):
            view['arguments']['definition_id'] = '41'
            return {'outcome':'created'}
        self.assertEqual('unresolved', self.journal.execute_once(self.intent, mutate)['state'])
        self.assertEqual('40', self.intent['arguments']['definition_id'])
    def test_lost_attempt_ack_never_dispatches_or_replays(self):
        original = ledger.new_json
        def write_then_lose(path, value):
            original(path, value)
            if path.name.endswith('.attempt.json'):
                raise OSError('lost write acknowledgement')
        with mock.patch.object(ledger, 'new_json', side_effect=write_then_lose):
            with self.assertRaises(OSError): self.journal.execute_once(self.intent, self.callback)
        self.assertEqual([], self.calls)
        self.assertEqual('unresolved', self.journal.status('1'*32)['state'])
        with self.assertRaises(FoundryError): self.journal.execute_once(self.intent, self.callback)
    def test_lost_result_ack_is_inspected_not_replayed(self):
        original = ledger.new_json
        def write_then_lose(path, value):
            original(path, value)
            if path.name.endswith('.result.json'):
                raise OSError('lost write acknowledgement')
        with mock.patch.object(ledger, 'new_json', side_effect=write_then_lose):
            self.assertEqual('unresolved', self.journal.execute_once(self.intent, self.callback)['state'])
        self.assertEqual('returned', ledger.BuildJournal(self.root).status('1'*32)['state'])
        with self.assertRaises(FoundryError): self.journal.execute_once(self.intent, self.callback)
        self.assertEqual(1, len(self.calls))
    def test_uncertain_prior_request_blocks_a_different_request_after_reopen(self):
        def lose_reply(view):
            self.calls.append(view)
            raise RuntimeError('lost acknowledgement')
        self.assertEqual('unresolved', self.journal.execute_once(self.intent, lose_reply)['state'])
        reopened = ledger.BuildJournal(self.root)
        later = dict(self.intent, request_id='4'*32)
        with self.assertRaises(FoundryError) as error:
            reopened.execute_once(later, self.callback)
        self.assertEqual('journal_pending', error.exception.code)
        self.assertEqual('not_found', reopened.status('4'*32)['state'])
        self.assertEqual(1, len(self.calls))

    def test_orphan_record_blocks_a_new_request(self):
        (self.root/'requests'/('5'*32+'.attempt.json')).write_text('{}')
        with self.assertRaises(FoundryError):
            self.journal.execute_once(self.intent, self.callback)
        self.assertEqual([], self.calls)

    def test_prepared_prior_request_blocks_a_new_request(self):
        original = ledger.new_json
        def fail_before_attempt(path, value):
            if path.name.endswith('.attempt.json'):
                raise OSError('attempt publication failed')
            return original(path, value)
        with mock.patch.object(ledger, 'new_json', side_effect=fail_before_attempt):
            with self.assertRaises(OSError):
                self.journal.execute_once(self.intent, self.callback)
        self.assertEqual('prepared', self.journal.status('1'*32)['state'])
        with self.assertRaises(FoundryError) as error:
            self.journal.execute_once(dict(self.intent, request_id='4'*32), self.callback)
        self.assertEqual('journal_pending', error.exception.code)
        self.assertEqual([], self.calls)

    def test_missing_result_and_malformed_records_fail_closed(self):
        self.journal.execute_once(self.intent, self.callback)
        result = self.root/'requests'/('1'*32+'.result.json')
        result.unlink()
        self.assertEqual('unresolved', self.journal.status('1'*32)['state'])
        attempt = self.root/'requests'/('1'*32+'.attempt.json')
        raw = json.loads(attempt.read_bytes())
        raw['schema'] = True
        attempt.write_text(json.dumps(raw))
        with self.assertRaises(FoundryError): self.journal.status('1'*32)
    def test_oversized_nonfinite_and_invalid_intents_never_dispatch(self):
        cases = [dict(self.intent, request_id='abc'), dict(self.intent, operation='undo'),
                 dict(self.intent, session_id='3'*32),
                 dict(self.intent, arguments={'position':float('nan')}),
                 dict(self.intent, arguments={'nested':'a'*257})]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(FoundryError): self.journal.execute_once(case, self.callback)
        self.assertEqual([], self.calls)
    def test_result_bounds_and_types_are_unresolved(self):
        result = self.journal.execute_once(self.intent, lambda view:{'invalid':object()})
        self.assertEqual('unresolved', result['state'])
        self.assertIsNone(result['value'])
    def test_unknown_files_or_changed_journal_identity_refuse(self):
        (self.root/'requests'/'unrelated').write_text('fixture')
        with self.assertRaises(FoundryError): self.journal.execute_once(self.intent, self.callback)
        (self.root/'requests'/'unrelated').unlink()
        (self.root/'journal.json').write_text('{"schema":1,"session_id":"' + '3'*32 + '"}')
        with self.assertRaises(FoundryError): self.journal.execute_once(self.intent, self.callback)
        self.assertEqual([], self.calls)
