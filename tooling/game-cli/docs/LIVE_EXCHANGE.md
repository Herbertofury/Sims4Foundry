# Source-only Live read exchange

`companion_live_transport.exchange_once` is a separate, default-denied host
sender for the exact schema-3 Live read types. It is not a CLI live command,
installed companion, game provider, production permission policy, credential
provisioner or independent outcome verifier. Importing it opens no connection.
The existing schema-2 sender and authentication protocol remain separate.

## One original intent, one connection

The sender accepts only an exact `LiveQueueSubmission` or
`LiveQueueReceiptRead`. Before constructing a socket or calling a peer policy,
it captures the complete canonical original submission as private bytes. Actor,
all context fields, session, PID/creation lifetime, request ID and exact TTL
numeric kind remain bound together. Receipt reads derive their digest and query
only from that original submission; they do not renew or replace its intent.

Peer authorization receives a newly reconstructed submission. Its verdict must
be exact `True`, and its supplied view must still match the original bytes.
The application frame was already captured, and final receipt validation uses
a different reconstructed view. Retained caller or authorization aliases do not
become the receipt parser's baseline. This protects supplied values, not arbitrary
trusted Python execution in the same interpreter.

There is one IPv4 loopback connection to the explicit port. One absolute
monotonic deadline covers connect, peer authorization, application send,
half-close, response framing, EOF, decoding and validation. The maximum remains
five seconds; partial progress never renews the deadline. A trusted callback
that blocks cannot be preempted by this Python function.

After peer authorization, the application phase sends one bounded frame and
write EOF, then requires one bounded response and EOF. Existing frame, JSON,
result and whole-observation limits do not change. A second frame, incomplete
reply, invalid schema or wrong actor/context binding cannot become success.
The full validated receipt is returned transiently, with its unverified and
non-independent meanings preserved.

## Authentication and uncertain outcomes

An explicitly supplied peer hook is mandatory. The existing session-key hook
can be composed with this sender without changing its transcript. It proves
key possession and binds declared session/epoch and socket endpoints. It does
not establish game identity, operation permission, schema-3 capability or an
encrypted/integrity-protected application channel. No real key is supplied or
provisioned by this checkpoint.

There is no reconnect, retry, downgrade, fallback or resubmission. A complete
old-peer schema-2 error is an uncertain error, never evidence that the operation
did not run. Missing, late or malformed responses likewise cannot establish
known non-dispatch. The function does not enforce durable no-replay across
independent calls; the intent journal is the required orchestration boundary.

In the injected journal composition, immutable intent precedes host admission
and an immutable attempt precedes the sender callback. After uncertainty,
`reconcile(original_request_id)` may construct only an original-bound receipt
read. A new request ID, new session or restarted listener is not recovery.
The journal retains receipt summaries and digests, not observation bodies.

## Fixture validation scope

The dedicated tests use scripted sockets, synthetic owner-thread readers,
public deterministic test keys, temporary journals and fixture-owned loopback
listeners. Optional reference children use only the explicitly configured
`FOUNDRY_COMPANION_PYTHON37`, assert Python 3.7 and disable site imports and
bytecode writes. Without that interpreter the optional cases must report skips.
These are not user-desktop, Sims, profile, save or installed-mod tests.

The intended integration covers accepted-before-owner-read, one owner tick,
complete read receipts, whole-read row overflow, exact receipt-only recovery
after reply loss or timeout, and bounded cleanup of owned fixture resources.
Fault cases must establish their actual milestones instead of accepting an
unrelated earlier exception. See [VALIDATION.md](VALIDATION.md) for completed
evidence and skips; this contract alone is not a passing test report.

The prior final independent owner/transport re-review remains incomplete.
This separate sender does not complete or replace that review. Production
admission, native-reader purity, current-game qualification, durable shared
history, Windows power-loss behavior and all real mutations remain outstanding
under [QUALIFICATION.md](QUALIFICATION.md).
