"""Opt-in real registered-input fixture for this test process's own window.

It replaces the Sims-only identity validator and independent inspector for this
one exact synthetic Python process/window. All retained-handle, journal,
registry, native-message and WGC calls otherwise use their real implementations.
No game, profile or other process is opened.
"""
from contextlib import ExitStack
from copy import deepcopy
import ctypes
from ctypes import wintypes as wt
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
from unittest import mock

from sims4foundry_cli import (global_lifetime_authority, guarded_input, independent_live_owner, independent_live_service, input_frame_evidence, input_journal, live_input, native_input, process_exit,
                              registered_input, session_registry, windows, windows_registry_key, wgc_recorder)
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import ClientBounds, Point, encode_click
from sims4foundry_cli.io import digest, external_path, new_json
from sims4foundry_cli.retained_process_witness import RetainedProcessRegistry
from sims4foundry_cli.session_witness import SessionWitness
from sims4foundry_cli.sqlite_anchor import SQLiteAnchor
from sims4foundry_cli.visual_probe import _pixels

_OWNED_UNRESOLVED = []
_SAFE_DIAGNOSTICS = frozenset({
    'unknown_foreground','foreground_changed','integrity_mismatch','ownership_changed',
    'recording_unavailable','recording_unverified','input_context_unverified',
    'stale_observation','invalid_sequence','input_cleanup_unresolved',
    'input_preparation_failed','recording_start_failed','input_journal_bound',
})


def _trace_input_call(runner, diagnostics, *args):
    """Collect a bounded public code only; preserve the runner's behavior."""
    try:
        return runner(*args)
    except Exception as error:
        code = error.code if isinstance(error,FoundryError) else None
        diagnostics.append(code if type(code) is str and code in _SAFE_DIAGNOSTICS else 'other')
        raise


def _fixture_parent(path):
    parent = external_path(path).resolve(strict=True)
    if not parent.is_dir():
        raise FoundryError('fixture_parent', 'Use an existing external fixture directory.')
    return parent


