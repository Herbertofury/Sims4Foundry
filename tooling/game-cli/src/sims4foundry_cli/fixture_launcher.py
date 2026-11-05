"""Foreground fixture-only owner and durable one-shot client handshake.

This is NOT a live game service. The owner uses only synthetic process and
input providers. A supervisor hands the same fresh 32-byte secret to the owner
and each client via inherited private stdin; secrets are never CLI arguments or
written to records. The original owner is never reconstructed after loss.
Cooperative records and same-logon pipes are not authenticated authority.
"""
from copy import deepcopy
import os
import queue
import secrets
import subprocess
import sys
import threading
import uuid

from . import independent_owner, independent_session, input_journal, process_exit
from . import retained_process_witness, session_witness
from .errors import FoundryError
from .io import canonical, digest, external_path, loads, new_json

MAX_FRAME = independent_owner.MAX_FRAME
FIXTURE_IDENTITY = {'schema': 1, 'kind': 'foundry_windows_identity', 'pid': 123,
    'creation_filetime': 456, 'hwnd': 4, 'thread_id': 5, 'dpi': 96, 'integrity': 8192,
    'client_width': 1280, 'client_height': 720, 'executable': r'C:\Fixture\TS4_x64.exe',
    'executable_sha256': 'a' * 64, 'window_class': 'Canvas-fixture', 'client_origin': [0, 0]}
_UNRESOLVED_CHILDREN = []
_UNRESOLVED_OWNERS = []


class _SyntheticProcessApi:
    def __init__(self, identity): self._identity = deepcopy(identity)
    def open(self, pid): return 'synthetic-only-handle'
    def identity(self, handle):
        return self._identity['executable'], self._identity['creation_filetime']
    def wait(self, handle, timeout): return process_exit.WAIT_TIMEOUT
    def close(self, handle): pass


def _status_allows_continuation(operation, result):
    # Only a complete observed registry status with an explicit non-pending
    # state may leave the fixture open for a new request. A host or journal
    # observation alone does not qualify or acknowledge an input.
    return (operation == 'status' and type(result) is dict and result.get('outcome') == 'observed' and
            result.get('registry_pending') is False and result.get('receipt_replayed') is False)


