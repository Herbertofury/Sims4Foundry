"""Bounded offline PYC header observations, never a bytecode validity verdict.

Only the pinned CPython v3.7.17 importlib layout is interpreted. An explicit
target is four comparison bytes, not permission to guess a layout or runtime.
See docs/BYTECODE_HEADERS.md for references, limits and loader divergence.
"""
import json
import os
from pathlib import Path
import struct

from . import mods
from .errors import FoundryError
from .io import MAX_JSON


PREFIX_BYTES = 16
REFERENCE_MAGIC = "420d0d0a"
REFERENCE_LAYOUT = "cpython_3_7_17_importlib"
MAX_BYTECODE_MEMBERS = 1024
MAX_MEMBER_NAME_CHARS = 1024
MAX_MEMBER_NAME_UTF8_BYTES = 4096
MAX_MEMBER_NAME_JSON_BYTES = 4096
MAX_TOTAL_NAME_UTF8_BYTES = 256 * 1024
MAX_TOTAL_NAME_JSON_BYTES = 256 * 1024
MAX_SOURCE_PATH_CHARS = 4096


def _target_magic(value):
    if value is None:
        return None
    if (not isinstance(value, str) or len(value) != 8
            or any(char not in "0123456789abcdefABCDEF" for char in value)):
        raise FoundryError("invalid_target_magic", "Target magic must be exactly eight ASCII hexadecimal characters in file-byte order.")
    return value.lower()


def _interpret_prefix(prefix, target):
    """Pure interpretation of at most 16 retained bytes; no body operations."""
    magic = prefix[:4].hex() if len(prefix) >= 4 else None
    result = {"prefix_bytes": len(prefix), "prefix_hex": prefix.hex(),
              "magic_hex": magic, "header_status": "truncated_magic",
              "layout_reference": None, "flags_u32": None,
              "hash_based_bit": None, "check_source_bit": None,
              "reference_interpretation": None, "mtime_u32": None,
              "source_size_u32": None, "source_hash_hex": None,
              "target_comparison": ("not_requested" if target is None else
                                    "unavailable" if magic is None else
                                    "match" if magic == target else "mismatch"),
              "findings": []}
    if magic is None:
        return result
    if magic != REFERENCE_MAGIC:
        result["header_status"] = "unsupported_layout"
        return result
    result["layout_reference"] = REFERENCE_LAYOUT
    if len(prefix) < PREFIX_BYTES:
        result["header_status"] = "truncated_reference_header"
        return result
    result["header_status"] = "reference_header_complete"
    flags = struct.unpack_from("<I", prefix, 4)[0]
    result.update(flags_u32=flags, hash_based_bit=bool(flags & 1),
                  check_source_bit=bool(flags & 2))
    if flags & ~3:
        result["findings"].append("outside_reference_flag_mask")
    elif flags & 1:
        result["reference_interpretation"] = "checked_hash" if flags & 2 else "unchecked_hash"
        result["source_hash_hex"] = prefix[8:16].hex()
    else:
        result["reference_interpretation"] = "timestamp" if flags == 0 else "timestamp_bit_clear"
        result["mtime_u32"], result["source_size_u32"] = struct.unpack_from("<II", prefix, 8)
        if flags == 2:
            result["findings"].append("flags_2_loader_divergence")
    return result


def _candidate(row):
    # Selection does not assert that an importer searches this exact spelling.
    return not row.is_dir() and row.orig_filename.lower().endswith(".pyc")


