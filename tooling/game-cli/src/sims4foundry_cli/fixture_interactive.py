"""Persistent synthetic owner under an interactive, one-shot supervisor.

No real game sender, observation, reconnect or game-profile access exists.
Control EOF or explicit abandonment ends the synthetic owner unresolved.
"""
import os
import secrets
import subprocess
import sys
import threading

from . import fixture_launcher as base, input_journal
from .errors import FoundryError
from .io import canonical, loads


def _report_interruption(output):
    try:
        output.write(canonical({'outcome': 'unresolved', 'reason': 'fixture_owner_interrupted',
                                'owner_witness_may_be_lost': True, 'replayed': False}).decode('ascii') + '\n')
        output.flush()
    except BaseException:
        pass  # best effort only; never hide the original interruption


class _Control:
    def __init__(self, stream):
        self._done = threading.Event()
        self.value = None
        def read_once():
            try: self.value = stream.read(1)
            except BaseException: self.value = None
            finally: self._done.set()
        threading.Thread(target=read_once, daemon=True).start()
    def done(self): return self._done.is_set()
    def wait(self, seconds): return self._done.wait(seconds)


def serve(authority, session, *, input_stream=None, output_stream=None, pipe_factory=None, control_factory=None):
    stream = input_stream if input_stream is not None else sys.stdin.buffer
    output = output_stream if output_stream is not None else sys.stdout
    secret = base._read_secret(stream)
    control = (control_factory or _Control)(stream)
    broker = None
    pipe = None
    fault = None
    try:
        broker = base._fixture_owner(authority, session, allow_stop=False)
        base._UNRESOLVED_OWNERS.append(broker)
        if pipe_factory is None:
            from .fixture_named_pipe import FixturePipe
            pipe_factory = FixturePipe
        pipe = pipe_factory(broker, secret)
        output.write(canonical({'fixture_endpoint': pipe.name, 'session_id': broker.session_id,
                                'owner_id': broker.owner_id, 'game_qualified': False,
                                'live_dispatch_available': False}).decode('ascii') + '\n')
        output.flush()
    except BaseException as exc:
        if not isinstance(exc, Exception):
            _report_interruption(output)
            raise
        # A denied operation is not repeated or rerouted; retain the synthetic
        # owner in-process while awaiting the original control channel.
        fault = 'fixture_owner_permission_denied' if isinstance(exc, PermissionError) else 'fixture_owner_setup_unresolved'
        output.write(canonical({'outcome': 'unresolved', 'reason': fault}).decode('ascii') + '\n')
        output.flush()
    served = 0
    while not control.done():
        if fault or served >= 128:
            control.wait(0.1)  # keep original witness; never rearm after uncertainty
            continue
        try:
            outcome = pipe.serve_once()
            if outcome != {'fixture_idle': True}: served += 1
        except BaseException as exc:
            if not isinstance(exc, Exception):
                _report_interruption(output)
                raise
            fault = 'fixture_transport_permission_denied' if isinstance(exc, PermissionError) else 'fixture_transport_unresolved'
            output.write(canonical({'outcome': 'unresolved', 'reason': fault}).decode('ascii') + '\n')
            output.flush()
    if pipe is not None:
        try: pipe.close()
        except BaseException:
            # Original pipe code retains its own uncertain state. Do not retry.
            fault = 'fixture_pipe_close_unresolved'
    return {'ok': False, 'outcome': 'unresolved', 'reason': fault or 'fixture_owner_abandoned',
            'control_was_explicit_abandon': control.value == b'Q', 'game_qualified': False,
            'live_dispatch_available': False, 'original_witness_will_be_lost_on_process_exit': True}


