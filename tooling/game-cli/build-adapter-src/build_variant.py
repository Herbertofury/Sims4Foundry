"""One-shot direct definition variant change for a trusted Build host.

Same variant group is a narrow source-derived precondition, not proof of a
catalog swatch, undoable Build transaction, object-state preservation or save.
"""
from dataclasses import dataclass
import threading

from build_transform import _snapshot, _surface, _tuple, MAX_COORDINATE
from foundry_companion.build_observation import BuildContext, _context_copy, _context_key
from foundry_companion.native import NativeRefusal, _id
from foundry_companion.queue import _decimal_id, _token

MAX_REQUESTS = 128


@dataclass(frozen=True)
class VariantRequest:
    request_id: str
    context: BuildContext
    object_id: str
    before_position: tuple
    before_orientation: tuple
    before_surface: tuple
    before_definition: str
    target_definition: str


def _key(request):
    if type(request) is not VariantRequest or not _token(request.request_id):
        raise NativeRefusal('Exact variant request required.')
    context = _context_key(request.context)
    if (not _decimal_id(request.object_id) or not _decimal_id(request.before_definition)
            or not _decimal_id(request.target_definition)
            or request.before_definition == request.target_definition):
        raise NativeRefusal('Exact distinct object and definition IDs required.')
    position = _tuple(request.before_position, 3, MAX_COORDINATE)
    orientation = _tuple(request.before_orientation, 4, 1.0)
    if abs(sum(value * value for value in orientation) - 1.0) > 0.0001:
        raise NativeRefusal('Before orientation must be near unit length.')
    return (request.request_id, context, request.object_id, position, orientation,
            _surface(request.before_surface), request.before_definition, request.target_definition)


def _copy(key):
    return VariantRequest(key[0], _context_copy(key[1]), *key[2:])


class BuildVariantExecutor(object):
    def __init__(self, context_reader, object_reader, admission, variant_group,
                 world_surface_type):
        if not all(callable(value) for value in (context_reader, object_reader, variant_group)):
            raise NativeRefusal('Explicit Build readers and variant lookup required.')
        if type(world_surface_type) is not int or not 0 <= world_surface_type < 2**31:
            raise NativeRefusal('Exact world surface type required.')
        self._context, self._object = context_reader, object_reader
        self._admission, self._group, self._world = admission, variant_group, world_surface_type
        self._owner = threading.current_thread()
        self._owner_ident = self._owner.ident
        self._busy, self._seen = False, set()

    def _owned(self):
        current = threading.current_thread()
        if current is not self._owner or current.ident != self._owner_ident:
            raise NativeRefusal('Build variant requires the captured owner thread.')

    def _sample(self, context):
        self._owned()
        raw = self._context()
        self._owned()
        if (type(raw) is not dict or _context_key(raw.get('context')) != context
                or raw.get('zone_running') is not True or raw.get('in_build_buy') is not True):
            raise NativeRefusal('Exact running Build context required.')

    def _resolve(self, key):
        self._sample(key[1])
        obj = self._object(int(key[2]))
        self._sample(key[1])
        if obj is None:
            raise NativeRefusal('Selected object is unavailable.')
        actual = _snapshot(obj, key[2])
        definition = _id(obj.definition.id)
        self._sample(key[1])
        if (actual != (key[3], key[4], key[5]) or definition != key[6]
                or key[5][0] != int(key[1][3]) or key[5][2] != self._world):
            raise NativeRefusal('Object, definition or world-surface scope changed.')
        return obj

    def _groups(self, key):
        self._sample(key[1])
        before = self._group(int(key[6]))
        target = self._group(int(key[7]))
        self._sample(key[1])
        if _id(before) != _id(target):
            raise NativeRefusal('Definitions do not share an observed variant group.')
        return before

    def execute(self, request):
        self._owned()
        if self._busy:
            raise NativeRefusal('Reentrant Build variant change is refused.')
        self._busy = True
        started = False
        try:
            key = _key(request)
            if key[0] in self._seen or len(self._seen) >= MAX_REQUESTS:
                raise NativeRefusal('Request already consumed or history is full.')
            self._seen.add(key[0])
            view = _copy(key)
            try:
                verdict = self._admission.require_build_variant(view)
            except Exception:
                raise NativeRefusal('Build variant admission unavailable.') from None
            self._owned()
            if verdict is not True or _key(view) != key:
                raise NativeRefusal('Exact Build variant admission is required.')
            obj = self._resolve(key)
            group = self._groups(key)
            if self._resolve(key) is not obj or self._groups(key) != group:
                raise NativeRefusal('Object or variant group changed before dispatch.')
            self._owned()
            started = True
            try:
                returned = obj.set_definition(int(key[7]))
                self._sample(key[1])
                observed = self._object(int(key[2]))
                if observed is not obj:
                    raise NativeRefusal('Object changed after dispatch.')
                after = _id(observed.definition.id)
                self._sample(key[1])
                if type(returned) is not bool:
                    raise NativeRefusal('Native result unavailable.')
                if returned is not True or after != key[7]:
                    return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                            'dispatched':True, 'may_have_applied':True,
                            'native_returned':returned, 'after_definition':after}
            except Exception:
                return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                        'dispatched':True, 'may_have_applied':True}
            return {'ok':True, 'outcome':'dispatched', 'request_id':key[0],
                    'dispatched':True, 'native_returned':returned,
                    'before_definition':key[6], 'after_definition':after,
                    'live_qualified':False, 'undo_recorded':False,
                    'swatch_verified':False, 'transaction_verified':False}
        except NativeRefusal:
            if started:
                return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                        'dispatched':True, 'may_have_applied':True}
            raise
        except Exception:
            if started:
                return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                        'dispatched':True, 'may_have_applied':True}
            raise NativeRefusal('Build variant preflight unavailable.') from None
        finally:
            self._busy = False
