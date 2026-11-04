"""Fake-native fault coverage plus explicitly gated disposable Windows fixtures."""
from contextlib import ExitStack
from copy import deepcopy
import ctypes
from ctypes import wintypes as wt
import os
from pathlib import Path
import threading
import time
import traceback
import unittest
from unittest import mock

from sims4foundry_cli import native_input as native
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.guarded_input import run_packets
from sims4foundry_cli.input_packets import (Point, ClientBounds, Message, NavigationKey, ScanCode,
                                          encode_click, encode_drag, encode_key_press, encode_wheel)
import test_windows_native_fixture as fixture


def identity():
    return {"schema": 1, "kind": "foundry_windows_identity", "pid": 42, "creation_filetime": 123,
            "executable": "C:\\fixture\\TS4_x64.exe", "executable_sha256": "a" * 64,
            "hwnd": 100, "thread_id": 200, "window_class": "Canvas-Fixture", "client_width": 500,
            "client_height": 500, "client_origin": [0, 0], "dpi": 96, "integrity": 8192}


class FakePolicy(native._FixturePolicy):
    def __init__(self):
        self.window, self.recorder = object(), object()
        self.sequence = 0
        self.admitted = False
        self.fail_recording = False
        self.fail_window = False
    def sample(self):
        self.sequence += 1
        return native._RecorderSample(self.recorder, self.sequence, time.monotonic())
    def admit(self, expected, packets):
        self.admitted = True
        return native._FixtureTicket(self.window, self.sample())
    def window_epoch(self):
        if self.fail_window: raise RuntimeError("PRIVATE WINDOW DETAILS")
        return self.window
    def recorder_sample(self):
        if self.fail_recording: raise RuntimeError("PRIVATE RECORDER DETAILS")
        return self.sample()


class FakeApi:
    def __init__(self):
        self.state = identity()
        self.focus, self.hkl, self.scan = 12, 409, 1
        self.levels = {"self": 8192, "process": 8192}
        self.physical = set()
        self.posts, self.closed = [], []
        self.post_effect = None
        self.owner_fail = self.input_fail = self.geometry_fail = False
        self.pin_count = 0
    def pin_image(self, expected): self.pin_count += 1; return "image"
    def close(self, handle): self.closed.append(handle)
    def open_target(self, pid): return "process"
    def current_security_context(self): return ("S-1-fixture", 1)
    def _current_process(self): return "self"
    def integrity(self, process): return self.levels[process]
    def ownership(self, process, expected, security):
        if self.owner_fail: raise RuntimeError("PRIVATE PROCESS DETAILS")
        return {k: deepcopy(self.state[k]) for k in native.OWNED_KEYS}
    def window_owner(self, expected):
        if self.owner_fail or any(self.state[k] != expected[k] for k in ("pid", "hwnd", "thread_id", "window_class")):
            raise RuntimeError("PRIVATE WINDOW DETAILS")
    def geometry(self, expected):
        if self.geometry_fail: raise RuntimeError("PRIVATE GEOMETRY DETAILS")
        return {k: deepcopy(self.state[k]) for k in ("client_width", "client_height", "client_origin", "dpi")}
    def foreground(self): return self.focus
    def layout(self, thread): return self.hkl
    def mapping(self, key, hkl): return self.scan
    def input_observable(self, expected, foreground, integrity):
        if self.input_fail: raise RuntimeError("PRIVATE DESKTOP DETAILS")
    def key_down(self, key): return key in self.physical
    def post(self, hwnd, packet):
        self.posts.append((hwnd, packet))
        return self.post_effect(packet) if self.post_effect else True


class NativeBackendTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.api, self.policy = FakeApi(), FakePolicy()
        self.lease = mock.Mock()
        self.lease.__enter__ = mock.Mock(return_value=self.lease)
        self.lease.__exit__ = mock.Mock(return_value=False)
        self.stack.enter_context(mock.patch.object(native.os, "name", "nt"))
        self.stack.enter_context(mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}))
        self.factory = self.stack.enter_context(mock.patch.object(native, "_NativeApi", return_value=self.api))
        self.lease_factory = self.stack.enter_context(mock.patch.object(native.leases, "process_lease", return_value=self.lease))
        self.stack.enter_context(mock.patch.object(native.time, "sleep", return_value=None))
        self.packets = encode_click(Point(20, 30), ClientBounds(500, 500))
        self.journal = []
    def backend(self, packets=None):
        return native._fixture_backend(identity(), packets or self.packets, self.policy)
    def run_sequence(self, packets=None):
        packets = packets or self.packets
        with self.backend(packets) as backend:
            result = run_packets(identity(), packets, backend, self.journal.append)
        return result, backend
    def assert_code(self, code, operation):
        with self.assertRaises(FoundryError) as caught: operation()
        self.assertEqual(code, caught.exception.code)
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertNotIn("PRIVATE", "".join(traceback.format_exception(caught.exception)))
    def test_public_entry_is_always_denied_without_native_calls(self):
        for value in (identity(), dict(identity(), qualified=True, approved=True), True, None):
            self.assert_code("input_not_admitted", lambda: native.native_packet_backend(value))
        self.factory.assert_not_called(); self.lease_factory.assert_not_called()
    def test_constructor_and_import_do_not_load_dlls_or_acquire(self):
        self.backend()
        self.factory.assert_not_called(); self.lease_factory.assert_not_called()
    def test_admission_rejects_booleans_json_plain_callbacks_and_default_policy(self):
        for policy in (True, {}, lambda: True, native._FixturePolicy()):
            self.assert_code("input_not_admitted", lambda: native._fixture_backend(identity(), self.packets, policy))
        for result in (True, False, {}, None):
            self.policy.admit = lambda *args: result
            self.assert_code("input_not_admitted", lambda: self.backend().__enter__())
        self.factory.assert_not_called(); self.lease_factory.assert_not_called()
    def test_policy_failure_before_native_is_sanitized(self):
        self.policy.admit = mock.Mock(side_effect=RuntimeError("PRIVATE ADMISSION DETAILS"))
        self.assert_code("fixture_policy_failed", lambda: self.backend().__enter__())
        self.factory.assert_not_called()
    def test_non_windows_and_non_ci_refuse_without_admission(self):
        for platform, ci, code in (("posix", "true", "windows_required"), ("nt", "false", "input_not_admitted")):
            with mock.patch.object(native.os, "name", platform), mock.patch.dict(os.environ, {"GITHUB_ACTIONS": ci}):
                self.assert_code(code, lambda: self.backend().__enter__())
        self.assertFalse(self.policy.admitted); self.factory.assert_not_called()
    def test_exact_click_acceptance_is_not_game_qualification(self):
        result, backend = self.run_sequence()
        self.assertTrue(result["queue_accepted"])
        self.assertFalse(result["semantic_success"]); self.assertFalse(result["game_qualified"])
        self.assertFalse(backend.unresolved)
        self.assertEqual([Message.LEFT_DOWN, Message.LEFT_UP], [p.message for _, p in self.api.posts])
        self.assertEqual(1, self.api.pin_count)
        self.assertEqual(["process", "image"], self.api.closed)
        self.lease.__exit__.assert_called_once()
    def test_bound_target_and_packet_mutation_are_detached(self):
        target, packets = identity(), self.packets
        backend = native._fixture_backend(target, packets, self.policy)
        target["hwnd"] = 999; target["client_origin"][0] = 99
        object.__setattr__(packets[0], "lparam", 0)
        with backend:
            self.assertEqual(100, backend.observe()["hwnd"])
            self.assert_code("invalid_sequence", lambda: backend.post(100, packets[0]))
        self.assertEqual([], self.api.posts)
    def test_admission_receives_detached_packet_objects(self):
        original = self.policy.admit
        def admit(expected, packets):
            ticket = original(expected, packets)
            expected["hwnd"] = 999
            object.__setattr__(packets[0], "lparam", 0)
            return ticket
        self.policy.admit = admit
        self.assertTrue(self.run_sequence()[0]["ok"])
    def test_forged_packet_and_arbitrary_hwnd_refused(self):
        with self.backend() as backend:
            self.assert_code("invalid_packet", lambda: backend.post(100, {}))
            for hwnd in (0, 0xffff, 101, True):
                self.assert_code("ownership_changed", lambda: backend.post(hwnd, self.packets[0]))
            forged = deepcopy(self.packets[0]); object.__setattr__(forged, "message", 0x666)
            self.assert_code("invalid_packet", lambda: backend.post(100, forged))
        self.assertEqual([], self.api.posts)
    def test_release_without_down_move_out_of_order_and_replay_refused(self):
        with self.backend() as backend:
            self.assert_code("invalid_release", lambda: backend.post(100, self.packets[1]))
            self.assertTrue(backend.post(100, self.packets[0]))
            self.assert_code("invalid_sequence", lambda: backend.post(100, self.packets[0]))
            self.assertTrue(backend.post(100, self.packets[1]))
            self.assert_code("invalid_release", lambda: backend.post(100, self.packets[1]))
        self.assertEqual(2, len(self.api.posts))
    def test_single_use_and_cross_thread_rejected_before_native(self):
        backend = self.backend()
        with backend:
            with mock.patch.object(native.threading, "current_thread", return_value=object()):
                self.assert_code("input_thread", backend.observe)
                self.assert_code("input_thread", lambda: backend.__exit__(None,None,None))
            self.assertEqual(100, backend.observe()["hwnd"])
        self.assert_code("input_context", backend.__enter__)
    def test_lost_lease_blocks_posts(self):
        with self.backend() as backend:
            self.lease._check_context.side_effect = RuntimeError("PRIVATE LEASE DETAILS")
            self.assert_code("input_context", lambda: backend.post(100, self.packets[0]))
            self.lease._check_context.side_effect = None
        self.assertEqual([], self.api.posts)
    def test_live_identity_integrity_geometry_and_window_epoch_races_refuse_down(self):
        changes = [lambda: self.api.state.update(pid=43), lambda: self.api.state.update(thread_id=201),
                   lambda: self.api.state.update(window_class="Canvas-Other"),
                   lambda: self.api.state.update(creation_filetime=124), lambda: self.api.state.update(client_width=499),
                   lambda: self.api.levels.update(process=4096), lambda: setattr(self.policy, "window", object()),
                   lambda: setattr(self.api, "focus", 15)]
        for change in changes:
            self.api.state = identity(); self.api.levels["process"] = 8192; self.api.focus=12
            with self.backend() as backend:
                change()
                with self.assertRaises(FoundryError): backend.post(100, self.packets[0])
            self.assertEqual([], self.api.posts)
    def test_recorder_unknown_stale_regressed_forged_and_changed_epoch_refuse(self):
        with self.backend() as backend:
            prior = backend._sample
            bad = (True, None, native._RecorderSample(object(), 100, time.monotonic()),
                   native._RecorderSample(prior.epoch, True, time.monotonic()),
                   native._RecorderSample(prior.epoch, prior.sequence-1, time.monotonic()),
                   native._RecorderSample(prior.epoch, prior.sequence, prior.captured_at+.1),
                   native._RecorderSample(prior.epoch, prior.sequence+1, time.monotonic()-2),
                   native._RecorderSample(prior.epoch, prior.sequence+1, float("nan")))
            for sample in bad:
                self.policy.recorder_sample = lambda: sample
                self.assert_code("recording_unverified", backend.verify_recording)
        self.assertEqual([], self.api.posts)
    def test_recorder_must_advance_from_admission(self):
        self.policy.recorder_sample = lambda: native._RecorderSample(self.policy.recorder, self.policy.sequence, 0)
        self.assert_code("input_preparation_failed", lambda: self.backend().__enter__())
        self.assertEqual([], self.api.posts)
    def test_post_rechecks_recorder_after_runner_checks(self):
        with self.backend() as backend:
            self.policy.fail_recording = True
            self.assert_code("fixture_policy_failed", lambda: backend.post(100, self.packets[0]))
        self.assertEqual([], self.api.posts)
    def test_layout_scan_e0_e1_and_mapping_failure(self):
        packets = encode_key_press(NavigationKey.ESCAPE, ScanCode(1,False))
        for scan in (0, 2, 0xe001, 0xe101, 0x10001, True):
            self.api.scan = scan
            with self.backend(packets) as backend:
                self.assert_code("input_context_unverified", lambda: backend.post(100, packets[0]))
        self.api.scan = 0xe04b
        packets = encode_key_press(NavigationKey.LEFT, ScanCode(0x4b,True))
        self.assertTrue(self.run_sequence(packets)[0]["ok"])
    def test_layout_changed_between_samples_and_physical_key_refuse(self):
        packets = encode_key_press(NavigationKey.ESCAPE, ScanCode(1,False))
        with self.backend(packets) as backend:
            self.api.hkl += 1
            self.assert_code("input_context_unverified", lambda: backend.verify_input_context(packets[0]))
            self.api.hkl -= 1
            self.api.physical.add(int(NavigationKey.ESCAPE))
            self.assert_code("input_context_unverified", lambda: backend.verify_input_context(packets[0]))
        self.assertEqual([], self.api.posts)
    def test_modifiers_buttons_unknown_observability_and_boolean_shape(self):
        with self.backend() as backend:
            for key in (0x10,0x11,0x12,0x5b,0x5c,1,2,4,5,6):
                self.api.physical = {key}
                self.assert_code("input_context_unverified", lambda: backend.verify_input_context(self.packets[0]))
            self.api.physical=set(); self.api.input_fail=True
            self.assert_code("native_observation_failed", lambda: backend.verify_input_context(self.packets[0]))
        self.assertEqual([], self.api.posts)
    def test_exact_mouse_shift_control_flags_are_checked(self):
        self.api.physical={0x10,0x11}
        packets=encode_click(Point(20,30),ClientBounds(500,500),shift=True,control=True)
        self.assertTrue(self.run_sequence(packets)[0]["ok"])
    def test_geometry_recorder_foreground_layout_failures_do_not_block_original_up(self):
        def change(packet):
            if packet.message is Message.LEFT_DOWN:
                self.api.geometry_fail=True; self.policy.fail_recording=True; self.api.focus=99; self.api.hkl=999
            return True
        self.api.post_effect=change
        result, backend=self.run_sequence()
        self.assertFalse(result["ok"])
        self.assertEqual([Message.LEFT_DOWN,Message.LEFT_UP],[p.message for _,p in self.api.posts])
        self.assertFalse(backend._held)
    def test_ownership_or_integrity_loss_never_releases_to_replacement(self):
        for change in (lambda: setattr(self.api,"owner_fail",True), lambda: self.api.levels.update(process=4096),
                       lambda: setattr(self.policy,"window",object())):
            self.api.owner_fail=False; self.api.levels["process"]=8192; self.api.posts=[]
            self.api.post_effect=lambda packet: (change() or True) if packet.message is Message.LEFT_DOWN else True
            backend=self.backend()
            with self.assertRaises(FoundryError):
                with backend: result=run_packets(identity(),self.packets,backend,self.journal.append)
            self.assertEqual([Message.LEFT_DOWN],[p.message for _,p in self.api.posts])
            self.assertTrue(backend.unresolved)
            self.assertIsNotNone(result["release_error"])
    def test_explicit_down_refusal_is_not_unknown_acceptance(self):
        self.api.post_effect=lambda packet: False
        result,backend=self.run_sequence()
        self.assertFalse(result["ok"]); self.assertTrue(backend.unresolved)
        self.assertEqual(1,len(self.api.posts)); self.assertFalse(backend._held)
    def test_unknown_down_returns_release_once_and_sanitize_exception(self):
        for outcome in (None, 1, RuntimeError("PRIVATE POST DETAILS"), KeyboardInterrupt()):
            self.api.posts=[]
            def send(packet):
                if packet.release_intent: return True
                if isinstance(outcome,BaseException): raise outcome
                return outcome
            self.api.post_effect=send
            result,backend=self.run_sequence()
            self.assertFalse(result["ok"]); self.assertTrue(backend.unresolved)
            self.assertEqual(2,len(self.api.posts)); self.assertNotIn("PRIVATE",str(result))
    def test_failed_up_is_consumed_not_retried_or_cleared(self):
        self.api.post_effect=lambda packet: not packet.release_intent
        backend=self.backend()
        with self.assertRaises(FoundryError):
            with backend:
                result=run_packets(identity(),self.packets,backend,self.journal.append)
                self.assert_code("invalid_release",lambda:backend.post(100,self.packets[1]))
        self.assertEqual(2,len(self.api.posts)); self.assertTrue(backend.unresolved)
        self.assertIsNotNone(result["release_error"])
    def test_drag_failed_move_releases_at_last_attempted_point(self):
        packets=encode_drag((Point(1,2),Point(3,4),Point(5,6)),ClientBounds(500,500),duration_ms=10)
        def send(packet):
            if packet.message is Message.MOUSE_MOVE: raise RuntimeError("PRIVATE LOST MOVE")
            return True
        self.api.post_effect=send
        result,backend=self.run_sequence(packets)
        self.assertFalse(result["ok"])
        self.assertEqual([Message.LEFT_DOWN,Message.MOUSE_MOVE,Message.LEFT_UP],[p.message for _,p in self.api.posts])
        self.assertEqual(packets[1].lparam,self.api.posts[-1][1].lparam)
    def test_sleep_validation_and_interruption_preserve_release(self):
        with self.backend() as backend:
            for value in (-1,6,True,float("nan"),float("inf")):
                self.assert_code("invalid_timing",lambda:backend.sleep(value))
        with mock.patch.object(native.time,"sleep",side_effect=KeyboardInterrupt()):
            result,_=self.run_sequence()
        self.assertFalse(result["ok"]); self.assertEqual(2,len(self.api.posts))
    def test_cleanup_failure_is_sanitized_and_uncertainty_retained(self):
        backend=self.backend()
        self.api.close=mock.Mock(wraps=self.api.close)
        with self.assertRaises(FoundryError) as caught:
            with backend: self.api.close.side_effect=RuntimeError("PRIVATE CLOSE DETAILS")
        self.assertEqual("input_cleanup_unresolved",caught.exception.code)
        self.assertNotIn("PRIVATE",str(caught.exception)); self.assertTrue(backend.unresolved)


class NativeBindingTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.kernel,self.advapi,self.user=mock.Mock(),mock.Mock(),mock.Mock()
        self.stack.enter_context(mock.patch.object(native.os,"name","nt"))
        self.stack.enter_context(mock.patch.object(ctypes,"WinDLL",create=True,
            side_effect=[self.kernel,self.advapi,self.kernel,self.user]))
        self.stack.enter_context(mock.patch.object(ctypes,"get_last_error",create=True,return_value=122))
        self.api=native._NativeApi()
        self.kernel.GetDriveTypeW.return_value=3
    def test_only_postmessage_pointer_sized_binding_and_no_forbidden_apis(self):
        self.assertEqual([wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM],self.user.PostMessageW.argtypes)
        packet=encode_click(Point(20,30),ClientBounds(500,500))[0]
        self.user.PostMessageW.return_value=0
        self.assertFalse(self.api.post(100,packet))
        self.user.PostMessageW.assert_called_once_with(100,int(packet.message),packet.wparam,packet.lparam)
        forbidden=("SendInput","SendMessageW","SetForegroundWindow","SetCursorPos","AttachThreadInput","ChangeWindowMessageFilterEx")
        for name in forbidden: self.assertNotIn(name,self.user._mock_children)
    def test_integrity_sid_pointer_and_native_failures_are_bounded(self):
        def open_token(process,access,output):
            ctypes.cast(output,ctypes.POINTER(wt.HANDLE))[0]=700
            return 1
        def query(token,kind,output,size,required):
            ctypes.cast(required,ctypes.POINTER(wt.DWORD))[0]=64
            if output is None: return 0
            start=ctypes.addressof(output)
            sid=start+24
            ctypes.cast(output,ctypes.POINTER(ctypes.c_void_p))[0]=sid
            ctypes.memmove(sid,b"\x01\x01\x00\x00\x00\x00\x00\x10\x00\x20\x00\x00",12)
            return 1
        self.advapi.OpenProcessToken.side_effect=open_token
        self.advapi.GetTokenInformation.side_effect=query
        self.assertEqual(8192,self.api.integrity(100))
        original=query
        for pointer in (None,1,2**63):
            def broken(token,kind,output,size,required):
                result=original(token,kind,output,size,required)
                if output is not None: ctypes.cast(output,ctypes.POINTER(ctypes.c_void_p))[0]=pointer
                return result
            self.advapi.GetTokenInformation.side_effect=broken
            with self.assertRaises(FoundryError): self.api.integrity(100)
        self.assertEqual(4,self.kernel.CloseHandle.call_count)
    def test_native_ownership_checks_retained_lifetime_scope_image_and_ui_owner(self):
        api=self.api
        api.reject_impersonation=mock.Mock()
        api.validate_lifetime=mock.Mock()
        api.current_security_context=mock.Mock(return_value=("sid",1))
        api.security_context=mock.Mock(return_value=("sid",1))
        def image(process,flags,name,length):
            name.value=identity()["executable"]
            ctypes.cast(length,ctypes.POINTER(wt.DWORD))[0]=len(name.value)
            return 1
        def window(hwnd,pid):
            ctypes.cast(pid,ctypes.POINTER(wt.DWORD))[0]=42
            return 200
        def cls(hwnd,text,size):
            text.value="Canvas-Fixture"
            return len(text.value)
        self.kernel.QueryFullProcessImageNameW.side_effect=image
        self.user.GetWindowThreadProcessId.side_effect=window
        self.user.GetClassNameW.side_effect=cls
        self.assertEqual({k:identity()[k] for k in native.OWNED_KEYS},api.ownership(100,identity(),("sid",1)))
        self.assertEqual(2,api.validate_lifetime.call_count)
        api.security_context.return_value=("sid",2)
        with self.assertRaises(FoundryError): api.ownership(100,identity(),("sid",1))
        api.security_context.return_value=("sid",1)
        self.user.GetWindowThreadProcessId.side_effect=None
        self.user.GetWindowThreadProcessId.return_value=0
        with self.assertRaises(FoundryError): api.ownership(100,identity(),("sid",1))
    def test_async_zero_requires_active_desktop_access_and_foreground_integrity(self):
        self.kernel.GetCurrentThreadId.return_value=123
        self.user.GetThreadDesktop.return_value=700
        self.user.OpenInputDesktop.return_value=701
        self.user.CloseDesktop.return_value=1
        def info(desktop,kind,output,size,needed):
            self.assertEqual(6,kind)
            ctypes.cast(output,ctypes.POINTER(wt.BOOL))[0]=1
            ctypes.cast(needed,ctypes.POINTER(wt.DWORD))[0]=ctypes.sizeof(wt.BOOL)
            return 1
        def owner(hwnd,output):
            ctypes.cast(output,ctypes.POINTER(wt.DWORD))[0]=72
            return 500
        self.user.GetUserObjectInformationW.side_effect=info
        self.user.GetWindowThreadProcessId.side_effect=owner
        self.api.open_target=mock.Mock(return_value=702)
        self.api.integrity=mock.Mock(return_value=8192)
        self.api.input_observable(identity(),12,8192)
        self.user.OpenInputDesktop.assert_called_once_with(0,False,0x19)
        self.user.CloseDesktop.assert_called_once_with(701)
        self.kernel.CloseHandle.assert_called_once_with(702)
        self.api.integrity.return_value=12288
        with self.assertRaises(FoundryError): self.api.input_observable(identity(),12,8192)
        self.user.GetUserObjectInformationW.side_effect=None
        self.user.GetUserObjectInformationW.return_value=0
        with self.assertRaises(FoundryError): self.api.input_observable(identity(),12,8192)
    def test_image_pin_rejects_network_device_reparse_and_short_read(self):
        self.kernel.CreateFileW.return_value=123
        self.kernel.GetFileType.return_value=1
        for path in (r"\\server\share\TS4_x64.exe",r"\\?\C:\TS4_x64.exe",r"C:\a\..\TS4_x64.exe"):
            with self.assertRaises(FoundryError): self.api.pin_image(dict(identity(),executable=path))
        self.kernel.CreateFileW.assert_not_called()
        for attributes,size in ((0x400,3),(0x10,3),(0,0),(0,native.MAX_IMAGE_BYTES+1)):
            def info(handle,output):
                value=ctypes.cast(output,ctypes.POINTER(native._FileInfo)).contents
                value.attributes=attributes; value.size_low=size
                return 1
            self.kernel.GetFileInformationByHandle.side_effect=info
            with self.assertRaises(FoundryError): self.api.pin_image(identity())
        self.kernel.ReadFile.assert_not_called()

    def test_native_packet_validation_precedes_binding(self):
        for hwnd,packet in ((0,self._click()),(0xffff,self._click()),(True,self._click()),(100,{})):
            with self.assertRaises(FoundryError): self.api.post(hwnd,packet)
        self.user.PostMessageW.assert_not_called()
    @staticmethod
    def _click(): return encode_click(Point(1,1),ClientBounds(10,10))[0]
    def test_mapping_preserves_e0_and_queries_selected_thread(self):
        self.user.GetKeyboardLayout.return_value=409
        self.user.MapVirtualKeyExW.return_value=0xe04b
        self.assertEqual(409,self.api.layout(200))
        self.assertEqual(0xe04b,self.api.mapping(0x25,409))
        self.user.GetKeyboardLayout.assert_called_once_with(200)
        self.user.MapVirtualKeyExW.assert_called_once_with(0x25,4,409)
    def test_only_high_async_bit_is_used(self):
        for value,expected in ((0,False),(1,False),(0x8000,True),(-32767,True)):
            self.user.GetAsyncKeyState.return_value=value
            self.assertIs(expected,self.api.key_down(0x10))
    def test_file_open_readonly_noninherited_deny_write_delete_and_hash_failure_closes(self):
        self.kernel.CreateFileW.return_value=123
        self.kernel.GetFileType.return_value=1
        def info(handle,output):
            value=ctypes.cast(output,ctypes.POINTER(native._FileInfo)).contents
            value.size_low=3
            return 1
        def read(handle,buffer,count,output,overlapped):
            ctypes.memmove(buffer,b"abc",3)
            ctypes.cast(output,ctypes.POINTER(wt.DWORD))[0]=3
            return 1
        self.kernel.GetFileInformationByHandle.side_effect=info
        self.kernel.ReadFile.side_effect=read
        with self.assertRaises(FoundryError): self.api.pin_image(identity())
        self.kernel.CreateFileW.assert_called_once_with(identity()["executable"],0x80000000,1,None,3,0x00200000,None)
        self.kernel.CloseHandle.assert_called_once_with(123)
    def test_invalid_file_handle_never_read_or_closed(self):
        for value in (None,0,-1,ctypes.c_void_p(-1).value):
            self.kernel.CreateFileW.return_value=value
            with self.assertRaises(FoundryError): self.api.pin_image(identity())
        self.kernel.ReadFile.assert_not_called(); self.kernel.CloseHandle.assert_not_called()


