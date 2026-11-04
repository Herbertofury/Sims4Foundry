"""Native call-shape tests with fabricated services, never a running game."""
import ast
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'companion-src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion.native import NativeAPI, NativeRefusal, DenyAdmission
from foundry_companion.queue import (Request, ObserveArguments, SetSpeedArguments,
                                     EnterCasArguments, RequestExitMenuArguments)


def request(operation='observe'):
    arguments = {'observe':ObserveArguments(), 'live.set_speed':SetSpeedArguments(2),
                 'cas.enter':EnterCasArguments('30'), 'game.request_exit_menu':RequestExitMenuArguments()}[operation]
    return Request('1'*32,'2'*32,operation,arguments,
                   expected_zone_id=None if operation=='observe' else '10',
                   expected_household_id=None if operation=='observe' else '20')


class Admission(DenyAdmission):
    def __init__(self):
        self.read = True; self.mutate = True; self.cas = False; self.enter = None; self.scopes = []
    def require_read(self, request): return self.read
    def require_mutation(self, request, observation): return self.mutate
    @contextmanager
    def cas_scope(self, request, sim, client):
        if not self.cas: raise NativeRefusal('No qualified CAS preservation scope')
        self.scopes.append('enter')
        if self.enter: self.enter()
        try: yield
        finally: self.scopes.append('exit')


