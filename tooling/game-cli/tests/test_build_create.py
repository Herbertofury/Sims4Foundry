"""Fabricated direct creation tests; never run the real game."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'companion-src'))
sys.path.insert(0, str(ROOT / 'build-adapter-src'))
from foundry_build_adapter.build_create import BuildCreateExecutor, CreateRequest
from foundry_build_adapter.build_transform import BuildTransformExecutor, TransformRequest
from foundry_companion.build_observation import BuildContext
from foundry_companion.native import NativeRefusal
from foundry_companion.queue import ProcessEpoch


class FakeObject:
    def __init__(self):
        self.id, self.parent, self.is_sim = 30, None, False
        self.definition = NS(id=40)
        self.position = NS(x=0., y=0., z=0.)
        self.orientation = NS(x=0., y=0., z=0., w=1.)
        self.routing_surface = NS(primary_id=10, secondary_id=0, type=0)
    def move_to(self, *, routing_surface, translation, orientation):
        self.routing_surface, self.position, self.orientation = routing_surface, translation, orientation


class Admission:
    def __init__(self):
        self.verdict, self.callback, self.calls = True, None, 0
    def require_build_create(self, request):
        self.calls += 1
        if self.callback: self.callback(request)
        return self.verdict


class CreateTests(unittest.TestCase):
    def setUp(self):
        self.context = BuildContext('2'*32, ProcessEpoch(123,456), '10','20','5',4,'99',1)
        self.request = CreateRequest('1'*32, self.context, '40', (1.,2.,3.), 0., 0)
        self.obj, self.admission = FakeObject(), Admission()
        self.reads, self.creates, self.mode = [], [], True
        self.legal, self.test_callback, self.create_callback = True, None, None
        self.executor = BuildCreateExecutor(self.read_context, self.read_object,
            self.admission, lambda x,y,z:NS(x=x,y=y,z=z),
            lambda yaw:NS(x=0.,y=0.,z=0.,w=1.),
            lambda zone,level,kind:NS(primary_id=zone,secondary_id=level,type=kind), 0,
            lambda p,q:NS(position=p,orientation=q), lambda t,s:NS(transform=t,surface=s),
            self.placement_test, self.create)
    def read_context(self):
        self.reads.append('context')
        return {'context':self.context, 'zone_running':True, 'in_build_buy':self.mode}
    def read_object(self, value):
        self.reads.append(('object',value))
        return self.obj
    def placement_test(self, **kwargs):
        self.reads.append(('placement',kwargs['definition_id']))
        if self.test_callback: self.test_callback()
        return self.legal, ()
    def create(self, definition):
        self.creates.append(definition)
        if self.create_callback: self.create_callback()
        return self.obj
    def test_one_creation_exposes_id_but_not_placement_or_transaction(self):
        result = self.executor.execute(self.request)
        self.assertEqual('created', result['outcome'])
        self.assertEqual('30', result['object_id'])
        self.assertFalse(result['placed'])
        self.assertFalse(result['transaction_verified'])
        self.assertEqual([40], self.creates)
    def test_created_object_can_be_transformed_with_separate_request(self):
        created = self.executor.execute(self.request)
        move = BuildTransformExecutor(self.read_context, self.read_object,
            NS(require_build_transform=lambda request:True), lambda x,y,z:NS(x=x,y=y,z=z),
            lambda yaw:NS(x=0.,y=0.,z=0.,w=1.),
            lambda zone,level,kind:NS(primary_id=zone,secondary_id=level,type=kind), 0,
            lambda p,q:NS(position=p,orientation=q), lambda t,s:NS(transform=t,surface=s),
            lambda obj,location:(True,()))
        request = TransformRequest('3'*32, self.context, created['object_id'],
                    created['position'], created['orientation'], created['surface'],
                    (1.,2.,3.), 0., 0)
        result = move.execute(request)
        self.assertEqual('dispatched', result['outcome'])
        self.assertEqual((1.,2.,3.), result['after'][0])
        self.assertFalse(result['transaction_verified'])
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([40], self.creates)
    def test_placement_refusal_has_no_creation(self):
        self.legal = False
        result = self.executor.execute(self.request)
        self.assertEqual('placement_refused', result['outcome'])
        self.assertFalse(result['dispatched'])
        self.assertEqual([], self.creates)
    def test_requested_definition_location_and_floor_reach_placement_test(self):
        captured = []
        def inspect(**kwargs):
            captured.append(kwargs)
            return True, ()
        self.executor._test = inspect
        self.executor.execute(replace(self.request, destination=(9.,8.,7.), level=2))
        self.assertEqual(40, captured[0]['definition_id'])
        self.assertEqual(9., captured[0]['location'].transform.position.x)
        self.assertEqual(2, captured[0]['location'].surface.secondary_id)
    def test_creation_exception_is_unknown_without_cleanup_or_replay(self):
        def fail(): raise RuntimeError('lost create reply')
        self.create_callback = fail
        result = self.executor.execute(self.request)
        self.assertEqual('unresolved', result['outcome'])
        self.assertTrue(result['may_have_applied'])
        self.assertIsNone(result['object_id'])
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([40], self.creates)
    def test_id_is_retained_when_later_readback_fails(self):
        self.executor._object = lambda value: None
        result = self.executor.execute(self.request)
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual('30', result['object_id'])
        self.assertEqual([40], self.creates)
    def test_wrong_definition_or_context_after_creation_is_uncertain(self):
        for case in ('definition','context','sim','parent'):
            self.setUp()
            def mutate():
                if case == 'definition': self.obj.definition.id = 41
                elif case == 'context': self.context = replace(self.context, zone_generation=2)
                elif case == 'sim': self.obj.is_sim = True
                else: self.obj.parent = object()
            self.create_callback = mutate
            with self.subTest(case=case):
                result = self.executor.execute(self.request)
                self.assertEqual('unresolved', result['outcome'])
                self.assertEqual('30', result['object_id'])
                self.assertEqual([40], self.creates)
    def test_admission_and_mode_refuse_before_creation(self):
        for verdict in (False, None, 1):
            self.setUp()
            self.admission.verdict = verdict
            with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
            self.assertEqual([], self.reads)
        self.setUp()
        self.admission.callback = lambda req: object.__setattr__(req, 'definition_id','41')
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([], self.creates)
        self.setUp()
        self.mode = False
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([], self.creates)
    def test_stale_context_or_invalid_placement_test_refuses(self):
        self.test_callback = lambda: setattr(self, 'context', replace(self.context, zone_generation=2))
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([], self.creates)
        for result in (True,(1,()),(True,),[True,()]):
            self.setUp()
            self.executor._test = lambda **kwargs: result
            with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
            self.assertEqual([], self.creates)
    def test_invalid_inputs_and_full_history_fail_before_admission(self):
        for req in (replace(self.request, definition_id='040'),
                    replace(self.request, destination=(1,2,float('nan'))),
                    replace(self.request, yaw=True), replace(self.request, level=True)):
            with self.assertRaises(NativeRefusal): self.executor.execute(req)
        self.assertEqual(0, self.admission.calls)
        self.executor._seen.update('%032x' % value for value in range(128))
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([], self.reads)
    def test_foreign_thread_and_reentry_refuse(self):
        errors = []
        def foreign():
            try: self.executor.execute(self.request)
            except Exception as error: errors.append(error)
        worker = threading.Thread(target=foreign)
        worker.start(); worker.join()
        self.assertEqual([NativeRefusal], [type(error) for error in errors])
        self.assertEqual([], self.reads)
        def reenter(req):
            with self.assertRaises(NativeRefusal):
                self.executor.execute(replace(self.request, request_id='3'*32))
        self.admission.callback = reenter
        self.executor.execute(self.request)
        self.assertEqual([40], self.creates)
    def test_no_implicit_placement_cleanup_or_undo(self):
        for name in ('place','move','destroy','undo','redo','swatch'):
            self.assertFalse(hasattr(self.executor, name))
