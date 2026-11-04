# Standalone process-exit observation

`foundry-game observe-exit` verifies termination of one exact previously observed
Windows process lifetime. It has no Apex dependency and sends no input, quit,
save, discard or force-termination request. Use an identity produced by the
independently authorized `inspect --output` command:

```text
foundry-game observe-exit --identity CURRENT_IDENTITY.json --seconds 30 --output NEW_EXIT_OBSERVATION.json
```

Zero seconds performs one immediate observation. The maximum requested wait is
60 seconds. A timeout produces `outcome: unresolved`, `process_exit_verified:
false` and CLI exit code 3. It never repeats a quit request or opens a replacement
process. An already missing or inaccessible PID is an error, not exit proof.
The output path must be new and outside any game/profile directory.

The implementation rechecks the explicitly pinned executable using bounded
file reads. It opens one non-inheritable query/synchronize-only process handle,
checks image path and creation FILETIME, and waits only on that retained handle.
Path matching allows ASCII case differences only; non-ASCII spelling must match
exactly, rather than using Unicode casefold expansions that can merge distinct
Windows filenames. This may conservatively refuse equivalent Unicode case variants.
PID reuse, image mismatch, access denial, unexpected wait status or unverified
handle cleanup cannot produce success. It does not enumerate processes, use CIM,
inspect a profile, change security settings or retry an access failure by another
route. This command is not permission to work around an existing access denial.

Successful termination observation is not proof of a normal exit. A crash also
ends a process. `normal_exit_verified` and `save_verified` therefore remain false.
The task that requested an exit must separately establish the chosen save/discard
policy, exact slot/household, native confirmation and any required reload checks.
Window disappearance and a returned native quit-menu request are insufficient.

The finite native waits are at most 250 ms each and share a monotonic deadline.
Windows scheduling, sleep and filesystem/native calls are not hard real-time
guarantees. No game APIs or hooks are involved.

## Verification and sources

Author tests cover fake retained handles, deadline expiry, PID/image change,
denials, result types, cleanup failure, target aliasing, CLI status and output
collision. A separate GitHub-hosted Windows fixture observes only a newly owned
Python child and lets that child exit normally through its own stdin contract.
Neither test is Sims qualification or actual normal game-exit validation.

Microsoft API contracts consulted 2026-11-04:

- [WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject): finite waits and process-handle signalling.
- [GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes): creation time and undefined pre-exit exit time.
- [QueryFullProcessImageNameW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-queryfullprocessimagenamew): query-only executable-path observation.