def launch(authority, session, *, input_stream=None, output_stream=None, popen=None, banner_reader=None):
    """Run one foreground supervisor until explicit abandon or input loss."""
    requests = input_stream if input_stream is not None else sys.stdin.buffer
    output = output_stream if output_stream is not None else sys.stdout
    popen = popen or subprocess.Popen
    banner_reader = banner_reader or base._owner_banner
    secret = secrets.token_bytes(32)
    source = os.path.dirname(os.path.dirname(__file__))
    code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from sims4foundry_cli.cli import main;raise SystemExit(main())'
    prefix = [sys.executable, '-B', '-c', code, source]
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    owner, client, banner = None, None, None
    owner_finished = False
    acquiring_client = False
    fenced = False
    sent = 0
    try:
        owner = popen(prefix + ['fixture-owner', 'serve-persistent', '--authority', str(authority), '--session', str(session)],
                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        owner_error = base._BoundedReader(owner.stderr)
        if owner.stdin.write(secret) != len(secret): raise FoundryError('fixture_secret', 'Owner transfer is uncertain.')
        owner.stdin.flush()  # keep this inherited control stream open while owner runs
        banner = banner_reader(owner)
        owner_output = base._BoundedReader(owner.stdout)
        output.write(canonical({'outcome': 'fixture_owner_ready', **banner}).decode('ascii') + '\n'); output.flush()
        while True:
            raw = requests.readline(base.MAX_FRAME + 1)
            if not raw or len(raw) > base.MAX_FRAME:
                # EOF/oversize gives no permission to repeat or infer cleanup.
                owner.stdin.close(); owner.stdin = None
                break
            command = loads(raw)
            if command == {'command': 'abandon'}:
                if owner.stdin.write(b'Q') != 1: raise FoundryError('fixture_control', 'Control outcome is uncertain.')
                owner.stdin.flush(); owner.stdin.close(); owner.stdin = None
                break
            if (fenced or sent >= 128 or type(command) is not dict or
                    set(command) != {'operation_id', 'op', 'payload'} or
                    not input_journal._token(command['operation_id']) or
                    command['op'] not in ('status', 'input_status', 'fixture_click') or
                    type(command['payload']) is not dict):
                output.write(canonical({'outcome': 'unresolved', 'reason': 'fixture_input_fenced'}).decode('ascii') + '\n')
                output.flush(); fenced = True; continue
            request = {'schema': 1, 'session_id': banner['session_id'], 'owner_id': banner['owner_id'], **command}
            try:
                acquiring_client = True
                client = popen(prefix + ['fixture-owner', 'client', '--endpoint', banner['fixture_endpoint'],
                                         '--request', canonical(request).decode('ascii')], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
                acquiring_client = False
                cout, cerr = base._BoundedReader(client.stdout), base._BoundedReader(client.stderr)
                if client.stdin.write(secret) != len(secret): raise FoundryError('fixture_secret', 'Client transfer uncertain.')
                client.stdin.close(); client.stdin = None
                client.wait(timeout=8)
                payload = cout.finish(); cerr.finish()
                if client.returncode != 0: raise FoundryError('fixture_client_unresolved', 'Client did not confirm receipt.')
                response = loads(payload)
                row = response.get('response') if type(response) is dict else None
                if (type(response) is not dict or response.get('ok') is not True or
                        response.get('outcome') != 'fixture_response_received' or
                        response.get('semantic_success_established') is not False or
                        response.get('game_qualified') is not False or
                        response.get('live_dispatch_available') is not False or
                        type(row) is not dict or row.get('session_id') != banner['session_id'] or
                        row.get('owner_id') != banner['owner_id'] or row.get('operation_id') != command['operation_id']):
                    raise FoundryError('fixture_client_unresolved', 'Client response binding is unavailable.')
                client = None
                sent += 1
                # A matching receipt does not prove the underlying operation
                # succeeded. Only read-only observed status may leave sends open.
                if not base._status_allows_continuation(command['op'], row):
                    fenced = True
                response = dict(response, supervisor_fenced=fenced)
                output.write(canonical(response).decode('ascii') + '\n'); output.flush()
            except BaseException as exc:
                if client is not None: base._UNRESOLVED_CHILDREN.append(('client', client)); client = None
                elif acquiring_client: base._UNRESOLVED_CHILDREN.append(('client_acquisition_unknown', None))
                if not isinstance(exc, Exception):
                    _report_interruption(output)
                    raise
                fenced = True
                output.write(canonical({'outcome': 'unresolved', 'operation_id': command['operation_id'],
                                        'reason': ('fixture_client_permission_denied' if isinstance(exc, PermissionError)
                                                   else 'fixture_client_outcome_unknown'),
                                        'replayed': False}).decode('ascii') + '\n')
                output.flush()
        owner.wait(timeout=8)
        final_output = owner_output.finish(); owner_error.finish()
        final = loads(final_output)
        if owner.returncode != 3 or final.get('outcome') != 'unresolved':
            raise FoundryError('fixture_owner_unresolved', 'Owner exit outcome is unknown.')
        owner_finished = True
        return {'ok': False, 'outcome': 'unresolved', 'reason': 'fixture_owner_process_ended',
                'owner_id': banner['owner_id'], 'session_id': banner['session_id'],
                'original_fixture_witness_lost': True, 'do_not_relaunch_or_replay': True,
                'game_qualified': False, 'live_dispatch_available': False}
    except BaseException as exc:
        if client is not None: base._UNRESOLVED_CHILDREN.append(('client', client))
        elif acquiring_client: base._UNRESOLVED_CHILDREN.append(('client_acquisition_unknown', None))
        if not owner_finished: base._UNRESOLVED_CHILDREN.append(('owner', owner))
        if not isinstance(exc, Exception) or isinstance(exc, PermissionError):
            _report_interruption(output)
            raise
        raise FoundryError('fixture_supervision_unresolved', 'Fixture supervisor outcome is unknown; do not replay.',
                           outcome='unresolved', owner_id=banner.get('owner_id') if banner else None,
                           session_id=banner.get('session_id') if banner else None, replayed=False,
                           game_qualified=False) from exc
