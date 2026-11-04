# Exact connected-loopback ownership observation

`sims4foundry_cli.loopback_owner.observe_connection_owner(expected_pid,
server_port, client_port)` is a standalone, read-only Windows observation.
It has no Apex dependency. Importing it or starting the CLI does not load a
Windows DLL, query a table, open a connection, or enable a game command.

Despite the historical module/document name, **this observes a connected
server-side socket, not a listening port**. A listener PID does not establish
which process owns the socket carrying authentication. There is deliberately
no listener-only fallback.

## Caller requirements and result

The caller must already hold all of the following:

1. An independently established, retained target process lifetime, normally an
   exact Windows process handle acquired through the approved process-identity
   path. A numeric PID, a peer's PID claim, or a discovery file is insufficient.
2. The actual connected IPv4 TCP socket that will carry authentication. Check
   that both `getpeername()` and `getsockname()` addresses are exactly
   `127.0.0.1`. Do not substitute `localhost`, another `127/8` address, IPv6,
   wildcard binding, a configured port, or a port from an unrelated socket.
3. The server port from that socket's `getpeername()` and the client port from
   its `getsockname()`. Keep the socket open throughout observation and use.

The API accepts positive DWORD PIDs and distinct integer ports from 1 to 65535;
booleans, zero, coercible strings, and identical ports are refused before any
native call. Only one exact row may match:

- Local address `127.0.0.1`, local port `server_port`.
- Remote address `127.0.0.1`, remote port `client_port`.
- State `ESTABLISHED` and owning PID exactly `expected_pid`.

The inverse/client-side row cannot satisfy this check. An immutable
`ConnectionOwnerObservation(pid, server_port, client_port)` contains only the
requested tuple's successful observation. It is not a reusable authorization
token. There is no exported list of endpoints or discovered PID.

Before sending authentication material, verify the independently retained
process lifetime and observe the exact connection. Revalidate that same
lifetime around authentication and later admission, and retain the same
socket. Any refusal, closed/replaced socket, changed lifetime, uncertainty, or
unresolved operation must stop the dependent action. Do not reconnect and
reuse an earlier observation. The surrounding caller is responsible for its
process lease, expiry, authentication, operation scope, and admission policy;
this module supplies none of those policies and is not wired into a sender.

The observation supplements HMAC possession proof. HMAC alone does not prove
the operating-system PID, and this PID observation alone does not prove peer
key possession, application identity, loaded code, game readiness, or command
authorization. A snapshot cannot prevent a subsequent close, socket transfer,
process change, or a compromised process. It must never replace process
lifetime checks or be represented as authentication of the whole application.

## Native contract and bounds

The lazy native binding loads only System32's `iphlpapi.dll` and calls
`GetExtendedTcpTable` with `AF_INET` (2),
`TCP_TABLE_OWNER_PID_CONNECTIONS` (4), sorting disabled, and reserved zero.
This selects `MIB_TCPTABLE_OWNER_PID`, not listener, IPv6, module, or all-state
fallback tables. The initial null-buffer call must return
`ERROR_INSUFFICIENT_BUFFER`; its size is checked before allocation. A successful
fill's size can retain unused allocation capacity, but must contain its header
and every declared row and cannot exceed that allocation.
Sources: [GetExtendedTcpTable](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable)
and [TCP_TABLE_CLASS](https://learn.microsoft.com/en-us/windows/win32/api/iprtrmib/ne-iprtrmib-tcp_table_class).

The row has six 32-bit DWORD fields. `dwOwningPid` identifies the process that
issued the context bind; that is the specific observation obtained, not an
executable identity check. IPv4 addresses use the `in_addr` representation.
Local and remote ports contain a network-order 16-bit value in the low WORD;
the upper DWORD bits are not port bits. The implementation performs the
equivalent of the Microsoft sample's unsigned-short conversion plus `ntohs`,
rather than byte-swapping the entire DWORD. It requires little-endian Windows
and uses fixed-width ctypes fields, not platform-dependent `c_ulong`.
Sources: [MIB_TCPROW_OWNER_PID](https://learn.microsoft.com/en-us/windows/win32/api/tcpmib/ns-tcpmib-mib_tcprow_owner_pid),
[GetTcpTable example](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-gettcptable),
and [ntohs](https://learn.microsoft.com/en-us/windows/win32/api/winsock/nf-winsock-ntohs).

The table's first-row offset and row stride come from the ctypes ABI structures,
including their alignment, not a packed serialization assumption. The declared
row count is bounded and checked against the returned byte extent before
reading a row. Source:
[MIB_TCPTABLE_OWNER_PID](https://learn.microsoft.com/en-us/windows/win32/api/tcpmib/ns-tcpmib-mib_tcptable_owner_pid).

Hard limits per observation:

- At most 3 native calls: 1 size probe and at most 2 table reads.
- At most 256 KiB per allocation and 2 allocation attempts.
- At most 4,096 inspected rows, with the count checked before iteration.
- At most 1 resize after a genuine increasing size requirement.

The API does not support a PID/tuple filter: Windows necessarily returns other
IPv4 connection rows in the transient buffer. The function inspects only the
fields needed for matching, does not copy rows into Python records or create
an endpoint inventory, and returns no unrelated addresses, ports, or PIDs.
The entire allocated table is zeroed on success, refusal, exception, and resize.
No table is written to disk, logged, cached, or attached to error details.

Duplicate exact tuples, conflicting PIDs, non-established exact rows, wrong
addresses/ports/PID, invalid table states, truncated headers/rows, contradictory
sizes, oversize tables, and exhausted resize bounds refuse with `FoundryError`.
`ERROR_ACCESS_DENIED` refuses immediately without retry. Other native errors
also stop immediately; only their numeric error status may be reported.
The sole retry is the documented insufficient-buffer growth path. There is no
CIM, PowerShell, process enumeration, alternate transport, elevation, firewall
change, network configuration change, or security-setting modification.

## Verification scope

Run the fabricated tests on any supported Python host:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_loopback_owner.py -v
```

The private `_observe_connection_owner_with_api` seam accepts only fabricated
API behavior for unit tests. It is not a production owner-source override.
Coverage includes native ABI arguments/layout, exact tuple direction, address
and port decoding, duplicate/wrong-PID rows, malformed/partial tables, byte and
row limits, bounded resize races, denied/error returns, memory wiping, invalid
requests, and import-time inactivity.

One optional integration test is gated on Windows, `GITHUB_ACTIONS=true`,
`RUNNER_ENVIRONMENT=github-hosted`, and
`FOUNDRY_LOOPBACK_OWNER_NATIVE_FIXTURE=1`. Enable it only on a newly owned
GitHub-hosted Windows runner, never on a user's desktop or self-hosted runner.
The test creates its own exclusive loopback listener subprocess, retains the
`Popen` process lifetime, connects its own socket, waits for acceptance, derives
the actual tuple from that socket, and queries only that expected fixture PID.
Its exact child is stopped and its sockets closed during cleanup. The native
API still temporarily returns the bounded OS connection table as described
above. No game, user profile, installed application, or user-selected network
endpoint is inspected or contacted.

Fabricated tests are not Windows/game qualification. Until the owned Windows
fixture has passed, report the native binding as unverified on Windows. Even a
passing fixture does not qualify game authentication, live input, application
commands, or the surrounding process-lifetime policy.
