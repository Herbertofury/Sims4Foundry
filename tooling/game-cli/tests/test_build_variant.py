"""Fabricated direct variant tests; no actual game or user profile is used."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'companion-src'))
sys.path.insert(0, str(ROOT / 'build-adapter-src'))
from build_variant import BuildVariantExecutor, VariantRequest
from foundry_companion.build_observation import BuildContext
from foundry_companion.native import NativeRefusal
from foundry_companion.queue import ProcessEpoch


class FakeObject:
    def __init__(self):
        self.id, self.parent, self.is_sim = 30, None, False
        self.definition = NS(id=40)
        self.position = NS(x=1.,y=2.,z=3.)
        self.orientation = NS(x=0.,y=0.,z=0.,w=1.)
        self.routing_surface = NS(primary_id=10,secondary_id=0,type=0)
        self.calls, self.callback, self.returned = [], None, True
    def set_definition(self, definition):
        self.calls.append(definition)
        if self.callback: self.callback(definition)
        return self.returned


class Admission:
    def __init__(self):
        self.verdict, self.callback, self.calls = True, None, 0
    def require_build_variant(self, request):
        self.calls += 1
        if self.callback: self.callback(request)
        return self.verdict


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.context = BuildContext('2'*32, ProcessEpoch(123,456), '10','20','5',4,'99',1)
        self.request = VariantRequest('1'*32, self.context, '30', (1.,2.,3.),
                                      (0.,0.,0.,1.), (10,0,0), '40', '41')
        self.obj, self.admission = FakeObject(), Admission()
        self.groups, self.reads, self.mode = {40:7,41:7}, [], True
        self.executor = BuildVariantExecutor(self.read_context, self.read_object,
                                             self.admission, self.group, 0)
    def read_context(self):
        self.reads.append('context')
        return {'context':self.context, 'zone_running':True, 'in_build_buy':self.mode}
    def read_object(self, value):
        self.reads.append(('object',value))
        return self.obj
    def group(self, value):
        self.reads.append(('group',value))
        return self.groups.get(value, 0)
    def test_same_group_dispatch_and_exact_readback(self):
        self.obj.callback = lambda value: setattr(self.obj.definition, 'id', value)
        result = self.executor.execute(self.request)
        self.assertEqual('dispatched', result['outcome'])
        self.assertEqual('41', result['after_definition'])
        self.assertFalse(result['swatch_verified'])
        self.assertFalse(result['undo_recorded'])
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([41], self.obj.calls)
    def test_native_false_and_noop_are_not_swatch_success(self):
        self.obj.returned = False
        result = self.executor.execute(self.request)
        self.assertEqual('unresolved', result['outcome'])
        self.assertFalse(result['native_returned'])
        self.assertEqual('40', result['after_definition'])
        self.assertTrue(result['may_have_applied'])
    def test_nonboolean_return_or_effect_then_error_is_unresolved(self):
        for failure in ('return', 'error'):
            self.setUp()
            if failure == 'return': self.obj.returned = None
            else:
                def apply_then_fail(value):
                    self.obj.definition.id = value
                    raise RuntimeError('lost reply')
                self.obj.callback = apply_then_fail
            result = self.executor.execute(self.request)
            self.assertEqual('unresolved', result['outcome'])
            self.assertTrue(result['may_have_applied'])
            with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
            self.assertEqual([41], self.obj.calls)
    def test_missing_different_or_changed_group_refuses(self):
        for group in (0, 8, True, '7'):
            self.setUp()
            self.groups[41] = group
            with self.subTest(group=group):
                with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
                self.assertEqual([], self.obj.calls)
        self.setUp()
        count = [0]
        def changing_group(value):
            count[0] += 1
            return 7 if count[0] <= 2 else 8
        self.executor._group = changing_group
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([], self.obj.calls)
    def test_default_scope_denial_and_mutated_request_do_not_read(self):
        for verdict in (None, False, 1):
            self.setUp()
            self.admission.verdict = verdict
            with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
            self.assertEqual([], self.reads)
        self.setUp()
        self.admission.callback = lambda req: object.__setattr__(req, 'target_definition', '42')
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([], self.reads)
    def test_changed_context_object_and_definition_refuse(self):
        for case in ('mode','context','id','position','parent','sim','surface','definition'):
            self.setUp()
            if case == 'mode': self.mode = False
            elif case == 'context': self.context = replace(self.context, zone_generation=2)
            elif case == 'id': self.obj.id = 31
            elif case == 'position': self.obj.position.x = 9
            elif case == 'parent': self.obj.parent = object()
            elif case == 'sim': self.obj.is_sim = True
            elif case == 'surface': self.obj.routing_surface.type = 1
            else: self.obj.definition.id = 41
            with self.subTest(case=case):
                with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
                self.assertEqual([], self.obj.calls)
    def test_invalid_request_refuses_before_admission(self):
        for request in (replace(self.request, object_id='030'),
                        replace(self.request, before_definition='0'),
                        replace(self.request, target_definition='40'),
                        replace(self.request, before_surface=(10,0,True))):
            with self.assertRaises(NativeRefusal): self.executor.execute(request)
        self.assertEqual(0, self.admission.calls)
    def test_post_dispatch_identity_loss_is_unresolved(self):
        self.obj.callback = lambda value: setattr(self, 'obj', FakeObject())
        result = self.executor.execute(self.request)
        self.assertEqual('unresolved', result['outcome'])
        self.assertTrue(result['dispatched'])
    def test_wrong_thread_and_reentry_refused(self):
        errors = []
        def foreign():
            try: self.executor.execute(self.request)
            except Exception as error: errors.append(error)
        worker = threading.Thread(target=foreign)
        worker.start(); worker.join()
        self.assertEqual([NativeRefusal], [type(e) for e in errors])
        self.assertEqual([], self.reads)
        def reenter(req):
            with self.assertRaises(NativeRefusal):
                self.executor.execute(replace(self.request, request_id='3'*32))
        self.admission.callback = reenter
        self.executor.execute(self.request)
        self.assertEqual([41], self.obj.calls)
    def test_no_implicit_catalog_or_undo_operation(self):
        for name in ('undo','redo','delete','place','swatch','catalog'):
            self.assertFalse(hasattr(self.executor, name))
