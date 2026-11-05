"""Fixture tests for direct object transforms; never import or contact the game."""
from dataclasses import replace
import math
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'companion-src'))
sys.path.insert(0, str(ROOT / 'build-adapter-src'))
from foundry_build_adapter.build_transform import BuildTransformExecutor, TransformRequest
from foundry_companion.build_observation import BuildContext
from foundry_companion.native import NativeRefusal
from foundry_companion.queue import ProcessEpoch


class FakeObject:
    def __init__(self):
        self.id = 30
        self.parent = None
        self.is_sim = False
        self.position = NS(x=1.0, y=2.0, z=3.0)
        self.orientation = NS(x=0.0, y=0.0, z=0.0, w=1.0)
        self.routing_surface = NS(primary_id=10, secondary_id=0, type=0)
        self.calls = 0
        self.fail_after = False
    def move_to(self, *, routing_surface, translation, orientation):
        self.calls += 1
        self.position = translation
        self.orientation = orientation
        self.routing_surface = routing_surface
        if self.fail_after:
            raise RuntimeError('lost native reply')


class Admission:
    def __init__(self):
        self.verdict = True
        self.callback = None
        self.calls = 0
    def require_build_transform(self, request):
        self.calls += 1
        if self.callback: self.callback(request)
        return self.verdict


