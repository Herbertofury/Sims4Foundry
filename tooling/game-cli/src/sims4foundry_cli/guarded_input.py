"""Backend-injected sequence logic for a FUTURE separately qualified sender.

There is no native sender here or in the CLI. Tests inject synthetic backends.
This ports the inspected external-pointer release discipline without importing
Apex. Reviewed source SHA-256:
d8df2930835408334411e9f141a3d704e9dc32536eb75cca8f44136b90dfdcbd.

A backend must independently own its process/window lease and recorder checks.
This module does not grant profile ownership, game qualification or permission.
Returned queue acceptance is never semantic success. It cannot guarantee release
if the original window/process becomes unobservable, and never targets a replacement.
"""
from copy import deepcopy
from dataclasses import replace
from .errors import FoundryError
from .input_packets import InputPacket, Message, CoordinateSpace, MAX_DRAG_POINTS, MAX_DRAG_PATH_PIXELS
from .windows import validate_identity, same_identity

OWNERSHIP = ("pid", "creation_filetime", "executable", "executable_sha256", "hwnd", "thread_id", "window_class")


def _validate_sequence(packets, expected):
    if type(packets) is not tuple or not 1 <= len(packets) <= MAX_DRAG_POINTS + 1 or any(type(p) is not InputPacket for p in packets):
        raise FoundryError("invalid_sequence", "Expected a bounded immutable packet sequence.")
    if packets[0].offset_ms != 0 or any(a.offset_ms > b.offset_ms for a, b in zip(packets, packets[1:])):
        raise FoundryError("invalid_sequence", "Packet timing must start at zero and remain ordered.")
    first, last = packets[0], packets[-1]
    if first.message is Message.MOUSE_WHEEL:
        if len(packets) != 1:
            raise FoundryError("invalid_sequence", "Wheel input is one independently observed step.")
        x, y = first.lparam & 65535, first.lparam >> 16
        x, y = x - 65536 if x >= 32768 else x, y - 65536 if y >= 32768 else y
        ox, oy = expected["client_origin"]
        if not ox <= x < ox + expected["client_width"] or not oy <= y < oy + expected["client_height"]:
            raise FoundryError("invalid_sequence", "Wheel screen point is outside the originally observed viewport.")
        return None
    pairs = {Message.LEFT_DOWN: Message.LEFT_UP, Message.RIGHT_DOWN: Message.RIGHT_UP, Message.KEY_DOWN: Message.KEY_UP}
    if first.message not in pairs or last.message is not pairs[first.message] or not last.release_intent:
        raise FoundryError("invalid_sequence", "A held-input sequence needs one matching release.")
    if last.offset_ms <= 0 or any(a.offset_ms >= b.offset_ms for a, b in zip(packets[:-2], packets[1:-1])):
        raise FoundryError("invalid_sequence", "Held input needs a positive hold and strictly ordered move timing.")
    if first.message is Message.KEY_DOWN:
        if len(packets) != 2 or first.wparam != last.wparam or first.lparam | 0xC0000000 != last.lparam:
            raise FoundryError("invalid_sequence", "Key release must match the original key and scan code.")
    else:
        flag = 1 if first.message is Message.LEFT_DOWN else 2
        if any(p.message is not Message.MOUSE_MOVE or p.wparam != first.wparam for p in packets[1:-1]):
            raise FoundryError("invalid_sequence", "Drag moves must preserve the original held button/modifiers.")
        if first.wparam & ~flag != last.wparam or last.lparam != packets[-2].lparam:
            raise FoundryError("invalid_sequence", "Mouse release must match the final point/modifiers.")
        for packet in packets:
            x, y = packet.lparam & 65535, packet.lparam >> 16
            if not 0 <= x < expected["client_width"] or not 0 <= y < expected["client_height"]:
                raise FoundryError("invalid_sequence", "Mouse point is outside the originally observed viewport.")
        if len(packets) > 2:
            points = [(p.lparam & 65535, p.lparam >> 16) for p in packets[:-1]]
            distance = sum(abs(b[0]-a[0]) + abs(b[1]-a[1]) for a, b in zip(points, points[1:]))
            if not 1 <= distance <= MAX_DRAG_PATH_PIXELS:
                raise FoundryError("invalid_sequence", "Drag path exceeds its bounded runtime movement policy.")
    return last


