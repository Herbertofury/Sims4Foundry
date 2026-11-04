"""Build a separate, credential-free loader diagnostic, never install it.

The generated root entry is a proposed loader candidate, not proof that The
Sims 4 imports it. It calls only the diagnostic package and contains no game
commands, companion activation, endpoint, key, or profile admission.
"""
import hashlib
import math
from pathlib import PureWindowsPath
import re
import stat
import tempfile

from . import companion_build as build
from .errors import FoundryError
from .io import canonical, new_json, plain_path, safe_relative

MODULES = ("foundry_diagnostic/__init__.py", "foundry_diagnostic/runtime.py")
ENTRY = "sims4foundry_startup_probe.py"
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")


def probe_config(run_id, output_path, wait_seconds):
    """Validate data for a later Windows run without probing that filesystem."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise FoundryError("probe_config", "Run ID must be exactly 32 lowercase hexadecimal characters.")
    if (type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 30
            or not math.isfinite(wait_seconds)):
        raise FoundryError("probe_config", "Diagnostic wait must be finite and between 0 and 30 seconds.")
    if not isinstance(output_path, str) or not 1 <= len(output_path) <= 4096:
        raise FoundryError("probe_config", "Choose an explicit bounded Windows evidence filename.")
    try:
        output_path.encode("utf-8")
    except UnicodeError as exc:
        raise FoundryError("probe_config", "Evidence path must contain valid Unicode text.") from exc
    path = PureWindowsPath(output_path)
    # No UNC/device/drive-relative paths, ADS, normalization of traversal or
    # machine-dependent path interpretation. This does not inspect the target.
    if (not re.fullmatch(r"[A-Za-z]:", path.drive) or path.root != "\\"
            or "/" in output_path or "\\\\" in output_path or len(path.parts) < 2
            or not safe_relative(output_path[3:].replace("\\", "/"))
            or path.suffix.lower() != ".json" or len(path.name.encode("utf-8")) > 180):
        raise FoundryError("probe_config", "Choose a drive-qualified new external .json path without special or traversal components.")
    protected = {"game", "mods", "saves", "tray"}
    if any(part.casefold() in protected or part.casefold().startswith("the sims 4")
           for part in path.parts):
        raise FoundryError("probe_config", "Diagnostic evidence must be outside game and profile directories.")
    return {"run_id": run_id, "output_path": output_path, "wait_seconds": float(wait_seconds)}


def entry_source(config):
    # repr of canonical ASCII JSON is a Python string literal, not source
    # concatenation of user data. Never accept an arbitrary entrypoint or code.
    literal = repr(canonical(config).decode("ascii"))
    return ("\"\"\"Foundry startup diagnostic candidate; no game controls.\"\"\"\n"
            "try:\n"
            "    import json as _json\n"
            "    from foundry_diagnostic.runtime import start as _start\n"
            "    _probe_run = _start(_json.loads(" + literal + "),\n"
            "        module_origin=globals().get('__file__'),\n"
            "        loader_type=type(globals().get('__loader__')).__name__)\n"
            "except Exception:\n"
            "    # A diagnostic failure must not abort the game's loader.\n"
            "    # Missing evidence is unknown, never successful activation.\n"
            "    _probe_run = None\n").encode("ascii")


def _payloads(source, config):
    if not source.is_dir():
        raise FoundryError("probe_source_layout", "Select the explicit diagnostic-src directory.")
    roots = build._entries(source)
    if (len(roots) != 1 or roots[0][0] != "foundry_diagnostic"
            or not stat.S_ISDIR(roots[0][1].st_mode)):
        raise FoundryError("probe_source_layout", "Source root must contain only foundry_diagnostic.")
    expected = {"__init__.py", "runtime.py"}
    observed = set()
    for name, info in build._entries(source / "foundry_diagnostic"):
        if name == "__pycache__" and stat.S_ISDIR(info.st_mode):
            continue
        if name not in expected or not stat.S_ISREG(info.st_mode):
            raise FoundryError("probe_source_layout", "Only the fixed two diagnostic modules are accepted.")
        observed.add(name)
    if observed != expected:
        raise FoundryError("probe_source_layout", "Diagnostic source is incomplete.")
    payloads = [(name, build._regular(source / name, build.MAX_SOURCE_BYTES)[0]) for name in MODULES]
    payloads.append((ENTRY, entry_source(config)))
    return payloads


def build_startup_probe(source_root, output, python37, compiler_sha256, *,
                        run_id, probe_output, wait_seconds=30.0, manifest_output=None):
    """Compile trusted diagnostic source and one fixed data-only root entry.

    Nothing is installed, no target filesystem is inspected, and no generated
    source is executed. The output path is configuration for a separately
    authorized diagnostic run, not proof of an external or writable location.
    """
    config = probe_config(run_id, probe_output, wait_seconds)
    if not isinstance(compiler_sha256, str) or not build._HEX64.fullmatch(compiler_sha256):
        raise FoundryError("build_compiler_hash", "An explicit 64-digit SHA-256 compiler pin is required.")
    source, compiler = plain_path(source_root), plain_path(python37)
    output = build._destination(output, source, ".ts4script")
    manifest_path = build._destination(manifest_output if manifest_output is not None else
                                       output.with_name(output.name + ".manifest.json"), source, ".json")
    if output in manifest_path.parents or manifest_path in output.parents:
        raise FoundryError("build_output_path", "Archive and manifest outputs cannot contain each other.")
    payloads = _payloads(source, config)
    _, observed_hash, _ = build._regular(compiler, build.MAX_COMPILER_BYTES, retain=False)
    if observed_hash != compiler_sha256.lower():
        raise FoundryError("build_compiler_hash", "Compiler does not match the explicit SHA-256 pin.", observed_sha256=observed_hash)
    for parent in (output.parent, manifest_path.parent):
        parent.mkdir(parents=True, exist_ok=True)
        plain_path(parent)
    with tempfile.TemporaryDirectory(prefix=".foundry-probe-compile-", dir=output.parent) as scratch:
        compiled, compiled_records, compiler_info = build._compile(payloads, compiler, compiler_sha256.lower(), scratch)
    raw = build._archive(payloads + compiled)
    manifest = {
        "ok": True, "schema": 1, "kind": "foundry_startup_probe_build",
        "artifact": output.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "status": "diagnostic-loader-candidate-not-game-qualified",
        "archive_scope": "credential-free-startup-diagnostic", "entry_candidate": ENTRY,
        "compiler": compiler_info, "member_count": len(payloads) + len(compiled),
        "modules": [{"module": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    for name, data in payloads], "compiled_modules": compiled_records,
        "probe_config": config, "installed": False, "profile_modified": False,
        "game_contacted": False, "diagnostic_code_executed": False,
        "loader_qualified": False, "live_qualified": False, "credentials_included": False,
        "limitations": [
            "The game may not import the root candidate; compilation is not loader qualification.",
            "Configured output placement and access must be verified separately before an authorized run.",
            "Diagnostic evidence is self-reported and cannot authorize profile access or commands.",
            "A matching startup/tick Thread does not prove startup occurred outside a tick.",
            "No game controls, companion activation, key provisioning, or installation are included.",
            "The executable hash does not authenticate compiler libraries or vendor provenance."]}
    build._destination(output, source, ".ts4script")
    build._destination(manifest_path, source, ".json")
    try:
        build._publish_archive(output, raw)
    except FileExistsError as exc:
        raise FoundryError("build_output_exists", "Another writer created the archive; nothing was overwritten.") from exc
    except (OSError, FoundryError) as exc:
        raise FoundryError("build_publication_incomplete", "Probe archive publication failed and may exist; no completion manifest was written.",
                           artifact=str(output), manifest=str(manifest_path)) from exc
    try:
        new_json(manifest_path, manifest)
    except (OSError, FoundryError) as exc:
        raise FoundryError("build_publication_incomplete", "Probe archive exists but its completion manifest failed; build is incomplete.",
                           artifact=str(output), manifest=str(manifest_path)) from exc
    return manifest