def run_owned(test, hwnd, window_class, state, ffmpeg, ffmpeg_hash, require_isolation=lambda _:None):
    require_isolation(hwnd)
    session_id, witness_id, first_id, second_id = (uuid.uuid4().hex for _ in range(4))
    test.assertEqual(4,len({session_id,witness_id,first_id,second_id}))
    test.assertFalse({session_id,witness_id,first_id,second_id}.intersection(
        {'1'*32,'2'*32,'3'*32,'4'*32}))
    api = native_input._NativeApi()
    process_api = process_exit._WindowsApi()
    # The current-process pseudo-handle needs no acquisition or close.
    image, created = process_api.identity(api._current_process())
    expected_image = str(Path(sys.executable).resolve())
    test.assertTrue(process_exit._same_image(image, expected_image))
    with open(expected_image,'rb') as stream:
        checksum = hashlib.file_digest(stream,'sha256').hexdigest()
    user = ctypes.WinDLL('user32',use_last_error=True)
    owner = user.GetWindowThreadProcessId
    owner.argtypes=[wt.HWND,ctypes.POINTER(wt.DWORD)]; owner.restype=wt.DWORD
    pid = wt.DWORD()
    thread_id = int(owner(hwnd,ctypes.byref(pid)))
    test.assertEqual(os.getpid(),pid.value)
    target = {'schema':1,'kind':'foundry_windows_identity','pid':os.getpid(),
              'creation_filetime':created,'executable':expected_image,'executable_sha256':checksum,
              'hwnd':hwnd,'thread_id':thread_id,'window_class':window_class,
              'integrity':api.integrity(api._current_process())}
    target.update(api.geometry(target))
    real_validate = windows.validate_identity
    def validate_owned(value):
        if type(value) is dict and all(value.get(key) == target[key] for key in
                ('pid','creation_filetime','executable','executable_sha256','hwnd','thread_id','window_class')):
            return value
        return real_validate(value)
    def inspect_owned(pid_value, path, digest_value, hwnd_value=None):
        test.assertEqual((target['pid'],target['executable'],target['executable_sha256'],target['hwnd']),
                         (pid_value,str(path),digest_value,hwnd_value))
        return dict(deepcopy(target),observed_at=time.time())
    base = _fixture_parent(os.environ.get('FOUNDRY_REGISTERED_FIXTURE_ROOT',tempfile.gettempdir()))
    root = base / ('foundry-owned-registered-' + uuid.uuid4().hex)
    try:
        root.mkdir(exist_ok=False)
        test.assertEqual(base,root.resolve(strict=True).parent)
        new_json(root/'fixture-manifest.json',{'schema':1,'kind':'owned_registered_fixture',
                                               'directory_name':root.name,'game_accessed':False})
        diagnostics = []
        with ExitStack() as stack:
            original_runner = input_journal.run_packets
            stack.enter_context(mock.patch.object(input_journal,'run_packets',
                                side_effect=lambda *args:_trace_input_call(original_runner,diagnostics,*args)))
            for module in (windows,input_journal,session_registry,guarded_input,wgc_recorder):
                stack.enter_context(mock.patch.object(module,'validate_identity',side_effect=validate_owned))
            stack.enter_context(mock.patch.object(windows,'inspect',side_effect=inspect_owned))
            journal = input_journal.InputJournal.create(root/'journal',session_id,target)
            registry_dir = root/'registry'; registry_dir.mkdir()
            anchor = SQLiteAnchor.create(root/'anchor.sqlite')
            key = windows_registry_key.create_key(root/'registry-key.json',digest(session_registry._lifetime(target)))
            registry = session_registry.SessionRegistry(registry_dir,target,key,anchor)
            registry.open_session(session_id,registered_input.journal_binding(journal))
            if os.environ.get('FOUNDRY_WGC_LIVE_SERVICE_TEST') == '1':
                intent = {'session_id':session_id,'identity':target}
                action = os.environ.get('FOUNDRY_WGC_ACTION','click')
                common = {'request_id':first_id,'expectation':{'fixture':'test'},
                          'evidence_directory':str(root/'frame-evidence')}
                if action == 'drag':
                    command = dict(common,op='drag',path=[[20,30],[40,50]],duration_ms=50)
                    expected_messages = [0x0201,0x0200,0x0202]
                elif action == 'key':
                    mapping = api.mapping(0x09,api.layout(thread_id))
                    test.assertTrue(type(mapping) is int and 0 < (mapping & 255) <= 255 and (mapping >> 8) in (0,0xe0))
                    command = dict(common,op='key',key='TAB',scan_code=mapping & 255,extended=bool(mapping >> 8))
                    expected_messages = [0x0100,0x0101]
                elif action == 'wheel':
                    x,y = target['client_origin']
                    command = dict(common,op='wheel',screen_x=x+20,screen_y=y+30,delta=120)
                    expected_messages = [0x020a]
                else:
                    command = dict(common,op='click',x=20,y=30)
                    expected_messages = [0x0201,0x0202]
                commands = [
                    command,
                    {'op':'input_status','request_id':first_id},
                    {'op':'stop','abandon':True}]
                source = io.BytesIO(b''.join(json.dumps(command).encode()+b'\n' for command in commands))
                sink = io.StringIO()
                with (mock.patch.object(global_lifetime_authority,'_local_appdata',return_value=root/'fixture-local'),
                      mock.patch.object(independent_live_service.independent_session,'_open',
                                        return_value=(intent,registry,journal,registry.inspect()))):
                    global_lifetime_authority.initialize()
                    result = independent_live_service.serve(root/'unused-authority',root,witness_id,ffmpeg,ffmpeg_hash,
                                                            input_stream=source,output_stream=sink,
                                                            _fixture_admission=independent_live_service._OWNED_FIXTURE_ADMISSION)
                rows = [json.loads(line) for line in sink.getvalue().splitlines()]
                test.assertEqual('experimental_owner_ready',rows[0]['outcome'])
                test.assertEqual('frame_recorded',rows[1]['outcome'])
                test.assertEqual('awaiting_readback',rows[2]['input']['state'])
                test.assertEqual('owner_stopped',result['outcome'])
                deadline=time.monotonic()+12
                while len(state['events']) < len(expected_messages) and time.monotonic() < deadline: time.sleep(.05)
                test.assertEqual(expected_messages,[event[0] for event in state['events']])
                test.assertFalse(rows[1]['acknowledged'])
                return
            provider = RetainedProcessRegistry(process_api,lambda view:True)
            process_identity={key:target[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
            witness=SessionWitness(registry,provider,process_identity,witness_id)
            retained = [provider,None,str(root),None,hwnd]
            _OWNED_UNRESOLVED.append(retained)
            lease=witness.acquire()
            retained[1] = lease
            closed = False
            try:
                packets=encode_click(Point(20,30),ClientBounds(target['client_width'],target['client_height']))
                if os.environ.get('FOUNDRY_WGC_LIVE_OWNER_TEST') == '1':
                    owner = independent_live_owner.LiveOwner(registry,journal,witness,ffmpeg,ffmpeg_hash,
                                  _fixture_admission=independent_live_owner._OWNED_FIXTURE_ADMISSION)
                    retained[3] = owner
                    require_isolation(hwnd)
                    result = owner.click_once(first_id,20,30,expected_observation={'fixture':'white'},
                                              evidence_directory=root/'frame-evidence')
                    if result.get('outcome') != 'frame_recorded':
                        print('Owned input diagnostic:',diagnostics[-1] if diagnostics else 'no_runner_exception',flush=True)
                    test.assertEqual('frame_recorded',result['outcome'])
                    test.assertFalse(result['acknowledged'])
                    deadline=time.monotonic()+12
                    while len(state['events']) < 2 and time.monotonic() < deadline:
                        time.sleep(.05)
                    test.assertEqual([(int(packet.message),packet.wparam,packet.lparam) for packet in packets],state['events'])
                    test.assertEqual('awaiting_readback',journal.status(first_id)['state'])
                    observed = input_frame_evidence.inspect(journal,first_id,root/'frame-evidence')
                    test.assertEqual('matching_files_observed',observed['state'])
                    with test.assertRaises(FoundryError):
                        owner.click_once(first_id,20,30,expected_observation={'fixture':'white'},
                                         evidence_directory=root/'different-evidence')
                    test.assertEqual(2,len(state['events']))
                    if os.environ.get('FOUNDRY_WGC_OWNER_SEQUENCE_TEST') == '1':
                        # The owned window's WndProc is the explicit fixture observer.
                        # It is not a game observer or an operator attestation.
                        expected_events = [(int(packet.message),packet.wparam,packet.lparam) for packet in packets]
                        def observe(view, selected, expectation):
                            test.assertEqual(expected_events,state['events'])
                            test.assertEqual(target['hwnd'],selected['hwnd'])
                            return dict(view,evidence_sha256=digest({'events':state['events']}),
                                        observation=expectation)
                        completion = owner.fixture_complete_step(first_id,observe)
                        test.assertEqual('ready',completion['completion_state'])
                        with test.assertRaises(FoundryError):
                            owner.click_once(second_id,40,50,expected_observation={'fixture':'second'},
                                             evidence_directory=root/'second-frame')
                        inspected = owner.fixture_completion_snapshot(first_id)
                        test.assertEqual('confirmed',owner.fixture_confirm_completion(
                            first_id,inspected['completion_token'])['completion_state'])
                        require_isolation(hwnd)
                        second = owner.click_once(second_id,40,50,expected_observation={'fixture':'second'},
                                                  evidence_directory=root/'second-frame')
                        test.assertEqual('frame_recorded',second['outcome'])
                        second_packets = encode_click(Point(40,50),ClientBounds(target['client_width'],target['client_height']))
                        deadline = time.monotonic()+12
                        while len(state['events']) < 4 and time.monotonic() < deadline: time.sleep(.05)
                        test.assertEqual(expected_events + [(int(packet.message),packet.wparam,packet.lparam)
                                                            for packet in second_packets],state['events'])
                        test.assertEqual('awaiting_readback',journal.status(second_id)['state'])
                        test.assertFalse(second['semantic_state_verified'])
                        test.assertFalse(second['game_qualified'])
                    test.assertEqual('sender_cleanup_reported',owner.close_sender()['outcome'])
                else:
                    run_original(test,registry,journal,witness,packets,target,ffmpeg,ffmpeg_hash,state,root,first_id)
                result=lease.close()
                closed = result.get('outcome') == 'provider_reported_closed'
                test.assertTrue(closed)
                _OWNED_UNRESOLVED.remove(retained)
                new_json(root/'fixture-outcome.json',{'schema':1,'journal_state':'awaiting_readback',
                         'request_id':(second_id if os.environ.get('FOUNDRY_WGC_OWNER_SEQUENCE_TEST') == '1' else first_id),'capture_time_verified':False,'game_qualified':False})
            finally:
                # An uncertain witness close is never retried.
                if not closed:
                    pass
    finally:
        # The action remains awaiting readback; keep its original evidence even
        # on success, and preserve partial evidence on any uncertain failure.
        print('Owned fixture evidence directory name: ' + root.name,flush=True)


def run_original(test,registry,journal,witness,packets,target,ffmpeg,ffmpeg_hash,state,root,first_id):
    with live_input.LiveInputBackend(target,packets,ffmpeg,ffmpeg_hash) as backend:
        before = backend._recorder.sample()
        region = (before.width//2,before.height//2,1,1)
        _, pixel = _pixels(before.png,region)
        test.assertTrue(all(value <= 10 for value in pixel[:3]))
        result=registered_input.execute_once(registry,journal,first_id,packets,backend,
                                             expected_observation={'fixture':'white'},witness=witness)
        test.assertEqual('awaiting_readback',result['state'])
        test.assertFalse(result['result']['semantic_success'])
        deadline=time.monotonic()+12
        changed=None
        while time.monotonic()<deadline:
            try:
                frame=backend._recorder.sample()
                if state['clicked'] and frame.sequence>before.sequence:
                    _, pixel=_pixels(frame.png,region)
                    if all(value>=245 for value in pixel[:3]):
                        changed=frame; break
            except FoundryError as error:
                if error.code!='recording_unavailable': raise
            time.sleep(.05)
        test.assertIsNotNone(changed,'No expected white fixture pixel was received')
        test.assertEqual([(int(packet.message),packet.wparam,packet.lparam) for packet in packets],state['events'])
        evidence=input_frame_evidence.capture(journal,first_id,backend,root/'frame-evidence')
        test.assertEqual('frame_recorded',evidence['outcome'])
        test.assertFalse(evidence['capture_time_verified'])
        test.assertFalse(evidence['acknowledged'])
        observed = input_frame_evidence.inspect(journal,first_id,root/'frame-evidence')
        test.assertEqual('matching_files_observed',observed['state'])
        image = root/'frame-evidence'/'frame.png'
        compared = input_frame_evidence.compare(journal,first_id,root/'frame-evidence',observed['frame_sha256'],
                    observed['evidence_intent_sha256'],image,observed['frame_sha256'],(0,0,changed.width,changed.height))
        test.assertTrue(compared['matched'])
        test.assertFalse(compared['capture_time_verified'])
        test.assertFalse(compared['acknowledged'])
