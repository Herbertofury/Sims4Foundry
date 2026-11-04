"""Build only the neutral companion into a deterministic, uninstalled archive.

The compiler is an explicitly selected, SHA-256-pinned trusted CPython runtime.
Only this module's compiler driver executes; companion source is compiled, never
imported or executed. A build is not installation or live-game qualification.
"""
import base64
import binascii
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile

from .errors import FoundryError
from .io import MAX_JSON, canonical, exact_fields, external_path, loads, new_json, plain_path, sync_directory
from .launch import _same_open_source, _stamp


MODULES = tuple("foundry_companion/" + name + ".py" for name in (
    "__init__", "build_create", "build_destroy", "build_executor", "build_native_readers",
    "build_observation", "build_request", "build_schema", "build_transform", "build_variant",
    "build_wire", "live_executor", "live_observation",
    "live_schema", "live_wire", "native", "owner_runtime", "peer_auth",
    "queue", "transport", "wire"))
MAGIC = bytes.fromhex("420d0d0a")
PYC_PREFIX = MAGIC + bytes.fromhex("03000000")
MAX_DIRECTORY_ENTRIES = 64
MAX_SOURCE_BYTES = 256 * 1024
MAX_TOTAL_SOURCE_BYTES = 1024 * 1024
MAX_COMPILER_BYTES = 64 * 1024 * 1024
MAX_COMPILED_BYTES = 512 * 1024
MAX_TOTAL_COMPILED_BYTES = 2 * 1024 * 1024
MAX_STDOUT_BYTES = MAX_JSON
MAX_STDERR_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
COMPILE_TIMEOUT_SECONDS = 60
READ_CHUNK = 64 * 1024
_HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")

# Original driver for neutral source only. Python 3.7 syntax is intentional.
# There is no eval, exec, import of inputs, or game/profile activity here.
COMPILER_DRIVER = r'''
import base64, hashlib, importlib.util, importlib._bootstrap_external
import json, os, sys
if (sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 7)
        or importlib.util.MAGIC_NUMBER != b'\x42\x0d\x0d\x0a'
        or sys.flags.ignore_environment or os.environ.get('PYTHONHASHSEED') != '0'):
    raise RuntimeError('Requires trusted CPython 3.7, magic 420d0d0a, and hash seed 0.')
if os.name == 'posix':
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
rows = json.load(sys.stdin)
result = []
for row in rows:
    raw = base64.b64decode(row['source'], validate=True)
    code = compile(raw, row['module'], 'exec', dont_inherit=True, optimize=0)
    source_hash = importlib.util.source_hash(raw)
    pyc = bytes(importlib._bootstrap_external._code_to_hash_pyc(code, source_hash, checked=True))
    result.append({'module': row['module'][:-3] + '.pyc',
                   'source_sha256': hashlib.sha256(raw).hexdigest(),
                   'source_hash': source_hash.hex(),
                   'payload': base64.b64encode(pyc).decode('ascii')})
print(json.dumps({'python': '.'.join(str(n) for n in sys.version_info[:3]),
                  'magic': importlib.util.MAGIC_NUMBER.hex(), 'modules': result},
                 sort_keys=True, separators=(',', ':')))
'''


def _identity(info):
    # Reuse the existing Windows cross-API birthtime/ctime and synthetic .exe
    # mode reconciliation, while retaining complete same-API change stamps.
    return dict(_stamp(info), links=info.st_nlink)


def _regular(path, bound, retain=True):
    """Bound both actual reads and retention; refuse links, specials and changes."""
    path = plain_path(path)
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise FoundryError("build_input_type", "Build inputs must be regular files without hard links.", path=str(path))
    if initial.st_size > bound:
        raise FoundryError("build_input_bounds", "Build input exceeds its byte bound.", path=str(path), limit=bound)
    digest = hashlib.sha256()
    retained = bytearray()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            with os.fdopen(fd, "rb", buffering=0, closefd=False) as stream:
                descriptor_stamp = _identity(os.fstat(stream.fileno()))
                if not _same_open_source(_identity(initial), descriptor_stamp, path):
                    raise FoundryError("build_input_changed", "Build input changed before reading.", path=str(path))
                remaining = initial.st_size
                while remaining:
                    chunk = stream.read(min(READ_CHUNK, remaining))
                    if not chunk:
                        raise FoundryError("build_input_changed", "Build input ended before its observed size.", path=str(path))
                    remaining -= len(chunk)
                    digest.update(chunk)
                    if retain:
                        retained.extend(chunk)
                if stream.read(1) or _identity(os.fstat(stream.fileno())) != descriptor_stamp:
                    raise FoundryError("build_input_changed", "Build input changed while reading.", path=str(path))
        finally:
            os.close(fd)
    except OSError as exc:
        raise FoundryError("build_input_read", "Build input could not be opened or read safely.", path=str(path)) from exc
    if _identity(plain_path(path).lstat()) != _identity(initial):
        raise FoundryError("build_input_changed", "Build input was replaced while reading.", path=str(path))
    return bytes(retained), digest.hexdigest(), initial


