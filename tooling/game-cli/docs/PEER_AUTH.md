# Explicit session-key peer hooks, source only

This checkpoint implements mutual HMAC-SHA256 challenge-response at the existing
peer-policy seam. It does **not** provision a key, enable a listener, install a
mod, qualify a provider, alter a profile, or open a CLI execution path. Imports
and object construction do no I/O. All tests use public test-only keys and
fabricated ephemeral loopback sockets. No game API is imported or called.

## Narrow integration contract

- Embedded Python 3.7: `foundry_companion.peer_auth.SessionKeyPeer(key)` exposes
  `authorize_peer(connection, peer, binding, deadline)`. A trusted host may
  delegate **only** its peer callback to this method. It returns the exact boolean
  `True` after authenticating the client; ordinary failures return `False` and
  the transport closes the socket without an unauthenticated application error.
- Current CLI: `sims4foundry_cli.companion_auth.SessionKeyPeer(key)` is callable as
  `authorize_peer(connection, deadline, original_submission)` in `exchange_once`.
  It raises `PeerAuthError` on failure. The client sends no application request
  before validating both the server proof and final server confirmation.
- Neither object implements `authorize_listener`, `authorize_submit` or
  `authorize_dispatch`. The existing deny-by-default policies, queue, native
  admission, session/process/profile/lease/qualification and durable-journal
  requirements are unchanged. The server calls its peer callback before any
  application parsing, queue submission or receipt lookup. No wire flag enables
  this hook and no production host policy is supplied.

The transport owns socket creation, closure, capacity and the **single original
absolute monotonic deadline**. Both hooks use that deadline for every read and
write, verify it around CPU work, and refuse expired, nonfinite or more-than-five-
seconds-away deadlines. Partial reads do not renew it. There is no authentication
retry, fallback, resynchronization or handshake state retained across connections.
An authentication failure requires closing that socket, never continuing its
application parsing. Existing transport code already enforces this contract.

## Key provisioning is a separate authorized step

The constructor accepts an explicitly supplied immutable `bytes` key, 32 through
64 bytes inclusive. It does not generate, derive, discover, load, save, rotate,
transmit or print a key, and cannot establish its entropy. A future, separately
reviewed and authorized host must securely provision a cryptographically random
secret unique to the intended session and participants. A password, padded string
or reused fixture is not a suitable key; a captured transcript permits offline
guessing of a weak secret. No key is taken from game configuration or the wire.

Every holder of the same secret can impersonate either role. Key possession does
not distinguish people, users, processes, executables or instances that share it.
The host must independently establish the expected process lifetime and session
before supplying their binding. Secure distribution, authorization, isolation,
revocation and lifecycle remain absent. Adding this source alone cannot qualify
a real listener or create a persistent access grant.

The object retains a key reference in memory. Its representation is redacted and
pickling is refused; diagnostics never include keys, proofs, nonces or peer data.
There is no logging. Python immutable bytes do not provide guaranteed secure
zeroization, and a process-memory reader can still recover the secret. Tests
intentionally contain **public, deterministic test-only keys**, not credentials.

## Exact protocol and golden transcript

Each handshake message is one four-byte unsigned big-endian length followed by
1 through 1,024 bytes of strict UTF-8 JSON. There are exactly four states:

1. Client sends `client_hello` containing exactly `auth_schema: 1`, `kind`,
   `session_id`, `process_epoch`, `connection` and `client_nonce`.
2. Server sends `server_proof` with the same fields, plus `server_nonce` and
   `proof`. Both endpoints check their independently expected binding.
3. Client verifies the server proof before sending `client_proof` with the same
   field set. The server verifies it before permitting any application access.
4. Server sends `server_ready` with the same field set and a distinct MAC. The
   client verifies this explicit confirmation before returning to `exchange_once`.

`process_epoch` has exactly `pid` and `creation_filetime`, positive bounded
integers (uint32 and uint64 respectively). `session_id` is exactly 32 lowercase
hexadecimal characters. `connection` has exactly `client_port` and `server_port`,
integers 1 through 65,535. Each side obtains both endpoints from its actual
`AF_INET`/`SOCK_STREAM` socket's `getsockname` and `getpeername`; both addresses
must be exactly `127.0.0.1`. Claimed ports are compared with these local values,
never used as authority. The server also checks the callback's peer tuple.

Nonces are 32 fresh bytes from `os.urandom`, represented as 64 lowercase hex
characters. Each endpoint creates its own nonce on every invocation. Equal
client/server nonces fail closed, with no RNG fallback or retry. Replay
resistance depends on this cryptographic freshness, not an unbounded nonce cache.
An old proof does not match the next independently random challenge.

For each of the three proof-bearing messages, remove `proof` and compute:

```text
HMAC-SHA256(explicit_key,
  b"Foundry companion peer authentication\x00v1\x00"
  + canonical_UTF8_JSON(unsigned_message))
```

