"""Input journal tests use a synthetic backend and temporary files only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sims4foundry_cli import input_journal
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import Point, ClientBounds, Message, encode_click
from test_guarded_input import FakeBackend, identity


class InputJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'input-history'
        self.journal = input_journal.InputJournal.create(self.root, '2'*32, identity())
        self.packets = encode_click(Point(20,30), ClientBounds(500,500))
        self.backend = FakeBackend()
        self.id = '1'*32
    def acknowledge(self, request_id=None):
        def observer(view, target, expectation):
            self.assertEqual(42, target['pid'])
            return dict(view, evidence_sha256='a'*64, observation=expectation)
        return self.journal.acknowledge(request_id or self.id, observer)
    def test_queue_acceptance_needs_readback_before_next_run(self):
        result = self.journal.execute_once(self.id, self.packets, self.backend, expected_observation={'mode':'cas'})
        self.assertEqual('awaiting_readback', result['state'])
        self.assertFalse(result['result']['semantic_success'])
        self.assertEqual('awaiting_readback', input_journal.InputJournal(self.root).status(self.id)['state'])
        with self.assertRaises(FoundryError) as error:
            self.journal.execute_once('3'*32, self.packets, FakeBackend(), expected_observation={'mode':'cas'})
        self.assertEqual('input_pending', error.exception.code)
        self.assertEqual('acknowledged_by_host', self.acknowledge()['state'])
        self.assertEqual('awaiting_readback',
                         self.journal.execute_once('3'*32, self.packets, FakeBackend(), expected_observation={'mode':'cas'})['state'])
        with self.assertRaises(FoundryError): self.journal.execute_once(self.id, self.packets, FakeBackend(), expected_observation={'mode':'cas'})
        self.assertEqual([Message.LEFT_DOWN,Message.LEFT_UP],[packet.message for _,packet in self.backend.posts])
    def test_lost_down_ack_remains_unresolved_after_restart(self):
        def fail(packet):
            if packet.message is Message.LEFT_DOWN: raise OSError('lost native reply')
            return True
        self.backend.failure = fail
        result = self.journal.execute_once(self.id, self.packets, self.backend, expected_observation={'mode':'cas'})
        self.assertEqual('unresolved', result['state'])
        self.assertEqual([Message.LEFT_DOWN,Message.LEFT_UP],[packet.message for _,packet in self.backend.posts])
        reopened = input_journal.InputJournal(self.root)
        self.assertEqual('unresolved', reopened.status(self.id)['state'])
        with self.assertRaises(FoundryError): reopened.execute_once('3'*32, self.packets, FakeBackend(), expected_observation={'mode':'cas'})
        with self.assertRaises(FoundryError): reopened.acknowledge(self.id, lambda *args:{})
    def test_foreign_or_mutated_acknowledgements_do_not_release_latch(self):
        self.journal.execute_once(self.id, self.packets, self.backend, expected_observation={'mode':'cas'})
        def wrong(view, target, expectation):
            view['request_id'] = '3'*32
            return dict(view,evidence_sha256='a'*64,observation=expectation)
        with self.assertRaises(FoundryError): self.journal.acknowledge(self.id, wrong)
        def altered_target(view, target, expectation):
            target['hwnd'] = 999
            return dict(view,evidence_sha256='a'*64,observation=expectation)
        with self.assertRaises(FoundryError): self.journal.acknowledge(self.id, altered_target)
        def altered_expectation(view, target, expectation):
            expectation['mode'] = 'live'
            return dict(view,evidence_sha256='a'*64,observation=expectation)
        with self.assertRaises(FoundryError): self.journal.acknowledge(self.id, altered_expectation)
        def wrong_observation(view, target, expectation):
            return dict(view,evidence_sha256='a'*64,observation={'mode':'live'})
        with self.assertRaises(FoundryError): self.journal.acknowledge(self.id, wrong_observation)
        for key, value in (('session_id','3'*32),('intent_sha256','b'*64),
                           ('request_id','3'*32),('evidence_sha256','not-a-hash')):
            def malformed(view, target, expectation):
                result = dict(view,evidence_sha256='a'*64,observation=expectation)
                result[key] = value
                return result
            with self.subTest(key=key), self.assertRaises(FoundryError):
                self.journal.acknowledge(self.id, malformed)
        self.assertEqual('awaiting_readback', self.journal.status(self.id)['state'])
    def test_intent_persistence_failure_never_calls_backend(self):
        original = input_journal._write
        def fail(path, value):
            if path.name == 'intent.json': raise OSError('fixture failure')
            return original(path,value)
        with mock.patch.object(input_journal, '_write', side_effect=fail):
            with self.assertRaises(OSError): self.journal.execute_once(self.id, self.packets, self.backend, expected_observation={'mode':'cas'})
        self.assertEqual([],self.backend.posts)
        with self.assertRaises(FoundryError): self.journal.execute_once('3'*32, self.packets, FakeBackend(), expected_observation={'mode':'cas'})
    def test_lost_attempt_write_ack_is_unresolved_and_never_replayed(self):
        original = input_journal._write
        def lose(path,value):
            original(path,value)
            if path.name == 'attempt.json': raise OSError('lost acknowledgement')
        with mock.patch.object(input_journal, '_write', side_effect=lose):
            with self.assertRaises(OSError): self.journal.execute_once(self.id, self.packets, self.backend, expected_observation={'mode':'cas'})
        self.assertEqual([],self.backend.posts)
        self.assertEqual('unresolved',self.journal.status(self.id)['state'])
        with self.assertRaises(FoundryError): self.journal.execute_once(self.id,self.packets,self.backend, expected_observation={'mode':'cas'})
    def test_lost_post_event_ack_is_unresolved_even_if_no_post_occurred(self):
        original = input_journal._write
        def lose(path,value):
            original(path,value)
            if path.name == 'event-0001.json': raise OSError('lost acknowledgement')
        with mock.patch.object(input_journal, '_write', side_effect=lose):
            result = self.journal.execute_once(self.id,self.packets,self.backend, expected_observation={'mode':'cas'})
        self.assertEqual('unresolved',result['state'])
        self.assertEqual([],self.backend.posts)
        self.assertEqual('unresolved',self.journal.status(self.id)['state'])
    def test_runner_exception_reports_bounded_code_and_remains_unresolved(self):
        with mock.patch.object(self.backend,'foreground',side_effect=FoundryError('foreground_changed','PRIVATE')):
            result = self.journal.execute_once(self.id,self.packets,self.backend,expected_observation={'mode':'cas'})
        self.assertEqual('unresolved',result['state'])
        self.assertEqual({'diagnostic_source':'runner_exception','diagnostic_code':'foreground_changed',
                          'input_attempted':'unknown','no_replay':True},result['result'])
        self.assertEqual(result['result'],input_journal.InputJournal(self.root).status(self.id)['result'])
        self.assertNotIn('PRIVATE',json.dumps(result))
        with self.assertRaises(FoundryError):
            self.journal.execute_once('3'*32,self.packets,FakeBackend(),expected_observation={'mode':'cas'})
        self.assertEqual([],self.backend.posts)

    def test_escaped_runner_error_after_possible_effect_is_never_labeled_not_dispatched(self):
        def effect_then_fail(*args):
            self.backend.posts.append(('possible_effect',None))
            raise FoundryError('foreground_changed','PRIVATE')
        with mock.patch.object(input_journal,'run_packets',side_effect=effect_then_fail):
            result = self.journal.execute_once(self.id,self.packets,self.backend,expected_observation={'mode':'cas'})
        self.assertEqual('unresolved',result['state'])
        self.assertEqual('unknown',result['result']['input_attempted'])
        self.assertTrue(result['result']['no_replay'])
        with self.assertRaises(FoundryError):
            self.journal.execute_once('3'*32,self.packets,FakeBackend(),expected_observation={'mode':'cas'})
        self.assertEqual(1,len(self.backend.posts))

    def test_unknown_runner_error_diagnostic_does_not_expose_exception(self):
        with mock.patch.object(self.backend,'foreground',side_effect=FoundryError('account_PRIVATE','PRIVATE')):
            result = self.journal.execute_once(self.id,self.packets,self.backend,expected_observation={'mode':'cas'})
        self.assertEqual('other',result['result']['diagnostic_code'])
        self.assertEqual('unknown',result['result']['input_attempted'])
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_result_write_failure_does_not_replay(self):
        original = input_journal._write
        def fail(path,value):
            if path.name == 'result.json': raise OSError('fixture failure')
            return original(path,value)
        with mock.patch.object(input_journal, '_write', side_effect=fail):
            result = self.journal.execute_once(self.id,self.packets,self.backend, expected_observation={'mode':'cas'})
        self.assertEqual('unresolved',result['state'])
        self.assertEqual('unresolved',self.journal.status(self.id)['state'])
        with self.assertRaises(FoundryError): self.journal.execute_once(self.id,self.packets,self.backend, expected_observation={'mode':'cas'})
        self.assertEqual(2,len(self.backend.posts))
    def test_event_or_session_corruption_fails_closed(self):
        self.journal.execute_once(self.id,self.packets,self.backend, expected_observation={'mode':'cas'})
        event = self.root/'runs'/self.id/'events'/'event-0001.json'
        data = json.loads(event.read_bytes())
        data['previous_sha256'] = 'b'*64
        event.write_text(json.dumps(data))
        with self.assertRaises(FoundryError): self.journal.status(self.id)
        with self.assertRaises(FoundryError): self.journal.execute_once('3'*32,self.packets,FakeBackend(), expected_observation={'mode':'cas'})
    def test_nonexact_identity_and_request_fail_before_backend(self):
        for request in ('1','A'*32,True):
            with self.assertRaises(FoundryError): self.journal.execute_once(request,self.packets,self.backend, expected_observation={'mode':'cas'})
        with self.assertRaises(FoundryError): self.journal.execute_once(self.id,list(self.packets),self.backend, expected_observation={'mode':'cas'})
        self.assertEqual([],self.backend.posts)
    def test_invalid_expected_ui_state_fails_before_backend(self):
        for value in ({}, {'mode':float('nan')}, {'mode':object()}, {'mode':'a'*257}, {'mode':(1,2)}):
            with self.subTest(value=type(value).__name__), self.assertRaises(FoundryError):
                self.journal.execute_once(self.id,self.packets,self.backend,expected_observation=value)
        self.assertEqual([],self.backend.posts)
    def test_process_target_is_detached_from_caller(self):
        original = identity()
        other = input_journal.InputJournal.create(Path(self.temp.name)/'other','4'*32,original)
        original['hwnd'] = 999
        result = other.execute_once('5'*32,self.packets,self.backend, expected_observation={'mode':'cas'})
        self.assertEqual('awaiting_readback',result['state'])
        self.assertEqual(100,self.backend.posts[0][0])
