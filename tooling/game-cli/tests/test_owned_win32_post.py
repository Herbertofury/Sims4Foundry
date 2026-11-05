"""Exercise actual Win32 message posting only against an owned hidden window."""
import ctypes
from ctypes import wintypes as wt
import os
import threading
import unittest
import uuid

from sims4foundry_cli.input_packets import ClientBounds, Message, Point, encode_click


@unittest.skipUnless(os.name == 'nt', 'Owned Win32 window fixture requires Windows')
class OwnedWin32PostTests(unittest.TestCase):
    def test_real_postmessage_delivers_to_owned_message_window(self):
        from sims4foundry_cli.native_input import _NativeApi
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        class WNDCLASS(ctypes.Structure):
            _fields_ = [('style',wt.UINT),('wndproc',WNDPROC),('cls_extra',ctypes.c_int),
                        ('wnd_extra',ctypes.c_int),('instance',wt.HINSTANCE),('icon',wt.HICON),
                        ('cursor',wt.HANDLE),('background',wt.HBRUSH),('menu',wt.LPCWSTR),
                        ('name',wt.LPCWSTR)]
        def bind(dll,name,result,args):
            fn = getattr(dll,name); fn.restype = result; fn.argtypes = args; return fn
        instance = bind(kernel,'GetModuleHandleW',wt.HMODULE,[wt.LPCWSTR])(None)
        register = bind(user,'RegisterClassW',wt.ATOM,[ctypes.POINTER(WNDCLASS)])
        create = bind(user,'CreateWindowExW',wt.HWND,[wt.DWORD,wt.LPCWSTR,wt.LPCWSTR,wt.DWORD,
                      ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wt.HWND,wt.HMENU,wt.HINSTANCE,ctypes.c_void_p])
        default = bind(user,'DefWindowProcW',ctypes.c_ssize_t,[wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM])
        peek_message = bind(user,'PeekMessageW',wt.BOOL,[ctypes.POINTER(wt.MSG),wt.HWND,wt.UINT,wt.UINT,wt.UINT])
        translate = bind(user,'TranslateMessage',wt.BOOL,[ctypes.POINTER(wt.MSG)])
        dispatch = bind(user,'DispatchMessageW',ctypes.c_ssize_t,[ctypes.POINTER(wt.MSG)])
        destroy = bind(user,'DestroyWindow',wt.BOOL,[wt.HWND])
        unregister = bind(user,'UnregisterClassW',wt.BOOL,[wt.LPCWSTR,wt.HINSTANCE])
        name = 'FoundryOwnedFixture' + uuid.uuid4().hex
        ready, done, stop = threading.Event(), threading.Event(), threading.Event()
        lifetime_lock = threading.Lock()
        state = {'window':None, 'error':None, 'events':[], 'destroyed':True, 'unregistered':True}
        @WNDPROC
        def callback(hwnd, message, wparam, lparam):
            if message in (int(Message.LEFT_DOWN),int(Message.LEFT_UP)):
                state['events'].append((message,int(wparam),int(lparam)))
                if message == int(Message.LEFT_UP): stop.set()
                return 0
            return default(hwnd,message,wparam,lparam)
        def owner():
            hwnd = None
            registered = False
            try:
                cls = WNDCLASS(0,callback,0,0,instance,None,None,None,None,name)
                if not register(ctypes.byref(cls)): raise OSError('Cannot register fixture window')
                registered = True
                hwnd = create(0,name,name,0,0,0,0,0,wt.HWND(-3),None,instance,None)  # HWND_MESSAGE
                if not hwnd: raise OSError('Cannot create fixture window')
                state['window'] = int(hwnd)
                ready.set()
                msg = wt.MSG()
                while not stop.is_set():
                    while peek_message(ctypes.byref(msg),None,0,0,1):
                        translate(ctypes.byref(msg)); dispatch(ctypes.byref(msg))
                    stop.wait(.01)
            except Exception as error:
                state['error'] = error
                ready.set()
            finally:
                with lifetime_lock:
                    if hwnd: state['destroyed'] = bool(destroy(hwnd))
                    if registered: state['unregistered'] = bool(unregister(name,instance))
                    done.set()
        thread = threading.Thread(target=owner,daemon=True)
        thread.start()
        try:
            self.assertTrue(ready.wait(5), 'Owned window did not start')
            if state['error']: raise state['error']
            packets = encode_click(Point(20,30),ClientBounds(500,500))
            native = _NativeApi()
            with lifetime_lock:
                self.assertFalse(done.is_set(), 'Owned window already stopped')
                for packet in packets:
                    self.assertIs(native.post(state['window'],packet), True)
            self.assertTrue(done.wait(5), 'Owned window did not receive both messages')
            self.assertEqual([(int(p.message),p.wparam,p.lparam) for p in packets],state['events'])
        finally:
            stop.set()
            thread.join(timeout=5)
            if thread.is_alive() or not done.is_set() or not state['destroyed'] or not state['unregistered']:
                raise AssertionError('Owned fixture window or thread cleanup is unresolved')


if __name__ == '__main__': unittest.main()
