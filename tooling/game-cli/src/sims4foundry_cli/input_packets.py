"""Pure Win32 packet plans; no native sender and no verified game support.

Construction is NOT queue acceptance, dispatch, delivery, or semantic success.
Release packets express mandatory release intent, not guaranteed delivery: a future
sender must retain cleanup obligations even on partial failure. It must separately
validate process lifetime/hash, HWND/thread/integrity/foreground baseline and an
actively progressing recorder. These encoders cannot establish those conditions.

Packing references (Microsoft Learn, consulted 2026-11-04):
https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-lbuttondown
https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-lbuttonup
https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-rbuttondown
https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-rbuttonup
https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-mousemove
https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-mousewheel
https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-keydown
https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-keyup
https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes

All packed integers are unsigned 32-bit bit patterns, not host-sized LPARAMs.
Wheel deltas include high-resolution values, not just multiples of 120. Clicks
and drags require in-client coordinates; signed screen coordinates are wheel-only.
No text, arbitrary virtual keys/messages, ALT, or held keyboard modifiers are
supported. Mouse modifier flags describe externally verified state only; they do
not press/release modifiers. Timing/path caps below are policy, not Win32 limits.
"""
from dataclasses import dataclass
from enum import Enum, IntEnum

MAX_DURATION_MS = 5000
MAX_DRAG_POINTS = 128
MAX_DRAG_PATH_PIXELS = 16384  # Manhattan length, avoiding floating point overflow.


class MouseButton(Enum):
    LEFT = "left"
    RIGHT = "right"


class NavigationKey(IntEnum):
    TAB = 0x09
    ENTER = 0x0D
    ESCAPE = 0x1B
    PAGE_UP = 0x21
    PAGE_DOWN = 0x22
    END = 0x23
    HOME = 0x24
    LEFT = 0x25
    UP = 0x26
    RIGHT = 0x27
    DOWN = 0x28


class Message(IntEnum):
    KEY_DOWN = 0x0100
    KEY_UP = 0x0101
    MOUSE_MOVE = 0x0200
    LEFT_DOWN = 0x0201
    LEFT_UP = 0x0202
    RIGHT_DOWN = 0x0204
    RIGHT_UP = 0x0205
    MOUSE_WHEEL = 0x020A


class CoordinateSpace(Enum):
    NONE = "none"
    CLIENT = "client"
    SCREEN = "screen"


