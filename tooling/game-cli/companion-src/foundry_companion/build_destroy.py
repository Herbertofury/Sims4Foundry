# Adapted from Sims4Foundry public Build commit 9bf905924c92183fa73975c4b0dd11ea0bcc44f5
# Original Git blob: c0eabd02be10eee0fc84c50e8f215c07012bbcc5; packaged relative imports.
"""One-shot direct destruction of a selected object for a trusted Build host.

This is not a Build/Buy sell/delete transaction, refund, or undo operation.
No game services are imported and no behavior runs without an explicit caller.
"""
from dataclasses import dataclass
import threading

from .build_transform import _snapshot, _surface, _tuple, MAX_COORDINATE
from .build_observation import BuildContext, _context_copy, _context_key
from .native import NativeRefusal
from .queue import _decimal_id, _token

MAX_REQUESTS = 128


@dataclass(frozen=True)
class DestroyRequest:
    request_id: str
    context: BuildContext
    object_id: str
    before_position: tuple
    before_orientation: tuple
    before_surface: tuple


def _key(request):
    if type(request) is not DestroyRequest or not _token(request.request_id):
        raise NativeRefusal('Exact destruction request required.')
    context = _context_key(request.context)
    if not _decimal_id(request.object_id):
        raise NativeRefusal('Exact object ID required.')
    position = _tuple(request.before_position, 3, MAX_COORDINATE)
    orientation = _tuple(request.before_orientation, 4, 1.0)
    if abs(sum(value * value for value in orientation) - 1.0) > 0.0001:
        raise NativeRefusal('Before orientation must be near unit length.')
    return (request.request_id, context, request.object_id, position,
            orientation, _surface(request.before_surface))


def _copy(key):
    return DestroyRequest(key[0], _context_copy(key[1]), *key[2:])


class BuildDestroyExecutor(object):
    def __init__(self, context_reader, object_reader, admission, world_surface_type):
        if not callable(context_reader) or not callable(object_reader):
            raise NativeRefusal('Explicit Build readers required.')
        if type(world_surface_type) is not int or not 0 <= world_surface_type < 2**31:
            raise NativeRefusal('Exact world surface type required.')
        self._context, self._object = context_reader, object_reader
        self._admission, self._world = admission, world_surface_type
        self._owner = threading.current_thread()
        self._owner_ident = self._owner.ident
        self._busy, self._seen = False, set()

    def _owned(self):
        current = threading.current_thread()
        if current is not self._owner or current.ident != self._owner_ident:
            raise NativeRefusal('Build destruction requires the captured owner thread.')

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
        self._sample(key[1])
        if (actual != (key[3], key[4], key[5])
                or key[5][0] != int(key[1][3]) or key[5][2] != self._world):
            raise NativeRefusal('Object or world-surface scope changed.')
        return obj

    def execute(self, request):
        self._owned()
        if self._busy:
            raise NativeRefusal('Reentrant Build destruction is refused.')
        self._busy = True
        started = False
        try:
            key = _key(request)
            if key[0] in self._seen or len(self._seen) >= MAX_REQUESTS:
                raise NativeRefusal('Request already consumed or history is full.')
            self._seen.add(key[0])
            view = _copy(key)
            try:
                verdict = self._admission.require_build_destroy(view)
            except Exception:
                raise NativeRefusal('Build destruction admission unavailable.') from None
            self._owned()
            if verdict is not True or _key(view) != key:
                raise NativeRefusal('Exact Build destruction admission is required.')
            obj = self._resolve(key)
            again = self._resolve(key)
            if again is not obj:
                raise NativeRefusal('Object identity changed before dispatch.')
            self._owned()
            started = True
            try:
                obj.destroy()
                self._sample(key[1])
                observed = self._object(int(key[2]))
                self._sample(key[1])
                if observed is not None:
                    return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                            'dispatched':True, 'may_have_applied':True, 'object_absent':False}
            except Exception:
                return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                        'dispatched':True, 'may_have_applied':True}
            return {'ok':True, 'outcome':'dispatched', 'request_id':key[0],
                    'dispatched':True, 'object_absent':True,
                    'live_qualified':False, 'undo_recorded':False,
                    'transaction_verified':False, 'refund_verified':False}
        except NativeRefusal:
            if started:
                return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                        'dispatched':True, 'may_have_applied':True}
            raise
        except Exception:
            if started:
                return {'ok':False, 'outcome':'unresolved', 'request_id':key[0],
                        'dispatched':True, 'may_have_applied':True}
            raise NativeRefusal('Build destruction preflight unavailable.') from None
        finally:
            self._busy = False
