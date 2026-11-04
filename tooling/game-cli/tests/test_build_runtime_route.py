"""Owned fake core and callbacks exercise the real shared runtime/queue route.

Only listener start is suppressed: no socket, native/game module or profile is
opened. Admission, immutable transport records, owner tick and queue are real.
"""
from dataclasses import replace
import math
from pathlib import Path
import sys
from time import monotonic
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'companion-src'))
from foundry_companion import owner_runtime as runtime, build_wire as wire
from foundry_companion.build_observation import BuildContext
from foundry_companion.build_request import (BuildRequest, BuildExpectedContext, BuildSurface,
    BuildCaptureArguments, BuildTransformArguments, request_sha256)
from foundry_companion.queue import ProcessEpoch
from foundry_companion.native import NativeRefusal
from foundry_companion.transport import DenyHostPolicy
import test_build_executor as fixtures

class FixturePolicy(DenyHostPolicy):
    def authorize_submit(self,*args):return True
    def authorize_dispatch(self,*args):return True

class BuildRuntimeRouteTests(unittest.TestCase):
    def setUp(self):
        self.session,self.epoch='2'*32,ProcessEpoch(123,456)
        self.context=BuildContext(self.session,self.epoch,'10','20','5',4,'99',1)
        self.expected=BuildExpectedContext('10','20','5',4,'99',1)
        self.capture=BuildRequest('1'*32,self.session,self.epoch,
            BuildCaptureArguments('30','40',(1.,2.,3.),(0.,0.,0.,1.),BuildSurface('10',0,0)),
            self.expected,'a'*64)
        self.obj=fixtures.Object();self.lookups=[];self.core=ModuleType('owned_fake_core')
        self.original=lambda:'original-result';self.core.on_tick=self.original
        self.services=ModuleType('owned_fake_services');self.placement_hook=None
    def require_build_request(self,request):return True
    def object_reader(self,identity):
        self.lookups.append((identity,self.obj));return self.obj
    def context_reader(self):return dict(context=self.context,zone_running=True,in_build_buy=True)
    def placement(self,obj,location):
        if self.placement_hook:self.placement_hook()
        return True,()
    def make_owner(self,provider=True):
        readers=runtime.BuildReaders('a'*64,self.context_reader,self.object_reader,
            lambda x,y,z:NS(x=x,y=y,z=z),
            lambda yaw:NS(x=0.,y=math.sin(yaw/2),z=0.,w=math.cos(yaw/2)),
            lambda zone,level,kind:NS(primary_id=zone,secondary_id=level,type=kind),0,
            lambda p,q:NS(position=p,orientation=q),lambda t,s:NS(transform=t,surface=s),
            self.placement,self)
        owner=runtime.OwnerRuntime(self.session,self.epoch,
            module_loader=lambda name:self.services if name=='services' else self.core,
            host_policy=FixturePolicy(),build_readers=readers if provider else None)
        # Keep the actual shared transport/queue; suppress only listener creation.
        with patch.object(owner._transport,'start',return_value=('127.0.0.1',1)):
            owner.install()
        self.addCleanup(owner.close)
        return owner
    def transform(self,request_id='3'*32):
        a=self.capture.arguments
        return BuildRequest(request_id,self.session,self.epoch,
            BuildTransformArguments(a.object_id,a.definition_id,a.before_position,a.before_orientation,
                a.before_surface,self.capture.request_id,request_sha256(self.capture),(4.,5.,6.),0.,0),
            self.expected,'a'*64,operation='build.transform_selected')
    def submit(self,owner,request):
        return owner._transport._handle(wire.request_wire(request),monotonic()+2)[0]
    def receipt(self,owner,request):
        return owner._transport._handle(dict(schema=4,kind='receipt_read',request_id=request.request_id,
            session_id=request.session_id,process_epoch=dict(pid=123,creation_filetime=456),
            request_sha256=request_sha256(request)),monotonic()+2)[0]
    def capture_once(self,owner):
        self.assertEqual('accepted',self.submit(owner,self.capture)['state'])
        self.assertEqual('original-result',self.core.on_tick())
        self.assertEqual('captured',self.receipt(owner,self.capture)['adapter_result']['action_status'])
    def test_capture_transform_retains_exact_object_and_sticky_latch(self):
        owner=self.make_owner();self.capture_once(owner)
        self.assertIs(self.obj,owner._build._selections[self.capture.request_id][1])
        move=self.transform();self.assertEqual('accepted',self.submit(owner,move)['state'])
        self.core.on_tick();result=self.receipt(owner,move)
        self.assertEqual('adapter_returned',result['state'])
        self.assertEqual('dispatched',result['adapter_result']['action_status'])
        self.assertEqual('unverified',result['adapter_result']['outcome'])
        self.assertEqual(1,self.obj.calls)
        self.assertTrue(all(obj is self.obj for _,obj in self.lookups))
        self.assertIsNone(owner._build._selections[self.capture.request_id])
        self.assertIsNotNone(owner._transport.queue_status().mutation_blocked_reason)
        self.assertEqual('refused',self.submit(owner,self.transform('4'*32))['state'])
        self.assertEqual('adapter_returned',self.submit(owner,move)['state'])
        self.core.on_tick();self.assertEqual(1,self.obj.calls)
    def test_missing_provider_never_captures_or_moves(self):
        owner=self.make_owner(False);self.submit(owner,self.capture);self.core.on_tick()
        result=self.receipt(owner,self.capture)
        self.assertEqual(('unknown','execution_exception'),(result['state'],result['reason']))
        self.assertIsNone(result['adapter_result']);self.assertEqual([],self.lookups)
        self.assertEqual(0,self.obj.calls)
    def test_replacement_same_id_never_moves_either_object(self):
        owner=self.make_owner();self.capture_once(owner);retained=self.obj;self.obj=fixtures.Object()
        move=self.transform();self.submit(owner,move);self.core.on_tick()
        self.assertEqual('unknown',self.receipt(owner,move)['state'])
        self.assertEqual(0,retained.calls+self.obj.calls)
        self.assertIsNone(owner._build._selections[self.capture.request_id])
        self.assertIsNotNone(owner._transport.queue_status().mutation_blocked_reason)
    def test_callback_time_hook_replacement_blocks_move_and_invalidates_selection(self):
        owner=self.make_owner();self.capture_once(owner)
        old=self.core.on_tick
        replacement=lambda:old()
        self.placement_hook=lambda:setattr(self.core,'on_tick',replacement)
        move=self.transform();self.submit(owner,move);old()
        self.assertIs(replacement,self.core.on_tick)
        self.assertEqual(0,self.obj.calls)
        self.assertEqual('unknown',self.receipt(owner,move)['state'])
        self.assertTrue(all(row is None for row in owner._build._selections.values()))
        self.assertIsNotNone(owner._transport.queue_status().mutation_blocked_reason)
        # The replacement may chain our old hook but cannot reactivate dispatch.
        self.core.on_tick();self.assertEqual(0,self.obj.calls)

    def test_hook_replacement_and_close_release_selection(self):
        for close in (False,True):
            self.setUp();owner=self.make_owner();self.capture_once(owner)
            if close:
                owner.close();self.assertIs(self.original,self.core.on_tick)
            else:
                old=self.core.on_tick
                replacement=lambda:old()
                self.core.on_tick=replacement;self.core.on_tick()
                self.assertEqual('hook_replaced',owner.status.state)
                self.assertIs(replacement,self.core.on_tick)
            self.assertTrue(owner._build._closed)
            self.assertTrue(all(row is None for row in owner._build._selections.values()))
            with self.assertRaises(NativeRefusal):owner._build.execute(self.transform())
            self.assertEqual(0,self.obj.calls)

if __name__=='__main__':unittest.main()
