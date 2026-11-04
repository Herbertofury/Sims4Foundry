"""Uninstalled, default-denied Live queue readback using trusted injected readers.

No game imports, bootstrap, I/O, dispatch, events, clock or retained observations.
These sequential samples are not atomic, independent evidence, durable handles,
completion/idle assertions or installed-game qualification. See
``docs/LIVE_NATIVE_OBSERVATION.md`` for the host contract and source provenance.
"""
from dataclasses import dataclass
import json
import threading

from .native import DenyAdmission, NativeRefusal, _capture_binding
from .queue import (MAX_TTL_SECONDS, MAX_UINT64, ObserveArguments, ProcessEpoch,
                    Request, LiveQueueRequest, live_request_key, live_request_from_key,
                    _decimal_id, _token)


MAX_ROWS = 64
REQUEST_MAX_ROWS = 49
MAX_OUTPUT_BYTES = 32 * 1024


class _LiveRowLimitExceeded(NativeRefusal):
    """Fixed request-bound overflow, never an arbitrary provider diagnostic."""
    def __init__(self):
        super().__init__('row_limit_exceeded')


@dataclass(frozen=True)
class LiveContext:
    """Host-declared context; generation must change on every zone lifetime."""
    session_id: str
    process_epoch: ProcessEpoch
    zone_id: str
    household_id: str
    client_id: str
    save_slot: int
    save_guid: str
    zone_generation: int

    def __post_init__(self):
        key = _context_key(self)
        # A frozen dataclass does not otherwise detach its nested mutable alias.
        object.__setattr__(self, 'process_epoch', ProcessEpoch(key[1], key[2]))


def _context_key(value):
    valid = False
    try:
        if type(value) is LiveContext:
            binding = _capture_binding(value.session_id, value.process_epoch)
            valid = (all(_decimal_id(item) for item in
                         (value.zone_id, value.household_id, value.client_id))
                     and type(value.save_slot) is int
                     and 0 <= value.save_slot <= MAX_UINT64
                     and type(value.save_guid) is str
                     and (value.save_guid == '0' or _decimal_id(value.save_guid))
                     and type(value.zone_generation) is int
                     and 1 <= value.zone_generation <= MAX_UINT64)
            if valid:
                key = binding + (value.zone_id, value.household_id, value.client_id,
                                 value.save_slot, value.save_guid, value.zone_generation)
    except Exception:
        valid = False
    # Raise outside the caught scope: no reader exception is deliberately chained.
    if not valid:
        raise NativeRefusal('invalid_live_context')
    return key


def _context_copy(key):
    return LiveContext(key[0], ProcessEpoch(key[1], key[2]), *key[3:])


def _context_view(key):
    return {'session_id': key[0],
            'process_epoch': {'pid': str(key[1]), 'creation_filetime': str(key[2])},
            'zone_id': key[3], 'household_id': key[4], 'client_id': key[5],
            'save_slot': str(key[6]), 'save_guid': key[7],
            'zone_generation': str(key[8])}


def _request_key(value):
    """Validate the existing exact observe DTO without invoking shadowed methods.

    Keep only immutable primitive values, including TTL's exact numeric type.
    Neither a frozen Request nor an admission-retained copy is our baseline.
    """
    valid = False
    try:
        if type(value) is Request:
            valid = (_token(value.request_id) and _token(value.session_id)
                     and type(value.operation) is str and value.operation == 'observe'
                     and type(value.arguments) is ObserveArguments
                     and type(value.ttl_seconds) in (int, float)
                     and 0 < value.ttl_seconds <= MAX_TTL_SECONDS
                     and value.expected_zone_id is None
                     and value.expected_household_id is None
                     and value.expected_save_slot is None
                     and value.expected_save_guid is None)
            if valid:
                binding = _capture_binding(value.session_id, value.process_epoch)
                key = (value.request_id,) + binding + (value.ttl_seconds,
                                                      'int' if type(value.ttl_seconds) is int else 'float')
    except Exception:
        valid = False
    if not valid:
        raise NativeRefusal('invalid_observe_request')
    return key


def _request_copy(key):
    return Request(key[0], key[1], 'observe', ObserveArguments(),
                   ttl_seconds=key[4], process_epoch=ProcessEpoch(key[2], key[3]))


def _unavailable(reason):
    return {'status': 'unavailable', 'value': None, 'reason': reason}


def _channel_unavailable(reason):
    return {'status': 'unavailable', 'rows': None, 'reason': reason}


