"""Real Win32 binding tests on GitHub's disposable Windows runner, not Sims.

The fixture is compiled from the C# source below into a new temporary directory.
Its Sims-like image/class names only exercise strict identity checks. It is never
run on the user's desktop or treated as real gameplay/qualification evidence.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import sha256
from sims4foundry_cli.windows import inspect

CS = r'''
using System;
using System.IO;
using System.Runtime.InteropServices;
class Fixture {
    delegate IntPtr Proc(IntPtr h, uint m, IntPtr w, IntPtr l);
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    struct WC { public uint style; public Proc proc; public int cls; public int wnd;
        public IntPtr instance, icon, cursor, brush; public string menu, name; }
    [StructLayout(LayoutKind.Sequential)]
    struct POINT { public int x,y; }
    [StructLayout(LayoutKind.Sequential)]
    struct MSG { public IntPtr hwnd; public uint message; public UIntPtr wp; public IntPtr lp;
        public uint time; public POINT pt; public uint extra; }
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern ushort RegisterClassW(ref WC c);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern IntPtr CreateWindowExW(uint ex, string c,
        string title, uint style, int x,int y,int w,int h,IntPtr parent,IntPtr menu,IntPtr instance,IntPtr param);
    [DllImport("user32.dll")] static extern IntPtr DefWindowProcW(IntPtr h,uint m,IntPtr w,IntPtr l);
    [DllImport("user32.dll")] static extern int GetMessageW(out MSG m,IntPtr h,uint min,uint max);
    [DllImport("user32.dll")] static extern bool TranslateMessage(ref MSG m);
    [DllImport("user32.dll")] static extern IntPtr DispatchMessageW(ref MSG m);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h,int n);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern IntPtr GetModuleHandleW(string name);
    static Proc proc = DefWindowProcW;
    static void Main(string[] args) {
        var instance = GetModuleHandleW(null);
        var c = new WC { proc=proc, instance=instance, name="Canvas-FoundryDisposableFixture" };
        if (RegisterClassW(ref c)==0) Environment.Exit(10);
        var h = CreateWindowExW(0,c.name,"Foundry synthetic fixture",0x10CF0000,0,0,320,240,
            IntPtr.Zero,IntPtr.Zero,instance,IntPtr.Zero);
        if (h==IntPtr.Zero) Environment.Exit(11);
        if(args[1]=="double") CreateWindowExW(0,c.name,"Second synthetic fixture",0x10CF0000,50,50,320,240,
            IntPtr.Zero,IntPtr.Zero,instance,IntPtr.Zero);
        if(args[1]=="minimized") ShowWindow(h,6);
        File.WriteAllText(args[0],h.ToInt64().ToString());
        MSG msg;
        while(GetMessageW(out msg,IntPtr.Zero,0,0)>0) { TranslateMessage(ref msg); DispatchMessageW(ref msg); }
    }
}
'''


@unittest.skipUnless(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true",
                     "Native fixture is restricted to disposable GitHub Windows runners")
class NativeFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="foundry-native-fixture-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        source = cls.root / "Fixture.cs"
        source.write_text(CS, encoding="utf-8")
        cls.exe = cls.root / "TS4_x64.exe"
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
        if not compiler.is_file():
            raise RuntimeError("Required official .NET Framework compiler is missing from the Windows CI image")
        subprocess.run([str(compiler), "/nologo", "/target:winexe", "/out:" + str(cls.exe), str(source)],
                       check=True, capture_output=True, timeout=30)
        cls.checksum = sha256(cls.exe)

    def start_fixture(self, mode):
        ready = self.root / (mode + ".ready")
        process = subprocess.Popen([str(self.exe), str(ready), mode])
        def cleanup():
            # This exact Popen handle belongs only to our new synthetic fixture.
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(ready.exists(), "Synthetic window did not become ready")
        return process, int(ready.read_text())

    def test_exact_fixture_identity_with_real_win32_calls(self):
        process, hwnd = self.start_fixture("normal")
        value = inspect(process.pid, self.exe, self.checksum, hwnd)
        self.assertEqual(process.pid, value["pid"])
        self.assertEqual(hwnd, value["hwnd"])
        self.assertGreater(value["creation_filetime"], 0)
        self.assertGreater(value["integrity"], 0)
        self.assertEqual(self.checksum, value["executable_sha256"])
        self.assertFalse(value["input_sent"])
        self.assertFalse(value["real_game_control_ready"])

    def test_multiple_fixture_windows_require_explicit_selection(self):
        process, hwnd = self.start_fixture("double")
        with self.assertRaises(FoundryError) as caught:
            inspect(process.pid, self.exe, self.checksum)
        self.assertEqual("ambiguous_window", caught.exception.code)
        self.assertEqual(hwnd, inspect(process.pid, self.exe, self.checksum, hwnd)["hwnd"])

    def test_minimized_fixture_is_not_actionable(self):
        process, hwnd = self.start_fixture("minimized")
        with self.assertRaises(FoundryError) as caught:
            inspect(process.pid, self.exe, self.checksum, hwnd)
        self.assertEqual("ambiguous_window", caught.exception.code)
