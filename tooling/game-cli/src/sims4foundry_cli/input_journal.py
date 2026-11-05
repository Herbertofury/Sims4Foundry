"""Durable host-side journal for one-shot guarded input sequences.

The backend and semantic observer are trusted injected providers. This module
opens no target process, sends no input by itself, and supplies no live provider.
"""
from copy import deepcopy
import math
import os
import re
import stat

from .errors import FoundryError
from .guarded_input import _validate_sequence, run_packets
from .input_packets import InputPacket
from .io import canonical, digest, external_path, lock, loads, new_json, plain_path
from .windows import IDENTITY_KEYS, validate_identity

MAX_RUNS = 128
MAX_EVENTS = 512
MAX_RECORD = 32768
TOKEN = re.compile(r"[0-9a-f]{32}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
EVENT = re.compile(r"event-([0-9]{4})\.json\Z")
# Diagnostics are reports from the runner, not proof of a pre-dispatch failure.
_PUBLIC_RUNNER_CODES = frozenset((
    "unknown_foreground", "foreground_changed", "integrity_mismatch", "ownership_changed",
    "recording_unavailable", "recording_unverified", "input_context_unverified",
    "stale_observation", "invalid_sequence", "input_journal_bound", "post_unresolved",
    "release_unresolved", "recording_start_failed", "input_preparation_failed",
))


def _require(value, code, message):
    if not value:
        raise FoundryError(code, message)


def _token(value):
    return type(value) is str and TOKEN.fullmatch(value) is not None


def _read(path):
    path = plain_path(path)
    try:
        info = path.lstat()
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                 "input_journal_corrupt", "Input record must be an ordinary single-link file.")
        with path.open("rb") as stream:
            data = stream.read(MAX_RECORD + 1)
    except OSError as error:
        raise FoundryError("input_journal_corrupt", "Input record is unavailable.") from error
    _require(0 < len(data) <= MAX_RECORD, "input_journal_corrupt", "Input record is missing or too large.")
    return loads(data)


def _write(path, value):
    raw = canonical(value)
    _require(len(raw) <= MAX_RECORD, "input_journal_bound", "Input record exceeds its bound.")
    new_json(path, loads(raw))


def _target(value):
    validate_identity(value)
    return {"schema":1, "kind":"foundry_windows_identity",
            **{key:deepcopy(value[key]) for key in IDENTITY_KEYS}}


def _packets(packets, target):
    _require(type(packets) is tuple and 1 <= len(packets) <= 129,
             "invalid_sequence", "An exact bounded packet tuple is required.")
    copied = []
    for packet in packets:
        _require(type(packet) is InputPacket, "invalid_sequence", "An exact input packet is required.")
        try:
            copied.append(InputPacket(packet.message, packet.wparam, packet.lparam, packet.offset_ms,
                                      packet.coordinate_space, packet.release_intent))
        except (ValueError, TypeError) as error:
            raise FoundryError("invalid_sequence", "Packet validation failed.") from error
    copied = tuple(copied)
    _validate_sequence(copied, target)
    wire = [{"message":int(p.message), "wparam":p.wparam, "lparam":p.lparam,
             "offset_ms":p.offset_ms, "coordinate_space":p.coordinate_space.value,
             "release":p.release_intent} for p in copied]
    return copied, wire


def _expected(value, depth=0):
    _require(depth <= 5, "invalid_readback", "Expected UI observation is too deeply nested.")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        _require(-(2**64) <= value <= 2**64-1, "invalid_readback", "Observation integer is out of bounds.")
        return value
    if type(value) is float:
        _require(math.isfinite(value) and abs(value) <= 2**64-1,
                 "invalid_readback", "Observation number is invalid.")
        return value
    if type(value) is str:
        _require(len(value) <= 256, "invalid_readback", "Observation text is too long.")
        return value
    if type(value) is list:
        _require(len(value) <= 32, "invalid_readback", "Observation array is too large.")
        return [_expected(item, depth+1) for item in value]
    if type(value) is dict:
        _require(len(value) <= 32 and all(type(key) is str and 0 < len(key) <= 64 for key in value),
                 "invalid_readback", "Observation fields are invalid.")
        return {key:_expected(item, depth+1) for key,item in value.items()}
    raise FoundryError("invalid_readback", "Observation contains an unsupported type.")


