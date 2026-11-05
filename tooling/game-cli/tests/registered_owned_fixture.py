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
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
from unittest import mock

from sims4foundry_cli import (guarded_input, input_frame_evidence, input_journal, live_input, native_input, process_exit,
                              registered_input, session_registry, windows, windows_registry_key, wgc_recorder)
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import ClientBounds, Point, encode_click
from sims4foundry_cli.io import digest, external_path, new_json
from sims4foundry_cli.retained_process_witness import RetainedProcessRegistry
from sims4foundry_cli.session_witness import SessionWitness
from sims4foundry_cli.sqlite_anchor import SQLiteAnchor
from sims4foundry_cli.visual_probe import _pixels

_OWNED_UNRESOLVED = []


def _fixture_parent(path):
    parent = external_path(path).resolve(strict=True)
    if not parent.is_dir():
        raise FoundryError('fixture_parent', 'Use an existing external fixture directory.')
    return parent


def run_owned(test, hwnd, window_class, state, ffmpeg, ffmpeg_hash):
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
        with ExitStack() as stack:
            for module in (windows,input_journal,session_registry,guarded_input,wgc_recorder):
                stack.enter_context(mock.patch.object(module,'validate_identity',side_effect=validate_owned))
            stack.enter_context(mock.patch.object(windows,'inspect',side_effect=inspect_owned))
            journal = input_journal.InputJournal.create(root/'journal','1'*32,target)
            registry_dir = root/'registry'; registry_dir.mkdir()
            anchor = SQLiteAnchor.create(root/'anchor.sqlite')
            key = windows_registry_key.create_key(root/'registry-key.json',digest(session_registry._lifetime(target)))
            registry = session_registry.SessionRegistry(registry_dir,target,key,anchor)
            registry.open_session('1'*32,registered_input.journal_binding(journal))
            provider = RetainedProcessRegistry(process_api,lambda view:True)
            process_identity={key:target[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
            witness=SessionWitness(registry,provider,process_identity,'2'*32)
            retained = [provider,None,str(root)]
            _OWNED_UNRESOLVED.append(retained)
            lease=witness.acquire()
            retained[1] = lease
            closed = False
            try:
                packets=encode_click(Point(20,30),ClientBounds(target['client_width'],target['client_height']))
                with live_input.LiveInputBackend(target,packets,ffmpeg,ffmpeg_hash) as backend:
                    before = backend._recorder.sample()
                    region = (before.width//2,before.height//2,1,1)
                    _, pixel = _pixels(before.png,region)
                    test.assertTrue(all(value <= 10 for value in pixel[:3]))
                    result=registered_input.execute_once(registry,journal,'3'*32,packets,backend,
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
                    test.assertEqual([(int(packet.message),packet.wparam,packet.lparam) for packet in packets],
                                     state['events'])
                    evidence=input_frame_evidence.capture(journal,'3'*32,backend,root/'frame-evidence')
                    test.assertEqual('frame_recorded',evidence['outcome'])
                    test.assertFalse(evidence['capture_time_verified'])
                    test.assertFalse(evidence['acknowledged'])
                result=lease.close()
                closed = result.get('outcome') == 'provider_reported_closed'
                test.assertTrue(closed)
                _OWNED_UNRESOLVED.remove(retained)
                new_json(root/'fixture-outcome.json',{'schema':1,'journal_state':'awaiting_readback',
                         'request_id':'3'*32,'capture_time_verified':False,'game_qualified':False})
            finally:
                # An uncertain witness close is never retried.
                if not closed:
                    pass
    finally:
        # The action remains awaiting readback; keep its original evidence even
        # on success, and preserve partial evidence on any uncertain failure.
        print('Owned fixture evidence directory name: ' + root.name,flush=True)