def _entries(directory):
    rows = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if len(rows) == MAX_DIRECTORY_ENTRIES:
                raise FoundryError("build_source_bounds", "Companion directory exceeds the entry bound.")
            path = plain_path(entry.path)
            rows.append((entry.name, path.lstat()))
    return rows


def _source_payloads(source):
    if not source.is_dir():
        raise FoundryError("build_source_layout", "Select the explicit companion-src directory.")
    roots = _entries(source)
    if len(roots) != 1 or roots[0][0] != "foundry_companion" or not stat.S_ISDIR(roots[0][1].st_mode):
        raise FoundryError("build_source_layout", "Source root must contain only the neutral foundry_companion package.")
    package = plain_path(source / "foundry_companion")
    expected = {Path(name).name for name in MODULES}
    observed = set()
    for name, info in _entries(package):
        # Never read or package old bytecode, including fixture-generated caches.
        if name == "__pycache__" and stat.S_ISDIR(info.st_mode):
            continue
        if name not in expected or not stat.S_ISREG(info.st_mode):
            raise FoundryError("build_source_layout", "Unexpected companion input; only the fixed neutral module allowlist is accepted.", name=name)
        observed.add(name)
    if observed != expected:
        raise FoundryError("build_source_layout", "Neutral companion source set is incomplete.", missing=sorted(expected - observed))
    payloads, records, total = [], [], 0
    for name in MODULES:
        raw, digest, _ = _regular(source / name, min(MAX_SOURCE_BYTES, MAX_TOTAL_SOURCE_BYTES - total))
        total += len(raw)
        if total > MAX_TOTAL_SOURCE_BYTES:
            raise FoundryError("build_source_bounds", "Companion source exceeds the total byte bound.")
        payloads.append((name, raw))
        records.append({"module": name, "bytes": len(raw), "sha256": digest})
    return payloads, records


def _compiler_environment(compiler):
    # Deliberately do not copy PYTHONPATH/HOME, loader hooks, shell variables,
    # credentials, proxy settings, user startup configuration or ambient PATH.
    result = {"PYTHONHASHSEED": "0", "PYTHONIOENCODING": "utf-8",
              "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C", "LANG": "C"}
    if os.name == "nt":
        for name in ("SystemRoot", "WINDIR", "SystemDrive"):
            if name in os.environ:
                result[name] = os.environ[name]
        result["PATH"] = str(compiler.parent)
    return result


