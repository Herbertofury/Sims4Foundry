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
        get_message = bind(user,'GetMessageW',ctypes.c_int,[ctypes.POINTER(wt.MSG),wt.HWND,wt.UINT,wt.UINT])
        translate = bind(user,'TranslateMessage',wt.BOOL,[ctypes.POINTER(wt.MSG)])
        dispatch = bind(user,'DispatchMessageW',ctypes.c_ssize_t,[ctypes.POINTER(wt.MSG)])
        quit_message = bind(user,'PostQuitMessage',None,[ctypes.c_int])
        destroy = bind(user,'DestroyWindow',wt.BOOL,[wt.HWND])
        unregister = bind(user,'UnregisterClassW',wt.BOOL,[wt.LPCWSTR,wt.HINSTANCE])
        post = bind(user,'PostMessageW',wt.BOOL,[wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM])
        name = 'FoundryOwnedFixture' + uuid.uuid4().hex
        ready, done = threading.Event(), threading.Event()
        state = {'window':None, 'error':None, 'events':[]}
        @WNDPROC
        def callback(hwnd, message, wparam, lparam):
            if message in (int(Message.LEFT_DOWN),int(Message.LEFT_UP)):
                state['events'].append((message,int(wparam),int(lparam)))
                if message == int(Message.LEFT_UP): quit_message(0)
                return 0
            if message == 0x0010:  # WM_CLOSE only for this owned fixture
                quit_message(0)
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
                while get_message(ctypes.byref(msg),None,0,0) > 0:
                    translate(ctypes.byref(msg)); dispatch(ctypes.byref(msg))
            except Exception as error:
                state['error'] = error
                ready.set()
            finally:
                if hwnd: destroy(hwnd)
                if registered: unregister(name,instance)
                done.set()
        thread = threading.Thread(target=owner,daemon=True)
        thread.start()
        try:
            self.assertTrue(ready.wait(5), 'Owned window did not start')
            if state['error']: raise state['error']
            packets = encode_click(Point(20,30),ClientBounds(500,500))
            native = _NativeApi()
            for packet in packets:
                self.assertIs(native.post(state['window'],packet), True)
            self.assertTrue(done.wait(5), 'Owned window did not receive both messages')
            self.assertEqual([(int(p.message),p.wparam,p.lparam) for p in packets],state['events'])
        finally:
            if not done.is_set() and state['window']:
                post(state['window'],0x0010,0,0)
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), 'Owned fixture window did not stop')


if __name__ == '__main__': unittest.main()
