"""Explicit-lifecycle, bounded loopback bridge. Uninstalled and default denied.

Import/construction starts nothing. No authentication implementation, credential,
bootstrap, scheduler, game import, native executor or durable journal ships here.
A trusted host must independently authenticate the peer and authorize/record each
operation through the policy seam; 127.0.0.1 and a digest are NOT authentication.
The seam is deliberately not configurable by a wire field or CLI flag.

One request frame per connection, followed by client write EOF before admission.
Extra bytes (including coalesced frames) fail the entire request. Reconnecting for
receipt_read only observes retained history. No requeue, TTL renewal or reset.
Every connection has one absolute deadline from accept through response; partial
reads/writes cannot extend it. Host callbacks must also obey that deadline and
must never call game APIs from workers. Python cannot preempt a misbehaving hook.
"""

from dataclasses import dataclass
import socket
import struct
import sys
import threading
from time import monotonic

from .queue import (CompanionQueue, ProcessEpoch, QueueError,
                    Snapshot, MAX_HISTORY, MAX_PENDING)
from .wire import (MAX_FRAME_BYTES, ProtocolError, decode_payload, encode_frame,
                   parse_submit, parse_receipt_read, receipt)

MAX_CONNECTIONS = 8
MAX_CONNECTION_SECONDS = 5.0


@dataclass(frozen=True)
class Binding:
    session_id: str
    process_epoch: ProcessEpoch


class DenyHostPolicy:
    """Required external security/provider integration. All defaults deny.

    authorize_listener: establish exact process/profile/session, prior-history
      reconciliation, durable journal and qualification before opening a port.
    authorize_peer: independently authenticate THIS socket/peer, with bounded
      work. The peer tuple, claimed IDs and digest alone cannot satisfy this.
    authorize_submit: require exact operation/context authorization and durable
      intent, without reading game APIs or dispatching. Called once per new ID.
    authorize_dispatch: owner-only fresh process/profile/zone/household/save,
      lease and qualification admission. Bounded local/native checks only: no
      network/journal I/O, waiting for workers, or dispatch of the operation.

    Methods must return the exact bool True. No implementation is included.
    The listener cannot enforce correctness of a host-supplied policy; installing
    an allow-all fixture in production would REMOVE its security boundary.
    """
    def authorize_listener(self, binding):
        return False

    def authorize_peer(self, connection, peer, binding, deadline):
        return False

    def authorize_submit(self, submission, binding, deadline):
        return False

    def authorize_dispatch(self, submission, binding):
        return False


