"""Volatile, uninstalled owner-thread queue, written for Python 3.7 syntax.

This module opens no listeners, imports no native/game API, and performs no I/O.
Only owner-thread ``admit(request)``/``execute(request)`` hooks in ``drain`` may
check native state/dispatch. Neither may do network I/O or wait for a worker.
Constructing this queue does not authorize that callback or qualify a game build.
The host still needs authentication and permissions, a profile lease, current-build
qualification, and an external durable intent/result journal before dispatch.

Every admitted request ID is retained for the entire lifetime of this instance,
including capacity/TTL refusals. History never evicts, expires, or resets. Once
history fills, all new IDs are refused permanently; these *unadmitted* refusals
cannot be remembered in bounded memory. Deduplication and payload-conflict checks
cover admitted IDs, not an unbounded stream of rejected submissions. There is no
reset, rebind, cancellation, or automatic retry operation.

This is not durable exactly-once execution: process loss also loses the history.
A future host must explicitly establish and reconcile a new session, using its
external durable journal, after restart/history exhaustion. Never replay an
uncertain request under either its old ID or a replacement ID/session. TTL only
limits when an action may START, not how long an already-started action may run.
"""

from collections import deque
from dataclasses import dataclass, field
import math
import re
import threading
from time import perf_counter
from types import MappingProxyType
from typing import Any, Mapping, Optional, Union


MAX_PENDING = 48
MAX_HISTORY = 128
MAX_TTL_SECONDS = 30.0
MAX_UINT64 = (1 << 64) - 1
MAX_RESULT_NODES = 1024
MAX_RESULT_TEXT = 16384
MAX_RESULT_DEPTH = 8
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_DECIMAL = re.compile(r"[1-9][0-9]{0,19}\Z")


class QueueError(ValueError):
    """An invalid request or an operation that cannot safely proceed."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ProcessEpoch:
    pid: int
    creation_filetime: int

    def __post_init__(self):
        _check(type(self.pid) is int and 1 <= self.pid <= 2**32 - 1
               and type(self.creation_filetime) is int
               and 1 <= self.creation_filetime <= MAX_UINT64,
               "invalid_epoch", "Expected exact PID and creation FILETIME.")


class QueueBusy(QueueError):
    """Owner-thread operations never wait for another thread's queue lock."""

    def __init__(self):
        super().__init__("queue_busy", "Owner thread must try on a later tick, not wait.")


def _check(condition, code, message):
    if not condition:
        raise QueueError(code, message)


def _token(value):
    return type(value) is str and _TOKEN.fullmatch(value) is not None


def _decimal_id(value):
    return (type(value) is str and _DECIMAL.fullmatch(value) is not None
            and int(value) <= MAX_UINT64)


@dataclass(frozen=True)
class ObserveArguments:
    """Observation has no command arguments."""


@dataclass(frozen=True)
class SetSpeedArguments:
    speed: int

    def __post_init__(self):
        _check(type(self.speed) is int and 0 <= self.speed <= 3,
               "invalid_arguments", "Speed must be an integer from 0 through 3.")


@dataclass(frozen=True)
class EnterCasArguments:
    sim_id: str

    def __post_init__(self):
        _check(_decimal_id(self.sim_id), "invalid_arguments",
               "Sim ID must be exact, canonical positive uint64 decimal text.")


@dataclass(frozen=True)
class RequestExitMenuArguments:
    """Only requests the menu, never a save/discard/quit decision."""


Arguments = Union[ObserveArguments, SetSpeedArguments, EnterCasArguments,
                  RequestExitMenuArguments]
_ARGUMENT_TYPES = {
    "observe": ObserveArguments,
    "live.set_speed": SetSpeedArguments,
    "cas.enter": EnterCasArguments,
    "game.request_exit_menu": RequestExitMenuArguments,
}


