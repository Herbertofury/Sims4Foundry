"""Source-only schema-3 owner fixtures. No sockets, game imports or real readers."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'companion-src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion import queue as q, live_wire, wire
from foundry_companion.live_executor import LiveQueueExecutor
from foundry_companion.live_observation import LiveContext, _LiveRowLimitExceeded
from foundry_companion.native import NativeAPI, NativeRefusal
import test_companion_live_observation as fixtures


def request(number=1, **changes):
    values = dict(request_id='{:032x}'.format(number), session_id='2' * 32,
                  process_epoch=q.ProcessEpoch(123, 456), arguments=q.LiveQueueArguments('30'),
                  expected_context=q.LiveQueueExpectedContext('10', '20', '5', 4, '99', 1))
    values.update(changes)
    return q.LiveQueueRequest(**values)


def executor(harness, **changes):
    values = dict(context_reader=harness.read_context, sim_info_reader=harness.read_info,
                  instance_reader=harness.read_instance, admission=harness.admission,
                  session_id='2' * 32, process_epoch=q.ProcessEpoch(123, 456))
    values.update(changes)
    return LiveQueueExecutor(**values)


def diagnostic(reason='row_limit_exceeded'):
    return dict(schema_version=1, outcome='unverified', game_qualified=False,
                read_status='unavailable', observation=None, reason=reason)


def queue():
    return q.CompanionQueue('2' * 32, clock=lambda: 100.0)


def legacy_request(observe=False, **changes):
    values = dict(request_id='9' * 32, session_id='2' * 32,
                  operation='observe' if observe else 'live.set_speed',
                  arguments=q.ObserveArguments() if observe else q.SetSpeedArguments(1),
                  process_epoch=q.ProcessEpoch(123, 456))
    if not observe:
        values.update(expected_zone_id='10', expected_household_id='20',
                      expected_save_slot=4, expected_save_guid='99')
    values.update(changes)
    return q.Request(**values)


def redirect_legacy(req, to_mutation):
    replacement = legacy_request(observe=not to_mutation)
    for name in ('operation', 'arguments', 'expected_zone_id', 'expected_household_id',
                 'expected_save_slot', 'expected_save_guid'):
        object.__setattr__(req, name, getattr(replacement, name))


class LiveRequestTests(unittest.TestCase):
    def test_exact_types_primitive_copy_and_fixed_operation(self):
        epoch, args, context = q.ProcessEpoch(123, 456), q.LiveQueueArguments('30'), request().expected_context
        req = request(process_epoch=epoch, arguments=args, expected_context=context)
        object.__setattr__(epoch, 'pid', 9)
        object.__setattr__(args, 'actor_sim_id', '31')
        object.__setattr__(context, 'zone_generation', 2)
        self.assertEqual(request(), req)
        copied = q.live_request_from_key(q.live_request_key(req))
        self.assertEqual(req, copied)
        self.assertIsNot(req, copied)
        self.assertIsNot(req.process_epoch, copied.process_epoch)
        object.__setattr__(req, 'operation', 'observe')
        with self.assertRaises(q.QueueError): q.live_request_key(req)

    def test_subclasses_invalid_modes_contexts_ids_and_ttls_reject(self):
        class RequestSubclass(q.LiveQueueRequest): pass
        class ArgSubclass(q.LiveQueueArguments): pass
        class ContextSubclass(q.LiveQueueExpectedContext): pass
        with self.assertRaises(q.QueueError): ArgSubclass('30')
        with self.assertRaises(q.QueueError): ContextSubclass('10', '20', '5', 4, '99', 1)
        for change in [dict(process_epoch=None), dict(ttl_seconds=True), dict(ttl_seconds=float('nan')),
                       dict(ttl_seconds=float('inf')), dict(ttl_seconds=0), dict(ttl_seconds=31)]:
            with self.subTest(change=change), self.assertRaises(q.QueueError): request(**change)
        with self.assertRaises(q.QueueError):
            RequestSubclass('1' * 32, '2' * 32, q.ProcessEpoch(123, 456),
                            q.LiveQueueArguments('30'), request().expected_context)
        for mode in ('build', 'cas', 'loading', 'menu', 'unknown', None, False):
            with self.subTest(mode=mode), self.assertRaises(q.QueueError):
                q.LiveQueueExpectedContext('10', '20', '5', 4, '99', 1, mode)
        for actor in ('0', '030', '\u0663\u0660', '18446744073709551616', None, 30, True):
            with self.subTest(actor=actor), self.assertRaises(q.QueueError): q.LiveQueueArguments(actor)

    def test_classifier_is_explicit_and_shadowed_methods_cannot_admit(self):
        self.assertFalse(q.operation_is_mutation(request()))
        self.assertFalse(q.operation_is_mutation(fixtures.request()))
        req = q.Request('3' * 32, '2' * 32, 'live.set_speed', q.SetSpeedArguments(1),
                        expected_zone_id='10', expected_household_id='20')
        self.assertTrue(q.operation_is_mutation(req))
        for invalid in (object(), None, {'operation': 'live.observe_queue'}):
            with self.assertRaises(q.QueueError): q.operation_is_mutation(invalid)
        req = request()
        object.__setattr__(req, 'validate', lambda: None)
        object.__setattr__(req, 'operation', 'observe')
        with self.assertRaises(q.QueueError): queue().submit(req)

    def test_ttl_numeric_kind_changes_queue_intent(self):
        pending = queue()
        pending.submit(request(ttl_seconds=5))
        with self.assertRaises(q.QueueError) as error: pending.submit(request(ttl_seconds=5.0))
        self.assertEqual('request_conflict', error.exception.code)
        self.assertEqual('request_conflict', pending.queue_status().mutation_blocked_reason)


class LiveExecutorTests(unittest.TestCase):
    def test_default_other_scopes_and_missing_binding_make_no_queries(self):
        class OtherScopes:
            def require_read(self, req): return True
            def require_mutation(self, req, ctx): return True
            def require_build_read(self, req, ctx): return True
        for settings in (dict(admission=None), dict(admission=OtherScopes()),
                         dict(session_id=None, process_epoch=None), dict(session_id='3' * 32),
                         dict(process_epoch=q.ProcessEpoch(123, 457))):
            h = fixtures.Harness()
            with self.subTest(settings=settings), self.assertRaises(NativeRefusal):
                executor(h, **settings).execute(request())
            self.assertEqual([], h.calls)

    def test_admission_sees_distinct_complete_detached_request(self):
        h, req = fixtures.Harness(), request()
        result = executor(h).execute(req)
        view, context, actor = h.admission.calls[0]
        self.assertIs(type(view), q.LiveQueueRequest)
        self.assertEqual('live.observe_queue', view.operation)
        self.assertEqual(q.live_request_key(req), q.live_request_key(view))
        self.assertIsNot(req, view)
        self.assertEqual('30', actor)
        self.assertEqual('5', context.client_id)
        self.assertEqual('observed', result['read_status'])
        self.assertEqual({'schema_version', 'outcome', 'game_qualified', 'read_status',
                          'observation', 'reason'}, set(result))
        self.assertEqual('unverified', result['outcome'])
        self.assertFalse(result['game_qualified'])

    def test_only_exact_true_admits(self):
        for verdict in (False, None, 1, 'yes'):
            h = fixtures.Harness()
            h.admission.verdict = verdict
            with self.assertRaises(NativeRefusal): executor(h).execute(request())
            self.assertEqual([], h.calls)

    def test_each_admission_alias_change_is_detected_before_queries(self):
        changes = [lambda req, ctx: object.__setattr__(req.arguments, 'actor_sim_id', '31'),
                   lambda req, ctx: object.__setattr__(req.process_epoch, 'pid', 124),
                   lambda req, ctx: object.__setattr__(req, 'ttl_seconds', 5),
                   lambda req, ctx: object.__setattr__(req, 'operation', 'observe'),
                   lambda req, ctx: object.__setattr__(ctx, 'client_id', '6')]
        for name, value in [('zone_id', '11'), ('household_id', '21'), ('client_id', '6'),
                            ('save_slot', 5), ('save_guid', '100'), ('zone_generation', 2), ('mode', 'cas')]:
            changes.append(lambda req, ctx, n=name, v=value: object.__setattr__(req.expected_context, n, v))
        for change in changes:
            h = fixtures.Harness()
            h.admission.callback = lambda req, ctx, actor: change(req, ctx)
            with self.subTest(change=change), self.assertRaises(NativeRefusal): executor(h).execute(request())
            self.assertEqual([], h.calls)

    def test_original_and_retained_alias_changes_during_sampling_discard_dto(self):
        for stage in ('context', 'info', 'info_id', 'instance', 'sim_id', 'queue',
                      'iter_queued', 'next_queued', 'guid', 'runtime'):
            for target in ('original', 'view', 'context'):
                h, req = fixtures.Harness(), request()
                h.queued = fixtures.ProbeIterator(h, 'queued', [fixtures.ProbeRow(h)])
                def mutate(value):
                    view, context, actor = h.admission.calls[0]
                    selected = req if target == 'original' else view
                    if target == 'context': object.__setattr__(context, 'zone_generation', 2)
                    else: object.__setattr__(selected, 'ttl_seconds', 5)
                    return value
                h.handlers[stage] = mutate
                with self.subTest(stage=stage, target=target), self.assertRaises(NativeRefusal):
                    executor(h).execute(req)

    def test_every_actual_context_dimension_and_mode_gates_actor_queries(self):
        changes = [('session_id', '3' * 32), ('process_epoch', q.ProcessEpoch(123, 457)),
                   ('zone_id', '11'), ('household_id', '21'), ('client_id', '6'),
                   ('save_slot', 5), ('save_guid', '100'), ('zone_generation', 2)]
        for name, value in changes:
            h = fixtures.Harness()
            h.ctx = replace(h.ctx, **{name: value})
            with self.subTest(name=name), self.assertRaises(NativeRefusal): executor(h).execute(request())
            self.assertNotIn('info', h.calls)
        for mode in ('cas', 'build', 'loading', 'menu', 'unknown', None, False):
            h = fixtures.Harness()
            h.mode = mode
            with self.subTest(mode=mode), self.assertRaises(NativeRefusal): executor(h).execute(request())
            self.assertNotIn('info', h.calls)

    def test_zero_one_and_49_rows_keep_every_split_and_available_mask(self):
        for count in (0, 1, 49):
            for split in range(count + 1):
                for guid, runtime in ((1, 1), (None, 2**64 - 1), (2**53 + 1, None), (None, None)):
                    h = fixtures.Harness()
                    rows = [fixtures.ProbeRow(h, guid, runtime) for _ in range(count)]
                    h.queued = fixtures.ProbeIterator(h, 'queued', rows[:split])
                    h.running = fixtures.ProbeIterator(h, 'running', rows[split:])
                    result = executor(h).execute(request())
                    dto = result['observation']
                    self.assertEqual(split, len(dto['queued']['rows']))
                    self.assertEqual(count - split, len(dto['running']['rows']))
                    live_wire.preview_result_receipt(result, request())

    def test_50_rows_discard_whole_read_and_overflow_fields_are_never_read(self):
        class Overflow:
            def __getattribute__(self, name): raise AssertionError('overflow row field read')
        for split in (0, 1, 25, 49, 50):
            h = fixtures.Harness()
            rows = [fixtures.ProbeRow(h) for _ in range(49)] + [Overflow()]
            h.queued = fixtures.ProbeIterator(h, 'queued', rows[:split])
            h.running = fixtures.ProbeIterator(h, 'running', rows[split:])
            api = executor(h)
            self.assertEqual(diagnostic(), api.execute(request()))
            self.assertEqual(50, h.queued.yields + h.running.yields)
            self.assertEqual(49, h.calls.count('guid'))
            self.assertNotIn('_request_guard', api._observer.__dict__)

    def test_overflow_exception_is_typed_static_and_standalone_keeps_64(self):
        h = fixtures.Harness()
        h.queued = fixtures.ProbeIterator(h, 'queued', [fixtures.ProbeRow(h)] * 50)
        with self.assertRaises(_LiveRowLimitExceeded): h.api.observe_request(request())
        h = fixtures.Harness()
        h.queued = fixtures.ProbeIterator(h, 'queued', [fixtures.ProbeRow(h)] * 64)
        self.assertEqual(64, len(h.observe()['queued']['rows']))
        h = fixtures.Harness()
        h.queued = fixtures.ProbeIterator(h, 'queued', [fixtures.ProbeRow(h)] * 65)
        with self.assertRaises(NativeRefusal) as caught: h.observe()
        self.assertIsNot(type(caught.exception), _LiveRowLimitExceeded)

    def test_context_loss_on_overflow_wins_over_read_unavailable(self):
        h = fixtures.Harness()
        h.queued = fixtures.ProbeIterator(h, 'queued', [fixtures.ProbeRow(h)] * 50)
        def change(value):
            if h.queued.next_calls == 50: h.ctx = replace(h.ctx, zone_generation=2)
            return value
        h.handlers['next_queued'] = change
        with self.assertRaises(NativeRefusal) as caught: executor(h).execute(request())
        self.assertIsNot(type(caught.exception), _LiveRowLimitExceeded)
        self.assertEqual(49, h.calls.count('guid'))

    def test_owner_reentrancy_control_exceptions_and_guard_cleanup(self):
        h = fixtures.Harness()
        api = executor(h)
        outcomes = []
        def wrong_thread():
            try: api.execute(request())
            except NativeRefusal as error: outcomes.append(str(error))
        thread = threading.Thread(target=wrong_thread)
        thread.start(); thread.join()
        self.assertEqual(['owner_mismatch'], outcomes)
        self.assertEqual([], h.calls)
        for error in (KeyboardInterrupt(), SystemExit()):
            h.handlers['info'] = fixtures.fail(error)
            with self.assertRaises(type(error)): api.execute(request())
            self.assertNotIn('_request_guard', api._observer.__dict__)
            self.assertFalse(api._observer._busy)
        h.handlers.clear()
        def reenter(value):
            with self.assertRaises(NativeRefusal): api.execute(request())
            return value
        h.handlers['info'] = reenter
        self.assertEqual('observed', api.execute(request())['read_status'])

    def test_legitimate_queue_and_complete_wire_budget_failures_are_diagnostics(self):
        for module, name, limit in ((q, 'MAX_RESULT_NODES', 40), (wire, 'MAX_FRAME_BYTES', 700)):
            h = fixtures.Harness()
            with patch.object(module, name, limit):
                self.assertEqual(diagnostic('result_budget_exceeded'), executor(h).execute(request()))

    def test_malformed_candidate_and_unexpected_failures_are_not_budget_diagnostics(self):
        h = fixtures.Harness()
        api = executor(h)
        for observation in ({}, {'opaque': 'x' * 100000}):
            with patch.object(api._observer, 'observe_request', return_value=observation):
                with self.assertRaises(wire.ProtocolError): api.execute(request())
        with patch.object(api._observer, 'observe_request', side_effect=RuntimeError('private')):
            with self.assertRaises(RuntimeError): api.execute(request())

    def test_native_four_operation_executor_rejects_new_request_before_imports(self):
        class Services:
            def __getattribute__(self, name): raise AssertionError('native service inspected')
        def load(name): raise AssertionError('command imported')
        native = NativeAPI(Services(), command_loader=load)
        with self.assertRaises(NativeRefusal): native.execute(request())


class LiveQueueTests(unittest.TestCase):
    def test_original_and_callback_views_are_detached_from_retained_baseline(self):
        pending, req, h = queue(), request(), fixtures.Harness()
        pending.submit(req)
        object.__setattr__(req.arguments, 'actor_sim_id', '31')
        object.__setattr__(req.expected_context, 'client_id', '6')
        views = []
        def admit(view): views.append(view); return True
        def execute(view):
            self.assertIsNot(views[0], view)
            object.__setattr__(views[0].arguments, 'actor_sim_id', '32')
            return executor(h).execute(view)
        done = pending.drain(execute, admit)
        self.assertEqual('unverified', done.state)
        self.assertEqual('30', done.result['observation']['actor_sim_id'])
        self.assertEqual(done, pending.submit(request()))

    def test_changed_admission_view_is_refused_without_execution(self):
        pending = queue()
        pending.submit(request())
        calls = []
        def admit(view): object.__setattr__(view, 'ttl_seconds', 5); return True
        done = pending.drain(lambda view: calls.append(view), admit)
        self.assertEqual('refused', done.state)
        self.assertEqual('host_dispatch_denied', done.reason)
        self.assertEqual([], calls)

    def test_frozen_candidate_validation_never_retains_invalid_result_even_deferred(self):
        changes = [lambda result: result.update(outcome='verified'),
                   lambda result: result.update(extra=True),
                   lambda result: result['observation'].update(actor_sim_id='31'),
                   lambda result: result['observation']['context'].update(client_id='6'),
                   lambda result: result['observation'].update(independent_evidence=True)]
        for change in changes:
            for defer in (False, True):
                pending, h = queue(), fixtures.Harness()
                pending.submit(request())
                def execute(view):
                    result = executor(h).execute(view)
                    change(result)
                    if defer: pending._lock.acquire()
                    return result
                done = pending.drain(execute)
                self.assertEqual(('unknown', 'invalid_execution_result', None),
                                 (done.state, done.reason, done.result))
                if defer:
                    self.assertEqual('started', pending._entries[request().request_id].snapshot.state)
                    pending._lock.release()
                self.assertIsNone(pending.status(request().request_id).result)
                self.assertIsNone(pending.queue_status().mutation_blocked_reason)

    def test_execution_view_change_cannot_bind_result_to_replacement_actor(self):
        pending = queue(); pending.submit(request())
        def execute(view):
            object.__setattr__(view.arguments, 'actor_sim_id', '31')
            return diagnostic()
        done = pending.drain(execute)
        self.assertEqual(('unknown', 'invalid_execution_result', None),
                         (done.state, done.reason, done.result))

    def test_unknown_and_unavailable_reads_never_replay_or_latch_mutation(self):
        for execute in (lambda view: diagnostic(), lambda view: {},
                        lambda view: (_ for _ in ()).throw(RuntimeError('private'))):
            pending = queue(); req = request(); pending.submit(req)
            done = pending.drain(execute)
            self.assertIn(done.state, ('unknown', 'unverified'))
            self.assertEqual(done, pending.submit(req))
            self.assertIsNone(pending.drain(lambda view: self.fail('replayed')))
            self.assertIsNone(pending.queue_status().mutation_blocked_reason)

    def test_prior_mutation_uncertainty_stays_blocked_after_live_read(self):
        pending = queue()
        mutation = q.Request('9' * 32, '2' * 32, 'live.set_speed', q.SetSpeedArguments(1),
                             expected_zone_id='10', expected_household_id='20')
        pending.submit(mutation)
        pending.drain(lambda view: {})
        self.assertEqual('pending', pending.submit(request()).state)
        self.assertEqual('unverified', pending.drain(lambda view: diagnostic()).state)
        self.assertEqual('unresolved_mutation', pending.queue_status().mutation_blocked_reason)
        denied = pending.submit(replace(mutation, request_id='8' * 32))
        self.assertEqual(('refused', 'unresolved_mutation'), (denied.state, denied.reason))

    def test_legacy_caller_cannot_redirect_read_or_mutation_across_latched_gate(self):
        for observe in (False, True):
            pending, req = queue(), legacy_request(observe=observe)
            baseline = q._legacy_request_key(req)
            pending.submit(req)
            redirect_legacy(req, to_mutation=observe)
            pending.block_mutations()
            calls = []
            def execute(view):
                calls.append(q._legacy_request_key(view))
                return {'outcome': 'unverified'}
            final = pending.drain(execute)
            if observe:
                self.assertEqual('unverified', final.state)
                self.assertEqual([baseline], calls)
            else:
                self.assertEqual(('refused', 'unresolved_mutation'), (final.state, final.reason))
                self.assertEqual([], calls)
            self.assertEqual('unresolved_mutation', pending.queue_status().mutation_blocked_reason)

    def test_legacy_admission_cannot_redirect_either_operation_direction(self):
        for observe in (False, True):
            pending = queue(); pending.submit(legacy_request(observe=observe))
            def admit(view):
                redirect_legacy(view, to_mutation=observe)
                pending.block_mutations()
                return True
            final = pending.drain(lambda view: self.fail('changed admission dispatched'), admit)
            self.assertEqual(('refused', 'host_dispatch_denied'), (final.state, final.reason))
            self.assertEqual('unresolved_mutation', pending.queue_status().mutation_blocked_reason)

    def test_complete_legacy_intent_detaches_caller_and_retained_admission_aliases(self):
        pending, req = queue(), legacy_request()
        baseline = q._legacy_request_key(req)
        pending.submit(req)
        object.__setattr__(req.arguments, 'speed', 3)
        object.__setattr__(req.process_epoch, 'creation_filetime', 457)
        object.__setattr__(req, 'expected_save_slot', 5)
        object.__setattr__(req, 'ttl_seconds', 5)
        retained = []
        def admit(view):
            retained.append(view)
            self.assertEqual(baseline, q._legacy_request_key(view))
            return True
        def execute(view):
            self.assertIsNot(retained[0], view)
            self.assertIsNot(retained[0].arguments, view.arguments)
            self.assertIsNot(retained[0].process_epoch, view.process_epoch)
            redirect_legacy(retained[0], False)
            object.__setattr__(retained[0].process_epoch, 'pid', 124)
            self.assertEqual(baseline, q._legacy_request_key(view))
            return {'outcome': 'verified'}
        final = pending.drain(execute, admit)
        self.assertEqual('verified', final.state)
        self.assertEqual(final, pending.submit(legacy_request()))

    def test_every_legacy_binding_field_and_ttl_kind_participates_in_intent(self):
        changes = [('arguments', q.SetSpeedArguments(2)), ('ttl_seconds', 5), ('ttl_seconds', 6.0),
                   ('expected_zone_id', '11'), ('expected_household_id', '21'),
                   ('process_epoch', q.ProcessEpoch(124, 456)), ('process_epoch', q.ProcessEpoch(123, 457)),
                   ('process_epoch', None), ('expected_save_slot', 5), ('expected_save_guid', '100')]
        for name, value in changes:
            pending = queue(); pending.submit(legacy_request())
            with self.subTest(name=name, value=value), self.assertRaises(q.QueueError) as caught:
                pending.submit(legacy_request(**{name: value}))
            self.assertEqual('request_conflict', caught.exception.code)
            pending = queue(); pending.submit(legacy_request())
            def admit(view): object.__setattr__(view, name, value); return True
            final = pending.drain(lambda view: self.fail('changed legacy field dispatched'), admit)
            self.assertEqual(('refused', 'host_dispatch_denied'), (final.state, final.reason))
        for name, value in (('request_id', '8' * 32), ('session_id', '3' * 32)):
            pending = queue(); pending.submit(legacy_request())
            def admit(view): object.__setattr__(view, name, value); return True
            final = pending.drain(lambda view: self.fail('changed identity dispatched'), admit)
            self.assertEqual(('refused', 'host_dispatch_denied'), (final.state, final.reason))
        pending = queue(); pending.submit(legacy_request())
        with self.assertRaises(q.QueueError):
            pending.submit(legacy_request(expected_save_slot=None, expected_save_guid=None))
        original = legacy_request(process_epoch=None, expected_save_slot=None, expected_save_guid=None)
        pending = queue(); pending.submit(original)
        seen = []
        pending.drain(lambda view: seen.append(q._legacy_request_key(view)) or {'outcome': 'verified'})
        self.assertEqual([q._legacy_request_key(original)], seen)

    def test_legacy_baseline_never_invokes_shadowed_methods(self):
        req = legacy_request()
        def poison(*args): self.fail('caller-owned method invoked')
        object.__setattr__(req, 'validate', poison)
        object.__setattr__(req, 'arguments_dict', poison)
        object.__setattr__(req.arguments, '__post_init__', poison)
        object.__setattr__(req.process_epoch, '__post_init__', poison)
        pending = queue(); pending.submit(req)
        self.assertEqual('verified', pending.drain(lambda view: {'outcome': 'verified'}).state)
        object.__setattr__(req.arguments, 'speed', True)
        with self.assertRaises(q.QueueError): queue().submit(req)

    def test_legacy_admission_classification_survives_changed_operations_and_all_exits(self):
        def mutation(number):
            return q.Request('{:032x}'.format(number), '2' * 32, 'live.set_speed',
                             q.SetSpeedArguments(1), expected_zone_id='10', expected_household_id='20')
        for operation in ('observe', 'bogus'):
            for error in (None, RuntimeError, KeyboardInterrupt, SystemExit):
                for defer in (False, True):
                    pending = queue()
                    pending.submit(mutation(9))
                    def execute(view):
                        object.__setattr__(view, 'operation', operation)
                        if defer: pending._lock.acquire()
                        if error: raise error('private fixture')
                        return {'outcome': 'unverified'}
                    with self.subTest(operation=operation, error=error, defer=defer):
                        if error in (KeyboardInterrupt, SystemExit):
                            with self.assertRaises(error): pending.drain(execute)
                        else:
                            final = pending.drain(execute)
                            self.assertEqual('unknown', final.state)
                        if defer: pending._lock.release()
                        final = pending.status(mutation(9).request_id)
                        self.assertEqual('unknown', final.state)
                        self.assertEqual('unresolved_mutation', pending.queue_status().mutation_blocked_reason)
                        self.assertEqual('refused', pending.submit(mutation(8)).state)
                        self.assertIsNone(pending.drain(lambda req: self.fail('uncertain mutation replay')))

    def test_both_later_mutation_gates_use_admission_classification(self):
        def mutation(number):
            return q.Request('{:032x}'.format(number), '2' * 32, 'live.set_speed',
                             q.SetSpeedArguments(1), expected_zone_id='10', expected_household_id='20')
        for operation in ('observe', 'bogus'):
            pending, req = queue(), mutation(9)
            pending.submit(req)
            object.__setattr__(req, 'operation', operation)
            pending.block_mutations()
            final = pending.drain(lambda view: self.fail('first gate bypassed'))
            self.assertEqual(('refused', 'unresolved_mutation'), (final.state, final.reason))
            pending = queue(); pending.submit(mutation(9))
            def admit(view):
                object.__setattr__(view, 'operation', operation)
                pending.block_mutations()
                return True
            final = pending.drain(lambda view: self.fail('second gate bypassed'), admit)
            self.assertEqual(('refused', 'host_dispatch_denied'), (final.state, final.reason))

    def test_pending_started_and_refused_snapshot_views_cannot_rewrite_retained_state(self):
        pending, req = queue(), request()
        original = pending.submit(req)
        for view in (original, pending.submit(req), pending.status(req.request_id)):
            object.__setattr__(view, 'state', 'verified')
            object.__setattr__(view, 'expires_at', 1.0)
            object.__setattr__(view, 'request_id', 'f' * 32)
            object.__setattr__(view, 'result', {'outcome': 'verified', 'forged': True})
            retained = pending.status(req.request_id)
            self.assertEqual(('pending', 105.0, req.request_id, None),
                             (retained.state, retained.expires_at, retained.request_id, retained.result))
        def execute(view):
            started = pending.status(req.request_id)
            self.assertEqual('started', started.state)
            object.__setattr__(started, 'state', 'refused')
            object.__setattr__(started, 'reason', 'forged')
            object.__setattr__(started, 'result', {'outcome': 'verified'})
            self.assertEqual(('started', None, None),
                             (pending.status(req.request_id).state, pending.status(req.request_id).reason,
                              pending.status(req.request_id).result))
            return diagnostic()
        self.assertEqual('unverified', pending.drain(execute).state)
        pending = queue(); pending.close()
        refused = pending.submit(request())
        object.__setattr__(refused, 'state', 'pending')
        object.__setattr__(refused, 'reason', None)
        self.assertEqual(('refused', 'host_shutdown'),
                         (pending.status(req.request_id).state, pending.submit(request()).reason))
        pending = queue(); pending.submit(req)
        refused = pending.drain(lambda view: self.fail('admission should refuse'), lambda view: False)
        object.__setattr__(refused, 'state', 'pending')
        self.assertEqual('refused', pending.status(req.request_id).state)

    def test_legacy_public_snapshots_are_detached_without_changing_result_contract(self):
        req = q.Request('9' * 32, '2' * 32, 'live.set_speed', q.SetSpeedArguments(1),
                        expected_zone_id='10', expected_household_id='20')
        pending = queue()
        for view in (pending.submit(req), pending.submit(req), pending.status(req.request_id)):
            object.__setattr__(view, 'state', 'verified')
            object.__setattr__(view, 'expires_at', 0)
            self.assertEqual(('pending', 105.0),
                             (pending.status(req.request_id).state, pending.status(req.request_id).expires_at))
        candidate = {'outcome': 'unverified', 'detail': {'rows': [1, 2]}}
        def execute(view):
            started = pending.status(req.request_id)
            object.__setattr__(started, 'state', 'verified')
            object.__setattr__(started, 'result', {'outcome': 'verified'})
            self.assertEqual('started', pending.status(req.request_id).state)
            return candidate
        final = pending.drain(execute)
        candidate['detail']['rows'].append(3)
        for view in (final, pending.status(req.request_id), pending.submit(req)):
            self.assertEqual((1, 2), view.result['detail']['rows'])
            with self.assertRaises(TypeError): view.result['detail']['rows'] = ()
            object.__setattr__(view, 'state', 'verified')
            object.__setattr__(view, 'result', {'outcome': 'verified'})
        retained = pending.status(req.request_id)
        self.assertEqual('unverified', retained.state)
        self.assertEqual((1, 2), retained.result['detail']['rows'])
        self.assertEqual('unresolved_mutation', pending.queue_status().mutation_blocked_reason)
        pending = queue(); pending.close()
        refused = pending.submit(req)
        object.__setattr__(refused, 'state', 'pending')
        object.__setattr__(refused, 'reason', None)
        self.assertEqual(('refused', 'host_shutdown'),
                         (pending.status(req.request_id).state, pending.submit(req).reason))

    def test_terminal_and_deferred_completion_snapshots_are_detached_and_results_frozen(self):
        for defer in (False, True):
            pending, req = queue(), request()
            pending.submit(req)
            candidate = diagnostic()
            def execute(view):
                if defer: pending._lock.acquire()
                return candidate
            final = pending.drain(execute)
            # Candidate data and returned snapshot are two separate alias edges.
            candidate['outcome'] = 'verified'
            candidate['game_qualified'] = True
            object.__setattr__(final, 'state', 'verified')
            object.__setattr__(final, 'result', {'outcome': 'verified', 'forged': True})
            if defer: pending._lock.release()
            for get in (lambda: pending.status(req.request_id), lambda: pending.submit(req)):
                view = get()
                self.assertEqual('unverified', view.state)
                self.assertEqual(diagnostic(), q._thaw_result(view.result))
                with self.assertRaises(TypeError): view.result['outcome'] = 'verified'
                object.__setattr__(view, 'result', {'outcome': 'verified'})
                object.__setattr__(view, 'state', 'refused')
            retained = pending.status(req.request_id)
            self.assertEqual('unverified', retained.state)
            self.assertEqual(diagnostic(), q._thaw_result(retained.result))
            self.assertIsNone(pending.drain(lambda view: self.fail('terminal replayed')))

    def test_reflected_mapping_comparison_cannot_mutate_private_frozen_results(self):
        class Rewrite:
            def __init__(self, name, value): self.name, self.value = name, value
            def __eq__(self, other):
                if type(other) is dict: other[self.name] = self.value
                return False
        for live in (False, True):
            req = request() if live else q.Request('9' * 32, '2' * 32, 'observe', q.ObserveArguments())
            for defer in (False, True):
                pending = queue(); pending.submit(req)
                if live:
                    h = fixtures.Harness()
                    h.queued = fixtures.ProbeIterator(h, 'queued', [fixtures.ProbeRow(h)])
                    raw = executor(h).execute(req)
                else:
                    raw = {'outcome': 'unverified', 'nested': {'rows': [{'value': 1}]}}
                def nested(result):
                    return (result['observation']['queued']['rows'][0]['tuning_guid64'] if live
                            else result['nested']['rows'][0])
                def execute(view):
                    if defer: pending._lock.acquire()
                    return raw
                final = pending.drain(execute)
                final.result == Rewrite('outcome', 'verified')
                nested(final.result) == Rewrite('value', 'forged')
                if defer: pending._lock.release()
                for get in (lambda: pending.status(req.request_id), lambda: pending.submit(req)):
                    view = get()
                    self.assertEqual('unverified', view.result['outcome'])
                    view.result == Rewrite('outcome', 'verified')
                    self.assertEqual('100' if live else 1, nested(view.result)['value'])
                    nested(view.result) == Rewrite('value', 'forged')
                self.assertEqual(raw, q._thaw_result(pending.status(req.request_id).result))

    def test_lifecycle_expiry_capacity_closure_and_control_exception_keep_old_gates(self):
        time = [100.0]
        pending = q.CompanionQueue('2' * 32, pending_capacity=1, history_capacity=2, clock=lambda: time[0])
        first = pending.submit(request())
        self.assertEqual('pending_full', pending.submit(request(2)).reason)
        self.assertEqual('history_full', pending.submit(request(3)).reason)
        time[0] = first.expires_at
        self.assertEqual('expired', pending.drain(lambda req: self.fail('expired executed')).reason)
        pending = queue(); pending.submit(request()); pending.close()
        self.assertEqual('host_shutdown', pending.drain(lambda req: self.fail('closed executed')).reason)
        pending = queue(); pending.submit(request())
        def interrupt(view): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): pending.drain(interrupt)
        self.assertEqual('unknown', pending.status(request().request_id).state)
        self.assertIsNone(pending.queue_status().mutation_blocked_reason)


if __name__ == '__main__': unittest.main()
