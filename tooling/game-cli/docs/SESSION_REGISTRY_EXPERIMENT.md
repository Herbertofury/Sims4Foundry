# Shared session registry experiment

`session_registry.SessionRegistry` keeps one authenticated append-only chain
per PID, process creation time and executable hash. A session binds a session ID
and journal digest. Input and exit attempts share a single pending gate; a
second action is refused until the original request has a bound resolution.
Records use HMAC-SHA256 and are linked by digest. An externally supplied
trusted anchor must durably and atomically compare-and-swap the chain head;
otherwise deleting a valid tail cannot be detected. The registry never retries
an anchor update after a lost acknowledgement. A mismatch fails closed.

`registered_input` binds the registry session to the chosen journal directory
path and session record, registers the immutable input intent before calling the
journal, and keeps the common gate pending until an acknowledgement for that
same intent is present. A registry or journal failure leaves it pending. This
only coordinates callers that use these functions and the same authority. The
path binding alone does not retain a directory handle. On Windows,
acknowledgement reconciliation pins each validated record and its ancestor path
through the registry update and repeats full journal validation under those
pins. This does not prevent the creation of new files in a pinned directory or
protect other lifecycle stages against same-path replacement.

The key, anchor and common registry directory are supplied by the hosting
authority. The module does not create or authenticate that authority, store a
key securely, authenticate clients, establish a Windows user or process lease,
or stop an independent program from sending input. The synthetic anchor used
by tests is only in memory. Supplying different keys, anchors or directories
does not make a shared authority. Receipt hashes bind claims supplied by a
trusted caller; they do not verify UI state or the source of a receipt.

This is an integration component, not yet the shared production authority:
secure cross-process key distribution, a durable rollback-resistant anchor,
provider-owned retained-handle witnesses, original-journal and receipt
verification, and explicit reconciliation of a persisted record whose anchor
did not advance remain required. No command enables game input from this
registry. Its tests use synthetic process identities and files outside the game
and profile; they are not live-game qualification.

`SQLiteAnchor` supplies an explicit local database with full-synchronous
SQLite transactions and cross-process compare-and-swap for cooperative
experiments. It can detect a removed chain tail while its head is intact, but
does not prevent another same-user process from replacing or rolling back both
the database and chain. It is not an independently protected anchor and does
not qualify production input or exit.

`windows_registry_key` can explicitly create and reopen a random registry key
protected by Windows DPAPI for the current user, scoped to a chosen digest. A
Windows fixture has exercised reopening it in a separate owned process. This
protects key material at rest from other accounts under ordinary DPAPI policy;
it does not authenticate other programs running as the same user, prevent key
file replacement, ensure secure memory, or make the SQLite anchor rollback
resistant. Treat the key, authority and anchor as experimental together.