def _expectation(value):
    _require(type(value) is dict and bool(value), "invalid_readback", "A nonempty expected UI observation is required.")
    result = _expected(value)
    _require(len(canonical(result)) <= 4096, "invalid_readback", "Expected UI observation is too large.")
    return result


class InputJournal:
    @classmethod
    def create(cls, directory, session_id, target):
        _require(_token(session_id), "invalid_session", "An exact session ID is required.")
        target = _target(target)
        root = external_path(directory)
        _require(not root.exists() and not root.is_symlink(), "input_journal_exists",
                 "Choose a new input journal directory.")
        root.mkdir(parents=True, exist_ok=False)
        (root / "runs").mkdir()
        _write(root / "session.json", {"schema":1, "session_id":session_id, "target":target})
        return cls(root)

    def __init__(self, directory):
        self.root = external_path(directory)
        self.session = _read(self.root / "session.json")
        _require(type(self.session) is dict and set(self.session) == {"schema","session_id","target"}
                 and type(self.session["schema"]) is int and self.session["schema"] == 1
                 and _token(self.session["session_id"])
                 and _target(self.session["target"]) == self.session["target"],
                 "input_journal_corrupt", "Input session is invalid.")
        _require(plain_path(self.root / "runs").is_dir(), "input_journal_corrupt", "Runs directory is missing.")
        self.session = loads(canonical(self.session))

    def _verify(self):
        _require(canonical(_read(self.root / "session.json")) == canonical(self.session),
                 "input_journal_corrupt", "Input session identity changed.")
        _require(plain_path(self.root / "runs").is_dir(), "input_journal_corrupt", "Runs directory is missing.")

    def _runs(self):
        names = []
        with os.scandir(self.root / "runs") as entries:
            for entry in entries:
                _require(len(names) < MAX_RUNS and _token(entry.name) and entry.is_dir(follow_symlinks=False)
                         and not entry.is_symlink(), "input_journal_corrupt", "Runs directory is invalid or full.")
                names.append(entry.name)
        return sorted(names)

    def _events(self, run):
        names = []
        with os.scandir(run / "events") as entries:
            for entry in entries:
                _require(len(names) < MAX_EVENTS and EVENT.fullmatch(entry.name)
                         and entry.is_file(follow_symlinks=False) and not entry.is_symlink(),
                         "input_journal_corrupt", "Input event directory is invalid or full.")
                names.append(entry.name)
        names.sort()
        previous = "0"*64
        for index, name in enumerate(names):
            value = _read(run / "events" / name)
            _require(name == "event-%04d.json" % index and type(value) is dict
                     and set(value) == {"schema","index","previous_sha256","event"}
                     and type(value["schema"]) is int and value["schema"] == 1
                     and type(value["index"]) is int and value["index"] == index
                     and value["previous_sha256"] == previous and type(value["event"]) is dict,
                     "input_journal_corrupt", "Input event sequence is inconsistent.")
            previous = digest(value)
        return len(names), previous

    def _status(self, request_id):
        _require(_token(request_id), "invalid_request", "An exact request ID is required.")
        run = plain_path(self.root / "runs" / request_id)
        if not run.exists():
            return {"ok":True, "state":"not_found", "request_id":request_id}
        _require(run.is_dir(), "input_journal_corrupt", "Input run is invalid.")
        intent = _read(run / "intent.json")
        _require(type(intent) is dict and set(intent) == {"schema","request_id","session_id","target","packets","expectation"}
                 and type(intent["schema"]) is int and intent["schema"] == 1
                 and intent["request_id"] == request_id and intent["session_id"] == self.session["session_id"]
                 and canonical(intent["target"]) == canonical(self.session["target"])
                 and type(intent["packets"]) is list and _expectation(intent["expectation"]) == intent["expectation"],
                 "input_journal_corrupt", "Input intent is inconsistent.")
        fingerprint = digest(intent)
        attempt_path = run / "attempt.json"
        if not attempt_path.exists():
            count, _ = self._events(run)
            _require(count == 0 and not (run / "result.json").exists() and not (run / "ack.json").exists(),
                     "input_journal_corrupt", "Input run has records without an attempt.")
            return {"ok":True, "state":"prepared", "request_id":request_id, "intent_sha256":fingerprint}
        attempt = _read(attempt_path)
        _require(type(attempt) is dict and set(attempt) == {"schema","intent_sha256"}
                 and type(attempt["schema"]) is int and attempt["schema"] == 1
                 and attempt["intent_sha256"] == fingerprint,
                 "input_journal_corrupt", "Input attempt is inconsistent.")
        count, previous = self._events(run)
        result_path = run / "result.json"
        if not result_path.exists():
            _require(not (run / "ack.json").exists(),
                     "input_journal_corrupt", "Input acknowledgement has no result.")
            return {"ok":True, "state":"unresolved", "request_id":request_id, "intent_sha256":fingerprint}
        result = _read(result_path)
        _require(type(result) is dict and set(result) == {"schema","intent_sha256","event_count","last_event_sha256","state","result"}
                 and type(result["schema"]) is int and result["schema"] == 1
                 and result["intent_sha256"] == fingerprint
                 and type(result["event_count"]) is int and result["event_count"] == count
                 and result["last_event_sha256"] == previous
                 and result["state"] in ("rejected","awaiting_readback","unresolved")
                 and (result["result"] is None or type(result["result"]) is dict),
                 "input_journal_corrupt", "Input result is inconsistent.")
        ack = run / "ack.json"
        state = result["state"]
        if ack.exists():
            value = _read(ack)
            _require(state == "awaiting_readback" and type(value) is dict
                     and set(value) == {"schema","request_id","session_id","intent_sha256","evidence_sha256","observation_sha256"}
                     and type(value["schema"]) is int and value["schema"] == 1
                     and value["request_id"] == request_id and value["session_id"] == self.session["session_id"]
                     and value["intent_sha256"] == fingerprint
                     and type(value["evidence_sha256"]) is str and HASH.fullmatch(value["evidence_sha256"])
                     and value["observation_sha256"] == digest(intent["expectation"]),
                     "input_journal_corrupt", "Input readback acknowledgement is inconsistent.")
            state = "acknowledged_by_host"
        return {"ok":True, "state":state, "request_id":request_id,
                "intent_sha256":fingerprint, "result":result["result"]}

    def status(self, request_id):
        with lock(self.root / "operation.lock"):
            self._verify()
            return self._status(request_id)

    def execute_once(self, request_id, packets, backend, *, expected_observation):
        _require(_token(request_id), "invalid_request", "An exact request ID is required.")
        target = _target(self.session["target"])
        packets, wire = _packets(packets, target)
        expectation = _expectation(expected_observation)
        intent = {"schema":1, "request_id":request_id, "session_id":self.session["session_id"],
                  "target":target, "packets":wire, "expectation":expectation}
        fingerprint = digest(intent)
        with lock(self.root / "operation.lock"):
            self._verify()
            names = self._runs()
            _require(request_id not in names, "input_request_exists", "Inspect the existing request; do not replay it.")
            _require(len(names) < MAX_RUNS, "input_journal_full", "Input history is full.")
            for name in names:
                _require(self._status(name)["state"] in ("rejected","acknowledged_by_host"),
                         "input_pending", "An earlier input requires readback or reconciliation.")
            run = self.root / "runs" / request_id
            run.mkdir(exist_ok=False)
            (run / "events").mkdir()
            _write(run / "intent.json", intent)
            _write(run / "attempt.json", {"schema":1, "intent_sha256":fingerprint})
            count, previous = 0, "0"*64
            def record(event):
                nonlocal count, previous
                _require(type(event) is dict and count < MAX_EVENTS, "input_journal_bound", "Input event is invalid or full.")
                value = {"schema":1, "index":count, "previous_sha256":previous, "event":event}
                _write(run / "events" / ("event-%04d.json" % count), value)
                previous, count = digest(value), count + 1
            try:
                returned = run_packets(deepcopy(target), packets, backend, record)
                _require(type(returned) is dict and returned.get("outcome") in ("queue_only","unresolved","not_dispatched"),
                         "invalid_input_result", "Input result is invalid.")
                state = {"queue_only":"awaiting_readback", "unresolved":"unresolved",
                         "not_dispatched":"rejected"}[returned["outcome"]]
            except Exception as error:
                # An escaped exception may follow a post or an unacknowledged
                # journal write. Report only a fixed code; never infer non-dispatch.
                code = error.code if isinstance(error, FoundryError) else None
                code = code if type(code) is str and code in _PUBLIC_RUNNER_CODES else "other"
                returned, state = {"diagnostic_source":"runner_exception", "diagnostic_code":code,
                                   "input_attempted":"unknown", "no_replay":True}, "unresolved"
                try:
                    count, previous = self._events(run)
                except Exception:
                    return {"ok":False, "state":"unresolved", "request_id":request_id,
                            "intent_sha256":fingerprint}
            result = {"schema":1, "intent_sha256":fingerprint, "event_count":count,
                      "last_event_sha256":previous, "state":state, "result":returned}
            try:
                _write(run / "result.json", result)
            except Exception:
                return {"ok":False, "state":"unresolved", "request_id":request_id, "intent_sha256":fingerprint}
            return {"ok":state != "unresolved", "state":state, "request_id":request_id,
                    "intent_sha256":fingerprint, "result":returned}

    def acknowledge(self, request_id, observer):
        """Record trusted host readback for a completed queue-only attempt."""
        _require(callable(observer), "invalid_observer", "An explicit host observer is required.")
        with lock(self.root / "operation.lock"):
            self._verify()
            status = self._status(request_id)
            _require(status["state"] == "awaiting_readback", "input_unresolved",
                     "Only a completed sequence awaiting readback can be acknowledged.")
            expected = {"schema":1, "request_id":request_id, "session_id":self.session["session_id"],
                        "intent_sha256":status["intent_sha256"]}
            view = deepcopy(expected)
            target_view = deepcopy(self.session["target"])
            intent = _read(self.root / "runs" / request_id / "intent.json")
            expectation = _expectation(intent["expectation"])
            expectation_view = deepcopy(expectation)
            try:
                observed = observer(view, target_view, expectation_view)
            except Exception:
                raise FoundryError("input_readback_unavailable", "Host readback is unavailable.") from None
            try:
                matched = (canonical(view) == canonical(expected)
                           and canonical(target_view) == canonical(self.session["target"])
                           and canonical(expectation_view) == canonical(expectation)
                           and type(observed) is dict
                           and set(observed) == set(expected) | {"evidence_sha256","observation"}
                           and canonical({key:observed.get(key) for key in expected}) == canonical(expected)
                           and type(observed["evidence_sha256"]) is str and HASH.fullmatch(observed["evidence_sha256"])
                           and canonical(_expectation(observed["observation"])) == canonical(expectation))
            except Exception:
                matched = False
            _require(matched,
                     "input_readback_unavailable", "Host readback did not match the original input.")
            ack = dict(expected, evidence_sha256=observed["evidence_sha256"],
                       observation_sha256=digest(expectation))
            _write(self.root / "runs" / request_id / "ack.json", ack)
            return {"ok":True, "state":"acknowledged_by_host", "request_id":request_id,
                    "intent_sha256":status["intent_sha256"], "game_qualified":False}
