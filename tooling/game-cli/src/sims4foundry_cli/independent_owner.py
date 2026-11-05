"""Fixture-only retained owner and bounded connected-socket protocol.

No listener, service launcher, native sender, observer or live admission exists
here. The only input backend records fake packets in memory. The caller owns the
already connected socket and original owner thread. Peer authentication,
endpoint exclusivity and provider/session authority are not implemented.
"""
from copy import deepcopy
import math
import socket
import struct
import threading
import time

from . import input_journal, registered_input, session_registry, session_witness
from .errors import FoundryError
from .input_packets import ClientBounds, Point, encode_click
from .io import canonical, digest, loads, new_json

MAX_FRAME = 16384
MAX_OPS = 128
MAX_SECONDS = 5.0


class _FixtureBackend:
    """An intentionally inert backend: post only appends in-memory records."""
    def __init__(self, target):
        self._target = deepcopy(target)
        self.posts = []
        self._time = 0.0
    def observe(self): return deepcopy(self._target)
    def observe_owned(self): return deepcopy(self._target)
    def integrities(self): return (self._target['integrity'], self._target['integrity'])
    def foreground(self): return 1
    def verify_recording(self): return True
    def verify_input_context(self, packet): return True
    def monotonic(self): return self._time
    def sleep(self, seconds): self._time += seconds
    def post(self, hwnd, packet):
        self.posts.append((hwnd, packet))
        return True


def _depth_ok(raw):
    depth = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            if escaped: escaped = False
            elif char == 92: escaped = True
            elif char == 34: quoted = False
        elif char == 34: quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > 12: return False
        elif char in (93, 125):
            depth -= 1
            if depth < 0: return False
    return not quoted and depth == 0


