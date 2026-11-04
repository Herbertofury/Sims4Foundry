"""Read-only, bounded DBPF 2.1 index inspection and exact TGI overlap audit.

This is an original narrow parser, not a wrapper around a mod or vendor module.
Format references, inspected 2026-11-04:
https://raw.githubusercontent.com/s4ptacle/Sims4Tools/master/s4pi/Package/Package.cs
https://raw.githubusercontent.com/s4ptacle/Sims4Tools/master/s4pi/Package/PackageIndex.cs
https://raw.githubusercontent.com/Llama-Logic/LlamaLogic/main/LlamaLogic.Packages/DataBasePackedFile.cs
https://raw.githubusercontent.com/thequux/s4py/master/lib/s4py/package/dbpf.py

Supported subset: 96-byte DBPF 2.1 header, index marker 3, zero reserved
fields, shared type/group/instance-high bits 0..2, little-endian 32-bit entry
fields, and optional compression/committed words indicated by size bit 31.
The 64-bit index position at 64 takes precedence over the legacy position at
40; inconsistent nonzero positions refuse. Resources may follow the index.
Deleted records, unknown compression methods/layouts, duplicate keys and any
overlapping nonempty resource ranges refuse rather than being silently hidden.

Only the header and index are parsed. Whole-file SHA-256 uses bounded opaque
streaming reads, twice, to bind results and detect drift. Resource bodies are
never extracted, interpreted, decompressed or executed. Stat/hash checks detect
ordinary concurrent edits; they are not a hostile-filesystem snapshot guarantee.
"""

from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import os
import stat
import struct

from .errors import FoundryError
from .io import MAX_JSON, plain_path, safe_relative


HEADER_BYTES = 96
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_RESOURCE_BYTES = 512 * 1024 * 1024
MAX_INDEX_BYTES = 1024 * 1024
MAX_ENTRIES = 8192
MAX_PACKAGES = 128
MAX_AUDIT_ENTRIES = 16384
MAX_AUDIT_BYTES = 1024 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
_WINDOWS = os.name == "nt"
COMPRESSION_METHODS = {
    0x0000: "uncompressed",
    0x5A42: "zlib",
    0xFFFE: "streamable",
    0xFFFF: "internal",
}


def _changed():
    raise FoundryError("package_changed", "Package source changed during inspection; no result was accepted.")


def _stamp(info):
    # atime is intentionally absent: reading the source may update it.
    return {"device": info.st_dev, "inode": info.st_ino, "bytes": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "birthtime_ns": getattr(info, "st_birthtime_ns", None),
            "mode": info.st_mode, "file_attributes": getattr(info, "st_file_attributes", 0)}


def _same_open_source(path_stamp, descriptor_stamp):
    # CPython on Windows may expose creation time through stat().st_ctime_ns
    # but change time through fstat().st_ctime_ns (cpython issue #157671).
    # Compare the explicit birth time across APIs, retaining each API's FULL
    # original stamp separately for subsequent drift checks. Do not discard
    # ctime from same-API checks or relax identity/size/mtime/attribute matching.
    if (_WINDOWS and path_stamp["birthtime_ns"] is not None
            and descriptor_stamp["birthtime_ns"] is not None):
        return ({k: v for k, v in path_stamp.items() if k != "ctime_ns"}
                == {k: v for k, v in descriptor_stamp.items() if k != "ctime_ns"})
    return path_stamp == descriptor_stamp


def _source_path(path):
    path = plain_path(path)
    if (path.suffix.lower() != ".package" or not safe_relative(path.name)
            or len(str(path).encode("utf-8")) > 4096):
        raise FoundryError("unsupported_package", "Select a regular, unlinked .package with a portable filename.")
    return path


def _source_stat(path):
    info = plain_path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise FoundryError("unsupported_package", "Only regular package files are accepted.")
    if not HEADER_BYTES <= info.st_size <= MAX_PACKAGE_BYTES:
        raise FoundryError("package_size", "Package must be at least 96 bytes and at most 512 MiB.")
    return _stamp(info)


@contextmanager
def _open_source(path, expected):
    # O_NOFOLLOW protects the leaf when supported. The shared plain_path guard
    # also rejects observed linked ancestors and Windows reparse points.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                         | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        # Keep descriptor ownership explicit even if wrapper construction fails.
        # closefd=False prevents an uncertain constructor failure from closing
        # and potentially reusing the descriptor behind this finally block.
        with os.fdopen(descriptor, "rb", buffering=0, closefd=False) as stream:
            descriptor_stamp = _stamp(os.fstat(stream.fileno()))
            if not _same_open_source(expected, descriptor_stamp):
                _changed()
            yield stream, descriptor_stamp
    finally:
        os.close(descriptor)


