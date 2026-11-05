"""Compare an identified recording frame to an explicitly pinned UI reference."""
import argparse
import json
import math
import re

from . import companion_build, visual_probe
from .errors import FoundryError
from .io import canonical, digest, external_path, loads, new_json, read_json
from .path_identity import same_windows_path
from .windows import IDENTITY_KEYS, same_identity, validate_identity

HASH = re.compile(r"[0-9a-f]{64}\Z")
MAX_RECEIPT = 32768


def _require(value, code, message):
    if not value:
        raise FoundryError(code, message)


def _receipt(path):
    raw, checksum, _ = companion_build._regular(path, MAX_RECEIPT)
    value = loads(raw)
    _require(raw == canonical(value) + b"\n", "readback_receipt", "Recording receipt is not canonical.")
    return value, checksum


def inspect_recording(directory, completion_sha256, expected_identity, reference,
                      reference_sha256, region, *, max_channel_difference=0,
                      max_mismatched_pixels=0):
    """Compare the last receipt-bound frame; never assert game semantics."""
    validate_identity(expected_identity)
    _require(type(completion_sha256) is str and HASH.fullmatch(completion_sha256),
             "readback_receipt", "An exact completion-receipt hash is required.")
    root = external_path(directory)
    complete, actual_hash = _receipt(root / "recording-complete.json")
    _require(actual_hash == completion_sha256 and type(complete) is dict
             and set(complete) == {"ok","outcome","directory","frames","intent_sha256","input_sent",
                                   "semantic_state_verified","live_input_qualified","capture_time_verified"}
             and complete["ok"] is True and complete["outcome"] == "frames_recorded"
             and complete["input_sent"] is False and complete["semantic_state_verified"] is False
             and complete["live_input_qualified"] is False and complete["capture_time_verified"] is False
             and type(complete["directory"]) is str and same_windows_path(complete["directory"], str(root)),
             "readback_receipt", "Recording completion is invalid or has changed.")
    intent, _ = _receipt(root / "recording-intent.json")
    _require(type(intent) is dict and set(intent) == {"schema","identity","ffmpeg_sha256","frames","seconds","input_sent"}
             and type(intent["schema"]) is int and intent["schema"] == 1 and intent["input_sent"] is False
             and type(intent["frames"]) is int and 1 <= intent["frames"] <= 10
             and type(intent["seconds"]) in (int,float) and math.isfinite(intent["seconds"])
             and 0 < intent["seconds"] <= 60 and type(intent["ffmpeg_sha256"]) is str
             and HASH.fullmatch(intent["ffmpeg_sha256"]) and digest(intent) == complete["intent_sha256"],
             "readback_receipt", "Recording intent is invalid or changed.")
    same_identity(expected_identity, intent["identity"])
    rows = complete["frames"]
    _require(type(rows) is list and len(rows) == intent["frames"],
             "readback_receipt", "Recording frame count is inconsistent.")
    previous = 0
    for index,row in enumerate(rows,1):
        _require(type(row) is dict and set(row) == {"file","sequence","sha256","width","height","received_at_monotonic"}
                 and row["file"] == "frame-%04d.png" % index
                 and type(row["sequence"]) is int and row["sequence"] > previous
                 and type(row["sha256"]) is str and HASH.fullmatch(row["sha256"])
                 and type(row["width"]) is int and 1 <= row["width"] <= 16384
                 and type(row["height"]) is int and 1 <= row["height"] <= 16384
                 and type(row["received_at_monotonic"]) in (int,float)
                 and math.isfinite(row["received_at_monotonic"]) and row["received_at_monotonic"] >= 0,
                 "readback_receipt", "Recording frame metadata is invalid.")
        stored, _ = _receipt(root / ("frame-%04d.json" % index))
        _require(canonical(stored) == canonical(row), "readback_receipt", "Frame metadata changed.")
        previous = row["sequence"]
    selected = rows[-1]
    result = visual_probe.compare(root / selected["file"], selected["sha256"], reference,
                                  reference_sha256, region,
                                  max_channel_difference=max_channel_difference,
                                  max_mismatched_pixels=max_mismatched_pixels)
    _require(result["image_size"] == [selected["width"],selected["height"]],
             "readback_receipt", "Frame dimensions do not match the receipt.")
    target = {"schema":1, "kind":"foundry_windows_identity",
              **{key:expected_identity[key] for key in IDENTITY_KEYS}}
    result.update({"completion_sha256":completion_sha256, "recording_intent_sha256":complete["intent_sha256"],
                   "target_sha256":digest(target), "frame_sequence":selected["sequence"],
                   "frame_received_at_monotonic":selected["received_at_monotonic"],
                   "capture_time_verified":False, "caused_by_input_verified":False})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", required=True)
    parser.add_argument("--completion-sha256", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--reference-sha256", required=True)
    parser.add_argument("--region", type=int, nargs=4, required=True, metavar=("X","Y","W","H"))
    parser.add_argument("--max-channel-difference", type=int, default=0)
    parser.add_argument("--max-mismatched-pixels", type=int, default=0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        destination = external_path(args.output)
        if destination.exists() or destination.is_symlink():
            raise FoundryError("output_exists", "Choose a new readback output file.")
        result = inspect_recording(args.recording, args.completion_sha256, read_json(args.identity),
                                   args.reference, args.reference_sha256, args.region,
                                   max_channel_difference=args.max_channel_difference,
                                   max_mismatched_pixels=args.max_mismatched_pixels)
        new_json(destination, result)
    except FoundryError as error:
        result = error.as_dict()
    except (OSError, ValueError, TypeError, KeyError) as error:
        result = {"ok":False, "code":"local_failure", "error_type":type(error).__name__}
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    if result.get("ok") and result.get("matched"):
        return 0
    return 1 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
