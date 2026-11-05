"""Experimental foreground stdin owner; explicit selected session only.

This is not an authenticated cross-process service, a game-qualified observer,
or a restart recovery mechanism. It does not launch a game or access profiles.
"""
import json
import os
import re
import sys

from . import independent_live_owner, independent_session, input_journal, native_input, process_exit
from .errors import FoundryError
from .io import canonical, external_path, loads, new_json
from .retained_process_witness import RetainedProcessRegistry
from .session_witness import SessionWitness

MAX_COMMAND = 32768
_UNRESOLVED = []
_OWNED_FIXTURE_ADMISSION = object()


def _fixture_pid():
    return os.getpid()


def serve(authority, session, witness_id, ffmpeg, ffmpeg_sha256, *, input_stream=None, output_stream=None,
          _fixture_admission=None):
    """Retain a selected witness while processing bounded JSON commands on stdin."""
    if _fixture_admission is not _OWNED_FIXTURE_ADMISSION:
        raise FoundryError('live_authority_unqualified',
                           'Live admission is disabled until a single cross-workspace authority is qualified.',
                           input_attempted=False,game_qualified=False)
    if (not input_journal._token(witness_id) or type(ffmpeg) is not str or not ffmpeg
            or type(ffmpeg_sha256) is not str or not re.fullmatch(r'[0-9a-f]{64}',ffmpeg_sha256)):
        raise FoundryError('live_owner_arguments', 'An exact witness ID and pinned FFmpeg image are required.')
    intent, registry, journal, state = independent_session._open(authority,session)
    if len(state['events']) != 1 or state['events'][0].get('kind') != 'session':
        raise FoundryError('live_owner_unresolved', 'An unused original session is required; do not reacquire.')
    target = input_journal._target(intent['identity'])
    if target['pid'] != _fixture_pid():
        raise FoundryError('live_fixture_only', 'Only this test process may be selected by the fixture admission.')
    identity = {key:target[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
    marker = external_path(session)/'live-owner-intent.json'
    try:
        new_json(marker,{'schema':1,'session_id':intent['session_id'],'witness_id':witness_id,
                         'target':identity,'ffmpeg_sha256':ffmpeg_sha256,'experimental':True})
    except FileExistsError:
        raise FoundryError('live_owner_unresolved', 'Original owner was already attempted; inspect without reacquiring.') from None
    # The marker is published before either an image pin or process handle is
    # acquired. Windows power-loss durability is not established.
    retained = {'api':None,'image':None,'provider':None,'lease':None,'owner':None}
    _UNRESOLVED.append(retained)
    try:
        api = native_input._NativeApi()
        retained['api'] = api
        retained['image'] = api.pin_image(target)
        selected = canonical(identity)
        provider = RetainedProcessRegistry(process_exit._WindowsApi(),lambda view:canonical(view) == selected)
        retained['provider'] = provider
        witness = SessionWitness(registry,provider,identity,witness_id)
        lease = witness.acquire()
        retained['lease'] = lease
        owner = independent_live_owner.LiveOwner(registry,journal,witness,ffmpeg,ffmpeg_sha256,
                                                _fixture_admission=independent_live_owner._OWNED_FIXTURE_ADMISSION)
        retained['owner'] = owner
    except Exception as error:
        code = error.code if isinstance(error,FoundryError) else None
        code = code if type(code) is str and re.fullmatch(r'[a-z0-9_]{1,64}',code) else 'other'
        raise FoundryError('live_owner_start_unresolved',
                           'Original owner preparation may have acquired resources; inspect without retry.',
                           phase='after_owner_intent',no_replay=True,original_error_code=code) from None
    source = sys.stdin.buffer if input_stream is None else input_stream
    sink = sys.stdout if output_stream is None else output_stream
    def emit(value):
        sink.write(json.dumps(value,ensure_ascii=True,allow_nan=False,separators=(',',':'))+'\n')
        sink.flush()
    emit({'outcome':'experimental_owner_ready','session_id':intent['session_id'],'witness_id':witness_id,
          'same_user_authenticated':False,'capture_time_verified':False,'game_qualified':False})
    stop_attempted = False
    while True:
        line = source.readline(MAX_COMMAND+1)
        if not line:
            return {'ok':False,'outcome':'owner_input_closed','cleanup_verified':False,'no_replay':True}
        if type(line) is not bytes or len(line) > MAX_COMMAND or not line.endswith(b'\n'):
            raise FoundryError('live_owner_protocol', 'A bounded newline-delimited JSON command is required.')
        try:
            value = loads(line)
            if type(value) is not dict or type(value.get('op')) is not str:
                raise FoundryError('live_owner_protocol', 'An exact command object is required.')
            operation = value['op']
            if stop_attempted and operation not in ('status','input_status'):
                raise FoundryError('live_owner_stopping',
                                   'Stop was attempted; inspect original state without further dispatch or cleanup.',
                                   no_replay=True)
            if operation == 'status' and set(value) == {'op'}:
                current = registry.inspect()
                result = {'outcome':'owner_status','registry_pending':bool(current['events'] and current['events'][-1]['kind']=='attempt'),
                          'sender_retained':owner._backend_open,'stop_attempted':stop_attempted,
                          'acknowledged':False,'game_qualified':False}
            elif operation == 'input_status' and set(value) == {'op','request_id'}:
                result = {'outcome':'input_status','input':journal.status(value['request_id']),
                          'acknowledged':False,'game_qualified':False}
            elif operation == 'click' and set(value) == {'op','request_id','x','y','expectation','evidence_directory'}:
                result = owner.click_once(value['request_id'],value['x'],value['y'],
                                          expected_observation=value['expectation'],
                                          evidence_directory=value['evidence_directory'])
            elif operation == 'right_click' and set(value) == {'op','request_id','x','y','expectation','evidence_directory'}:
                result = owner.click_once(value['request_id'],value['x'],value['y'],button='right',
                                          expected_observation=value['expectation'],
                                          evidence_directory=value['evidence_directory'])
            elif operation == 'drag' and set(value) == {'op','request_id','path','duration_ms','expectation','evidence_directory'}:
                result = owner.drag_once(value['request_id'],value['path'],value['duration_ms'],
                                         expected_observation=value['expectation'],
                                         evidence_directory=value['evidence_directory'])
            elif operation == 'key' and set(value) == {'op','request_id','key','scan_code','extended','expectation','evidence_directory'}:
                result = owner.key_once(value['request_id'],value['key'],value['scan_code'],value['extended'],
                                        expected_observation=value['expectation'],
                                        evidence_directory=value['evidence_directory'])
            elif operation == 'wheel' and set(value) == {'op','request_id','screen_x','screen_y','delta','expectation','evidence_directory'}:
                result = owner.wheel_once(value['request_id'],value['screen_x'],value['screen_y'],value['delta'],
                                          expected_observation=value['expectation'],
                                          evidence_directory=value['evidence_directory'])
            elif operation == 'recover_frame_binding' and set(value) == {'op','request_id'}:
                result = owner.recover_frame_binding(value['request_id'])
            elif operation == 'capture_frame' and set(value) == {'op','request_id','evidence_directory'}:
                result = owner.capture_frame(value['request_id'],value['evidence_directory'])
            elif operation == 'stop' and set(value) == {'op','abandon'} and value['abandon'] is True:
                if stop_attempted:
                    raise FoundryError('live_owner_unresolved', 'Stop was already attempted; do not repeat it.')
                stop_attempted = True
                new_json(external_path(session)/'live-owner-stop-intent.json',
                         {'schema':1,'session_id':intent['session_id'],'witness_id':witness_id,'abandon':True})
                if owner._backend is not None:
                    owner.close_sender()
                receipt = lease.close()
                if (type(receipt) is not dict or receipt.get('outcome') != 'provider_reported_closed'
                        or receipt.get('os_release_verified') is not False
                        or receipt.get('pid') != identity['pid']
                        or receipt.get('creation_filetime') != identity['creation_filetime']):
                    raise FoundryError('live_owner_cleanup_unresolved', 'Original witness release is unconfirmed; do not retry.')
                api.close(retained['image'])
                retained['image'] = None
                new_json(external_path(session)/'live-owner-stop-receipt.json',
                         {'schema':1,'session_id':intent['session_id'],'witness_id':witness_id,
                          'provider_receipt':receipt,'os_release_verified':False})
                _UNRESOLVED.remove(retained)
                emit({'outcome':'owner_stop_reported','os_release_verified':False,'game_qualified':False})
                return {'ok':True,'outcome':'owner_stopped','os_release_verified':False,'game_qualified':False}
            else:
                raise FoundryError('live_owner_protocol', 'Unknown command or unexpected fields.')
        except FoundryError as error:
            result = error.as_dict()
        except (OSError,ValueError,KeyError,TypeError) as error:
            result = {'ok':False,'code':'live_owner_unresolved','error_type':type(error).__name__,
                      'no_replay':True}
        emit(result)
