"""Bounded JSON, no linked paths, and durable local evidence primitives."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import uuid
from .errors import FoundryError

MAX_JSON = 4 * 1024 * 1024


def plain_path(path):
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        if not part.exists() and not part.is_symlink():
            continue
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise FoundryError("linked_path", "Symlinks and Windows reparse points are not accepted.", path=str(part))
    return path


def external_path(path):
    path = plain_path(path)
    # No workflow storage/staging inside a likely Sims profile or game install.
    protected = {"the sims 4", "the sims 4 original", "the sims 4 backup", "game"}
    if any(p.casefold() in protected or p.casefold().startswith("the sims 4") for p in path.parts):
        raise FoundryError("protected_path", "Use an external Foundry workspace, not a game/profile directory.")
    return path


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise FoundryError("invalid_json", "Duplicate JSON keys are not accepted.")
        value[key] = item
    return value


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite number")
    return number


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or value.startswith("/"):
        return False
    reserved = {"con", "prn", "aux", "nul", "conin$", "conout$"}
    reserved.update("com" + str(n) for n in range(1, 10))
    reserved.update("lpt" + str(n) for n in range(1, 10))
    reserved.update(prefix + digit for prefix in ("com", "lpt") for digit in "¹²³")
    for part in value.split("/"):
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or any(ord(c) < 32 or c in '<>"|?*' for c in part)
                # Windows also treats "NUL .json" as a device name. Spaces
                # before the first extension do not make a reserved stem safe.
                or part.split(".", 1)[0].rstrip(" ").casefold() in reserved):
            return False
    return True


def loads(raw):
    if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_JSON:
        raise FoundryError("oversized_json", "JSON exceeds the 4 MiB bound.")
    try:
        return json.loads(raw, object_pairs_hook=_unique_pairs, parse_float=_finite_float,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite number")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise FoundryError("invalid_json", "Expected bounded strict JSON.") from exc


def read_json(path):
    path = plain_path(path)
    with path.open("rb") as stream:
        return loads(stream.read(MAX_JSON + 1))


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256(path):
    with plain_path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def new_json(path, value):
    path = plain_path(path)
    raw = canonical(value) + b"\n"
    if len(raw) > MAX_JSON:
        raise FoundryError("oversized_json", "Output JSON exceeds the same 4 MiB read bound.")
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".pending")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Same-directory hard link publishes the complete inode atomically and
        # refuses an existing destination. No replace/overwrite fallback.
        os.link(temporary, path, follow_symlinks=False)
        sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()



def replace_json(path, value):
    path = plain_path(path)
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        new_json(temp, value)
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def lock(path):
    path = plain_path(path)
    # Stable inode plus an OS-held lock. Never unlink the lock file: another
    # process could already hold/wait on that inode. OS process exit releases
    # the lock, allowing read-only crash reconciliation without lock stealing.
    stream = path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise FoundryError("busy", "Another process owns the workspace operation lock.", lock=str(path)) from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def exact_fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise FoundryError("invalid_schema", "Object has missing or unrecognized fields.", required=sorted(required), optional=sorted(optional))


def exact_int(value, low=0, high=2**31-1):
    return type(value) is int and low <= value <= high


def finite_number(value, low, high):
    if type(value) is int:
        return low <= value <= high
    return type(value) is float and math.isfinite(value) and low <= value <= high
