"""Capture image-pin tests use fabricated APIs and an owned temporary file."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from sims4foundry_cli import capture_pin
from sims4foundry_cli.errors import FoundryError


class FakeApi:
    def __init__(self):
        self.data = b'fixture helper image'
        self.offset = 0
        self.opened, self.closed = [], []
        self.attrs = {}
        self.bad_open = None
        self.close_bad = None
        self.fixed = True
    def fixed_drive(self, root):
        return self.fixed
    def open_directory(self, path):
        self.opened.append(('dir',path))
        return None if path == self.bad_open else path
    def open_image(self, path):
        self.opened.append(('file',path))
        return None if path == self.bad_open else path
    def metadata(self, handle):
        if handle in self.attrs:
            return self.attrs[handle]
        return {'attributes':0 if handle.endswith('ffmpeg.exe') else 0x10,
                'size':len(self.data), 'links':1}
    def read(self, handle, size):
        value = self.data[self.offset:self.offset+min(size,3)]
        self.offset += len(value)
        return value
    def close(self, handle):
        self.closed.append(handle)
        return handle != self.close_bad


class PinTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.path = r'C:\owned\helpers\ffmpeg.exe'
        self.pin = hashlib.sha256(self.api.data).hexdigest()
    def retained(self, path=None, pin=None):
        return capture_pin._retain(path or self.path, pin or self.pin, self.api)
    def test_holds_all_ancestors_and_image_until_exit(self):
        with self.retained():
            self.assertEqual([('dir',r'C:\owned'), ('dir',r'C:\owned\helpers'),
                              ('file',self.path)], self.api.opened)
            self.assertEqual([], self.api.closed)
        self.assertEqual([self.path,r'C:\owned\helpers',r'C:\owned'], self.api.closed)
    def test_hash_mismatch_or_bad_path_never_yields_and_closes(self):
        with self.assertRaises(FoundryError):
            with self.retained(pin='0'*64): self.fail('must not yield')
        self.assertEqual(len(self.api.opened),len(self.api.closed))
        for value in (r'\\server\share\ffmpeg.exe', r'C:\owned\..\ffmpeg.exe',
                      r'C:\owned\other.exe', r'C:\owned\ffmpeg.exe:stream'):
            with self.subTest(path=value), self.assertRaises(FoundryError):
                with self.retained(path=value): self.fail('must not yield')
    def test_reparse_directory_or_image_and_hardlinks_refuse(self):
        for path, attrs in ((r'C:\owned',0x410),(self.path,0x400)):
            self.setUp()
            self.api.attrs[path] = {'attributes':attrs,'size':len(self.api.data),'links':1}
            with self.assertRaises(FoundryError):
                with self.retained(): self.fail('must not yield')
            self.assertEqual(len(self.api.opened),len(self.api.closed))
        self.setUp()
        self.api.attrs[self.path] = {'attributes':0,'size':len(self.api.data),'links':2}
        with self.assertRaises(FoundryError):
            with self.retained(): self.fail('must not yield')
    def test_failed_intermediate_open_closes_earlier_pins(self):
        self.api.bad_open = r'C:\owned\helpers'
        with self.assertRaises(FoundryError):
            with self.retained(): self.fail('must not yield')
        self.assertEqual([r'C:\owned'],self.api.closed)
    def test_cleanup_failure_is_reported_after_all_close_attempts(self):
        self.api.close_bad = r'C:\owned\helpers'
        with self.assertRaises(FoundryError) as error:
            with self.retained(): pass
        self.assertEqual('capture_pin_cleanup_failed', error.exception.code)
        self.assertEqual(3,len(self.api.closed))
    def test_nonfixed_drive_fails_before_open(self):
        self.api.fixed = False
        with self.assertRaises(FoundryError):
            with self.retained(): self.fail('must not yield')
        self.assertEqual([],self.api.opened)
    def test_short_or_extended_read_refuses_and_closes_every_handle(self):
        for delta in (-1,1):
            self.setUp()
            self.api.attrs[self.path] = {'attributes':0, 'size':len(self.api.data)+delta, 'links':1}
            with self.subTest(delta=delta), self.assertRaises(FoundryError):
                with self.retained(): self.fail('must not yield')
            self.assertEqual(len(self.api.opened),len(self.api.closed))
    def test_exception_in_protected_work_still_releases_all_handles(self):
        with self.assertRaises(RuntimeError):
            with self.retained(): raise RuntimeError('fixture runner failure')
        self.assertEqual([self.path,r'C:\owned\helpers',r'C:\owned'], self.api.closed)


@unittest.skipUnless(os.name == 'nt', 'Owned Windows filesystem fixture only')
class NativePinTests(unittest.TestCase):
    def test_real_owned_file_rejects_write_and_rename_while_retained(self):
        with tempfile.TemporaryDirectory(prefix='foundry-pin-fixture-') as root:
            folder = Path(root)/'owned'
            folder.mkdir()
            image = folder/'ffmpeg.exe'
            image.write_bytes(b'owned fixture only')
            replacement = folder/'replacement.exe'
            replacement.write_bytes(b'other fixture')
            moved = Path(root)/'moved'
            pin = hashlib.sha256(image.read_bytes()).hexdigest()
            with capture_pin.retained_capture_image(str(image),pin):
                with self.assertRaises(PermissionError):
                    with image.open('wb'): pass
                with self.assertRaises(PermissionError):
                    os.replace(replacement,image)
                with self.assertRaises(PermissionError):
                    os.rename(folder,moved)
            self.assertEqual(b'owned fixture only',image.read_bytes())
            self.assertEqual(b'other fixture',replacement.read_bytes())