def _preflight_report_names(rows):
    # The existing bounded ZipInfo collection already exists. Do not build a
    # separate selected list or retain any prefixes until ALL budgets pass.
    count = utf8_total = json_total = 0
    for row in rows:
        if not _candidate(row):
            continue
        count += 1
        name = row.orig_filename
        if count > MAX_BYTECODE_MEMBERS or len(name) > MAX_MEMBER_NAME_CHARS:
            raise FoundryError("bytecode_report_bounds", "Bytecode report exceeds its candidate-count or member-name character bound.")
        utf8_size = len(name.encode("utf-8"))
        json_size = len(json.dumps(name, ensure_ascii=True).encode("ascii"))
        utf8_total += utf8_size
        json_total += json_size
        if (utf8_size > MAX_MEMBER_NAME_UTF8_BYTES
                or json_size > MAX_MEMBER_NAME_JSON_BYTES
                or utf8_total > MAX_TOTAL_NAME_UTF8_BYTES
                or json_total > MAX_TOTAL_NAME_JSON_BYTES):
            raise FoundryError("bytecode_report_bounds", "Bytecode report exceeds its individual or cumulative member-name byte bound.")


def _prefix(archive, row):
    # read(16) may internally read/decompress more. This is a retention bound,
    # not a disk-I/O, decompressor-output, CPU or latency guarantee.
    expected = min(PREFIX_BYTES, row.file_size)
    prefix = bytearray()
    with archive.open(row, "r") as member:
        while len(prefix) < expected:
            chunk = member.read(expected - len(prefix))
            if not chunk:
                raise FoundryError("bytecode_prefix_truncated", "Script member ended before its declared prefix extent.")
            prefix.extend(chunk)
    return bytes(prefix)


def _collect_headers(archive, rows, target):
    _preflight_report_names(rows)
    members = [{"member": row.orig_filename, "declared_bytes": row.file_size,
                **_interpret_prefix(_prefix(archive, row), target)}
               for row in rows if _candidate(row)]
    directories = sum(row.is_dir() for row in rows)
    sources = sum(not row.is_dir() and row.orig_filename.lower().endswith(".py") for row in rows)
    return {"members": members, "directory_count": directories, "python_source_count": sources,
            "other_file_count": len(rows) - len(members) - directories - sources}


def _comparison(rows, target):
    counts = {kind + "_count": sum(row["target_comparison"] == kind for row in rows)
              for kind in ("match", "mismatch", "unavailable")}
    status = ("not_requested" if target is None else
              "no_bytecode" if not rows else
              "mismatch" if counts["mismatch_count"] else
              "incomplete" if counts["unavailable_count"] else "match")
    return {"status": status, **counts}


def _bounded_result(result):
    # This is exactly the CLI's larger indented ASCII representation + newline.
    if len(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2).encode("ascii")) + 1 > MAX_JSON:
        raise FoundryError("oversized_json", "Bytecode report exceeds the 4 MiB JSON bound.")
    return result


def inspect_headers(path, target_magic=None):
    """Report selected header bytes only after full archive and source checks."""
    target = _target_magic(target_magic)  # Must precede every filesystem call.
    path = Path(os.path.abspath(path))
    if len(str(path)) > MAX_SOURCE_PATH_CHARS:
        raise FoundryError("bytecode_report_bounds", "Bytecode report source path exceeds its character bound.")
    if path.suffix.lower() != ".ts4script":
        raise FoundryError("unsupported_bytecode_artifact", "Select one regular, unlinked .ts4script file for header inspection.")
    artifact, details = mods._inspect_artifact(path, lambda archive, members: _collect_headers(archive, members, target))
    rows = details["members"]
    return _bounded_result({"ok": True, "schema": 1, "kind": "foundry_script_bytecode_headers",
                            "source": artifact["source"], "name": artifact["name"],
                            "bytes": artifact["bytes"], "sha256": artifact["sha256"],
                            "validation": "bounded_archive_crc_and_header_observations",
                            "member_count": artifact["member_count"],
                            "bytecode_count": len(rows), **details,
                            "target_magic_hex": target, "comparison": _comparison(rows, target),
                            "code_executed": False, "members_extracted": False,
                            "marshal_body_validated": False, "source_hash_verified": False,
                            "importability_verified": False, "compatibility_verified": False,
                            "game_modified": False})