class TransformTests(unittest.TestCase):
    def setUp(self):
        self.context = BuildContext('2'*32, ProcessEpoch(123,456), '10', '20', '5', 4, '99', 1)
        self.request = TransformRequest('1'*32, self.context, '30', (1.,2.,3.),
                    (0.,0.,0.,1.), (10,0,0), (4.,5.,6.), 0., 0)
        self.obj = FakeObject()
        self.admission = Admission()
        self.reads = []
        self.mode = True
        self.legal = True
        self.placement_callback = None
        self.executor = self.make_executor()

    def read_context(self):
        self.reads.append('context')
        return {'context':self.context, 'zone_running':True, 'in_build_buy':self.mode}
    def read_object(self, obj_id):
        self.reads.append(('object', obj_id))
        return self.obj
    def placement(self, obj, location):
        self.reads.append('placement')
        if self.placement_callback: self.placement_callback()
        return self.legal, ()
    def make_executor(self):
        return BuildTransformExecutor(self.read_context, self.read_object, self.admission,
            lambda x,y,z:NS(x=x,y=y,z=z),
            lambda yaw:NS(x=0.,y=math.sin(yaw/2),z=0.,w=math.cos(yaw/2)),
            lambda zone,level,kind:NS(primary_id=zone,secondary_id=level,type=kind),
            0, lambda position,orientation:NS(position=position,orientation=orientation),
            lambda transform,surface:NS(transform=transform,surface=surface), self.placement)

    def test_legal_transform_moves_once_and_reports_detached_after(self):
        result = self.executor.execute(self.request)
        self.assertTrue(result['ok'])
        self.assertEqual('dispatched', result['outcome'])
        self.assertEqual((4.,5.,6.), result['after'][0])
        self.assertFalse(result['transaction_verified'])
        self.assertFalse(result['undo_recorded'])
        self.assertEqual(1, self.obj.calls)
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual(1, self.obj.calls)

    def test_rotation_and_floor_are_forwarded_to_native_method(self):
        result = self.executor.execute(replace(self.request, yaw=math.pi, level=2))
        self.assertAlmostEqual(1., result['after'][1][1])
        self.assertAlmostEqual(0., result['after'][1][3])
        self.assertEqual((10,2,0), result['after'][2])
        self.assertEqual(1, self.obj.calls)

    def test_opposite_quaternion_and_small_rounding_match(self):
        original = self.obj.move_to
        def move(**kwargs):
            original(**kwargs)
            self.obj.position.x += 0.000001
            self.obj.orientation.w *= -1
        self.obj.move_to = move
        self.assertEqual('dispatched', self.executor.execute(self.request)['outcome'])

    def test_placement_refusal_sends_no_mutation_or_retry(self):
        self.legal = False
        result = self.executor.execute(self.request)
        self.assertEqual('placement_refused', result['outcome'])
        self.assertFalse(result['dispatched'])
        self.assertEqual(0, self.obj.calls)
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)

    def test_after_effect_exception_is_unknown_and_never_replayed(self):
        self.obj.fail_after = True
        result = self.executor.execute(self.request)
        self.assertEqual('unresolved', result['outcome'])
        self.assertTrue(result['may_have_applied'])
        self.assertEqual(4., self.obj.position.x)
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual(1, self.obj.calls)

    def test_denial_or_changed_admission_never_reads_or_moves(self):
        for verdict in (False, None, 1, 'yes'):
            self.setUp()
            self.admission.verdict = verdict
            with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
            self.assertEqual([], self.reads)
        self.setUp()
        self.admission.callback = lambda req: object.__setattr__(req, 'object_id', '31')
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([], self.reads)

    def test_mode_context_identity_and_baseline_are_enforced(self):
        for change in ('mode', 'context', 'id', 'position', 'parent', 'sim', 'surface'):
            self.setUp()
            if change == 'mode': self.mode = False
            elif change == 'context': self.context = replace(self.context, zone_generation=2)
            elif change == 'id': self.obj.id = 31
            elif change == 'position': self.obj.position.x = 7.
            elif change == 'parent': self.obj.parent = object()
            elif change == 'sim': self.obj.is_sim = True
            else: self.obj.routing_surface.type = 1
            with self.subTest(change=change):
                with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
                self.assertEqual(0, self.obj.calls)

    def test_object_or_context_change_during_placement_stops_dispatch(self):
        for change in ('object', 'context', 'position'):
            self.setUp()
            def mutate():
                if change == 'object': self.obj = FakeObject()
                elif change == 'context': self.context = replace(self.context, zone_generation=2)
                else: self.obj.position.x = 10.
            self.placement_callback = mutate
            with self.subTest(change=change):
                with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
                self.assertEqual(0, self.obj.calls)

    def test_bad_placement_result_or_exception_is_refused(self):
        for result in (True, (1, ()), (True,), [True, ()]):
            self.setUp()
            self.executor._test = lambda *args, **kwargs: result
            with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
            self.assertEqual(0, self.obj.calls)
        self.setUp()
        def fail(*args, **kwargs): raise RuntimeError('PRIVATE')
        self.executor._test = fail
        with self.assertRaises(NativeRefusal) as error: self.executor.execute(self.request)
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_post_dispatch_context_loss_is_unresolved(self):
        def move_then_change(**kwargs):
            self.obj.calls += 1
            self.context = replace(self.context, zone_generation=2)
        self.obj.move_to = move_then_change
        result = self.executor.execute(self.request)
        self.assertEqual('unresolved', result['outcome'])
        self.assertTrue(result['dispatched'])
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual(1, self.obj.calls)

    def test_noop_native_call_is_not_reported_as_transaction_success(self):
        self.obj.move_to = lambda **kwargs: None
        result = self.executor.execute(self.request)
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual(result['before'], result['after'])
        self.assertTrue(result['may_have_applied'])

    def test_malformed_readers_or_factories_refuse_without_dispatch(self):
        self.executor._vector3 = lambda *args: (_ for _ in ()).throw(RuntimeError('PRIVATE'))
        with self.assertRaises(NativeRefusal) as error: self.executor.execute(self.request)
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertEqual(0, self.obj.calls)
        self.setUp()
        self.executor._context = lambda: {'context':self.context,
                                           'zone_running':True, 'in_build_buy':1}
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual(0, self.obj.calls)

    def test_detached_admission_context_and_original_request_cannot_redirect(self):
        def modify(req):
            object.__setattr__(req.context.process_epoch, 'pid', 124)
        self.admission.callback = modify
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([], self.reads)
        self.assertEqual(123, self.request.context.process_epoch.pid)

    def test_full_history_refuses_before_admission(self):
        self.executor._seen.update('%032x' % n for n in range(128))
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual(0, self.admission.calls)
        self.assertEqual([], self.reads)

    def test_invalid_inputs_are_refused_before_admission(self):
        cases = [replace(self.request, object_id='030'),
                 replace(self.request, destination=(1.,2.,float('nan'))),
                 replace(self.request, destination=(1.,2.,True)),
                 replace(self.request, yaw=float('inf')),
                 replace(self.request, level=True),
                 replace(self.request, before_orientation=(0.,0.,0.,2.)),
                 replace(self.request, before_surface=(10,0,True))]
        for request in cases:
            self.setUp()
            with self.subTest(request=request):
                with self.assertRaises(NativeRefusal): self.executor.execute(request)
                self.assertEqual(0, self.admission.calls)
                self.assertEqual([], self.reads)

    def test_wrong_thread_and_reentry_do_not_dispatch(self):
        errors = []
        def wrong_thread():
            try: self.executor.execute(self.request)
            except Exception as error: errors.append(error)
        worker = threading.Thread(target=wrong_thread)
        worker.start(); worker.join()
        self.assertEqual([NativeRefusal], [type(error) for error in errors])
        self.assertEqual([], self.reads)
        def reenter(req):
            with self.assertRaises(NativeRefusal):
                self.executor.execute(replace(self.request, request_id='3'*32))
        self.admission.callback = reenter
        self.assertTrue(self.executor.execute(self.request)['ok'])
        self.assertEqual(1, self.obj.calls)

    def test_no_automatic_undo_delete_purchase_or_swatch(self):
        for name in ('undo', 'redo', 'delete', 'place', 'swatch', 'create'):
            self.assertFalse(hasattr(self.executor, name))
