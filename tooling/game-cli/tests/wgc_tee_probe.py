"""Opt-in owned-window experiment: correlate WGC PNG payloads with packet PTS."""
import ctypes
from fractions import Fraction
import hashlib
import subprocess
import threading
import time

from sims4foundry_cli.capture_pin import retained_capture_image
from sims4foundry_cli.input_packets import ClientBounds, Point, encode_click
from sims4foundry_cli.native_input import _NativeApi
from sims4foundry_cli.visual_probe import _pixels
from sims4foundry_cli.wgc_recorder import _read_frame

# A possibly running child retains its pin here rather than being retried or
# silently discarded if setup or cleanup acknowledgement is lost.
_UNRESOLVED = []


def run_owned(test, hwnd, state, invalidate, ffmpeg, checksum):
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    qpc,qpf = kernel.QueryPerformanceCounter,kernel.QueryPerformanceFrequency
    qpc.argtypes = [ctypes.POINTER(ctypes.c_longlong)]; qpc.restype = ctypes.c_int
    qpf.argtypes = [ctypes.POINTER(ctypes.c_longlong)]; qpf.restype = ctypes.c_int
    frequency = ctypes.c_longlong()
    test.assertTrue(qpf(ctypes.byref(frequency)))
    def counter():
        value = ctypes.c_longlong()
        test.assertTrue(qpc(ctypes.byref(value)))
        return value.value
    command = [ffmpeg,'-nostdin','-hide_banner','-loglevel','error',
               '-f','lavfi','-i',f'gfxcapture=hwnd={hwnd}:max_framerate=5:capture_cursor=0:capture_border=0:display_border=0',
               '-vf','hwdownload,format=bgra','-t','2.5','-map','0:v','-c:v','png','-threads','1',
               '-fps_mode','passthrough','-enc_time_base','filter',
               '-f','tee','[f=image2pipe]pipe:1|[f=framehash:hash=sha256]pipe:2']
    retained = {'pin':retained_capture_image(ffmpeg,checksum),'spawn_entered':False,'process':None}
    _UNRESOLVED.append(retained)
    retained['pin'].__enter__()
    retained['spawn_entered'] = True
    process = subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    retained['process'] = process
    frames,lines,errors = [],[],[]
    white_seen = threading.Event()
    def read_frames():
        try:
            total = 0
            while process.stdout.peek(1):
                raw,shape = _read_frame(process.stdout)
                total += len(raw)
                if total > 32*1024*1024 or len(frames) >= 1024:
                    raise ValueError('Owned fixture stream exceeded its bound')
                frames.append((raw,shape))
                _,pixel = _pixels(raw,(shape[0]//2,shape[1]//2,1,1))
                if state['marker'] and not state['clicked'] and all(v >= 245 for v in pixel[:3]):
                    white_seen.set()
        except BaseException as error:
            errors.append(error)
    def read_rows():
        try:
            for line in process.stderr:
                if len(lines) >= 2048: raise ValueError('Owned fixture metadata exceeded its bound')
                lines.append(line.rstrip(b'\r\n'))
        except BaseException as error:
            errors.append(error)
    workers = [threading.Thread(target=read_frames,daemon=True),threading.Thread(target=read_rows,daemon=True)]
    for worker in workers: worker.start()
    time.sleep(.2)
    marker = counter()
    state['marker'] = True
    invalidate(hwnd,None,True)
    deadline = time.monotonic()+10
    while not white_seen.wait(.05) and time.monotonic() < deadline:
        invalidate(hwnd,None,True)
    test.assertTrue(white_seen.is_set(),'No marker frame arrived before input')
    test.assertIsNone(process.poll(),'Fixture recorder ended before input')
    packets = encode_click(Point(20,30),ClientBounds(128,128))
    api = _NativeApi()
    for packet in packets:
        test.assertIs(api.post(hwnd,packet),True)
    posted = counter()
    for _ in range(12):
        time.sleep(.1)
        invalidate(hwnd,None,True)
    # A timeout is unresolved: retain the original process and pin without a
    # second terminate/kill attempt. communicate handles no game process.
    process.wait(timeout=20)
    for worker in workers: worker.join(timeout=5)
    test.assertEqual(0,process.returncode)
    test.assertFalse(any(worker.is_alive() for worker in workers))
    test.assertEqual([],errors)
    process.stdout.close()
    process.stderr.close()
    retained['pin'].__exit__(None,None,None)
    _UNRESOLVED.remove(retained)
    timebase = next(line.decode('ascii').split(':',1)[1].strip()
                    for line in lines if line.startswith(b'#tb'))
    scale = Fraction(timebase)
    rows = [line.decode('ascii').split(',') for line in lines if line and not line.startswith(b'#')]
    test.assertEqual(len(rows),len(frames))
    test.assertGreaterEqual(len(frames),2)
    events = []
    for row,(raw,shape) in zip(rows,frames):
        test.assertEqual(6,len(row))
        test.assertEqual(0,int(row[0].strip()))
        test.assertEqual(len(raw),int(row[4].strip()))
        test.assertEqual(hashlib.sha256(raw).hexdigest(),row[-1].strip())
        _,pixel = _pixels(raw,(shape[0]//2,shape[1]//2,1,1))
        events.append((int(row[2].strip()),pixel))
    test.assertTrue(all(a[0] < b[0] for a,b in zip(events,events[1:])))
    white = next(index for index,(_,pixel) in enumerate(events) if all(v >= 245 for v in pixel[:3]))
    bracket = Fraction(posted-marker,frequency.value)
    black = next((index for index in range(len(events)-1,white,-1) if all(v <= 10 for v in events[index][1][:3])
                  and (events[index][0]-events[white][0])*scale > bracket+Fraction(1,20)),None)
    test.assertIsNotNone(black,'No bracketed black fixture frame; samples='+str([(pts,list(pixel[:3])) for pts,pixel in events]))
    delta = (events[black][0]-events[white][0])*scale
    test.assertGreater(delta,bracket+Fraction(1,20))
    test.assertTrue(state['clicked'])
    test.assertEqual([(int(packet.message),packet.wparam,packet.lparam) for packet in packets],state['events'])
    print('Owned WGC tee fixture:',len(frames),'frames, PTS interval',float(delta),
          'seconds, QPC bracket',float(bracket),'seconds, time base',timebase,flush=True)
