"""External single-attempt ledger for a trusted Build operation exchange.

No game, process, network, admission or startup implementation is supplied.
The callback is trusted, and an attempt means possible dispatch, not success.
"""
import math
import os
import re
import stat

from .errors import FoundryError
from .io import canonical, digest, external_path, loads, lock, new_json, plain_path

MAX_RECORD = 16384
MAX_REQUESTS = 128
TOKEN = re.compile(r"[0-9a-f]{32}\Z")
OPERATIONS = frozenset(("create", "transform", "variant", "destroy"))


def _require(value, code, message):
    if not value:
        raise FoundryError(code, message)


def _token(value):
    return type(value) is str and TOKEN.fullmatch(value) is not None


def _bounded(value, depth=0):
    _require(depth <= 8, "journal_bound", "Value exceeds the journal depth bound.")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        _require(-(2**64) <= value <= 2**64-1, "journal_bound", "Integer is out of bounds.")
        return value
    if type(value) is float:
        _require(math.isfinite(value) and abs(value) <= 2**64-1, "journal_bound", "Number is out of bounds.")
        return value
    if type(value) is str:
        _require(len(value) <= 256, "journal_bound", "Text is too long.")
        return value
    if type(value) in (list, tuple):
        _require(len(value) <= 32, "journal_bound", "Array is too large.")
        return [_bounded(item, depth+1) for item in value]
    if type(value) is dict:
        _require(len(value) <= 32 and all(type(k) is str and 0 < len(k) <= 64 for k in value),
                 "journal_bound", "Object keys are invalid.")
        return {k:_bounded(v, depth+1) for k,v in value.items()}
    raise FoundryError("journal_bound", "Value contains an unsupported type.")


def _encoded(value):
    value = _bounded(value)
    raw = canonical(value)
    _require(len(raw) <= MAX_RECORD, "journal_bound", "Record is too large.")
    return value


def _intent(value, session):
    value = _encoded(value)
    _require(type(value) is dict and set(value) == {
        "schema","request_id","session_id","operation","context","arguments"},
        "invalid_intent", "Exact Build intent fields are required.")
    _require(type(value["schema"]) is int and value["schema"] == 1
             and _token(value["request_id"]) and value["session_id"] == session
             and type(value["operation"]) is str and value["operation"] in OPERATIONS
             and type(value["context"]) is dict and type(value["arguments"]) is dict,
             "invalid_intent", "Build intent identity or operation is invalid.")
    return value


def _read(path):
    path = plain_path(path)
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
             "journal_corrupt", "Journal record is not an ordinary file.")
    with path.open("rb") as stream:
        raw = stream.read(MAX_RECORD + 1)
    _require(0 < len(raw) <= MAX_RECORD, "journal_corrupt", "Journal record exceeds its bound.")
    return loads(raw)