class FixtureBroker:
    """Bind operation IDs to durable intents before fixture-only dispatch."""
    def __init__(self, owner, directory, session_id, owner_id, *, allow_stop=True):
        if (type(owner) is not independent_owner.FixtureOwner or
                not input_journal._token(session_id) or not input_journal._token(owner_id) or type(allow_stop) is not bool):
            raise FoundryError('fixture_binding', 'Original fixture owner and IDs required.')
        self.owner, self.directory = owner, directory
        self.session_id, self.owner_id = session_id, owner_id
        self.allow_stop = allow_stop
        self.failed = False
        self.fenced = False

    def handle(self, request):
        if (self.failed or type(request) is not dict or
                set(request) != {'schema', 'session_id', 'owner_id', 'operation_id', 'op', 'payload'} or
                type(request['schema']) is not int or request['schema'] != 1 or
                request['session_id'] != self.session_id or request['owner_id'] != self.owner_id or
                not input_journal._token(request['operation_id']) or
                request['op'] not in ('status', 'input_status', 'fixture_click', 'stop') or
                type(request['payload']) is not dict or len(canonical(request)) > MAX_FRAME):
            raise FoundryError('fixture_binding', 'Exact fixture-owner request binding required.')
        if request['op'] == 'stop' and not self.allow_stop:
            raise FoundryError('fixture_binding', 'Interactive owner retains its original witness until abandonment.')
        op_id = request['operation_id']
        intent_path = self.directory / (op_id + '.intent.json')
        receipt_path = self.directory / (op_id + '.receipt.json')
        intent = independent_session._record('foundry_fixture_request_intent',
                                             session_id=self.session_id, owner_id=self.owner_id,
                                             operation_id=op_id, request_sha256=digest(request))
        def existing_receipt():
            prior = independent_session._checked(intent_path, 'foundry_fixture_request_intent',
                                                  ('session_id', 'owner_id', 'operation_id', 'request_sha256'))
            if canonical(prior) != canonical(intent):
                raise FoundryError('fixture_request_conflict', 'Operation ID was bound to another request.')
            try:
                receipt = independent_session._checked(receipt_path, 'foundry_fixture_request_receipt',
                                                        ('session_id', 'owner_id', 'operation_id', 'intent_sha256', 'result'))
            except FileNotFoundError:
                raise FoundryError('fixture_request_unresolved', 'No original receipt; never dispatch again.') from None
            if (receipt['session_id'] != self.session_id or receipt['owner_id'] != self.owner_id or
                    receipt['operation_id'] != op_id or receipt['intent_sha256'] != digest(intent)):
                raise FoundryError('fixture_request_unresolved', 'Receipt does not bind the original intent.')
            if type(receipt['result']) is not dict:
                raise FoundryError('fixture_request_unresolved', 'Original receipt result is unavailable.')
            # This is a point-in-time receipt, not a new registry observation.
            self.fenced = True
            return dict(deepcopy(receipt['result']), receipt_replayed=True)
        if self.fenced:
            try: return existing_receipt()
            except FileNotFoundError:
                raise FoundryError('fixture_request_unresolved', 'Owner is fenced; no new dispatch is allowed.') from None
        try:
            new_json(intent_path, intent)
        except FileExistsError:
            # Never dispatch again, even when no receipt or record is damaged.
            return existing_receipt()
        except BaseException:
            self.failed = True
            raise
        try:
            result = self.owner.handle(request)
            if type(result) is dict:
                result = dict(result, receipt_replayed=False)
            if not _status_allows_continuation(request['op'], result):
                self.fenced = True
            receipt = independent_session._record('foundry_fixture_request_receipt', session_id=self.session_id,
                         owner_id=self.owner_id, operation_id=op_id, intent_sha256=digest(intent), result=result)
            new_json(receipt_path, receipt)
            return result
        except BaseException:
            self.failed = True
            raise


def _read_secret(stream):
    secret = stream.read(32)
    if type(secret) is not bytes or len(secret) != 32:
        raise FoundryError('fixture_secret', 'An inherited private stream must supply exactly 32 secret bytes.')
    return secret


def _fixture_owner(authority, session, *, allow_stop=True):
    """Acquire a synthetic witness exactly once for this original owner."""
    intent, registry, journal, state = independent_session._open(authority, session)
    if canonical(intent['identity']) != canonical(FIXTURE_IDENTITY):
        raise FoundryError('fixture_identity_required', 'The exact built-in synthetic identity is required.')
    if (type(state) is not dict or type(state.get('events')) is not list or len(state['events']) != 1 or
            type(state['events'][0]) is not dict or state['events'][0].get('kind') != 'session'):
        raise FoundryError('fixture_owner_unresolved', 'Only an unused registry may acquire its first fixture owner.')
    directory = external_path(session) / 'fixture-owner'
    try: directory.mkdir(exist_ok=False)
    except FileExistsError:
        raise FoundryError('fixture_owner_unresolved', 'An owner was already attempted; never reacquire.') from None
    owner_id = uuid.uuid4().hex
    new_json(directory / 'owner-intent.json', independent_session._record(
        'foundry_fixture_owner_intent', session_id=intent['session_id'], owner_id=owner_id,
        lifetime_sha256=intent['lifetime_sha256']))
    operations = directory / 'operations'
    operations.mkdir(exist_ok=False)
    identity = intent['identity']
    api = _SyntheticProcessApi(identity)
    provider = retained_process_witness.RetainedProcessRegistry(api, lambda _: True)
    process = {key: identity[key] for key in ('pid', 'creation_filetime', 'executable', 'executable_sha256')}
    witness = session_witness.SessionWitness(registry, provider, process, uuid.uuid4().hex)
    # The provider may acquire a handle even if acknowledgment is interrupted.
    # Retain the original objects before crossing that boundary.
    hold = {'registry': registry, 'provider': provider, 'witness': witness, 'lease': None}
    _UNRESOLVED_OWNERS.append(hold)
    lease = witness.acquire()
    hold['lease'] = lease
    owner = independent_owner.FixtureOwner(registry, journal, witness, lease, owner_id)
    return FixtureBroker(owner, operations, intent['session_id'], owner_id, allow_stop=allow_stop)


