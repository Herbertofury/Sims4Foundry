"""Tiny CRC-valid fixtures, not real-game capture acceptance."""
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.png import inspect_png


def chunk(kind, payload=b''):
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)


class PngStructureTests(unittest.TestCase):
    def image(self, middle, color=6):
        return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, color, 0, 0, 0))
                + middle + chunk(b'IEND'))

    def inspect(self, raw):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'capture.png'
            path.write_bytes(raw)
            try:
                return inspect_png(path)
            finally:
                self.assertEqual(path.read_bytes(), raw)

    def test_reject_nonconsecutive_idat_with_valid_crc_and_pixels(self):
        data = zlib.compress(b'\0\xff\0\xff\xff')
        raw = self.image(chunk(b'IDAT', data[:4]) + chunk(b'tEXt', b'key\0value') + chunk(b'IDAT', data[4:]))
        with self.assertRaises(FoundryError):
            self.inspect(raw)

    def test_reject_invalid_palette_structure(self):
        data = chunk(b'IDAT', zlib.compress(b'\0\xff\0\xff\xff'))
        palette = chunk(b'PLTE', b'\0\0\0')
        cases = [palette + palette + data, data + palette,
                 chunk(b'PLTE') + data, chunk(b'PLTE', b'xx') + data,
                 chunk(b'PLTE', b'\0' * 771) + data]
        for middle in cases:
            with self.subTest(size=len(middle)), self.assertRaises(FoundryError):
                self.inspect(self.image(middle))

    def test_reject_palette_for_grayscale(self):
        for color, pixels in [(0, b'\0\xff'), (4, b'\0\xff\xff')]:
            with self.subTest(color=color), self.assertRaises(FoundryError):
                self.inspect(self.image(chunk(b'PLTE', b'\0\0\0') + chunk(b'IDAT', zlib.compress(pixels)), color))

    def test_accept_consecutive_idat_and_optional_truecolor_palette(self):
        data = zlib.compress(b'\0\xff\0\xff\xff')
        middle = (chunk(b'PLTE', b'\0\0\0') + chunk(b'IDAT', data[:4]) + chunk(b'IDAT')
                  + chunk(b'IDAT', data[4:]) + chunk(b'tEXt', b'key\0value'))
        self.assertEqual(self.inspect(self.image(middle)), (1, 1))
