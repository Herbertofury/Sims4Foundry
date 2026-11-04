"""Uninstalled, owner-thread-only bindings for four narrow native operations.

This module starts nothing and imports no game module at import time. A future
qualified host must supply actual services, current profile/process admission,
external durable intent and operation-specific evidence. Admission defaults deny.
CAS additionally needs a reviewed preservation/frontdoor scope; this file does
not replace Apex's guards or install a neutral replacement for them.

Call signatures were inspected in the user's existing test_driver.py and final
owner-thread queue on 2026-11-04. This is new neutral code, not an import/copy of
Apex's runtime. Python 3.7 syntax alone does not prove current-game compatibility.
No save/load, full-edit cheat, force quit, generic cheat or arbitrary command API.
"""
import importlib
import threading


class NativeRefusal(RuntimeError):
    pass


class DenyAdmission(object):
    """No default path can read or mutate the current game."""
    def require_read(self, request):
        raise NativeRefusal('A current, independently reviewed Foundry admission provider is required.')

    def require_mutation(self, request, observation):
        raise NativeRefusal('Native mutation has not been admitted or qualified.')

    def cas_scope(self, request, sim, client):
        raise NativeRefusal('A separately reviewed CAS preservation/frontdoor provider is required.')


def _id(value):
    if type(value) is not int or not 1 <= value <= 2**64-1:
        raise NativeRefusal('Native identity is missing or not an exact positive uint64.')
    return str(value)


def _integer(value):
    if type(value) is not int or not 0 <= value <= 2**64-1:
        raise NativeRefusal('Native integer observation is unavailable.')
    return value


def _boolean(value):
    if type(value) is not bool:
        raise NativeRefusal('Native boolean observation is unavailable.')
    return value


def _speed(value):
    if not isinstance(value, int) or type(value) is bool or not 0 <= value <= 3:
        raise NativeRefusal('Native clock speed is not a recognized value.')
    return int(value)


def _same_live_context(before, after):
    return (not after['errors'] and after['zone_running'] is True and after['in_build_buy'] is False
            and all(after[key] == before[key] for key in
                    ('zone_id', 'household_id', 'client_id', 'save_slot', 'save_guid')))


