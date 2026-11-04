"""Bounded validation of the non-interlaced 8-bit PNGs produced by capture.

CRC and decompression validation establish image integrity, not freshness,
nonblank content, correct surface, or any semantic gameplay result.
"""
import struct
import zlib
from .errors import FoundryError
from .io import plain_path

MAX_BYTES = 128 * 1024 * 1024


def inspect_png(path):
    path = plain_path(path)
    if not 57 <= path.stat().st_size <= MAX_BYTES:
        raise FoundryError("capture_invalid", "Capture is truncated or exceeds the image bound.")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise FoundryError("capture_invalid", "Capture is not a bounded PNG.")
    offset = 8
    image = bytearray()
    shape = None
    ended = False
    while offset < len(raw):
        if offset + 12 > len(raw):
            raise FoundryError("capture_invalid", "PNG chunk is truncated.")
        size = struct.unpack_from(">I", raw, offset)[0]
        kind = raw[offset+4:offset+8]
        end = offset + size + 12
        if end > len(raw):
            raise FoundryError("capture_invalid", "PNG chunk size exceeds its file.")
        payload = raw[offset+8:offset+8+size]
        crc = struct.unpack_from(">I", raw, offset+8+size)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise FoundryError("capture_invalid", "PNG chunk CRC does not match.")
        if shape is None and kind != b"IHDR":
            raise FoundryError("capture_invalid", "PNG must begin with IHDR.")
        if kind == b"IHDR":
            if shape is not None or size != 13:
                raise FoundryError("capture_invalid", "PNG has an invalid IHDR.")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
            if (not 1 <= width <= 16384 or not 1 <= height <= 16384 or depth != 8 or channels is None
                    or compression != 0 or filtering != 0 or interlace != 0):
                raise FoundryError("capture_invalid", "Unsupported capture PNG pixel format.")
            stride = width * channels + 1
            expected = stride * height
            if expected > MAX_BYTES:
                raise FoundryError("capture_invalid", "Decoded capture exceeds the image bound.")
            shape = width, height, stride, expected
        elif kind == b"IDAT":
            image.extend(payload)
        elif kind == b"IEND":
            if size or end != len(raw):
                raise FoundryError("capture_invalid", "Invalid PNG end marker/trailing data.")
            ended = True
            break
        elif kind[0] & 32 == 0 and kind != b"PLTE":
            raise FoundryError("capture_invalid", "Unsupported critical PNG chunk.")
        offset = end
    if not ended or not shape or not image:
        raise FoundryError("capture_invalid", "PNG has no complete image data.")
    width, height, stride, expected = shape
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(image, expected + 1)
    except zlib.error as exc:
        raise FoundryError("capture_invalid", "PNG data cannot be decompressed.") from exc
    if len(decoded) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise FoundryError("capture_invalid", "PNG image data size/end does not match.")
    if any(decoded[row * stride] > 4 for row in range(height)):
        raise FoundryError("capture_invalid", "PNG has an invalid row filter.")
    return width, height