def serve(authority, session, *, input_stream=None, output_stream=None, count=1, pipe_factory=None):
    """Foreground fixture service; never reconnect/recreate after an error."""
    if type(count) is not int or not 1 <= count <= 128:
        raise FoundryError('fixture_arguments', 'Fixture connection count must be between 1 and 128.')
    secret = _read_secret(input_stream if input_stream is not None else sys.stdin.buffer)
    output_stream = output_stream if output_stream is not None else sys.stdout
    broker = _fixture_owner(authority, session)
    if pipe_factory is None:
        from .fixture_named_pipe import FixturePipe
        pipe_factory = FixturePipe
    # Keep the original synthetic witness reachable on every exit path. This
    # cannot retain it after the enclosing owner process itself terminates.
    _UNRESOLVED_OWNERS.append(broker)
    pipe = pipe_factory(broker, secret)
    try:
        output_stream.write(canonical({'fixture_endpoint': pipe.name, 'session_id': broker.session_id,
                                      'owner_id': broker.owner_id, 'game_qualified': False,
                                      'live_dispatch_available': False}).decode('ascii') + '\n')
        output_stream.flush()
        for _ in range(count): pipe.serve_once()
    finally:
        pipe.close()
    return {'ok': False, 'outcome': 'unresolved', 'reason': 'bounded_fixture_owner_ending',
            'game_qualified': False, 'live_dispatch_available': False,
            'owner_exit_authorized': False, 'original_witness_will_be_lost_on_process_exit': True}


def client(endpoint, request, *, input_stream=None, sender=None):
    """Single attempt only; caller retains original operation ID on uncertainty."""
    secret = _read_secret(input_stream if input_stream is not None else sys.stdin.buffer)
    if sender is None:
        from .fixture_named_pipe import fixture_client
        sender = fixture_client
    result = sender(endpoint, secret, request)
    return {'ok': True, 'outcome': 'fixture_response_received', 'response': result,
            'semantic_success_established': False, 'game_qualified': False,
            'live_dispatch_available': False}


def inspect_receipt(authority, session, owner_id, operation_id):
    """Inspect existing fixture records; never acknowledge, send or reacquire."""
    if not input_journal._token(owner_id) or not input_journal._token(operation_id):
        raise FoundryError('fixture_binding', 'Original owner and operation IDs are required.')
    try:
        intent, _, _, _ = independent_session._open(authority, session)
        if canonical(intent['identity']) != canonical(FIXTURE_IDENTITY):
            raise FoundryError('fixture_identity_required', 'Exact synthetic identity required.')
        root = external_path(session) / 'fixture-owner'
        owner = independent_session._checked(root / 'owner-intent.json', 'foundry_fixture_owner_intent',
                                              ('session_id', 'owner_id', 'lifetime_sha256'))
        if (owner['session_id'] != intent['session_id'] or owner['owner_id'] != owner_id or
                owner['lifetime_sha256'] != intent['lifetime_sha256']):
            raise FoundryError('fixture_binding', 'Owner does not bind the original session.')
        operations = root / 'operations'
        attempted = independent_session._checked(operations / (operation_id + '.intent.json'),
                    'foundry_fixture_request_intent', ('session_id', 'owner_id', 'operation_id', 'request_sha256'))
        receipt = independent_session._checked(operations / (operation_id + '.receipt.json'),
                    'foundry_fixture_request_receipt', ('session_id', 'owner_id', 'operation_id', 'intent_sha256', 'result'))
    except FileNotFoundError:
        return {'ok': False, 'outcome': 'unresolved', 'reason': 'original_fixture_record_unavailable',
                'replayed': False, 'game_qualified': False}
    if (attempted['session_id'] != intent['session_id'] or attempted['owner_id'] != owner_id or
            attempted['operation_id'] != operation_id or receipt['session_id'] != intent['session_id'] or
            receipt['owner_id'] != owner_id or receipt['operation_id'] != operation_id or
            receipt['intent_sha256'] != digest(attempted)):
        raise FoundryError('fixture_binding', 'Receipt does not match the original operation.')
    return {'ok': True, 'outcome': 'original_fixture_receipt_observed', 'response': deepcopy(receipt['result']),
            'owner_liveness_proved': False, 'observer_authenticated': False, 'replayed': False,
            'game_qualified': False, 'live_dispatch_available': False}


