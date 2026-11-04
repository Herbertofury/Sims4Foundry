"""Retained-object identity fixtures only. No game, profile or native calls."""
from dataclasses import replace
import math
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'companion-src'))
from foundry_companion.build_executor import BuildExecutor
from foundry_companion.build_request import (BuildRequest, BuildExpectedContext, BuildSurface,
    BuildCaptureArguments, BuildTransformArguments, request_sha256)
from foundry_companion.build_observation import BuildContext
from foundry_companion.native import NativeRefusal
from foundry_companion.queue import ProcessEpoch


class Object:
    def __init__(self):
        self.id, self.definition, self.parent, self.is_sim = 30, NS(id=40), None, False
        self.position = NS(x=1., y=2., z=3.)
        self.orientation = NS(x=0., y=0., z=0., w=1.)
        self.routing_surface = NS(primary_id=10, secondary_id=0, type=0)
        self.calls, self.callback = 0, None
    def move_to(self, **kwargs):
        self.calls += 1
        self.position, self.orientation, self.routing_surface = (kwargs['translation'],
            kwargs['orientation'], kwargs['routing_surface'])
        if self.callback:
            self.callback()


class BuildExecutorTests(unittest.TestCase):
    def setUp(self):
        self.epoch, self.session, self.deployment = ProcessEpoch(123, 456), '2'*32, 'a'*64
        self.context = BuildContext(self.session, self.epoch, '10', '20', '5', 4, '99', 1)
        self.expected = BuildExpectedContext('10', '20', '5', 4, '99', 1)
        self.args = BuildCaptureArguments('30', '40', (1.,2.,3.), (0.,0.,0.,1.), BuildSurface('10',0,0))
        self.capture = BuildRequest('1'*32, self.session, self.epoch, self.args, self.expected, self.deployment)
        self.obj = Object()
        self.now, self.mode, self.allowed = 1., True, True
        self.admission_callback = self.placement_callback = None
        self.reads = self.admissions = 0
        self.executor = self.make_executor()
    def read_context(self):
        self.reads += 1
        return {'context':self.context, 'zone_running':True, 'in_build_buy':self.mode}
    def require_build_request(self, request):
        self.admissions += 1
        if self.admission_callback:
            self.admission_callback(request)
        return self.allowed
    def placement(self, obj, location):
        if self.placement_callback:
            self.placement_callback()
        return True, ()
    def make_executor(self, admission=True):
        return BuildExecutor(self.session, self.epoch, self.deployment, self.read_context,
            lambda object_id:self.obj, lambda x,y,z:NS(x=x,y=y,z=z),
            lambda yaw:NS(x=0.,y=math.sin(yaw/2),z=0.,w=math.cos(yaw/2)),
            lambda zone,level,kind:NS(primary_id=zone,secondary_id=level,type=kind), 0,
            lambda p,q:NS(position=p,orientation=q), lambda t,s:NS(transform=t,surface=s),
            self.placement, admission=self if admission else None, clock=lambda:self.now, owner_active=lambda:True)
    def move_request(self, request_id='3'*32):
        return BuildRequest(request_id, self.session, self.epoch,
            BuildTransformArguments('30','40',(1.,2.,3.),(0.,0.,0.,1.),BuildSurface('10',0,0),
                self.capture.request_id, request_sha256(self.capture), (4.,5.,6.),0.,0),
            self.expected,self.deployment,operation='build.transform_selected')
    def capture_once(self):
        result = self.executor.execute(self.capture)
        self.assertEqual('captured', result['action_status'])
        self.assertFalse(result['game_qualified'])
    def test_capture_and_single_transform(self):
        self.capture_once()
        result = self.executor.execute(self.move_request())
        self.assertEqual('dispatched', result['action_status'])
        self.assertEqual('unverified', result['outcome'])
        self.assertFalse(result['undo_recorded'])
        self.assertEqual(1,self.obj.calls)
        for request in (self.move_request(), self.move_request('4'*32), self.capture):
            with self.assertRaises(NativeRefusal): self.executor.execute(request)
        self.assertEqual(1,self.obj.calls)
    def test_original_object_replacement_same_id_and_pose_refuses(self):
        self.capture_once()
        original = self.obj
        self.obj = Object()
        with self.assertRaises(NativeRefusal): self.executor.execute(self.move_request())
        self.assertEqual(0,self.obj.calls + original.calls)
        self.obj = original
        with self.assertRaises(NativeRefusal): self.executor.execute(self.move_request('4'*32))
    def test_changes_during_placement_and_late_admission_refuse(self):
        for change in ('replacement','definition','pose','context','mode','expiry','denial'):
            self.setUp(); self.capture_once(); original = self.obj
            def mutate():
                if change == 'replacement': self.obj = Object()
                elif change == 'definition': self.obj.definition.id = 41
                elif change == 'pose': self.obj.position.x = 9.
                elif change == 'context': self.context = replace(self.context, save_guid='100')
                elif change == 'mode': self.mode = False
                elif change == 'expiry': self.now = 6.
                else: self.allowed = False
            self.placement_callback = mutate
            with self.subTest(change=change):
                with self.assertRaises(NativeRefusal): self.executor.execute(self.move_request())
                self.assertEqual(0,self.obj.calls + original.calls)
    def test_invalid_binding_denies_before_native_reader(self):
        for change in (dict(session_id='5'*32),dict(process_epoch=ProcessEpoch(123,457)),
                       dict(deployment_sha256='b'*64)):
            with self.subTest(change=change):
                with self.assertRaises(NativeRefusal): self.executor.execute(replace(self.capture,**change))
                self.assertEqual(0,self.reads)
    def test_default_denied_and_detached_admission(self):
        self.executor = self.make_executor(False)
        with self.assertRaises(NativeRefusal): self.executor.execute(self.capture)
        self.assertEqual(0,self.reads)
        self.setUp()
        def mutate(view): object.__setattr__(view.arguments, 'object_id', '31')
        self.admission_callback = mutate
        with self.assertRaises(NativeRefusal): self.executor.execute(self.capture)
        self.assertEqual(0,self.reads)
        self.assertEqual('30',self.capture.arguments.object_id)
    def test_expiry_and_observed_mode_exit_permanently_release_reference(self):
        for change in ('expiry','mode','context','clock','closed'):
            self.setUp(); self.capture_once()
            if change == 'expiry': self.now = 6.
            elif change == 'mode': self.mode = False
            elif change == 'context': self.context = replace(self.context,zone_generation=2)
            elif change == 'clock': self.now = 0.
            else: self.executor.close()
            self.executor.poll_context()
            self.now, self.mode = 6., True
            self.context = replace(self.context,zone_generation=1)
            with self.subTest(change=change):
                with self.assertRaises(NativeRefusal): self.executor.execute(self.move_request())
                self.assertTrue(all(r is None for r in self.executor._selections.values()))
                self.assertEqual(0,self.obj.calls)
    def test_selection_payload_mismatch_consumes(self):
        self.capture_once()
        wrong = replace(self.move_request(), arguments=replace(self.move_request().arguments,definition_id='41'))
        with self.assertRaises(NativeRefusal): self.executor.execute(wrong)
        with self.assertRaises(NativeRefusal): self.executor.execute(self.move_request('4'*32))
        self.assertEqual(0,self.obj.calls)
    def test_capture_numeric_spelling_and_signed_zero_are_bound(self):
        for changed in ((1,2.,3.), (1.,2,3.)):
            self.setUp();self.capture_once()
            request = self.move_request()
            request = replace(request,arguments=replace(request.arguments,before_position=changed))
            with self.assertRaises(NativeRefusal):self.executor.execute(request)
            self.assertEqual(0,self.obj.calls)
        self.setUp();self.capture_once()
        request=self.move_request()
        request=replace(request,arguments=replace(request.arguments,before_orientation=(-0.,0.,0.,1.)))
        with self.assertRaises(NativeRefusal):self.executor.execute(request)
        self.assertEqual(0,self.obj.calls)
    def test_owner_predicate_is_required_and_refreshed_after_placement(self):
        self.executor._owner_active = None
        with self.assertRaises(NativeRefusal):self.executor.execute(self.capture)
        self.assertEqual(0,self.reads)
        self.setUp();self.capture_once()
        self.placement_callback=lambda:setattr(self.executor,'_owner_active',lambda:False)
        with self.assertRaises(NativeRefusal):self.executor.execute(self.move_request())
        self.assertEqual(0,self.obj.calls)
    def test_after_effect_failure_never_replays(self):
        self.capture_once()
        self.obj.callback = lambda: (_ for _ in ()).throw(RuntimeError('private'))
        result = self.executor.execute(self.move_request())
        self.assertEqual('unresolved',result['action_status'])
        self.assertTrue(result['may_have_applied'])
        with self.assertRaises(NativeRefusal): self.executor.execute(self.move_request('4'*32))
        self.assertEqual(1,self.obj.calls)
    def test_reentrant_and_wrong_owner_requests_refuse(self):
        errors = []
        def run():
            try:self.executor.execute(self.capture)
            except Exception as error:errors.append(error)
        worker=threading.Thread(target=run);worker.start();worker.join()
        self.assertEqual([NativeRefusal],[type(e) for e in errors])
        def reenter(view):
            with self.assertRaises(NativeRefusal):self.executor.execute(replace(self.capture,request_id='4'*32))
        self.admission_callback = reenter
        self.capture_once()
    def test_history_capacity_never_evicts_or_rebinds(self):
        self.executor._seen.update('%032x'%i for i in range(128))
        with self.assertRaises(NativeRefusal):self.executor.execute(self.capture)
        self.assertEqual(0,self.admissions)


if __name__ == '__main__':
    unittest.main()
