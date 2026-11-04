import hashlib
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sims4foundry_cli import neutral_ea_process
from sims4foundry_cli.errors import FoundryError


class FakeApi:
    def __init__(self, image):
        self.path = image
        self.creation = 123456
        self.lives = [True, True]
        self.closed = []
    def open(self, pid): return pid
    def live(self, handle): return self.lives.pop(0)
    def created(self, handle): return self.creation
    def image(self, handle): return str(self.path)
    def close(self, handle): self.closed.append(handle)


class EaProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / 'EADesktop.exe'
        self.image.write_bytes(b'EA fixture')
        self.hash = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.api = FakeApi(self.image)

    def test_explicit_live_identity_and_handle_cleanup(self):
        value = neutral_ea_process.inspect(42, self.image, self.hash, api=self.api)
        self.assertEqual(42, value['pid'])
        self.assertEqual(123456, value['creation_filetime'])
        self.assertEqual([42], self.api.closed)

    def test_exit_or_changed_image_refuses_and_closes(self):
        self.api.lives = [True, False]
        with self.assertRaises(FoundryError):
            neutral_ea_process.inspect(42, self.image, self.hash, api=self.api)
        self.assertEqual([42], self.api.closed)
        other = self.image.with_name('Other.exe')
        other.write_bytes(b'other')
        self.api = FakeApi(other)
        with self.assertRaises(FoundryError):
            neutral_ea_process.inspect(42, self.image, self.hash, api=self.api)
        self.assertEqual([42], self.api.closed)

    def test_wrong_hash_refuses_before_process_open(self):
        with self.assertRaises(FoundryError):
            neutral_ea_process.inspect(42, self.image, '0'*64, api=self.api)
        self.assertEqual([], self.api.closed)

    def test_bounded_extent_rejects_growth_replacement_and_sparse_oversize(self):
        real_read = neutral_ea_process._read
        changed = [False]
        def grow(fd, count):
            result = real_read(fd, count)
            if not changed[0]:
                changed[0] = True
                with self.image.open('ab') as stream: stream.write(b'growth')
            return result
        with patch.object(neutral_ea_process, '_read', side_effect=grow):
            with self.assertRaises(FoundryError) as caught:
                neutral_ea_process.stable_executable(self.image)
        self.assertEqual('ea_image_changed', caught.exception.code)
        before_replacement = self.image.read_bytes()
        changed[0] = False
        def replace(fd, count):
            result = real_read(fd, count)
            if not changed[0]:
                changed[0] = True
                replacement = self.image.with_name('replacement.exe')
                replacement.write_bytes(self.image.read_bytes())
                replacement.replace(self.image)
            return result
        with patch.object(neutral_ea_process, '_read', side_effect=replace):
            with self.assertRaises((FoundryError, PermissionError)) as caught:
                neutral_ea_process.stable_executable(self.image)
        self.assertTrue(changed[0])
        if isinstance(caught.exception, PermissionError):
            # Windows can deny replacement of an open executable. That stops
            # the read without fallback; do not retry/bypass the denied move.
            self.assertEqual(before_replacement, self.image.read_bytes())
        else:
            self.assertEqual('ea_image_changed', caught.exception.code)
        with self.image.open('r+b') as stream: stream.truncate(1024*1024*1024)
        with patch.object(neutral_ea_process, '_read', side_effect=AssertionError('unexpected read')):
            with self.assertRaises(FoundryError) as caught:
                neutral_ea_process.stable_executable(self.image)
        self.assertEqual('ea_image_bound', caught.exception.code)

    def test_nonregular_file_is_rejected_before_open(self):
        other = self.image.with_name('directory.exe'); other.mkdir()
        with patch.object(neutral_ea_process, '_open', side_effect=AssertionError('unexpected open')):
            with self.assertRaises(FoundryError): neutral_ea_process.stable_executable(other)

    def test_read_bound_is_original_extent_plus_one(self):
        requested = []
        real_read = neutral_ea_process._read
        def record(fd, count):
            requested.append(count)
            return real_read(fd, count)
        with patch.object(neutral_ea_process, '_read', side_effect=record):
            self.assertEqual(self.hash, neutral_ea_process.stable_executable(self.image)['sha256'])
        self.assertEqual(self.image.stat().st_size + 1, sum(requested))

    def test_deadline_expires_during_hash_before_process_open(self):
        now = [0.0]
        real_read = neutral_ea_process._read
        def slow(fd, count):
            result = real_read(fd, count)
            now[0] = 2.0
            return result
        with patch.object(neutral_ea_process, '_read', side_effect=slow):
            with self.assertRaises(TimeoutError):
                neutral_ea_process.inspect(42, self.image, self.hash, api=self.api,
                                           deadline=1.0, monotonic=lambda:now[0])
        self.assertEqual([], self.api.closed)

    def test_process_hash_and_file_identity_are_rechecked_under_handle(self):
        original = self.api.image
        def swap(handle):
            replacement = self.image.with_name('same-bytes.exe')
            replacement.write_bytes(self.image.read_bytes())
            replacement.replace(self.image)
            return original(handle)
        self.api.image = swap
        with self.assertRaises(FoundryError): neutral_ea_process.inspect(42, self.image, self.hash, api=self.api)
        self.assertEqual([42], self.api.closed)

    def test_windows_path_descriptor_differences_are_narrowly_normalized(self):
        from sims4foundry_cli import launch
        base = self.image.stat()
        fields = {name:getattr(base,name) for name in
                  ('st_dev','st_ino','st_size','st_mode','st_mtime_ns','st_ctime_ns')}
        path = SimpleNamespace(**dict(fields, st_mode=fields['st_mode'] | 0o111,
                                      st_ctime_ns=100, st_birthtime_ns=50))
        descriptor = SimpleNamespace(**dict(fields, st_ctime_ns=200, st_birthtime_ns=50))
        with patch.object(launch, '_WINDOWS', True), \
             patch.object(neutral_ea_process, '_lstat', return_value=path), \
             patch.object(neutral_ea_process, '_fstat', return_value=descriptor):
            self.assertEqual(self.hash, neutral_ea_process.stable_executable(self.image)['sha256'])
        # Exact same-API change detection remains, including descriptor ctime.
        changed = SimpleNamespace(**dict(vars(descriptor), st_ctime_ns=201))
        with patch.object(launch, '_WINDOWS', True), \
             patch.object(neutral_ea_process, '_lstat', return_value=path), \
             patch.object(neutral_ea_process, '_fstat', side_effect=[descriptor,changed]):
            with self.assertRaises(FoundryError): neutral_ea_process.stable_executable(self.image)
        # Unknown birthtime cannot excuse differing ctime across APIs.
        descriptor.st_birthtime_ns = None
        path.st_birthtime_ns = None
        with patch.object(launch, '_WINDOWS', True), \
             patch.object(neutral_ea_process, '_lstat', return_value=path), \
             patch.object(neutral_ea_process, '_fstat', return_value=descriptor):
            with self.assertRaises(FoundryError): neutral_ea_process.stable_executable(self.image)


if __name__ == '__main__':
    unittest.main()