def run_packets(expected, packets, backend, journal):
    """Run against an injected backend; not exported as a live CLI operation.

    Backend methods: observe(), observe_owned(), integrities(), foreground(),
    verify_recording()->True, verify_input_context(packet)->True,
    post(hwnd, packet)->bool, monotonic(), sleep(seconds).
    Input context must verify the current key/scan/layout mapping and actual
    modifier state; construction alone is not verification.
    Observe methods return identity dicts; owned observation may omit geometry.
    Journal must durably persist records or raise. All packet intent is persisted
    before dispatch; optional per-packet evidence is buffered while input is held.
    Up precedes its flush, even if recorder/viewport/foreground checks fail.
    Release still requires original ownership and equal known integrity; losing
    either leaves release unresolved, never authorizes a weaker fallback.
    Target copies prevent accidental aliasing, not arbitrary trusted-Python access.
    Every backend call must be bounded; this pure runner cannot supervise a
    stalled native backend or guarantee cleanup after process termination.
    """
    # Detach nested geometry as well as scalar ownership fields. Audit callbacks
    # must never receive the internal binding used for dispatch or release.
    expected = deepcopy(expected)
    validate_identity(expected)
    release = _validate_sequence(packets, expected)
    baseline = backend.foreground()
    if type(baseline) is not int or not 0 < baseline <= 2**64-1:
        raise FoundryError("unknown_foreground", "An exact foreground baseline is required.")
    events = []
    deferred_audit = []
    armed = False
    attempted = False
    last_point = packets[0].lparam
    error = None
    release_error = None

    def equal_integrity():
        levels = backend.integrities()
        if (type(levels) is not tuple or len(levels) != 2 or any(type(v) is not int or v <= 0 for v in levels)
                or levels[0] != levels[1] or levels[1] != expected["integrity"]):
            raise FoundryError("integrity_mismatch", "Equal known sender/target integrity is required; no bypass exists.")

    def current():
        same_identity(expected, backend.observe())
        equal_integrity()
        if backend.verify_recording() is not True:
            raise FoundryError("recording_unverified", "Actively progressing recording must be explicitly verified.")
        if backend.foreground() != baseline:
            raise FoundryError("foreground_changed", "Another operator changed foreground; stop without replay.")

    def release_original():
        nonlocal armed, release_error
        if not armed:
            return
        # Disarm before the sole release attempt, even if it throws or is refused.
        armed = False
        try:
            observed = backend.observe_owned()
            if not isinstance(observed, dict) or any(observed.get(key) != expected[key] for key in OWNERSHIP):
                raise FoundryError("ownership_changed", "Original ownership is unverified; never release to a replacement.")
            equal_integrity()
            packet = release if release.coordinate_space is CoordinateSpace.NONE else replace(release, lparam=last_point)
            prepare = getattr(backend, "prepare_release", None)
            if prepare is not None:
                # The optional in-memory adapter returns the last point actually
                # attempted by its native sender. It must perform no OS or disk I/O.
                packet = prepare(packet)
                if (type(packet) is not InputPacket or
                        (release.coordinate_space is CoordinateSpace.NONE and packet != release) or
                        (release.coordinate_space is not CoordinateSpace.NONE and
                         (packet.lparam not in {item.lparam for item in packets if not item.release_intent}
                          or packet != replace(release, lparam=packet.lparam)))):
                    raise FoundryError("invalid_release", "Prepared release differs from the original packet plan.")
            # No recorder, viewport, sleep, timing or optional disk operations here.
            accepted = backend.post(expected["hwnd"], packet)
            events.append({"message": int(packet.message), "release": True, "accepted": accepted is True})
            if accepted is not True:
                raise FoundryError("release_unresolved", "Original release was not accepted; do not replay it blindly.")
        except Exception as exc:
            release_error = type(exc).__name__ + ": " + str(exc)

    # Failure in preflight or intent persistence occurs before any post.
    current()
    for packet in packets:
        if not packet.release_intent and backend.verify_input_context(packet) is not True:
            raise FoundryError("input_context_unverified", "Key/layout and actual modifier state must be explicitly verified.")
    journal({"state": "dispatch_intent", "packet_count": len(packets), "target": deepcopy(expected),
             "packets": [{"message": int(p.message), "wparam": p.wparam, "lparam": p.lparam,
                          "offset_ms": p.offset_ms, "release": p.release_intent} for p in packets],
             "semantic_success": False, "qualification_granted": False})
    start = backend.monotonic()
    try:
        for packet in packets:
            if packet.release_intent:
                delay = packet.offset_ms / 1000 - (backend.monotonic() - start)
                if delay > 0:
                    backend.sleep(delay)
                release_original()
                break
            delay = packet.offset_ms / 1000 - (backend.monotonic() - start)
            if delay > 0:
                backend.sleep(delay)
            record = {"state": "post_attempt", "message": int(packet.message), "release": False}
            if armed:
                deferred_audit.append(record)
            else:
                journal(record)
            # Persistence can stall or permit user/recorder changes. Recheck after
            # it, immediately before the native sender's own leased post check.
            current()
            if backend.verify_input_context(packet) is not True:
                raise FoundryError("input_context_unverified", "Key/layout or modifier state changed before the next packet.")
            # A thrown call can follow queue acceptance. Preserve release obligation.
            if packet.message in (Message.LEFT_DOWN, Message.RIGHT_DOWN, Message.KEY_DOWN):
                armed = True
            last_point = packet.lparam
            attempted = True
            accepted = backend.post(expected["hwnd"], packet)
            if accepted is False and packet.message in (Message.LEFT_DOWN, Message.RIGHT_DOWN, Message.KEY_DOWN):
                armed = False  # explicit native refusal, not a lost/unknown result
            if accepted is not True:
                raise FoundryError("post_unresolved", "Packet acceptance was refused or not established; no replay.")
            events.append({"message": int(packet.message), "release": False, "accepted": True})
            record = {"state": "post_result", "message": int(packet.message), "accepted": True}
            if armed:
                deferred_audit.append(record)
            else:
                journal(record)
        current()
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)
    finally:
        release_original()
    for record in deferred_audit:
        try:
            journal(dict(record, persisted_after_release=True))
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
            break
    result = {"ok": error is None and release_error is None, "queue_accepted": error is None and release_error is None,
              "outcome": "queue_only" if error is None and release_error is None else "unresolved" if attempted else "not_dispatched",
              "input_attempted": attempted, "events": events, "error": error, "release_error": release_error,
              "semantic_success": False, "game_qualified": False, "action_replayed": False}
    # Optional final evidence cannot prevent an already-required release.
    journal({"state": "sequence_result", "result": result})
    return result
