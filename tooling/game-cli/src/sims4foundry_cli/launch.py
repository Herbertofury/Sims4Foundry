"""Read-only EA launch metadata preparation, independent of Apex journals.

Derived from the reviewed Apex launch algorithm, not an execution adapter.
No guessed edition IDs, cache writes, process launches, registry changes or UAC.
"""
import hashlib
import ntpath
import re
from xml.etree import ElementTree
from urllib.parse import urlencode
from .errors import FoundryError
from .io import plain_path, sha256


def _same_image(a, b):
    return ntpath.normcase(ntpath.normpath(a)) == ntpath.normcase(ntpath.normpath(b))


def prepare(game_root, ea_logs, expected_executable_sha256):
    root = plain_path(game_root)
    executable = plain_path(root / "Game" / "Bin" / "TS4_x64.exe")
    launcher = plain_path(root / "Game" / "Bin" / "TS4_Launcher_x64.exe")
    manifest = plain_path(root / "__Installer" / "installerdata.xml")
    if not all(path.is_file() for path in (executable, launcher, manifest)):
        raise FoundryError("incomplete_installation", "Explicit installation needs the DX11 executable, launcher and EA metadata.")
    checksum = sha256(executable)
    if checksum != expected_executable_sha256:
        raise FoundryError("wrong_executable", "Game executable does not match the explicitly expected SHA-256.")
    launcher_sha = sha256(launcher)
    manifest_sha = sha256(manifest)
    if not 0 < manifest.stat().st_size <= 1024 * 1024:
        raise FoundryError("manifest_bound", "EA manifest exceeds its read bound.")
    try:
        text = manifest.read_bytes().decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("document type/entity declarations are unsupported")
        metadata = ElementTree.fromstring(text)
    except (UnicodeError, ValueError, ElementTree.ParseError) as exc:
        raise FoundryError("invalid_installation_manifest", "Expected bounded UTF-8 EA metadata without entities.") from exc
    ids = [node.text for node in metadata.findall("./contentIDs/contentID")]
    if (not 1 <= len(ids) <= 64 or len(set(ids)) != len(ids)
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}", value) for value in ids)):
        raise FoundryError("invalid_content_ids", "Installation has no unambiguous numeric content-ID catalog.")
    if not isinstance(ea_logs, (list, tuple)) or not 1 <= len(ea_logs) <= 2:
        raise FoundryError("launch_evidence_required", "Select one current EA log, or predecessor first and current last.")
    lines = []
    source_hashes = []
    for path in ea_logs:
        path = plain_path(path)
        with path.open("rb") as stream:
            size = path.stat().st_size
            stream.seek(max(0, size - 2 * 1024 * 1024))
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise FoundryError("log_changed", "EA log grew across the bounded read; reread before planning.")
        # Keep the user's account/log contents private; return only evidence hashes.
        source_hashes.append(hashlib.sha256(raw).hexdigest())
        lines.extend(raw.decode("utf-8", "replace").splitlines())
    selected = None
    for index in range(len(lines)-1, -1, -1):
        line = lines[index]
        if "Processing launch request:" not in line or "requestSource[Client]" not in line:
            continue
        match = re.search(r"offerId\[([A-Za-z0-9:_-]{1,200})\] contentId\[([0-9]{1,10})\] exe\[([^\]]{1,32768})\]", line)
        if not match or not _same_image(match[3], str(launcher)):
            continue
        success = any("Successful launch." in prior and "offerKey.offerId=[" + match[1] + "]" in prior
                      and "slug=[the-sims-4]" in prior for prior in lines[max(0,index-30):index])
        if success:
            selected = {"offer_id": match[1], "content_id": match[2],
                        "record_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest()}
            break
    if selected is None:
        raise FoundryError("missing_successful_play", "No matching successful EA Client Play record was found. No edition ID was guessed.")
    if selected["content_id"] not in ids:
        raise FoundryError("edition_mismatch", "The successful account launch ID is absent from this installation manifest.")
    if sha256(executable) != checksum or sha256(launcher) != launcher_sha or sha256(manifest) != manifest_sha:
        raise FoundryError("installation_changed", "Installation changed during metadata preparation.")
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