def _owner_banner(child, timeout=8.0):
    result = queue.Queue(maxsize=1)
    def read_once():
        try: result.put((True, child.stdout.readline(MAX_FRAME + 1)))
        except BaseException as exc: result.put((False, exc))
    worker = threading.Thread(target=read_once, daemon=True)
    worker.start()
    try: ok, raw = result.get(timeout=timeout)
    except queue.Empty:
        raise FoundryError('fixture_owner_unresolved', 'Owner banner was not observed; do not relaunch.') from None
    if not ok or not raw or len(raw) > MAX_FRAME or not raw.endswith(b'\n'):
        raise FoundryError('fixture_owner_unresolved', 'Owner banner is unavailable or malformed; do not relaunch.')
    banner = loads(raw)
    if (type(banner) is not dict or set(banner) != {'fixture_endpoint', 'session_id', 'owner_id',
                                                     'game_qualified', 'live_dispatch_available'} or
            type(banner['fixture_endpoint']) is not str or
            not banner['fixture_endpoint'].startswith(r'\\.\pipe\foundry-fixture-') or
            not input_journal._token(banner['session_id']) or not input_journal._token(banner['owner_id']) or
            banner['game_qualified'] is not False or banner['live_dispatch_available'] is not False):
        raise FoundryError('fixture_owner_unresolved', 'Owner banner binding is invalid; do not relaunch.')
    return banner


class _BoundedReader:
    """Read at most one bounded stream; unfinished reads remain unresolved."""
    def __init__(self, stream):
        self._result = queue.Queue(maxsize=1)
        def read_once():
            try: self._result.put((True, stream.read(MAX_FRAME + 1)))
            except BaseException as exc: self._result.put((False, exc))
        self._worker = threading.Thread(target=read_once, daemon=True)
        self._worker.start()

    def finish(self):
        try: ok, data = self._result.get(timeout=1)
        except queue.Empty:
            raise FoundryError('fixture_output_unresolved', 'Owned output reader did not finish.') from None
        if not ok or type(data) is not bytes or len(data) > MAX_FRAME:
            raise FoundryError('fixture_output_unresolved', 'Owned output failed or exceeded its bound.')
        return data


