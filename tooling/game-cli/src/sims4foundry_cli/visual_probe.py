"""Compare one pinned PNG region against an explicitly pinned reference."""
import struct
import zlib

from . import companion_build
from .errors import FoundryError

MAX_DECODED = 128 * 1024 * 1024
MAX_REGION_PIXELS = 512 * 512
PNG = b"\x89PNG\r\n\x1a\n"


def _invalid():
    raise FoundryError("visual_png_invalid", "PNG content is invalid or unsupported.") from None


def _paeth(left, above, upper_left):
    prediction = left + above - upper_left
    a, b, c = abs(prediction-left), abs(prediction-above), abs(prediction-upper_left)
    return left if a <= b and a <= c else above if b <= c else upper_left


def _pixels(raw, region):
    if type(raw) is not bytes or len(raw) < 57 or raw[:8] != PNG:
        _invalid()
    offset, shape, compressed, ended = 8, None, bytearray(), False
    while offset < len(raw):
        if offset + 12 > len(raw):
            _invalid()
        size = struct.unpack_from(">I", raw, offset)[0]
        kind = raw[offset+4:offset+8]
        end = offset + size + 12
        if end > len(raw):
            _invalid()
        payload = raw[offset+8:offset+8+size]
        if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack_from(">I", raw, end-4)[0]:
            _invalid()
        if shape is None and kind != b"IHDR":
            _invalid()
        if kind == b"IHDR":
            if shape is not None or size != 13:
                _invalid()
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            channels = {0:1, 2:3, 4:2, 6:4}.get(color)
            if (not 1 <= width <= 16384 or not 1 <= height <= 16384 or depth != 8
                    or channels is None or compression or filtering or interlace
                    or (width * channels + 1) * height > MAX_DECODED):
                _invalid()
            shape = (width,height,channels)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            if size != 0 or end != len(raw):
                _invalid()
            ended = True
            break
        elif kind in (b"tRNS", b"acTL", b"fcTL", b"fdAT"):
            # Do not silently ignore transparency or animated-frame semantics.
            _invalid()
        elif kind != b"PLTE" and not kind[0] & 32:
            _invalid()
        offset = end
    if not ended or shape is None or not compressed:
        _invalid()
    width,height,channels = shape
    x,y,w,h = region
    if x+w > width or y+h > height:
        raise FoundryError("visual_region", "Selected region is outside the captured image.")
    stride = width * channels
    expected = (stride + 1) * height
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(compressed, expected + 1)
    except zlib.error:
        _invalid()
    if len(decoded) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        _invalid()
    if any(decoded[row*(stride+1)] > 4 for row in range(height)):
        _invalid()
    previous = bytearray(stride)
    output = bytearray()
    for row in range(y+h):
        start = row*(stride+1)
        mode = decoded[start]
        current = bytearray(decoded[start+1:start+1+stride])
        if mode:
            for index in range(stride):
                left = current[index-channels] if index >= channels else 0
                above = previous[index]
                upper_left = previous[index-channels] if index >= channels else 0
                predictor = (left if mode == 1 else above if mode == 2 else
                             (left+above)//2 if mode == 3 else _paeth(left,above,upper_left))
                current[index] = (current[index] + predictor) & 255
        if row >= y:
            for col in range(x,x+w):
                index = col*channels
                pixel = current[index:index+channels]
                if channels == 1:
                    output.extend((pixel[0],pixel[0],pixel[0],255))
                elif channels == 2:
                    output.extend((pixel[0],pixel[0],pixel[0],pixel[1]))
                elif channels == 3:
                    output.extend((pixel[0],pixel[1],pixel[2],255))
                else:
                    output.extend(pixel)
        previous = current
    return shape[:2], bytes(output)


def compare(observed, observed_sha256, reference, reference_sha256, region,
            *, max_channel_difference=0, max_mismatched_pixels=0):
    if (type(region) not in (tuple,list) or len(region) != 4
            or any(type(value) is not int for value in region)):
        raise FoundryError("visual_region", "Region requires exact integer x, y, width and height.")
    x,y,w,h = region
    if (x < 0 or y < 0 or not 1 <= w <= 512 or not 1 <= h <= 512
            or w*h > MAX_REGION_PIXELS or type(max_channel_difference) is not int
            or not 0 <= max_channel_difference <= 255 or type(max_mismatched_pixels) is not int
            or not 0 <= max_mismatched_pixels <= w*h):
        raise FoundryError("visual_region", "Region or comparison tolerance is outside its bounds.")
    for value in (observed_sha256,reference_sha256):
        if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise FoundryError("visual_hash", "Explicit lowercase SHA-256 pins are required.")
    captured, captured_hash, _ = companion_build._regular(observed, MAX_DECODED)
    expected, expected_hash, _ = companion_build._regular(reference, MAX_DECODED)
    if captured_hash != observed_sha256 or expected_hash != reference_sha256:
        raise FoundryError("visual_hash", "Captured image or reference does not match its pin.")
    captured_size, actual = _pixels(captured, (x,y,w,h))
    reference_size, wanted = _pixels(expected, (0,0,w,h))
    if reference_size != (w,h):
        raise FoundryError("visual_reference", "Reference dimensions must equal the selected region.")
    mismatches = 0
    for offset in range(0,len(actual),4):
        if any(abs(actual[offset+i]-wanted[offset+i]) > max_channel_difference for i in range(4)):
            mismatches += 1
    return {"ok":True, "matched":mismatches <= max_mismatched_pixels,
            "observed_sha256":captured_hash, "reference_sha256":expected_hash,
            "image_size":list(captured_size), "region":[x,y,w,h], "pixels":w*h,
            "mismatched_pixels":mismatches, "max_channel_difference":max_channel_difference,
            "max_mismatched_pixels":max_mismatched_pixels,
            "semantic_state_verified":False, "game_qualified":False}
