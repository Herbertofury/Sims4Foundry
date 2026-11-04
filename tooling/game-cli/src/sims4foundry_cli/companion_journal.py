"""Source-only, restart-safe client intent ledger. No default executor or CLI.

A trusted injected host must authorize each callback; this module supplies no
host, authentication, independent outcome verifier, or live qualification. An
attempt means possible transmission, NOT observed dispatch. Adapter assertions
never become independent game evidence. Hashes detect inconsistency, not forgery.

Storage is a cooperative external workspace, using io's stable OS lock and
no-overwrite fsynced records. Windows power-loss ordering is NOT qualified.
Missing/partial records fail closed. Coherent rollback/removal of the whole
workspace cannot be detected locally; a trusted cross-workspace history anchor
is required before live use. There is no reset, resend, renewal, or deletion API.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
import re
import stat
import uuid

from . import companion_protocol as wire
from . import companion_live_protocol as live
from .companion_transport import ExpectedContext, ReceiptRead, Submission, parse_receipt
from .errors import FoundryError
from .io import external_path, lock, new_json, plain_path, replace_json, sync_directory

SCHEMA = 1
MAX_REQUESTS = 128
MAX_RECORDS = 1024
MAX_ATTEMPTS = 32
MAX_RECORD_BYTES = 4096
_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
# Only these fixed public protocol codes may appear in stored diagnostics.
# A regex alone would allow a token-shaped secret to survive in receipt.reason.
_PUBLIC_REASONS = frozenset({
    "history_missing", "history_full", "pending_full", "expired", "invalid_clock",
    "unresolved_mutation", "request_conflict", "host_shutdown", "transport_shutdown",
    "host_admission_denied", "host_dispatch_denied", "queue_admission_failed",
    "queue_busy_before_start", "execution_exception", "invalid_execution_result",
    "adapter_refused", "adapter_unknown",
})
_ROOT_NAMES = {"journal.json", "head.json", "operation.lock", "records"}
_TERMINAL = {"refused", "unknown", "not_found", "adapter_returned"}


def _require(ok, code="journal_corrupt", message="Journal history is incomplete or inconsistent; no replay is permitted."):
    if not ok:
        raise FoundryError(code, message)


def _fields(value, names):
    _require(type(value) is dict and value.keys() == set(names))


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _matches(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _payload(value):
    result = wire.encode_frame(value)[4:]
    _require(len(result) < MAX_RECORD_BYTES, "journal_bound", "Journal record exceeds its byte bound.")
    return result


def _sha(value):
    return hashlib.sha256(_payload(value)).hexdigest()


def _plain(path, *, directory=False):
    path = plain_path(path)
    info = path.lstat()
    _require(stat.S_ISDIR(info.st_mode) if directory else
             (stat.S_ISREG(info.st_mode) and info.st_nlink == 1))
    return path


def _names(path, limit):
    _plain(path, directory=True)
    names = set()
    with os.scandir(path) as entries:
        for entry in entries:
            _require(len(names) < limit, "journal_bound", "Journal directory exceeds its scan bound.")
            names.add(entry.name)
    return names


def _read(path):
    with _plain(path).open("rb") as stream:
        raw = stream.read(MAX_RECORD_BYTES + 1)
    _require(0 < len(raw) <= MAX_RECORD_BYTES, "journal_bound", "Journal file is empty or exceeds its byte bound.")
    return wire.decode_payload(raw)


def _submission(value):
    _require(type(value) is dict and type(value.get("schema")) is int)
    if value["schema"] == 3:
        epoch = value.get("process_epoch")
        _fields(epoch, {"pid", "creation_filetime"})
        return live.parse_live_submit(value, value.get("session_id"), wire.ProcessEpoch(**epoch))
    # No version inference or fallback: schema 2 keeps its original exact parser.
    _fields(value, {"schema", "kind", "request", "ttl_seconds", "expected_context"})
    _require(type(value["schema"]) is int and value["schema"] == 2 and value["kind"] == "submit")
    request = value["request"]
    _require(type(request) is dict and type(request.get("process_epoch")) is dict)
    _fields(request["process_epoch"], {"pid", "creation_filetime"})
    epoch = wire.ProcessEpoch(**request["process_epoch"])
    request = wire.parse_request(request, session_id=request["session_id"], process_epoch=epoch)
    context = value["expected_context"]
    if context is not None:
        _fields(context, {"zone_id", "household_id", "save_slot", "save_guid"})
        context = ExpectedContext(**context)
    return Submission(request, value["ttl_seconds"], context)


def _legacy_submission_wire(message):
    """Capture legacy primitive fields without invoking instance serializers."""
    _require(type(message) is Submission and type(message.request) is wire.Request,
             "invalid_request", "Use an exact supported submission.")
    request = message.request
    _require(type(request.process_epoch) is wire.ProcessEpoch
             and type(request.operation) is wire.Operation,
             "invalid_request", "Use exact request identity and operation types.")
    arguments = request.arguments
    argument_type = wire._ARGUMENT_TYPES[request.operation][0]
    _require(type(arguments) is argument_type, "invalid_request", "Use exact operation arguments.")
    context = message.expected_context
    if context is not None:
        _require(type(context) is ExpectedContext, "invalid_request", "Use exact mutation context.")
        context = dict(zone_id=context.zone_id, household_id=context.household_id,
                       save_slot=context.save_slot, save_guid=context.save_guid)
    value = dict(schema=2, kind="submit", ttl_seconds=message.ttl_seconds,
                 expected_context=context,
                 request=dict(schema=1, kind="request", request_id=request.request_id,
                              session_id=request.session_id,
                              process_epoch=dict(pid=request.process_epoch.pid,
                                                 creation_filetime=request.process_epoch.creation_filetime),
                              operation=request.operation.value,
                              arguments=argument_type.to_wire(arguments)))
    # Construct fresh values through the unchanged v2 parser. No shadowed method
    # on a callback-owned request, nested value or submission is consulted.
    return Submission.to_wire(_submission(value))


def _message_wire(message):
    # Class dispatch avoids instance methods replaced with object.__setattr__.
    kind = type(message)
    if kind is Submission:
        return _legacy_submission_wire(message)
    if kind is ReceiptRead:
        original = _legacy_submission_wire(message.submission)
        request = original["request"]
        return dict(schema=2, kind="receipt_read", request_id=request["request_id"],
                    session_id=request["session_id"], process_epoch=request["process_epoch"],
                    request_sha256=hashlib.sha256(wire.encode_frame(original)[4:]).hexdigest())
    if kind is live.LiveQueueSubmission:
        return live.LiveQueueSubmission.to_wire(message)
    if kind is live.LiveQueueReceiptRead:
        return live.LiveQueueReceiptRead.to_wire(message)
    _require(False, "invalid_request", "Use an exact supported submission or receipt read.")


def _is_submission(message):
    _require(type(message) in (Submission, ReceiptRead, live.LiveQueueSubmission, live.LiveQueueReceiptRead),
             "invalid_request", "Use an exact supported submission or receipt read.")
    return type(message) in (Submission, live.LiveQueueSubmission)


def _is_mutation(submission):
    if type(submission) is live.LiveQueueSubmission:
        live.LiveQueueSubmission.to_wire(submission)
        return False
    _require(type(submission) is Submission and type(submission.request) is wire.Request,
             "invalid_request", "Use an exact supported submission.")
    operation = submission.request.operation
    if operation is wire.Operation.OBSERVE:
        return False
    _require(any(operation is allowed for allowed in (wire.Operation.SET_SPEED,
             wire.Operation.ENTER_CAS, wire.Operation.REQUEST_EXIT_MENU)),
             "invalid_request", "Unknown operations cannot create read or mutation authority.")
    return True


def _receipt_reader(submission):
    if type(submission) is live.LiveQueueSubmission:
        return live.LiveQueueReceiptRead(submission)
    _require(type(submission) is Submission, "invalid_request", "Use an exact supported submission.")
    return ReceiptRead(submission)


def _parse_receipt(receipt, submission):
    if type(submission) is live.LiveQueueSubmission:
        return live.parse_live_receipt(receipt, submission)
    _require(type(submission) is Submission, "invalid_request", "Use an exact supported submission.")
    return parse_receipt(receipt, submission)


def _freeze(submission):
    _require(type(submission) in (Submission, live.LiveQueueSubmission),
             "invalid_request", "Use an exact typed schema-2 or schema-3 submission.")
    # Copy through the strict codec: never retain caller-owned values, and never
    # use Python numeric equality to conflate a 5 with a 5.0 TTL/digest.
    return _submission(wire.decode_payload(_payload(_message_wire(submission))))


@dataclass(frozen=True)
class Attempt:
    """Exact local callback identity. It is not a wire/peer authorization token."""
    journal_id: str
    request_id: str
    request_sha256: str
    attempt_id: str
    sequence: int
    kind: str  # submit or receipt_read; only one submit can ever exist

    def to_wire(self):
        _require(type(self) is Attempt and _matches(self.journal_id, _ID)
                 and _matches(self.request_id, _ID) and _matches(self.request_sha256, _HASH)
                 and _matches(self.attempt_id, _ID) and _integer(self.sequence, 1, MAX_ATTEMPTS)
                 and type(self.kind) is str and self.kind in ("submit", "receipt_read"),
                 "invalid_attempt", "Expected exact immutable attempt fields.")
        return dict(journal_id=self.journal_id, request_id=self.request_id,
                    request_sha256=self.request_sha256, attempt_id=self.attempt_id,
                    sequence=self.sequence, kind=self.kind)


def _callback_binding_matches(message, ticket, message_bytes, ticket_bytes):
    try:
        return (_payload(_message_wire(message)) == message_bytes
                and _payload(Attempt.to_wire(ticket)) == ticket_bytes)
    except (wire.ProtocolError, FoundryError, ValueError, TypeError, KeyError, AttributeError):
        return False


@dataclass(frozen=True)
class ReceiptSnapshot:
    state: str
    reason: str | None
    adapter_outcome: str | None
    receipt_sha256: str

    def to_wire(self):
        return dict(state=self.state, reason=self.reason,
                    adapter_outcome=self.adapter_outcome, receipt_sha256=self.receipt_sha256)


def _snapshot(receipt, submission):
    # Parse a detached bounded copy. Arbitrary adapter_result details and raw
    # exceptions are NEVER persisted (they could contain secrets/account data).
    receipt = wire.decode_payload(wire.encode_frame(receipt)[4:])
    _parse_receipt(receipt, submission)
    outcome = receipt["adapter_result"]["outcome"] if receipt["adapter_result"] else None
    reason = receipt["reason"]
    if reason is not None and reason not in _PUBLIC_REASONS:
        reason = "adapter_refused" if receipt["state"] == "refused" else "adapter_unknown"
    return ReceiptSnapshot(receipt["state"], reason, outcome,
                           hashlib.sha256(wire.encode_frame(receipt)[4:]).hexdigest())


def _parse_snapshot(value):
    _fields(value, {"state", "reason", "adapter_outcome", "receipt_sha256"})
    state, reason, outcome = value["state"], value["reason"], value["adapter_outcome"]
    _require(type(state) is str and _matches(value["receipt_sha256"], _HASH))
    if state in ("accepted", "dispatched"):
        _require(reason is None and outcome is None)
    elif state == "adapter_returned":
        _require(reason is None and type(outcome) is str and outcome in ("verified", "unverified"))
    else:
        _require(state in ("refused", "unknown", "not_found") and outcome is None
                 and type(reason) is str and reason in _PUBLIC_REASONS
                 and (state != "not_found" or reason == "history_missing"))
    return ReceiptSnapshot(**value)


@dataclass(frozen=True)
class Status:
    request_id: str
    request_sha256: str
    state: str
    mutation: bool
    unresolved: bool
    transmission: str
    attempt_count: int
    last_attempt_id: str | None
    receipt: ReceiptSnapshot | None
    uncertainty_latched: bool
    independent_evidence: bool = False
    live_qualified: bool = False


class _Entry:
    def __init__(self, submission, intent_sequence):
        self.intent_sequence = intent_sequence
        self.submission = submission
        self.attempt = None
        self.attempt_record_sequence = None
        self.answered = True
        self.uncertain = False
        self.submitted = False
        self.receipt = None
        self.local_refused = False
        self.resolved = False
        self.count = 0

    def status(self):
        state = ("not_dispatched" if self.local_refused else
                 self.receipt.state if self.receipt else
                 "unknown" if self.attempt else "intent_recorded")
        if self.attempt and not self.answered:
            state = "unknown"
        return Status(self.submission.request.request_id, self.submission.request_sha256,
                      state, _is_mutation(self.submission),
                      not self.resolved, "possible" if self.attempt else "not_attempted",
                      self.count, self.attempt.attempt_id if self.attempt else None, self.receipt,
                      self.uncertain or bool(self.attempt and not self.answered))


def _progress(entry, snapshot):
    prior = entry.receipt
    if prior:
        if prior.state in _TERMINAL:
            _require(snapshot == prior, "receipt_conflict", "A terminal adapter receipt cannot change.")
        elif prior.state == "dispatched":
            _require(snapshot.state in {"dispatched", "adapter_returned", "unknown", "not_found"},
                     "receipt_conflict", "A dispatched operation cannot regress to a non-dispatch claim.")
    if snapshot.state == "refused":
        _require(entry.submitted and entry.attempt is not None and (entry.attempt.kind == "submit"
                 or (prior is not None and prior.state in ("accepted", "refused"))),
                 "receipt_conflict", "A first receipt read cannot establish non-dispatch chronology.")
        _require(not entry.uncertain, "receipt_conflict",
                 "A refusal after an uncertain attempt cannot establish non-dispatch.")


def create_journal(directory):
    """Create an exclusively new offline workspace, never reopen/reset history.

    Creating another workspace does not resolve anything or permit a live host.
    Parent directory must already exist. Partial initialization remains unusable.
    """
    directory = external_path(directory)
    directory.mkdir(exist_ok=False)
    sync_directory(directory.parent)
    (directory / "records").mkdir()
    with lock(directory / "operation.lock"):
        journal_id = uuid.uuid4().hex
        new_json(directory / "journal.json", {"schema": SCHEMA, "journal_id": journal_id,
                 "scope": "offline_client_ledger", "independent_evidence": False})
        replace_json(directory / "head.json", {"schema": SCHEMA, "journal_id": journal_id,
                     "sequence": 0, "record_sha256": None})
    sync_directory(directory)
    return Journal(directory)


class Journal:
    """One bounded ledger, with no production callback and no independent verifier.

    submit_once/reconcile are injection harnesses only. authorize(message,
    attempt) must independently admit the exact target/history/permissions and
    return exact True before exchange(message, attempt) may run. Authorization
    must not transmit or dispatch. exchange must authenticate its own peer and
    obey bounded deadlines; arbitrary Python callbacks cannot be preempted here.
    Hooks and secrets are never stored. A caller JSON flag cannot supply a hook.
    """
    def __init__(self, directory):
        self.directory = external_path(directory)

    @contextmanager
    def _locked(self):
        try:
            _require(_names(self.directory, len(_ROOT_NAMES)) == _ROOT_NAMES)
            lock_path = _plain(self.directory / "operation.lock")
            _require(lock_path.stat().st_size == 1)
            with lock(lock_path):
                yield
        except FoundryError:
            raise
        except (OSError, wire.ProtocolError, ValueError, KeyError, TypeError, AttributeError):
            raise FoundryError("journal_corrupt", "Journal is unreadable or incomplete; no replay is permitted.") from None

    def _load(self):
        _require(_names(self.directory, len(_ROOT_NAMES)) == _ROOT_NAMES)
        metadata = _read(self.directory / "journal.json")
        _fields(metadata, {"schema", "journal_id", "scope", "independent_evidence"})
        _require(type(metadata["schema"]) is int and metadata["schema"] == SCHEMA
                 and _matches(metadata["journal_id"], _ID)
                 and metadata["scope"] == "offline_client_ledger"
                 and metadata["independent_evidence"] is False)
        head = _read(self.directory / "head.json")
        _fields(head, {"schema", "journal_id", "sequence", "record_sha256"})
        _require(type(head["schema"]) is int and head["schema"] == SCHEMA
                 and head["journal_id"] == metadata["journal_id"]
                 and _integer(head["sequence"], 0, MAX_RECORDS))
        names = _names(self.directory / "records", MAX_RECORDS)
        _require(names == {f"{i:06d}.json" for i in range(1, head["sequence"] + 1)})
        entries, attempt_ids, prior = {}, set(), None
        for sequence in range(1, head["sequence"] + 1):
            record = _read(self.directory / "records" / f"{sequence:06d}.json")
            _fields(record, {"schema", "journal_id", "sequence", "previous_sha256", "kind", "request_id", "data"})
            _require(type(record["schema"]) is int and record["schema"] == SCHEMA
                     and type(record["sequence"]) is int and record["sequence"] == sequence
                     and record["journal_id"] == head["journal_id"] and record["previous_sha256"] == prior
                     and _matches(record["request_id"], _ID))
            request_id, kind, data = record["request_id"], record["kind"], record["data"]
            if kind == "intent":
                _require(request_id not in entries and len(entries) < MAX_REQUESTS)
                _fields(data, {"submission", "request_sha256"})
                submission = _submission(data["submission"])
                _require(submission.request.request_id == request_id
                         and submission.request_sha256 == data["request_sha256"])
                # Global mutation barrier, independent of session/PID/IDs.
                if _is_mutation(submission):
                    _require(not any(row.status().mutation and not row.resolved for row in entries.values()))
                entries[request_id] = _Entry(submission, sequence)
            else:
                _require(request_id in entries)
                entry = entries[request_id]
                if kind == "local_refusal":
                    _fields(data, {"reason"})
                    _require(data["reason"] == "host_admission_refused" and entry.attempt is None
                             and not entry.local_refused and sequence == entry.intent_sequence + 1)
                    entry.local_refused = entry.resolved = True
                elif kind == "attempt":
                    _fields(data, {"journal_id", "request_id", "request_sha256", "attempt_id", "sequence", "kind"})
                    _require(not entry.resolved and _matches(data["attempt_id"], _ID)
                             and data["attempt_id"] not in attempt_ids
                             and data["journal_id"] == head["journal_id"] and data["request_id"] == request_id
                             and data["request_sha256"] == entry.submission.request_sha256
                             and _integer(data["sequence"], 1, MAX_ATTEMPTS)
                             and data["sequence"] == entry.count + 1
                             and data["kind"] in ("submit", "receipt_read")
                             and (data["kind"] != "submit" or (entry.count == 0 and sequence == entry.intent_sequence + 1)))
                    if entry.attempt and not entry.answered:
                        entry.uncertain = True
                    entry.submitted = entry.submitted or data["kind"] == "submit"
                    entry.attempt_record_sequence = sequence
                    entry.attempt, entry.answered = Attempt(**data), False
                    entry.count += 1
                    attempt_ids.add(data["attempt_id"])
                elif kind in ("receipt", "exchange_failed"):
                    _require(entry.attempt is not None and not entry.answered
                             and sequence == entry.attempt_record_sequence + 1)
                    _fields(data, {"attempt_id", "attempt_sequence", "snapshot" if kind == "receipt" else "reason"})
                    _require(data["attempt_id"] == entry.attempt.attempt_id
                             and type(data["attempt_sequence"]) is int
                             and data["attempt_sequence"] == entry.attempt.sequence)
                    if kind == "exchange_failed":
                        _require(data["reason"] in ("callback_failed", "invalid_receipt"))
                        entry.uncertain = True
                    else:
                        snapshot = _parse_snapshot(data["snapshot"])
                        if type(entry.submission) is live.LiveQueueSubmission:
                            _require(snapshot.adapter_outcome in (None, "unverified"))
                        _progress(entry, snapshot)
                        entry.receipt = snapshot
                        if snapshot.state in ("unknown", "not_found"):
                            entry.uncertain = True
                        entry.resolved = snapshot.state == "refused" and not entry.uncertain
                    entry.answered = True
                else:
                    _require(False)
            prior = _sha(record)
        _require(head["record_sha256"] == prior)
        return head, entries

    def _append(self, head, kind, request_id, data):
        _require(head["sequence"] < MAX_RECORDS, "journal_full", "Journal history is full; no eviction or reset is supported.")
        sequence = head["sequence"] + 1
        record = {"schema": SCHEMA, "journal_id": head["journal_id"], "sequence": sequence,
                  "previous_sha256": head["record_sha256"], "kind": kind,
                  "request_id": request_id, "data": data}
        digest = _sha(record)
        # No callback can run until BOTH publications finish. Orphans, pending
        # files, missing records and a head mismatch all block subsequent use.
        new_json(self.directory / "records" / f"{sequence:06d}.json", record)
        head = dict(head, sequence=sequence, record_sha256=digest)
        replace_json(self.directory / "head.json", head)
        return head

    def _intent(self, head, entries, submission):
        request_id = submission.request.request_id
        if request_id in entries:
            entry = entries[request_id]
            _require(_payload(_message_wire(entry.submission)) == _payload(_message_wire(submission)),
                     "intent_conflict", "Request ID already binds a different immutable submission.")
            return head, entry, False
        _require(len(entries) < MAX_REQUESTS, "journal_full", "Retained intent limit reached; no eviction or reset is supported.")
        if _is_mutation(submission):
            _require(not any(row.status().mutation and not row.resolved for row in entries.values()),
                     "mutation_unresolved", "An earlier mutation is unresolved; changing ID/session does not resolve it.")
        head = self._append(head, "intent", request_id,
                            {"submission": _message_wire(submission), "request_sha256": submission.request_sha256})
        return head, _Entry(submission, head["sequence"]), True

    @staticmethod
    def _hooks(authorize, exchange):
        _require(callable(authorize) and callable(exchange), "host_policy_required",
                 "No production/default executor exists; independent trusted host admission is required.")

    def status(self, request_id=None):
        """Read local recorded status only; no network, renewal or inferred result."""
        with self._locked():
            _, entries = self._load()
            if request_id is None:
                return tuple(entry.status() for entry in entries.values())
            _require(request_id in entries, "intent_missing", "Original intent is absent; do not resubmit or replace it.")
            return entries[request_id].status()

    def record_intent(self, submission):
        """Persist without transmission. Existing intents can never be submitted later."""
        submission = _freeze(submission)
        with self._locked():
            head, entries = self._load()
            _, entry, _ = self._intent(head, entries, submission)
            return entry.status()

    def submit_once(self, submission, *, authorize=None, exchange=None):
        """For NEW intent only, persist intent then one admitted callback attempt.

        An identical existing ID only returns status, including after crashes.
        No existing intent, unanswered attempt or expired TTL can be resubmitted.
        """
        submission = _freeze(submission)
        with self._locked():
            head, entries = self._load()
            if submission.request.request_id in entries:
                return self._intent(head, entries, submission)[1].status()
            self._hooks(authorize, exchange)
            head, entry, _ = self._intent(head, entries, submission)
            return self._exchange(head, entry, submission, authorize, exchange)

    def reconcile(self, request_id, *, authorize=None, exchange=None):
        """Read only the EXACT persisted original receipt. Never call submit."""
        with self._locked():
            head, entries = self._load()
            _require(request_id in entries, "intent_missing", "Original intent is absent; do not replace it.")
            entry = entries[request_id]
            if entry.resolved:
                return entry.status()
            self._hooks(authorize, exchange)
            return self._exchange(head, entry, _receipt_reader(entry.submission), authorize, exchange)

    def _exchange(self, head, entry, message, authorize, exchange):
        _require(entry.count < MAX_ATTEMPTS and head["sequence"] + 2 <= MAX_RECORDS,
                 "journal_full", "Bounded attempt/history capacity exhausted; no reset or replay is supported.")
        kind = "submit" if _is_submission(message) else "receipt_read"
        original_bytes = _payload(_message_wire(entry.submission))
        saved_ticket = Attempt(head["journal_id"], entry.submission.request.request_id,
                         entry.submission.request_sha256, uuid.uuid4().hex, entry.count + 1,
                         kind)
        message_bytes = _payload(_message_wire(message))
        ticket_bytes = _payload(Attempt.to_wire(saved_ticket))

        def detached_message():
            original = _submission(wire.decode_payload(original_bytes))
            return original if kind == "submit" else _receipt_reader(original)

        # Admission never receives the retained intent or an exchange-owned view.
        admission_message = detached_message()
        admission_ticket = Attempt(**wire.decode_payload(ticket_bytes))
        _require(_callback_binding_matches(admission_message, admission_ticket, message_bytes, ticket_bytes))
        try:
            admitted = authorize(admission_message, admission_ticket)
        except Exception:
            raise FoundryError("host_admission_failed", "Admission failed; existing intent remains unresolved.") from None
        _require(_callback_binding_matches(admission_message, admission_ticket, message_bytes, ticket_bytes),
                 "admission_changed", "Trusted admission changed the immutable callback binding.")
        if admitted is False and kind == "submit":
            self._append(head, "local_refusal", saved_ticket.request_id, {"reason": "host_admission_refused"})
            return self._load()[1][saved_ticket.request_id].status()
        _require(admitted is True, "host_admission_refused", "Trusted host did not admit this exact callback.")
        # Retained admission aliases cannot redirect these fresh callback views.
        exchange_message = detached_message()
        exchange_ticket = Attempt(**wire.decode_payload(ticket_bytes))
        head = self._append(head, "attempt", saved_ticket.request_id, Attempt.to_wire(saved_ticket))
        # An exception/crash from here forward is possible transmission, even if
        # no bytes were actually sent. No later refusal may clear uncertainty.
        try:
            receipt = exchange(exchange_message, exchange_ticket)
        except Exception:
            self._append(head, "exchange_failed", saved_ticket.request_id,
                         {"attempt_id": saved_ticket.attempt_id, "attempt_sequence": saved_ticket.sequence,
                          "reason": "callback_failed"})
            raise FoundryError("exchange_uncertain", "Callback failed; only receipt reads are allowed.") from None
        try:
            _require(_callback_binding_matches(exchange_message, exchange_ticket, message_bytes, ticket_bytes))
            # Evaluate against the reloaded attempt chronology, including an
            # unanswered earlier attempt after a process restart.
            current = self._load()[1][saved_ticket.request_id]
            snapshot = _snapshot(receipt, current.submission)
            _progress(current, snapshot)
        except (wire.ProtocolError, FoundryError, ValueError, TypeError, KeyError, AttributeError):
            self._append(head, "exchange_failed", saved_ticket.request_id,
                         {"attempt_id": saved_ticket.attempt_id, "attempt_sequence": saved_ticket.sequence,
                          "reason": "invalid_receipt"})
            raise FoundryError("receipt_uncertain", "Receipt is invalid or contradicts history; outcome remains unresolved.") from None
        self._append(head, "receipt", saved_ticket.request_id,
                     {"attempt_id": saved_ticket.attempt_id, "attempt_sequence": saved_ticket.sequence,
                      "snapshot": snapshot.to_wire()})
        return self._load()[1][saved_ticket.request_id].status()