class NativeAPI(object):
    def __init__(self, services, admission=None, command_loader=None):
        self._owner = threading.current_thread().ident
        self._owner_thread = threading.current_thread()
        self._services = services
        self._admission = admission if admission is not None else DenyAdmission()
        self._load = command_loader if command_loader is not None else importlib.import_module

    def _owned(self):
        if threading.current_thread() is not self._owner_thread or threading.current_thread().ident != self._owner:
            raise NativeRefusal('All native reads and writes belong to the captured game owner thread.')

    def _read(self):
        """All observations are primitive values; no Sim scan or appearance reads."""
        self._owned()
        errors = {}
        def read(name, function):
            try:
                return function()
            except Exception as error:
                errors[name] = type(error).__name__
                return None
        services = self._services
        zone = read('zone', lambda: services.current_zone())
        household = read('household', lambda: services.active_household())
        clients = read('clients', lambda: services.client_manager())
        client = read('client', lambda: clients.get_first_client())
        persistence = read('persistence', lambda: services.get_persistence_service())
        slot = read('slot', lambda: persistence.get_save_slot_proto_buff())
        clock = read('clock', lambda: services.game_clock_service())
        time_service = read('time', lambda: services.time_service())
        result = {
            'zone_id': read('zone_id', lambda: _id(zone.id)),
            'household_id': read('household_id', lambda: _id(household.id)),
            'client_id': read('client_id', lambda: _id(client.id)),
            'save_slot': read('save_slot', lambda: _integer(slot.slot_id)),
            'save_guid': read('save_guid', lambda: str(_integer(persistence.get_save_slot_proto_guid()))),
            'clock_speed': read('clock_speed', lambda: _speed(clock.clock_speed)),
            'in_build_buy': read('in_build_buy', lambda: _boolean(zone.is_in_build_buy)),
            'zone_running': read('zone_running', lambda: _boolean(zone.is_zone_running)),
            'sim_ticks': read('sim_ticks', lambda: _integer(time_service.sim_now.absolute_ticks())),
            'game_ticks': read('game_ticks', lambda: _integer(clock.now().absolute_ticks())),
            'errors': errors,
        }
        # The game clock can advance while simulation is paused. Keep both clocks.
        result['sim_time_source'] = 'services.time_service().sim_now'
        result['game_time_source'] = 'services.game_clock_service().now()'
        return result, client

    def _admitted_read(self, request):
        self._owned()
        if self._admission.require_read(request) is not True:
            raise NativeRefusal('Admission must explicitly permit this exact observed request.')
        return self._read()

    def _before_mutation(self, request):
        observation, client = self._admitted_read(request)
        if (observation['errors'] or observation['zone_id'] != request.expected_zone_id
                or observation['household_id'] != request.expected_household_id
                or observation['zone_running'] is not True or observation['in_build_buy'] is not False):
            raise NativeRefusal('Exact running Live zone/household context changed or is unobservable.')
        if self._admission.require_mutation(request, observation) is not True:
            raise NativeRefusal('Mutation requires an exact current profile/lease/operation admission.')
        return observation, client

    def execute(self, request):
        """Called only by an admitted owner-thread queue, never a transport thread.

        A native returned value is not CAS visibility, save persistence or process
        exit. The outer host must keep unknown outcomes blocked from replay.
        """
        self._owned()
        from .queue import Request
        if type(request) is not Request:
            raise NativeRefusal('A validated typed companion request is required.')
        request.validate()
        if request.operation == 'observe':
            observation, _ = self._admitted_read(request)
            return {'outcome': 'verified' if not observation['errors'] else 'unverified',
                    'observation': observation, 'game_qualified': False}
        # Command modules can have import-time effects. Read-only permission is
        # insufficient: require mutation admission before imports, then again
        # afterward so an import-time context change cannot reach native dispatch.
        preflight, _ = self._before_mutation(request)

        def fresh_context():
            value, client = self._before_mutation(request)
            if not _same_live_context(preflight, value):
                raise NativeRefusal('Native context changed while loading command modules.')
            return value, client
        arguments = request.arguments_dict()
        if request.operation == 'live.set_speed':
            command = self._load('server_commands.clock_commands').set_speed
            before, client = fresh_context()
            speed = arguments['speed']
            command(('paused', 'one', 'two', 'three')[speed], 'foundry.cli', _connection=int(before['client_id']))
            after, _ = self._read()
            matched = _same_live_context(before, after) and after['clock_speed'] == speed
            return {'outcome': 'verified' if matched else 'unverified', 'before': before,
                    'observation': after, 'persistence_verified': False, 'game_qualified': False}
        if request.operation == 'game.request_exit_menu':
            command = self._load('sims4.commands').client_cheat
            before, client = fresh_context()
            command('quit', int(before['client_id']))
            return {'outcome': 'unverified', 'request': 'quit_menu', 'before': before,
                    'menu_visibility_verified': False, 'game_exit_verified': False,
                    'save_choice_made': False, 'game_qualified': False}
        if request.operation == 'cas.enter':
            command = self._load('server_commands.cas_commands').modify_in_cas
            target = self._load('server_commands.argument_helpers').OptionalTargetParam
            before, client = fresh_context()
            sim = self._services.sim_info_manager().get(int(arguments['sim_id']))
            instance = sim.get_sim_instance() if sim is not None else None
            if (sim is None or _id(sim.id) != arguments['sim_id'] or instance is None
                    or _id(sim.household_id) != before['household_id']):
                raise NativeRefusal('CAS requires the exact instanced Sim in the admitted household.')
            # Provider must preserve relevant Sim/form/household transactions;
            # absence is a refusal, not permission to remove the original guards.
            with self._admission.cas_scope(request, sim, client):
                current, current_client = self._before_mutation(request)
                if not _same_live_context(before, current):
                    raise NativeRefusal('Native save/client context changed while preparing CAS entry.')
                if (self._services.sim_info_manager().get(int(arguments['sim_id'])) is not sim
                        or sim.get_sim_instance() is not instance or _id(sim.household_id) != current['household_id']):
                    raise NativeRefusal('CAS target changed during preparation.')
                command(target(arguments['sim_id']), _connection=int(current['client_id']))
            return {'outcome': 'unverified', 'request': 'cas_entry', 'sim_id': arguments['sim_id'],
                    'cas_visibility_verified': False, 'save_persistence_verified': False, 'game_qualified': False}
        raise NativeRefusal('Unsupported companion operation; no fallback exists.')
