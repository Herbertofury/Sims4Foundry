"""Visual readback uses generated pixels and owned files only."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from sims4foundry_cli import visual_probe
from sims4foundry_cli.errors import FoundryError


def make_png(width,height,color,scanlines):
    def chunk(kind,payload):
        return (struct.pack(">I",len(payload)) + kind + payload
                + struct.pack(">I",zlib.crc32(kind+payload) & 0xffffffff))
    return (visual_probe.PNG + chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,color,0,0,0))
            + chunk(b'IDAT',zlib.compress(scanlines)) + chunk(b'IEND',b''))


def rgba(width,height,color):
    row=bytes(color)*width
    return make_png(width,height,6,b''.join(b'\0'+row for _ in range(height)))


class PixelTests(unittest.TestCase):
    def test_all_five_png_filters_decode_to_known_values(self):
        row=bytes((10,20,30,255,40,50,60,255))
        scans=(b'\0'+row
               +b'\1'+bytes((10,20,30,255,30,30,30,0))
               +b'\2'+bytes(8)
               +b'\3'+bytes((5,10,15,128,15,15,15,0))
               +b'\4'+bytes(8))
        shape,pixels=visual_probe._pixels(make_png(2,5,6,scans),(1,0,1,5))
        self.assertEqual((2,5),shape)
        self.assertEqual(bytes((40,50,60,255))*5,pixels)
    def test_rgb_and_grayscale_expand_to_rgba(self):
        rgb=make_png(1,1,2,b'\0'+bytes((1,2,3)))
        gray=make_png(1,1,0,b'\0'+bytes((11,)))
        gray_alpha=make_png(1,1,4,b'\0'+bytes((11,77)))
        self.assertEqual(bytes((1,2,3,255)),visual_probe._pixels(rgb,(0,0,1,1))[1])
        self.assertEqual(bytes((11,11,11,255)),visual_probe._pixels(gray,(0,0,1,1))[1])
        self.assertEqual(bytes((11,11,11,77)),visual_probe._pixels(gray_alpha,(0,0,1,1))[1])
    def test_crc_truncation_and_invalid_filter_are_refused(self):
        raw=rgba(1,1,(1,2,3,255))
        invalid_filter=make_png(1,1,6,b'\5'+bytes((1,2,3,255)))
        for data in (raw[:20],raw[:-1],raw[:40]+b'X'+raw[41:],invalid_filter):
            with self.subTest(length=len(data)),self.assertRaises(FoundryError):
                visual_probe._pixels(data,(0,0,1,1))
    def test_transparency_and_animation_metadata_are_not_silently_ignored(self):
        raw=rgba(1,1,(1,2,3,255))
        for kind in (b'tRNS',b'acTL',b'fcTL',b'fdAT'):
            payload=b'\0\0'
            chunk=struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
            altered=raw[:33]+chunk+raw[33:]
            with self.subTest(kind=kind),self.assertRaises(FoundryError):
                visual_probe._pixels(altered,(0,0,1,1))


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.observed=root/'observed.png'
        self.reference=root/'reference.png'
        self.observed.write_bytes(rgba(4,4,(10,20,30,255)))
        self.reference.write_bytes(rgba(2,2,(10,20,30,255)))
        self.a=hashlib.sha256(self.observed.read_bytes()).hexdigest()
        self.b=hashlib.sha256(self.reference.read_bytes()).hexdigest()
    def compare(self, **kwargs):
        return visual_probe.compare(self.observed,self.a,self.reference,self.b,(1,1,2,2),**kwargs)
    def test_exact_match_is_pixel_readback_not_semantic_proof(self):
        result=self.compare()
        self.assertTrue(result['matched'])
        self.assertEqual(0,result['mismatched_pixels'])
        self.assertFalse(result['semantic_state_verified'])
        self.assertFalse(result['game_qualified'])
    def test_pixel_difference_and_explicit_tolerance(self):
        self.reference.write_bytes(rgba(2,2,(11,20,30,255)))
        self.b=hashlib.sha256(self.reference.read_bytes()).hexdigest()
        self.assertFalse(self.compare()['matched'])
        self.assertTrue(self.compare(max_channel_difference=1)['matched'])
        self.assertTrue(self.compare(max_mismatched_pixels=4)['matched'])
    def test_wrong_hash_dimensions_and_bounds_fail(self):
        with self.assertRaises(FoundryError):
            visual_probe.compare(self.observed,'0'*64,self.reference,self.b,(1,1,2,2))
        for region in ((3,3,2,2),(0,0,0,1),(True,0,1,1),(0,0,513,1)):
            with self.subTest(region=region),self.assertRaises(FoundryError):
                visual_probe.compare(self.observed,self.a,self.reference,self.b,region)
        with self.assertRaises(FoundryError): self.compare(max_channel_difference=True)
    def test_read_data_is_pinned_not_reopened_for_comparison(self):
        self.observed.write_bytes(rgba(4,4,(0,0,0,255)))
        with self.assertRaises(FoundryError): self.compare()
