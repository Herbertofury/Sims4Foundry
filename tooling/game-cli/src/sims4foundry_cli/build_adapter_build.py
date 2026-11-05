"""Build a separate, uninstalled Foundry Build adapter archive."""
import argparse
import hashlib
import json
import re
import stat
import tempfile

from . import companion_build as base
from .errors import FoundryError
from .io import MAX_JSON, canonical, new_json, plain_path

MODULES = tuple("foundry_build_adapter/" + name + ".py" for name in (
    "__init__", "build_native_readers", "build_transform", "build_create",
    "build_destroy", "build_variant"))
_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")


def _payloads(source):
    if not source.is_dir():
        raise FoundryError("build_source_layout", "Select the Build adapter source directory.")
    root = [(name, info) for name, info in base._entries(source)
            if not (name == "__pycache__" and stat.S_ISDIR(info.st_mode))]
    if len(root) != 1 or root[0][0] != "foundry_build_adapter" or not stat.S_ISDIR(root[0][1].st_mode):
        raise FoundryError("build_source_layout", "Source root must contain only the adapter package.")
    package = plain_path(source / "foundry_build_adapter")
    expected = {name.rsplit("/", 1)[1] for name in MODULES}
    observed = set()
    for name, info in base._entries(package):
        if name == "__pycache__" and stat.S_ISDIR(info.st_mode):
            continue
        if name not in expected or not stat.S_ISREG(info.st_mode):
            raise FoundryError("build_source_layout", "Unexpected Build adapter source.", name=name)
        observed.add(name)
    if observed != expected:
        raise FoundryError("build_source_layout", "Build adapter source set is incomplete.")
    payloads, records, total = [], [], 0
    for name in MODULES:
        raw, digest, _ = base._regular(source / name, min(base.MAX_SOURCE_BYTES,
                                                         base.MAX_TOTAL_SOURCE_BYTES - total))
        total += len(raw)
        if total > base.MAX_TOTAL_SOURCE_BYTES:
            raise FoundryError("build_source_bounds", "Build adapter source exceeds the byte bound.")
        payloads.append((name, raw))
        records.append({"module":name, "bytes":len(raw), "sha256":digest})
    return payloads, records


def build_adapter(source_root, output, python37, compiler_sha256, manifest_output=None):
    """Compile and publish an explicit source set; never install or run it."""
    if type(compiler_sha256) is not str or not _HASH.fullmatch(compiler_sha256):
        raise FoundryError("build_compiler_hash", "An explicit 64-digit SHA-256 compiler pin is required.")
    source, compiler = plain_path(source_root), plain_path(python37)
    output = base._destination(output, source, ".ts4script")
    manifest_path = base._destination(manifest_output if manifest_output is not None else
                                      output.with_name(output.name + ".manifest.json"), source, ".json")
    if output in manifest_path.parents or manifest_path in output.parents:
        raise FoundryError("build_output_path", "Archive and manifest outputs cannot contain each other.")
    payloads, source_records = _payloads(source)
    _, observed, _ = base._regular(compiler, base.MAX_COMPILER_BYTES, retain=False)
    if observed != compiler_sha256.lower():
        raise FoundryError("build_compiler_hash", "Compiler does not match the explicit SHA-256 pin.")
    for parent in (output.parent, manifest_path.parent):
        parent.mkdir(parents=True, exist_ok=True)
        plain_path(parent)
    with tempfile.TemporaryDirectory(prefix=".foundry-build-adapter-", dir=output.parent) as scratch:
        compiled, compiled_records, compiler_info = base._compile(payloads, compiler,
                                                                    compiler_sha256.lower(), scratch)
    raw = base._archive(payloads + compiled)
    manifest = {"ok":True, "schema":1, "kind":"foundry_build_adapter",
                "artifact":output.name, "bytes":len(raw), "sha256":hashlib.sha256(raw).hexdigest(),
                "status":"development-not-runtime-qualified", "compiler":compiler_info,
                "modules":source_records, "compiled_modules":compiled_records,
                "member_count":len(payloads)+len(compiled), "archive_format":"zip-stored-fixed-1980-timestamps",
                "requires":"foundry_companion", "installed":False, "profile_modified":False,
                "game_contacted":False, "adapter_code_executed":False, "live_qualified":False}
    if len(canonical(manifest)) + 1 > MAX_JSON:
        raise FoundryError("oversized_json", "Build adapter manifest exceeds its byte bound.")
    base._destination(output, source, ".ts4script")
    base._destination(manifest_path, source, ".json")
    try:
        base._publish_archive(output, raw)
    except FileExistsError as exc:
        raise FoundryError("build_output_exists", "Output already exists; nothing was overwritten.") from exc
    except (OSError, FoundryError) as exc:
        raise FoundryError("build_publication_incomplete",
                           "Archive publication may have occurred; completion is unresolved.") from exc
    try:
        new_json(manifest_path, manifest)
    except (OSError, FoundryError) as exc:
        raise FoundryError("build_publication_incomplete",
                           "Archive was published without a completion manifest.") from exc
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--python37", required=True)
    parser.add_argument("--compiler-sha256", required=True)
    parser.add_argument("--manifest-output")
    args = parser.parse_args(argv)
    try:
        result = build_adapter(args.source, args.output, args.python37,
                               args.compiler_sha256, args.manifest_output)
    except FoundryError as error:
        result = error.as_dict()
    except (OSError, ValueError, TypeError) as error:
        result = {"ok":False, "code":"local_failure", "error_type":type(error).__name__}
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
