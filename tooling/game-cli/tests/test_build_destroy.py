"""Fabricated direct destruction tests; never import or contact the game."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'companion-src'))
sys.path.insert(0, str(ROOT / 'build-adapter-src'))
from build_destroy import BuildDestroyExecutor, DestroyRequest
from foundry_companion.build_observation import BuildContext
from foundry_companion.native import NativeRefusal
from foundry_companion.queue import ProcessEpoch


class FakeObject:
    def __init__(self):
        self.id, self.parent, self.is_sim = 30, None, False
        self.position = NS(x=1., y=2., z=3.)
        self.orientation = NS(x=0., y=0., z=0., w=1.)
        self.routing_surface = NS(primary_id=10, secondary_id=0, type=0)
        self.calls, self.callback = 0, None
    def destroy(self):
        self.calls += 1
        if self.callback: self.callback()


class Admission:
    def __init__(self):
        self.verdict, self.callback, self.calls = True, None, 0
    def require_build_destroy(self, request):
        self.calls += 1
        if self.callback: self.callback(request)
        return self.verdict


class DestroyTests(unittest.TestCase):
    def setUp(self):
        self.context = BuildContext('2'*32, ProcessEpoch(123,456), '10','20','5',4,'99',1)
        self.request = DestroyRequest('1'*32, self.context, '30', (1.,2.,3.),
                                      (0.,0.,0.,1.), (10,0,0))
        self.obj, self.admission = FakeObject(), Admission()
        self.mode, self.present, self.reads = True, True, []
        self.executor = BuildDestroyExecutor(self.read_context, self.read_object,
                                             self.admission, 0)
    def read_context(self):
        self.reads.append('context')
        return {'context':self.context, 'zone_running':True, 'in_build_buy':self.mode}
    def read_object(self, value):
        self.reads.append(('object',value))
        return self.obj if self.present else None
    def test_one_dispatch_with_absence_observation(self):
        self.obj.callback = lambda: setattr(self, 'present', False)
        result = self.executor.execute(self.request)
        self.assertEqual('dispatched', result['outcome'])
        self.assertTrue(result['object_absent'])
        self.assertFalse(result['transaction_verified'])
        self.assertFalse(result['undo_recorded'])
        self.assertFalse(result['refund_verified'])
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual(1, self.obj.calls)
    def test_native_noop_reports_still_present(self):
        result = self.executor.execute(self.request)
        self.assertEqual('unresolved', result['outcome'])
        self.assertFalse(result['object_absent'])
        self.assertTrue(result['may_have_applied'])
        self.assertEqual(1, self.obj.calls)
    def test_effect_then_exception_is_unknown_not_replayed(self):
        def apply_then_fail():
            self.present = False
            raise RuntimeError('lost reply')
        self.obj.callback = apply_then_fail
        result = self.executor.execute(self.request)
        self.assertEqual('unresolved', result['outcome'])
        self.assertTrue(result['may_have_applied'])
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual(1, self.obj.calls)
    def test_post_dispatch_context_change_is_unknown(self):
        self.obj.callback = lambda: setattr(self, 'mode', False)
        result = self.executor.execute(self.request)
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual(1, self.obj.calls)
    def test_admission_requires_exact_scope_and_detached_unchanged_request(self):
        for value in (None, False, 1):
            self.setUp()
            self.admission.verdict = value
            with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
            self.assertEqual([], self.reads)
        self.setUp()
        self.admission.callback = lambda req: object.__setattr__(req.context.process_epoch, 'pid', 124)
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual([], self.reads)
        self.assertEqual(123, self.request.context.process_epoch.pid)
    def test_stale_context_identity_or_object_state_stops_dispatch(self):
        for case in ('mode', 'context', 'id', 'position', 'parent', 'sim', 'surface'):
            self.setUp()
            if case == 'mode': self.mode = False
            elif case == 'context': self.context = replace(self.context, zone_generation=2)
            elif case == 'id': self.obj.id = 31
            elif case == 'position': self.obj.position.x = 9
            elif case == 'parent': self.obj.parent = object()
            elif case == 'sim': self.obj.is_sim = True
            else: self.obj.routing_surface.type = 1
            with self.subTest(case=case):
                with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
                self.assertEqual(0, self.obj.calls)
    def test_replaced_object_before_dispatch_is_refused(self):
        original = self.obj
        count = [0]
        def read(value):
            count[0] += 1
            return original if count[0] == 1 else FakeObject()
        self.executor._object = read
        with self.assertRaises(NativeRefusal): self.executor.execute(self.request)
        self.assertEqual(0, original.calls)
    def test_invalid_input_and_full_history_refuse_before_admission(self):
        for req in (replace(self.request, object_id='030'),
                    replace(self.request, before_position=(1,2,float('nan'))),
                    replace(self.request, before_orientation=(0,0,0,2)),
                    replace(self.request, before_surface=(10,0,True))):
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
        self.obj.callback = lambda: setattr(self, 'present', False)
        self.executor.execute(self.request)
        self.assertEqual(1, self.obj.calls)
    def test_no_implicit_undo_or_other_operations(self):
        for name in ('undo', 'redo', 'delete', 'place', 'move', 'swatch'):
            self.assertFalse(hasattr(self.executor, name))
