"""Synthetic source-only Build readback tests; no game/profile/process is used."""
import ast
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'companion-src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion.build_observation import BuildContext, BuildObserver, MAX_OBJECT_IDS
from foundry_companion.native import DenyAdmission, NativeRefusal
from foundry_companion.queue import (Request, ProcessEpoch, ObserveArguments,
                                     SetSpeedArguments, QueueError)


def request():
    return Request('1' * 32, '2' * 32, 'observe', ObserveArguments(),
                   process_epoch=ProcessEpoch(123, 456))


def context():
    return BuildContext('2' * 32, ProcessEpoch(123, 456), '10', '20', '5', 4, '99', 1)


def native_object(object_id=30):
    return NS(id=object_id, definition=NS(id=40), position=NS(x=0.0, y=1.5, z=-2),
              orientation=NS(x=0.0, y=0.0, z=0.0, w=1.0),
              routing_surface=NS(primary_id=10, secondary_id=-1, type=0))


class Admission(DenyAdmission):
    def __init__(self):
        self.calls = []
        self.callback = None
        self.scope_callback = None
        self.scopes = []
        self.verdict = True

    def require_build_read(self, value, expected, selected):
        self.calls.append(value)
        self.scopes.append((expected, selected))
        if self.scope_callback:
            self.scope_callback(expected, selected)
        if self.callback:
            self.callback(value)
        return self.verdict


