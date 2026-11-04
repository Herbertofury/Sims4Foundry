# Companion recovery integration regressions

`tests/test_companion_recovery.py` composes the existing durable client journal,
current typed client, mutual session-key authentication, real IPv4 loopback
transport and owner-thread queue. It changes no production component and enables
no live controller. All process identities, keys, context, host admission and
adapter outcomes are fabricated. No game, profile, desktop or account is accessed.

## What the tests establish

- The exact immutable submission, its digest and its first attempt are already
  published on disk before the client opens its application exchange. The real
  server's admission hook independently reads those same records before queueing.
- Socket workers authenticate and admit requests but never execute the adapter.
  Only the captured owner thread can tick the queue. A wrong-thread tick fails.
- The reply-loss fixture waits until the real authenticated submission is queued,
  ticks the owner once, then closes that owned server socket before its reply
  write. The client observes a missing reply and the server latches response loss.
- The timeout fixture holds that first receipt until the real client's absolute
  socket deadline expires. It does not simulate a timeout by raising one, sleep
  to synchronize threads, or replace the transport with a receipt stub.
- Reopening the same journal after either failure preserves uncertainty. An
  identical request ID never calls the admission or exchange hooks again.
  Recovery sends only a receipt read bound to the original submission and digest.
- A callback that fails after receiving an actual acceptance and executing the
  fabricated adapter still cannot cause resubmission. Adapter exceptions remain
  unknown and repeated receipt reads do not execute the adapter again.
- Losing volatile server history does not turn a missing receipt into proof of
  non-dispatch. Both the replacement fixture server and the durable journal keep
  mutations blocked. This deliberately recreated fixture is not authorization to
  restart, rebind or discard a real host's history.
- An adapter's `verified` result remains an assertion: the journal stays
  unresolved, `independent_evidence` and `live_qualified` remain false, and a new
  mutation is blocked.
- Each zone, household, save-slot and save-GUID mismatch is refused before the
  adapter callback by an explicitly fabricated owner-admission policy. A changed
  current process epoch is similarly refused. A wrong handshake process epoch or
  session key never reaches application admission. Changing an existing ID's
  immutable context is rejected locally before another socket exchange.

The context comparison is a **test host policy**, not a context-verification
feature supplied by the transport. The cryptographic exchange proves possession
of the deliberately public test key; it does not establish game process identity
or provide production key provisioning.

## Runtime and cleanup boundaries

Two optional cases run the actual companion server under a separate Python 3.7
interpreter, while the durable journal/client runs under the supported current
CLI interpreter. The child is invoked with `-B -S -s`; the fixture command path
exits before importing current-only CLI modules. The child asserts that it really
is Python 3.7. There is no emulation of its syntax or socket behavior.

Every fixture uses an ephemeral loopback port, a temporary journal and explicit
bounded cleanup. Thread events and command replies coordinate admission, dispatch
and fault release. Reader/client exceptions are captured and asserted, faulted
server completion is awaited, server shutdown must report success, and owned
subprocess readers must join. A subprocess that cannot stop normally is terminated
or killed only as cleanup and the test still fails. No unrelated process is
signaled. The fault's underlying timeout/EOF/reset category is asserted rather
than accepting any error merely because the journal reports uncertainty.

## Run

From `tooling/game-cli`, using Python 3.11 or newer:

```sh
PYTHONPATH=src python -B -m unittest discover -s tests -p test_companion_recovery.py -v
```

The two separate-runtime tests skip unless the optional interpreter is selected:

```sh
FOUNDRY_COMPANION_PYTHON37=/absolute/path/to/python3.7 \
  PYTHONPATH=src python -B -m unittest discover -s tests -p test_companion_recovery.py -v
```

Run the full current-interpreter suite with the same optional environment variable:

```sh
PYTHONPATH=src python -B -m unittest discover -s tests -v
```

On 2026-11-04, all 14 recovery cases passed in the Linux cloud workspace, including
both actual Python 3.7.17 server cases. These are source integration regressions,
not game qualification, a production security audit, or Windows runtime evidence.
They exercise ordinary durable file publication and journal reopening, not power
loss, disk rollback, a malicious filesystem, live-host admission or trusted
independent game readback. Existing journal durability/security limitations in
[COMPANION_JOURNAL.md](COMPANION_JOURNAL.md) still apply.