def _check_source(path, stream, expected, descriptor_expected):
    if _stamp(os.fstat(stream.fileno())) != descriptor_expected:
        _changed()
    try:
        current = _source_stat(path)
    except FileNotFoundError:
        _changed()
    if current != expected:
        _changed()


def _read_exact(stream, offset, size):
    """Used only for the pre-bounded header/index, never a resource body."""
    stream.seek(offset)
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise FoundryError("package_truncated", "Package header or index is truncated.")
        data.extend(chunk)
    return bytes(data)


def _hash_source(stream, expected_size, chunk_callback=None):
    """Opaque streaming hashing, with no unbounded read even if a file grows."""
    stream.seek(0)
    checksum = hashlib.sha256()
    remaining = expected_size
    while remaining:
        chunk = stream.read(min(remaining, HASH_CHUNK_BYTES))
        if not chunk:
            _changed()
        checksum.update(chunk)
        if chunk_callback is not None:
            chunk_callback(expected_size - remaining, chunk)
        remaining -= len(chunk)
    if stream.read(1):
        _changed()
    return checksum.hexdigest()


def _header(raw, file_size):
    if raw[:4] != b"DBPF":
        raise FoundryError("invalid_package", "Package does not have a DBPF signature.")
    words = struct.unpack("<24I", raw)
    if (words[1], words[2]) != (2, 1):
        raise FoundryError("unsupported_dbpf_version", "Resource inspection supports DBPF 2.1 only.",
                           major=words[1], minor=words[2])
    if words[15] != 3 or any(words[i] for i in (5, 8, 12, 13, 14, 18, 19, 20, 21, 22, 23)):
        raise FoundryError("unsupported_dbpf_layout", "Expected index marker 3 and zero reserved header fields.")
    count, legacy_offset, size = words[9:12]
    offset64 = struct.unpack_from("<Q", raw, 64)[0]
    if legacy_offset and offset64 and legacy_offset != offset64:
        raise FoundryError("ambiguous_index_offset", "Nonzero legacy and 64-bit index positions disagree.")
    offset = offset64 or legacy_offset
    if count > MAX_ENTRIES or size > MAX_INDEX_BYTES:
        raise FoundryError("package_index_bound", "Index exceeds the 8192-entry or 1 MiB bound.")
    if count == 0 and size == 0 and offset == 0:
        return {"entry_count": 0, "offset": 0, "bytes": 0, "version_marker": 3,
                "position_field": "absent", "shared_flags": "0x00000000", "shared_fields": {}}
    # Python integers do not wrap; subtraction avoids accidentally treating an
    # overflowing offset + size as a valid extent in a future port.
    if offset < HEADER_BYTES or offset > file_size or size < 4 or size > file_size - offset:
        raise FoundryError("package_index_range", "Index extent is outside the package or overlaps its header.")
    # Four fixed per-entry DWORDs remain even with all three shared fields.
    if count > (size - 4) // 16:
        raise FoundryError("package_index_count", "Entry count cannot fit in the declared index extent.")
    return {"entry_count": count, "offset": offset, "bytes": size, "version_marker": 3,
            "position_field": "offset64" if offset64 else "legacy_offset32"}