_NATIVE_CI=os.name=="nt" and os.environ.get("GITHUB_ACTIONS")=="true"


@unittest.skipUnless(_NATIVE_CI,"Native backend fixture is restricted to disposable GitHub Windows runners")
class NativePacketFixtureTests(unittest.TestCase):
    # Reuse helpers, not TestCase inheritance/import, avoiding duplicate tests.
    setUpClass=classmethod(fixture.NativeFixtureTests.setUpClass.__func__)
    start_fixture=fixture.NativeFixtureTests.start_fixture
    def _exercise_packet_kind(self, kind):
        from sims4foundry_cli.windows import inspect
        process,hwnd=self.start_fixture("native-backend-"+kind)
        expected=inspect(process.pid,self.exe,self.checksum,hwnd)
        stop=threading.Event(); recorder={"sequence":0,"at":time.monotonic()}
        lock=threading.Lock()
        def record():
            # Independent synthetic recorder heartbeat, NOT WGC/game evidence.
            while not stop.wait(.005):
                with lock:
                    recorder["sequence"]+=1; recorder["at"]=time.monotonic()
        worker=threading.Thread(target=record,daemon=True); worker.start()
        def stop_recorder():
            stop.set()
            worker.join(1)
            self.assertFalse(worker.is_alive(), "Owned synthetic recorder did not stop within one second")
        self.addCleanup(stop_recorder)
        class Policy(native._FixturePolicy):
            def __init__(self): self.epoch=object(); self.recording=object()
            def admit(policy,target,packets):
                if target!=expected or process.poll() is not None: raise RuntimeError("not owned fixture")
                # This process was just compiled/launched by this test and has no prior dispatch.
                initial=policy.recorder_sample()
                deadline=time.monotonic()+.2
                while policy.recorder_sample().sequence<=initial.sequence and time.monotonic()<deadline:
                    time.sleep(.001)
                return native._FixtureTicket(policy.epoch,initial)
            def window_epoch(policy):
                # The fixed fixture source creates once and never recreates a window.
                if process.poll() is not None: raise RuntimeError("fixture gone")
                return policy.epoch
            def recorder_sample(policy):
                with lock: return native._RecorderSample(policy.recording,recorder["sequence"],recorder["at"])
        bounds=ClientBounds(expected["client_width"],expected["client_height"])
        if kind=="click": packets=encode_click(Point(20,20),bounds)
        elif kind=="key": packets=encode_key_press(NavigationKey.ESCAPE,ScanCode(1,False))
        elif kind=="wheel":
            x,y=expected["client_origin"]
            packets=(encode_wheel(Point(x+20,y+20),-120),)
        else: packets=encode_drag((Point(10,10),Point(15,15),Point(20,20)),bounds,duration_ms=40)
        baseline=self.events_path.read_text().splitlines()
        user=ctypes.WinDLL("user32",use_last_error=True)
        user.GetForegroundWindow.restype=wt.HWND
        user.GetCursorPos.argtypes=[ctypes.POINTER(wt.POINT)]; user.GetCursorPos.restype=wt.BOOL
        foreground=user.GetForegroundWindow(); before=wt.POINT()
        self.assertTrue(user.GetCursorPos(ctypes.byref(before)))
        with native._fixture_backend(expected,packets,Policy()) as backend:
            result=run_packets(expected,packets,backend,lambda record: None)
        self.assertTrue(result["queue_accepted"]); self.assertFalse(result["game_qualified"])
        deadline=time.monotonic()+5
        rows=[]
        while time.monotonic()<deadline:
            rows=self.events_path.read_text().splitlines()[len(baseline):]
            if len(rows)>=len(packets): break
            time.sleep(.01)
        self.assertEqual([(int(p.message),p.wparam,p.lparam) for p in packets],
                         [tuple(int(v) for v in row.split(",")) for row in rows])
        self.assertEqual(foreground,user.GetForegroundWindow())
        after=wt.POINT(); self.assertTrue(user.GetCursorPos(ctypes.byref(after)))
        self.assertEqual((before.x,before.y),(after.x,after.y))


    def test_click_backend_and_runner_on_new_synthetic_window(self):
        self._exercise_packet_kind("click")
    def test_key_backend_and_runner_on_new_synthetic_window(self):
        self._exercise_packet_kind("key")
    def test_wheel_backend_and_runner_on_new_synthetic_window(self):
        self._exercise_packet_kind("wheel")
    def test_drag_backend_and_runner_on_new_synthetic_window(self):
        self._exercise_packet_kind("drag")


if __name__=="__main__": unittest.main()
