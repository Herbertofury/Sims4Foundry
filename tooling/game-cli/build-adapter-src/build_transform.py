"""Guarded, single-dispatch direct object transform for a trusted Build host.

This is not a Build/Buy UI transaction, purchase, undo, deletion, or swatch.
There is no bootstrap or game import; host callbacks are trusted, synchronous
and separately responsible for admission, durable history and qualification.
"""
from dataclasses import dataclass
import math
import threading

from foundry_companion.build_observation import (
    BuildContext, _context_copy, _context_key, _quaternion, _routing, _vector)
from foundry_companion.native import NativeRefusal, _id
from foundry_companion.queue import MAX_UINT64, _decimal_id, _token

MAX_REQUESTS = 128
MAX_COORDINATE = 1000000.0


def _number(value, bound):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > bound:
        raise NativeRefusal('Invalid bounded numeric value.')
    return float(value)


def _tuple(value, count, bound):
    if type(value) is not tuple or len(value) != count:
        raise NativeRefusal('Exact numeric tuple required.')
    return tuple(_number(item, bound) for item in value)


def _surface(value):
    if (type(value) is not tuple or len(value) != 3
            or type(value[0]) is not int or not 0 <= value[0] <= MAX_UINT64
            or type(value[1]) is not int or not -(2**31) <= value[1] < 2**31
            or type(value[2]) is not int or not 0 <= value[2] < 2**31):
        raise NativeRefusal('Exact routing surface required.')
    return value


@dataclass(frozen=True)
class TransformRequest:
    request_id: str
    context: BuildContext
    object_id: str
    before_position: tuple
    before_orientation: tuple
    before_surface: tuple
    destination: tuple
    yaw: float
    level: int


def _key(request):
    if type(request) is not TransformRequest or not _token(request.request_id):
        raise NativeRefusal('Exact transform request required.')
    context = _context_key(request.context)
    if not _decimal_id(request.object_id):
        raise NativeRefusal('Exact object ID required.')
    before_position = _tuple(request.before_position, 3, MAX_COORDINATE)
    before_orientation = _tuple(request.before_orientation, 4, 1.0)
    if abs(sum(v*v for v in before_orientation) - 1.0) > 0.0001:
        raise NativeRefusal('Before orientation must be near unit length.')
    before_surface = _surface(request.before_surface)
    destination = _tuple(request.destination, 3, MAX_COORDINATE)
    yaw = _number(request.yaw, 2 * math.pi)
    if type(request.level) is not int or not -32 <= request.level <= 32:
        raise NativeRefusal('Exact bounded floor level required.')
    return (request.request_id, context, request.object_id, before_position,
            before_orientation, before_surface, destination, yaw, request.level)


def _copy(key):
    return TransformRequest(key[0], _context_copy(key[1]), *key[2:])


def _snapshot(obj, object_id):
    if _id(obj.id) != object_id or obj.parent is not None or obj.is_sim is not False:
        raise NativeRefusal('Object identity or direct-transform scope unavailable.')
    position = _vector(obj.position, ('x', 'y', 'z'))
    orientation = _quaternion(obj.orientation)
    surface = _routing(obj.routing_surface)
    return (tuple(position[name] for name in ('x', 'y', 'z')),
            tuple(orientation[name] for name in ('x', 'y', 'z', 'w')),
            tuple(surface[name] for name in ('primary_id', 'secondary_id', 'type')))


def _destination_matches(after, position, orientation, surface):
    close = lambda a, b: abs(a - b) <= 0.00001
    position_ok = all(close(a, b) for a, b in zip(after[0], position))
    # A quaternion and its negation represent the same rotation.
    rotation_ok = (all(close(a, b) for a, b in zip(after[1], orientation))
                   or all(close(a, -b) for a, b in zip(after[1], orientation)))
    return position_ok and rotation_ok and after[2] == surface


