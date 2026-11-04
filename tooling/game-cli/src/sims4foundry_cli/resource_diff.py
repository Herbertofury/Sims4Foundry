"""Bounded comparison of exact TGI keys and opaque stored resource ranges.

The shared DBPF parser owns all reads and source verification. This module
never extracts, decompresses, interprets or executes resource bodies. Equal
stored hashes and lengths do not prove decoded equality or a safe update.
"""

import hashlib
import json

from . import package_resources as packages
from .errors import FoundryError
from .io import MAX_JSON


_SOURCE_FIELDS = ("source", "name", "bytes", "sha256", "source_stat", "dbpf_version", "index", "resource_count")
_SNAPSHOT_FIELDS = ("stored_bytes", "memory_bytes", "compression", "extended_compression", "committed",
                    "offset", "entry", "index_entry_offset", "index_entry_bytes")
_METADATA_FIELDS = ("memory_bytes", "compression", "extended_compression", "committed")
_LAYOUT_FIELDS = ("offset", "entry", "index_entry_offset", "index_entry_bytes")


class _RangeCollector:
    """Consume validated, nonoverlapping ranges from sequential bounded chunks.

    There is at most one active nonempty hash state. Zero extents need no
    reads and may share offsets with other resources or the index. Chunks are
    not retained, and the collector has no path, descriptor or seek capability.
    """

    def __init__(self, rows):
        empty_hash = hashlib.sha256(b"").hexdigest()
        self._hashes = {row["tgi"]: empty_hash for row in rows if not row["stored_bytes"]}
        self._ranges = sorted((row["offset"], row["offset"] + row["stored_bytes"], row["tgi"])
                              for row in rows if row["stored_bytes"])
        self._cursor = 0
        self._position = 0
        self._consumed = 0
        self._active = None

    def feed(self, offset, chunk):
        if offset != self._position:
            packages._changed()
        end = offset + len(chunk)
        self._position = end
        view = memoryview(chunk)
        while self._cursor < len(self._ranges):
            start, stop, key = self._ranges[self._cursor]
            if start >= end:
                break
            first, last = max(start, offset), min(stop, end)
            if first != start + self._consumed or last <= first:
                packages._changed()
            if self._active is None:
                self._active = hashlib.sha256()
            self._active.update(view[first - offset:last - offset])
            self._consumed += last - first
            if last != stop:
                break
            self._hashes[key] = self._active.hexdigest()
            self._active = None
            self._consumed = 0
            self._cursor += 1

    def finish(self):
        if self._cursor != len(self._ranges) or self._active is not None or self._consumed:
            packages._changed()
        return self._hashes


def _snapshot(row, checksums):
    return {"stored_sha256": checksums[row["tgi"]], **{field: row[field] for field in _SNAPSHOT_FIELDS}}


def _bounded_result(result):
    # Bound the complete stdout representation, including its final newline,
    # rather than estimating row sizes or silently truncating a changed report.
    if len(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2).encode("utf-8")) + 1 > MAX_JSON:
        raise FoundryError("oversized_json", "Resource comparison exceeds the 4 MiB JSON bound; reduce selected package/report size.")
    return result


def compare_resources(before, after):
    """Compare two explicit version selections, including the same source.

    Same paths and detected hard-link aliases are valid unchanged comparisons,
    not duplicate owners. Both selections still undergo complete verification.
    No combined-entry cap is imposed in addition to the parser's per-file
    bounds and the actual complete JSON output bound.
    """
    prepared = []
    for value in (before, after):
        path = packages._source_path(value)
        prepared.append((path, packages._source_stat(path)))
    reports, checksums = [], []
    for path, stamp in prepared:
        report, hashes = packages._inspect_resources(path, _RangeCollector)
        if report["source_stat"] != stamp:
            packages._changed()
        reports.append(report)
        checksums.append(hashes)
    old, new = reports
    old_rows, new_rows = ({row["tgi"]: row for row in report["resources"]} for report in reports)
    old_keys, new_keys = set(old_rows), set(new_rows)
    added = [{"tgi": key, **_snapshot(new_rows[key], checksums[1])} for key in sorted(new_keys - old_keys)]
    removed = [{"tgi": key, **_snapshot(old_rows[key], checksums[0])} for key in sorted(old_keys - new_keys)]
    changed = []
    stored_count = metadata_count = layout_count = 0
    for key in sorted(old_keys & new_keys):
        left, right = _snapshot(old_rows[key], checksums[0]), _snapshot(new_rows[key], checksums[1])
        stored = any(left[field] != right[field] for field in ("stored_bytes", "stored_sha256"))
        metadata = any(left[field] != right[field] for field in _METADATA_FIELDS)
        layout = any(left[field] != right[field] for field in _LAYOUT_FIELDS)
        stored_count += stored
        metadata_count += metadata
        layout_count += layout
        if stored or metadata or layout:
            changed.append({"tgi": key, "stored_bytes_changed": stored, "metadata_changed": metadata,
                            "layout_changed": layout, "before": left, "after": right})
    shared = len(old_keys & new_keys)
    result = {
        "ok": True, "schema": 1, "kind": "foundry_package_resource_diff",
        "before": {field: old[field] for field in _SOURCE_FIELDS},
        "after": {field: new[field] for field in _SOURCE_FIELDS},
        "package_bytes_equal": old["bytes"] == new["bytes"] and old["sha256"] == new["sha256"],
        "equality_basis": "sha256_and_length", "index_layout_changed": old["index"] != new["index"],
        "added": added, "removed": removed, "changed": changed,
        "before_count": len(old_keys), "after_count": len(new_keys),
        "added_count": len(added), "removed_count": len(removed), "shared_count": shared,
        "changed_count": len(changed), "unchanged_count": shared - len(changed),
        "stored_bytes_changed_count": stored_count, "metadata_changed_count": metadata_count,
        "layout_changed_count": layout_count,
        "body_handling": "opaque_stored_ranges_sha256", "resource_contents_validated": False,
        "decoded_bytes_compared": False, "decompressed": False, "code_executed": False,
        "game_modified": False, "compatibility_verified": False, "load_order_inferred": False,
        "interpretation": "Only opaque stored bytes and indexed fields are compared. Remaining container bytes are not interpreted. Equal stored hashes and lengths do not prove decoded equality, compatibility, a safe update or a pure repack.",
    }
    _bounded_result(result)
    # Detect earlier-input drift while inspecting the later source, and drift
    # during report construction/bounding. No publication precedes these checks.
    for report in reports:
        packages._verify_snapshot(report)
    return result