@dataclass(frozen=True)
class Request:
    request_id: str
    session_id: str
    operation: str
    arguments: Arguments
    ttl_seconds: float = 5.0
    expected_zone_id: Optional[str] = None
    expected_household_id: Optional[str] = None
    process_epoch: Optional[ProcessEpoch] = None
    expected_save_slot: Optional[int] = None
    expected_save_guid: Optional[str] = None

    def __post_init__(self):
        self.validate()

    def validate(self):
        _check(_token(self.request_id) and _token(self.session_id), "invalid_id",
               "Session and request IDs must be 32 lowercase hexadecimal characters.")
        _check(type(self.operation) is str and self.operation in _ARGUMENT_TYPES,
               "invalid_operation", "The operation is not allowlisted.")
        _check(type(self.arguments) is _ARGUMENT_TYPES[self.operation],
               "invalid_arguments", "Arguments must have the exact operation's typed shape.")
        if type(self.arguments) is SetSpeedArguments:
            self.arguments.__post_init__()
        elif type(self.arguments) is EnterCasArguments:
            self.arguments.__post_init__()
        _check(type(self.ttl_seconds) in (int, float)
               and 0 < self.ttl_seconds <= MAX_TTL_SECONDS,
               "invalid_ttl", "TTL must be finite and greater than zero, at most 30 seconds.")
        if self.process_epoch is not None:
            _check(type(self.process_epoch) is ProcessEpoch, "invalid_epoch",
                   "Expected an immutable exact process epoch.")
            self.process_epoch.__post_init__()
        if self.expected_save_slot is not None or self.expected_save_guid is not None:
            _check(type(self.expected_save_slot) is int
                   and 0 <= self.expected_save_slot <= MAX_UINT64
                   and type(self.expected_save_guid) is str
                   and (self.expected_save_guid == "0" or _decimal_id(self.expected_save_guid)),
                   "invalid_context", "Expected exact save slot and canonical uint64 GUID text.")
        if self.operation == "observe":
            _check(self.expected_zone_id is None and self.expected_household_id is None
                   and self.expected_save_slot is None and self.expected_save_guid is None,
                   "invalid_context", "Observation does not carry mutation preconditions.")
        else:
            _check(_decimal_id(self.expected_zone_id)
                   and _decimal_id(self.expected_household_id), "invalid_context",
                   "Mutations require exact expected zone and household uint64 IDs.")

    def arguments_dict(self):
        """Return a detached native-adapter argument view, never mutable request state."""
        if self.operation == "live.set_speed":
            return {"speed": self.arguments.speed}
        if self.operation == "cas.enter":
            return {"sim_id": self.arguments.sim_id}
        return {}


@dataclass(frozen=True)
class LiveQueueArguments:
    actor_sim_id: str

    def __post_init__(self):
        _check(type(self) is LiveQueueArguments and _decimal_id(self.actor_sim_id), "invalid_arguments",
               "Live reads require one exact canonical positive uint64 actor ID.")


@dataclass(frozen=True)
class LiveQueueExpectedContext:
    zone_id: str
    household_id: str
    client_id: str
    save_slot: int
    save_guid: str
    zone_generation: int
    mode: str = 'live'

    def __post_init__(self):
        _check(type(self) is LiveQueueExpectedContext
               and all(_decimal_id(value) for value in
                       (self.zone_id, self.household_id, self.client_id))
               and type(self.save_slot) is int and 0 <= self.save_slot <= MAX_UINT64
               and type(self.save_guid) is str
               and (self.save_guid == '0' or _decimal_id(self.save_guid))
               and type(self.zone_generation) is int
               and 1 <= self.zone_generation <= MAX_UINT64
               and type(self.mode) is str and self.mode == 'live',
               "invalid_context", "Live reads require complete exact Live context.")


