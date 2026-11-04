"""Source-only, default-denied Build readback with injected trusted readers.

No game imports, hooks, bootstrap, transport, mutation or mode-change requests.
Observed fields and transition notifications are not independent evidence or live
qualification. This module uses the existing companion admission/request types;
its context is a host declaration, never an additional source of authority.
"""
from dataclasses import dataclass
import math
import threading

from .native import (DenyAdmission, NativeRefusal, _capture_binding,
                     _check_admission_request, _copy_request, _id)
from .queue import MAX_UINT64, ProcessEpoch, _decimal_id


MAX_OBJECT_IDS = 32


@dataclass(frozen=True)
class BuildContext:
    """Exact expected read context; generation changes on every zone lifetime.

    The host must observe zone_generation independently, including same-ID zone
    reloads. Matching these fields does not establish process/profile authority.
    """
    session_id: str
    process_epoch: ProcessEpoch
    zone_id: str
    household_id: str
    client_id: str
    save_slot: int
    save_guid: str
    zone_generation: int

    def __post_init__(self):
        _context_key(self)


def _context_key(value):
    if type(value) is not BuildContext:
        raise NativeRefusal('An exact typed Build context is required.')
    binding = _capture_binding(value.session_id, value.process_epoch)
    if (not all(_decimal_id(item) for item in
                (value.zone_id, value.household_id, value.client_id))
            or type(value.save_slot) is not int or not 0 <= value.save_slot <= MAX_UINT64
            or type(value.save_guid) is not str
            or not (value.save_guid == '0' or _decimal_id(value.save_guid))
            or type(value.zone_generation) is not int
            or not 1 <= value.zone_generation <= MAX_UINT64):
        raise NativeRefusal('Exact zone, household, client, save and generation are required.')
    return binding + (value.zone_id, value.household_id, value.client_id,
                      value.save_slot, value.save_guid, value.zone_generation)


def _context_view(key):
    return {'session_id': key[0],
            'process_epoch': {'pid': key[1], 'creation_filetime': key[2]},
            'zone_id': key[3], 'household_id': key[4], 'client_id': key[5],
            'save_slot': key[6], 'save_guid': key[7], 'zone_generation': key[8]}


def _context_copy(key):
    return BuildContext(key[0], ProcessEpoch(key[1], key[2]), *key[3:])


def _object_ids(values):
    # Do not iterate arbitrary iterables or accept coercible IDs/JSON selectors.
    if type(values) not in (tuple, list) or len(values) > MAX_OBJECT_IDS:
        raise NativeRefusal('Select at most 32 explicit object IDs in a plain list or tuple.')
    copied = tuple(values)
    if any(not _decimal_id(value) for value in copied) or len(set(copied)) != len(copied):
        raise NativeRefusal('Object IDs must be unique canonical positive uint64 strings.')
    return copied


def _finite(value):
    if type(value) not in (int, float) or not -MAX_UINT64 <= value <= MAX_UINT64:
        raise NativeRefusal('Expected a bounded finite numeric observation.')
    result = float(value)
    if not math.isfinite(result):
        raise NativeRefusal('Expected a finite numeric observation.')
    return result


def _vector(value, names):
    return {name: _finite(getattr(value, name)) for name in names}


def _quaternion(value):
    result = _vector(value, ('x', 'y', 'z', 'w'))
    norm_squared = sum(component * component for component in result.values())
    if not math.isfinite(norm_squared) or abs(norm_squared - 1.0) > 0.0001:
        raise NativeRefusal('Orientation must be a finite near-unit quaternion; no normalization is inferred.')
    return result


def _routing(value):
    fields = (value.primary_id, value.secondary_id, value.type)
    if (type(fields[0]) is not int or not 0 <= fields[0] <= MAX_UINT64
            or type(fields[1]) is not int or not -(2**31) <= fields[1] < 2**31
            or type(fields[2]) is not int or not 0 <= fields[2] <= 2**31-1):
        raise NativeRefusal('Routing-surface fields must be bounded exact integers.')
    return dict(zip(('primary_id', 'secondary_id', 'type'), fields))


def _available(value):
    return {'status': 'available', 'value': value}


def _unavailable(reason):
    return {'status': 'unavailable', 'value': None, 'reason': reason}