class BuildTransformExecutor(object):
    """Move/rotate one already-existing, explicitly scoped world object once.

    Factories and `test_location` are supplied game callables matching the
    documented native signatures. The executor never imports them or auto-runs.
    A true placement check is not a reservation or a Build transaction.
    """
    def __init__(self, context_reader, object_reader, admission, vector3,
                 yaw_quaternion, surface_identifier, world_surface_type,
                 transform, location, test_location):
        callbacks = (context_reader, object_reader, vector3, yaw_quaternion,
                     surface_identifier, transform, location, test_location)
        if not all(callable(item) for item in callbacks):
            raise NativeRefusal('Explicit Build callbacks required.')
        if type(world_surface_type) is not int or not 0 <= world_surface_type < 2**31:
            raise NativeRefusal('Exact world surface type required.')
        self._context = context_reader
        self._object = object_reader
        self._admission = admission
        self._vector3, self._yaw = vector3, yaw_quaternion
        self._surface, self._world = surface_identifier, world_surface_type
        self._transform, self._location, self._test = transform, location, test_location
        self._owner = threading.current_thread()
        self._owner_ident = self._owner.ident
        self._busy = False
        self._seen = set()

    def _owned(self):
        current = threading.current_thread()
        if current is not self._owner or current.ident != self._owner_ident:
            raise NativeRefusal('Build transform requires the captured owner thread.')

    def _sample(self, context_key):
        self._owned()
        raw = self._context()
        self._owned()
        if (type(raw) is not dict or _context_key(raw.get('context')) != context_key
                or raw.get('zone_running') is not True
                or raw.get('in_build_buy') is not True):
            raise NativeRefusal('Exact running Build context required.')

    def _resolve(self, key):
        self._sample(key[1])
        obj = self._object(int(key[2]))
        self._sample(key[1])
        if obj is None:
            raise NativeRefusal('Selected object is unavailable.')
        snapshot = _snapshot(obj, key[2])
        self._sample(key[1])
        if snapshot != (key[3], key[4], key[5]):
            raise NativeRefusal('Selected object changed since the supplied readback.')
        if key[5][0] != int(key[1][3]) or key[5][2] != self._world:
            raise NativeRefusal('Only an object on the current world surface is supported.')
        return obj, snapshot

    def execute(self, request):
        self._owned()
        if self._busy:
            raise NativeRefusal('Reentrant Build transforms are refused.')
        self._busy = True
        started = False
        try:
            key = _key(request)
            if key[0] in self._seen or len(self._seen) >= MAX_REQUESTS:
                raise NativeRefusal('Request already consumed or history is full.')
            # Reserve even refused IDs within this process. This is not durable.
            self._seen.add(key[0])
            view = _copy(key)
            try:
                verdict = self._admission.require_build_transform(view)
            except Exception:
                raise NativeRefusal('Build transform admission unavailable.') from None
            self._owned()
            if verdict is not True or _key(view) != key:
                raise NativeRefusal('Exact Build transform admission is required.')
            obj, before = self._resolve(key)
            position = self._vector3(*key[6])
            orientation = self._yaw(key[7])
            expected_orientation = _quaternion(orientation)
            expected_orientation = tuple(expected_orientation[name] for name in ('x', 'y', 'z', 'w'))
            surface = self._surface(int(key[1][3]), key[8], self._world)
            location = self._location(self._transform(position, orientation), surface)
            self._sample(key[1])
            try:
                tested = self._test(obj, location=location)
            except Exception:
                raise NativeRefusal('Placement test unavailable.') from None
            self._sample(key[1])
            if type(tested) is not tuple or len(tested) != 2 or type(tested[0]) is not bool:
                raise NativeRefusal('Placement test returned an unsupported result.')
            if tested[0] is not True:
                return {'ok': False, 'outcome': 'placement_refused',
                        'request_id': key[0], 'dispatched': False}
            current, again = self._resolve(key)
            if current is not obj or again != before:
                raise NativeRefusal('Object changed before dispatch.')
            self._owned()
            started = True
            try:
                obj.move_to(routing_surface=surface, translation=position, orientation=orientation)
                self._sample(key[1])
                final = self._object(int(key[2]))
                if final is not obj:
                    raise NativeRefusal('Object changed after dispatch.')
                after = _snapshot(final, key[2])
                self._sample(key[1])
                if not _destination_matches(after, key[6], expected_orientation,
                                            (int(key[1][3]), key[8], self._world)):
                    return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                            'dispatched':True, 'may_have_applied':True,
                            'before':before, 'after':after}
            except Exception:
                return {'ok': False, 'outcome': 'unresolved', 'request_id': key[0],
                        'dispatched': True, 'may_have_applied': True}
            return {'ok': True, 'outcome': 'dispatched', 'request_id': key[0],
                    'dispatched': True, 'before': before, 'after': after,
                    'live_qualified': False, 'undo_recorded': False,
                    'transaction_verified': False}
        except NativeRefusal:
            if started:
                return {'ok': False, 'outcome': 'unresolved',
                        'request_id': key[0], 'dispatched': True, 'may_have_applied': True}
            raise
        except Exception:
            if started:
                return {'ok': False, 'outcome': 'unresolved',
                        'request_id': key[0], 'dispatched': True, 'may_have_applied': True}
            raise NativeRefusal('Build transform preflight unavailable.') from None
        finally:
            self._busy = False