def _run_compiler(compiler, request, scratch):
    """Drain bounded pipes, kill on overflow/timeout; no shell or source imports."""
    captured = [bytearray(), bytearray()]
    problems = []
    with tempfile.TemporaryFile(dir=scratch) as input_stream:
        input_stream.write(request)
        input_stream.seek(0)
        try:
            process = subprocess.Popen(
                [str(compiler), "-B", "-S", "-s", "-c", COMPILER_DRIVER],
                stdin=input_stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=scratch, env=_compiler_environment(compiler), shell=False,
                close_fds=True)
        except OSError as exc:
            raise FoundryError("build_compiler_start", "Could not start the explicitly pinned compiler.") from exc

        def drain(stream, target, limit):
            try:
                while True:
                    chunk = stream.read(min(READ_CHUNK, limit + 1 - len(target)))
                    if not chunk:
                        return
                    if len(target) + len(chunk) > limit:
                        problems.append("overflow")
                        process.kill()
                        return
                    target.extend(chunk)
            except (OSError, ValueError):
                problems.append("pipe")
            finally:
                stream.close()

        readers = []
        timed_out = False
        reaped = False
        started = []
        try:
            for stream, target, bound in ((process.stdout, captured[0], MAX_STDOUT_BYTES),
                                          (process.stderr, captured[1], MAX_STDERR_BYTES)):
                reader = threading.Thread(target=drain, args=(stream, target, bound), daemon=True)
                readers.append(reader)
                started.append(reader)
                reader.start()
            try:
                process.wait(timeout=COMPILE_TIMEOUT_SECONDS)
                reaped = True
            except subprocess.TimeoutExpired:
                timed_out = True
        except Exception as exc:
            raise FoundryError("build_compile_failed", "Compiler supervision could not start or finish safely.") from exc
        finally:
            # Own exactly this child, never another process or a process tree.
            # This runs for partial reader startup, interrupts and wait errors,
            # not only the ordinary success/timeout cases.
            unwinding = sys.exc_info()[0] is not None
            cleanup_errors = []
            if not reaped:
                try:
                    process.kill()
                except OSError:
                    pass  # It may already have exited; still reap below.
                except BaseException as exc:
                    cleanup_errors.append(exc)
                try:
                    process.wait(timeout=5)
                    reaped = True
                except BaseException as exc:
                    cleanup_errors.append(exc)
            try:
                for reader in started:
                    try:
                        reader.join(timeout=5)
                    except BaseException as exc:
                        cleanup_errors.append(exc)
            finally:
                # Close pipes even when join itself fails or only one reader
                # started. Started readers also close their stream on exit.
                for stream in (process.stdout, process.stderr):
                    try:
                        stream.close()
                    except BaseException as exc:
                        cleanup_errors.append(exc)
            if cleanup_errors and not unwinding:
                for exc in cleanup_errors:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise exc
                raise FoundryError("build_compile_cleanup", "Compiler supervision cleanup failed; no archive was published.") from cleanup_errors[0]
        if timed_out:
            raise FoundryError("build_compile_timeout", "Pinned compilation exceeded its wall-clock limit.")
        if problems or any(reader.is_alive() for reader in readers):
            raise FoundryError("build_compile_output_bounds", "Compiler output exceeded a bound or could not be read completely.")
        if process.returncode != 0:
            raise FoundryError("build_compile_failed", "Pinned Python 3.7 compiler refused the source or failed.",
                               returncode=process.returncode,
                               diagnostic=bytes(captured[1][:4096]).decode("utf-8", errors="replace"))
        if captured[1]:
            raise FoundryError("build_compile_failed", "Compiler produced unexpected diagnostics.",
                               diagnostic=bytes(captured[1][:4096]).decode("utf-8", errors="replace"))
        return bytes(captured[0])