def _field(read):
    try:
        return _available(read())
    except AttributeError:
        return _unavailable('missing_field')
    except NativeRefusal:
        return _unavailable('invalid_value')
    except Exception:
        # Never expose an arbitrary exception message or object representation.
        return _unavailable('reader_exception')


def _mode(samples):
    if samples and type(samples[0]) is bool and all(value is samples[0] for value in samples):
        return _available(samples[0])
    return _unavailable('changed_or_unavailable')


class BuildObserver(object):
    """Bounded readback only; construction captures Thread object plus ident.

    context_reader() -> a plain dict with context: BuildContext,
    zone_running: bool, in_build_buy: bool or None.
    object_reader(int_id) -> the same native-style object on repeated resolution,
    or None. Its read-only shape is documented in BUILD_OBSERVATION.md.
    Readers/admission are trusted Python, not sandboxed or authenticated here.
    """
    def __init__(self, context_reader, object_reader, admission=None,
                 session_id=None, process_epoch=None):
        if not callable(context_reader) or not callable(object_reader):
            raise NativeRefusal('Explicit context and bounded object readers are required.')
        self._owner_thread = threading.current_thread()
        self._owner_ident = self._owner_thread.ident
        self._context_reader = context_reader
        self._object_reader = object_reader
        self._admission = admission if admission is not None else DenyAdmission()
        self._binding = (None if session_id is None and process_epoch is None
                         else _capture_binding(session_id, process_epoch))
        self._busy = False
        self._last_context = None
        self._last_event = None
        self._gap_observed = False

    @staticmethod
    def capabilities():
        return {'source_only': True, 'read_only': True, 'max_object_ids': MAX_OBJECT_IDS,
                'live_qualified': False, 'independent_evidence': False,
                'atomic_snapshot': False, 'context_source': 'injected_host_declaration'}

    def _owned(self):
        current = threading.current_thread()
        if current is not self._owner_thread or current.ident != self._owner_ident:
            raise NativeRefusal('Build readers belong to the captured owner Thread and ident.')

    def _enter(self):
        self._owned()
        if self._busy:
            raise NativeRefusal('Reentrant Build observation is refused.')
        self._busy = True

    def _preflight(self, request, expected, selected):
        copied = _copy_request(request)
        key = _context_key(expected)
        if (copied.operation != 'observe' or self._binding is None
                or _capture_binding(copied.session_id, copied.process_epoch) != self._binding
                or key[:3] != self._binding):
            raise NativeRefusal('Read request and context must match the captured session and process.')
        view = _copy_request(copied)
        context_view = _context_copy(key)
        try:
            # Existing status-only require_read permission is deliberately not
            # enough: the same host provider must admit this precise Build scope.
            verdict = self._admission.require_build_read(view, context_view, selected)
        except Exception:
            raise NativeRefusal('Current read admission could not be established.') from None
        self._owned()
        _check_admission_request(copied, view)
        if _context_key(context_view) != key:
            raise NativeRefusal('Build admission changed its exact context view.')
        if verdict is not True:
            raise NativeRefusal('Current read admission must explicitly permit the exact request.')
        return copied.request_id, key

    def _sample(self, key, samples):
        self._owned()
        # Admission and exact host bindings were checked before this first reader.
        # There is no recursive read-admission loop.
        try:
            raw = self._context_reader()
        except Exception:
            raise NativeRefusal('Current Build context could not be read.') from None
        self._owned()
        if (type(raw) is not dict or len(raw) > 3
                or _context_key(raw.get('context')) != key
                or raw.get('zone_running') is not True):
            raise NativeRefusal('Build context changed, is unavailable, or is not running.')
        mode = raw.get('in_build_buy')
        samples.append(mode if type(mode) is bool else None)

    def _resolve(self, object_id, key, samples):
        self._sample(key, samples)
        self._owned()
        try:
            value = self._object_reader(int(object_id))
            error = None
        except Exception:
            value, error = None, 'reader_exception'
        self._owned()
        self._sample(key, samples)
        return value, error

    def _read_field(self, read, key, samples):
        self._sample(key, samples)
        self._owned()
        result = _field(read)
        self._owned()
        self._sample(key, samples)
        return result

    def _object(self, object_id, key, samples):
        value, error = self._resolve(object_id, key, samples)
        if error or value is None:
            return {'object_id': object_id, **_unavailable(error or 'not_found')}
        identity = self._read_field(lambda: _id(value.id), key, samples)
        if identity.get('value') != object_id:
            return {'object_id': object_id, **_unavailable('identity_mismatch_or_unavailable')}
        # Each returned leaf is copied to a bounded primitive. No native object,
        # position, quaternion, surface or definition reference escapes.
        fields = {
            'definition_id': self._read_field(lambda: _id(value.definition.id), key, samples),
            'position': self._read_field(lambda: _vector(value.position, ('x', 'y', 'z')), key, samples),
            'orientation': self._read_field(lambda: _quaternion(value.orientation), key, samples),
            'routing_surface': self._read_field(lambda: _routing(value.routing_surface), key, samples)}
        again, error = self._resolve(object_id, key, samples)
        # Keep the first object alive until re-resolution: Python id reuse cannot
        # make a replacement compare equal by identity within this read.
        if error or again is None:
            return {'object_id': object_id, **_unavailable(error or 'disappeared')}
        if (again is not value
                or self._read_field(lambda: _id(again.id), key, samples).get('value') != object_id):
            return {'object_id': object_id, **_unavailable('identity_changed')}
        self._sample(key, samples)
        return {'object_id': object_id, 'status': 'observed', 'fields': fields,
                'identity_scope': 'this_read_only'}

    def _activate(self, key):
        if self._last_context != key:
            self._last_context = key
            self._last_event = None
            self._gap_observed = False

    def _events(self):
        return {'last_sequence': None if self._last_event is None else self._last_event[0],
                'last_transition': None if self._last_event is None else self._last_event[1],
                'sequence_gap_observed': self._gap_observed,
                'initial_mode_known': False, 'current_mode_from_events': None}

    def observe(self, request, expected_context, object_ids=()):
        """Read only explicitly selected objects. Empty selection reads mode only."""
        self._enter()
        try:
            selected = _object_ids(object_ids)
            request_id, key = self._preflight(request, expected_context, selected)
            samples = []
            self._sample(key, samples)
            objects = [self._object(object_id, key, samples) for object_id in selected]
            self._sample(key, samples)
            self._activate(key)
            return dict(self.capabilities(), request_id=request_id,
                        context=_context_view(key), mode=_mode(samples),
                        objects=objects, transitions=self._events())
        finally:
            self._busy = False

    def record_transition(self, request, expected_context, sequence, transition):
        """Record an observed notification, never request a Build enter or exit.

        Sequence is a host-supplied source sequence, not a timestamp, replay key
        or independent evidence. Only the last accepted event is retained.
        """
        self._enter()
        try:
            if (type(sequence) is not int or not 1 <= sequence <= MAX_UINT64
                    or type(transition) is not str or transition not in ('entered', 'exited')):
                raise NativeRefusal('Expected a bounded event sequence and observed transition.')
            request_id, key = self._preflight(request, expected_context, ())
            previous = self._last_event if key == self._last_context else None
            status = 'first'
            gap = self._gap_observed if key == self._last_context else False
            if previous is not None:
                if sequence < previous[0] or (sequence == previous[0] and transition != previous[1]):
                    raise NativeRefusal('Stale or conflicting transition notification.')
                if sequence == previous[0]:
                    status = 'duplicate'
                elif sequence == previous[0] + 1:
                    status = 'contiguous'
                else:
                    status, gap = 'gap', True
            samples = []
            self._sample(key, samples)
            self._sample(key, samples)
            self._activate(key)
            self._last_event = (sequence, transition)
            self._gap_observed = gap
            mode = _mode(samples)
            matches = (None if mode['status'] != 'available'
                       else mode['value'] is (transition == 'entered'))
            return dict(self.capabilities(), request_id=request_id,
                        context=_context_view(key), mode=mode,
                        event={'sequence': sequence, 'transition': transition, 'status': status,
                               'matches_sampled_mode': matches,
                               'repeated_transition': previous is not None and sequence > previous[0]
                               and transition == previous[1]}, transitions=self._events())
        finally:
            self._busy = False