def _parse_index(raw, metadata, file_size):
    if not raw:
        return []
    position = 0

    def u32():
        nonlocal position
        if position > len(raw) - 4:
            raise FoundryError("package_index_truncated", "An index entry is truncated.")
        value = struct.unpack_from("<I", raw, position)[0]
        position += 4
        return value

    flags = u32()
    if flags & ~0x7:
        raise FoundryError("unsupported_index_flags", "Only shared type, group and instance-high index flags are supported.",
                           shared_flags=f"0x{flags:08X}")
    shared = {field: u32() for bit, field in enumerate(("type", "group", "instance_high")) if flags & (1 << bit)}
    metadata["shared_flags"] = f"0x{flags:08X}"
    metadata["shared_fields"] = {name: f"0x{value:08X}" for name, value in shared.items()}
    minimum = 4 * (7 - len(shared))
    remaining = len(raw) - position
    count = metadata["entry_count"]
    if not count * minimum <= remaining <= count * (minimum + 4):
        raise FoundryError("package_index_size", "Index size does not match its count and supported entry layout.")
    rows, keys, ranges = [], set(), []
    for ordinal in range(count):
        start_index = position
        fields = [shared[name] if name in shared else u32() for name in ("type", "group", "instance_high")]
        instance_low, offset, packed_size, memory_size = u32(), u32(), u32(), u32()
        extended = bool(packed_size & 0x80000000)
        word = u32() if extended else 0
        compression = word & 0xFFFF
        committed = word >> 16 if extended else None
        if compression == 0xFFE0:
            raise FoundryError("unsupported_deleted_resource", "Deleted index records require separate handling and are not audited as owners.")
        if compression not in COMPRESSION_METHODS or (extended and committed not in (0, 1)):
            raise FoundryError("unsupported_index_entry", "Unknown compression method or committed marker in resource index.",
                               entry=ordinal, compression=f"0x{compression:04X}", committed=committed)
        stored_size = packed_size & 0x7FFFFFFF
        if stored_size > MAX_RESOURCE_BYTES or memory_size > MAX_RESOURCE_BYTES:
            raise FoundryError("resource_size", "Resource declared size exceeds the 512 MiB bound.", entry=ordinal)
        if offset < HEADER_BYTES or offset > file_size or stored_size > file_size - offset:
            raise FoundryError("resource_range", "Resource extent is outside the package or overlaps its header.", entry=ordinal)
        index_start, index_end = metadata["offset"], metadata["offset"] + metadata["bytes"]
        if stored_size and offset < index_end and offset + stored_size > index_start:
            raise FoundryError("resource_index_overlap", "Resource extent overlaps the index.", entry=ordinal)
        if compression == 0 and stored_size != memory_size:
            raise FoundryError("resource_size_mismatch", "Uncompressed resource stored and memory sizes disagree.", entry=ordinal)
        key = (fields[0], fields[1], (fields[2] << 32) | instance_low)
        if key in keys:
            raise FoundryError("duplicate_resource_key", "A package contains a duplicate exact type/group/instance key.",
                               tgi=_tgi(key), entry=ordinal)
        keys.add(key)
        if stored_size:
            ranges.append((offset, offset + stored_size, ordinal))
        rows.append({"type": f"0x{key[0]:08X}", "group": f"0x{key[1]:08X}",
                     "instance": f"0x{key[2]:016X}", "tgi": _tgi(key), "entry": ordinal,
                     "index_entry_offset": metadata["offset"] + start_index,
                     "index_entry_bytes": position - start_index,
                     "offset": offset, "stored_bytes": stored_size, "memory_bytes": memory_size,
                     "extended_compression": extended, "compression": f"0x{compression:04X}",
                     "compression_method": COMPRESSION_METHODS[compression], "committed": committed})
    if position != len(raw):
        raise FoundryError("package_index_size", "Unexplained bytes remain in the index.")
    previous_end, previous_ordinal = 0, None
    for start, end, ordinal in sorted(ranges):
        if start < previous_end:
            raise FoundryError("resource_overlap", "Nonempty resource extents overlap; aliasing is not supported.",
                               entries=[previous_ordinal, ordinal])
        previous_end, previous_ordinal = end, ordinal
    return rows


def _tgi(key):
    return f"{key[0]:08X}:{key[1]:08X}:{key[2]:016X}"


def _bounded_result(result):
    # CLI uses indent=2; enforce that larger representation as well as the
    # compact new_json representation, including the terminating newline.
    if len(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2).encode("utf-8")) + 1 > MAX_JSON:
        raise FoundryError("oversized_json", "Resource report exceeds the 4 MiB JSON bound; inspect fewer packages.")
    return result


def inspect_resources(path):
    """Return validated index metadata, not gameplay/content compatibility."""
    return _inspect_resources(path)[0]


def _inspect_resources(path, collector_factory=None):
    """Return the original report and optional separately collected details.

    The private collector sees only validated rows and second-pass chunks, not
    a path or stream. Accept its result only after the parser's source checks.
    """
    path = _source_path(path)
    before_stat = _source_stat(path)
    size = before_stat["bytes"]
    with _open_source(path, before_stat) as (stream, descriptor_stat):
        header = _read_exact(stream, 0, HEADER_BYTES)
        metadata = _header(header, size)
        before_hash = _hash_source(stream, size)
        _check_source(path, stream, before_stat, descriptor_stat)
        if _read_exact(stream, 0, HEADER_BYTES) != header:
            _changed()
        raw_index = _read_exact(stream, metadata["offset"], metadata["bytes"]) if metadata["bytes"] else b""
        resources = _parse_index(raw_index, metadata, size)
        collector = collector_factory(resources) if collector_factory is not None else None
        if collector is None:
            # Preserve the original call shape for existing callers and mocks.
            after_hash = _hash_source(stream, size)
        else:
            def observe(offset, chunk):
                # Bind the parsed header/index to the hash-producing pass, even
                # if a temporary metadata edit was hidden from stat checks.
                view = memoryview(chunk)
                for start, raw in ((0, header), (metadata["offset"], raw_index)):
                    first, last = max(offset, start), min(offset + len(chunk), start + len(raw))
                    if first < last and view[first - offset:last - offset] != raw[first - start:last - start]:
                        _changed()
                collector.feed(offset, chunk)

            after_hash = _hash_source(stream, size, observe)
        _check_source(path, stream, before_stat, descriptor_stat)
        if after_hash != before_hash:
            _changed()
        details = collector.finish() if collector is not None else None
    report = _bounded_result({"ok": True, "schema": 1, "kind": "foundry_package_resources",
                            "source": str(path), "name": path.name, "bytes": size, "sha256": before_hash,
                            "source_stat": before_stat, "dbpf_version": "2.1",
                            "validation": "bounded_header_and_index", "index": metadata,
                            "resource_count": len(resources), "resources": resources,
                            "body_handling": "opaque_stream_for_source_hash_only",
                            "resource_contents_validated": False, "decompressed": False,
                            "code_executed": False, "game_modified": False,
                            "compatibility_verified": False})
    return report, details