def _integer(value: int, low: int, high: int, name: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _boolean(value: bool, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a bool")
    return value


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int

    def __post_init__(self) -> None:
        _integer(self.x, -32768, 32767, "x")
        _integer(self.y, -32768, 32767, "y")


@dataclass(frozen=True, slots=True)
class ClientBounds:
    width: int
    height: int

    def __post_init__(self) -> None:
        _integer(self.width, 1, 32768, "width")
        _integer(self.height, 1, 32768, "height")


@dataclass(frozen=True, slots=True)
class ScanCode:
    """Caller-supplied verified scan byte and E0 status; no mapping is inferred.

    Creating this object does not verify the active keyboard layout or key mapping.
    The future runtime must verify both against NavigationKey before dispatch.
    """
    code: int
    extended: bool

    def __post_init__(self) -> None:
        _integer(self.code, 1, 255, "scan code")
        _boolean(self.extended, "extended")


@dataclass(frozen=True, slots=True)
class InputPacket:
    message: Message
    wparam: int
    lparam: int
    offset_ms: int
    coordinate_space: CoordinateSpace
    release_intent: bool = False

    def __post_init__(self) -> None:
        if type(self.message) is not Message:
            raise ValueError("message must be an allowlisted Message")
        if type(self.coordinate_space) is not CoordinateSpace:
            raise ValueError("coordinate_space must be a CoordinateSpace")
        _integer(self.wparam, 0, 0xFFFFFFFF, "wparam")
        _integer(self.lparam, 0, 0xFFFFFFFF, "lparam")
        _integer(self.offset_ms, 0, MAX_DURATION_MS, "offset_ms")
        _boolean(self.release_intent, "release_intent")
        key_messages = (Message.KEY_DOWN, Message.KEY_UP)
        release_messages = (Message.KEY_UP, Message.LEFT_UP, Message.RIGHT_UP)
        if self.release_intent != (self.message in release_messages):
            raise ValueError("release_intent must match the message")
        if self.message in key_messages:
            if self.coordinate_space is not CoordinateSpace.NONE:
                raise ValueError("key packets have no coordinate space")
            if self.wparam not in {int(key) for key in NavigationKey}:
                raise ValueError("virtual key is not allowlisted")
            # Exactly one transition, a nonzero scan byte, no ALT/reserved bits.
            expected_state = 0xC0000000 if self.release_intent else 0
            if (self.lparam & 0xFFFF) != 1 or not ((self.lparam >> 16) & 255):
                raise ValueError("key packet requires one repeat and a nonzero scan byte")
            if (self.lparam & 0xFE000000) != expected_state:
                raise ValueError("invalid key state, context or reserved bits")
        elif self.message is Message.MOUSE_WHEEL:
            if self.coordinate_space is not CoordinateSpace.SCREEN:
                raise ValueError("wheel packet requires screen coordinates")
            if (self.wparam & 0xFFFF) & ~12 or not (self.wparam >> 16):
                raise ValueError("wheel packet requires a delta and only SHIFT/CTRL flags")
        else:
            if self.coordinate_space is not CoordinateSpace.CLIENT:
                raise ValueError("mouse packet requires client coordinates")
            if self.lparam & 0x80008000:
                raise ValueError("click/drag packet must use nonnegative client coordinates")
            if self.message is Message.LEFT_DOWN:
                allowed_buttons = (1,)
            elif self.message is Message.RIGHT_DOWN:
                allowed_buttons = (2,)
            elif self.message is Message.MOUSE_MOVE:
                allowed_buttons = (1, 2)
            else:
                allowed_buttons = (0,)
            if self.wparam & ~15 or (self.wparam & 3) not in allowed_buttons:
                raise ValueError("invalid held-button/modifier flags")


def _packed_point(point: Point) -> int:
    if type(point) is not Point:
        raise ValueError("point must be a Point")
    return (point.x & 0xFFFF) | ((point.y & 0xFFFF) << 16)


def _client_point(point: Point, bounds: ClientBounds) -> int:
    packed = _packed_point(point)
    if type(bounds) is not ClientBounds:
        raise ValueError("bounds must be ClientBounds")
    if not (0 <= point.x < bounds.width and 0 <= point.y < bounds.height):
        raise ValueError("point must be inside client bounds")
    return packed


def _modifiers(shift: bool, control: bool) -> int:
    return (4 if _boolean(shift, "shift") else 0) | (8 if _boolean(control, "control") else 0)


def _button(button: MouseButton) -> tuple[Message, Message, int]:
    if type(button) is not MouseButton:
        raise ValueError("button must be MouseButton.LEFT or MouseButton.RIGHT")
    if button is MouseButton.LEFT:
        return Message.LEFT_DOWN, Message.LEFT_UP, 1
    return Message.RIGHT_DOWN, Message.RIGHT_UP, 2


def encode_click(point: Point, bounds: ClientBounds, *, button: MouseButton = MouseButton.LEFT,
                 hold_ms: int = 50, shift: bool = False, control: bool = False) -> tuple[InputPacket, ...]:
    """Return down/up intent; no timing, clicking, or keyboard state changes occur."""
    _integer(hold_ms, 1, MAX_DURATION_MS, "hold_ms")
    packed = _client_point(point, bounds)
    down, up, flag = _button(button)
    modifiers = _modifiers(shift, control)
    return (InputPacket(down, modifiers | flag, packed, 0, CoordinateSpace.CLIENT),
            InputPacket(up, modifiers, packed, hold_ms, CoordinateSpace.CLIENT, True))


def encode_navigation_key(key: NavigationKey, verified_scan_code: ScanCode, *,
                          release: bool, offset_ms: int = 0) -> InputPacket:
    """Encode a single initial press or explicit release, never autorepeat/ALT."""
    if type(key) is not NavigationKey or type(verified_scan_code) is not ScanCode:
        raise ValueError("key must be NavigationKey and verified_scan_code must be ScanCode")
    _boolean(release, "release")
    bits = 1 | (verified_scan_code.code << 16) | (int(verified_scan_code.extended) << 24)
    if release:
        bits |= (1 << 30) | (1 << 31)
    return InputPacket(Message.KEY_UP if release else Message.KEY_DOWN, int(key), bits,
                       offset_ms, CoordinateSpace.NONE, release)


def encode_key_press(key: NavigationKey, verified_scan_code: ScanCode, *,
                     hold_ms: int = 50) -> tuple[InputPacket, ...]:
    _integer(hold_ms, 1, MAX_DURATION_MS, "hold_ms")
    return (encode_navigation_key(key, verified_scan_code, release=False),
            encode_navigation_key(key, verified_scan_code, release=True, offset_ms=hold_ms))


def encode_wheel(screen_point: Point, delta: int, *, shift: bool = False,
                 control: bool = False) -> InputPacket:
    """Screen, not client, coordinates; signed 16-bit nonzero wheel delta."""
    _integer(delta, -32768, 32767, "delta")
    if delta == 0:
        raise ValueError("delta must be nonzero")
    return InputPacket(Message.MOUSE_WHEEL, ((delta & 0xFFFF) << 16) | _modifiers(shift, control),
                       _packed_point(screen_point), 0, CoordinateSpace.SCREEN)


def encode_drag(path: tuple[Point, ...], bounds: ClientBounds, *, duration_ms: int,
                button: MouseButton = MouseButton.LEFT, shift: bool = False,
                control: bool = False) -> tuple[InputPacket, ...]:
    """Bounded client path, held-button moves and mandatory final release intent.

    Only finite tuples are accepted; generators cannot hide unbounded work. Moves
    have strictly increasing integer offsets, with release at the final offset.
    No capture, interpolation, sleeping, dispatch or semantic guarantee is implied.
    """
    if type(path) is not tuple or not 2 <= len(path) <= MAX_DRAG_POINTS:
        raise ValueError(f"path must be a tuple with 2..{MAX_DRAG_POINTS} points")
    _integer(duration_ms, len(path) - 1, MAX_DURATION_MS, "duration_ms")
    packed = tuple(_client_point(point, bounds) for point in path)
    distance = sum(abs(b.x - a.x) + abs(b.y - a.y) for a, b in zip(path, path[1:]))
    if distance == 0 or distance > MAX_DRAG_PATH_PIXELS:
        raise ValueError(f"path length must be 1..{MAX_DRAG_PATH_PIXELS} pixels")
    down, up, flag = _button(button)
    modifiers = _modifiers(shift, control)
    packets = [InputPacket(down, modifiers | flag, packed[0], 0, CoordinateSpace.CLIENT)]
    for index in range(1, len(path)):
        packets.append(InputPacket(Message.MOUSE_MOVE, modifiers | flag, packed[index],
                                   index * duration_ms // (len(path) - 1), CoordinateSpace.CLIENT))
    packets.append(InputPacket(up, modifiers, packed[-1], duration_ms, CoordinateSpace.CLIENT, True))
    return tuple(packets)