@dataclass(frozen=True)
class LiveQueueRequest:
    request_id: str
    session_id: str
    process_epoch: ProcessEpoch
    arguments: LiveQueueArguments
    expected_context: LiveQueueExpectedContext
    ttl_seconds: float = 5.0
    operation: str = field(default='live.observe_queue', init=False)

    def __post_init__(self):
        LiveQueueRequest.validate(self)
        epoch, args, context = self.process_epoch, self.arguments, self.expected_context
        object.__setattr__(self, 'process_epoch', ProcessEpoch(epoch.pid, epoch.creation_filetime))
        object.__setattr__(self, 'arguments', LiveQueueArguments(args.actor_sim_id))
        object.__setattr__(self, 'expected_context', LiveQueueExpectedContext(
            context.zone_id, context.household_id, context.client_id, context.save_slot,
            context.save_guid, context.zone_generation, context.mode))

    def validate(self):
        _check(type(self) is LiveQueueRequest, "invalid_request", "Expected exact Live request.")
        _check(_token(self.request_id) and _token(self.session_id), "invalid_id",
               "Session and request IDs must be 32 lowercase hexadecimal characters.")
        _check(type(self.operation) is str and self.operation == 'live.observe_queue',
               "invalid_operation", "Expected the distinct Live read operation.")
        _check(type(self.process_epoch) is ProcessEpoch, "invalid_epoch", "Expected exact epoch.")
        ProcessEpoch.__post_init__(self.process_epoch)
        _check(type(self.arguments) is LiveQueueArguments, "invalid_arguments",
               "Expected exact Live arguments.")
        LiveQueueArguments.__post_init__(self.arguments)
        _check(type(self.expected_context) is LiveQueueExpectedContext, "invalid_context",
               "Expected exact Live context.")
        LiveQueueExpectedContext.__post_init__(self.expected_context)
        _check(type(self.ttl_seconds) in (int, float)
               and 0 < self.ttl_seconds <= MAX_TTL_SECONDS, "invalid_ttl",
               "TTL must be finite and greater than zero, at most 30 seconds.")


def live_request_key(request):
    """Private-retention value: every field, including the TTL's numeric kind."""
    _check(type(request) is LiveQueueRequest, "invalid_request", "Expected exact Live request.")
    LiveQueueRequest.validate(request)
    context = request.expected_context
    return (request.request_id, request.session_id, request.process_epoch.pid,
            request.process_epoch.creation_filetime, request.arguments.actor_sim_id,
            context.zone_id, context.household_id, context.client_id, context.save_slot,
            context.save_guid, context.zone_generation, context.mode, request.operation,
            request.ttl_seconds, 'int' if type(request.ttl_seconds) is int else 'float')


def live_request_from_key(key):
    """Reconstruct a detached callback view from an internal primitive baseline."""
    request = LiveQueueRequest(key[0], key[1], ProcessEpoch(key[2], key[3]),
                              LiveQueueArguments(key[4]),
                              LiveQueueExpectedContext(*key[5:12]), key[13])
    _check(live_request_key(request) == key, "invalid_request", "Invalid Live baseline.")
    return request


def _legacy_request_key(request):
    """Capture every legacy field through fresh exact typed values, never hooks.

    Keep the original legacy dataclass validation/fields unchanged. Constructing
    a detached value avoids calling instance-shadowed validate, __post_init__ or
    arguments_dict methods on any caller-owned object.
    """
    _check(type(request) is Request, "invalid_request", "Expected an exact legacy request.")
    operation = request.operation
    _check(type(operation) is str and operation in _ARGUMENT_TYPES,
           "invalid_operation", "The operation is not allowlisted.")
    arguments = request.arguments
    _check(type(arguments) is _ARGUMENT_TYPES[operation],
           "invalid_arguments", "Expected exact legacy operation arguments.")
    argument_value = None
    if operation == 'live.set_speed':
        arguments = SetSpeedArguments(arguments.speed)
        argument_value = arguments.speed
    elif operation == 'cas.enter':
        arguments = EnterCasArguments(arguments.sim_id)
        argument_value = arguments.sim_id
    else:
        arguments = _ARGUMENT_TYPES[operation]()
    epoch = request.process_epoch
    if epoch is not None:
        _check(type(epoch) is ProcessEpoch, "invalid_epoch", "Expected exact process epoch.")
        epoch = ProcessEpoch(epoch.pid, epoch.creation_filetime)
    copied = Request(request.request_id, request.session_id, operation, arguments,
                     request.ttl_seconds, request.expected_zone_id, request.expected_household_id,
                     epoch, request.expected_save_slot, request.expected_save_guid)
    return (copied.request_id, copied.session_id, operation, argument_value,
            copied.ttl_seconds, 'int' if type(copied.ttl_seconds) is int else 'float',
            copied.expected_zone_id, copied.expected_household_id,
            None if epoch is None else epoch.pid,
            None if epoch is None else epoch.creation_filetime,
            copied.expected_save_slot, copied.expected_save_guid)