def launch(authority, session, requests, *, popen=None, banner_reader=None):
    """Supervise one fixture owner and its clients; never reconnect or retry.

    Children are the explicitly spawned Python fixture processes only. Unknown
    acquisitions/cleanup are retained in memory and reported unresolved; they
    are not stopped, queried via another route, or declared absent.
    """
    if (type(requests) is not list or not 1 <= len(requests) <= 128 or
            len(canonical(requests)) > MAX_FRAME):
        raise FoundryError('fixture_arguments', 'A bounded nonempty fixture request list is required.')
    seen = set()
    for item in requests:
        if (type(item) is not dict or set(item) != {'operation_id', 'op', 'payload'} or
                not input_journal._token(item['operation_id']) or item['operation_id'] in seen or
                item['op'] not in ('status', 'input_status', 'fixture_click', 'stop') or
                type(item['payload']) is not dict):
            raise FoundryError('fixture_arguments', 'Each fixture request needs a unique operation ID and exact fields.')
        seen.add(item['operation_id'])
    popen = subprocess.Popen if popen is None else popen
    banner_reader = _owner_banner if banner_reader is None else banner_reader
    source_root = os.path.dirname(os.path.dirname(__file__))
    code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from sims4foundry_cli.cli import main;raise SystemExit(main())'
    prefix = [sys.executable, '-B', '-c', code, source_root]
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    secret = secrets.token_bytes(32)
    owner = None
    current = None
    client_acquisition_pending = False
    owner_finished = False
    banner = None
    operation_id = None
    try:
        owner = popen(prefix + ['fixture-owner', 'serve', '--authority', str(authority), '--session', str(session),
                                '--count', str(len(requests))], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, creationflags=flags)
        owner_error = _BoundedReader(owner.stderr)
        if owner.stdin.write(secret) != len(secret):
            raise FoundryError('fixture_owner_unresolved', 'Owner secret transfer is incomplete; do not relaunch.')
        owner.stdin.close(); owner.stdin = None
        banner = banner_reader(owner)
        owner_output = _BoundedReader(owner.stdout)
        responses = []
        for item in requests:
            operation_id = item['operation_id']
            request = {'schema': 1, 'session_id': banner['session_id'], 'owner_id': banner['owner_id'],
                       **item}
            client_acquisition_pending = True
            current = popen(prefix + ['fixture-owner', 'client', '--endpoint', banner['fixture_endpoint'],
                                      '--request', canonical(request).decode('ascii')], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
            client_acquisition_pending = False
            client_output = _BoundedReader(current.stdout)
            client_error = _BoundedReader(current.stderr)
            if current.stdin.write(secret) != len(secret):
                raise FoundryError('fixture_client_unresolved', 'Client secret transfer is incomplete; do not replay.')
            current.stdin.close(); current.stdin = None
            current.wait(timeout=8)
            output = client_output.finish()
            client_error.finish()
            if current.returncode != 0:
                raise FoundryError('fixture_client_unresolved', 'Client response was not confirmed; do not replay.')
            response = loads(output)
            observed = response.get('response') if type(response) is dict else None
            if (type(response) is not dict or response.get('outcome') != 'fixture_response_received' or
                    type(observed) is not dict or observed.get('operation_id') != item['operation_id'] or
                    observed.get('session_id') != banner['session_id'] or observed.get('owner_id') != banner['owner_id']):
                raise FoundryError('fixture_client_unresolved', 'Client response binding is unknown; do not replay.')
            responses.append(response)
            current = None
        owner.wait(timeout=8)
        output = owner_output.finish()
        owner_error.finish()
        if owner.returncode != 3:
            raise FoundryError('fixture_owner_unresolved', 'Owner exit is not confirmed; do not relaunch.')
        final = loads(output)
        if (type(final) is not dict or final.get('outcome') != 'unresolved' or
                final.get('reason') != 'bounded_fixture_owner_ending'):
            raise FoundryError('fixture_owner_unresolved', 'Owner final outcome is unknown; do not relaunch.')
        owner_finished = True
        return {'ok': False, 'outcome': 'unresolved', 'reason': 'fixture_owner_process_ended', 'responses': responses,
                'game_qualified': False, 'live_dispatch_available': False,
                'same_user_authenticated': False, 'owner_exit_is_game_proof': False,
                'original_fixture_witness_lost': True, 'do_not_relaunch_or_replay': True}
    except BaseException as exc:
        if current is not None: _UNRESOLVED_CHILDREN.append(('client', current))
        elif client_acquisition_pending: _UNRESOLVED_CHILDREN.append(('client_acquisition_unknown', None))
        if not owner_finished: _UNRESOLVED_CHILDREN.append(('owner', owner))
        if not isinstance(exc, Exception) or isinstance(exc, PermissionError):
            raise
        raise FoundryError('fixture_supervision_unresolved',
            'Fixture owner or client outcome is unknown; inspect original records and do not replay.',
            outcome='unresolved', session_id=banner.get('session_id') if banner else None,
            owner_id=banner.get('owner_id') if banner else None, operation_id=operation_id,
            replayed=False, game_qualified=False) from exc
