"""Opt-in real WGC stream test on an owned, briefly visible test window."""
from copy import deepcopy
import ctypes
from ctypes import wintypes as wt
import os
import threading
import time
import unittest
import uuid
from unittest import mock

from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import ClientBounds, Message, Point, encode_click
from sims4foundry_cli.native_input import _NativeApi
from sims4foundry_cli.visual_probe import _pixels
from sims4foundry_cli.wgc_recorder import WgcRecorder, _retain_unresolved
from test_guarded_input import identity

_UNRESOLVED_WINDOWS = []


def _stop_or_retain(thread, stop, state, unresolved):
    if any(item.get('hwnd') == state['window'] for item in unresolved):
        _UNRESOLVED_WINDOWS.append((thread,stop,state))
        return False
    stop.set()
    thread.join(timeout=5)
    return not thread.is_alive() and state['destroyed'] and state['unregistered']


def _ensure_distinct_fixture(hwnd, bound_previous=None):
    """Compare recorded values only; never query or operate on the old process."""
    if os.environ.get('FOUNDRY_WGC_OWNER_SEQUENCE_TEST') != '1':
        return
    if (os.environ.get('FOUNDRY_WGC_REGISTERED_TEST') != '1'
            or os.environ.get('FOUNDRY_WGC_LIVE_OWNER_TEST') != '1'
            or os.environ.get('FOUNDRY_WGC_LIVE_SERVICE_TEST') == '1'
            or os.environ.get('FOUNDRY_WGC_TEE_PROBE') == '1'
            or os.environ.get('FOUNDRY_WGC_ACTION','click') != 'click'):
        raise FoundryError('fixture_isolation', 'The registered owner sequence fixture must be selected exactly.')
    old_pid = os.environ.get('FOUNDRY_WGC_PREVIOUS_PID','')
    old_hwnd = os.environ.get('FOUNDRY_WGC_PREVIOUS_HWND','')
    if (bound_previous is not None and (old_pid,old_hwnd) != bound_previous):
        raise FoundryError('fixture_isolation', 'The prior fixture binding changed.')
    if (not old_pid.isascii() or not old_pid.isdecimal() or not old_hwnd.isascii()
            or not old_hwnd.isdecimal() or int(old_pid) <= 0 or int(old_hwnd) <= 0
            or type(hwnd) is not int or hwnd <= 0 or os.getpid() == int(old_pid)
            or hwnd == int(old_hwnd)):
        raise FoundryError('fixture_isolation', 'A new process and window distinct from the original fixture are required.')


def _initial_recorder_retention(recorder, cleanup_confirmed, hwnd):
    # No cleanup call is made here. A lost/failed close result keeps the exact
    # window, even if a late worker may already have released its resources.
    return [] if recorder is None or cleanup_confirmed is True else [{'hwnd':hwnd}]


def _fixture_service_retentions(entries, hwnd):
    result = []
    for item in entries:
        value = item.get('fixture_hwnd') if type(item) is dict else None
        result.append({'hwnd':value if type(value) is int and value > 0 else hwnd})
    return result