def _legacy_request_from_key(key):
    operation = key[2]
    arguments = (_ARGUMENT_TYPES[operation]() if key[3] is None
                 else _ARGUMENT_TYPES[operation](key[3]))
    epoch = None if key[8] is None else ProcessEpoch(key[8], key[9])
    request = Request(key[0], key[1], operation, arguments, key[4], key[6], key[7],
                      epoch, key[10], key[11])
    _check(_legacy_request_key(request) == key, "invalid_request", "Invalid legacy baseline.")
    return request


def operation_is_mutation(request):
    """Only exact known types/operations classify; unknown never means read."""
    if type(request) not in (Request, LiveQueueRequest) and __package__:
        from .build_request import BuildRequest, request_key
        if type(request) is BuildRequest:
            request_key(request)
            return request.operation == "build.transform_selected"
    if type(request) is LiveQueueRequest:
        LiveQueueRequest.validate(request)
        return False
    key = _legacy_request_key(request)
    if key[2] == 'observe':
        return False
    _check(key[2] in ('live.set_speed', 'cas.enter', 'game.request_exit_menu'),
           "invalid_operation", "Unknown operations cannot be classified.")
    return True


@dataclass(frozen=True)
class Snapshot:
    request_id: str
    session_id: str
    state: str
    reason: Optional[str] = None
    result: Optional[Mapping[str, Any]] = None
    admitted_at: Optional[float] = None
    expires_at: Optional[float] = None
    started_at: Optional[float] = None


@dataclass(frozen=True)
class QueueStatus:
    session_id: str
    pending: int
    retained: int
    pending_capacity: int
    history_capacity: int
    history_exhausted: bool
    mutation_blocked_reason: Optional[str] = None
    closed_reason: Optional[str] = None


@dataclass
class _Entry:
    snapshot: Snapshot
    # Admission-time classification is a private immutable bool. Never derive
    # safety latches or later gates from a callback-modifiable request view.
    mutation: bool
    legacy_key: object = None
    live_key: object = None
    build_key: object = None


def _snapshot_copy(snapshot):
    """Detach public views from private retained and deferred-completion state.

    frozen=True cannot stop object.__setattr__. Mapping proxies also expose their
    backing mapping to reflected comparisons, so even frozen result containers
    must be copied before they leave the private queue. Only exact scalars share.
    """
    return Snapshot(snapshot.request_id, snapshot.session_id, snapshot.state,
                    snapshot.reason, _copy_frozen_result(snapshot.result), snapshot.admitted_at,
                    snapshot.expires_at, snapshot.started_at)


def _copy_frozen_result(value):
    # Only private results already validated and frozen by _freeze_result enter
    # here. Preserve their exact representation without invoking provider hooks.
    if type(value) is MappingProxyType:
        return MappingProxyType({name: _copy_frozen_result(child) for name, child in value.items()})
    if type(value) is tuple:
        return tuple(_copy_frozen_result(child) for child in value)
    return value


def _entry_request(entry):
    if entry.build_key is not None:
        from .build_request import request_from_key
        return request_from_key(entry.build_key)
    return (_legacy_request_from_key(entry.legacy_key) if entry.live_key is None
            else live_request_from_key(entry.live_key))


def _entry_view_unchanged(entry, view):
    if entry.build_key is not None:
        from .build_request import request_key
        return request_key(view) == entry.build_key
    return (_legacy_request_key(view) == entry.legacy_key if entry.live_key is None
            else live_request_key(view) == entry.live_key)


def _thaw_result(value):
    """Only accepts our frozen result containers, never provider conversion hooks."""
    if type(value) is MappingProxyType:
        return {name: _thaw_result(child) for name, child in value.items()}
    if type(value) is tuple:
        return [_thaw_result(child) for child in value]
    return value


