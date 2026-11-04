"""Offline packet encoding tests. These do not qualify a live game adapter."""
from dataclasses import FrozenInstanceError
import unittest

from sims4foundry_cli.input_packets import (
    ClientBounds, CoordinateSpace, InputPacket, MAX_DRAG_PATH_PIXELS,
    MAX_DRAG_POINTS, MAX_DURATION_MS, Message, MouseButton, NavigationKey,
    Point, ScanCode, encode_click, encode_drag, encode_key_press,
    encode_navigation_key, encode_wheel,
)


def signed_word(value):
    value &= 0xFFFF
    return value - 65536 if value >= 32768 else value


class InputPacketTests(unittest.TestCase):
    def test_signed_coordinate_round_trip(self):
        for x in (-32768, -1, 0, 1, 32767):
            for y in (-32768, -1, 0, 1, 32767):
                p = encode_wheel(Point(x, y), 120)
                self.assertEqual((x, y), (signed_word(p.lparam), signed_word(p.lparam >> 16)))
                self.assertEqual(CoordinateSpace.SCREEN, p.coordinate_space)
                self.assertTrue(0 <= p.lparam <= 0xFFFFFFFF)

    def test_coordinate_validation(self):
        for bad in (True, False, -32769, 32768, 10**100, -(10**100), 1.0, '1', None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    Point(bad, 0)
                with self.assertRaises(ValueError):
                    Point(0, bad)
        for bad in (True, 0, -1, 32769, 10**100, 1.0):
            with self.assertRaises(ValueError):
                ClientBounds(bad, 1)
            with self.assertRaises(ValueError):
                ClientBounds(1, bad)

    def test_click_flags_and_release(self):
        for button, down, up, flag in ((MouseButton.LEFT, Message.LEFT_DOWN, Message.LEFT_UP, 1),
                                       (MouseButton.RIGHT, Message.RIGHT_DOWN, Message.RIGHT_UP, 2)):
            packets = encode_click(Point(32767, 0), ClientBounds(32768, 1), button=button,
                                   hold_ms=MAX_DURATION_MS, shift=True, control=True)
            self.assertIsInstance(packets, tuple)
            self.assertEqual([down, up], [p.message for p in packets])
            self.assertEqual([flag | 12, 12], [p.wparam for p in packets])
            self.assertEqual([False, True], [p.release_intent for p in packets])
            self.assertEqual([0, MAX_DURATION_MS], [p.offset_ms for p in packets])
            self.assertTrue(all(p.coordinate_space is CoordinateSpace.CLIENT for p in packets))

    def test_click_requires_in_client_point(self):
        for point in (Point(-1, 0), Point(0, -1), Point(10, 0), Point(0, 10)):
            with self.assertRaises(ValueError):
                encode_click(point, ClientBounds(10, 10))
        for button in ('left', 1, True, None):
            with self.assertRaises(ValueError):
                encode_click(Point(0, 0), ClientBounds(1, 1), button=button)
        for flag in (1, 0, 'true', None):
            with self.assertRaises(ValueError):
                encode_click(Point(0, 0), ClientBounds(1, 1), shift=flag)
            with self.assertRaises(ValueError):
                encode_wheel(Point(0, 0), 1, control=flag)

    def test_key_fields_and_release(self):
        for key in NavigationKey:
            for extended in (False, True):
                down, up = encode_key_press(key, ScanCode(255, extended))
                self.assertEqual(Message.KEY_DOWN, down.message)
                self.assertEqual(Message.KEY_UP, up.message)
                self.assertEqual(int(key), down.wparam)
                self.assertEqual(1, down.lparam & 0xFFFF)
                self.assertEqual(255, (down.lparam >> 16) & 255)
                self.assertEqual(int(extended), (down.lparam >> 24) & 1)
                self.assertEqual(0, (down.lparam >> 25) & 127)
                self.assertEqual(down.lparam | 0xC0000000, up.lparam)
                self.assertFalse(down.release_intent)
                self.assertTrue(up.release_intent)
                self.assertEqual(CoordinateSpace.NONE, up.coordinate_space)
        self.assertEqual(0x00010001, encode_key_press(NavigationKey.ESCAPE, ScanCode(1, False))[0].lparam)

    def test_key_injection_rejected(self):
        for bad in (True, 0x25, 'LEFT', 'A', 'ALT', None, 10**100):
            with self.assertRaises(ValueError):
                encode_key_press(bad, ScanCode(1, False))
        for bad in (True, 0, -1, 256, 0xE04B, 10**100, 1.0):
            with self.assertRaises(ValueError):
                ScanCode(bad, False)
        for bad in (0, 1, None):
            with self.assertRaises(ValueError):
                ScanCode(1, bad)
            with self.assertRaises(ValueError):
                encode_navigation_key(NavigationKey.LEFT, ScanCode(1, False), release=bad)
        with self.assertRaises(ValueError):
            encode_key_press(NavigationKey.LEFT, 75)

    def test_wheel_signed_delta_and_modifiers(self):
        for delta in (-32768, -120, -1, 1, 15, 120, 32767):
            p = encode_wheel(Point(-10, -20), delta, shift=True, control=True)
            self.assertEqual(delta, signed_word(p.wparam >> 16))
            self.assertEqual(12, p.wparam & 0xFFFF)
            self.assertFalse(p.release_intent)
        for delta in (True, False, 0, -32769, 32768, 10**100, 1.5, None):
            with self.assertRaises(ValueError):
                encode_wheel(Point(0, 0), delta)

    def test_drag_release_and_held_flags(self):
        path = (Point(0, 0), Point(3, 4), Point(8, 9))
        for button, flag in ((MouseButton.LEFT, 1), (MouseButton.RIGHT, 2)):
            packets = encode_drag(path, ClientBounds(10, 10), duration_ms=101,
                                  button=button, control=True)
            self.assertEqual((0, 50, 101, 101), tuple(p.offset_ms for p in packets))
            self.assertEqual((flag | 8,) * 3 + (8,), tuple(p.wparam for p in packets))
            self.assertEqual((False, False, False, True), tuple(p.release_intent for p in packets))
            self.assertEqual(packets[-2].lparam, packets[-1].lparam)
            self.assertTrue(all(p.coordinate_space is CoordinateSpace.CLIENT for p in packets))

    def test_drag_limits(self):
        bounds = ClientBounds(32768, 32768)
        start = Point(0, 0)
        for path in ((), (start,), [start, Point(1, 1)], iter((start, Point(1, 1))),
                     (start,) * (MAX_DRAG_POINTS + 1), (start, start),
                     (start, Point(MAX_DRAG_PATH_PIXELS + 1, 0)),
                     (start, Point(-1, 1)), (start, 'bad')):
            with self.assertRaises(ValueError):
                encode_drag(path, bounds, duration_ms=1000)
        valid = encode_drag((start, Point(MAX_DRAG_PATH_PIXELS, 0)), bounds, duration_ms=1)
        self.assertTrue(valid[-1].release_intent)
        path = tuple(Point(i, 0) for i in range(MAX_DRAG_POINTS))
        valid = encode_drag(path, bounds, duration_ms=MAX_DURATION_MS)
        self.assertEqual(MAX_DRAG_POINTS + 1, len(valid))
        self.assertTrue(all(a.offset_ms < b.offset_ms for a, b in zip(valid[:-2], valid[1:-1])))
        with self.assertRaises(ValueError):
            encode_drag(path, bounds, duration_ms=MAX_DRAG_POINTS - 2)

    def test_duration_bounds_and_bool_rejection(self):
        for bad in (True, False, 0, -1, MAX_DURATION_MS + 1, 10**100, 1.5, None):
            with self.assertRaises(ValueError):
                encode_click(Point(0, 0), ClientBounds(2, 2), hold_ms=bad)
            with self.assertRaises(ValueError):
                encode_key_press(NavigationKey.TAB, ScanCode(15, False), hold_ms=bad)
            with self.assertRaises(ValueError):
                encode_drag((Point(0, 0), Point(1, 1)), ClientBounds(2, 2), duration_ms=bad)

    def test_direct_packet_construction_cannot_bypass_contract(self):
        valid = dict(message=Message.KEY_DOWN, wparam=int(NavigationKey.LEFT),
                     lparam=0x014B0001, offset_ms=0,
                     coordinate_space=CoordinateSpace.NONE, release_intent=False)
        for mutation in ({"wparam": 0x41}, {"lparam": 0x214B0001},
                         {"lparam": 0x414B0001}, {"lparam": 0x814B0001},
                         {"lparam": 0x034B0001}, {"lparam": 1},
                         {"lparam": 0x014B0002}, {"release_intent": True},
                         {"coordinate_space": CoordinateSpace.CLIENT},
                         {"offset_ms": True}, {"offset_ms": MAX_DURATION_MS + 1}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                InputPacket(**(valid | mutation))
        for message, flags, coords, space, release in (
            (Message.LEFT_UP, 1, 0, CoordinateSpace.CLIENT, True),
            (Message.RIGHT_DOWN, 1, 0, CoordinateSpace.CLIENT, False),
            (Message.MOUSE_MOVE, 0, 0, CoordinateSpace.CLIENT, False),
            (Message.MOUSE_MOVE, 3, 0, CoordinateSpace.CLIENT, False),
            (Message.LEFT_DOWN, 1, 0xFFFF, CoordinateSpace.CLIENT, False),
            (Message.LEFT_DOWN, 1, 0, CoordinateSpace.SCREEN, False),
            (Message.MOUSE_WHEEL, 120 << 16, 0, CoordinateSpace.CLIENT, False),
            (Message.MOUSE_WHEEL, (120 << 16) | 1, 0, CoordinateSpace.SCREEN, False),
            (Message.MOUSE_WHEEL, 0, 0, CoordinateSpace.SCREEN, False),
        ):
            with self.assertRaises(ValueError):
                InputPacket(message, flags, coords, 0, space, release)

    def test_immutable_and_no_unlisted_message(self):
        packet = encode_wheel(Point(0, 0), 120)
        with self.assertRaises(FrozenInstanceError):
            packet.wparam = 0
        with self.assertRaises(FrozenInstanceError):
            Point(0, 0).x = 1
        with self.assertRaises(ValueError):
            InputPacket(0x12, 0, 0, 0, CoordinateSpace.NONE)
        for bad in (True, -1, 2**32, 10**100):
            with self.assertRaises(ValueError):
                InputPacket(Message.KEY_UP, bad, 0, 0, CoordinateSpace.NONE)
            with self.assertRaises(ValueError):
                InputPacket(Message.KEY_UP, 0, bad, 0, CoordinateSpace.NONE)


if __name__ == '__main__':
    unittest.main()