class FixtureOwner:
    """Retain one original witness in one process/thread; no live dispatch.

    The fixture backend is constructed here and cannot be replaced by a caller.
    An exit/stop is never automatically acknowledged as safe. Losing this owner
    loses its in-memory handle; durable registry/journal records stay unresolved.
    This class is not a trust boundary against arbitrary code in one interpreter.
    """
    def __init__(self, registry, journal, witness, lease, owner_id):
        if (type(registry) is not session_registry.SessionRegistry or
                type(journal) is not input_journal.InputJournal or
                type(witness) is not session_witness.SessionWitness or witness._registry is not registry or
                not input_journal._token(owner_id) or
                getattr(lease, '_registry', None) is not witness._provider or
                getattr(lease, '_token', None) != witness._request):
            raise FoundryError('owner_binding', 'The original registry, journal and retained witness are required.')
        registered_input._check(registry, journal)
        self._registry, self._journal, self._witness, self._lease = registry, journal, witness, lease
        self._owner_id = owner_id
        self._session_id = journal.session['session_id']
        self._thread = threading.current_thread()
        self._backend = _FixtureBackend(journal.session['target'])
        self._seen = {}
        self._stopping = False

    def _owner(self):
        if threading.current_thread() is not self._thread:
            raise FoundryError('owner_changed', 'The original owner thread is required.')

    def _reply(self, op_id, outcome, **fields):
        return {'schema': 1, 'session_id': self._session_id, 'owner_id': self._owner_id,
                'operation_id': op_id, 'outcome': outcome, 'game_qualified': False,
                'observer_authenticated': False, 'peer_authenticated': False, **fields}

    def handle(self, request):
        """Return receipt/status only; repeated mutation IDs are never resent."""
        self._owner()
        if (type(request) is not dict or set(request) != {'schema', 'session_id', 'owner_id', 'operation_id', 'op', 'payload'} or
                type(request['schema']) is not int or request['schema'] != 1 or
                request['session_id'] != self._session_id or request['owner_id'] != self._owner_id or
                not input_journal._token(request['operation_id']) or type(request['op']) is not str or
                type(request['payload']) is not dict or len(canonical(request)) > MAX_FRAME):
            raise FoundryError('owner_request_invalid', 'Exact bounded owner/session request is required.')
        op_id, operation = request['operation_id'], request['op']
        if operation not in ('status', 'input_status', 'fixture_click', 'stop'):
            raise FoundryError('owner_request_invalid', 'Unsupported owner operation.')
        fingerprint = digest(request)
        if op_id in self._seen:
            previous, response = self._seen[op_id]
            if previous != fingerprint:
                raise FoundryError('owner_request_conflict', 'Operation ID belongs to another original request.')
            return deepcopy(response)
        if len(self._seen) >= MAX_OPS:
            raise FoundryError('owner_history_full', 'Owner receipt history is full; no new operation admitted.')
        if operation == 'status':
            if request['payload']:
                raise FoundryError('owner_request_invalid', 'Status takes no payload.')
            state = self._registry.inspect()
            response = self._reply(op_id, 'observed', registry_pending=bool(state['events'] and state['events'][-1]['kind'] == 'attempt'),
                                   owner_running=True, input_backend='fixture_only', witness_restart_recovery=False)
        elif operation == 'input_status':
            value = request['payload']
            if set(value) != {'request_id'} or not input_journal._token(value['request_id']):
                raise FoundryError('owner_request_invalid', 'Original input request ID required.')
            row = self._journal.status(value['request_id'])
            response = self._reply(op_id, 'observed', input=row, host_ack_is_game_proof=False)
        elif operation == 'fixture_click':
            value = request['payload']
            if (self._stopping or set(value) != {'x', 'y', 'expectation'} or
                    type(value['x']) is not int or type(value['y']) is not int):
                raise FoundryError('owner_request_invalid', 'Exact fixture click and expectation are required.')
            target = self._journal.session['target']
            packets = encode_click(Point(value['x'], value['y']), ClientBounds(target['client_width'], target['client_height']))
            expectation = input_journal._expectation(value['expectation'])
            # Latch before entering the one-shot durable registry/journal path.
            self._seen[op_id] = (fingerprint, self._reply(op_id, 'unresolved', input_attempted=None))
            try:
                result = registered_input.execute_once(self._registry, self._journal, op_id, packets, self._backend,
                                                       expected_observation=expectation, witness=self._witness)
                response = self._reply(op_id, result['state'], input=result,
                                       input_backend='fixture_only', semantic_success=False)
            except PermissionError:
                raise
            except Exception as error:
                response = self._reply(op_id, 'unresolved', error_type=type(error).__name__, input_attempted=None)
        else:
            if request['payload'] != {'abandon_original_witness': True} or self._stopping:
                raise FoundryError('owner_request_invalid', 'Explicit original-witness abandonment is required.')
            with self._witness.guard_for_dispatch(self._registry) as check_live:
                check_live()
                # Cooperative registry preserves a pending stop if persistence,
                # release or acknowledgment becomes uncertain; never resolve it.
                self._registry.begin(op_id, 'exit', fingerprint)
            self._stopping = True
            self._seen[op_id] = (fingerprint, self._reply(op_id, 'unresolved', release_attempted=None))
            try:
                receipt = self._lease.close()
                response = self._reply(op_id, 'unresolved', provider_release=receipt,
                                       os_release_verified=False, owner_exit_authorized=False)
            except PermissionError:
                raise
            except Exception as error:
                response = self._reply(op_id, 'unresolved', error_type=type(error).__name__,
                                       os_release_verified=False, owner_exit_authorized=False)
        self._seen[op_id] = (fingerprint, deepcopy(response))
        return deepcopy(response)

    def serve_one(self, connection, seconds=2.0):
        """Serve one already-connected fixture socket with one absolute deadline."""
        self._owner()
        if (type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS or
                not isinstance(connection, socket.socket)):
            raise FoundryError('owner_transport_invalid', 'Explicit connected fixture socket and bounded timeout required.')
        local, peer = connection.getsockname(), connection.getpeername()
        if (connection.family != socket.AF_INET or connection.type != socket.SOCK_STREAM or
                type(local) is not tuple or type(peer) is not tuple or
                local[0] != '127.0.0.1' or peer[0] != '127.0.0.1'):
            raise FoundryError('owner_transport_invalid', 'Only an explicitly supplied IPv4 loopback fixture socket is accepted.')
        deadline = time.monotonic() + seconds
        def read_exact(size):
            chunks = bytearray()
            while len(chunks) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FoundryError('owner_transport_unresolved', 'Fixture transport deadline expired.')
                connection.settimeout(remaining)
                data = connection.recv(size - len(chunks))
                if not data:
                    raise FoundryError('owner_transport_invalid', 'Incomplete fixture frame.')
                chunks.extend(data)
            return bytes(chunks)
        size = struct.unpack('>I', read_exact(4))[0]
        if not 1 <= size <= MAX_FRAME:
            raise FoundryError('owner_transport_invalid', 'Fixture frame exceeds its bound.')
        raw = read_exact(size)
        if not _depth_ok(raw):
            raise FoundryError('owner_transport_invalid', 'Fixture JSON depth or framing is invalid.')
        # A writer must half-close before dispatch; reject extra bytes/frames.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FoundryError('owner_transport_unresolved', 'Fixture transport deadline expired.')
        connection.settimeout(remaining)
        if connection.recv(1):
            raise FoundryError('owner_transport_invalid', 'Additional fixture frames are refused.')
        response = self.handle(loads(raw))
        encoded = canonical(response)
        if len(encoded) > MAX_FRAME:
            raise FoundryError('owner_transport_unresolved', 'Fixture response exceeds its bound; never replay.')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FoundryError('owner_transport_unresolved', 'Fixture result missed its transport deadline; inspect original ID.')
        connection.settimeout(remaining)
        connection.sendall(struct.pack('>I', len(encoded)) + encoded)
        return response
