"""Exact-mode contract fixtures only; no installed UI source is qualified here."""
from contextlib import contextmanager
from dataclasses import replace
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import test_companion_native as fixture
from foundry_companion.native import HostModeDeclaration, NativeAPI, NativeRefusal
from foundry_companion.queue import CompanionQueue, ProcessEpoch, SetSpeedArguments


class NativeModeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.NativeBindingTests()
        self.f.setUp()

    def execute(self, operation='live.set_speed'):
        return self.f.api.execute(fixture.request(operation))

    def no_dispatch(self):
        self.assertEqual([], self.f.commands)

    def test_generic_admission_does_not_supply_exact_mode(self):
        self.f.admission.observe_mode = None
        for operation in fixture.OPERATIONS[1:]:
            with self.subTest(operation=operation):
                with self.assertRaises(NativeRefusal): self.execute(operation)
        self.assertEqual([], self.f.loads)
        self.assertEqual([], self.f.admission.scopes)
        self.no_dispatch()

    def test_read_only_observe_makes_no_mode_claim_or_callback(self):
        self.f.admission.observe_mode = lambda *args: self.fail('Mode reader was called')
        result = self.execute('observe')
        self.assertEqual('verified', result['outcome'])
        self.assertNotIn('host_mode', result['observation'])
        self.assertNotIn('mode', result['observation'])
        self.assertEqual([], self.f.loads)

    def test_non_live_mode_never_reaches_imports_scopes_or_commands(self):
        for operation in fixture.OPERATIONS[1:]:
            for mode in ('cas', 'loading', 'build_buy', 'menu', 'unknown'):
                self.setUp(); self.f.admission.mode = mode
                with self.subTest(operation=operation, mode=mode):
                    with self.assertRaises(NativeRefusal): self.execute(operation)
                    self.assertEqual([], self.f.loads)
                    self.assertEqual([], self.f.admission.scopes)
                    self.no_dispatch()

    def test_live_declaration_cannot_override_generic_admission(self):
        for permission in ('read', 'mutate'):
            self.setUp(); setattr(self.f.admission, permission, False)
            with self.assertRaises(NativeRefusal): self.execute()
            self.assertEqual([], self.f.admission.mode_calls)
            self.assertEqual([], self.f.loads)
            self.no_dispatch()

    def test_mode_is_rechecked_after_each_import_before_next_action(self):
        for operation in fixture.OPERATIONS[1:]:
            self.setUp(); self.f.admission.cas = True
            def loader(name):
                self.f.admission.mode = 'cas'
                return self.f.loader(name)
            self.f.api = NativeAPI(self.f.services, self.f.admission, loader,
                                   session_id='2'*32, process_epoch=ProcessEpoch(123,456))
            with self.subTest(operation=operation):
                with self.assertRaises(NativeRefusal): self.execute(operation)
                self.assertEqual(1, len(self.f.loads))
                self.assertEqual([], self.f.admission.scopes)
                self.no_dispatch()

    def test_mode_loss_in_final_generic_admission_never_dispatches(self):
        for operation in fixture.OPERATIONS[1:]:
            self.setUp(); self.f.admission.cas = True
            calls = []
            final = 4 if operation == 'cas.enter' else 2
            def admitted(request, observation):
                calls.append(request)
                if len(calls) == final: self.f.admission.mode = 'loading'
                return True
            self.f.admission.require_mutation = admitted
            with self.subTest(operation=operation):
                with self.assertRaises(NativeRefusal): self.execute(operation)
                self.assertEqual(final, len(calls))
                self.no_dispatch()
                self.assertEqual(['enter','exit'] if operation=='cas.enter' else [],
                                 self.f.admission.scopes)

    def test_initial_admission_mode_loss_refuses_before_import(self):
        def admitted(request, observation):
            self.f.admission.mode = 'loading'
            return True
        self.f.admission.require_mutation = admitted
        with self.assertRaises(NativeRefusal): self.execute()
        self.assertEqual([], self.f.loads)
        self.no_dispatch()

    def test_post_speed_non_live_or_unavailable_latches_no_replay(self):
        for mode in ('cas', 'loading', 'build_buy', 'menu', 'unknown', None, True, 'LIVE'):
            self.setUp()
            def changed():
                self.f.clock.clock_speed = 2
                self.f.admission.mode = mode
            self.f.on_speed = changed
            queue = CompanionQueue('2'*32)
            request = fixture.request('live.set_speed')
            queue.submit(request)
            result = queue.drain(self.f.api.execute)
            with self.subTest(mode=mode):
                self.assertEqual('unverified', result.state)
                self.assertEqual('unresolved_mutation', queue.queue_status().mutation_blocked_reason)
                queue.submit(request)
                self.assertIsNone(queue.drain(self.f.api.execute))
                refused = queue.submit(replace(request, request_id='3'*32))
                self.assertEqual('refused', refused.state)
                self.assertIsNone(queue.drain(self.f.api.execute))
                self.assertEqual(1, len(self.f.commands))

    def test_post_speed_mode_exception_is_redacted_and_unverified(self):
        def changed():
            self.f.clock.clock_speed = 2
            def failed(*args): raise RuntimeError('private secret from provider')
            self.f.admission.observe_mode = failed
        self.f.on_speed = changed
        result = self.execute()
        self.assertEqual('unverified', result['outcome'])
        self.assertEqual('unknown', result['observation']['host_mode'])
        self.assertNotIn('private secret', repr(result))
        self.assertEqual(1, len(self.f.commands))

    def test_mode_provider_exception_is_redacted_before_dispatch(self):
        def failed(*args): raise RuntimeError('private secret from provider')
        self.f.admission.observe_mode = failed
        with self.assertRaises(NativeRefusal) as caught: self.execute()
        self.assertNotIn('private secret', str(caught.exception))
        self.assertEqual([], self.f.loads)
        self.no_dispatch()

    def test_exact_type_client_token_and_mode_are_required(self):
        changes = (lambda value: True,
                   lambda value: dict(mode='live'),
                   lambda value: replace(value, client_id=5),
                   lambda value: replace(value, client_id='6'),
                   lambda value: replace(value, sample_token=object()),
                   lambda value: replace(value, mode=True),
                   lambda value: replace(value, mode='Live'),
                   lambda value: replace(value, request=NS()))
        for index, change in enumerate(changes):
            self.setUp()
            def observer(request, observation, token):
                return change(HostModeDeclaration(request, observation['client_id'], token, 'live'))
            self.f.admission.observe_mode = observer
            with self.subTest(change=index):
                with self.assertRaises(NativeRefusal): self.execute()
                self.assertEqual([], self.f.loads)
                self.no_dispatch()

    def test_full_request_is_bound_in_returned_declaration(self):
        changes = ({'request_id':'3'*32}, {'session_id':'3'*32},
                   {'process_epoch':ProcessEpoch(124,456)},
                   {'process_epoch':ProcessEpoch(123,457)},
                   {'arguments':SetSpeedArguments(3)}, {'ttl_seconds':5},
                   {'expected_zone_id':'11'}, {'expected_household_id':'21'},
                   {'expected_save_slot':5}, {'expected_save_guid':'100'})
        for fields in changes:
            self.setUp()
            def observer(request, observation, token):
                return HostModeDeclaration(replace(request, **fields), observation['client_id'], token, 'live')
            self.f.admission.observe_mode = observer
            with self.subTest(fields=fields):
                with self.assertRaises(NativeRefusal): self.execute()
                self.assertEqual([], self.f.loads)
                self.no_dispatch()

    def test_request_view_mutation_is_refused(self):
        def observer(request, observation, token):
            object.__setattr__(request.arguments, 'speed', 3)
            return HostModeDeclaration(request, observation['client_id'], token, 'live')
        self.f.admission.observe_mode = observer
        with self.assertRaises(NativeRefusal): self.execute()
        self.assertEqual([], self.f.loads)
        self.no_dispatch()

    def test_observation_views_cannot_change_internal_context(self):
        def observer(request, observation, token):
            result = HostModeDeclaration(request, observation['client_id'], token, 'live')
            observation['client_id'] = '6'
            observation['save_slot'] = 5
            observation['errors']['fabricated'] = 'changed'
            return result
        self.f.admission.observe_mode = observer
        result = self.execute()
        self.assertEqual('verified', result['outcome'])
        self.assertEqual('5', result['before']['client_id'])
        self.assertEqual(4, result['observation']['save_slot'])
        self.assertEqual({}, result['observation']['errors'])
        self.assertEqual([('speed','two','foundry.cli',5)], self.f.commands)

    def test_cached_declaration_fails_on_the_next_sample(self):
        retained = []
        def observer(request, observation, token):
            if not retained:
                retained.append(HostModeDeclaration(request, observation['client_id'], token, 'live'))
            return retained[0]
        self.f.admission.observe_mode = observer
        with self.assertRaises(NativeRefusal): self.execute()
        self.assertEqual(1, len(self.f.loads))
        self.no_dispatch()
        another = NativeAPI(self.f.services, self.f.admission, self.f.loader,
                            session_id='2'*32, process_epoch=ProcessEpoch(123,456))
        with self.assertRaises(NativeRefusal): another.execute(fixture.request('live.set_speed'))
        self.assertEqual(1, len(self.f.loads))
        self.no_dispatch()

    def test_retained_declarations_cannot_rewrite_later_baselines(self):
        retained = []
        def observer(request, observation, token):
            for previous in retained:
                object.__setattr__(previous.request.arguments, 'speed', 3)
                object.__setattr__(previous.request.process_epoch, 'pid', 124)
                object.__setattr__(previous, 'client_id', '6')
                object.__setattr__(previous, 'mode', 'cas')
            value = HostModeDeclaration(request, observation['client_id'], token, 'live')
            retained.append(value)
            return value
        self.f.admission.observe_mode = observer
        self.assertEqual('verified', self.execute()['outcome'])
        self.assertEqual(3, len(retained))
        self.assertEqual(3, len({id(value.sample_token) for value in retained}))
        self.assertEqual([('speed','two','foundry.cli',5)], self.f.commands)

    def test_ident_match_does_not_substitute_for_owner_thread(self):
        impostor = NS(ident=threading.current_thread().ident)
        with patch('foundry_companion.native.threading.current_thread', return_value=impostor):
            with self.assertRaises(NativeRefusal): self.execute()
        self.assertEqual([], self.f.admission.mode_calls)
        self.no_dispatch()

    def test_owner_is_rechecked_after_mode_provider_returns(self):
        real = threading.current_thread()
        impostor = NS(ident=real.ident)
        changed = patch('foundry_companion.native.threading.current_thread', return_value=impostor)
        def observer(request, observation, token):
            changed.start()
            return HostModeDeclaration(request, observation['client_id'], token, 'live')
        self.f.admission.observe_mode = observer
        try:
            with self.assertRaises(NativeRefusal): self.execute()
        finally:
            changed.stop()
        self.assertEqual([], self.f.loads)
        self.no_dispatch()

    def test_initial_cas_lookup_mode_loss_cannot_enter_preservation(self):
        self.f.admission.cas = True
        def get(sid):
            self.f.admission.mode = 'loading'
            return self.f.sim
        self.f.services.sim_info_manager = lambda: NS(get=get)
        with self.assertRaises(NativeRefusal): self.execute('cas.enter')
        self.assertEqual([], self.f.admission.scopes)
        self.no_dispatch()

    def test_cas_scope_and_target_mode_changes_unwind_without_dispatch(self):
        for stage in ('scope', 'target', 'final_lookup', 'final_instance'):
            self.setUp(); self.f.admission.cas = True
            def changed(): self.f.admission.mode = 'cas'
            if stage == 'scope': self.f.admission.enter = changed
            elif stage == 'target':
                def target(value): changed(); return ('target', value)
                self.f.modules['server_commands.argument_helpers'].OptionalTargetParam = target
            elif stage == 'final_lookup':
                calls = []
                def get(sid):
                    calls.append(sid)
                    if len(calls) == 2: changed()
                    return self.f.sim
                self.f.services.sim_info_manager = lambda: NS(get=get)
            else:
                calls = []
                def instance():
                    calls.append(True)
                    if len(calls) == 2: changed()
                    return self.f.instance
                self.f.sim.get_sim_instance = instance
            with self.subTest(stage=stage):
                with self.assertRaises(NativeRefusal): self.execute('cas.enter')
                self.assertEqual(['enter','exit'], self.f.admission.scopes)
                self.no_dispatch()

    def test_cas_lookup_context_drift_refuses_before_scope(self):
        self.f.admission.cas = True
        def get(sid):
            self.f.persistence.get_save_slot_proto_guid = lambda:100
            return self.f.sim
        self.f.services.sim_info_manager = lambda:NS(get=get)
        with self.assertRaises(NativeRefusal): self.execute('cas.enter')
        self.assertEqual([], self.f.admission.scopes)
        self.no_dispatch()

    def test_success_is_a_host_assertion_not_game_qualification(self):
        result = self.execute()
        self.assertEqual('verified', result['outcome'])
        self.assertEqual('live', result['observation']['host_mode'])
        self.assertEqual('injected_host_declaration', result['observation']['mode_source'])
        self.assertFalse(result['game_qualified'])
        self.assertFalse(result['persistence_verified'])

    def test_mode_callback_context_side_effects_refuse_before_import(self):
        changes = (lambda:setattr(self.f.zone, 'id', 11),
                   lambda:setattr(self.f.household, 'id', 21),
                   lambda:setattr(self.f.client, 'id', 6),
                   lambda:setattr(self.f.persistence, 'get_save_slot_proto_buff', lambda:NS(slot_id=5)),
                   lambda:setattr(self.f.persistence, 'get_save_slot_proto_guid', lambda:100),
                   lambda:setattr(self.f.zone, 'is_zone_running', False),
                   lambda:setattr(self.f.zone, 'is_in_build_buy', True))
        for operation in fixture.OPERATIONS[1:]:
            for index, change in enumerate(changes):
                self.setUp()
                def observer(request, observation, token):
                    change()
                    return HostModeDeclaration(request, observation['client_id'], token, 'live')
                self.f.admission.observe_mode = observer
                with self.subTest(operation=operation, change=index):
                    with self.assertRaises(NativeRefusal): self.execute(operation)
                    self.assertEqual([], self.f.loads)
                    self.assertEqual([], self.f.admission.scopes)
                    self.no_dispatch()

    def test_post_mode_callback_clock_change_cannot_verify_stale_speed(self):
        calls = []
        def observer(request, observation, token):
            calls.append(token)
            if len(calls) == 3: self.f.clock.clock_speed = 0
            return HostModeDeclaration(request, observation['client_id'], token, 'live')
        self.f.admission.observe_mode = observer
        queue = CompanionQueue('2'*32)
        queue.submit(fixture.request('live.set_speed'))
        result = queue.drain(self.f.api.execute)
        self.assertEqual('unverified', result.state)
        self.assertEqual(0, result.result['observation']['clock_speed'])
        self.assertEqual('unresolved_mutation', queue.queue_status().mutation_blocked_reason)
        self.assertEqual(1, len(self.f.commands))

    def test_post_mode_callback_context_change_is_unverified(self):
        calls = []
        def observer(request, observation, token):
            calls.append(token)
            if len(calls) == 3: self.f.client.id = 6
            return HostModeDeclaration(request, observation['client_id'], token, 'live')
        self.f.admission.observe_mode = observer
        result = self.execute()
        self.assertEqual('unverified', result['outcome'])
        self.assertEqual('6', result['observation']['client_id'])
        self.assertEqual(1, len(self.f.commands))

    def test_malformed_typed_declaration_errors_are_redacted(self):
        self.f.admission.observe_mode = lambda *args:object.__new__(HostModeDeclaration)
        with self.assertRaises(NativeRefusal) as caught: self.execute()
        self.assertEqual('Exact host mode observation is unavailable or invalid.', str(caught.exception))
        self.assertEqual([], self.f.loads)
        self.no_dispatch()

    def test_mode_callback_cannot_reenter_execute(self):
        calls = []
        def observer(request, observation, token):
            calls.append(token)
            self.f.api.execute(replace(request, request_id='3'*32))
            return HostModeDeclaration(request, observation['client_id'], token, 'live')
        self.f.admission.observe_mode = observer
        with self.assertRaises(NativeRefusal): self.execute()
        self.assertEqual(1, len(calls))
        self.assertEqual([], self.f.loads)
        self.no_dispatch()
        # Guard cleanup lets a later read proceed; it is not reset/replay authority.
        self.assertEqual('verified', self.execute('observe')['outcome'])

    def test_handled_reentrancy_never_dispatches_nested_command(self):
        refusals = []
        def observer(request, observation, token):
            try: self.f.api.execute(replace(request, request_id='3'*32))
            except NativeRefusal: refusals.append(True)
            return HostModeDeclaration(request, observation['client_id'], token, 'live')
        self.f.admission.observe_mode = observer
        self.assertEqual('verified', self.execute()['outcome'])
        self.assertEqual(3, len(refusals))
        self.assertEqual(1, len(self.f.commands))

    def test_base_exception_releases_guard_and_queue_preserves_uncertainty(self):
        def failed(*args): raise KeyboardInterrupt()
        self.f.admission.observe_mode = failed
        queue = CompanionQueue('2'*32)
        request = fixture.request('live.set_speed')
        queue.submit(request)
        with self.assertRaises(KeyboardInterrupt): queue.drain(self.f.api.execute)
        self.assertEqual('unknown', queue.status(request.request_id).state)
        self.assertEqual('unresolved_mutation', queue.queue_status().mutation_blocked_reason)
        self.assertEqual('verified', self.execute('observe')['outcome'])
        self.no_dispatch()

    def test_last_mode_callback_cannot_rewrite_retained_cas_scope_request(self):
        self.f.admission.cas = True
        original = self.f.admission.cas_scope
        retained = []
        calls = []
        @contextmanager
        def scope(request, sim, client):
            retained.append(request)
            with original(request, sim, client): yield
        def observer(request, observation, token):
            calls.append(token)
            if len(calls) == 7:
                object.__setattr__(retained[0].arguments, 'sim_id', '31')
            return HostModeDeclaration(request, observation['client_id'], token, 'live')
        self.f.admission.cas_scope = scope
        self.f.admission.observe_mode = observer
        with self.assertRaises(NativeRefusal): self.execute('cas.enter')
        self.assertEqual(['enter','exit'], self.f.admission.scopes)
        self.no_dispatch()

    def test_cas_scope_mode_loss_never_reaches_target_constructor(self):
        self.f.admission.cas = True
        self.f.admission.enter = lambda:setattr(self.f.admission, 'mode', 'cas')
        target_calls = []
        self.f.modules['server_commands.argument_helpers'].OptionalTargetParam = target_calls.append
        with self.assertRaises(NativeRefusal): self.execute('cas.enter')
        self.assertEqual([], target_calls)
        self.assertEqual(['enter','exit'], self.f.admission.scopes)
        self.no_dispatch()

    def test_inside_scope_mode_callback_cannot_rewrite_scope_view_before_target(self):
        self.f.admission.cas = True
        original = self.f.admission.cas_scope
        retained, calls, targets = [], [], []
        @contextmanager
        def scope(request, sim, client):
            retained.append(request)
            with original(request, sim, client): yield
        def observer(request, observation, token):
            calls.append(token)
            if len(calls) == 5:
                object.__setattr__(retained[0].arguments, 'sim_id', '31')
            return HostModeDeclaration(request, observation['client_id'], token, 'live')
        self.f.admission.cas_scope = scope
        self.f.admission.observe_mode = observer
        self.f.modules['server_commands.argument_helpers'].OptionalTargetParam = targets.append
        with self.assertRaises(NativeRefusal): self.execute('cas.enter')
        self.assertEqual([], targets)
        self.assertEqual(['enter','exit'], self.f.admission.scopes)
        self.no_dispatch()