class WgcFixtureOwnershipTests(unittest.TestCase):
    def test_sequence_fixture_requires_distinct_recorded_process_and_window(self):
        env = {'FOUNDRY_WGC_OWNER_SEQUENCE_TEST':'1', 'FOUNDRY_WGC_REGISTERED_TEST':'1',
               'FOUNDRY_WGC_LIVE_OWNER_TEST':'1', 'FOUNDRY_WGC_PREVIOUS_PID':'17',
               'FOUNDRY_WGC_PREVIOUS_HWND':'25'}
        with mock.patch.dict(os.environ,env), mock.patch.object(os,'getpid',return_value=18):
            _ensure_distinct_fixture(26)
            with self.assertRaises(FoundryError): _ensure_distinct_fixture(25)
            with mock.patch.object(os,'getpid',return_value=17):
                with self.assertRaises(FoundryError): _ensure_distinct_fixture(26)
            with mock.patch.dict(os.environ,{'FOUNDRY_WGC_PREVIOUS_HWND':''}):
                with self.assertRaises(FoundryError): _ensure_distinct_fixture(26)
            with mock.patch.dict(os.environ,{'FOUNDRY_WGC_LIVE_SERVICE_TEST':'1'}):
                with self.assertRaises(FoundryError): _ensure_distinct_fixture(26)
            with self.assertRaises(FoundryError): _ensure_distinct_fixture(26,('19','25'))
            with mock.patch.dict(os.environ,{'FOUNDRY_WGC_ACTION':'key'}):
                with self.assertRaises(FoundryError): _ensure_distinct_fixture(26)

    def test_initial_recorder_uncertainty_retains_original_window(self):
        class Recorder:
            def close(self): raise AssertionError('Must not repeat uncertain close')
        recorder = Recorder()
        self.assertEqual([{'hwnd':123}],_initial_recorder_retention(recorder,False,123))
        self.assertEqual([], _initial_recorder_retention(recorder,True,123))
        self.assertEqual([], _initial_recorder_retention(None,False,123))

    def test_service_retention_uses_original_or_unknown_window_binding(self):
        rows = _fixture_service_retentions([{'fixture_hwnd':123},{'fixture_hwnd':456}, {}, None],123)
        self.assertEqual([{'hwnd':123},{'hwnd':456},{'hwnd':123},{'hwnd':123}],rows)

    def test_unresolved_child_retains_window_without_stop(self):
        class Thread:
            def join(self, **kwargs): raise AssertionError('Must not join retained owner')
        class Stop:
            def set(self): raise AssertionError('Must not stop retained window')
        state = {'window':123,'destroyed':False,'unregistered':False}
        self.assertFalse(_stop_or_retain(Thread(),Stop(),state,[{'hwnd':123}]))
        self.assertIs(_UNRESOLVED_WINDOWS[-1][2],state)
        _UNRESOLVED_WINDOWS.pop()


@unittest.skipUnless(os.name == 'nt' and os.environ.get('FOUNDRY_WGC_TEST_FFMPEG')
                     and os.environ.get('FOUNDRY_WGC_TEST_SHA256'),
                     'Opt in with an explicit FFmpeg path and SHA-256')
