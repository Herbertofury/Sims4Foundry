"""Real offline artifact inspection, comparison and never-in-place staging.

This module never installs into a live profile, downloads, imports, or executes
mods. A valid hash/archive is not a compatibility or malware-safety verdict.
"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import zipfile
try:
    import zlib
except ImportError:  # Preserve stored-only inspection on Python without zlib.
    zlib = None
from .errors import FoundryError
from .io import (digest, exact_fields, exact_int, external_path, new_json,
                 plain_path, safe_relative, sync_directory)

MAX_ARTIFACT = 512 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
MAX_SCRIPT_ENTRIES = 4096
MAX_SCRIPT_METADATA_BYTES = 4 * 1024 * 1024
_ZIP_END_BYTES = 22
_ZIP_COMMENT_BYTES = 65535
_ZIP_CENTRAL_BYTES = 46
_SCRIPT_ZIP_ERRORS = (zipfile.BadZipFile, RuntimeError, NotImplementedError, UnicodeError)
if zlib is not None:
    _SCRIPT_ZIP_ERRORS += (zlib.error,)
MAX_INVENTORY_ARTIFACTS = 4096
# Count every entry below the selected root, including irrelevant files and
# directories. Root depth is zero; at most 32 nested directories are scanned.
MAX_SCAN_ENTRIES = 16384
MAX_SCAN_DEPTH = 32
# Conservative Windows output portability without assuming long-path support.
# MAX_PATH includes a terminating NUL; these limits count UTF-16 code units.
MAX_MOD_PATH_UNITS = 259
MAX_MOD_COMPONENT_UNITS = 255
MAX_MOD_DIRECTORY_UNITS = 247
MAX_MOD_COMPONENT_BYTES = 255


def _within_path_units(value, maximum):
    if len(value) > maximum:
        return False
    try:
        return len(value.encode("utf-16-le")) <= maximum * 2
    except UnicodeEncodeError:
        return False


def _mod_relative(value):
    # Bound the string and depth before constructing Path.parents prefixes.
    if not isinstance(value, str) or not _within_path_units(value, MAX_MOD_PATH_UNITS):
        return False
    parts = value.split("/")
    return (len(parts) <= MAX_SCAN_DEPTH + 1
            and all(_mod_component(part) for part in parts)
            and safe_relative(value))


def _mod_component(value):
    # Enforce both representations on every host so a Windows-valid spelling
    # does not produce an unstaggable inventory on ordinary UTF-8 filesystems.
    return (_within_path_units(value, MAX_MOD_COMPONENT_UNITS)
            and len(value.encode("utf-8")) <= MAX_MOD_COMPONENT_BYTES)


def _check_staging_paths(destination, artifacts):
    relative_paths = ["staging-intent.json", "staging-complete.json"]
    # new_json publishes through this same-directory temporary-name shape.
    # Its complete path, not just the final receipt name, must remain usable.
    relative_paths.extend("." + name + "." + "0" * 32 + ".pending"
                          for name in tuple(relative_paths))
    relative_paths.extend("artifacts/" + row["relative"] for row in artifacts)
    for relative in relative_paths:
        path = destination / relative
        if (not _within_path_units(str(path), MAX_MOD_PATH_UNITS)
                or not _within_path_units(str(path.parent), MAX_MOD_DIRECTORY_UNITS)
                or any(not _mod_component(part) for part in path.parts if part != path.anchor)):
            raise FoundryError("staging_path_bound", "Staging output exceeds supported path-length bounds.",
                               maximum_path_units=MAX_MOD_PATH_UNITS,
                               maximum_component_units=MAX_MOD_COMPONENT_UNITS,
                               maximum_component_bytes=MAX_MOD_COMPONENT_BYTES,
                               maximum_directory_units=MAX_MOD_DIRECTORY_UNITS)


def _bounded_chunks(stream, expected_size, code):
    # A source being updated must not turn a bounded inspection or copy into
    # an EOF-following stream. The final probe is never yielded or written.
    remaining = expected_size
    while remaining:
        chunk = stream.read(min(remaining, READ_CHUNK_BYTES))
        if not chunk:
            raise FoundryError(code, "Artifact size changed during bounded reading.")
        remaining -= len(chunk)
        yield chunk
    if stream.read(1):
        raise FoundryError(code, "Artifact size changed during bounded reading.")


def _hash_artifact(path, expected_size, code="artifact_changed"):
    checksum = hashlib.sha256()
    with plain_path(path).open("rb") as stream:
        for chunk in _bounded_chunks(stream, expected_size, code):
            checksum.update(chunk)
    return checksum.hexdigest()


def _copy_artifact(source, target, expected_size):
    for chunk in _bounded_chunks(source, expected_size, "copy_changed"):
        target.write(chunk)


def _script_size(stream, expected_size):
    stream.seek(0, os.SEEK_END)
    if stream.tell() != expected_size:
        raise FoundryError("artifact_changed", "Script archive size changed during inspection.")


def _script_read_at(stream, offset, length):
    """Read a previously bounded metadata extent, never follow a growing EOF."""
    stream.seek(offset)
    data, position = bytearray(length), 0
    while position < length:
        chunk = stream.read(min(length - position, READ_CHUNK_BYTES))
        if not chunk:
            raise FoundryError("artifact_changed", "Script archive was truncated during inspection.")
        data[position:position + len(chunk)] = chunk
        position += len(chunk)
    return bytes(data)


class _ScriptArchiveView:
    """Fixed extent with frozen, preflighted metadata for the stdlib parser.

    Local headers/payload stay on the same open source handle. Neither growing
    EOF nor replacement metadata can enlarge a later ZipFile allocation. This
    is not an atomic snapshot or protection against a hostile filesystem.
    """
    def __init__(self, stream, size, metadata_offset, metadata):
        self.stream, self.size = stream, size
        self.metadata_offset, self.metadata = metadata_offset, metadata
        self.position = 0

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=os.SEEK_SET):
        if whence == os.SEEK_CUR:
            offset += self.position
        elif whence == os.SEEK_END:
            offset += self.size
        elif whence != os.SEEK_SET:
            raise ValueError("Invalid archive seek mode")
        if not 0 <= offset <= self.size:
            raise zipfile.BadZipFile("Archive seek exceeds the inspected extent")
        self.position = offset
        return offset

    def read(self, size=-1):
        remaining = self.size - self.position
        size = remaining if size is None or size < 0 else min(size, remaining)
        # Even an unexpected stdlib read-to-EOF is bounded before allocation.
        maximum = max(READ_CHUNK_BYTES,
                      MAX_SCRIPT_METADATA_BYTES + _ZIP_END_BYTES + _ZIP_COMMENT_BYTES)
        if size > maximum:
            raise FoundryError("archive_bounds", "Script archive read exceeds its metadata/read bound.")
        prefix = b""
        if self.position < self.metadata_offset:
            count = min(size, self.metadata_offset - self.position)
            prefix = _script_read_at(self.stream, self.position, count)
            self.position += count
            size -= count
        start = self.position - self.metadata_offset
        suffix = self.metadata[start:start + size] if size else b""
        self.position += size
        return prefix + suffix


def _script_archive_view(stream, size):
    """Bound/count classic ZIP metadata before constructing any ZipInfo.

    Deliberately no ZIP64 central/end metadata, split disks, concatenation-offset
    inference or non-file central records. See docs/SCRIPT_ARCHIVE.md for limits.
    """
    _script_size(stream, size)
    tail_offset = max(0, size - _ZIP_END_BYTES - _ZIP_COMMENT_BYTES)
    tail = _script_read_at(stream, tail_offset, size - tail_offset)
    end = tail.rfind(b"PK\x05\x06")
    if end < 0 or len(tail) - end < _ZIP_END_BYTES:
        raise zipfile.BadZipFile("Missing or truncated archive end record")
    (_, disk, central_disk, disk_count, count, central_size, central_offset,
     comment_size) = struct.unpack_from("<4s4H2IH", tail, end)
    end_offset = tail_offset + end
    if end + _ZIP_END_BYTES + comment_size != len(tail):
        raise zipfile.BadZipFile("Archive comment extent does not end at EOF")
    if (disk or central_disk or disk_count != count
            or count == 0xffff or central_size == 0xffffffff or central_offset == 0xffffffff):
        raise zipfile.BadZipFile("Split or ZIP64 archive metadata is unsupported")
    if not 1 <= count <= MAX_SCRIPT_ENTRIES or central_size > MAX_SCRIPT_METADATA_BYTES:
        raise FoundryError("archive_bounds", "Script archive exceeds entry or metadata bounds.",
                           maximum_entries=MAX_SCRIPT_ENTRIES,
                           maximum_metadata_bytes=MAX_SCRIPT_METADATA_BYTES)
    if central_size < _ZIP_CENTRAL_BYTES or central_offset + central_size != end_offset:
        raise zipfile.BadZipFile("Unsupported or inconsistent central-directory extent")
    metadata = _script_read_at(stream, central_offset, size - central_offset)
    # The second read must retain exactly the end record/comment we validated.
    if metadata[central_size:] != tail[end:]:
        raise FoundryError("artifact_changed", "Script archive metadata changed during inspection.")
    # CPython probes this location even without ZIP64 sentinels. A coincidental
    # signature here is refused too, rather than allowing another interpretation.
    if metadata[central_size - 20:central_size - 16] == b"PK\x06\x07":
        raise zipfile.BadZipFile("ZIP64 end metadata is unsupported")
    position, actual_count, first_offset = 0, 0, central_offset
    while position < central_size:
        actual_count += 1
        if actual_count > MAX_SCRIPT_ENTRIES:
            raise FoundryError("archive_bounds", "Script archive exceeds its actual entry bound.",
                               maximum_entries=MAX_SCRIPT_ENTRIES)
        if central_size - position < _ZIP_CENTRAL_BYTES:
            raise zipfile.BadZipFile("Truncated central-directory header")
        (signature, _, _, _, method, _, _, _, compressed, expanded,
         name_size, extra_size, member_comment_size, member_disk, _, _,
         local_offset) = struct.unpack_from("<4s6H3I5H2I", metadata, position)
        if signature != b"PK\x01\x02":
            raise zipfile.BadZipFile("Unsupported central-directory record")
        next_position = position + _ZIP_CENTRAL_BYTES + name_size + extra_size + member_comment_size
        if next_position > central_size:
            raise zipfile.BadZipFile("Central-directory member exceeds metadata extent")
        if (member_disk or compressed == 0xffffffff or expanded == 0xffffffff
                or local_offset == 0xffffffff):
            raise zipfile.BadZipFile("Split or ZIP64 member metadata is unsupported")
        if local_offset + 30 > central_offset:
            raise zipfile.BadZipFile("Local header is outside the archive data extent")
        first_offset = min(first_offset, local_offset)
        if method not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise FoundryError("archive_compression", "Only stored and Deflate script members are supported.",
                               method=method)
        extra = position + _ZIP_CENTRAL_BYTES + name_size
        extra_end = extra + extra_size
        while extra < extra_end:
            if extra_end - extra < 4:
                raise zipfile.BadZipFile("Truncated central-directory extra field")
            tag, length = struct.unpack_from("<HH", metadata, extra)
            extra += 4 + length
            if extra > extra_end or tag == 1:
                raise zipfile.BadZipFile("Malformed or ZIP64 central-directory extra field")
        position = next_position
    if actual_count != count or first_offset != 0:
        raise zipfile.BadZipFile("Inconsistent entry count or unsupported archive prefix")
    _script_size(stream, size)
    return _ScriptArchiveView(stream, size, central_offset, metadata)


def inspect_artifact(path):
    # Keep the public artifact contract unchanged. Optional report collection
    # belongs to the same validated archive lifetime, before final drift checks.
    return _inspect_artifact(path)[0]


def _inspect_artifact(path, script_inspector=None):
    details = None
    path = plain_path(path)
    if not safe_relative(path.name) or not path.is_file() or path.suffix.lower() not in (".package", ".ts4script"):
        raise FoundryError("unsupported_artifact", "Only regular .package and .ts4script files are accepted.")
    size = path.stat().st_size
    if not 4 <= size <= MAX_ARTIFACT:
        raise FoundryError("artifact_size", "Artifact is empty, truncated, or larger than 512 MiB.")
    before = _hash_artifact(path, size)
    if path.suffix.lower() == ".package":
        with path.open("rb") as stream:
            if stream.read(4) != b"DBPF":
                raise FoundryError("invalid_package", "Package does not have a DBPF signature.")
        inspection = {"kind": "dbpf", "validation": "signature_only", "resources_validated": False}
    else:
        try:
            with path.open("rb") as stream, zipfile.ZipFile(_script_archive_view(stream, size)) as archive:
                rows = archive.infolist()
                if not 1 <= len(rows) <= MAX_SCRIPT_ENTRIES or sum(row.file_size for row in rows) > 256 * 1024 * 1024:
                    raise FoundryError("archive_bounds", "Script archive exceeds entry or expanded-size bounds.")
                names, files, directories = set(), set(), set()
                for row in rows:
                    # ZipInfo normalizes backslashes on Windows and truncates NUL.
                    # Inspect the original archived spelling, not its sanitized alias.
                    name = row.orig_filename
                    member_path = name[:-1] if row.is_dir() else name
                    if name != row.filename or not safe_relative(member_path):
                        raise FoundryError("archive_path", "Script archive contains an unsafe member path.")
                    if name.casefold() in names:
                        raise FoundryError("archive_duplicate", "Script archive contains duplicate/case-colliding paths.")
                    key = member_path.casefold()
                    parents = {str(parent).casefold() for parent in PurePosixPath(member_path).parents
                               if str(parent) != "."}
                    if key in files or parents & files or (not row.is_dir() and key in directories):
                        raise FoundryError("archive_path_conflict", "A script member path is both a file and a directory.")
                    directories.update(parents)
                    if row.is_dir():
                        directories.add(key)
                    else:
                        files.add(key)
                    names.add(name.casefold())
                    if stat.S_ISLNK(row.external_attr >> 16) or row.flag_bits & 1:
                        raise FoundryError("archive_member", "Linked or encrypted members are not accepted.")
                    if row.file_size > 32 * 1024 * 1024 or row.file_size > max(1, row.compress_size) * 1000:
                        raise FoundryError("archive_bounds", "Script member exceeds size/compression bounds.")
                bad = archive.testzip()
                if bad:
                    raise FoundryError("archive_crc", "Script archive failed CRC validation.", member=bad)
                if script_inspector is not None:
                    details = script_inspector(archive, rows)
                _script_size(stream, size)
                inspection = {"kind": "ts4script_zip", "validation": "bounded_members_and_crc", "member_count": len(rows), "code_executed": False}
        except _SCRIPT_ZIP_ERRORS as exc:
            raise FoundryError("invalid_script_archive", "Unsupported or invalid script ZIP.") from exc
    if _hash_artifact(path, size) != before or path.stat().st_size != size:
        raise FoundryError("artifact_changed", "Source changed during inspection; no artifact was accepted.")
    return {"name": path.name, "source": str(path), "bytes": size, "sha256": before, **inspection}, details


def _inventory_files(directory):
    """Bound enumeration before allocation, descent, or artifact inspection.

    One over-limit entry may be obtained to detect exhaustion, but is neither
    inspected nor descended into. Each directory handle closes before another
    is opened. Observed links/reparse points refuse, not a hostile-FS guarantee.
    """
    pending, paths, visited = [(directory, 0)], [], 0
    while pending:
        current, depth = pending.pop()
        with os.scandir(plain_path(current)) as entries:
            for entry in entries:
                visited += 1
                if visited > MAX_SCAN_ENTRIES:
                    raise FoundryError("inventory_scan_bound", "Inventory exceeds its total directory-entry scan bound.",
                                       maximum=MAX_SCAN_ENTRIES)
                # DirEntry's non-following stat observes Windows junctions and
                # other reparse entries before queuing any descendant scan.
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise FoundryError("linked_path", "Symlinks and Windows reparse points are not accepted.", path=entry.path)
                path = plain_path(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    if depth >= MAX_SCAN_DEPTH:
                        raise FoundryError("inventory_depth_bound", "Inventory exceeds its nested-directory depth bound.",
                                           maximum=MAX_SCAN_DEPTH)
                    pending.append((path, depth + 1))
                elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in (".package", ".ts4script"):
                    if len(paths) >= MAX_INVENTORY_ARTIFACTS:
                        raise FoundryError("inventory_bound", "Inventory exceeds 4096 artifacts.")
                    paths.append(path)
    return paths


def inventory(directory):
    directory = plain_path(directory)
    if not directory.is_dir():
        raise FoundryError("missing_directory", "Artifact directory is missing.")
    rows = []
    seen = set()
    for path in _inventory_files(directory):
        relative = path.relative_to(directory).as_posix()
        if not _mod_relative(relative):
            raise FoundryError("artifact_path", "Artifact path is not portable to Windows.")
        row = inspect_artifact(path)
        row["relative"] = relative
        key = row["relative"].casefold()
        if key in seen:
            raise FoundryError("duplicate_artifact", "Case-colliding artifact paths are not portable.")
        seen.add(key)
        rows.append(row)
    rows.sort(key=lambda row: row["relative"])
    result = {"schema": 1, "artifacts": rows, "kind": "foundry_mod_inventory", "game_modified": False}
    result["manifest_sha256"] = digest(result)
    return _manifest(result)


def _manifest(value):
    exact_fields(value, {"schema", "kind", "artifacts", "game_modified", "manifest_sha256"})
    if type(value["schema"]) is not int or value["schema"] != 1 or value["kind"] != "foundry_mod_inventory" or value["game_modified"] is not False:
        raise FoundryError("invalid_manifest", "Expected a Foundry mod inventory.")
    unsigned = {key: item for key, item in value.items() if key != "manifest_sha256"}
    if digest(unsigned) != value["manifest_sha256"]:
        raise FoundryError("manifest_digest", "Inventory digest does not match.")
    if not isinstance(value["artifacts"], list) or len(value["artifacts"]) > MAX_INVENTORY_ARTIFACTS:
        raise FoundryError("invalid_manifest", "Artifact list exceeds its bound.")
    seen, parent_paths = set(), set()
    for row in value["artifacts"]:
        if not isinstance(row, dict):
            raise FoundryError("invalid_manifest", "Artifact row is not an object.")
        relative = row.get("relative")
        if (not _mod_relative(relative) or PurePosixPath(relative).name != row.get("name")
                or PurePosixPath(relative).suffix.lower() not in (".package", ".ts4script")
                or not isinstance(row.get("source"), str) or not row["source"] or "\x00" in row["source"]):
            raise FoundryError("invalid_manifest", "Artifact has an unsafe relative path.")
        # Validation must match the format at the destination. A digest binds
        # manifest contents, but does not make a renamed extension trustworthy.
        if Path(row["source"]).suffix.lower() != PurePosixPath(relative).suffix.lower():
            raise FoundryError("invalid_manifest", "Source and destination artifact extensions must match.")
        key = relative.casefold()
        parents = {str(parent).casefold() for parent in PurePosixPath(relative).parents if str(parent) != "."}
        if key in seen:
            raise FoundryError("duplicate_artifact", "Manifest contains a duplicate path.")
        if key in parent_paths or parents & seen:
            raise FoundryError("artifact_path_conflict", "An artifact path also names a directory; paths must be portable to Windows.")
        seen.add(key)
        parent_paths.update(parents)
        checksum = row.get("sha256")
        if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum) or not exact_int(row.get("bytes"), 4, MAX_ARTIFACT):
            raise FoundryError("invalid_manifest", "Artifact has an invalid size/hash.")
    return value


def compare(before, after):
    _manifest(before)
    _manifest(after)
    old = {row["relative"].casefold(): row for row in before["artifacts"]}
    new = {row["relative"].casefold(): row for row in after["artifacts"]}
    return {"ok": True, "added": [new[key]["relative"] for key in sorted(new.keys() - old.keys())],
            "removed": [old[key]["relative"] for key in sorted(old.keys() - new.keys())],
            "changed": [new[key]["relative"] for key in sorted(old.keys() & new.keys()) if old[key]["sha256"] != new[key]["sha256"]],
            "unchanged": sum(old[key]["sha256"] == new[key]["sha256"] for key in old.keys() & new.keys()),
            "game_modified": False, "compatibility_verified": False}


def stage(manifest, destination, execute=False):
    _manifest(manifest)
    # Length checks are lexical and precede filesystem calls, which may already
    # fail on an oversized component even when the output does not exist.
    destination = Path(os.path.abspath(destination))
    _check_staging_paths(destination, manifest["artifacts"])
    destination = external_path(destination)
    if destination.exists():
        raise FoundryError("destination_exists", "Staging requires a never-existing external directory.")
    # Complete all validation before creating the output tree.
    for row in manifest["artifacts"]:
        source = plain_path(row["source"])
        if destination == source or destination in source.parents:
            raise FoundryError("overlapping_source", "Staging destination cannot contain its sources.")
        actual = inspect_artifact(source)
        if actual["sha256"] != row["sha256"] or actual["bytes"] != row["bytes"]:
            raise FoundryError("source_changed", "Source no longer matches the inventory.", relative=row["relative"])
    plan = {"ok": True, "executed": False, "destination": str(destination), "manifest_sha256": manifest["manifest_sha256"],
            "artifact_count": len(manifest["artifacts"]), "game_modified": False, "installed": False}
    if not execute:
        return plan
    # Flush new directory entries bottom-up before declaring complete. Include
    # the existing ancestor that owns the first newly created directory.
    directories = {destination}
    parent = destination.parent
    while not parent.exists():
        directories.add(parent)
        parent = parent.parent
    directories.add(parent)
    destination.mkdir(parents=True, exist_ok=False)
    new_json(destination / "staging-intent.json", plan)
    for row in manifest["artifacts"]:
        target = plain_path(destination / "artifacts" / row["relative"])
        parent = target.parent
        while parent != destination:
            directories.add(parent)
            parent = parent.parent
        target.parent.mkdir(parents=True, exist_ok=True)
        with plain_path(row["source"]).open("rb") as src, target.open("xb") as dst:
            _copy_artifact(src, dst, row["bytes"])
            dst.flush()
            os.fsync(dst.fileno())
        if target.stat().st_size != row["bytes"] or _hash_artifact(target, row["bytes"], "copy_changed") != row["sha256"]:
            raise FoundryError("copy_changed", "Copied artifact did not match. Staging is incomplete, not installable.", destination=str(destination))
    for directory in sorted(directories, key=lambda path: (-len(path.parts), str(path))):
        sync_directory(plain_path(directory))
    result = dict(plan, executed=True, staged=True)
    new_json(destination / "staging-complete.json", result)
    return result
