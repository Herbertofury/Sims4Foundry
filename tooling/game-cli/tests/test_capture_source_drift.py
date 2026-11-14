"""Generated captures only; no game/desktop/native helper execution."""
from pathlib import Path
import hashlib
import struct
import tempfile
import zlib
import unittest
from unittest import mock
from sims4foundry_cli import windows
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import sha256
from test_windows_cli import identity, png
from test_png_structure import chunk
from sims4foundry_cli.png import inspect_png_evidence


class CaptureSourceDriftTests(unittest.TestCase):
    def test_changed_frame_during_identity_recheck_never_completes(self):
        same_size_different_pixels = (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\0\xff\0\xff' * 2) * 2))
            + chunk(b'IEND'))
        for replacement in (b'not a PNG', png(3, 2), same_size_different_pixels):
            with self.subTest(size=len(replacement)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                helper = root / 'ffmpeg.exe'
                helper.write_bytes(b'fixture only')
                destination = root / 'capture'
                calls = 0
                def observer(*args):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        (destination / 'frame.png').write_bytes(replacement)
                    return identity()
                def runner(argv, **kwargs):
                    Path(argv[-1]).write_bytes(png())
                    return mock.Mock(returncode=0)
                with self.assertRaises(FoundryError):
                    windows.capture(identity(), helper, sha256(helper), destination,
                                    observer=observer, runner=runner)
                self.assertTrue((destination / 'capture-intent.json').exists())
                self.assertFalse((destination / 'capture-complete.json').exists())
                self.assertEqual((destination / 'frame.png').read_bytes(), replacement)

    def test_snapshot_hash_and_dimensions_describe_same_validated_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frame.png'
            raw = png(3, 2)
            path.write_bytes(raw)
            self.assertEqual(inspect_png_evidence(path),
                             {"width": 3, "height": 2, "bytes": len(raw),
                              "sha256": hashlib.sha256(raw).hexdigest()})