class NativeBindingTests(unittest.TestCase):
    def setUp(self):
        self.zone=NS(id=10,is_in_build_buy=False,is_zone_running=True)
        self.household=NS(id=20); self.client=NS(id=5)
        self.instance=object(); self.sim=NS(id=30,household_id=20,get_sim_instance=lambda:self.instance)
        self.clock=NS(clock_speed=1,now=lambda:NS(absolute_ticks=lambda:200))
        self.persistence=NS(get_save_slot_proto_buff=lambda:NS(slot_id=4),get_save_slot_proto_guid=lambda:99)
        self.services=NS(current_zone=lambda:self.zone,active_household=lambda:self.household,
                         client_manager=lambda:NS(get_first_client=lambda:self.client),
                         get_persistence_service=lambda:self.persistence,game_clock_service=lambda:self.clock,
                         time_service=lambda:NS(sim_now=NS(absolute_ticks=lambda:100)),
                         sim_info_manager=lambda:NS(get=lambda sid:self.sim if sid==30 else None))
        self.commands=[]; self.loads=[]; self.admission=Admission()
        self.on_speed=None; self.on_cas=None
        def speed(value, source, _connection):
            self.commands.append(('speed',value,source,_connection))
            if self.on_speed: self.on_speed()
            else: self.clock.clock_speed=('paused','one','two','three').index(value)
        def cas(value, _connection):
            self.commands.append(('cas',value,_connection))
            if self.on_cas: self.on_cas()
        self.modules={'server_commands.clock_commands':NS(set_speed=speed),
                      'sims4.commands':NS(client_cheat=lambda *args:self.commands.append(args)),
                      'server_commands.cas_commands':NS(modify_in_cas=cas),
                      'server_commands.argument_helpers':NS(OptionalTargetParam=lambda value:('target',value))}
        def load(name):
            self.loads.append(name)
            return self.modules[name]
        self.loader=load
        self.api=NativeAPI(self.services,self.admission,self.loader)

    def test_default_admission_refuses_before_services_or_command_imports(self):
        api=NativeAPI(None,command_loader=self.loader)
        for operation in ('observe','live.set_speed','cas.enter','game.request_exit_menu'):
            with self.assertRaises(NativeRefusal): api.execute(request(operation))
        self.assertEqual([],self.loads)
        self.assertEqual([],self.commands)

    def test_observation_keeps_distinct_clocks_and_exact_ids(self):
        result=self.api.execute(request())
        self.assertEqual('verified',result['outcome'])
        value=result['observation']
        self.assertEqual(('10','20','5','99'),tuple(value[k] for k in ('zone_id','household_id','client_id','save_guid')))
        self.assertEqual((100,200),(value['sim_ticks'],value['game_ticks']))
        self.assertFalse(result['game_qualified'])
        self.assertEqual([],self.loads)

    def test_unloaded_services_are_unknown_not_a_fabricated_mode(self):
        api=NativeAPI(None,self.admission,self.loader)
        result=api.execute(request())
        self.assertEqual('unverified',result['outcome'])
        self.assertIsNone(result['observation']['zone_id'])
        self.assertTrue(result['observation']['errors'])
        self.assertNotIn('mode',result['observation'])

    def test_non_owner_cannot_even_observe(self):
        errors=[]
        def work():
            try: self.api.execute(request())
            except NativeRefusal as error: errors.append(error)
        worker=threading.Thread(target=work);worker.start();worker.join(2)
        self.assertEqual(1,len(errors))
        self.assertEqual([],self.commands)

    def test_explicit_read_and_mutation_verdicts_required(self):
        for field in ('read','mutate'):
            for value in (False,None):
                setattr(self.admission,field,value)
                with self.assertRaises(NativeRefusal): self.api.execute(request('live.set_speed'))
                setattr(self.admission,field,True)
        self.assertEqual([],self.commands)
        self.assertEqual([],self.loads)

    def test_context_drift_and_build_mode_refuse(self):
        for object_,field,value in ((self.zone,'id',11),(self.household,'id',21),
                                    (self.zone,'is_zone_running',False),(self.zone,'is_in_build_buy',True)):
            previous=getattr(object_,field);setattr(object_,field,value)
            with self.assertRaises(NativeRefusal):self.api.execute(request('live.set_speed'))
            setattr(object_,field,previous)
        self.assertEqual([],self.commands)

    def test_speed_uses_native_call_and_independent_readback(self):
        result=self.api.execute(request('live.set_speed'))
        self.assertEqual([('speed','two','foundry.cli',5)],self.commands)
        self.assertEqual('verified',result['outcome'])
        self.assertFalse(result['persistence_verified'])

    def test_native_speed_return_without_change_is_not_success(self):
        self.on_speed=lambda:None
        self.assertEqual('unverified',self.api.execute(request('live.set_speed'))['outcome'])
        self.assertEqual(1,len(self.commands))

    def test_post_speed_context_change_is_not_success(self):
        def change():self.clock.clock_speed=2;self.zone.id=11
        self.on_speed=change
        self.assertEqual('unverified',self.api.execute(request('live.set_speed'))['outcome'])

    def test_speed_changed_save_or_mode_is_not_verified(self):
        changes=(lambda:setattr(self.persistence,'get_save_slot_proto_guid',lambda:100),
                 lambda:setattr(self.persistence,'get_save_slot_proto_buff',lambda:NS(slot_id=5)),
                 lambda:setattr(self.zone,'is_in_build_buy',True),
                 lambda:setattr(self.zone,'is_zone_running',False))
        for change in changes:
            self.setUp()
            def changed(): self.clock.clock_speed=2;change()
            self.on_speed=changed
            self.assertEqual('unverified',self.api.execute(request('live.set_speed'))['outcome'])
            self.assertEqual(1,len(self.commands))

    def test_command_import_context_change_aborts_before_native_call(self):
        def loader(name):
            self.persistence.get_save_slot_proto_guid=lambda:100
            return self.loader(name)
        api=NativeAPI(self.services,self.admission,loader)
        with self.assertRaises(NativeRefusal):api.execute(request('live.set_speed'))
        self.assertEqual([],self.commands)

    def test_unknown_native_speed_is_not_coerced(self):
        for speed in (True,'2',2.1,4):
            self.clock.clock_speed=speed
            self.assertEqual('unverified',self.api.execute(request())['outcome'])
            with self.assertRaises(NativeRefusal):self.api.execute(request('live.set_speed'))
        self.assertEqual([],self.commands)

    def test_quit_only_requests_menu(self):
        result=self.api.execute(request('game.request_exit_menu'))
        self.assertEqual([('quit',5)],self.commands)
        self.assertEqual('unverified',result['outcome'])
        self.assertFalse(result['game_exit_verified'])
        self.assertFalse(result['save_choice_made'])
        self.client=None
        with self.assertRaises(NativeRefusal):self.api.execute(request('game.request_exit_menu'))
        self.assertEqual(1,len(self.commands))

    def test_cas_refuses_without_preservation_scope(self):
        with self.assertRaises(NativeRefusal):self.api.execute(request('cas.enter'))
        self.assertEqual([],self.commands)

    def test_cas_call_is_exact_target_and_not_full_edit(self):
        self.admission.cas=True
        result=self.api.execute(request('cas.enter'))
        self.assertEqual([('cas',('target','30'),5)],self.commands)
        self.assertEqual(['enter','exit'],self.admission.scopes)
        self.assertEqual('unverified',result['outcome'])
        self.assertFalse(result['cas_visibility_verified'])
        self.assertFalse(result['save_persistence_verified'])

    def test_cas_requires_instanced_owned_sim(self):
        self.admission.cas=True
        self.instance=None
        with self.assertRaises(NativeRefusal):self.api.execute(request('cas.enter'))
        self.instance=object();self.sim.household_id=21
        with self.assertRaises(NativeRefusal):self.api.execute(request('cas.enter'))
        self.assertEqual([],self.commands)

    def test_cas_preparation_drift_never_submits(self):
        self.admission.cas=True
        changes=(lambda:setattr(self.client,'id',6),lambda:setattr(self.sim,'household_id',21),
                 lambda:setattr(self,'instance',object()))
        for change in changes:
            self.setUp();self.admission.cas=True;self.admission.enter=change
            with self.assertRaises(NativeRefusal):self.api.execute(request('cas.enter'))
            self.assertEqual([],self.commands)
            self.assertEqual(['enter','exit'],self.admission.scopes)

    def test_companion_native_source_parses_as_python37(self):
        source=(SOURCE/'foundry_companion'/'native.py').read_text()
        if sys.version_info[:2] == (3,7): ast.parse(source)
        else: ast.parse(source,feature_version=(3,7))
        self.assertNotIn('apex_core',source)
        self.assertNotIn('SendInput',source)
        self.assertNotIn('fulleditmode',source)