def _remaining(deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ProtocolError("connection_deadline", "Absolute connection deadline elapsed.")
    return remaining


def _read_exact(connection, size, deadline):
    parts = bytearray()
    while len(parts) < size:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(size - len(parts))
        if not chunk:
            raise ProtocolError("truncated_frame", "EOF before complete frame; do not replay.")
        parts.extend(chunk)
    return bytes(parts)


def read_one(connection, deadline):
    """Require an exact frame AND write EOF before permitting admission."""
    prefix = _read_exact(connection, 4, deadline)
    size = struct.unpack(">I", prefix)[0]
    if not 1 <= size <= MAX_FRAME_BYTES:
        raise ProtocolError("invalid_frame", "Frame length is outside the bound.")
    payload = _read_exact(connection, size, deadline)
    connection.settimeout(_remaining(deadline))
    if connection.recv(1):
        raise ProtocolError("extra_frame", "Exactly one frame and write EOF are required.")
    return decode_payload(payload)


def _send_one(connection, value, deadline):
    frame = encode_frame(value)
    connection.settimeout(_remaining(deadline))
    # sendall's timeout bounds the whole call, not each partial write.
    connection.sendall(frame)


@dataclass(frozen=True)
class _Record:
    submission: object
    refusal: object = None


class LoopbackTransport:
    """Construct on the owner thread; start explicitly, tick explicitly, close.

    Workers touch only framing, policy admission, ledger and queue submit/status.
    owner_tick never acquires a network/ledger lock or does socket/journal I/O.
    Queue contention fails fast as QueueBusy. No lock crosses its executor call.
    Records never evict; exhaustion permanently refuses new IDs. Volatile history
    is NOT crash recovery. Restart requires separate durable reconciliation; this
    object cannot restart, rebind or clear its uncertainty latch.
    """
    def __init__(self, session_id, process_epoch, policy=None, pending_capacity=MAX_PENDING,
                 history_capacity=MAX_HISTORY, max_connections=4, connection_seconds=2.0):
        if type(process_epoch) is not ProcessEpoch:
            raise QueueError("invalid_epoch", "A typed process epoch is required.")
        process_epoch.__post_init__()
        if type(max_connections) is not int or not 1 <= max_connections <= MAX_CONNECTIONS:
            raise QueueError("invalid_capacity", "Connections must be between one and eight.")
        if (type(connection_seconds) not in (int, float)
                or not 0 < connection_seconds <= MAX_CONNECTION_SECONDS):
            raise QueueError("invalid_timeout", "Connection deadline must be positive, at most five seconds.")
        self._queue = CompanionQueue(session_id, pending_capacity, history_capacity)
        self._binding = Binding(session_id, process_epoch)
        self._policy = policy if policy is not None else DenyHostPolicy()
        self._capacity = history_capacity
        self._max_connections = max_connections
        self._connection_seconds = connection_seconds
        self._records = {}
        self._operation_lock = threading.Lock()  # workers only, never owner_tick
        self._network_lock = threading.Lock()    # sockets/worker tracking only
        self._workers = {}
        self._stop = threading.Event()
        self._cleanup_failed = threading.Event()
        self._startup_uncertain = threading.Event()
        self._start_attempted = False
        self._listener = None
        self._accept_thread = None
        self._address = None
        self._owner = threading.current_thread()

    @property
    def binding(self):
        return self._binding

    @property
    def address(self):
        """Explicitly bound endpoint, or None. It carries no authorization."""
        return self._address

    def queue_status(self):
        return self._queue.queue_status()

    def owner_tick(self, execute):
        """One queue item per owner tick; native work only through this callback."""
        if threading.current_thread() is not self._owner:
            raise QueueError("wrong_thread", "Only the captured owner may tick.")
        if not callable(execute):
            raise QueueError("invalid_executor", "An explicit owner executor is required.")
        def admit(request):
            if self._stop.is_set():
                return False
            # Immutable records are published before queue submission and never
            # removed. No network lock is needed to read this entry.
            record = self._records[request.request_id]
            return (self._policy.authorize_dispatch(record.submission, self.binding) is True
                    and not self._stop.is_set())
        def dispatch(request):
            if self._stop.is_set():
                # Started is already published: no exception can imply refusal.
                raise QueueError("transport_shutdown", "Shutdown after start remains uncertain.")
            return execute(request)
        return self._queue.drain(dispatch, admit=admit)

    def start(self, port=0):
        """Open only IPv4 127.0.0.1. No default listener or automatic restart."""
        if threading.current_thread() is not self._owner:
            raise QueueError("wrong_thread", "Only the captured owner may start.")
        if type(port) is not int or not 0 <= port <= 65535:
            raise QueueError("invalid_port", "Expected an explicit valid TCP port or zero.")
        if self._start_attempted or self._stop.is_set():
            raise QueueError("lifecycle_closed", "Transport cannot start twice or after shutdown.")
        self._start_attempted = True
        if self._policy.authorize_listener(self.binding) is not True:
            raise QueueError("host_policy_required", "Independent host/peer authorization is absent.")
        if self._stop.is_set():
            raise QueueError("lifecycle_closed", "Shutdown interrupted listener admission.")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        start_issued, start_returned = False, False
        try:
            if sys.platform == "win32":
                # On Windows another socket using REUSEADDR can otherwise share
                # this port. Exclusive bind is defense in depth, NOT peer auth.
                option = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if option is None:
                    raise QueueError("exclusive_bind_unavailable", "Windows exclusive bind is required.")
                listener.setsockopt(socket.SOL_SOCKET, option, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(self._max_connections)
            listener.settimeout(0.05)
            self._address = listener.getsockname()
            self._listener = listener
            self._accept_thread = threading.Thread(target=self._accept, name="foundry-loopback-accept")
            self._accept_thread.daemon = True
            start_issued = True
            self._accept_thread.start()
            start_returned = True
            if self._stop.is_set():
                self.shutdown()
                raise QueueError("lifecycle_closed", "Shutdown interrupted listener startup.")
        except BaseException:
            self._close_socket(listener)
            self._stop.set()
            # An interrupted start can have issued an OS thread before ident is
            # visible. Keep this uncertainty, even if that thread later exits.
            if start_issued and not start_returned:
                self._startup_uncertain.set()
            elif self._accept_thread is not None and self._accept_thread.ident is None:
                self._accept_thread = None
            raise
        return self._address

    def _close_socket(self, connection):
        try:
            connection.close()
        except OSError:
            # Descriptor closure is uncertain. Do not silently declare clean
            # shutdown or retry ingress; the sticky flag survives later calls.
            self._cleanup_failed.set()
            self._stop.set()

    def _accept(self):
        try:
            while not self._stop.is_set():
                try:
                    connection, peer = self._listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break  # fatal ingress loss, never a listener restart
                deadline = monotonic() + self._connection_seconds
                with self._network_lock:
                    if self._stop.is_set() or len(self._workers) >= self._max_connections:
                        self._close_socket(connection)
                        continue
                    worker, start_issued = None, False
                    try:
                        # Accepted socket ownership is protected through every
                        # construction/daemon/start step, not just Thread.start.
                        worker = threading.Thread(target=self._serve, args=(connection, peer, deadline),
                                                  name="foundry-loopback-peer")
                        worker.daemon = True
                        self._workers[connection] = worker
                        start_issued = True
                        worker.start()
                    except BaseException:
                        if start_issued:
                            # ident=None can mean startup has not published it
                            # yet, not proof no OS thread exists. Retain tracking.
                            self._startup_uncertain.set()
                        else:
                            self._workers.pop(connection, None)
                        self._close_socket(connection)
                        break
        finally:
            # Losing the listener is NOT proof that pending or started operations
            # were cancelled. Latch dispatch off and preserve every receipt.
            self._stop.set()
            try:
                self._queue.close("transport_shutdown")
            except QueueError as error:
                if error.code != "queue_busy":
                    raise
            self._close_socket(self._listener)

    def _serve(self, connection, peer, deadline):
        admitted = False
        try:
            if self._stop.is_set():
                return
            if self._policy.authorize_peer(connection, peer, self.binding, deadline) is not True:
                return
            _remaining(deadline)
            value = read_one(connection, deadline)
            if not self._operation_lock.acquire(timeout=_remaining(deadline)):
                raise ProtocolError("connection_deadline", "Admission lock deadline elapsed.")
            try:
                response, admitted = self._handle(value, deadline)
            finally:
                self._operation_lock.release()
            _send_one(connection, response, deadline)
        except (ProtocolError, QueueError) as error:
            if admitted:
                self._queue.block_mutations("response_lost")
            try:
                _send_one(connection, {"schema": 2, "kind": "error", "code": error.code,
                                       "outcome_known": False}, deadline)
            except (OSError, ProtocolError):
                pass
        except Exception:
            if admitted:
                self._queue.block_mutations("response_lost")
            # No exception text/peer data is exposed or logged. A dropped response
            # is uncertain to the client; only receipt_read may follow it.
        finally:
            try:
                self._close_socket(connection)
            finally:
                with self._network_lock:
                    self._workers.pop(connection, None)

    def _handle(self, value, deadline):
        if self._stop.is_set():
            raise ProtocolError("transport_shutdown", "Transport is permanently closed.")
        session, epoch = self.binding.session_id, self.binding.process_epoch
        if value.get("kind") == "receipt_read":
            request_id, digest = parse_receipt_read(value, session, epoch)
            record = self._records.get(request_id)
            if record is None:
                self._queue.block_mutations("history_missing")
                return receipt(request_id, digest, session, epoch), False
            if record.submission.request_sha256 != digest:
                self._queue.block_mutations("request_conflict")
                raise ProtocolError("request_conflict", "Receipt does not match retained intent.")
            snapshot = record.refusal or self._queue.status(request_id)
            return receipt(request_id, digest, session, epoch, snapshot), snapshot.state != "refused"
        submission = parse_submit(value, session, epoch)
        request, digest = submission.request, submission.request_sha256
        request_id = request.request_id
        record = self._records.get(request_id)
        if record is not None:
            if record.submission.canonical_payload != submission.canonical_payload:
                self._queue.block_mutations("request_conflict")
                raise ProtocolError("request_conflict", "ID already binds different immutable intent.")
            snapshot = record.refusal or self._queue.status(request_id)
            return receipt(request_id, digest, session, epoch, snapshot), snapshot.state != "refused"
        if len(self._records) == self._capacity:
            snapshot = Snapshot(request_id, session, "refused", "history_full")
            return receipt(request_id, digest, session, epoch, snapshot), False
        # New, authenticated submissions alone may create an entry. Hooks must
        # perform bounded non-native work and durably record authorized intent.
        status = self._queue.queue_status()
        reason = status.closed_reason
        if reason is None and request.operation != "observe":
            reason = status.mutation_blocked_reason
        try:
            if reason is None and self._policy.authorize_submit(submission, self.binding, deadline) is not True:
                reason = "host_admission_denied"
            _remaining(deadline)
            if self._stop.is_set():
                reason = "transport_shutdown"
        except Exception:
            reason = reason or "host_admission_denied"
        if reason is not None:
            snapshot = Snapshot(request_id, session, "refused", reason)
            self._records[request_id] = _Record(submission, snapshot)
            return receipt(request_id, digest, session, epoch, snapshot), False
        self._records[request_id] = _Record(submission)
        try:
            snapshot = self._queue.submit(request)
        except BaseException:
            # Admission unexpectedly failed. Preserve a permanent known refusal
            # only if no queue entry exists; never guess that dispatch did not run.
            snapshot = self._queue.status(request_id)
            if snapshot is None:
                snapshot = Snapshot(request_id, session, "refused", "queue_admission_failed")
                self._records[request_id] = _Record(submission, snapshot)
            self._queue.block_mutations("admission_uncertain")
            raise
        return receipt(request_id, digest, session, epoch, snapshot), snapshot.state != "refused"

    def shutdown(self, timeout=2.0):
        """Close sockets and latch dispatch off. Never discard or reset receipts.

        Returns whether all workers stopped and socket cleanup succeeded within
        the deadline. False exposes a startup race, host hook or cleanup failure,
        never permission to abandon or restart history.
        Started native calls are not preemptible and remain subject to read-only
        reconciliation. Call lifecycle methods outside performance-critical ticks.
        """
        if type(timeout) not in (int, float) or not 0 <= timeout <= MAX_CONNECTION_SECONDS:
            raise QueueError("invalid_timeout", "Shutdown wait must be between zero and five seconds.")
        self._stop.set()
        # Owner never waits on the queue lock. If contended, the stop event still
        # gates every dispatch; a later shutdown call can finish the queue latch.
        try:
            self._queue.close("transport_shutdown")
        except QueueError as error:
            if error.code != "queue_busy":
                raise
        if self._listener is not None:
            self._close_socket(self._listener)
        with self._network_lock:
            workers = list(self._workers.items())
        for connection, worker in workers:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._close_socket(connection)
        deadline = monotonic() + timeout
        threads = [worker for connection, worker in workers]
        if self._accept_thread is not None:
            threads.append(self._accept_thread)
        unstarted = False
        for worker in threads:
            if worker.ident is None:
                unstarted = True  # startup race is not proof of stopped work
            elif worker is not threading.current_thread():
                worker.join(max(0, deadline - monotonic()))
        return (not unstarted and not self._startup_uncertain.is_set()
                and not self._cleanup_failed.is_set()
                and not any(worker.is_alive() for worker in threads))