def _verify_snapshot(report):
    path = _source_path(report["source"])
    expected = report["source_stat"]
    if _source_stat(path) != expected:
        _changed()
    with _open_source(path, expected) as (stream, descriptor_stat):
        actual = _hash_source(stream, report["bytes"])
        _check_source(path, stream, expected, descriptor_stat)
        if actual != report["sha256"]:
            _changed()


def audit_overlaps(paths):
    """Audit explicit package inputs without guessing load order or a winner.

    Identical complete-file hashes identify duplicate package bytes. For other
    exact TGI overlaps, resource byte equality is unknown, even when sizes and
    index metadata match. An override candidate is not a proven incompatibility.
    Repeated paths and hard-link aliases refuse instead of inventing owners.
    """
    if not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= MAX_PACKAGES:
        raise FoundryError("package_audit_bound", "Supply an explicit list of 1 through 128 package paths.")
    prepared, seen_paths, seen_files, total_bytes = [], set(), set(), 0
    for value in paths:
        path = _source_path(value)
        stamp = _source_stat(path)
        identity = stamp["device"], stamp["inode"]
        path_key = os.path.normcase(str(path))
        if path_key in seen_paths or (stamp["inode"] and identity in seen_files):
            raise FoundryError("duplicate_package_input", "The same source file was supplied more than once.", source=str(path))
        seen_paths.add(path_key)
        seen_files.add(identity)
        total_bytes += stamp["bytes"]
        if total_bytes > MAX_AUDIT_BYTES:
            raise FoundryError("package_audit_bound", "Combined package input exceeds 1 GiB.")
        prepared.append((path, stamp))
    reports, owners_by_key, by_hash, total_entries = [], defaultdict(list), defaultdict(list), 0
    for path, stamp in prepared:
        report = inspect_resources(path)
        if report["source_stat"] != stamp:
            _changed()
        total_entries += report["resource_count"]
        if total_entries > MAX_AUDIT_ENTRIES:
            raise FoundryError("package_audit_bound", "Combined index entry count exceeds 16384.")
        owner = {"source": report["source"], "name": report["name"], "sha256": report["sha256"]}
        by_hash[report["sha256"]].append(owner)
        for resource in report["resources"]:
            owners_by_key[resource["tgi"]].append((owner, resource))
        reports.append(report)
    overlaps = []
    for key, rows in sorted(owners_by_key.items()):
        if len(rows) < 2:
            continue
        identical_package = len({owner["sha256"] for owner, _ in rows}) == 1
        example = rows[0][1]
        overlaps.append({"tgi": key, **{field: example[field] for field in ("type", "group", "instance")},
                         "classification": "identical_package_bytes" if identical_package else "override_candidate",
                         "resource_bytes_equal": True if identical_package else None,
                         "equality_basis": "whole_package_sha256" if identical_package else "not_compared",
                         "owners": [{**owner, **{field: row[field] for field in
                                    ("entry", "offset", "stored_bytes", "memory_bytes", "compression")}}
                                    for owner, row in rows]})
    result = {"ok": True, "schema": 1, "kind": "foundry_package_overlap_audit",
              "package_count": len(reports), "resource_count": total_entries,
              "unique_tgi_count": len(owners_by_key), "overlap_count": len(overlaps), "overlaps": overlaps,
              "packages": [{field: report[field] for field in
                           ("source", "name", "bytes", "sha256", "source_stat", "dbpf_version", "index", "resource_count")}
                           for report in reports],
              "duplicate_package_groups": [{"sha256": checksum, "owners": rows} for checksum, rows in sorted(by_hash.items()) if len(rows) > 1],
              "interpretation": "Exact TGI overlap is an override candidate, not proof of incompatibility. Distinct package hashes do not establish different resource bytes.",
              "load_order_inferred": False, "compatibility_verified": False,
              "resource_contents_validated": False, "game_modified": False,
              "code_executed": False, "decompressed": False}
    _bounded_result(result)
    # Catch changes to an earlier package while later inputs were inspected.
    for report in reports:
        _verify_snapshot(report)
    return result
