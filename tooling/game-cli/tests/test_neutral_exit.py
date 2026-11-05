"""Injected-only normal-exit tests; no game, UI, profile or native input."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

if not os.path.isabs(__file__):
    raise ImportError('The test module needs an absolute lexical source path.')
_src = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src'))
_package = os.path.join(_src, 'sims4foundry_cli')
def _origin_matches(module, filename):
    actual = getattr(module, '__file__', None)
    return (type(actual) is str and os.path.isabs(actual) and
            os.path.normcase(os.path.normpath(actual)) == os.path.normcase(os.path.normpath(filename)))
_modules = [('sims4foundry_cli', os.path.join(_package, '__init__.py'))]
_modules += [('sims4foundry_cli.' + _stem, os.path.join(_package, _stem + '.py'))
             for _stem in ('neutral_exit', 'errors', 'io', 'neutral_records', 'windows',
                           'companion_build', 'path_identity', 'launch')]
for _name, _file in _modules:
    if _name in sys.modules and not _origin_matches(sys.modules[_name], _file):
        raise ImportError('Refusing a cached Foundry module from another lexical checkout: ' + _name)
sys.path.insert(0, _src)
from sims4foundry_cli import neutral_exit as exit_workflow
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import digest
for _name, _file in _modules:
    if _name in sys.modules and not _origin_matches(sys.modules[_name], _file):
        raise ImportError('Loaded Foundry module does not belong to this lexical checkout: ' + _name)


IDENTITY = {'schema': 1, 'kind': 'foundry_windows_identity', 'pid': 123,
    'creation_filetime': 456, 'hwnd': 4, 'thread_id': 5, 'dpi': 96, 'integrity': 8192,
    'client_width': 1280, 'client_height': 720, 'executable': r'C:\Fixture\TS4_x64.exe',
    'executable_sha256': 'a' * 64, 'window_class': 'Canvas-fixture', 'client_origin': [0, 0]}


def request(policy='save_and_exit'):
    return {'request_id': 'b' * 32, 'policy': policy, 'identity': deepcopy(IDENTITY),
            'save_guid': '1234', 'household_id': '5678', 'profile_binding_sha256': 'c' * 64}


class Lease:
    def __init__(self, events):
        self.events = events
        self.terminated = False
        self.alive = True
        self.closed = False
    def identity(self):
        return deepcopy(IDENTITY)
    def is_running(self):
        return self.alive
    def wait_exit(self, seconds):
        self.events.append(('wait', seconds))
        return {'pid': 123, 'creation_filetime': 456, 'handle_retained': True, 'terminated': self.terminated}
    def close(self):
        self.closed = True
        return {'pid': 123, 'creation_filetime': 456, 'released': True}


class Provider:
    def __init__(self):
        self.events = []
        self.lease = Lease(self.events)
        self.surface = 'live'
        self.fail_confirm = False
        self.change_after_intent = False
        self.reads = 0
    def retain_process(self, identity):
        self.events.append(('retain', identity['pid']))
        return self.lease
    def readback(self, identity, binding):
        self.reads += 1
        if self.change_after_intent and self.surface == 'exit_confirmation' and self.reads >= 4:
            self.surface = 'changed'
        actions = ['open_exit_menu'] if self.surface == 'live' else ['confirm_save_and_exit', 'confirm_discard_and_exit']
        return {'identity': identity, **binding, 'surface': self.surface, 'view_sha256': 'd' * 64,
                'available_actions': actions}
    def send_once(self, identity, binding, request_id, action, view):
        self.events.append(('send', action))
        if action == 'open_exit_menu':
            self.surface = 'exit_confirmation'
        elif self.fail_confirm:
            raise OSError('fixture: confirmation reply lost')
        return {'request_id': request_id, 'action': action, 'view_sha256': digest(view), 'accepted': True}


class ExitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='foundry-neutral-exit-test-')
        self.addCleanup(self.temp.cleanup)
        self.session = Path(self.temp.name) / 'exit-session'
        self.registry = Path(self.temp.name) / 'reservations'
        self.registry.mkdir()
        self.provider = Provider()

    def workflow(self, policy='save_and_exit'):
        value = exit_workflow.ExitWorkflow(request(policy), self.session, self.registry, self.provider)
        self.addCleanup(lambda: value.close(abandon_unresolved=True))
        return value

    def test_both_policies_require_readback_and_retained_handle_before_confirm(self):
        for policy in ('save_and_exit', 'discard_and_exit'):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory(prefix='foundry-exit-mode-') as directory:
                provider = Provider()
                provider.lease.terminated = True
                registry = Path(directory) / 'reservations'
                registry.mkdir()
                workflow = exit_workflow.ExitWorkflow(request(policy), Path(directory) / 'session', registry, provider)
                result = workflow.run()
                self.assertTrue(result['provider_terminated_observed'])
                self.assertFalse(result['process_exit_verified'])
                self.assertFalse(result['ok'])
                self.assertFalse(result['normal_exit_verified'])
                self.assertFalse(result['save_verified'])
                self.assertFalse(result['game_qualified'])
                self.assertEqual(provider.events[:3], [('retain', 123), ('send', 'open_exit_menu'),
                                                       ('send', 'confirm_' + policy)])
                self.assertEqual(workflow.reconcile(), result)
                self.assertEqual(len([x for x in provider.events if x[0] == 'send']), 2)
                with self.assertRaisesRegex(FoundryError, 'remains retained'):
                    workflow.close()
                released = workflow.close(abandon_unresolved=True)
                self.assertTrue(released['provider_release_acknowledged'])
                self.assertFalse(released['os_release_verified'])

    def test_lost_confirmation_ack_is_unresolved_and_never_replayed(self):
        self.provider.fail_confirm = True
        workflow = self.workflow()
        result = workflow.run()
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual(result['stage'], 'confirm_once')
        self.provider.lease.terminated = True
        self.assertEqual(workflow.reconcile()['outcome'], 'unresolved')
        with self.assertRaises(FoundryError):
            workflow.run()
        other = self.workflow()
        with self.assertRaises(FoundryError):
            other.run()
        self.assertEqual([x for x in self.provider.events if x[0] == 'send'],
                         [('send', 'open_exit_menu'), ('send', 'confirm_save_and_exit')])

    def test_confirmation_readback_change_refuses_input(self):
        self.provider.change_after_intent = True
        result = self.workflow().run()
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual([x for x in self.provider.events if x[0] == 'send'], [('send', 'open_exit_menu')])

    def test_receipt_publish_ack_lost_reconciles_without_sending_again(self):
        original = exit_workflow.new_json
        def publish_then_raise(path, value):
            original(path, value)
            if Path(path).name == 'confirm-accepted.json':
                raise OSError('fixture receipt acknowledgement lost')
        workflow = self.workflow()
        with patch.object(exit_workflow, 'new_json', publish_then_raise):
            self.assertEqual(workflow.run()['outcome'], 'unresolved')
        self.provider.lease.terminated = True
        self.assertTrue(workflow.reconcile()['provider_terminated_observed'])
        self.assertEqual(len([x for x in self.provider.events if x[0] == 'send']), 2)

    def test_no_handle_never_infers_fast_exit_or_sends_input(self):
        self.provider.retain_process = lambda identity: None
        workflow = exit_workflow.ExitWorkflow(request(), self.session, self.registry, self.provider)
        result = workflow.run()
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertFalse(result['process_exit_verified'])
        self.assertEqual(self.provider.events, [])
        with self.assertRaisesRegex(FoundryError, 'acquisition is uncertain'):
            workflow.close(abandon_unresolved=True)

    def test_permission_error_stops_before_input(self):
        workflow = self.workflow()
        with patch.object(exit_workflow, 'new_json', side_effect=PermissionError('fixture denial')):
            with self.assertRaises(PermissionError):
                workflow.run()
        self.assertEqual(self.provider.events, [])

    def test_process_can_exit_immediately_after_confirm_on_original_lease(self):
        original = self.provider.send_once
        def send_then_exit(identity, binding, request_id, action, view):
            result = original(identity, binding, request_id, action, view)
            if action == 'confirm_discard_and_exit':
                self.provider.lease.alive = False
                self.provider.lease.terminated = True
            return result
        self.provider.send_once = send_then_exit
        result = self.workflow('discard_and_exit').run()
        self.assertTrue(result['provider_terminated_observed'])
        self.assertFalse(result['process_exit_verified'])
        self.assertFalse(result['normal_exit_verified'])
        self.assertFalse(result['save_verified'])
        self.assertEqual(self.provider.events[0], ('retain', 123))

    def test_foreign_context_blocks_first_input(self):
        original = self.provider.readback
        def foreign(identity, binding):
            value = original(identity, binding)
            value['save_guid'] = '9999'
            return value
        self.provider.readback = foreign
        result = self.workflow().run()
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual([x for x in self.provider.events if x[0] == 'send'], [])

    def test_missing_semantic_choice_blocks_confirmation(self):
        original = self.provider.readback
        def without_discard(identity, binding):
            value = original(identity, binding)
            if value['surface'] == 'exit_confirmation':
                value['available_actions'] = ['confirm_save_and_exit']
            return value
        self.provider.readback = without_discard
        result = self.workflow('discard_and_exit').run()
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual([x for x in self.provider.events if x[0] == 'send'], [('send', 'open_exit_menu')])

    def test_confirmation_intent_changed_after_publication_cannot_send(self):
        original = self.provider.readback
        def tamper_on_second_confirmation(identity, binding):
            value = original(identity, binding)
            if self.provider.surface == 'exit_confirmation' and self.provider.reads >= 4:
                (self.session / 'confirm-intent.json').write_text('{}', encoding='utf-8')
            return value
        self.provider.readback = tamper_on_second_confirmation
        result = self.workflow().run()
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual([x for x in self.provider.events if x[0] == 'send'], [('send', 'open_exit_menu')])

    def test_pid_reuse_or_malformed_retained_result_is_not_exit_proof(self):
        self.provider.lease.wait_exit = lambda seconds: {
            'pid': 123, 'creation_filetime': 999, 'handle_retained': True, 'terminated': True}
        result = self.workflow().run()
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertFalse(result['process_exit_verified'])

    def test_permission_error_after_confirmation_attempt_is_not_retried(self):
        original = self.provider.send_once
        def denied(identity, binding, request_id, action, view):
            if action == 'confirm_save_and_exit':
                self.provider.events.append(('send', action))
                raise PermissionError('fixture access refusal')
            return original(identity, binding, request_id, action, view)
        self.provider.send_once = denied
        workflow = self.workflow()
        with self.assertRaises(PermissionError):
            workflow.run()
        self.assertEqual(workflow.reconcile()['outcome'], 'unresolved')
        self.assertEqual(len([x for x in self.provider.events if x[0] == 'send']), 2)

    def test_fresh_request_and_session_cannot_evade_original_lifetime_reservation(self):
        self.provider.fail_confirm = True
        workflow = self.workflow()
        self.assertEqual(workflow.run()['outcome'], 'unresolved')
        fresh = request('discard_and_exit')
        fresh['request_id'] = 'e' * 32
        other = exit_workflow.ExitWorkflow(fresh, Path(self.temp.name) / 'different-session',
                                           self.registry, self.provider)
        self.addCleanup(lambda: other.close(abandon_unresolved=True))
        result = other.run()
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual(result['stage'], 'reserve_lifetime')
        self.assertEqual([x for x in self.provider.events if x[0] == 'send'],
                         [('send', 'open_exit_menu'), ('send', 'confirm_save_and_exit')])

    def test_unresolved_close_retains_lease_until_observation(self):
        workflow = self.workflow()
        self.assertEqual(workflow.run()['outcome'], 'unresolved')
        with self.assertRaisesRegex(FoundryError, 'remains retained'):
            workflow.close()
        self.assertFalse(self.provider.lease.closed)
        self.provider.lease.terminated = True
        self.assertTrue(workflow.reconcile()['provider_terminated_observed'])
        with self.assertRaisesRegex(FoundryError, 'remains retained'):
            workflow.close()
        released = workflow.close(abandon_unresolved=True)
        self.assertTrue(released['provider_release_acknowledged'])
        self.assertFalse(released['os_release_verified'])
        self.assertTrue(self.provider.lease.closed)

    def test_explicit_abandon_close_ack_failure_never_recloses_handle(self):
        workflow = exit_workflow.ExitWorkflow(request(), self.session, self.registry, self.provider)
        self.assertEqual(workflow.run()['outcome'], 'unresolved')
        calls = []
        def close_then_raise():
            calls.append('close')
            self.provider.lease.closed = True
            raise OSError('fixture close acknowledgement lost')
        self.provider.lease.close = close_then_raise
        with self.assertRaisesRegex(OSError, 'acknowledgement lost'):
            workflow.close(abandon_unresolved=True)
        with self.assertRaisesRegex(FoundryError, 'do not retry'):
            workflow.close(abandon_unresolved=True)
        with self.assertRaises(FoundryError):
            workflow.reconcile()
        self.assertEqual(calls, ['close'])
        release = json.loads((self.session / 'lease-release-intent.json').read_text(encoding='ascii'))
        self.assertTrue(release['abandon_unresolved'])
        self.assertFalse(release['provider_terminal_observed'])

    def test_release_intent_publication_failure_does_not_close_handle(self):
        workflow = exit_workflow.ExitWorkflow(request(), self.session, self.registry, self.provider)
        self.assertEqual(workflow.run()['outcome'], 'unresolved')
        original = exit_workflow.new_json
        def lost_receipt(path, value):
            original(path, value)
            if Path(path).name == 'lease-release-intent.json':
                raise OSError('fixture release intent acknowledgement lost')
        with patch.object(exit_workflow, 'new_json', lost_receipt):
            with self.assertRaisesRegex(OSError, 'intent acknowledgement lost'):
                workflow.close(abandon_unresolved=True)
        self.assertFalse(self.provider.lease.closed)
        with self.assertRaisesRegex(FoundryError, 'do not retry'):
            workflow.close(abandon_unresolved=True)
        self.provider.lease.terminated = True
        self.assertTrue(workflow.reconcile()['provider_terminated_observed'])

    def test_durable_input_intent_does_not_claim_a_sender_attempt(self):
        self.provider.fail_confirm = True
        self.assertEqual(self.workflow().run()['outcome'], 'unresolved')
        intent = json.loads((self.session / 'confirm-intent.json').read_text(encoding='ascii'))
        self.assertEqual(intent['send_state'], 'not_started_or_unknown')
        self.assertNotIn('input_attempted', intent)

    def test_missing_or_negative_release_receipt_stays_unresolved_without_retry(self):
        for response in (None, False, {'pid': 123, 'creation_filetime': 456, 'released': False},
                         {'pid': 123, 'creation_filetime': 999, 'released': True}):
            with self.subTest(response=response), tempfile.TemporaryDirectory(prefix='foundry-release-case-') as directory:
                registry = Path(directory) / 'reservations'
                registry.mkdir()
                provider = Provider()
                workflow = exit_workflow.ExitWorkflow(request(), Path(directory) / 'session', registry, provider)
                self.assertEqual(workflow.run()['outcome'], 'unresolved')
                calls = []
                def no_ack():
                    calls.append('close')
                    return response
                provider.lease.close = no_ack
                with self.assertRaisesRegex(FoundryError, 'do not retry'):
                    workflow.close(abandon_unresolved=True)
                with self.assertRaisesRegex(FoundryError, 'do not retry'):
                    workflow.close(abandon_unresolved=True)
                self.assertEqual(calls, ['close'])

    def test_provider_claim_without_os_proof_is_never_labeled_verified(self):
        workflow = self.workflow()
        self.assertEqual(workflow.run()['outcome'], 'unresolved')
        self.provider.lease.close = lambda: {'pid': 123, 'creation_filetime': 456, 'released': True}
        result = workflow.close(abandon_unresolved=True)
        self.assertTrue(result['provider_release_acknowledged'])
        self.assertFalse(result['os_release_verified'])
        self.assertFalse(self.provider.lease.closed)

    def test_provider_acquires_then_loses_return_witness_without_reacquiring(self):
        calls = []
        acquired = []
        def acquire_then_raise(identity):
            calls.append('acquire')
            acquired.append(self.provider.lease)
            raise OSError('fixture acquisition acknowledgement lost')
        self.provider.retain_process = acquire_then_raise
        workflow = exit_workflow.ExitWorkflow(request(), self.session, self.registry, self.provider)
        result = workflow.run()
        self.assertEqual(result['stage'], 'retain_process')
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual(calls, ['acquire'])
        self.assertFalse(acquired[0].closed)
        with self.assertRaisesRegex(FoundryError, 'acquisition is uncertain'):
            workflow.close(abandon_unresolved=True)
        with self.assertRaises(FoundryError):
            workflow.reconcile()
        self.assertEqual(calls, ['acquire'])
        self.assertEqual([x for x in self.provider.events if x[0] == 'send'], [])
        intent = json.loads((self.session / 'lease-acquire-intent.json').read_text(encoding='ascii'))
        self.assertEqual(intent['acquisition_state'], 'not_started_or_unknown')

    def test_permission_denial_during_acquisition_remains_unknown_and_is_not_retried(self):
        calls = []
        def denied(identity):
            calls.append('acquire')
            raise PermissionError('fixture acquisition denied')
        self.provider.retain_process = denied
        workflow = exit_workflow.ExitWorkflow(request(), self.session, self.registry, self.provider)
        with self.assertRaises(PermissionError):
            workflow.run()
        with self.assertRaisesRegex(FoundryError, 'acquisition is uncertain'):
            workflow.close(abandon_unresolved=True)
        self.assertEqual(calls, ['acquire'])
        self.assertEqual([x for x in self.provider.events if x[0] == 'send'], [])

    def test_returned_foreign_lease_is_neither_closed_nor_reacquired(self):
        foreign = deepcopy(IDENTITY)
        foreign['creation_filetime'] = 999
        self.provider.lease.identity = lambda: foreign
        workflow = exit_workflow.ExitWorkflow(request(), self.session, self.registry, self.provider)
        result = workflow.run()
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual(result['stage'], 'retain_process')
        with self.assertRaisesRegex(FoundryError, 'do not close or reacquire'):
            workflow.close(abandon_unresolved=True)
        self.assertFalse(self.provider.lease.closed)
        self.assertEqual([x for x in self.provider.events if x[0] == 'send'], [])

    def test_boolean_schema_is_refused_even_with_recomputed_record_digest(self):
        workflow = self.workflow()
        self.assertEqual(workflow.run()['outcome'], 'unresolved')
        target = self.session / 'confirm-accepted.json'
        value = json.loads(target.read_text(encoding='utf-8'))
        value['schema'] = True
        value['record_sha256'] = digest({k: v for k, v in value.items() if k != 'record_sha256'})
        target.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(FoundryError, 'malformed or changed'):
            workflow.reconcile()

    def test_terminal_observation_extra_claim_or_numeric_type_change_is_refused(self):
        for edit in ('claimed_save', 'float_pid', 'wrong_outcome'):
            with self.subTest(edit=edit), tempfile.TemporaryDirectory(prefix='foundry-exit-record-') as directory:
                registry = Path(directory) / 'reservations'
                registry.mkdir()
                provider = Provider()
                provider.lease.terminated = True
                workflow = exit_workflow.ExitWorkflow(request(), Path(directory) / 'session', registry, provider)
                workflow.run()
                target = Path(directory) / 'session' / 'process-exit.json'
                value = json.loads(target.read_text(encoding='utf-8'))
                if edit == 'claimed_save':
                    value['observation']['save_verified'] = True
                elif edit == 'float_pid':
                    value['observation']['pid'] = 123.0
                else:
                    value['observation']['outcome'] = 'saved'
                value['record_sha256'] = digest({k: v for k, v in value.items() if k != 'record_sha256'})
                target.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaisesRegex(FoundryError, 'Stored process observation differs'):
                    workflow.reconcile()
                workflow.close(abandon_unresolved=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