class LiveQueueObserver(object):
    """One explicit actor, at most 64 combined queued/running occurrences.

    The three readers and admission provider are trusted synchronous Python, not
    a sandbox. Supply the same reviewed host admission object used elsewhere;
    only its exact require_live_read scope may permit this seam. No implicit
    services adapter, fallback resolver, hidden-Sim policy or native import exists.
    """
    def __init__(self, context_reader, sim_info_reader, instance_reader,
                 admission=None, session_id=None, process_epoch=None):
        if not all(callable(reader) for reader in
                   (context_reader, sim_info_reader, instance_reader)):
            raise NativeRefusal('explicit_readers_required')
        self._owner_thread = threading.current_thread()
        self._owner_ident = self._owner_thread.ident
        self._context_reader = context_reader
        self._sim_info_reader = sim_info_reader
        self._instance_reader = instance_reader
        self._admission = admission if admission is not None else DenyAdmission()
        self._binding = None
        failed = False
        if session_id is not None or process_epoch is not None:
            try:
                self._binding = _capture_binding(session_id, process_epoch)
            except Exception:
                failed = True
        if failed:
            raise NativeRefusal('invalid_host_binding')
        self._busy = False

    @staticmethod
    def capabilities():
        return {'source': 'source_only_injected_readers', 'source_only': True,
                'read_only': True, 'live_qualified': False,
                'independent_evidence': False, 'atomic_snapshot': False,
                'context_source': 'injected_host_declaration',
                'identity_scope': 'this_call_only', 'selection_authority': False,
                'cancellation_authority': False, 'action_outcome': 'unknown',
                'max_rows': MAX_ROWS, 'max_output_bytes': MAX_OUTPUT_BYTES,
                'scheduler': False, 'enforces_ttl': False}

    def _owned(self):
        current = threading.current_thread()
        if current is not self._owner_thread or current.ident != self._owner_ident:
            raise NativeRefusal('owner_mismatch')
        guard = self.__dict__.get('_request_guard')
        if guard is not None:
            guard()

    def _enter(self):
        self._owned()
        if self._busy:
            raise NativeRefusal('reentrant_observation')
        self._busy = True

    def _preflight(self, request, expected, actor_id):
        request_key = _request_key(request)
        key = _context_key(expected)
        if not _decimal_id(actor_id):
            raise NativeRefusal('invalid_actor_id')
        if (self._binding is None or request_key[1:4] != self._binding
                or key[:3] != self._binding):
            raise NativeRefusal('binding_mismatch')
        view, context_view = _request_copy(request_key), _context_copy(key)
        failed = False
        try:
            verdict = self._admission.require_live_read(view, context_view, actor_id)
        except Exception:
            failed = True
        self._owned()
        if failed:
            raise NativeRefusal('live_admission_unavailable')
        if _request_key(view) != request_key or _context_key(context_view) != key:
            raise NativeRefusal('live_admission_changed')
        if verdict is not True:
            raise NativeRefusal('live_admission_denied')
        return request_key[0], key

    def _preflight_request(self, request):
        # Never project the distinct Live operation onto legacy generic observe.
        try:
            baseline = live_request_key(request)
        except Exception:
            raise NativeRefusal('invalid_live_request') from None
        key = (baseline[1], baseline[2], baseline[3]) + baseline[5:11]
        if self._binding is None or key[:3] != self._binding:
            raise NativeRefusal('binding_mismatch')
        view = live_request_from_key(baseline)
        context_view = _context_copy(key)

        def unchanged():
            valid = False
            try:
                valid = (live_request_key(request) == baseline
                         and live_request_key(view) == baseline
                         and _context_key(context_view) == key)
            except Exception:
                pass
            if not valid:
                raise NativeRefusal('live_admission_changed')

        # Every subsequent trusted callback is bracketed by _owned(), so retained
        # request/context aliases cannot redirect a later reader under old intent.
        self._request_guard = unchanged
        failed = False
        try:
            verdict = self._admission.require_live_read(view, context_view, baseline[4])
        except Exception:
            failed = True
        self._owned()
        if failed:
            raise NativeRefusal('live_admission_unavailable')
        if verdict is not True:
            raise NativeRefusal('live_admission_denied')
        return baseline[0], key, baseline[4]

    def _sample(self, key):
        self._owned()
        failed = False
        try:
            raw = self._context_reader()
        except Exception:
            failed = True
        self._owned()
        if failed:
            raise NativeRefusal('live_context_unavailable')
        # Check exact keys before lookups, avoiding arbitrary key equality hooks.
        if (type(raw) is not dict or len(raw) != 3
                or any(type(name) is not str for name in raw)
                or set(raw) != {'context', 'zone_running', 'mode'}):
            raise NativeRefusal('invalid_live_declaration')
        current = _context_key(raw['context'])
        if (current != key or raw['zone_running'] is not True
                or type(raw['mode']) is not str or raw['mode'] != 'live'):
            raise NativeRefusal('live_context_changed_or_not_live')

    def _external(self, key, operation, *args, **options):
        """Bracket every property/reader/iterator operation, including failure.

        Only observer-owned call counts are bounded. These trusted operations
        may perform unbounded internal work; no latency/side-effect claim follows.
        """
        self._sample(key)
        self._owned()
        value, error = None, None
        kind = options.get('kind')
        try:
            value = operation(*args)
        except StopIteration:
            error = 'exhausted' if kind == 'next' else 'reader_exception'
        except AttributeError:
            error = 'missing_field' if kind == 'field' else 'reader_exception'
        except TypeError:
            error = 'not_iterable' if kind == 'iter' else 'reader_exception'
        except Exception:
            error = 'reader_exception'
        # A changed context wins over ordinary failures and exhaustion. Control
        # exceptions skip further readers and unwind to observe's guard cleanup.
        self._owned()
        self._sample(key)
        return value, error

    def _field(self, value, name, key):
        raw, error = self._external(key, getattr, value, name, kind='field')
        if error:
            return _unavailable(error)
        if raw is None:
            return _unavailable('null_value')
        if type(raw) is not int or not 1 <= raw <= MAX_UINT64:
            return _unavailable('invalid_value')
        return {'status': 'available', 'value': str(raw)}

    def _actor(self, actor_id, key):
        info, error = self._external(key, self._sim_info_reader, int(actor_id))
        if error or info is None:
            return None, None, 'reader_exception' if error else 'not_found'
        if self._field(info, 'id', key)['value'] != actor_id:
            return None, None, 'identity_mismatch_or_unavailable'
        sim, error = self._external(key, self._instance_reader, info)
        if error or sim is None:
            return info, None, 'reader_exception' if error else 'not_instanced'
        if self._field(sim, 'sim_id', key)['value'] != actor_id:
            return info, None, 'identity_mismatch_or_unavailable'
        return info, sim, None

    def _same_container(self, sim, name, container, key):
        current, error = self._external(key, getattr, sim, name, kind='field')
        if error or current is not container:
            raise NativeRefusal('container_changed')

    def _channel(self, sim, name, key, used, row_limit=MAX_ROWS):
        container, error = self._external(key, getattr, sim, name, kind='field')
        if error or container is None:
            return _channel_unavailable(error or 'null_value'), used
        iterator, error = self._external(key, iter, container, kind='iter')
        if error:
            self._same_container(sim, name, container, key)
            return _channel_unavailable(error), used
        rows = []
        while True:
            value, error = self._external(key, next, iterator, kind='next')
            if error == 'exhausted':
                break
            if error:
                raise NativeRefusal('iteration_failed')
            if used >= row_limit:
                # The sole overflow probe must not read any field from this row.
                if row_limit == REQUEST_MAX_ROWS:
                    raise _LiveRowLimitExceeded()
                raise NativeRefusal('row_limit_exceeded')
            used += 1
            rows.append({'index': len(rows),
                         'tuning_guid64': self._field(value, 'guid64', key),
                         'runtime_instance_id': self._field(value, 'id', key)})
        self._same_container(sim, name, container, key)
        return {'status': 'observed', 'rows': rows}, used

    def _observe(self, request_id, key, explicit_actor_id, row_limit):
        self._sample(key)
        info, sim, reason = self._actor(explicit_actor_id, key)
        if reason:
            actor = {'status': 'unavailable', 'reason': reason}
            queued = _channel_unavailable('actor_unavailable')
            running = _channel_unavailable('actor_unavailable')
        else:
            actor = {'status': 'observed'}
            queued, used = self._channel(sim, 'queue', key, 0, row_limit)
            running, used = self._channel(sim, 'si_state', key, used, row_limit)
            again_info, again_sim, error = self._actor(explicit_actor_id, key)
            # Retain original references, rather than compare reusable id().
            if error or again_info is not info or again_sim is not sim:
                raise NativeRefusal('actor_changed')
        self._sample(key)
        result = {'schema_version': 1, 'source': 'source_only_injected_readers',
                  'source_only': True, 'read_only': True, 'live_qualified': False,
                  'independent_evidence': False, 'atomic_snapshot': False,
                  'context_source': 'injected_host_declaration',
                  'identity_scope': 'this_call_only', 'selection_authority': False,
                  'cancellation_authority': False, 'action_outcome': 'unknown',
                  'request_id': request_id, 'context': _context_view(key),
                  'actor_sim_id': explicit_actor_id, 'actor': actor,
                  'queued': queued, 'running': running}
        encoded = json.dumps(result, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode('utf-8')) > MAX_OUTPUT_BYTES:
            raise NativeRefusal('output_limit_exceeded')
        return result

    def observe(self, request, expected_context, explicit_actor_id):
        """Unchanged standalone entry point with its existing 64-row limit."""
        self._enter()
        try:
            request_id, key = self._preflight(request, expected_context, explicit_actor_id)
            return self._observe(request_id, key, explicit_actor_id, MAX_ROWS)
        finally:
            self._busy = False

    def observe_request(self, request):
        """Complete request-bound Live read; fixed 49 rows, no selector knobs."""
        self._enter()
        try:
            request_id, key, actor_id = self._preflight_request(request)
            return self._observe(request_id, key, actor_id, REQUEST_MAX_ROWS)
        finally:
            self.__dict__.pop('_request_guard', None)
            self._busy = False