def _freeze_result(result):
    """Validate the explicit outcome contract and bound/copy/freeze its details."""
    _check(type(result) is dict and type(result.get("outcome")) is str
           and result["outcome"] in ("verified", "unverified"),
           "invalid_execution_result", "Expected an explicit verified/unverified result.")
    budget = [0, 0]

    def visit(value, depth):
        budget[0] += 1
        _check(budget[0] <= MAX_RESULT_NODES and depth <= MAX_RESULT_DEPTH,
               "invalid_execution_result", "Result exceeds its structural bound.")
        kind = type(value)
        if kind is str:
            budget[1] += len(value)
            _check(budget[1] <= MAX_RESULT_TEXT, "invalid_execution_result",
                   "Result text exceeds its bound.")
            return value
        if value is None or kind is bool:
            return value
        if kind is int:
            _check(-MAX_UINT64 <= value <= MAX_UINT64, "invalid_execution_result",
                   "Result integer exceeds its bound.")
            return value
        if kind is float:
            _check(math.isfinite(value), "invalid_execution_result", "Result must be finite.")
            return value
        if kind is dict:
            _check(len(value) <= MAX_RESULT_NODES, "invalid_execution_result",
                   "Result object exceeds its bound.")
            frozen = {}
            for key, child in value.items():
                _check(type(key) is str, "invalid_execution_result", "Result keys must be strings.")
                visit(key, depth + 1)
                frozen[key] = visit(child, depth + 1)
            return MappingProxyType(frozen)
        if kind is list:
            _check(len(value) <= MAX_RESULT_NODES, "invalid_execution_result",
                   "Result array exceeds its bound.")
            return tuple(visit(child, depth + 1) for child in value)
        raise QueueError("invalid_execution_result", "Result details must be plain JSON values.")

    return visit(result, 0)