class BuildJournal:
    @classmethod
    def create(cls, directory, session_id):
        _require(_token(session_id), "invalid_session", "An exact session ID is required.")
        root = external_path(directory)
        _require(not root.exists() and not root.is_symlink(), "journal_exists", "Choose a new journal directory.")
        root.mkdir(parents=True, exist_ok=False)
        (root / "requests").mkdir()
        new_json(root / "journal.json", {"schema":1, "session_id":session_id})
        return cls(root)

    def __init__(self, directory):
        self.root = external_path(directory)
        value = _read(self.root / "journal.json")
        _require(type(value) is dict and set(value) == {"schema","session_id"}
                 and type(value["schema"]) is int and value["schema"] == 1 and _token(value["session_id"]),
                 "journal_corrupt", "Journal identity is invalid.")
        self.session_id = value["session_id"]
        requests = plain_path(self.root / "requests")
        _require(requests.is_dir(), "journal_corrupt", "Journal requests directory is missing.")

    def _verify(self):
        value = _read(self.root / "journal.json")
        _require(type(value) is dict and set(value) == {"schema","session_id"}
                 and type(value["schema"]) is int and value["schema"] == 1
                 and value["session_id"] == self.session_id,
                 "journal_corrupt", "Journal identity changed.")
        _require(plain_path(self.root / "requests").is_dir(),
                 "journal_corrupt", "Journal requests directory is missing.")

    def _capacity(self):
        requests = set()
        total = 0
        pattern = re.compile(r"([0-9a-f]{32})\.(intent|attempt|result)\.json\Z")
        with os.scandir(self.root / "requests") as entries:
            for entry in entries:
                total += 1
                match = pattern.fullmatch(entry.name)
                _require(total <= MAX_REQUESTS * 3 and match is not None,
                         "journal_corrupt", "Journal directory is invalid or exceeds its bound.")
                requests.add(match.group(1))
        _require(len(requests) < MAX_REQUESTS, "journal_full", "Journal history is full.")
        # A different request ID must not bypass an interrupted or uncertain
        # exchange. Validate every existing record while holding the same lock.
        for request_id in sorted(requests):
            previous = self._status(request_id)
            _require(previous["state"] == "returned", "journal_pending",
                     "An earlier Build exchange is incomplete; inspect its original request.")

    def _paths(self, request_id):
        _require(_token(request_id), "invalid_request", "An exact request ID is required.")
        root = plain_path(self.root / "requests")
        return tuple(plain_path(root / (request_id + suffix))
                     for suffix in (".intent.json", ".attempt.json", ".result.json"))

    def _status(self, request_id):
        intent_path, attempt_path, result_path = self._paths(request_id)
        if not intent_path.exists():
            _require(not attempt_path.exists() and not result_path.exists(),
                     "journal_corrupt", "A request has records without an intent.")
            return {"ok":True, "state":"not_found", "request_id":request_id}
        intent = _intent(_read(intent_path), self.session_id)
        _require(intent["request_id"] == request_id, "journal_corrupt", "Intent identity changed.")
        fingerprint = digest(intent)
        if not attempt_path.exists():
            _require(not result_path.exists(), "journal_corrupt", "A result has no attempt.")
            return {"ok":True, "state":"prepared", "request_id":request_id, "intent_sha256":fingerprint}
        attempt = _read(attempt_path)
        _require(type(attempt) is dict and set(attempt) == {"schema","request_id","intent_sha256"}
                 and type(attempt["schema"]) is int and attempt["schema"] == 1
                 and attempt["request_id"] == request_id and attempt["intent_sha256"] == fingerprint,
                 "journal_corrupt", "Attempt does not match its intent.")
        if not result_path.exists():
            return {"ok":True, "state":"unresolved", "request_id":request_id, "intent_sha256":fingerprint}
        result = _read(result_path)
        _require(type(result) is dict and set(result) == {"schema","request_id","intent_sha256","state","value"}
                 and type(result["schema"]) is int and result["schema"] == 1
                 and result["request_id"] == request_id and result["intent_sha256"] == fingerprint
                 and result["state"] in ("returned","unresolved")
                 and ((result["state"] == "returned" and type(result["value"]) is dict)
                      or (result["state"] == "unresolved" and result["value"] is None)),
                 "journal_corrupt", "Result does not match its intent and attempt.")
        _encoded(result)
        return {"ok":True, "state":result["state"], "request_id":request_id,
                "intent_sha256":fingerprint, "value":result["value"]}

    def status(self, request_id):
        with lock(self.root / "operation.lock"):
            self._verify()
            return self._status(request_id)

    def execute_once(self, intent, callback):
        """Persist intent and attempt before one callback; never replay an ID."""
        captured = _intent(intent, self.session_id)
        _require(callable(callback), "invalid_callback", "A trusted exchange callback is required.")
        request_id, fingerprint = captured["request_id"], digest(captured)
        with lock(self.root / "operation.lock"):
            self._verify()
            _require(self._status(request_id)["state"] == "not_found", "request_exists",
                     "A request with this ID already exists; inspect its status.")
            self._capacity()
            intent_path, attempt_path, result_path = self._paths(request_id)
            new_json(intent_path, captured)
            new_json(attempt_path, {"schema":1, "request_id":request_id, "intent_sha256":fingerprint})
            # A freshly decoded detached view prevents callback mutation from
            # changing the retained original or redirecting another operation.
            view = loads(canonical(captured))
            try:
                value = _encoded(callback(view))
                _require(type(value) is dict and digest(view) == fingerprint,
                         "invalid_result", "Callback returned an invalid result or changed intent.")
                result = {"schema":1, "request_id":request_id, "intent_sha256":fingerprint,
                          "state":"returned", "value":value}
                _encoded(result)
            except Exception:
                result = {"schema":1, "request_id":request_id, "intent_sha256":fingerprint,
                          "state":"unresolved", "value":None}
            try:
                new_json(result_path, result)
            except Exception:
                return {"ok":False, "state":"unresolved", "request_id":request_id,
                        "intent_sha256":fingerprint}
            return {"ok":result["state"] == "returned", "state":result["state"],
                    "request_id":request_id, "intent_sha256":fingerprint, "value":result["value"]}
