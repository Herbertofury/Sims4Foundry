"""Real offline artifact inspection, comparison and never-in-place staging.

This module never installs into a live profile, downloads, imports, or executes
mods. A valid hash/archive is not a compatibility or malware-safety verdict.
"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile
from .errors import FoundryError
from .io import (digest, exact_fields, exact_int, external_path, new_json,
                 plain_path, safe_relative)

MAX_ARTIFACT = 512 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
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


def inspect_artifact(path):
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
            with zipfile.ZipFile(path) as archive:
                rows = archive.infolist()
                if not 1 <= len(rows) <= 4096 or sum(row.file_size for row in rows) > 256 * 1024 * 1024:
                    raise FoundryError("archive_bounds", "Script archive exceeds entry or expanded-size bounds.")
                names = set()
                for row in rows:
                    # ZipInfo normalizes backslashes on Windows and truncates NUL.
                    # Inspect the original archived spelling, not its sanitized alias.
                    name = row.orig_filename
                    if name != row.filename or not safe_relative(name.rstrip("/")):
                        raise FoundryError("archive_path", "Script archive contains an unsafe member path.")
                    if name.casefold() in names:
                        raise FoundryError("archive_duplicate", "Script archive contains duplicate/case-colliding paths.")
                    names.add(name.casefold())
                    if stat.S_ISLNK(row.external_attr >> 16) or row.flag_bits & 1:
                        raise FoundryError("archive_member", "Linked or encrypted members are not accepted.")
                    if row.file_size > 32 * 1024 * 1024 or row.file_size > max(1, row.compress_size) * 1000:
                        raise FoundryError("archive_bounds", "Script member exceeds size/compression bounds.")
                bad = archive.testzip()
                if bad:
                    raise FoundryError("archive_crc", "Script archive failed CRC validation.", member=bad)
                inspection = {"kind": "ts4script_zip", "validation": "bounded_members_and_crc", "member_count": len(rows), "code_executed": False}
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise FoundryError("invalid_script_archive", "Unsupported or invalid script ZIP.") from exc
    if _hash_artifact(path, size) != before or path.stat().st_size != size:
        raise FoundryError("artifact_changed", "Source changed during inspection; no artifact was accepted.")
    return {"name": path.name, "source": str(path), "bytes": size, "sha256": before, **inspection}


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
    destination.mkdir(parents=True, exist_ok=False)
    new_json(destination / "staging-intent.json", plan)
    for row in manifest["artifacts"]:
        target = plain_path(destination / "artifacts" / row["relative"])
        target.parent.mkdir(parents=True, exist_ok=True)
        with plain_path(row["source"]).open("rb") as src, target.open("xb") as dst:
            _copy_artifact(src, dst, row["bytes"])
            dst.flush()
            os.fsync(dst.fileno())
        if target.stat().st_size != row["bytes"] or _hash_artifact(target, row["bytes"], "copy_changed") != row["sha256"]:
            raise FoundryError("copy_changed", "Copied artifact did not match. Staging is incomplete, not installable.", destination=str(destination))
    result = dict(plan, executed=True, staged=True)
    new_json(destination / "staging-complete.json", result)
    return result
