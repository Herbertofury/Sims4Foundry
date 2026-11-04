"""One-shot direct object creation, without an automatic placement or cleanup.

Not a catalog purchase, Build/Buy transaction, refund, or undoable action.
The host supplies all native callables, durable history and admission.
"""
from dataclasses import dataclass
import math
import threading

from build_transform import _snapshot, _tuple, _number, MAX_COORDINATE
from foundry_companion.build_observation import BuildContext, _context_copy, _context_key
from foundry_companion.native import NativeRefusal, _id
from foundry_companion.queue import _decimal_id, _token

MAX_REQUESTS = 128


@dataclass(frozen=True)
class CreateRequest:
    request_id: str
    context: BuildContext
    definition_id: str
    destination: tuple
    yaw: float
    level: int


def _key(request):
    if type(request) is not CreateRequest or not _token(request.request_id):
        raise NativeRefusal('Exact creation request required.')
    context = _context_key(request.context)
    if not _decimal_id(request.definition_id):
        raise NativeRefusal('Exact definition ID required.')
    destination = _tuple(request.destination, 3, MAX_COORDINATE)
    yaw = _number(request.yaw, 2 * math.pi)
    if type(request.level) is not int or not -32 <= request.level <= 32:
        raise NativeRefusal('Exact bounded floor level required.')
    return (request.request_id, context, request.definition_id, destination, yaw, request.level)


def _copy(key):
    return CreateRequest(key[0], _context_copy(key[1]), *key[2:])


class BuildCreateExecutor(object):
    def __init__(self, context_reader, object_reader, admission, vector3,
                 yaw_quaternion, surface_identifier, world_surface_type,
                 transform, location, test_location, create_object):
        callbacks = (context_reader, object_reader, vector3, yaw_quaternion,
                     surface_identifier, transform, location, test_location, create_object)
        if not all(callable(item) for item in callbacks):
            raise NativeRefusal('Explicit Build callbacks required.')
        if type(world_surface_type) is not int or not 0 <= world_surface_type < 2**31:
            raise NativeRefusal('Exact world surface type required.')
        self._context, self._object, self._admission = context_reader, object_reader, admission
        self._vector, self._yaw, self._surface = vector3, yaw_quaternion, surface_identifier
        self._world, self._transform, self._location = world_surface_type, transform, location
        self._test, self._create = test_location, create_object
        self._owner = threading.current_thread()
        self._owner_ident = self._owner.ident
        self._busy, self._seen = False, set()

    def _owned(self):
        current = threading.current_thread()
        if current is not self._owner or current.ident != self._owner_ident:
            raise NativeRefusal('Build creation requires the captured owner thread.')

    def _sample(self, context):
        self._owned()
        raw = self._context()
        self._owned()
        if (type(raw) is not dict or _context_key(raw.get('context')) != context
                or raw.get('zone_running') is not True or raw.get('in_build_buy') is not True):
            raise NativeRefusal('Exact running Build context required.')

    def execute(self, request):
        self._owned()
        if self._busy:
            raise NativeRefusal('Reentrant Build creation is refused.')
        self._busy = True
        started, object_id = False, None
        try:
            key = _key(request)
            if key[0] in self._seen or len(self._seen) >= MAX_REQUESTS:
                raise NativeRefusal('Request already consumed or history is full.')
            self._seen.add(key[0])
            view = _copy(key)
            try:
                verdict = self._admission.require_build_create(view)
            except Exception:
                raise NativeRefusal('Build creation admission unavailable.') from None
            self._owned()
            if verdict is not True or _key(view) != key:
                raise NativeRefusal('Exact Build creation admission is required.')
            self._sample(key[1])
            position = self._vector(*key[3])
            orientation = self._yaw(key[4])
            surface = self._surface(int(key[1][3]), key[5], self._world)
            location = self._location(self._transform(position, orientation), surface)
            self._sample(key[1])
            try:
                tested = self._test(definition_id=int(key[2]), location=location)
            except Exception:
                raise NativeRefusal('Definition placement test unavailable.') from None
            self._sample(key[1])
            if type(tested) is not tuple or len(tested) != 2 or type(tested[0]) is not bool:
                raise NativeRefusal('Placement test returned an unsupported result.')
            if tested[0] is not True:
                return {'ok':False, 'outcome':'placement_refused', 'request_id':key[0],
                        'dispatched':False}
            self._owned()
            started = True
            try:
                obj = self._create(int(key[2]))
                if obj is None:
                    raise NativeRefusal('Created object unavailable.')
                object_id = _id(obj.id)
                self._sample(key[1])
                if self._object(int(object_id)) is not obj or _id(obj.definition.id) != key[2]:
                    raise NativeRefusal('Created identity or definition changed.')
                snapshot = _snapshot(obj, object_id)
                self._sample(key[1])
            except Exception:
                return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                        'dispatched':True, 'may_have_applied':True, 'object_id':object_id}
            return {'ok':True, 'outcome':'created', 'request_id':key[0],
                    'dispatched':True, 'object_id':object_id,
                    'position':snapshot[0], 'orientation':snapshot[1], 'surface':snapshot[2],
                    'placed':False, 'live_qualified':False, 'undo_recorded':False,
                    'transaction_verified':False}
        except NativeRefusal:
            if started:
                return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                        'dispatched':True, 'may_have_applied':True, 'object_id':object_id}
            raise
        except Exception:
            if started:
                return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                        'dispatched':True, 'may_have_applied':True, 'object_id':object_id}
            raise NativeRefusal('Build creation preflight unavailable.') from None
        finally:
            self._busy = False