class CompanionQueue:
    """A bounded, single-session, at-most-once queue for a future qualified host.

    Construct on the owner thread. Worker threads may submit or read snapshots.
    Only that same Thread object and ident may drain. No lock is held while the
    injected callback runs. The owner never blocks on a queue lock: contention
    raises QueueBusy, which is a later-tick condition, not permission to replay.
    Status reads are observational; only drain expires pending work.
    """

    def __init__(self, session_id, pending_capacity=MAX_PENDING,
                 history_capacity=MAX_HISTORY, clock=perf_counter):
        _check(_token(session_id), "invalid_id", "Invalid session ID.")
        _check(type(pending_capacity) is int and 1 <= pending_capacity <= MAX_PENDING,
               "invalid_capacity", "Pending capacity must be from 1 through 48.")
        _check(type(history_capacity) is int and pending_capacity <= history_capacity <= MAX_HISTORY,
               "invalid_capacity", "History capacity must cover pending capacity and be at most 128.")
        _check(callable(clock), "invalid_clock", "A monotonic clock callable is required.")
        self._session_id = session_id
        self._pending_capacity = pending_capacity
        self._history_capacity = history_capacity
        self._clock = clock
        self._last_time = None
        self._owner_thread = threading.current_thread()
        self._owner_ident = self._owner_thread.ident
        self._lock = threading.Lock()
        self._entries = {}
        self._pending = deque()
        # deque append/popleft are thread-safe. Only drain appends, readers apply
        # completions while holding _lock. This handoff prevents a finishing owner
        # callback from waiting on readers, without exposing mutable result state.
        # Each admitted entry can yield at most one completion, so it is bounded.
        self._completed = deque()
        self._draining = False
        self._mutation_blocked_reason = None
        self._closed_reason = None

    @property
    def session_id(self):
        return self._session_id

    @property
    def owner_thread_id(self):
        return self._owner_ident

    def _is_owner(self):
        thread = threading.current_thread()
        return thread is self._owner_thread and thread.ident == self._owner_ident

    def _acquire(self):
        if not self._lock.acquire(blocking=not self._is_owner()):
            raise QueueBusy()

    def _apply_completed(self):
        while self._completed:
            request_id, snapshot = self._completed.popleft()
            entry = self._entries[request_id]
            entry.snapshot = snapshot
            if entry.mutation and snapshot.state in ("unknown", "unverified"):
                self._mutation_blocked_reason = self._mutation_blocked_reason or "unresolved_mutation"

    def _now(self):
        value = self._clock()
        _check(type(value) in (int, float), "invalid_clock",
               "Clock must return a finite nonnegative number.")
        try:
            value = float(value)
        except OverflowError:
            raise QueueError("invalid_clock", "Clock is outside the supported range.")
        _check(math.isfinite(value) and value >= 0,
               "invalid_clock", "Clock must return a finite nonnegative number.")
        _check(self._last_time is None or value >= self._last_time,
               "invalid_clock", "Clock must not move backward.")
        self._last_time = value
        return value

    def submit(self, request):
        """Admit or return the existing snapshot. Never execute or wait as owner."""
        build_key = None
        if type(request) not in (Request, LiveQueueRequest) and __package__:
            from .build_request import BuildRequest, request_key, request_from_key
            build_key = request_key(request) if type(request) is BuildRequest else None
        live_key = live_request_key(request) if type(request) is LiveQueueRequest else None
        legacy_key = _legacy_request_key(request) if live_key is None and build_key is None else None
        request = (request_from_key(build_key) if build_key is not None else
                   live_request_from_key(live_key) if live_key is not None else
                   _legacy_request_from_key(legacy_key))
        mutation = operation_is_mutation(request)
        _check(request.session_id == self._session_id, "session_mismatch",
               "Request belongs to a different session; reconcile rather than replay.")
        self._acquire()
        try:
            self._apply_completed()
            existing = self._entries.get(request.request_id)
            if existing is not None:
                if (existing.live_key != live_key or existing.legacy_key != legacy_key
                        or existing.build_key != build_key):
                    self._mutation_blocked_reason = self._mutation_blocked_reason or "request_conflict"
                    raise QueueError("request_conflict", "This ID has a different immutable payload.")
                return _snapshot_copy(existing.snapshot)
            if len(self._entries) == self._history_capacity:
                return Snapshot(request.request_id, self._session_id, "refused", "history_full")
            now = self._now()
            deadline = now + request.ttl_seconds
            _check(math.isfinite(deadline) and deadline > now, "invalid_clock",
                   "Clock precision cannot represent this request's TTL.")
            reason = self._closed_reason
            if reason is None and mutation:
                reason = self._mutation_blocked_reason
            if reason is None and len(self._pending) == self._pending_capacity:
                reason = "pending_full"
            snapshot = Snapshot(request.request_id, self._session_id,
                                "refused" if reason else "pending", reason,
                                admitted_at=now, expires_at=deadline)
            self._entries[request.request_id] = _Entry(snapshot, mutation, legacy_key, live_key, build_key)
            if reason is None:
                self._pending.append(request.request_id)
            return _snapshot_copy(snapshot)
        finally:
            self._lock.release()

    def status(self, request_id):
        """Read an immutable snapshot or None. Never expire, cancel, or dispatch."""
        _check(_token(request_id), "invalid_id", "Invalid request ID.")
        self._acquire()
        try:
            self._apply_completed()
            entry = self._entries.get(request_id)
            return None if entry is None else _snapshot_copy(entry.snapshot)
        finally:
            self._lock.release()

    def queue_status(self):
        """Read bounded occupancy, without consulting game state or the clock."""
        self._acquire()
        try:
            self._apply_completed()
            return QueueStatus(self._session_id, len(self._pending), len(self._entries),
                               self._pending_capacity, self._history_capacity,
                               len(self._entries) == self._history_capacity,
                               self._mutation_blocked_reason, self._closed_reason)
        finally:
            self._lock.release()

    def block_mutations(self, reason="unresolved_mutation"):
        """Permanently close mutation admission and dispatch, keeping receipts.

        The owner remains nonblocking. This does not abort an already-started
        callback, erase history, or create a reconciliation/reset route.
        """
        _check(type(reason) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason),
               "invalid_reason", "Expected a bounded refusal reason code.")
        self._acquire()
        try:
            self._mutation_blocked_reason = self._mutation_blocked_reason or reason
        finally:
            self._lock.release()

    def close(self, reason="host_shutdown"):
        """Permanently refuse future dispatch; never wait, even on a control worker."""
        _check(type(reason) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason),
               "invalid_reason", "Expected a bounded refusal reason code.")
        if not self._lock.acquire(blocking=False):
            raise QueueBusy()
        try:
            self._closed_reason = self._closed_reason or reason
        finally:
            self._lock.release()

    def _finish(self, request_id, old, state, reason=None, result=None, started_at=None):
        final = Snapshot(request_id, self._session_id, state, reason, result,
                         old.admitted_at, old.expires_at, started_at)
        self._completed.append((request_id, final))
        self._draining = False
        if self._lock.acquire(blocking=False):
            try:
                self._apply_completed()
            finally:
                self._lock.release()
        return _snapshot_copy(final)

    def drain(self, execute, admit=None):
        """Consume at most one pending entry and invoke execute at most once.

        Optional owner-only admit(request) runs BEFORE the started transition,
        with no lock held, and must explicitly return True. It must only check
        admission, never dispatch. Receipt readers still see pending while this
        check runs. Reacquisition is nonblocking; contention refuses the selected
        request without requeue. TTL, closure and uncertainty are checked again.

        After started is published EVERY executor exception/invalid result is
        unknown, never a known-not-dispatched refusal. SystemExit/KeyboardInterrupt
        preserve that record before propagation. Adapter verified is an assertion,
        never independent evidence of build qualification or gameplay success.
        """
        _check(self._is_owner(), "wrong_thread", "Only the captured owner may drain.")
        _check(not self._draining, "reentrant_drain", "Drain must not call itself.")
        _check(callable(execute), "invalid_executor", "An execution callback is required.")
        _check(admit is None or callable(admit), "invalid_admission", "Expected an owner admission hook.")
        self._acquire()
        try:
            self._apply_completed()
            if not self._pending:
                return None
            request_id = self._pending[0]
            entry = self._entries[request_id]
            old = entry.snapshot
            try:
                now = self._now()
                reason = self._closed_reason
                if reason is None and entry.mutation:
                    reason = self._mutation_blocked_reason
                if reason is None and now >= old.expires_at:
                    reason = "expired"
            except Exception:
                reason = "invalid_clock"
            self._pending.popleft()
            if reason is not None:
                entry.snapshot = Snapshot(request_id, self._session_id, "refused", reason,
                                          admitted_at=old.admitted_at, expires_at=old.expires_at)
                return _snapshot_copy(entry.snapshot)
            self._draining = True
        finally:
            self._lock.release()

        try:
            view = _entry_request(entry)
            allowed = admit is None or admit(view) is True
            if not _entry_view_unchanged(entry, view):
                allowed = False
        except BaseException as error:
            final = self._finish(request_id, old, "refused", "host_dispatch_denied")
            if not isinstance(error, Exception):
                raise
            return final
        if not allowed:
            return self._finish(request_id, old, "refused", "host_dispatch_denied")
        if not self._lock.acquire(blocking=False):
            return self._finish(request_id, old, "refused", "queue_busy_before_start")
        reason, now = None, None
        try:
            self._apply_completed()
            try:
                now = self._now()
                reason = self._closed_reason
                if reason is None and entry.mutation:
                    reason = self._mutation_blocked_reason
                if reason is None and now >= old.expires_at:
                    reason = "expired"
            except BaseException as error:
                reason = "invalid_clock"
                if not isinstance(error, Exception):
                    self._finish(request_id, old, "refused", reason)
                    raise
            if reason is None:
                entry.snapshot = Snapshot(request_id, self._session_id, "started",
                                          admitted_at=old.admitted_at, expires_at=old.expires_at,
                                          started_at=now)
        finally:
            self._lock.release()
        if reason is not None:
            return self._finish(request_id, old, "refused", reason)

        result = None
        state, reason = "unknown", "execution_exception"
        try:
            view = _entry_request(entry)
            raw = execute(view)
            try:
                candidate = _freeze_result(raw)
                _check(_entry_view_unchanged(entry, view), "invalid_execution_result",
                       "Execution changed the request view.")
                if entry.live_key is not None:
                    # Delayed import keeps queue construction inert and avoids a
                    # module cycle. Validate the frozen copy against original intent.
                    from .live_wire import preview_result_receipt
                    preview_result_receipt(_thaw_result(candidate),
                                           live_request_from_key(entry.live_key))
                if entry.build_key is not None:
                    from .build_wire import preview_result_receipt
                    from .build_request import request_from_key
                    preview_result_receipt(_thaw_result(candidate), request_from_key(entry.build_key))
                result = candidate
                state, reason = result["outcome"], None
            except Exception:
                state, reason = "unknown", "invalid_execution_result"
        except Exception:
            # Never retain or expose exception messages, nor infer non-dispatch.
            pass
        finally:
            final = self._finish(request_id, old, state, reason, result, now)
        return final
