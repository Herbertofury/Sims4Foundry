"""Volatile, uninstalled owner-thread queue, written for Python 3.7 syntax.

This module opens no listeners, imports no native/game API, and performs no I/O.
Only the caller-supplied ``execute(request)`` in ``drain`` may touch a native API.
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
from dataclasses import dataclass
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
        if self.operation == "observe":
            _check(self.expected_zone_id is None and self.expected_household_id is None,
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


@dataclass
class _Entry:
    request: Request
    snapshot: Snapshot


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
            self._entries[request_id].snapshot = snapshot

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
        _check(type(request) is Request, "invalid_request", "A typed Request is required.")
        request.validate()
        _check(request.session_id == self._session_id, "session_mismatch",
               "Request belongs to a different session; reconcile rather than replay.")
        self._acquire()
        try:
            self._apply_completed()
            existing = self._entries.get(request.request_id)
            if existing is not None:
                _check(existing.request == request, "request_conflict",
                       "This request ID already has a different immutable payload.")
                return existing.snapshot
            if len(self._entries) == self._history_capacity:
                return Snapshot(request.request_id, self._session_id, "refused", "history_full")
            now = self._now()
            deadline = now + request.ttl_seconds
            _check(math.isfinite(deadline) and deadline > now, "invalid_clock",
                   "Clock precision cannot represent this request's TTL.")
            full = len(self._pending) == self._pending_capacity
            snapshot = Snapshot(request.request_id, self._session_id,
                                "refused" if full else "pending",
                                "pending_full" if full else None,
                                admitted_at=now, expires_at=deadline)
            self._entries[request.request_id] = _Entry(request, snapshot)
            if not full:
                self._pending.append(request.request_id)
            return snapshot
        finally:
            self._lock.release()

    def status(self, request_id):
        """Read an immutable snapshot or None. Never expire, cancel, or dispatch."""
        _check(_token(request_id), "invalid_id", "Invalid request ID.")
        self._acquire()
        try:
            self._apply_completed()
            entry = self._entries.get(request_id)
            return None if entry is None else entry.snapshot
        finally:
            self._lock.release()

    def queue_status(self):
        """Read bounded occupancy, without consulting game state or the clock."""
        self._acquire()
        try:
            self._apply_completed()
            return QueueStatus(self._session_id, len(self._pending), len(self._entries),
                               self._pending_capacity, self._history_capacity,
                               len(self._entries) == self._history_capacity)
        finally:
            self._lock.release()

    def drain(self, execute):
        """Consume at most one pending entry, invoking execute at most once.

        Empty ticks do not even consult the clock. An expired entry is refused
        without execute. A callback exception/invalid result becomes unknown and
        is never retried. SystemExit/KeyboardInterrupt are recorded unknown before
        being re-raised. A verified result is only the adapter's assertion, not
        independent evidence that its game build or API has been qualified.
        """
        _check(self._is_owner(), "wrong_thread", "Only the captured owner may drain.")
        _check(not self._draining, "reentrant_drain", "Drain must not call itself.")
        _check(callable(execute), "invalid_executor", "An execution callback is required.")
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
                reason = "expired" if now >= old.expires_at else None
            except Exception:
                now = None
                reason = "invalid_clock"
            self._pending.popleft()
            if reason is not None:
                entry.snapshot = Snapshot(request_id, self._session_id, "refused", reason,
                                          admitted_at=old.admitted_at, expires_at=old.expires_at)
                return entry.snapshot
            entry.snapshot = Snapshot(request_id, self._session_id, "started",
                                      admitted_at=old.admitted_at, expires_at=old.expires_at,
                                      started_at=now)
            self._draining = True
        finally:
            self._lock.release()

        result = None
        state, reason = "unknown", "execution_exception"
        try:
            raw = execute(entry.request)
            try:
                result = _freeze_result(raw)
                state, reason = result["outcome"], None
            except Exception:
                state, reason = "unknown", "invalid_execution_result"
        except Exception:
            # No exception object/message is retained or logged; the host owns
            # safe diagnostics and read-only reconciliation of uncertain actions.
            pass
        finally:
            final = Snapshot(request_id, self._session_id, state, reason, result,
                             old.admitted_at, old.expires_at, now)
            self._completed.append((request_id, final))
            self._draining = False
            if self._lock.acquire(blocking=False):
                try:
                    self._apply_completed()
                finally:
                    self._lock.release()
        return final