class WgcOwnedWindowTests(unittest.TestCase):
    def test_real_stream_from_owned_window(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        class WNDCLASS(ctypes.Structure):
            _fields_ = [('style',wt.UINT),('wndproc',WNDPROC),('cls_extra',ctypes.c_int),
                        ('wnd_extra',ctypes.c_int),('instance',wt.HINSTANCE),('icon',wt.HICON),
                        ('cursor',wt.HANDLE),('background',wt.HBRUSH),('menu',wt.LPCWSTR),('name',wt.LPCWSTR)]
        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [('dc',wt.HDC),('erase',wt.BOOL),('paint',wt.RECT),('restore',wt.BOOL),
                        ('inc_update',wt.BOOL),('reserved',ctypes.c_ubyte*32)]
        def bind(dll,name,result,args):
            fn=getattr(dll,name); fn.restype=result; fn.argtypes=args; return fn
        instance = bind(kernel,'GetModuleHandleW',wt.HMODULE,[wt.LPCWSTR])(None)
        stock = bind(gdi,'GetStockObject',wt.HANDLE,[ctypes.c_int])
        black, white = stock(4), stock(0)
        register = bind(user,'RegisterClassW',wt.ATOM,[ctypes.POINTER(WNDCLASS)])
        create = bind(user,'CreateWindowExW',wt.HWND,[wt.DWORD,wt.LPCWSTR,wt.LPCWSTR,wt.DWORD,
                      ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wt.HWND,wt.HMENU,wt.HINSTANCE,ctypes.c_void_p])
        default = bind(user,'DefWindowProcW',ctypes.c_ssize_t,[wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM])
        show = bind(user,'ShowWindow',wt.BOOL,[wt.HWND,ctypes.c_int])
        update = bind(user,'UpdateWindow',wt.BOOL,[wt.HWND])
        invalidate = bind(user,'InvalidateRect',wt.BOOL,[wt.HWND,ctypes.POINTER(wt.RECT),wt.BOOL])
        begin_paint = bind(user,'BeginPaint',wt.HDC,[wt.HWND,ctypes.POINTER(PAINTSTRUCT)])
        end_paint = bind(user,'EndPaint',wt.BOOL,[wt.HWND,ctypes.POINTER(PAINTSTRUCT)])
        client_rect = bind(user,'GetClientRect',wt.BOOL,[wt.HWND,ctypes.POINTER(wt.RECT)])
        fill = bind(user,'FillRect',ctypes.c_int,[wt.HDC,ctypes.POINTER(wt.RECT),wt.HBRUSH])
        peek_message = bind(user,'PeekMessageW',wt.BOOL,[ctypes.POINTER(wt.MSG),wt.HWND,wt.UINT,wt.UINT,wt.UINT])
        translate = bind(user,'TranslateMessage',wt.BOOL,[ctypes.POINTER(wt.MSG)])
        dispatch = bind(user,'DispatchMessageW',ctypes.c_ssize_t,[ctypes.POINTER(wt.MSG)])
        destroy = bind(user,'DestroyWindow',wt.BOOL,[wt.HWND])
        unregister = bind(user,'UnregisterClassW',wt.BOOL,[wt.LPCWSTR,wt.HINSTANCE])
        name='FoundryWgcFixture'+uuid.uuid4().hex
        ready, done, stop = threading.Event(), threading.Event(), threading.Event()
        bound_previous = (os.environ.get('FOUNDRY_WGC_PREVIOUS_PID',''),
                          os.environ.get('FOUNDRY_WGC_PREVIOUS_HWND',''))
        def require_isolation(hwnd):
            _ensure_distinct_fixture(hwnd,bound_previous)
        lifetime_lock = threading.Lock()
        state={'window':None,'error':None,'clicked':False,'marker':False,'events':[],'destroyed':True,'unregistered':True}
        @WNDPROC
        def callback(hwnd,message,wparam,lparam):
            tracked = (Message.LEFT_DOWN,Message.LEFT_UP)
            if os.environ.get('FOUNDRY_WGC_ACTION') == 'drag':
                tracked = (Message.LEFT_DOWN,Message.MOUSE_MOVE,Message.LEFT_UP)
            elif os.environ.get('FOUNDRY_WGC_ACTION') == 'key':
                tracked = (Message.KEY_DOWN,Message.KEY_UP)
            elif os.environ.get('FOUNDRY_WGC_ACTION') == 'wheel':
                tracked = (Message.MOUSE_WHEEL,)
            if message in tuple(int(item) for item in tracked):
                state['events'].append((message,int(wparam),int(lparam)))
            if message == int(Message.LEFT_UP):
                state['clicked']=True
                invalidate(hwnd,None,True)
                return 0
            if message == 0x000F:  # WM_PAINT for this owned fixture
                paint=PAINTSTRUCT(); dc=begin_paint(hwnd,ctypes.byref(paint)); rect=wt.RECT()
                if dc and client_rect(hwnd,ctypes.byref(rect)):
                    brush = black if state['marker'] and state['clicked'] else white if state['clicked'] or state['marker'] else black
                    fill(dc,ctypes.byref(rect),brush)
                end_paint(hwnd,ctypes.byref(paint))
                return 0
            return default(hwnd,message,wparam,lparam)
        def owner():
            hwnd=None; registered=False
            try:
                cls=WNDCLASS(0,callback,0,0,instance,None,None,black,None,name)
                if not register(ctypes.byref(cls)): raise OSError('Cannot register capture fixture')
                registered=True
                hwnd=create(0,name,name,0x80000000,20,20,128,128,None,None,instance,None)
                if not hwnd: raise OSError('Cannot create capture fixture')
                show(hwnd,4)  # SW_SHOWNOACTIVATE
                update(hwnd)
                state['window']=int(hwnd); ready.set()
                msg=wt.MSG()
                while not stop.is_set():
                    while peek_message(ctypes.byref(msg),None,0,0,1):
                        translate(ctypes.byref(msg)); dispatch(ctypes.byref(msg))
                    stop.wait(.01)
            except Exception as error:
                state['error']=error; ready.set()
            finally:
                with lifetime_lock:
                    if hwnd: state['destroyed']=bool(destroy(hwnd))
                    if registered: state['unregistered']=bool(unregister(name,instance))
                    done.set()
        thread=threading.Thread(target=owner,daemon=True); thread.start()
        recorder=None
        close_attempted=False
        recorder_cleanup_confirmed=False
        try:
            self.assertTrue(ready.wait(5),'Capture fixture did not start')
            if state['error']: raise state['error']
            with lifetime_lock:
                self.assertFalse(done.is_set(),'Owned capture window already stopped')
                require_isolation(state['window'])
                if os.environ.get('FOUNDRY_WGC_TEE_PROBE') == '1':
                    from wgc_tee_probe import run_owned
                    run_owned(self,state['window'],state,invalidate,
                              os.environ['FOUNDRY_WGC_TEST_FFMPEG'],os.environ['FOUNDRY_WGC_TEST_SHA256'])
                    return
                target=identity(); target['hwnd']=state['window']
                recorder=WgcRecorder(target,os.environ['FOUNDRY_WGC_TEST_FFMPEG'],
                                     os.environ['FOUNDRY_WGC_TEST_SHA256'],
                                     observer=lambda *args:deepcopy(target))
                # Hold strong ownership across interruption windows. A failed
                # start performs its own one-shot cleanup or stays quarantined.
                _retain_unresolved(recorder)
                recorder.start()
                deadline=time.monotonic()+12
                frame=None
                while time.monotonic()<deadline:
                    try:
                        frame=recorder.sample(); break
                    except FoundryError as error:
                        if error.code != 'recording_unavailable': raise
                        time.sleep(.05)
                self.assertIsNotNone(frame,'No complete frame arrived from the owned window')
                self.assertGreater(frame.width,0); self.assertGreater(frame.height,0)
                self.assertTrue(frame.png.startswith(b'\x89PNG\r\n\x1a\n'))
                region = (frame.width//2, frame.height//2, 1, 1)
                _, before_pixel = _pixels(frame.png,region)
                self.assertTrue(all(value <= 10 for value in before_pixel[:3]),'Fixture did not begin black')
                if os.environ.get('FOUNDRY_WGC_REGISTERED_TEST') == '1':
                    close_attempted=True
                    recorder_cleanup_confirmed = recorder.close() is True
                    self.assertTrue(recorder_cleanup_confirmed,'Owned capture process cleanup was unresolved')
                    from registered_owned_fixture import run_owned
                    run_owned(self,state['window'],name,state,os.environ['FOUNDRY_WGC_TEST_FFMPEG'],
                              os.environ['FOUNDRY_WGC_TEST_SHA256'],require_isolation)
                else:
                    # The ordinary opt-in fixture checks raw posting plus WGC.
                    native = _NativeApi()
                    packets = encode_click(Point(20,30),ClientBounds(128,128))
                    for packet in packets:
                        self.assertIs(native.post(state['window'],packet), True)
                    before = frame
                    changed = None
                    deadline = time.monotonic()+12
                    while time.monotonic()<deadline:
                        try:
                            candidate = recorder.sample()
                            if state['clicked'] and candidate.sequence > before.sequence:
                                _, pixel = _pixels(candidate.png,region)
                                if all(value >= 245 for value in pixel[:3]):
                                    changed = candidate; break
                        except FoundryError as error:
                            if error.code != 'recording_unavailable': raise
                        time.sleep(.05)
                    self.assertIsNotNone(changed,'Owned window input was not followed by a changed captured frame')
                    self.assertEqual([(int(packet.message),packet.wparam,packet.lparam) for packet in packets],
                                     state['events'])
                    close_attempted=True
                    recorder_cleanup_confirmed = recorder.close() is True
                    self.assertTrue(recorder_cleanup_confirmed,'Owned capture process cleanup was unresolved')
        finally:
            try:
                if recorder is not None and not close_attempted and not recorder._cancel.is_set():
                    close_attempted=True
                    recorder_cleanup_confirmed = recorder.close() is True
                    self.assertTrue(recorder_cleanup_confirmed,'Owned capture process cleanup was unresolved')
            finally:
                unresolved = []
                if os.environ.get('FOUNDRY_WGC_TEE_PROBE') == '1':
                    from wgc_tee_probe import _UNRESOLVED
                    unresolved = list(_UNRESOLVED)
                elif os.environ.get('FOUNDRY_WGC_REGISTERED_TEST') == '1':
                    from registered_owned_fixture import _OWNED_UNRESOLVED
                    unresolved = [{'hwnd':item[4]} for item in _OWNED_UNRESOLVED if len(item) > 4]
                    if os.environ.get('FOUNDRY_WGC_LIVE_SERVICE_TEST') == '1':
                        from sims4foundry_cli.independent_live_service import _UNRESOLVED as service_unresolved
                        # A malformed/missing binding is not proof this window is free.
                        unresolved += _fixture_service_retentions(service_unresolved,state['window'])
                unresolved += _initial_recorder_retention(recorder,recorder_cleanup_confirmed,state['window'])
                if not _stop_or_retain(thread,stop,state,unresolved) or not done.is_set():
                    raise AssertionError('Owned capture window or thread cleanup is unresolved')


if __name__ == '__main__': unittest.main()
