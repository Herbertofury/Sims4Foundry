"""Owner-only capture and one retained-object transform, default denied.

No game imports, discovery, bootstrap, I/O, authentication or profile authority.
The shared queue/journal provide the durable no-replay boundary. This bounded
registry additionally consumes selection lifetimes. Python identity is not a
claim about an unqualified native provider's wrapper/lifetime behavior.
"""
import math
import threading
import time

from .build_observation import BuildContext, _context_key
from .build_request import BuildRequest, request_key, request_from_key, request_sha256, arguments_wire
from .build_transform import (BuildTransformExecutor, TransformRequest, _snapshot,
                              _key as transform_key)
from .build_wire import make_result
from .native import NativeRefusal, _id
from .queue import ProcessEpoch
from .wire import encode_frame


class BuildExecutor(object):
    def __init__(self, session_id, process_epoch, deployment_sha256,
                 context_reader, object_reader, vector3, yaw_quaternion,
                 surface_identifier, world_surface_type, transform, location,
                 test_location, admission=None, clock=None, owner_active=None):
        # Validate binding without accepting a caller-defined coercion/provider.
        from .build_schema import token, hash_value, epoch_fields
        from .wire import _require
        if (not token(session_id) or type(process_epoch) is not ProcessEpoch
                or not hash_value(deployment_sha256)):
            raise NativeRefusal('Exact Build deployment/session/process binding required.')
        epoch_fields(dict(pid=process_epoch.pid, creation_filetime=process_epoch.creation_filetime), _require)
        self._binding = (session_id, process_epoch.pid, process_epoch.creation_filetime, deployment_sha256)
        self._owner = threading.current_thread()
        self._context, self._object, self._admission = context_reader, object_reader, admission
        self._clock = time.monotonic if clock is None else clock
        if not callable(self._clock):
            raise NativeRefusal('Monotonic clock required.')
        self._owner_active = owner_active
        self._last_clock = None
        self._busy = False
        self._closed = False
        self._seen = set()
        self._selections = {}
        self._active = None
        self._deadline = None
        self._native = BuildTransformExecutor(self._native_context, object_reader, self,
            vector3, yaw_quaternion, surface_identifier, world_surface_type,
            transform, location, test_location, dispatch_guard=self._dispatch_guard)
        self._world = world_surface_type
        if not callable(context_reader) or not callable(object_reader):
            raise NativeRefusal('Explicit readers required.')

    def _owned(self):
        if threading.current_thread() is not self._owner:
            raise NativeRefusal('Captured Build owner thread required.')

    def _invalidate(self):
        # Tombstones retain IDs, but release all native object references.
        for key in self._selections:
            self._selections[key] = None

    def close(self):
        self._owned()
        self._closed = True
        self._invalidate()

    def _now(self):
        value = self._clock()
        self._owned()
        if (type(value) not in (int, float) or not math.isfinite(value)
                or (self._last_clock is not None and value < self._last_clock)):
            self.close()
            raise NativeRefusal('Selection clock unavailable.')
        self._last_clock = value
        for key, record in self._selections.items():
            if record is not None and value >= record[2]:
                self._selections[key] = None
        return value

    def _request(self):
        if self._closed or self._active is None:
            raise NativeRefusal('Active Build request required.')
        return request_from_key(self._active)

    def _require_active(self):
        self._owned()
        if (self._closed or not callable(self._owner_active)
                or self._owner_active() is not True):
            self.close()
            raise NativeRefusal('Current admitted owner scope required.')

    def _dispatch_guard(self):
        # No game-reader or admission callback after final native identity checks.
        # The trusted host predicate examines its current hook/owner state only.
        self._require_active()
        self._check_deadline()
        self._require_active()
        return True

    def _check_deadline(self):
        if self._deadline is not None and self._now() >= self._deadline:
            raise NativeRefusal('Build request or retained selection expired.')

    def _admit(self):
        self._require_active()
        self._check_deadline()
        original = self._active
        view = self._request()
        try:
            verdict = self._admission.require_build_request(view)
        except Exception:
            raise NativeRefusal('Build admission unavailable.') from None
        self._require_active()
        if self._closed or verdict is not True or request_key(view) != original:
            raise NativeRefusal('Exact fresh Build admission required.')
        self._check_deadline()

    @staticmethod
    def _context_for(request):
        c = request.expected_context
        return BuildContext(request.session_id, request.process_epoch, c.zone_id,
            c.household_id, c.client_id, c.save_slot, c.save_guid, c.zone_generation)

    def _sample(self, request):
        try:
            self._owned()
            if self._closed:
                raise NativeRefusal('Build executor closed.')
            expected = _context_key(self._context_for(request))
            self._require_active()
            raw = self._context()
            self._require_active()
            if (self._closed or type(raw) is not dict or _context_key(raw.get('context')) != expected
                    or raw.get('zone_running') is not True or raw.get('in_build_buy') is not True):
                raise NativeRefusal('Running Build context changed.')
            # Expire old selections on every observed context mismatch, even if
            # a caller later returns to the same zone/Build-mode declaration.
            for key, record in self._selections.items():
                if record is not None:
                    old = request_from_key(record[0])
                    if _context_key(self._context_for(old)) != expected:
                        self._selections[key] = None
            self._check_deadline()
            return {'context': self._context_for(request), 'zone_running': True, 'in_build_buy': True}
        except BaseException:
            self._invalidate()
            raise

    def poll_context(self):
        """Invalidate retained selections on observed mode/context loss, no I/O."""
        self._owned()
        if self._closed or self._busy:
            return
        self._busy = True
        try:
            self._now()
            record = next((r for r in self._selections.values() if r is not None), None)
            if record is not None:
                self._sample(request_from_key(record[0]))
        except BaseException:
            self._invalidate()
        finally:
            self._busy = False

    def _native_context(self):
        return self._sample(self._request())

    @staticmethod
    def _transform_request(request):
        a = request.arguments
        return TransformRequest(request.request_id, BuildExecutor._context_for(request),
            a.object_id, a.before_position, a.before_orientation,
            (int(a.before_surface.primary), a.before_surface.level, a.before_surface.type),
            a.destination, a.yaw, a.level)

    def require_build_transform(self, request):
        # Internal adapter admission never substitutes its reduced context for
        # the complete, immutable schema-4 request/deployment authorization.
        expected = self._transform_request(self._request())
        if transform_key(request) != transform_key(expected):
            raise NativeRefusal('Transform differs from the admitted full request.')
        self._admit()
        return True

    def _resolve_capture(self, request, retained=None):
        a = request.arguments
        self._sample(request)
        obj = self._object(int(a.object_id))
        self._sample(request)
        if obj is None or (retained is not None and obj is not retained):
            raise NativeRefusal('Captured object changed.')
        if _id(obj.definition.id) != a.definition_id:
            raise NativeRefusal('Captured definition changed.')
        observed = _snapshot(obj, a.object_id)
        self._sample(request)
        expected = (a.before_position, a.before_orientation,
                    (int(a.before_surface.primary), a.before_surface.level, a.before_surface.type))
        if (observed != expected or _id(obj.definition.id) != a.definition_id
                or a.before_surface.primary != request.expected_context.zone_id
                or a.before_surface.type != self._world):
            raise NativeRefusal('Captured direct-world-object snapshot changed.')
        return obj

    def execute(self, request):
        self._owned()
        if self._closed or self._busy:
            raise NativeRefusal('Build executor closed or reentrant.')
        baseline = request_key(request)
        request = request_from_key(baseline)
        binding = (request.session_id, request.process_epoch.pid,
                   request.process_epoch.creation_filetime, request.deployment_sha256)
        if binding != self._binding:
            raise NativeRefusal('Wrong Build deployment/session/process lifetime.')
        if request.request_id in self._seen or len(self._seen) >= 128:
            raise NativeRefusal('Build request consumed or history full.')
        self._seen.add(request.request_id)
        self._busy, self._active = True, baseline
        try:
            now = self._now()
            deadline = now + request.ttl_seconds
            if not math.isfinite(deadline) or deadline <= now:
                raise NativeRefusal('Unrepresentable selection lifetime.')
            self._deadline = deadline
            self._admit()
            if request.operation == 'build.capture_transform':
                # Reserve a tombstone before callbacks; never renew this ID.
                self._selections[request.request_id] = None
                obj = self._resolve_capture(request)
                self._admit()
                self._resolve_capture(request, obj)
                if self._now() >= deadline:
                    raise NativeRefusal('Capture expired before completion.')
                self._selections[request.request_id] = (baseline, obj, deadline)
                return make_result(request, 'captured')
            a = request.arguments
            record = self._selections.get(a.capture_request_id)
            if record is None:
                raise NativeRefusal('Selection is missing, expired or consumed.')
            # An admitted transform attempt consumes before native preflight.
            self._selections[a.capture_request_id] = None
            self._deadline = min(deadline, record[2])
            self._check_deadline()
            capture = request_from_key(record[0])
            original_args = arguments_wire(capture.arguments)
            selected_args = arguments_wire(a)
            selected_args = {key: selected_args[key] for key in original_args}
            if (request_sha256(capture) != a.capture_request_sha256
                    or capture.expected_context != request.expected_context
                    or capture.deployment_sha256 != request.deployment_sha256
                    or encode_frame(original_args) != encode_frame(selected_args)):
                raise NativeRefusal('Selection does not match the captured request.')
            self._resolve_capture(request, record[1])
            result = self._native.execute_retained(self._transform_request(request), record[1], a.definition_id)
            action = result['outcome']
            after = result.get('after')
            after = None if after is None else dict(position=list(after[0]), orientation=list(after[1]),
                surface=dict(primary=str(after[2][0]), level=after[2][1], type=after[2][2]))
            return make_result(request, action,
                reason=action if action in ('placement_refused', 'unresolved') else None,
                dispatch_attempted=result.get('dispatched') is True, after=after)
        finally:
            self._active, self._busy, self._deadline = None, False, None
