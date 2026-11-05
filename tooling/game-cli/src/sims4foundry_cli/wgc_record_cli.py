"""Capture a bounded WGC frame sequence from an explicitly identified window."""
import argparse
from copy import deepcopy
import json
import time

from . import companion_build
from .errors import FoundryError
from .io import canonical, digest, external_path, loads, new_json, read_json, sha256
from .png import inspect_png
from .wgc_recorder import WgcRecorder
from .windows import validate_identity

MAX_FRAMES = 10
MAX_SECONDS = 60.0


def record_frames(identity, ffmpeg, ffmpeg_sha256, destination, *, frames=3, seconds=10.0,
                  recorder_factory=None, clock=None, sleep=None):
    validate_identity(identity)
    if type(frames) is not int or not 1 <= frames <= MAX_FRAMES:
        raise FoundryError("invalid_frames", "Select between one and ten frames.")
    if type(seconds) not in (int, float) or not 0 < seconds <= MAX_SECONDS:
        raise FoundryError("invalid_timeout", "Recording time must be positive and at most 60 seconds.")
    output = external_path(destination)
    if output.exists() or output.is_symlink():
        raise FoundryError("output_exists", "Choose a new recording directory.")
    recorder_factory = recorder_factory or WgcRecorder
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    identity = loads(canonical(identity))
    validate_identity(identity)
    recorder_view = deepcopy(identity)
    recorder = recorder_factory(recorder_view, ffmpeg, ffmpeg_sha256)
    if canonical(recorder_view) != canonical(identity):
        raise FoundryError("identity_changed", "Recorder construction changed the selected identity.")
    output.mkdir(parents=True, exist_ok=False)
    intent = {"schema":1, "identity":identity, "ffmpeg_sha256":ffmpeg_sha256,
              "frames":frames, "seconds":seconds, "input_sent":False}
    new_json(output / "recording-intent.json", intent)
    rows, last_sequence, failure, interrupted = [], 0, None, None
    try:
        recorder.start()
        deadline = clock() + float(seconds)
        while len(rows) < frames:
            if clock() >= deadline:
                raise FoundryError("recording_timeout", "Not enough fresh frames arrived before the deadline.")
            try:
                frame = recorder.sample()
            except FoundryError as error:
                if error.code != "recording_unavailable":
                    raise
                sleep(min(.05, max(0.0, deadline - clock())))
                continue
            if frame.sequence <= last_sequence:
                sleep(min(.05, max(0.0, deadline - clock())))
                continue
            path = output / ("frame-%04d.png" % (len(rows) + 1))
            companion_build._publish_archive(path, frame.png)
            width, height = inspect_png(path)
            if sha256(path) != frame.sha256 or (width,height) != (frame.width,frame.height):
                raise FoundryError("recording_frame_changed", "Recorded frame did not match the stream.")
            last_sequence = frame.sequence
            row = {"file":path.name, "sequence":frame.sequence, "sha256":frame.sha256,
                   "width":width, "height":height, "received_at_monotonic":frame.received_at}
            new_json(output / ("frame-%04d.json" % (len(rows) + 1)), row)
            rows.append(row)
    except BaseException as error:
        if isinstance(error, Exception):
            failure = error.code if isinstance(error, FoundryError) else "recording_failed"
        else:
            interrupted = error
    try:
        closed = recorder.close() is True
    except Exception:
        closed = False
    if not closed:
        return {"ok":False, "outcome":"unresolved", "code":"recording_cleanup_unresolved",
                "directory":str(output), "frames":rows, "input_sent":False}
    if interrupted is not None:
        raise interrupted
    if failure is not None:
        return {"ok":False, "outcome":"incomplete", "code":failure,
                "directory":str(output), "frames":rows, "input_sent":False}
    result = {"ok":True, "outcome":"frames_recorded", "directory":str(output), "frames":rows,
              "intent_sha256":digest(intent),
              "input_sent":False, "semantic_state_verified":False, "live_input_qualified":False,
              "capture_time_verified":False}
    try:
        new_json(output / "recording-complete.json", result)
    except Exception:
        return {"ok":False, "outcome":"unresolved", "code":"recording_receipt_unavailable",
                "directory":str(output), "frames":rows, "input_sent":False}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffmpeg-sha256", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--frames", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        result = record_frames(read_json(args.identity), args.ffmpeg, args.ffmpeg_sha256,
                               args.output, frames=args.frames, seconds=args.seconds)
    except FoundryError as error:
        result = error.as_dict()
    except (OSError, ValueError, TypeError) as error:
        result = {"ok":False, "code":"local_failure", "error_type":type(error).__name__}
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    if result.get("ok"):
        return 0
    return 3 if result.get("outcome") == "unresolved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