Canonical JSON uses sorted keys, no insignificant whitespace, no ASCII escaping,
and no nonfinite numbers, matching the existing codecs. The four-byte framing
prefix is not included in the MAC. The complete unsigned object is included:
version, role/state `kind`, exact session/process lifetime, both locally observed
ports and both nonces. Distinct `kind` values separate the client proof, server
proof and server confirmation; no role can reuse another's proof. The fixed
prefix separates this protocol from other uses of HMAC. No algorithm negotiation,
truncated MAC, key derivation or homemade cryptographic primitive is used.

`proof` is the full 32-byte tag represented as 64 lowercase hex characters. It is
validated for exact shape, then compared with `hmac.compare_digest`. All schemas
reject missing/extra fields, duplicate keys, bool-as-int, floating-point IDs,
noncanonical tokens, nonfinite/oversized integers and unpaired surrogates.
Structural nesting is capped at two **before** recursive JSON parsing. Reads and
writes are bounded; malformed input never returns to an earlier protocol state.

`tests/test_companion_auth.py` pins all four complete framed golden messages,
including their MACs, using explicitly public fixture key/nonces/ports. Both
codecs and the real isolated Python-3.7 subprocess must match these exact bytes.
The small shared helper block is mirrored because the CLI package cannot depend
on the uninstalled game-source tree; a source-parity test prevents silent drift.

## Application boundary and threat model

After these four messages, the existing schema-2 transport still accepts exactly
one application frame followed by client write EOF, then one response and EOF.
Repeated/out-of-order handshake messages fail validation. A surplus proof or any
other extra frame in the server's application stream causes rejection **before
queue/receipt access**. No pipelining or generic message relay is added. A missing
or malformed server proof/confirmation prevents client application send. Bytes
arriving after a valid confirmation belong to the application-response phase;
that phase's strict framing/schema validation rejects surplus handshake frames,
without claiming the request was never sent or undoing possible admission.

The security claim is narrowly possession of the explicit session key for this
expected session and socket under a trusted local OS/TCP implementation. Binding
both locally observed TCP ports rejects ordinary proxies that relay the handshake
across two different loopback connections. It is **not** general cryptographic
channel binding, OS identity, protection from privileged packet injection/socket
hijacking, or protection from code that can read process memory. Rewriting relayed
ports invalidates the MAC; merely forwarding them fails local endpoint comparison.

Application requests and receipts remain unMACed and unencrypted. These hooks do
not create an integrity-protected/encrypted application channel, protect against
a malicious key holder, provide forward secrecy or independently prove that the
socket belongs to a game process. Loopback, PID, FILETIME, session IDs and unkeyed
hashes alone remain unauthenticated. The separate trusted-host gates and any
stronger channel/process-isolation requirements still need qualification.

Authentication does not resolve uncertain application outcomes. Retain the
transport's no-replay rules: after an uncertain exchange, consult the exact
original receipt under a fresh authenticated connection, never replace an ID or
session to try again. An authenticated receipt is still only an adapter assertion,
not independent game evidence or proof of save persistence. The separate
[offline client ledger](COMPANION_JOURNAL.md) stores no peer keys, nonces or proofs,
and does not connect these hooks to a production executor.

## Reproducible isolated validation

From `tooling/game-cli`:

```sh
PYTHONPATH=src python -B -m unittest discover -s tests -p 'test_companion_auth.py' -v

PYTHONPATH=tests /path/to/python3.7 -B -S -s -m unittest test_companion_auth -v

FOUNDRY_COMPANION_PYTHON37=/path/to/python3.7 PYTHONPATH=src \
  python -B -m unittest discover -s tests -p 'test_companion_auth.py' -v

PYTHONPATH=src python -B -m unittest discover -s tests -v
```

Coverage includes strict key limits/redaction, golden transcript parity, role and
domain separation, exact session/PID/FILETIME/socket binding, replay/reflection,
wrong keys, malformed/truncated/out-of-order/surplus frames, bounded depth/bytes,
slow-drip absolute deadlines, entropy-source failure, a real two-connection relay,
no unauthenticated queue/receipt access and no client application send before
server confirmation. A separate actual Python-3.7 server accepts the current
client's authenticated submission/receipt and refuses the wrong key with no
retained request. All native and host authorization remains default-denied.

These are source/compatibility/security regressions on fabricated sockets, not an
external cryptographic audit or production/game qualification. Python 3.7 is
end-of-life; it is used only as an isolated trusted compatibility fixture. Native
Windows execution and the actual embedded game runtime still require their own
qualification. No profile, process, desktop, real credential or live chat is used.

## Primary references

- [Python `hmac` and constant-time comparison guidance](https://docs.python.org/3/library/hmac.html)
- [Python `os.urandom`](https://docs.python.org/3/library/os.html#os.urandom)
- [Python socket APIs and total `sendall` timeout](https://docs.python.org/3/library/socket.html#socket.socket.sendall)
- [RFC 2104, HMAC and key requirements](https://www.rfc-editor.org/rfc/rfc2104)
- [RFC 4231, HMAC-SHA-256 reference test vectors](https://www.rfc-editor.org/rfc/rfc4231.html)