class BuildObservationTests(unittest.TestCase):
    def setUp(self):
        self.context = context()
        self.mode = True
        self.running = True
        self.objects = {30: native_object()}
        self.reads = []
        self.admission = Admission()
        self.context_callback = None
        self.object_callback = None
        self.api = self.make_api()

    def read_context(self):
        self.reads.append(('context',))
        if self.context_callback:
            self.context_callback()
        return {'context': self.context, 'zone_running': self.running, 'in_build_buy': self.mode}

    def read_object(self, object_id):
        self.reads.append(('object', object_id))
        if self.object_callback:
            return self.object_callback(object_id)
        return self.objects.get(object_id)

    def make_api(self, **changes):
        values = dict(context_reader=self.read_context, object_reader=self.read_object,
                      admission=self.admission, session_id='2' * 32,
                      process_epoch=ProcessEpoch(123, 456))
        values.update(changes)
        return BuildObserver(**values)

    def observe(self, ids=('30',), **changes):
        values = dict(request=request(), expected_context=self.context, object_ids=ids)
        values.update(changes)
        return self.api.observe(**values)

    def event(self, sequence=1, transition='entered', **changes):
        values = dict(request=request(), expected_context=self.context,
                      sequence=sequence, transition=transition)
        values.update(changes)
        return self.api.record_transition(**values)

    def assert_no_reads(self):
        self.assertEqual([], self.reads)

    def test_default_denial_precedes_all_readers(self):
        api = self.make_api(admission=None)
        with self.assertRaises(NativeRefusal):
            api.observe(request(), self.context, ['30'])
        with self.assertRaises(NativeRefusal):
            api.record_transition(request(), self.context, 1, 'entered')
        self.assert_no_reads()

    def test_status_only_read_admission_does_not_grant_build_scope(self):
        provider = NS(require_read=lambda value: True)
        api = self.make_api(admission=provider)
        with self.assertRaises(NativeRefusal): api.observe(request(), self.context, ['30'])
        with self.assertRaises(NativeRefusal): api.record_transition(request(), self.context, 1, 'entered')
        self.assert_no_reads()

    def test_admission_sees_detached_exact_context_and_selection(self):
        expected, ids = context(), ['30']
        self.observe(ids=ids, expected_context=expected)
        admitted, selected = self.admission.scopes[-1]
        self.assertEqual(expected, admitted)
        self.assertIsNot(expected, admitted)
        self.assertIsNot(expected.process_epoch, admitted.process_epoch)
        self.assertEqual(('30',), selected)
        ids[:] = ['31']
        object.__setattr__(admitted, 'save_slot', 88)
        result = self.observe(ids=())
        self.assertEqual(4, result['context']['save_slot'])
        self.event()
        self.assertEqual((), self.admission.scopes[-1][1])

    def test_admission_cannot_change_context_including_nested_epoch(self):
        for change in (lambda value: object.__setattr__(value, 'save_slot', 88),
                       lambda value: object.__setattr__(value.process_epoch, 'pid', 124),
                       lambda value: object.__setattr__(value, 'zone_generation', 2)):
            self.admission.scope_callback = lambda expected, ids: change(expected)
            with self.assertRaises(NativeRefusal): self.observe()
        self.assert_no_reads()

    def test_admission_requires_exact_true_no_json_unlock(self):
        for verdict in (False, None, 1, 'yes', {'live_qualified': True}):
            self.admission.verdict = verdict
            with self.assertRaises(NativeRefusal): self.observe()
            self.assert_no_reads()
        with self.assertRaises(NativeRefusal):
            self.observe(request={'operation': 'observe', 'live_qualified': True})
        with self.assertRaises(NativeRefusal): self.observe(expected_context=vars(self.context))
        self.assert_no_reads()

    def test_unbound_partial_invalid_and_stale_binding_refuse_without_callbacks(self):
        for session, epoch in ((None, None), ('3' * 32, ProcessEpoch(123, 456)),
                               ('2' * 32, ProcessEpoch(123, 457))):
            api = self.make_api(session_id=session, process_epoch=epoch)
            with self.assertRaises(NativeRefusal): api.observe(request(), self.context, ['30'])
        for session, epoch in ((None, ProcessEpoch(123, 456)), ('2' * 32, None)):
            with self.assertRaises(NativeRefusal): self.make_api(session_id=session, process_epoch=epoch)
        self.assert_no_reads()
        self.assertEqual([], self.admission.calls)

    def test_stale_request_or_expected_epoch_refuses_before_admission(self):
        for req in (replace(request(), session_id='3' * 32),
                    replace(request(), process_epoch=None),
                    replace(request(), process_epoch=ProcessEpoch(124, 456)),
                    replace(request(), process_epoch=ProcessEpoch(123, 457))):
            with self.assertRaises(NativeRefusal): self.observe(request=req)
        with self.assertRaises(NativeRefusal):
            self.observe(expected_context=replace(self.context, process_epoch=ProcessEpoch(123, 457)))
        self.assert_no_reads()
        self.assertEqual([], self.admission.calls)

    def test_context_fields_are_exact_and_required(self):
        cases = [('zone_id', None), ('zone_id', '01'), ('zone_id', True),
                 ('household_id', None), ('client_id', 5), ('save_slot', None),
                 ('save_slot', True), ('save_slot', -1), ('save_slot', 2**64),
                 ('save_guid', None), ('save_guid', '099'), ('save_guid', 99),
                 ('zone_generation', 0), ('zone_generation', True), ('zone_generation', 2**64)]
        for name, value in cases:
            with self.subTest(field=name, value=value):
                corrupted = context()
                object.__setattr__(corrupted, name, value)
                with self.assertRaises(NativeRefusal): self.observe(expected_context=corrupted)
        self.assert_no_reads()
        self.assertEqual([], self.admission.calls)

    def test_observe_request_cannot_silently_override_explicit_build_context(self):
        for name, values in (('expected_zone_id', ('10', '11')),
                             ('expected_household_id', ('20', '21')),
                             ('expected_save_slot', (4, 5)),
                             ('expected_save_guid', ('99', '100'))):
            for value in values:
                req = request()
                object.__setattr__(req, name, value)
                with self.assertRaises((NativeRefusal, QueueError)):
                    self.observe(request=req)
        self.assert_no_reads()
        self.assertEqual([], self.admission.calls)

    def test_observe_only_no_mutation_dispatch_api(self):
        req = Request('1' * 32, '2' * 32, 'live.set_speed', SetSpeedArguments(2),
                      process_epoch=ProcessEpoch(123, 456), expected_zone_id='10',
                      expected_household_id='20', expected_save_slot=4, expected_save_guid='99')
        with self.assertRaises(NativeRefusal): self.observe(request=req)
        self.assert_no_reads()
        for name in ('execute', 'enter', 'exit', 'place', 'move', 'undo', 'dispatch', 'install'):
            self.assertFalse(hasattr(self.api, name))

    def test_bound_ids_no_enumeration_coercion_duplicates_or_unbounded_iterables(self):
        for values in (['30'] * 33, ['30', '30'], ['0'], ['030'], [30], [True],
                       [str(2**64)], {'30'}, iter(['30']), '30', None):
            with self.subTest(values=type(values).__name__):
                with self.assertRaises(NativeRefusal): self.observe(ids=values)
        self.assert_no_reads()
        self.assertEqual([], self.admission.calls)

    def test_maximum_selection_is_bounded_and_only_resolves_selected_ids(self):
        ids = [str(value) for value in range(1, MAX_OBJECT_IDS + 1)]
        result = self.observe(ids=ids)
        self.assertEqual(MAX_OBJECT_IDS, len(result['objects']))
        reads = [entry[1] for entry in self.reads if entry[0] == 'object']
        self.assertEqual(set(range(1, 33)), set(reads))
        self.assertLessEqual(len(reads), 2 * MAX_OBJECT_IDS)
        self.assertLessEqual(len(self.reads), 25 * MAX_OBJECT_IDS + 2)
        self.assertEqual(1, len(self.admission.calls))

    def test_all_present_maximum_objects_still_have_fixed_read_bound(self):
        self.objects = {value: native_object(value) for value in range(1, 33)}
        result = self.observe(ids=[str(value) for value in range(1, 33)])
        self.assertTrue(all(row['status'] == 'observed' for row in result['objects']))
        self.assertEqual(64, sum(entry[0] == 'object' for entry in self.reads))
        self.assertLessEqual(len(self.reads), 25 * MAX_OBJECT_IDS + 2)
        self.assertLess(len(json.dumps(result)), 65536)

    def test_mode_only_is_explicit_empty_selection_and_late_start_is_unknown(self):
        result = self.observe(ids=())
        self.assertEqual([], result['objects'])
        self.assertEqual({'status': 'available', 'value': True}, result['mode'])
        self.assertEqual(None, result['transitions']['last_sequence'])
        self.assertFalse(result['transitions']['initial_mode_known'])
        self.assertIsNone(result['transitions']['current_mode_from_events'])
        self.assertTrue(all(entry[0] == 'context' for entry in self.reads))

    def test_happy_snapshot_is_detached_and_unqualified(self):
        result = self.observe()
        self.assertFalse(result['live_qualified'])
        self.assertFalse(result['independent_evidence'])
        self.assertTrue(result['source_only'])
        self.assertFalse(result['atomic_snapshot'])
        self.assertEqual('injected_host_declaration', result['context_source'])
        self.assertEqual('1' * 32, result['request_id'])
        fields = result['objects'][0]['fields']
        self.assertEqual('40', fields['definition_id']['value'])
        self.assertEqual({'x': 0.0, 'y': 1.5, 'z': -2.0}, fields['position']['value'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 0.0, 'w': 1.0}, fields['orientation']['value'])
        self.assertEqual({'primary_id': 10, 'secondary_id': -1, 'type': 0}, fields['routing_surface']['value'])
        self.objects[30].position.y = 99
        self.objects[30].orientation.w = 0
        self.objects[30].definition.id = 41
        self.assertEqual(1.5, fields['position']['value']['y'])
        self.assertEqual(1.0, fields['orientation']['value']['w'])
        self.assertEqual('40', fields['definition_id']['value'])
        json.dumps(result, allow_nan=False)

    def test_returned_context_and_events_do_not_alias_retained_state(self):
        result = self.event()
        result['transitions']['last_sequence'] = 999
        result['context']['process_epoch']['pid'] = 999
        result['live_qualified'] = True
        current = self.observe(ids=())
        self.assertEqual(1, current['transitions']['last_sequence'])
        self.assertEqual(123, current['context']['process_epoch']['pid'])
        self.assertFalse(current['live_qualified'])

    def test_caller_request_context_and_selection_are_detached_before_hook(self):
        req, expected, ids = request(), context(), ['30']
        def change_original(view):
            object.__setattr__(req, 'request_id', '3' * 32)
            object.__setattr__(req, 'session_id', '3' * 32)
            object.__setattr__(req.process_epoch, 'pid', 999)
            object.__setattr__(expected, 'save_slot', 88)
            object.__setattr__(expected.process_epoch, 'pid', 999)
            ids[:] = ['999']
        self.admission.callback = change_original
        result = self.observe(ids=ids, request=req, expected_context=expected)
        self.assertEqual('30', result['objects'][0]['object_id'])
        self.assertEqual('1' * 32, result['request_id'])
        self.assertEqual(4, result['context']['save_slot'])
        self.assertEqual(123, result['context']['process_epoch']['pid'])

    def test_admission_cannot_rewrite_its_checked_view(self):
        changes = (lambda view: object.__setattr__(view, 'session_id', '3' * 32),
                   lambda view: object.__setattr__(view.process_epoch, 'pid', 124),
                   lambda view: object.__setattr__(view, 'ttl_seconds', 5))
        for change in changes:
            self.admission.callback = change
            with self.assertRaises(NativeRefusal): self.observe()
        self.assert_no_reads()

    def test_configured_epoch_is_not_aliased(self):
        epoch = ProcessEpoch(123, 456)
        api = self.make_api(process_epoch=epoch)
        object.__setattr__(epoch, 'creation_filetime', 457)
        self.assertFalse(api.observe(request(), self.context)['live_qualified'])

    def test_admission_context_change_stops_before_object_reads(self):
        expected = self.context
        self.admission.callback = lambda value: setattr(self, 'context', replace(expected, save_slot=5))
        with self.assertRaises(NativeRefusal): self.observe(expected_context=expected)
        self.assertEqual([('context',)], self.reads)
        self.assertEqual(1, len(self.admission.calls))

    def test_each_exact_current_context_field_is_rechecked(self):
        changes = (dict(session_id='3' * 32), dict(process_epoch=ProcessEpoch(123, 457)),
                   dict(zone_id='11'), dict(household_id='21'), dict(client_id='6'),
                   dict(save_slot=5), dict(save_guid='100'), dict(zone_generation=2))
        for changeset in changes:
            self.setUp()
            expected = self.context
            self.context = replace(expected, **changeset)
            with self.assertRaises(NativeRefusal): self.observe(expected_context=expected)
            self.assertEqual([('context',)], self.reads)

    def test_context_is_rechecked_after_object_resolver(self):
        expected = self.context
        def drift(object_id):
            self.context = replace(expected, zone_generation=2)
            return self.objects[object_id]
        self.object_callback = drift
        with self.assertRaises(NativeRefusal): self.observe(expected_context=expected)
        self.assertEqual(1, sum(entry[0] == 'object' for entry in self.reads))

    def test_context_is_checked_after_each_field_before_more_fields(self):
        expected = self.context
        owner = self
        class Changed:
            @property
            def id(self):
                owner.context = replace(expected, save_guid='100')
                return 40
        reads = []
        class Unreadable:
            @property
            def x(self):
                reads.append('position')
                return 0
        self.objects[30].definition = Changed()
        self.objects[30].position = Unreadable()
        with self.assertRaises(NativeRefusal): self.observe(expected_context=expected)
        self.assertEqual([], reads)

    def test_final_context_drift_discards_whole_snapshot(self):
        expected = self.context
        original = self.read_context
        count = [0]
        def current():
            count[0] += 1
            # Exact last read count established by a separate successful bounded read.
            if count[0] == expected_count: self.context = replace(expected, save_slot=5)
            return original()
        self.observe()
        expected_count = sum(entry[0] == 'context' for entry in self.reads)
        self.reads[:] = []
        self.api = self.make_api(context_reader=current)
        with self.assertRaises(NativeRefusal): self.observe(expected_context=expected)
        self.assertEqual(expected_count, count[0])

    def test_context_exception_missing_or_not_running_refuses(self):
        def fail(): raise RuntimeError('private detail')
        for reader in (fail, lambda: {}, lambda: {'context': self.context},
                       lambda: {'context': self.context, 'zone_running': 1},
                       lambda: {'context': self.context, 'zone_running': False},
                       lambda: {'context': self.context, 'zone_running': True, 'x': 1, 'y': 2}):
            api = self.make_api(context_reader=reader)
            with self.assertRaises(NativeRefusal) as raised: api.observe(request(), self.context, ['30'])
            self.assertNotIn('private detail', str(raised.exception))
        self.assert_no_reads()

    def test_missing_mode_is_unavailable_never_false(self):
        for mode in (None, 0, 1, 'true'):
            self.mode = mode
            result = self.observe(ids=())
            self.assertEqual('unavailable', result['mode']['status'])
            self.assertIsNone(result['mode']['value'])
        api = self.make_api(context_reader=lambda: {'context': self.context, 'zone_running': True})
        self.assertIsNone(api.observe(request(), self.context)['mode']['value'])

    def test_mode_change_or_missing_intermediate_sample_stays_unknown(self):
        reads = [0]
        def change():
            reads[0] += 1
            self.mode = None if reads[0] == 3 else True
        self.context_callback = change
        self.assertIsNone(self.observe()['mode']['value'])
        self.context_callback = None
        self.mode = False
        self.assertIs(self.observe(ids=())['mode']['value'], False)

    def test_object_missing_disappears_or_is_reused_during_capture(self):
        self.assertEqual('not_found', self.observe(ids=['31'])['objects'][0]['reason'])
        for replacement, reason in ((None, 'disappeared'), (native_object(), 'identity_changed')):
            calls = [0]
            def resolving(object_id):
                calls[0] += 1
                return self.objects[30] if calls[0] == 1 else replacement
            self.object_callback = resolving
            row = self.observe()['objects'][0]
            self.assertEqual(reason, row['reason'])
            self.assertNotIn('fields', row)

    def test_identity_is_exact_and_cannot_change_during_capture(self):
        for value in (None, True, '30', 0, 31, 2**64):
            self.objects[30].id = value
            self.assertEqual('identity_mismatch_or_unavailable', self.observe()['objects'][0]['reason'])
        self.objects[30].id = 30
        calls = [0]
        def changed(object_id):
            calls[0] += 1
            if calls[0] == 2: self.objects[30].id = 31
            return self.objects[30]
        self.object_callback = changed
        self.assertEqual('identity_changed', self.observe()['objects'][0]['reason'])

    def test_object_read_exception_is_unavailable_not_retried(self):
        def fail(object_id): raise RuntimeError('secret payload')
        self.object_callback = fail
        result = self.observe()
        self.assertEqual('reader_exception', result['objects'][0]['reason'])
        self.assertEqual(1, sum(entry[0] == 'object' for entry in self.reads))
        self.assertNotIn('secret payload', json.dumps(result))
        self.object_callback = None
        self.assertEqual('observed', self.observe()['objects'][0]['status'])

    def test_missing_fields_remain_unavailable_not_default_zero(self):
        self.objects[30] = NS(id=30)
        result = self.observe()
        for field in result['objects'][0]['fields'].values():
            self.assertEqual('unavailable', field['status'])
            self.assertIsNone(field['value'])
        self.objects[30] = native_object()
        del self.objects[30].position.y
        self.assertIsNone(self.observe()['objects'][0]['fields']['position']['value'])

    def test_field_exceptions_are_local_and_do_not_expose_exception_text(self):
        class BadPosition:
            @property
            def x(self): raise RuntimeError('private content')
        self.objects[30].position = BadPosition()
        result = self.observe()
        fields = result['objects'][0]['fields']
        self.assertEqual('reader_exception', fields['position']['reason'])
        self.assertEqual('available', fields['orientation']['status'])
        self.assertNotIn('private content', json.dumps(result))

    def test_nonfinite_coercible_or_oversize_position_is_unavailable(self):
        for value in (float('nan'), float('inf'), -float('inf'), True, '1', 2**64, 10**1000):
            self.objects[30].position.x = value
            field = self.observe()['objects'][0]['fields']['position']
            self.assertIsNone(field['value'])
            self.assertEqual('invalid_value', field['reason'])

    def test_quaternion_is_finite_near_unit_and_never_renormalized(self):
        for value in (0, 2, float('nan'), float('inf'), '1', True):
            self.objects[30].orientation.w = value
            field = self.observe()['objects'][0]['fields']['orientation']
            self.assertIsNone(field['value'])
        self.objects[30].orientation.w = -1.0
        self.assertEqual(-1.0, self.observe()['objects'][0]['fields']['orientation']['value']['w'])

    def test_routing_and_definition_must_be_exact_bounded_values(self):
        for name, value in (('primary_id', True), ('primary_id', -1), ('primary_id', 2**64),
                            ('secondary_id', -2**31-1), ('secondary_id', 2**31),
                            ('type', True), ('type', -1), ('type', 2**31), ('type', '0')):
            self.objects[30] = native_object()
            setattr(self.objects[30].routing_surface, name, value)
            self.assertIsNone(self.observe()['objects'][0]['fields']['routing_surface']['value'])
        for value in (None, 0, True, '40', 2**64):
            self.objects[30].definition.id = value
            self.assertIsNone(self.observe()['objects'][0]['fields']['definition_id']['value'])

    def test_events_never_determine_current_mode_even_when_matching(self):
        self.mode = None
        first = self.event(50, 'entered')
        self.assertEqual('first', first['event']['status'])
        self.assertIsNone(first['mode']['value'])
        self.assertIsNone(first['event']['matches_sampled_mode'])
        self.assertIsNone(first['transitions']['current_mode_from_events'])
        self.mode = False
        contrary = self.event(51, 'entered')
        self.assertIs(contrary['mode']['value'], False)
        self.assertFalse(contrary['event']['matches_sampled_mode'])
        self.assertTrue(contrary['event']['repeated_transition'])
        matching = self.event(52, 'exited')
        self.assertTrue(matching['event']['matches_sampled_mode'])
        self.assertFalse(matching['live_qualified'])
        self.assertFalse(matching['independent_evidence'])
        self.assertFalse(matching['transitions']['initial_mode_known'])

    def test_duplicates_stale_conflicting_and_missed_sequences(self):
        self.event(10, 'entered')
        self.assertEqual('duplicate', self.event(10, 'entered')['event']['status'])
        self.reads[:] = []
        for seq, kind in ((9, 'entered'), (10, 'exited')):
            with self.assertRaises(NativeRefusal): self.event(seq, kind)
        self.assert_no_reads()
        gap = self.event(12, 'exited')
        self.assertEqual('gap', gap['event']['status'])
        self.assertTrue(gap['transitions']['sequence_gap_observed'])
        self.assertTrue(self.event(13, 'entered')['transitions']['sequence_gap_observed'])
        self.assertTrue(self.event(13, 'entered')['transitions']['sequence_gap_observed'])

    def test_missed_events_cannot_hide_independently_observed_mode_change(self):
        self.event(1, 'entered')
        self.mode = False  # No exit notification delivered.
        result = self.observe(ids=())
        self.assertEqual('entered', result['transitions']['last_transition'])
        self.assertIs(result['mode']['value'], False)
        self.assertIsNone(result['transitions']['current_mode_from_events'])
        # Absence of a sequence gap is not a continuity guarantee.
        self.assertFalse(result['transitions']['sequence_gap_observed'])

    def test_zone_change_and_same_id_reload_reset_event_baseline(self):
        for new_context in (replace(self.context, zone_id='11', zone_generation=2),
                            replace(self.context, zone_generation=3)):
            self.event(10, 'entered')
            old = self.context
            self.context = new_context
            self.mode = None
            result = self.observe(ids=())
            self.assertIsNone(result['transitions']['last_sequence'])
            self.assertIsNone(result['mode']['value'])
            self.assertEqual('first', self.event(1, 'exited')['event']['status'])
            with self.assertRaises(NativeRefusal): self.event(11, expected_context=old)

    def test_failed_event_read_does_not_commit_sequence(self):
        self.event(1, 'entered')
        expected = self.context
        self.context_callback = lambda: setattr(self, 'context', replace(expected, save_slot=5))
        with self.assertRaises(NativeRefusal): self.event(2, 'exited', expected_context=expected)
        self.context_callback = None
        self.context = expected
        self.assertEqual('contiguous', self.event(2, 'exited')['event']['status'])

    def test_event_inputs_are_bounded_before_admission_or_reads(self):
        for sequence, transition in ((True, 'entered'), (0, 'entered'), (2**64, 'entered'),
                                     ('1', 'entered'), (1, 'enter'), (1, 'exited' * 100000)):
            with self.assertRaises(NativeRefusal): self.event(sequence, transition)
        self.assert_no_reads()
        self.assertEqual([], self.admission.calls)

    def test_retained_event_state_is_constant_and_native_references_not_cached(self):
        for seq in range(1, 1001): self.event(seq, 'entered' if seq % 2 else 'exited')
        self.assertEqual((1000, 'exited'), self.api._last_event)
        self.assertEqual(9, len(self.api._last_context))
        self.assertNotIn('objects', vars(self.api))
        self.observe()
        self.assertFalse(any(value is self.objects[30] for value in vars(self.api).values()))

    def test_wrong_thread_fails_before_admission_or_reader(self):
        errors = []
        def call():
            for action in (self.observe, self.event):
                try: action()
                except Exception as error: errors.append(error)
        worker = threading.Thread(target=call)
        worker.start(); worker.join()
        self.assertEqual(2, len(errors))
        self.assertTrue(all(type(error) is NativeRefusal for error in errors))
        self.assertEqual([], self.admission.calls)
        self.assert_no_reads()

    def test_same_ident_different_thread_object_and_changed_ident_refuse(self):
        with patch('foundry_companion.build_observation.threading.current_thread',
                   return_value=NS(ident=self.api._owner_ident)):
            with self.assertRaises(NativeRefusal): self.observe()
        self.api._owner_ident = -1
        with self.assertRaises(NativeRefusal): self.observe()
        self.assert_no_reads()
        self.assertEqual([], self.admission.calls)

    def test_reentry_from_admission_context_and_object_reader_is_rejected(self):
        for place in ('admission', 'context', 'object'):
            self.setUp()
            attempts = []
            def reenter():
                for action in (self.observe, self.event):
                    with self.assertRaises(NativeRefusal): action()
                    attempts.append(1)
            if place == 'admission': self.admission.callback = lambda view: reenter()
            elif place == 'context': self.context_callback = reenter
            else:
                def reader(object_id): reenter(); return self.objects[object_id]
                self.object_callback = reader
            self.assertEqual('observed', self.observe()['objects'][0]['status'])
            self.assertTrue(attempts)
            self.assertEqual(1, len(self.admission.calls))

    def test_admission_exception_releases_reentrancy_guard(self):
        def fail(view): raise RuntimeError('denied')
        self.admission.callback = fail
        with self.assertRaises(NativeRefusal) as raised: self.observe()
        self.assertNotIn('denied', str(raised.exception))
        self.assert_no_reads()
        self.admission.callback = None
        self.assertEqual('observed', self.observe()['objects'][0]['status'])

    def test_callback_refusals_do_not_leak_their_messages(self):
        def fail(*args): raise NativeRefusal('PRIVATE callback content')
        for place in ('admission', 'context'):
            self.setUp()
            if place == 'admission': self.admission.callback = fail
            else: self.context_callback = fail
            with self.assertRaises(NativeRefusal) as raised: self.observe()
            self.assertNotIn('PRIVATE', str(raised.exception))
            self.assertTrue(raised.exception.__suppress_context__)

    def test_source_is_python37_syntax_and_has_no_game_or_bootstrap_imports(self):
        text = (SOURCE / 'foundry_companion' / 'build_observation.py').read_text()
        tree = ast.parse(text, feature_version=7) if sys.version_info >= (3, 8) else ast.parse(text)
        roots = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): roots.extend(name.name.split('.')[0] for name in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level: roots.append(node.module.split('.')[0])
        self.assertEqual({'dataclasses', 'math', 'threading'}, set(roots))
        for name in ('services', 'zone', 'sims4', 'sims4communitylib', 'socket', 'subprocess'):
            self.assertNotIn(name, roots)


if __name__ == '__main__':
    unittest.main()
