"""Read-only EA launch metadata preparation, independent of Apex journals.

Derived from the reviewed Apex launch algorithm, not an execution adapter.
No guessed edition IDs, cache writes, process launches, registry changes or UAC.
"""
from collections import deque
import hashlib
import ntpath
import os
import re
import stat
from xml.etree import ElementTree
from urllib.parse import urlencode
from .errors import FoundryError
from .io import plain_path
from .path_identity import same_windows_path


MAX_BINARY_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_LOG_TAIL_BYTES = 2 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
_WINDOWS = os.name == "nt"


def _stamp(info):
    # Reads may change atime. Retain all other observed identity/change fields.
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mode": info.st_mode, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns,
            "birthtime_ns": getattr(info, "st_birthtime_ns", None),
            "attributes": getattr(info, "st_file_attributes", 0)}


def _same_open_source(path_stamp, descriptor_stamp, path=None):
    comparable_path = path_stamp
    # CPython Windows path stat adds 0111 for .exe/.bat/.cmd/.com names;
    # descriptor fstat has no filename and does not add those synthetic bits.
    # Reconcile only that exact regular-file difference, not mode/type drift.
    # Source: CPython v3.11.14 Modules/posixmodule.c:1924-1939 and
    # v3.13.17 Modules/posixmodule.c:1845-1869; Python/fileutils.c fstat.
    if (_WINDOWS and path is not None
            and ntpath.splitext(str(path))[1].casefold() in (".exe", ".bat", ".cmd", ".com")
            and stat.S_ISREG(path_stamp["mode"]) and stat.S_ISREG(descriptor_stamp["mode"])
            and not descriptor_stamp["mode"] & 0o111
            and path_stamp["mode"] == descriptor_stamp["mode"] | 0o111):
        comparable_path = dict(path_stamp, mode=descriptor_stamp["mode"])
    # Windows stat/fstat can expose different ctime meanings. Compare explicit
    # birth time across APIs, but retain each full stamp for same-API checks.
    if (_WINDOWS and path_stamp["birthtime_ns"] is not None
            and descriptor_stamp["birthtime_ns"] is not None):
        return ({k: v for k, v in comparable_path.items() if k != "ctime_ns"}
                == {k: v for k, v in descriptor_stamp.items() if k != "ctime_ns"})
    return comparable_path == descriptor_stamp


def _source_stat(path, code, maximum=None):
    info = plain_path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise FoundryError(code, "Launch metadata accepts only regular, unlinked source files.")
    if maximum is not None and not 0 < info.st_size <= maximum:
        raise FoundryError(code, "Installation source exceeds its bounded size.", maximum_bytes=maximum)
    return _stamp(info)


def _changed(code):
    raise FoundryError(code, "Launch metadata source changed during bounded reading; reread before planning.")


def _snapshot(path, expected, code, *, collect=False, tail=False):
    """Bound all I/O to the original extent plus one never-retained EOF probe.

    Logs may be arbitrarily large on disk, but only their final bounded extent
    and one preceding delimiter byte are read. These checks detect ordinary
    concurrent edits, not a hostile-filesystem snapshot guarantee.
    """
    try:
        if _source_stat(path, code) != expected:
            _changed(code)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            with os.fdopen(descriptor, "rb", buffering=0, closefd=False) as stream:
                descriptor_stamp = _stamp(os.fstat(stream.fileno()))
                if not _same_open_source(expected, descriptor_stamp, path):
                    _changed(code)
                start = max(0, expected["size"] - MAX_LOG_TAIL_BYTES) if tail else 0
                # Look behind the tail so a complete first line is not dropped.
                offset = start - 1 if start else 0
                stream.seek(offset)
                remaining = expected["size"] - offset
                checksum = hashlib.sha256()
                raw = bytearray() if collect else None
                while remaining:
                    chunk = stream.read(min(remaining, READ_CHUNK_BYTES))
                    if not chunk or len(chunk) > remaining:
                        _changed(code)
                    remaining -= len(chunk)
                    checksum.update(chunk)
                    if raw is not None:
                        raw.extend(chunk)
                if stream.read(1):
                    _changed(code)
                if (_stamp(os.fstat(stream.fileno())) != descriptor_stamp
                        or _source_stat(path, code) != expected):
                    _changed(code)
                return checksum.hexdigest(), None if raw is None else bytes(raw), bool(start)
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        _changed(code)


def _log_records(raw):
    # Iterate rather than split: millions of blank lines still need only the
    # fixed predecessor window, not millions of retained Python objects.
    for match in re.finditer(rb"[^\r\n]*(?:\r\n?|\n|\Z)", raw):
        if match.start() == match.end():
            continue
        line = match[0].rstrip(b"\r\n")
        try:
            text = line.decode("utf-8")
        except UnicodeError:
            # Invalid bytes never become selected evidence through replacement.
            # Keep the line's position in the bounded predecessor window.
            text = ""
        yield text, line


def _log_tail(raw, has_prefix):
    prefix, raw = (raw[:1], raw[1:]) if has_prefix else (b"", raw)
    tail_hash = hashlib.sha256(raw).hexdigest()
    if prefix and prefix != b"\n" and not (prefix == b"\r" and not raw.startswith(b"\n")):
        # The cut may split a UTF-8 code point. Discard only that incomplete line.
        end = re.search(rb"\r\n?|\n", raw)
        raw = raw[end.end():] if end else b""
    return _log_records(raw), tail_hash


