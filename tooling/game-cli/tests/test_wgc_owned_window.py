"""Opt-in real WGC stream test on an owned, briefly visible test window."""
from copy import deepcopy
import ctypes
from ctypes import wintypes as wt
import os
import threading
import time
import unittest
import uuid

from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import ClientBounds, Message, Point, encode_click
from sims4foundry_cli.native_input import _NativeApi
from sims4foundry_cli.wgc_recorder import WgcRecorder
from test_guarded_input import identity


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
        lifetime_lock = threading.Lock()
        state={'window':None,'error':None,'clicked':False}
        @WNDPROC
        def callback(hwnd,message,wparam,lparam):
            if message == int(Message.LEFT_UP):
                state['clicked']=True
                invalidate(hwnd,None,True)
                return 0
            if message == 0x000F:  # WM_PAINT for this owned fixture
                paint=PAINTSTRUCT(); dc=begin_paint(hwnd,ctypes.byref(paint)); rect=wt.RECT()
                if dc and client_rect(hwnd,ctypes.byref(rect)):
                    fill(dc,ctypes.byref(rect),white if state['clicked'] else black)
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
                    if hwnd: destroy(hwnd)
                    if registered: unregister(name,instance)
                    done.set()
        thread=threading.Thread(target=owner,daemon=True); thread.start()
        recorder=None
        try:
            self.assertTrue(ready.wait(5),'Capture fixture did not start')
            if state['error']: raise state['error']
            with lifetime_lock:
                self.assertFalse(done.is_set(),'Owned capture window already stopped')
                target=identity(); target['hwnd']=state['window']
                recorder=WgcRecorder(target,os.environ['FOUNDRY_WGC_TEST_FFMPEG'],
                                     os.environ['FOUNDRY_WGC_TEST_SHA256'],
                                     observer=lambda *args:deepcopy(target))
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
                native = _NativeApi()
                for packet in encode_click(Point(20,30),ClientBounds(128,128)):
                    self.assertIs(native.post(state['window'],packet), True)
                before = frame
                changed = None
                deadline = time.monotonic()+12
                while time.monotonic()<deadline:
                    try:
                        candidate = recorder.sample()
                        if state['clicked'] and candidate.sequence > before.sequence and candidate.sha256 != before.sha256:
                            changed = candidate; break
                    except FoundryError as error:
                        if error.code != 'recording_unavailable': raise
                    time.sleep(.05)
                self.assertIsNotNone(changed,'Owned window input was not followed by a changed captured frame')
                self.assertTrue(recorder.close(),'Owned capture process cleanup was unresolved')
                recorder=None
        finally:
            try:
                if recorder is not None:
                    self.assertTrue(recorder.close(),'Owned capture process cleanup was unresolved')
            finally:
                stop.set()
                thread.join(timeout=5)
        self.assertTrue(done.is_set(),'Owned capture window did not stop')


if __name__ == '__main__': unittest.main()
