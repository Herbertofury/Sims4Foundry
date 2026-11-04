"""Native call-shape tests with fabricated services, never a running game."""
import ast
from dataclasses import replace
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'companion-src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion.native import NativeAPI, NativeRefusal, DenyAdmission, HostModeDeclaration
from foundry_companion.queue import (Request, ObserveArguments, SetSpeedArguments,
                                     EnterCasArguments, RequestExitMenuArguments, ProcessEpoch,
                                     QueueError)


OPERATIONS = ('observe','live.set_speed','cas.enter','game.request_exit_menu')


def request(operation='observe'):
    arguments = {'observe':ObserveArguments(), 'live.set_speed':SetSpeedArguments(2),
                 'cas.enter':EnterCasArguments('30'), 'game.request_exit_menu':RequestExitMenuArguments()}[operation]
    return Request('1'*32,'2'*32,operation,arguments,
                   expected_zone_id=None if operation=='observe' else '10',
                   expected_household_id=None if operation=='observe' else '20',
                   process_epoch=ProcessEpoch(123,456),
                   expected_save_slot=None if operation=='observe' else 4,
                   expected_save_guid=None if operation=='observe' else '99')


class Admission(DenyAdmission):
    def __init__(self):
        self.read = True; self.mutate = True; self.cas = False; self.enter = None; self.scopes = []
        self.calls = []
        self.mode = 'live'; self.mode_calls = []
    def require_read(self, request):
        self.calls.append('read')
        return self.read
    def require_mutation(self, request, observation):
        self.calls.append('mutation')
        return self.mutate
    def observe_mode(self, request, observation, sample_token):
        self.mode_calls.append((request, observation, sample_token))
        return HostModeDeclaration(request, observation['client_id'], sample_token, self.mode)
    @contextmanager
    def cas_scope(self, request, sim, client):
        self.calls.append('scope')
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
        self.service_reads=[]
        for name,function in list(vars(self.services).items()):
            def observed(name=name,function=function):
                self.service_reads.append(name)
                return function()
            setattr(self.services,name,observed)
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
        self.api=NativeAPI(self.services,self.admission,self.loader,
                           session_id='2'*32,process_epoch=ProcessEpoch(123,456))

    def assert_untouched(self):
        self.assertEqual([],self.service_reads)
        self.assertEqual([],self.admission.calls)
        self.assertEqual([],self.loads)
        self.assertEqual([],self.admission.scopes)
        self.assertEqual([],self.commands)

    def test_unbound_api_refuses_before_any_admission_or_game_access(self):
        api=NativeAPI(self.services,self.admission,self.loader)
        for operation in OPERATIONS:
            with self.assertRaises(NativeRefusal):api.execute(request(operation))
        self.assert_untouched()

    def test_invalid_configured_binding_is_rejected_without_game_access(self):
        bindings=((None,ProcessEpoch(123,456)),('2'*32,None),
                  ('2'*31,ProcessEpoch(123,456)),('A'*32,ProcessEpoch(123,456)),
                  (True,ProcessEpoch(123,456)),('2'*32,NS(pid=123,creation_filetime=456)))
        for session,epoch in bindings:
            with self.subTest(session=session,epoch=epoch):
                with self.assertRaises(NativeRefusal):
                    NativeAPI(self.services,self.admission,self.loader,
                              session_id=session,process_epoch=epoch)
        self.assert_untouched()

    def test_invalid_configured_epoch_fields_are_not_coerced(self):
        for field,value in (('pid',True),('pid','123'),('pid',0),('pid',2**32),
                            ('creation_filetime',True),('creation_filetime','456'),
                            ('creation_filetime',0),('creation_filetime',2**64)):
            with self.subTest(field=field,value=value):
                epoch=ProcessEpoch(123,456)
                object.__setattr__(epoch,field,value)
                with self.assertRaises(NativeRefusal):
                    NativeAPI(self.services,self.admission,self.loader,
                              session_id='2'*32,process_epoch=epoch)
        self.assert_untouched()

    def test_wrong_configured_binding_refuses_before_game_access(self):
        for session,epoch in (('3'*32,ProcessEpoch(123,456)),
                              ('2'*32,ProcessEpoch(124,456)),
                              ('2'*32,ProcessEpoch(123,457))):
            api=NativeAPI(self.services,self.admission,self.loader,
                          session_id=session,process_epoch=epoch)
            for operation in OPERATIONS:
                with self.assertRaises(NativeRefusal):api.execute(request(operation))
        self.assert_untouched()

    def test_request_session_and_epoch_must_match_before_game_access(self):
        changes=(dict(session_id='3'*32),dict(process_epoch=None),
                 dict(process_epoch=ProcessEpoch(124,456)),
                 dict(process_epoch=ProcessEpoch(123,457)))
        for operation in OPERATIONS:
            for change in changes:
                with self.subTest(operation=operation,change=change):
                    with self.assertRaises(NativeRefusal):
                        self.api.execute(replace(request(operation),**change))
        self.assert_untouched()

    def test_mutations_need_both_save_preconditions_before_game_access(self):
        for operation in OPERATIONS[1:]:
            for fields in (('expected_save_slot','expected_save_guid'),
                           ('expected_save_slot',),('expected_save_guid',)):
                value=request(operation)
                # Partial save context is already invalid to Request; both absent
                # remains valid only for the pure legacy queue test interface.
                for field in fields:object.__setattr__(value,field,None)
                with self.subTest(operation=operation,fields=fields):
                    with self.assertRaises((NativeRefusal,QueueError)):self.api.execute(value)
        self.assert_untouched()

    def test_configured_epoch_is_copied_not_aliased(self):
        for field,value in (('pid',124),('creation_filetime',457)):
            self.setUp()
            epoch=ProcessEpoch(123,456)
            api=NativeAPI(self.services,self.admission,self.loader,
                          session_id='2'*32,process_epoch=epoch)
            object.__setattr__(epoch,field,value)
            with self.assertRaises(NativeRefusal):api.execute(replace(request(),process_epoch=epoch))
            self.assert_untouched()
            self.assertEqual('verified',api.execute(request())['outcome'])

    def test_execute_copies_complete_caller_request_before_hooks(self):
        original=request('live.set_speed')
        expected=request('live.set_speed')
        seen=[]
        def read(view):
            seen.append(view)
            self.assertEqual(expected,view)
            self.assertIsNot(original,view)
            self.assertIsNot(original.arguments,view.arguments)
            self.assertIsNot(original.process_epoch,view.process_epoch)
            object.__setattr__(original,'request_id','3'*32)
            object.__setattr__(original,'session_id','4'*32)
            object.__setattr__(original,'operation','game.request_exit_menu')
            object.__setattr__(original.arguments,'speed',3)
            object.__setattr__(original.process_epoch,'pid',124)
            object.__setattr__(original.process_epoch,'creation_filetime',457)
            object.__setattr__(original,'ttl_seconds',1.0)
            object.__setattr__(original,'expected_zone_id','11')
            object.__setattr__(original,'expected_household_id','21')
            object.__setattr__(original,'expected_save_slot',5)
            object.__setattr__(original,'expected_save_guid','100')
            return True
        self.admission.require_read=read
        result=self.api.execute(original)
        self.assertEqual('verified',result['outcome'])
        self.assertEqual([('speed','two','foundry.cli',5)],self.commands)
        self.assertEqual(2,len(seen))
        self.assertIsNot(seen[0],seen[1])
        self.assertIsNot(seen[0].arguments,seen[1].arguments)
        self.assertIsNot(seen[0].process_epoch,seen[1].process_epoch)

    def test_read_hook_cannot_rewrite_any_admitted_request_value(self):
        changes=(lambda value:object.__setattr__(value,'request_id','3'*32),
                 lambda value:object.__setattr__(value,'session_id','3'*32),
                 lambda value:object.__setattr__(value,'operation','observe'),
                 lambda value:object.__setattr__(value.arguments,'speed',3),
                 lambda value:object.__setattr__(value.process_epoch,'pid',124),
                 lambda value:object.__setattr__(value.process_epoch,'creation_filetime',457),
                 lambda value:object.__setattr__(value,'ttl_seconds',1.0),
                 lambda value:object.__setattr__(value,'expected_zone_id','11'),
                 lambda value:object.__setattr__(value,'expected_household_id','21'),
                 lambda value:object.__setattr__(value,'expected_save_slot',5),
                 lambda value:object.__setattr__(value,'expected_save_guid','100'))
        for index,change in enumerate(changes):
            with self.subTest(change=index):
                self.setUp()
                def read(value):change(value);return True
                self.admission.require_read=read
                with self.assertRaises(NativeRefusal):self.api.execute(request('live.set_speed'))
                self.assert_untouched()

    def test_mutation_hook_cannot_rewrite_admitted_request_or_argument(self):
        for change in (lambda value:object.__setattr__(value.arguments,'speed',3),
                       lambda value:object.__setattr__(value.process_epoch,'pid',124),
                       lambda value:object.__setattr__(value,'expected_save_slot',5)):
            self.setUp()
            def mutate(value,observation):change(value);return True
            self.admission.require_mutation=mutate
            with self.assertRaises(NativeRefusal):self.api.execute(request('live.set_speed'))
            self.assertTrue(self.service_reads)
            self.assertEqual([],self.loads)
            self.assertEqual([],self.admission.scopes)
            self.assertEqual([],self.commands)

    def test_admission_request_ttl_numeric_type_is_exact(self):
        for original,replacement in ((5.0,5),(5,5.0)):
            for hook in ('read','mutation'):
                with self.subTest(original=original,replacement=replacement,hook=hook):
                    self.setUp()
                    def changed(value,*unused):
                        object.__setattr__(value,'ttl_seconds',replacement)
                        return True
                    setattr(self.admission,'require_'+hook,changed)
                    value=replace(request('live.set_speed'),ttl_seconds=original)
                    with self.assertRaises(NativeRefusal):self.api.execute(value)
                    if hook=='read':self.assertEqual([],self.service_reads)
                    self.assertEqual([],self.loads)
                    self.assertEqual([],self.admission.scopes)
                    self.assertEqual([],self.commands)

    def test_retained_admission_requests_cannot_rewrite_later_execution(self):
        retained=[]
        def read(value):retained.append(value);return True
        def mutate(value,observation):retained.append(value);return True
        self.admission.require_read=read
        self.admission.require_mutation=mutate
        def loader(name):
            self.assertEqual(2,len(retained))
            for value in retained:
                object.__setattr__(value.arguments,'speed',3)
                object.__setattr__(value.process_epoch,'pid',124)
                object.__setattr__(value,'expected_save_slot',5)
            return self.loader(name)
        api=NativeAPI(self.services,self.admission,loader,
                      session_id='2'*32,process_epoch=ProcessEpoch(123,456))
        self.assertEqual('verified',api.execute(request('live.set_speed'))['outcome'])
        self.assertEqual([('speed','two','foundry.cli',5)],self.commands)
        self.assertEqual(4,len(retained))
        self.assertEqual(request('live.set_speed'),retained[-1])

    def test_mutation_observation_view_cannot_rewrite_readback_or_client(self):
        def mutate(value,observation):
            observation['client_id']='6'
            observation['errors']['fabricated']='rewritten'
            return True
        self.admission.require_mutation=mutate
        result=self.api.execute(request('live.set_speed'))
        self.assertEqual('verified',result['outcome'])
        self.assertEqual('5',result['before']['client_id'])
        self.assertEqual({},result['before']['errors'])
        self.assertEqual([('speed','two','foundry.cli',5)],self.commands)

    def test_retained_observation_cannot_hide_import_time_client_drift(self):
        retained=[]
        def mutate(value,observation):retained.append(observation);return True
        self.admission.require_mutation=mutate
        def loader(name):
            self.client.id=6
            for observation in retained:observation['client_id']='6'
            return self.loader(name)
        api=NativeAPI(self.services,self.admission,loader,
                      session_id='2'*32,process_epoch=ProcessEpoch(123,456))
        with self.assertRaises(NativeRefusal):api.execute(request('live.set_speed'))
        self.assertEqual([],self.commands)

    def test_retained_observation_cannot_hide_cas_scope_client_drift(self):
        self.admission.cas=True
        retained=[]
        def mutate(value,observation):retained.append(observation);return True
        self.admission.require_mutation=mutate
        def changed():
            self.client.id=6
            for observation in retained:observation['client_id']='6'
        self.admission.enter=changed
        with self.assertRaises(NativeRefusal):self.api.execute(request('cas.enter'))
        self.assertEqual([],self.commands)
        self.assertEqual(['enter','exit'],self.admission.scopes)

    def test_cas_scope_request_mutation_refuses_and_closes_scope(self):
        self.admission.cas=True
        original_scope=self.admission.cas_scope
        original=request('cas.enter')
        @contextmanager
        def scope(value,sim,client):
            self.assertIsNot(original,value)
            self.assertIsNot(original.arguments,value.arguments)
            self.assertIsNot(original.process_epoch,value.process_epoch)
            self.assertIs(self.sim,sim)
            self.assertIs(self.client,client)
            with original_scope(value,sim,client):
                object.__setattr__(value.arguments,'sim_id','31')
                yield
        self.admission.cas_scope=scope
        with self.assertRaises(NativeRefusal):self.api.execute(original)
        self.assertEqual([],self.commands)
        self.assertEqual(['enter','exit'],self.admission.scopes)

    def test_retained_scope_request_mutation_during_target_preparation_refuses(self):
        self.admission.cas=True
        original_scope=self.admission.cas_scope
        retained=[]
        @contextmanager
        def scope(value,sim,client):
            retained.append(value)
            with original_scope(value,sim,client):yield
        self.admission.cas_scope=scope
        def target(value):
            object.__setattr__(retained[0],'expected_save_guid','100')
            return ('target',value)
        self.modules['server_commands.argument_helpers'].OptionalTargetParam=target
        with self.assertRaises(NativeRefusal):self.api.execute(request('cas.enter'))
        self.assertEqual([],self.commands)
        self.assertEqual(['enter','exit'],self.admission.scopes)

    def test_default_admission_refuses_before_services_or_command_imports(self):
        api=NativeAPI(None,command_loader=self.loader,
                      session_id='2'*32,process_epoch=ProcessEpoch(123,456))
        for operation in OPERATIONS:
            with self.assertRaises(NativeRefusal): api.execute(request(operation))
        self.assertEqual([],self.loads)
        self.assertEqual([],self.commands)
        self.assertEqual([],self.service_reads)

    def test_explicit_save_context_is_rechecked_before_command_imports(self):
        for changes in (dict(expected_save_slot=5, expected_save_guid="99"),
                        dict(expected_save_slot=4, expected_save_guid="100")):
            with self.assertRaises(NativeRefusal):
                self.api.execute(replace(request('live.set_speed'), **changes))
        self.assertEqual([], self.loads)
        self.assertEqual([], self.commands)
        result = self.api.execute(replace(request('live.set_speed'), expected_save_slot=4, expected_save_guid="99"))
        self.assertEqual('verified', result['outcome'])

    def test_observation_keeps_distinct_clocks_and_exact_ids(self):
        result=self.api.execute(request())
        self.assertEqual('verified',result['outcome'])
        value=result['observation']
        self.assertEqual(('10','20','5','99'),tuple(value[k] for k in ('zone_id','household_id','client_id','save_guid')))
        self.assertEqual((100,200),(value['sim_ticks'],value['game_ticks']))
        self.assertFalse(result['game_qualified'])
        self.assertEqual([],self.loads)

    def test_unloaded_services_are_unknown_not_a_fabricated_mode(self):
        api=NativeAPI(None,self.admission,self.loader,
                      session_id='2'*32,process_epoch=ProcessEpoch(123,456))
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
        api=NativeAPI(self.services,self.admission,loader,
                      session_id='2'*32,process_epoch=ProcessEpoch(123,456))
        with self.assertRaises(NativeRefusal):api.execute(request('live.set_speed'))
        self.assertEqual([],self.commands)

    def test_command_import_cannot_bypass_read_or_mutation_readmission(self):
        for operation in OPERATIONS[1:]:
            for field in ('read','mutate'):
                with self.subTest(operation=operation,field=field):
                    self.setUp();self.admission.cas=True
                    def loader(name):
                        setattr(self.admission,field,False)
                        return self.loader(name)
                    api=NativeAPI(self.services,self.admission,loader,
                                  session_id='2'*32,process_epoch=ProcessEpoch(123,456))
                    with self.assertRaises(NativeRefusal):api.execute(request(operation))
                    self.assertTrue(self.loads)
                    self.assertEqual([],self.commands)
                    self.assertEqual([],self.admission.scopes)

    def test_final_mutation_admission_context_drift_never_submits(self):
        changes=(lambda:setattr(self.zone,'id',11),
                 lambda:setattr(self.household,'id',21),
                 lambda:setattr(self.client,'id',6),
                 lambda:setattr(self.persistence,'get_save_slot_proto_buff',lambda:NS(slot_id=5)),
                 lambda:setattr(self.persistence,'get_save_slot_proto_guid',lambda:100),
                 lambda:setattr(self.zone,'is_zone_running',False),
                 lambda:setattr(self.zone,'is_in_build_buy',True))
        for operation in OPERATIONS[1:]:
            for index,change in enumerate(changes):
                with self.subTest(operation=operation,change=index):
                    self.setUp();self.admission.cas=True
                    admissions=[]
                    final_count=4 if operation=='cas.enter' else 2
                    def mutate(value,observation):
                        admissions.append(observation)
                        if len(admissions)==final_count:change()
                        return True
                    self.admission.require_mutation=mutate
                    with self.assertRaises(NativeRefusal):self.api.execute(request(operation))
                    self.assertEqual(final_count,len(admissions))
                    self.assertEqual([],self.commands)
                    self.assertEqual(['enter','exit'] if operation=='cas.enter' else [],self.admission.scopes)

    def test_initial_mutation_admission_context_drift_refuses_before_imports(self):
        for operation in OPERATIONS[1:]:
            with self.subTest(operation=operation):
                self.setUp();self.admission.cas=True
                def mutate(value,observation):
                    self.client.id=6
                    return True
                self.admission.require_mutation=mutate
                with self.assertRaises(NativeRefusal):self.api.execute(request(operation))
                self.assertEqual([],self.loads)
                self.assertEqual([],self.admission.scopes)
                self.assertEqual([],self.commands)

    def test_mutation_admission_refresh_is_bounded_without_admission_loop(self):
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                self.setUp();self.admission.cas=True
                self.api.execute(request(operation))
                admissions=4 if operation=='cas.enter' else 2
                if operation=='observe':
                    self.assertEqual(1,self.admission.calls.count('read'))
                    self.assertEqual(0,self.admission.calls.count('mutation'))
                    self.assertEqual(1,self.service_reads.count('current_zone'))
                else:
                    self.assertEqual(admissions,self.admission.calls.count('read'))
                    self.assertEqual(admissions,self.admission.calls.count('mutation'))
                    reads=3*admissions+(2 if operation=='live.set_speed' else 6 if operation=='cas.enter' else 0)
                    self.assertEqual(reads,self.service_reads.count('current_zone'))

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
        changes=(lambda:setattr(self.client,'id',6),lambda:setattr(self.sim,'id',31),
                 lambda:setattr(self.sim,'household_id',21),
                 lambda:setattr(self,'instance',object()))
        for change in changes:
            self.setUp();self.admission.cas=True;self.admission.enter=change
            with self.assertRaises(NativeRefusal):self.api.execute(request('cas.enter'))
            self.assertEqual([],self.commands)
            self.assertEqual(['enter','exit'],self.admission.scopes)

    def test_cas_target_preparation_drift_never_submits(self):
        changes=(lambda:setattr(self.zone,'id',11),
                 lambda:setattr(self.household,'id',21),
                 lambda:setattr(self.client,'id',6),
                 lambda:setattr(self.persistence,'get_save_slot_proto_buff',lambda:NS(slot_id=5)),
                 lambda:setattr(self.persistence,'get_save_slot_proto_guid',lambda:100),
                 lambda:setattr(self.zone,'is_zone_running',False),
                 lambda:setattr(self.zone,'is_in_build_buy',True),
                 lambda:setattr(self.sim,'id',31),
                 lambda:setattr(self.sim,'household_id',21),
                 lambda:setattr(self,'instance',object()),
                 lambda:setattr(self,'sim',NS(id=30,household_id=20,get_sim_instance=lambda:self.instance)))
        for index,change in enumerate(changes):
            with self.subTest(change=index):
                self.setUp();self.admission.cas=True
                def target(value):
                    change()
                    return ('target',value)
                self.modules['server_commands.argument_helpers'].OptionalTargetParam=target
                with self.assertRaises(NativeRefusal):self.api.execute(request('cas.enter'))
                self.assertEqual([],self.commands)
                self.assertEqual(['enter','exit'],self.admission.scopes)

    def test_cas_target_is_prepared_inside_scope_before_fresh_admission(self):
        self.admission.cas=True
        def target(value):
            self.assertEqual(['enter'],self.admission.scopes)
            self.assertEqual(['read','mutation','read','mutation','read','mutation','scope'],self.admission.calls)
            return ('target',value)
        self.modules['server_commands.argument_helpers'].OptionalTargetParam=target
        self.api.execute(request('cas.enter'))
        self.assertEqual(['read','mutation','read','mutation','read','mutation','scope','read','mutation'],self.admission.calls)
        self.assertEqual([('cas',('target','30'),5)],self.commands)
        self.assertEqual(['enter','exit'],self.admission.scopes)

    def test_cas_target_cannot_bypass_read_or_mutation_readmission(self):
        for field in ('read','mutate'):
            with self.subTest(field=field):
                self.setUp();self.admission.cas=True
                def target(value):
                    setattr(self.admission,field,False)
                    return ('target',value)
                self.modules['server_commands.argument_helpers'].OptionalTargetParam=target
                with self.assertRaises(NativeRefusal):self.api.execute(request('cas.enter'))
                self.assertEqual([],self.commands)
                self.assertEqual(['enter','exit'],self.admission.scopes)

    def test_cas_target_constructor_error_closes_scope_without_native_call(self):
        self.admission.cas=True
        def target(value):raise RuntimeError('fabricated target construction failure')
        self.modules['server_commands.argument_helpers'].OptionalTargetParam=target
        with self.assertRaises(RuntimeError):self.api.execute(request('cas.enter'))
        self.assertEqual([],self.commands)
        self.assertEqual(['enter','exit'],self.admission.scopes)

    def test_cas_native_error_closes_scope_without_retry(self):
        self.admission.cas=True
        def failed():raise RuntimeError('fabricated native failure')
        self.on_cas=failed
        with self.assertRaises(RuntimeError):self.api.execute(request('cas.enter'))
        self.assertEqual([('cas',('target','30'),5)],self.commands)
        self.assertEqual(['enter','exit'],self.admission.scopes)

    def test_companion_native_source_parses_as_python37(self):
        source=(SOURCE/'foundry_companion'/'native.py').read_text()
        if sys.version_info[:2] == (3,7): ast.parse(source)
        else: ast.parse(source,feature_version=(3,7))
        self.assertNotIn('apex_core',source)
        self.assertNotIn('SendInput',source)
        self.assertNotIn('fulleditmode',source)