def _same_image(a, b):
    # Preserve the launch-log matcher's original lexical normalization only.
    return same_windows_path(ntpath.normpath(a), ntpath.normpath(b))


def prepare(game_root, ea_logs, expected_executable_sha256):
    root = plain_path(game_root)
    executable = plain_path(root / "Game" / "Bin" / "TS4_x64.exe")
    launcher = plain_path(root / "Game" / "Bin" / "TS4_Launcher_x64.exe")
    manifest = plain_path(root / "__Installer" / "installerdata.xml")
    if not all(path.is_file() for path in (executable, launcher, manifest)):
        raise FoundryError("incomplete_installation", "Explicit installation needs the DX11 executable, launcher and EA metadata.")
    if not isinstance(ea_logs, (list, tuple)) or not 1 <= len(ea_logs) <= 2:
        raise FoundryError("launch_evidence_required", "Select one current EA log, or predecessor first and current last.")
    # Preflight every extent before any source bytes are hashed or retained.
    installation = [(path, _source_stat(path, code, maximum)) for path, code, maximum in (
        (executable, "executable_bound", MAX_BINARY_BYTES),
        (launcher, "launcher_bound", MAX_BINARY_BYTES),
        (manifest, "manifest_bound", MAX_MANIFEST_BYTES))]
    logs = [(plain_path(path), _source_stat(path, "invalid_launch_log")) for path in ea_logs]
    checksum, _, _ = _snapshot(executable, installation[0][1], "installation_changed")
    if checksum != expected_executable_sha256:
        raise FoundryError("wrong_executable", "Game executable does not match the explicitly expected SHA-256.")
    launcher_sha, _, _ = _snapshot(launcher, installation[1][1], "installation_changed")
    manifest_sha, manifest_raw, _ = _snapshot(manifest, installation[2][1], "installation_changed", collect=True)
    try:
        text = manifest_raw.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("document type/entity declarations are unsupported")
        metadata = ElementTree.fromstring(text)
    except (UnicodeError, ValueError, ElementTree.ParseError) as exc:
        raise FoundryError("invalid_installation_manifest", "Expected bounded UTF-8 EA metadata without entities.") from exc
    ids = [node.text for node in metadata.findall("./contentIDs/contentID")]
    if (not 1 <= len(ids) <= 64 or len(set(ids)) != len(ids)
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}", value) for value in ids)):
        raise FoundryError("invalid_content_ids", "Installation has no unambiguous numeric content-ID catalog.")
    prior_lines = deque(maxlen=30)
    source_hashes = []
    log_snapshots = []
    selected = None
    for path, expected in logs:
        snapshot_hash, raw, has_prefix = _snapshot(path, expected, "log_changed", collect=True, tail=True)
        records, tail_hash = _log_tail(raw, has_prefix)
        # Keep the user's account/log contents private; return only evidence hashes.
        source_hashes.append(tail_hash)
        log_snapshots.append(snapshot_hash)
        for line, raw_line in records:
            if "Processing launch request:" in line and "requestSource[Client]" in line:
                match = re.search(r"offerId\[([A-Za-z0-9:_-]{1,200})\] contentId\[([0-9]{1,10})\] exe\[([^\]]{1,32768})\]", line)
                if match and _same_image(match[3], str(launcher)):
                    success = next((raw_prior for prior, raw_prior in reversed(prior_lines)
                                    if "Successful launch." in prior and "offerKey.offerId=[" + match[1] + "]" in prior
                                    and "slug=[the-sims-4]" in prior), None)
                    if success is not None:
                        selected = {"offer_id": match[1], "content_id": match[2],
                                    "record_sha256": hashlib.sha256(raw_line).hexdigest(),
                                    "success_record_sha256": hashlib.sha256(success).hexdigest()}
            prior_lines.append((line, raw_line))
    if selected is None:
        raise FoundryError("missing_successful_play", "No matching successful EA Client Play record was found. No edition ID was guessed.")
    if selected["content_id"] not in ids:
        raise FoundryError("edition_mismatch", "The successful account launch ID is absent from this installation manifest.")
    for (path, expected), checksum_expected in zip(installation, (checksum, launcher_sha, manifest_sha)):
        if _snapshot(path, expected, "installation_changed")[0] != checksum_expected:
            _changed("installation_changed")
    for (path, expected), snapshot_hash in zip(logs, log_snapshots):
        if _snapshot(path, expected, "log_changed", tail=True)[0] != snapshot_hash:
            _changed("log_changed")
    version = metadata.find("./buildMetaData/gameVersion")
    return {"ok": True, "schema": 1, "kind": "foundry_ea_launch_metadata", "launched": False,
            "dispatch_available": False, "profile_changed": False, "cache_written": False,
            "game_root": str(root), "executable_sha256": checksum, "launcher_sha256": launcher_sha,
            "manifest_sha256": manifest_sha, "game_version": None if version is None else version.get("version"),
            "account_launch": selected, "log_tail_sha256": source_hashes,
            "url": "origin2://game/launch?" + urlencode({"offerIds": selected["content_id"], "autoDownload": "0"}),
            "scope": "Read-only metadata preparation. Not launch, readiness or profile authorization.",
            "remaining_gates": ["Separate marked disposable-profile admission", "No competing game or operator",
                                "Verified registered EA handler", "Normal OS consent, if any",
                                "Single handoff and exact new process-lifetime observation", "Observed menu/household readiness"]}
