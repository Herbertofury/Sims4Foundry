"""Real offline artifact inspection, comparison and never-in-place staging.

This module never installs into a live profile, downloads, imports, or executes
mods. A valid hash/archive is not a compatibility or malware-safety verdict.
"""
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile
from .errors import FoundryError
from .io import (digest, exact_fields, exact_int, external_path, new_json,
                 plain_path, sha256, safe_relative)

MAX_ARTIFACT = 512 * 1024 * 1024


def inspect_artifact(path):
    path = plain_path(path)
    if not safe_relative(path.name) or not path.is_file() or path.suffix.lower() not in (".package", ".ts4script"):
        raise FoundryError("unsupported_artifact", "Only regular .package and .ts4script files are accepted.")
    size = path.stat().st_size
    if not 4 <= size <= MAX_ARTIFACT:
        raise FoundryError("artifact_size", "Artifact is empty, truncated, or larger than 512 MiB.")
    before = sha256(path)
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
    if sha256(path) != before or path.stat().st_size != size:
        raise FoundryError("artifact_changed", "Source changed during inspection; no artifact was accepted.")
    return {"name": path.name, "source": str(path), "bytes": size, "sha256": before, **inspection}


def inventory(directory):
    directory = plain_path(directory)
    if not directory.is_dir():
        raise FoundryError("missing_directory", "Artifact directory is missing.")
    rows = []
    seen = set()
    for path in sorted(directory.rglob("*")):
        plain_path(path)
        if path.is_file() and path.suffix.lower() in (".package", ".ts4script"):
            if len(rows) >= 4096:
                raise FoundryError("inventory_bound", "Inventory exceeds 4096 artifacts.")
            row = inspect_artifact(path)
            row["relative"] = path.relative_to(directory).as_posix()
            if not safe_relative(row["relative"]):
                raise FoundryError("artifact_path", "Artifact path is not portable to Windows.")
            key = row["relative"].casefold()
            if key in seen:
                raise FoundryError("duplicate_artifact", "Case-colliding artifact paths are not portable.")
            seen.add(key)
            rows.append(row)
    result = {"schema": 1, "artifacts": rows, "kind": "foundry_mod_inventory", "game_modified": False}
    result["manifest_sha256"] = digest(result)
    return result


def _manifest(value):
    exact_fields(value, {"schema", "kind", "artifacts", "game_modified", "manifest_sha256"})
    if type(value["schema"]) is not int or value["schema"] != 1 or value["kind"] != "foundry_mod_inventory" or value["game_modified"] is not False:
        raise FoundryError("invalid_manifest", "Expected a Foundry mod inventory.")
    unsigned = {key: item for key, item in value.items() if key != "manifest_sha256"}
    if digest(unsigned) != value["manifest_sha256"]:
        raise FoundryError("manifest_digest", "Inventory digest does not match.")
    if not isinstance(value["artifacts"], list) or len(value["artifacts"]) > 4096:
        raise FoundryError("invalid_manifest", "Artifact list exceeds its bound.")
    seen = set()
    for row in value["artifacts"]:
        if not isinstance(row, dict):
            raise FoundryError("invalid_manifest", "Artifact row is not an object.")
        relative = row.get("relative")
        if (not safe_relative(relative) or PurePosixPath(relative).name != row.get("name")
                or PurePosixPath(relative).suffix.lower() not in (".package", ".ts4script")
                or not isinstance(row.get("source"), str) or not row["source"] or "\x00" in row["source"]):
            raise FoundryError("invalid_manifest", "Artifact has an unsafe relative path.")
        if relative.casefold() in seen:
            raise FoundryError("duplicate_artifact", "Manifest contains a duplicate path.")
        seen.add(relative.casefold())
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
            shutil.copyfileobj(src, dst)
            dst.flush()
            import os
            os.fsync(dst.fileno())
        if target.stat().st_size != row["bytes"] or sha256(target) != row["sha256"]:
            raise FoundryError("copy_changed", "Copied artifact did not match. Staging is incomplete, not installable.", destination=str(destination))
    result = dict(plan, executed=True, staged=True)
    new_json(destination / "staging-complete.json", result)
    return result
