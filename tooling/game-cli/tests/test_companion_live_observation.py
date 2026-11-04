"""Fabricated source-only Live readback tests; no game/profile/process access."""
import ast
from dataclasses import replace
import gc
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import weakref

SOURCE = Path(__file__).resolve().parents[1] / 'companion-src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion import live_observation as live
from foundry_companion.live_observation import LiveContext, LiveQueueObserver
from foundry_companion.native import DenyAdmission, HostModeDeclaration, NativeRefusal
from foundry_companion.queue import (MAX_UINT64, ObserveArguments, ProcessEpoch,
                                     Request, SetSpeedArguments)


def request():
    return Request('1' * 32, '2' * 32, 'observe', ObserveArguments(),
                   process_epoch=ProcessEpoch(123, 456))


def context():
    return LiveContext('2' * 32, ProcessEpoch(123, 456), '10', '20', '5', 4, '99', 1)


class Box(object):
    def __init__(self, **values):
        self.__dict__.update(values)


class Poison(object):
    def bad(self, *args):
        raise AssertionError('PRIVATE_CANARY coercion or inspection')
    __str__ = __repr__ = __int__ = __index__ = __bool__ = __iter__ = bad
    __len__ = __length_hint__ = bad


class Admission(DenyAdmission):
    def __init__(self):
        self.calls = []
        self.callback = None
        self.verdict = True

    def require_live_read(self, req, ctx, actor):
        self.calls.append((req, ctx, actor))
        if self.callback:
            self.callback(req, ctx, actor)
        return self.verdict


class ProbeIterator(object):
    def __init__(self, harness, name, rows=(), infinite=False):
        self.harness, self.name, self.rows = harness, name, rows
        self.infinite, self.position, self.next_calls = infinite, 0, 0
        self.iter_calls, self.yields = 0, 0

    def __iter__(self):
        self.iter_calls += 1
        return self.harness.hook('iter_' + self.name, self)

    def __next__(self):
        self.next_calls += 1
        self.harness.hook('next_' + self.name, None)
        if not self.infinite and self.position >= len(self.rows):
            raise StopIteration
        value = self.rows[0] if self.infinite else self.rows[self.position]
        self.position += 1
        self.yields += 1
        return value

    def __len__(self):
        raise AssertionError('must not ask native length')

    def __length_hint__(self):
        raise AssertionError('must not ask native length hint')


class ProbeInfo(object):
    def __init__(self, harness): self.harness = harness
    @property
    def id(self): return self.harness.hook('info_id', self.harness.actor_id)


class ProbeSim(object):
    def __init__(self, harness): self.harness = harness
    @property
    def sim_id(self): return self.harness.hook('sim_id', self.harness.actor_id)
    @property
    def queue(self): return self.harness.hook('queue', self.harness.queued)
    @property
    def si_state(self): return self.harness.hook('running', self.harness.running)


class ProbeRow(object):
    def __init__(self, harness, guid=100, runtime=200):
        self.harness, self.guid, self.runtime = harness, guid, runtime
    @property
    def guid64(self): return self.harness.hook('guid', self.guid)
    @property
    def id(self): return self.harness.hook('runtime', self.runtime)
    @property
    def target(self): raise AssertionError('must not read target')
    @property
    def label(self): raise AssertionError('must not read label')
    def __repr__(self): raise AssertionError('must not format native object')


class Harness(object):
    def __init__(self):
        self.ctx, self.mode, self.zone_running = context(), 'live', True
        self.actor_id, self.calls, self.handlers = 30, [], {}
        self.info, self.sim = ProbeInfo(self), ProbeSim(self)
        self.queued = ProbeIterator(self, 'queued')
        self.running = ProbeIterator(self, 'running')
        self.admission = Admission()
        self.api = self.make_api()

    def hook(self, stage, value):
        self.calls.append(stage)
        handler = self.handlers.get(stage)
        return handler(value) if handler else value

    def read_context(self):
        return self.hook('context', {'context': self.ctx,
                                    'zone_running': self.zone_running, 'mode': self.mode})

    def read_info(self, actor):
        if type(actor) is not int or actor != self.actor_id:
            raise AssertionError('resolver must receive only explicit actor integer')
        return self.hook('info', self.info)

    def read_instance(self, info):
        if info is not self.info:
            raise AssertionError('instance reader must receive exact selected SimInfo')
        return self.hook('instance', self.sim)

    def make_api(self, **changes):
        values = dict(context_reader=self.read_context, sim_info_reader=self.read_info,
                      instance_reader=self.read_instance, admission=self.admission,
                      session_id='2' * 32, process_epoch=ProcessEpoch(123, 456))
        values.update(changes)
        return LiveQueueObserver(**values)

    def observe(self, **changes):
        values = dict(request=request(), expected_context=context(), explicit_actor_id=str(self.actor_id))
        values.update(changes)
        return self.api.observe(**values)