def _compile(payloads, compiler, expected_hash, scratch):
    _, digest, info = _regular(compiler, MAX_COMPILER_BYTES, retain=False)
    if digest != expected_hash:
        raise FoundryError("build_compiler_hash", "Compiler executable does not match the explicit SHA-256 pin.", observed_sha256=digest)
    # Windows embeddable _pth isolation ignores PYTHONHASHSEED. Refuse instead
    # of producing possibly nondeterministic frozenset/marshal ordering.
    for name in {compiler.with_suffix("._pth").name, "python37._pth", "python._pth"}:
        candidate = plain_path(compiler.parent / name)
        if candidate.exists():
            raise FoundryError("build_compiler_isolation", "A compiler _pth file prevents the required deterministic hash-seed policy.")
    if _identity(plain_path(compiler).lstat()) != _identity(info):
        raise FoundryError("build_input_changed", "Compiler executable changed before launch.")
    request = canonical([{"module": name, "source": base64.b64encode(raw).decode("ascii")}
                         for name, raw in payloads])
    if len(request) > MAX_JSON:
        raise FoundryError("build_source_bounds", "Compiler request exceeds its JSON bound.")
    raw_result = _run_compiler(compiler, request, scratch)
    if _identity(plain_path(compiler).lstat()) != _identity(info):
        raise FoundryError("build_input_changed", "Compiler executable changed during compilation.")
    if len(raw_result) > MAX_STDOUT_BYTES:
        raise FoundryError("build_compile_output_bounds", "Compiler response exceeds its byte bound.")
    try:
        result = loads(raw_result)
        exact_fields(result, ("python", "magic", "modules"))
        if (not isinstance(result["python"], str) or not re.fullmatch(r"3\.7\.\d{1,2}", result["python"])
                or result["magic"] != MAGIC.hex() or not isinstance(result["modules"], list)
                or len(result["modules"]) != len(payloads)):
            raise ValueError("Wrong compiler identity or module count")
        compiled, records, total = [], [], 0
        for (source_name, source), row in zip(payloads, result["modules"]):
            exact_fields(row, ("module", "source_sha256", "source_hash", "payload"))
            if (row["module"] != source_name[:-3] + ".pyc"
                    or row["source_sha256"] != hashlib.sha256(source).hexdigest()
                    or not isinstance(row["source_hash"], str)
                    or not re.fullmatch(r"[0-9a-f]{16}", row["source_hash"])
                    or not isinstance(row["payload"], str)
                    or len(row["payload"]) > ((MAX_COMPILED_BYTES + 2) // 3) * 4):
                raise ValueError("Unexpected, duplicate or oversized compiled module")
            pyc = base64.b64decode(row["payload"], validate=True)
            total += len(pyc)
            if (not 16 < len(pyc) <= MAX_COMPILED_BYTES or total > MAX_TOTAL_COMPILED_BYTES
                    or pyc[:8] != PYC_PREFIX or pyc[8:16].hex() != row["source_hash"]):
                raise ValueError("Invalid compiled header or compiled byte bound")
            compiled.append((row["module"], pyc))
            records.append({"module": row["module"], "bytes": len(pyc),
                            "sha256": hashlib.sha256(pyc).hexdigest(),
                            "source_hash": row["source_hash"]})
    except (FoundryError, ValueError, TypeError, KeyError, binascii.Error) as exc:
        raise FoundryError("build_compiler_response", "Compiler returned an invalid bounded module response.") from exc
    return compiled, records, {"implementation": "cpython", "version": result["python"],
                               "magic": MAGIC.hex(), "python_executable_sha256": digest,
                               "driver_sha256": hashlib.sha256(COMPILER_DRIVER.encode("utf-8")).hexdigest(),
                               "invalidation": "PEP 552 checked source hash", "hash_seed": 0,
                               "optimization": 0, "source_executed": False}


def _archive(payloads):
    # Stored ZIP avoids cross-zlib variation. Both source and adjacent .pyc are
    # included; no __pycache__, absolute filenames or machine timestamps.
    expected_size = 22 + sum(76 + 2 * len(name.encode("utf-8")) + len(raw) for name, raw in payloads)
    if expected_size > MAX_ARCHIVE_BYTES:
        raise FoundryError("build_archive_bounds", "Archive exceeds its byte bound before allocation.")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, raw in sorted(payloads):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, raw)
    raw_archive = stream.getvalue()
    if len(raw_archive) != expected_size:
        raise FoundryError("build_archive_bounds", "Archive byte count differed from the bounded layout.")
    with zipfile.ZipFile(io.BytesIO(raw_archive)) as archive:
        if archive.testzip() is not None or archive.namelist() != sorted(name for name, _ in payloads):
            raise FoundryError("build_archive_verification", "Archive CRC or member verification failed.")
        if any(archive.read(name) != raw for name, raw in payloads):
            raise FoundryError("build_archive_verification", "Archive member bytes did not match the compiled inputs.")
    return raw_archive


def _destination(path, source, suffix):
    path = external_path(path)
    if any(part.casefold() in {"mods", "saves", "tray"} for part in path.parts):
        raise FoundryError("protected_path", "Build outputs cannot be in a profile-content directory.")
    for parent in path.parents:
        # Also catch a renamed or otherwise nonstandard profile. This is a
        # conservative marker check, not discovery of every possible profile.
        if ((parent / "Options.ini").exists() or (parent / "GameVersion.txt").exists()
                or ((parent / "Mods").is_dir() and (parent / "saves").is_dir())):
            raise FoundryError("protected_path", "Build outputs cannot be inside an observed Sims profile.")
    if len(str(path)) > 4096 or len(path.name.encode("utf-8")) > 180 or path.suffix.lower() != suffix:
        raise FoundryError("build_output_path", "Choose a bounded external output path with the required extension.", extension=suffix)
    if path == source or source in path.parents:
        raise FoundryError("build_output_path", "Build outputs must be outside source inputs.")
    if path.exists():
        raise FoundryError("build_output_exists", "Build outputs must be new paths; nothing is overwritten.", path=str(path))
    return path


def _publish_archive(path, raw):
    temporary = None
    try:
        plain_path(path.parent)
        with tempfile.NamedTemporaryFile(prefix=".foundry-build-", suffix=".pending", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        plain_path(path)
        os.link(temporary, path, follow_symlinks=False)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink()


def build_companion(source_root, output, python37, compiler_sha256, manifest_output=None):
    """Create a new .ts4script and separate completion manifest, never install.

    The caller must select a trusted CPython 3.7 runtime and independently pin
    its executable hash. Existing outputs are always refused. The source tree
    must contain the complete fixed MODULES allowlist under foundry_companion.
    """
    if not isinstance(compiler_sha256, str) or not _HEX64.fullmatch(compiler_sha256):
        raise FoundryError("build_compiler_hash", "An explicit 64-digit SHA-256 compiler pin is required.")
    source = plain_path(source_root)
    compiler = plain_path(python37)
    output = _destination(output, source, ".ts4script")
    manifest_path = _destination(manifest_output if manifest_output is not None else
                                 output.with_name(output.name + ".manifest.json"), source, ".json")
    if output in manifest_path.parents or manifest_path in output.parents:
        raise FoundryError("build_output_path", "Archive and manifest outputs cannot contain each other.")
    payloads, source_records = _source_payloads(source)
    # Verify the pin before creating output directories or starting any process.
    _, observed_hash, _ = _regular(compiler, MAX_COMPILER_BYTES, retain=False)
    if observed_hash != compiler_sha256.lower():
        raise FoundryError("build_compiler_hash", "Compiler executable does not match the explicit SHA-256 pin.", observed_sha256=observed_hash)
    for parent in (output.parent, manifest_path.parent):
        parent.mkdir(parents=True, exist_ok=True)
        plain_path(parent)
    with tempfile.TemporaryDirectory(prefix=".foundry-compile-", dir=output.parent) as scratch:
        compiled, compiled_records, compiler_info = _compile(payloads, compiler, compiler_sha256.lower(), scratch)
    raw_archive = _archive(payloads + compiled)
    manifest = {"ok": True, "schema": 1, "kind": "foundry_companion_build",
                "artifact": output.name, "bytes": len(raw_archive),
                "sha256": hashlib.sha256(raw_archive).hexdigest(),
                "status": "development-not-runtime-qualified", "python_source_syntax": "3.7",
                "compiler": compiler_info, "modules": source_records,
                "compiled_modules": compiled_records, "member_count": len(payloads) + len(compiled),
                "archive_format": "zip-stored-fixed-1980-timestamps",
                "archive_scope": "neutral-foundry-companion-allowlist",
                "installed": False, "profile_modified": False, "game_contacted": False,
                "companion_code_executed": False, "live_qualified": False,
                "limitations": ["No startup activation, profile admission or installation is provided.",
                                "Compilation and ZIP checks do not prove game loading or gameplay compatibility.",
                                "The executable hash does not authenticate the compiler's libraries or vendor provenance."]}
    if len(canonical(manifest)) + 1 > MAX_JSON:
        raise FoundryError("oversized_json", "Build manifest exceeds its JSON bound.")
    # Recheck both names immediately before publication. The archive appears
    # first; only the separate manifest marks this two-file build complete.
    _destination(output, source, ".ts4script")
    _destination(manifest_path, source, ".json")
    try:
        _publish_archive(output, raw_archive)
    except FileExistsError as exc:
        raise FoundryError("build_output_exists", "Another writer created the output; nothing was overwritten.", path=str(output)) from exc
    except (OSError, FoundryError) as exc:
        raise FoundryError("build_publication_incomplete", "Archive publication failed and the destination may exist; no completion manifest was written.",
                           artifact=str(output), manifest=str(manifest_path)) from exc
    try:
        new_json(manifest_path, manifest)
    except (OSError, FoundryError) as exc:
        raise FoundryError("build_publication_incomplete", "Archive was published but completion-manifest publication failed; do not treat this build as complete.",
                           artifact=str(output), manifest=str(manifest_path)) from exc
    return manifest
