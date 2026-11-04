# Inspect a startup diagnostic record

`startup-probe-report` reads explicit local files. It does not contact a game,
install a probe, launch or discover a process, inspect a profile, or authorize
commands. Use it after a separately authorized diagnostic run or on explicitly
selected materialized evidence files.

```text
foundry-game startup-probe-report --report PROBE_REPORT.json --manifest PROBE.ts4script.manifest.json --run-id RUN_ID_HEX32 --expected-pid INDEPENDENTLY_EXPECTED_PID --archive PROBE.ts4script
```

Use the original 32 lowercase hexadecimal run ID and a PID obtained independently
of the report. `--archive` is optional. File-path selection does not prove where
or when a report was originally written.

The reader checks the current producer's exact schemas, bounded fields and
published result statuses. It compares the report and build manifest's run ID,
wait configuration and the expected PID. Optional archive checking compares
only byte count and SHA-256 against the unauthenticated manifest. It does not
import, extract, unmarshal, execute or authenticate any archive content.

A correlated report has status `self-report-correlated-not-qualified`. Missing
explicit evidence is `unknown-missing-evidence`; malformed, conflicting, linked,
nonregular, oversized or changing inputs are refused. Access failures stop the
read without another route. Reports are capped at 16 KiB and manifests at 64 KiB.

The returned `self_report` remains untrusted in-process data, including its
thread, loader, cleanup and PID statements. A matching Thread is not proof that
startup occurred outside a tick. A matching PID is not a process lifetime or
ownership proof. Cleanup is only a serialization-time snapshot. Consistent or
forged files never grant profile access or enable control: loader/live/game
qualification and command authorization stay false, and owner/outside-tick
authority stays unknown.

Build the separate credential-free probe with
[STARTUP_PROBE_BUILD.md](STARTUP_PROBE_BUILD.md). This checker makes that evidence
inspectable; it does not supply the missing production admission or game test.