def fail(error):
    def throwing(value):
        raise error
    return throwing


class LiveObservationTests(unittest.TestCase):
    def setUp(self): self.h = Harness()

    def assert_refusal(self, callback, code=None):
        with self.assertRaises(NativeRefusal) as caught:
            callback()
        if code:
            self.assertEqual(code, str(caught.exception))
        self.assertFalse(self.h.api._busy)
        return caught.exception

    def assert_no_readers(self): self.assertEqual([], self.h.calls)

    def test_empty_is_observed_not_idle_or_action_outcome(self):
        result = self.h.observe()
        self.assertEqual({'status': 'observed'}, result['actor'])
        self.assertEqual({'status': 'observed', 'rows': []}, result['queued'])
        self.assertEqual({'status': 'observed', 'rows': []}, result['running'])
        self.assertEqual('unknown', result['action_outcome'])
        self.assertNotIn('idle', result)
        self.assertEqual(1, len(self.h.admission.calls))
        self.assertEqual(2, self.h.calls.count('info'))
        self.assertEqual(2, self.h.calls.count('instance'))

    def test_default_and_other_scopes_cannot_admit(self):
        for provider in (None, DenyAdmission(), Box(require_read=lambda req: True),
                         Box(require_build_read=lambda *args: True),
                         Box(require_read=lambda req: True, require_build_read=lambda *args: True)):
            api = self.h.make_api(admission=provider)
            self.assert_refusal(lambda: api.observe(request(), context(), '30'),
                                'live_admission_unavailable')
            self.assert_no_readers()

    def test_only_exact_true_admits_without_truthiness(self):
        for verdict in (False, None, 1, 'yes', {}, Poison()):
            self.h.admission.verdict = verdict
            self.assert_refusal(self.h.observe, 'live_admission_denied')
            self.assert_no_readers()

    def test_binding_missing_partial_stale_and_detached(self):
        for session, epoch in ((None, None), ('3' * 32, ProcessEpoch(123, 456)),
                               ('2' * 32, ProcessEpoch(124, 456)),
                               ('2' * 32, ProcessEpoch(123, 457))):
            api = self.h.make_api(session_id=session, process_epoch=epoch)
            self.assert_refusal(lambda: api.observe(request(), context(), '30'), 'binding_mismatch')
        for session, epoch in ((None, ProcessEpoch(123, 456)), ('2' * 32, None)):
            self.assert_refusal(lambda: self.h.make_api(session_id=session, process_epoch=epoch))
        self.assert_no_readers()
        self.assertEqual([], self.h.admission.calls)
        epoch = ProcessEpoch(123, 456)
        api = self.h.make_api(process_epoch=epoch)
        object.__setattr__(epoch, 'pid', 124)
        self.assertEqual('123', api.observe(request(), context(), '30')['context']['process_epoch']['pid'])

    def test_context_constructor_detaches_epoch(self):
        epoch = ProcessEpoch(123, 456)
        ctx = replace(context(), process_epoch=epoch)
        self.assertIsNot(epoch, ctx.process_epoch)
        object.__setattr__(epoch, 'pid', 999)
        self.assertEqual(123, ctx.process_epoch.pid)

    def test_actor_selector_canonical_exact_and_never_coerced(self):
        class Text(str): pass
        for value in (None, '', '0', '-1', '+1', ' 1', '1 ', '01', '\u0661',
                      str(2**64), 30, True, Text('30'), [], set(), iter(()), Poison(), 'Alice'):
            self.assert_refusal(lambda: self.h.observe(explicit_actor_id=value), 'invalid_actor_id')
        self.assert_no_readers()
        self.assertEqual([], self.h.admission.calls)
        for actor in (1, MAX_UINT64):
            h = Harness(); h.actor_id = actor
            self.assertEqual(str(actor), h.observe()['actor_sim_id'])

    def test_request_exact_types_mutation_shape_and_corruption(self):
        class RequestChild(Request): pass
        class EpochChild(ProcessEpoch): pass
        class ArgsChild(ObserveArguments): pass
        bad = [Box(), RequestChild('1' * 32, '2' * 32, 'observe', ObserveArguments()),
               replace(request(), process_epoch=None),
               Request('1' * 32, '2' * 32, 'live.set_speed', SetSpeedArguments(1),
                       expected_zone_id='10', expected_household_id='20',
                       process_epoch=ProcessEpoch(123, 456))]
        for name, value in [('arguments', ArgsChild()), ('arguments', {}),
                            ('expected_zone_id', '10'), ('expected_household_id', '20'),
                            ('expected_save_slot', 4), ('expected_save_guid', '99'),
                            ('ttl_seconds', True), ('ttl_seconds', float('nan')),
                            ('ttl_seconds', float('inf')), ('ttl_seconds', 0),
                            ('ttl_seconds', 31), ('request_id', Poison()),
                            ('operation', Poison()), ('process_epoch', EpochChild(123, 456))]:
            req = request(); object.__setattr__(req, name, value); bad.append(req)
        req = request(); object.__delattr__(req, 'request_id'); bad.append(req)
        for req in bad:
            self.assert_refusal(lambda: self.h.observe(request=req), 'invalid_observe_request')
        self.assert_no_readers()
        self.assertEqual([], self.h.admission.calls)

    def test_stale_request_and_context_binding(self):
        for req in (replace(request(), session_id='3' * 32),
                    replace(request(), process_epoch=ProcessEpoch(124, 456)),
                    replace(request(), process_epoch=ProcessEpoch(123, 457))):
            self.assert_refusal(lambda: self.h.observe(request=req), 'binding_mismatch')
        self.assert_refusal(lambda: self.h.observe(expected_context=replace(context(),
                            process_epoch=ProcessEpoch(123, 457))), 'binding_mismatch')
        self.assert_no_readers()

    def test_context_rejects_subclasses_missing_and_invalid_fields(self):
        class ContextChild(LiveContext): pass
        class EpochChild(ProcessEpoch): pass
        class Text(str): pass
        with self.assertRaises(NativeRefusal):
            ContextChild('2' * 32, ProcessEpoch(123, 456), '10', '20', '5', 4, '99', 1)
        cases = [('session_id', 'X' * 32), ('zone_id', None), ('zone_id', '01'),
                 ('zone_id', Text('10')), ('household_id', True), ('client_id', 5),
                 ('save_slot', None), ('save_slot', True), ('save_slot', -1),
                 ('save_slot', 2**64), ('save_guid', None), ('save_guid', '099'),
                 ('save_guid', 99), ('zone_generation', 0), ('zone_generation', True),
                 ('zone_generation', 2**64), ('process_epoch', EpochChild(123, 456))]
        for name, value in cases:
            ctx = context(); object.__setattr__(ctx, name, value)
            self.assert_refusal(lambda: self.h.observe(expected_context=ctx), 'invalid_live_context')
        ctx = context(); object.__delattr__(ctx, 'process_epoch')
        self.assert_refusal(lambda: self.h.observe(expected_context=ctx), 'invalid_live_context')
        self.assert_no_readers()
        self.assertEqual([], self.h.admission.calls)

    def test_admission_gets_fresh_exact_detached_views(self):
        req, ctx = request(), context()
        self.h.observe(request=req, expected_context=ctx)
        admitted, expected, actor = self.h.admission.calls[-1]
        self.assertEqual(req, admitted); self.assertIsNot(req, admitted)
        self.assertIsNot(req.arguments, admitted.arguments)
        self.assertIsNot(req.process_epoch, admitted.process_epoch)
        self.assertEqual(ctx, expected); self.assertIsNot(ctx, expected)
        self.assertIsNot(ctx.process_epoch, expected.process_epoch)
        self.assertEqual('30', actor)

    def test_admission_view_mutations_are_rejected_before_any_reader(self):
        mutations = [lambda req, ctx: object.__setattr__(req, 'request_id', '3' * 32),
                     lambda req, ctx: object.__setattr__(req, 'ttl_seconds', 5),
                     lambda req, ctx: object.__setattr__(req.process_epoch, 'pid', 124),
                     lambda req, ctx: object.__setattr__(ctx.process_epoch, 'creation_filetime', 457),
                     lambda req, ctx: object.__setattr__(ctx, 'zone_generation', 2),
                     lambda req, ctx: object.__setattr__(ctx, 'save_slot', True)]
        for mutation in mutations:
            self.h.admission.callback = lambda req, ctx, actor: mutation(req, ctx)
            self.assert_refusal(self.h.observe)
            self.assert_no_readers()

    def test_original_and_prior_admission_aliases_cannot_redirect_baseline(self):
        req, ctx = request(), context()
        def mutate_original(view, expected, actor):
            object.__setattr__(req.process_epoch, 'pid', 999)
            object.__setattr__(ctx, 'zone_generation', 100)
        self.h.admission.callback = mutate_original
        self.assertEqual('1', self.h.observe(request=req, expected_context=ctx)['context']['zone_generation'])
        old_req, old_ctx, actor = self.h.admission.calls[-1]
        self.h.admission.callback = lambda *args: (object.__setattr__(old_req.process_epoch, 'pid', 5),
                                                  object.__setattr__(old_ctx, 'save_slot', 8))
        self.assertEqual('4', self.h.observe()['context']['save_slot'])

    def test_owner_thread_object_and_ident_are_both_required(self):
        errors = []
        def run():
            try: self.h.observe()
            except NativeRefusal as error: errors.append(str(error))
        worker = threading.Thread(target=run); worker.start(); worker.join()
        self.assertEqual(['owner_mismatch'], errors)
        with patch.object(live.threading, 'current_thread', return_value=Box(ident=self.h.api._owner_ident)):
            self.assert_refusal(self.h.observe, 'owner_mismatch')
        original = self.h.api._owner_ident
        self.h.api._owner_ident = original + 1
        self.assert_refusal(self.h.observe, 'owner_mismatch')
        self.h.api._owner_ident = original
        self.assert_no_readers()

    def test_owner_rechecked_after_admission_and_reader_failure(self):
        self.h.admission.callback = lambda *args: setattr(self.h.api, '_owner_ident', -1)
        self.assert_refusal(self.h.observe, 'owner_mismatch')
        self.assert_no_readers()
        h = Harness()
        def change(value):
            h.api._owner_ident = -1
            raise ValueError('PRIVATE_CANARY')
        h.handlers['info'] = change
        with self.assertRaisesRegex(NativeRefusal, '^owner_mismatch$'): h.observe()
        self.assertFalse(h.api._busy)

    def test_reentry_is_refused_at_every_external_stage_without_nested_read(self):
        stages = ('admission', 'context', 'info', 'info_id', 'instance', 'sim_id',
                  'queue', 'running', 'iter_queued', 'next_queued', 'guid', 'runtime')
        for stage in stages:
            h = Harness(); h.queued.rows = [ProbeRow(h)]
            nested = []
            def reenter(value):
                before = len(h.calls)
                with self.assertRaisesRegex(NativeRefusal, '^reentrant_observation$'): h.observe()
                self.assertEqual(before, len(h.calls))
                self.assertTrue(h.api._busy)
                nested.append(True)
                return value
            if stage == 'admission': h.admission.callback = lambda *args: reenter(None)
            else: h.handlers[stage] = reenter
            self.assertEqual('observed', h.observe()['actor']['status'])
            self.assertTrue(nested, stage)
            self.assertFalse(h.api._busy)

    def test_context_shape_and_exact_mode_are_not_native_mutation_declarations(self):
        class Text(str): pass
        for mode in ('cas', 'build_buy', 'loading', 'menu', 'unknown', 'LIVE', None, True,
                     Text('live'), HostModeDeclaration(request(), '5', object(), 'live')):
            h = Harness(); h.mode = mode
            with self.assertRaises(NativeRefusal): h.observe()
            self.assertNotIn('info', h.calls)
        for value in (False, 1, None):
            h = Harness(); h.zone_running = value
            with self.assertRaises(NativeRefusal): h.observe()
            self.assertNotIn('info', h.calls)
        for declaration in ('live', {}, {'context': context(), 'zone_running': True, 'in_build_buy': False},
                            {'context': context(), 'zone_running': True, 'mode': 'live', 'extra': None}):
            self.h.handlers['context'] = lambda value: declaration
            self.assert_refusal(self.h.observe, 'invalid_live_declaration')

    def test_every_context_dimension_may_invalidate_mid_read(self):
        cases = [('session_id', '3' * 32), ('process_epoch', ProcessEpoch(123, 457)),
                 ('zone_id', '11'), ('household_id', '21'), ('client_id', '6'),
                 ('save_slot', 5), ('save_guid', '100'), ('zone_generation', 2)]
        for field, value in cases:
            h = Harness()
            def change(default):
                h.ctx = replace(h.ctx, **{field: value})
                return default
            h.handlers['info'] = change
            with self.assertRaisesRegex(NativeRefusal, '^live_context_changed_or_not_live$'): h.observe()
            self.assertNotIn('instance', h.calls)

    def test_each_external_operation_is_bracketed_and_context_loss_wins(self):
        stages = ('info', 'info_id', 'instance', 'sim_id', 'queue', 'running',
                  'iter_queued', 'next_queued', 'guid', 'runtime')
        for stage in stages:
            for throws in (False, True):
                h = Harness(); h.queued.rows = [ProbeRow(h)]
                def change(default):
                    h.mode = 'cas'
                    if throws: raise AttributeError('PRIVATE_CANARY')
                    return default
                h.handlers[stage] = change
                with self.assertRaisesRegex(NativeRefusal, '^live_context_changed_or_not_live$'): h.observe()
                index = h.calls.index(stage)
                self.assertEqual('context', h.calls[index - 1], stage)
                self.assertEqual('context', h.calls[index + 1], stage)
                self.assertFalse(h.api._busy)

    def test_context_loss_on_exhaustion_and_overflow_wins(self):
        h = self.h
        h.handlers['next_queued'] = lambda value: setattr(h, 'zone_running', False)
        self.assert_refusal(h.observe, 'live_context_changed_or_not_live')
        h = Harness(); h.queued.rows = [ProbeRow(h)] * 2
        def second(value):
            if h.queued.next_calls == 2: h.ctx = replace(h.ctx, zone_generation=2)
            return value
        h.handlers['next_queued'] = second
        with patch.object(live, 'MAX_ROWS', 1):
            with self.assertRaisesRegex(NativeRefusal, '^live_context_changed_or_not_live$'): h.observe()

    def test_context_failures_are_sanitized_and_no_original_exception_chain(self):
        for stage in ('admission', 'context'):
            h = Harness()
            error = type('PRIVATE_CANARY_ERROR', (ValueError,), {})('PRIVATE_CANARY_MESSAGE')
            error.private = Poison()
            if stage == 'admission': h.admission.callback = lambda *args: fail(error)(None)
            else: h.handlers[stage] = fail(error)
            with self.assertRaises(NativeRefusal) as caught: h.observe()
            self.assertNotIn('PRIVATE_CANARY', str(caught.exception))
            self.assertIsNone(caught.exception.__context__)
            self.assertIsNone(caught.exception.__cause__)
            self.assertFalse(h.api._busy)

    def test_process_control_unwinds_without_more_readers_and_guard_recovers(self):
        for stage in ('admission', 'context', 'info', 'info_id', 'instance', 'sim_id',
                      'queue', 'iter_queued', 'next_queued', 'guid', 'runtime'):
            for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
                h = Harness(); h.queued.rows = [ProbeRow(h)]
                control = exception_type()
                if stage == 'admission': h.admission.callback = lambda *args: fail(control)(None)
                else: h.handlers[stage] = fail(control)
                with self.assertRaises(exception_type) as caught: h.observe()
                self.assertIs(control, caught.exception)
                self.assertFalse(h.api._busy)
                if stage != 'admission': self.assertEqual(stage, h.calls[-1])
                else: self.assertEqual([], h.calls)
                h.admission.callback = None; h.handlers.clear()
                h.queued.position = 0
                self.assertEqual('observed', h.observe()['actor']['status'])

    def test_initial_actor_absence_failures_and_identity_errors_are_explicit(self):
        cases = [('info', lambda value: None, 'not_found'),
                 ('info', fail(ValueError('PRIVATE_CANARY')), 'reader_exception'),
                 ('info_id', lambda value: None, 'identity_mismatch_or_unavailable'),
                 ('info_id', lambda value: 31, 'identity_mismatch_or_unavailable'),
                 ('info_id', fail(AttributeError()), 'identity_mismatch_or_unavailable'),
                 ('instance', lambda value: None, 'not_instanced'),
                 ('instance', fail(ValueError()), 'reader_exception'),
                 ('sim_id', lambda value: True, 'identity_mismatch_or_unavailable'),
                 ('sim_id', lambda value: 31, 'identity_mismatch_or_unavailable')]
        for stage, handler, reason in cases:
            h = Harness(); h.handlers[stage] = handler
            result = h.observe()
            self.assertEqual({'status': 'unavailable', 'reason': reason}, result['actor'])
            for channel in ('queued', 'running'):
                self.assertEqual({'status': 'unavailable', 'rows': None,
                                  'reason': 'actor_unavailable'}, result[channel])
            self.assertNotIn('queue', h.calls)
            if stage in ('info', 'info_id'): self.assertNotIn('instance', h.calls)
            self.assertEqual('context', h.calls[-1])

    def test_final_actor_disappearance_replacement_exception_or_id_change_refuses(self):
        for stage in ('info', 'instance', 'info_id', 'sim_id'):
            for change in ('absent', 'replaced', 'throws'):
                h = Harness()
                def replace_second(value):
                    if h.calls.count(stage) < 2: return value
                    if change == 'throws': raise ValueError('PRIVATE_CANARY')
                    if stage in ('info_id', 'sim_id'): return 31
                    if change == 'absent': return None
                    return ProbeInfo(h) if stage == 'info' else ProbeSim(h)
                h.handlers[stage] = replace_second
                with self.assertRaisesRegex(NativeRefusal, '^actor_changed$'): h.observe()
                self.assertFalse(h.api._busy)

    def test_missing_null_and_failing_channels_are_independently_unavailable(self):
        for stage, output in (('queue', 'queued'), ('running', 'running')):
            for handler, reason in ((fail(AttributeError()), 'missing_field'),
                                    (lambda value: None, 'null_value'),
                                    (fail(ValueError('PRIVATE_CANARY')), 'reader_exception'),
                                    (lambda value: 12, 'not_iterable')):
                h = Harness(); h.handlers[stage] = handler
                result = h.observe()
                self.assertEqual({'status': 'unavailable', 'rows': None, 'reason': reason}, result[output])
                other = 'running' if output == 'queued' else 'queued'
                self.assertEqual({'status': 'observed', 'rows': []}, result[other])
                self.assertEqual(1 if reason in ('missing_field', 'null_value', 'reader_exception') else 2,
                                 h.calls.count(stage))

    def test_iterator_construction_errors_are_channel_unavailable(self):
        for error, reason in ((ValueError('PRIVATE_CANARY'), 'reader_exception'),
                              (AttributeError(), 'reader_exception'), (TypeError(), 'not_iterable')):
            h = Harness(); h.handlers['iter_queued'] = fail(error)
            result = h.observe()
            self.assertEqual(reason, result['queued']['reason'])
            self.assertIsNone(result['queued']['rows'])
            self.assertEqual('observed', result['running']['status'])

    def test_iteration_errors_never_return_partial_rows(self):
        for count in (0, 1, 64):
            h = Harness(); h.queued.rows = [ProbeRow(h)] * count
            def fail_at_end(value):
                if h.queued.next_calls == count + 1: raise ValueError('PRIVATE_CANARY')
                return value
            h.handlers['next_queued'] = fail_at_end
            with self.assertRaisesRegex(NativeRefusal, '^iteration_failed$') as caught: h.observe()
            self.assertIsNone(caught.exception.__context__)
            self.assertFalse(h.api._busy)

    def test_container_reference_replacement_after_consumption_refuses(self):
        for stage in ('queue', 'running'):
            h = Harness()
            def replacement(value):
                return value if h.calls.count(stage) == 1 else ProbeIterator(h, 'replacement')
            h.handlers[stage] = replacement
            with self.assertRaisesRegex(NativeRefusal, '^container_changed$'): h.observe()

    def test_present_noniterable_container_is_revalidated_too(self):
        h = self.h
        h.handlers['queue'] = lambda value: 42 if h.calls.count('queue') == 1 else 43
        self.assert_refusal(h.observe, 'container_changed')

    def test_combined_64_bound_and_single_overflow_probe_no_materialization(self):
        for queued, running in ((0, 0), (64, 0), (0, 64), (32, 32), (63, 1),
                                (64, 1), (1, 64), (65, 0), (0, 65), (64, 64)):
            h = Harness(); h.queued.rows = [ProbeRow(h)] * queued
            h.running.rows = [ProbeRow(h)] * running
            if queued + running <= 64:
                result = h.observe()
                self.assertEqual(queued, len(result['queued']['rows']))
                self.assertEqual(running, len(result['running']['rows']))
            else:
                with self.assertRaisesRegex(NativeRefusal, '^row_limit_exceeded$'): h.observe()
            self.assertLessEqual(h.queued.yields + h.running.yields, 65)
            self.assertLessEqual(h.queued.next_calls + h.running.next_calls, 66)
            self.assertLessEqual(h.queued.iter_calls + h.running.iter_calls, 2)
            self.assertEqual(min(queued + running, 64), h.calls.count('guid'))
            self.assertEqual(min(queued + running, 64), h.calls.count('runtime'))

    def test_infinite_iterators_stop_at_65_in_either_channel(self):
        for channel in ('queued', 'running'):
            h = Harness(); iterator = getattr(h, channel)
            iterator.infinite = True; iterator.rows = [ProbeRow(h)]
            with self.assertRaisesRegex(NativeRefusal, '^row_limit_exceeded$'): h.observe()
            self.assertEqual(65, iterator.yields)
            self.assertEqual(64, h.calls.count('guid'))

    def test_overflow_row_getters_are_never_touched_under_patched_limit(self):
        h = self.h
        h.queued.rows = [ProbeRow(h), Poison()]
        with patch.object(live, 'MAX_ROWS', 1):
            self.assert_refusal(h.observe, 'row_limit_exceeded')
        self.assertEqual(1, h.calls.count('guid'))
        self.assertEqual(2, h.queued.yields)

    def test_malformed_rows_and_duplicates_consume_budget_and_preserve_order(self):
        h = self.h; row = ProbeRow(h, 123, 456)
        h.queued.rows = [row, Box(), row, ProbeRow(h, 124, 456)]
        h.running.rows = [row]
        result = h.observe()
        self.assertEqual(['123', None, '123', '124'],
                         [value['tuning_guid64']['value'] for value in result['queued']['rows']])
        self.assertEqual([0, 1, 2, 3], [value['index'] for value in result['queued']['rows']])
        self.assertEqual('123', result['running']['rows'][0]['tuning_guid64']['value'])
        self.assertEqual(0, result['running']['rows'][0]['index'])
        h = Harness(); h.queued.rows = [Box()] * 65
        with self.assertRaisesRegex(NativeRefusal, '^row_limit_exceeded$'): h.observe()

    def test_field_exact_uint64_validation_and_independence(self):
        class Number(int): pass
        for raw, reason in ((None, 'null_value'), (0, 'invalid_value'), (-1, 'invalid_value'),
                            (2**64, 'invalid_value'), (True, 'invalid_value'),
                            (1.0, 'invalid_value'), ('1', 'invalid_value'),
                            (Number(1), 'invalid_value'), (Poison(), 'invalid_value')):
            for field in ('guid', 'runtime'):
                h = Harness(); row = ProbeRow(h); setattr(row, field, raw)
                h.queued.rows = [row]
                result = h.observe()['queued']['rows'][0]
                name = 'tuning_guid64' if field == 'guid' else 'runtime_instance_id'
                other = 'runtime_instance_id' if field == 'guid' else 'tuning_guid64'
                self.assertEqual({'status': 'unavailable', 'value': None, 'reason': reason}, result[name])
                self.assertEqual('available', result[other]['status'])
        h = Harness(); h.queued.rows = [ProbeRow(h, 1, MAX_UINT64)]
        row = h.observe()['queued']['rows'][0]
        self.assertEqual('1', row['tuning_guid64']['value'])
        self.assertEqual(str(MAX_UINT64), row['runtime_instance_id']['value'])

    def test_field_missing_exception_and_both_unavailable_rows_are_retained(self):
        h = self.h; h.queued.rows = [Box(), ProbeRow(h)]
        h.handlers['runtime'] = fail(type('PRIVATE_CANARY_EXCEPTION', (ValueError,), {})('PRIVATE_CANARY'))
        result = h.observe()
        self.assertEqual('missing_field', result['queued']['rows'][0]['tuning_guid64']['reason'])
        self.assertEqual('missing_field', result['queued']['rows'][0]['runtime_instance_id']['reason'])
        self.assertEqual('reader_exception', result['queued']['rows'][1]['runtime_instance_id']['reason'])
        self.assertNotIn('PRIVATE_CANARY', json.dumps(result))

    def test_sequential_fields_and_undetected_aba_are_not_claimed_atomic(self):
        h = self.h; row = ProbeRow(h, 100, 200); h.queued.rows = [row]
        def mutate(value):
            h.mode = 'cas'; h.mode = 'live'  # Entirely between context samples.
            row.runtime = 201
            return value
        h.handlers['guid'] = mutate
        result = h.observe()
        self.assertEqual('100', result['queued']['rows'][0]['tuning_guid64']['value'])
        self.assertEqual('201', result['queued']['rows'][0]['runtime_instance_id']['value'])
        self.assertIs(False, result['atomic_snapshot'])

    def test_output_is_exact_detached_bounded_primitives_and_fixed_flags(self):
        h = self.h; h.queued.rows = [ProbeRow(h, MAX_UINT64, MAX_UINT64)] * 64
        h.ctx = replace(h.ctx, save_slot=MAX_UINT64, save_guid=str(MAX_UINT64), zone_generation=MAX_UINT64)
        result = h.observe(expected_context=h.ctx)
        def plain(value):
            self.assertIn(type(value), (dict, list, str, int, bool, type(None)))
            if type(value) is dict:
                for key, item in value.items(): self.assertIs(str, type(key)); plain(item)
            elif type(value) is list:
                for item in value: plain(item)
        plain(result)
        self.assertLessEqual(len(json.dumps(result, separators=(',', ':'), allow_nan=False).encode('utf-8')), 32768)
        for name in ('live_qualified', 'independent_evidence', 'atomic_snapshot',
                     'selection_authority', 'cancellation_authority'):
            self.assertIs(False, result[name])
        for name in ('source_only', 'read_only'): self.assertIs(True, result[name])
        self.assertEqual('unknown', result['action_outcome'])
        self.assertEqual('this_call_only', result['identity_scope'])
        for value in result['context']['process_epoch'].values(): self.assertIs(str, type(value))
        self.assertIs(str, type(result['context']['save_slot']))
        self.assertIs(str, type(result['context']['zone_generation']))
        self.assertEqual({'index', 'tuning_guid64', 'runtime_instance_id'}, set(result['queued']['rows'][0]))

    def test_zero_save_identity_is_allowed_but_never_invented(self):
        self.h.ctx = replace(self.h.ctx, save_slot=0, save_guid='0')
        result = self.h.observe(expected_context=self.h.ctx)
        self.assertEqual('0', result['context']['save_slot'])
        self.assertEqual('0', result['context']['save_guid'])

    def test_output_size_is_enforced_not_truncated(self):
        with patch.object(live, 'MAX_OUTPUT_BYTES', 1):
            self.assert_refusal(self.h.observe, 'output_limit_exceeded')
        self.assertEqual('observed', self.h.observe()['actor']['status'])

    def test_returned_container_mutation_cannot_change_later_call_or_binding(self):
        result = self.h.observe()
        result['context']['process_epoch']['pid'] = '999'
        result['queued']['rows'].append(Poison())
        result['actor']['status'] = 'bad'
        result['live_qualified'] = True
        later = self.h.observe()
        self.assertEqual('123', later['context']['process_epoch']['pid'])
        self.assertEqual([], later['queued']['rows'])
        self.assertIs(False, later['live_qualified'])

    def test_no_per_call_state_history_or_native_retention_after_all_exits(self):
        for mode in ('success', 'overflow', 'exception', 'context_loss'):
            refs = []
            store = {}
            ctx = context()
            def reader_context(): return {'context': ctx, 'zone_running': True, 'mode': 'live'}
            def reader_info(actor): return store['info']
            def reader_instance(info): return store['sim']
            api = LiveQueueObserver(reader_context, reader_info, reader_instance,
                                    admission=Box(require_live_read=lambda *args: True),
                                    session_id='2' * 32, process_epoch=ProcessEpoch(123, 456))
            before = dict(vars(api))
            class Iterator(object):
                def __init__(self): self.position = 0
                def __iter__(self): return self
                def __next__(self):
                    self.position += 1
                    if mode == 'exception': raise ValueError('PRIVATE_CANARY')
                    if mode == 'context_loss': object.__setattr__(ctx, 'zone_generation', 2)
                    if mode != 'overflow' and self.position > 1: raise StopIteration
                    return store['row']
            store['row'] = Box(guid64=10, id=20)
            store['info'] = Box(id=30)
            store['iterator'] = Iterator()
            store['sim'] = Box(sim_id=30, queue=store['iterator'], si_state=())
            refs.extend(weakref.ref(value) for value in store.values())
            try: api.observe(request(), context(), '30')
            except NativeRefusal: pass
            store.clear(); gc.collect()
            self.assertTrue(all(ref() is None for ref in refs), mode)
            self.assertEqual(before, vars(api))
        self.assertEqual({'_owner_thread', '_owner_ident', '_context_reader', '_sim_info_reader',
                          '_instance_reader', '_admission', '_binding', '_busy'}, set(vars(self.h.api)))

    def test_capabilities_do_not_claim_scheduler_ttl_authority_or_dispatch(self):
        caps = self.h.api.capabilities()
        for name in ('scheduler', 'enforces_ttl', 'live_qualified', 'selection_authority', 'cancellation_authority'):
            self.assertIs(False, caps[name])
        self.assertEqual(64, caps['max_rows'])
        self.assertEqual(32768, caps['max_output_bytes'])
        caps['live_qualified'] = True
        self.assertIs(False, self.h.api.capabilities()['live_qualified'])
        for name in ('execute', 'dispatch', 'enqueue', 'cancel', 'install', 'reset', 'rebind', 'record_transition'):
            self.assertFalse(hasattr(self.h.api, name))

    def test_source_has_only_local_stdlib_imports_no_io_or_integration(self):
        path = SOURCE / 'foundry_companion' / 'live_observation.py'
        tree = ast.parse(path.read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imports.update(item.name for item in node.names)
            if isinstance(node, ast.ImportFrom): imports.add('.' * node.level + node.module)
        self.assertEqual({'dataclasses', 'json', 'threading', '.native', '.queue'}, imports)
        forbidden = {'open', 'eval', 'exec', '__import__', 'compile', 'sleep', 'perf_counter',
                     'Thread', 'Timer', 'Lock', 'socket', 'import_module', 'list', 'tuple',
                     'length_hint', 'sorted', 'repr', 'id'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else '')
                self.assertNotIn(name, forbidden)
        for name in ('native.py', 'wire.py', 'transport.py', 'queue.py', '__init__.py'):
            self.assertNotIn('live_observation', (SOURCE / 'foundry_companion' / name).read_text())


if __name__ == '__main__':
    unittest.main()
