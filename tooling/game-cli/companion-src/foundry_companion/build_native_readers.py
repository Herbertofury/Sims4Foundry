# Adapted from Sims4Foundry public Build commit 9bf905924c92183fa73975c4b0dd11ea0bcc44f5
# Original Git blob: 90585d71254417265e77c00d3b2d6de263668751; packaged relative imports.
"""Explicit owner-thread Build readers over supplied game services.

This supplies no startup, admission, network, mutation, or generation source.
The trusted host must establish those separately before use with BuildObserver.
"""
import threading

from .build_observation import BuildContext
from .native import NativeRefusal, _capture_binding, _id, _integer
from .queue import ProcessEpoch


class BuildNativeReaders(object):
    """Read a zone context and resolve explicitly selected object IDs only.

    ``services`` is an already supplied game services module. ``generation`` is
    a trusted synchronous callable whose positive value must change on every zone
    lifetime. Neither argument is discovered or imported by this module.
    """
    def __init__(self, services, generation, session_id, process_epoch):
        if services is None or not callable(generation):
            raise NativeRefusal('Explicit services and zone generation are required.')
        self._binding = _capture_binding(session_id, process_epoch)
        self._services = services
        self._generation = generation
        self._owner = threading.current_thread()
        self._owner_ident = self._owner.ident
        self._busy = False

    def _enter(self):
        current = threading.current_thread()
        if current is not self._owner or current.ident != self._owner_ident:
            raise NativeRefusal('Build readers require the captured owner thread.')
        if self._busy:
            raise NativeRefusal('Reentrant Build service reads are refused.')
        self._busy = True

    def _owned(self):
        current = threading.current_thread()
        if current is not self._owner or current.ident != self._owner_ident:
            raise NativeRefusal('Build readers require the captured owner thread.')

    def context(self):
        self._enter()
        try:
            try:
                services = self._services
                zone = services.current_zone()
                if zone is None:
                    raise NativeRefusal('Zone unavailable.')
                zone_id = _id(zone.id)
                running = zone.is_zone_running
                if type(running) is not bool:
                    raise NativeRefusal('Zone running state unavailable.')
                try:
                    mode = zone.is_in_build_buy
                except Exception:
                    mode = None
                if type(mode) is not bool:
                    mode = None
                generation = self._generation()
                household = services.active_household()
                client = services.client_manager().get_first_client()
                persistence = services.get_persistence_service()
                slot = persistence.get_save_slot_proto_buff()
                context = BuildContext(self._binding[0],
                    ProcessEpoch(self._binding[1], self._binding[2]), zone_id,
                    _id(household.id), _id(client.id), _integer(slot.slot_id),
                    str(_integer(persistence.get_save_slot_proto_guid())), generation)
                self._owned()
                final_generation = self._generation()
                if (services.current_zone() is not zone or _id(zone.id) != zone_id
                        or type(final_generation) is not int or final_generation != generation):
                    raise NativeRefusal('Zone lifetime changed.')
                # Dependent getters above may synchronously change either flag.
                # Recheck after all of them, including final zone/generation reads.
                # This is sequential sampling, not an atomic state/lifetime lock.
                final_running = zone.is_zone_running
                try:
                    final_mode = zone.is_in_build_buy
                except Exception:
                    final_mode = None
                if type(final_mode) is not bool:
                    final_mode = None
                if (type(final_running) is not bool or final_running is not running
                        or final_mode is not mode):
                    raise NativeRefusal('Zone running or Build mode changed.')
                return {'context': context, 'zone_running': running, 'in_build_buy': mode}
            except Exception:
                raise NativeRefusal('Build service context is unavailable or changed.') from None
        finally:
            self._busy = False

    def object(self, object_id):
        self._enter()
        try:
            # An exact positive integer is required; no scans or coercion.
            _id(object_id)
            try:
                services = self._services
                zone = services.current_zone()
                if zone is None:
                    raise NativeRefusal('Zone unavailable.')
                zone_id = _id(zone.id)
                generation = self._generation()
                if type(generation) is not int or not 1 <= generation <= 2**64 - 1:
                    raise NativeRefusal('Zone generation unavailable.')
                manager = services.object_manager()
                if manager is None:
                    raise NativeRefusal('Object manager unavailable.')
                value = manager.get(object_id)
                self._owned()
                final_generation = self._generation()
                if (services.current_zone() is not zone or _id(zone.id) != zone_id
                        or services.object_manager() is not manager
                        or type(final_generation) is not int or final_generation != generation):
                    raise NativeRefusal('Zone or object manager changed.')
                return value
            except Exception:
                raise NativeRefusal('Build object service is unavailable or changed.') from None
        finally:
            self._busy = False
