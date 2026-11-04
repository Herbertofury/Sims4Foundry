"""Fabricated IP Helper buffers; native calls require an owned hosted-CI fixture."""

import ctypes
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import queue
import socket
import struct
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from sims4foundry_cli import loopback_owner as owner
from sims4foundry_cli.errors import FoundryError


PID, SERVER_PORT, CLIENT_PORT = 12345, 0x1234, 0xABCD
LOOPBACK = int.from_bytes(b"\x7f\x00\x00\x01", "little")


def network_port(port):
    return int.from_bytes(port.to_bytes(2, "big"), "little")


def row(*, pid=PID, local_addr=LOOPBACK, remote_addr=LOOPBACK,
        local_port=SERVER_PORT, remote_port=CLIENT_PORT, state=5,
        local_raw=None, remote_raw=None):
    return struct.pack(
        "<6I", state, local_addr,
        network_port(local_port) if local_raw is None else local_raw,
        remote_addr,
        network_port(remote_port) if remote_raw is None else remote_raw,
        pid,
    )


def table(*rows, count=None, tail=b""):
    return struct.pack("<I", len(rows) if count is None else count) + b"".join(rows) + tail


class FakeApi:
    """Each step is (status, output_size, bytes_to_write), or an exception."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []
        self.buffers = []

    def __call__(self, buffer, size_ptr, order, family, table_class, reserved):
        size = ctypes.cast(size_ptr, ctypes.POINTER(ctypes.c_uint32))
        self.calls.append((buffer is None, size.contents.value,
                           order, family, table_class, reserved))
        if buffer is not None:
            self.buffers.append(buffer)
        if not self.steps:
            raise AssertionError("The observation exceeded its scripted read budget.")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        code, reported, payload = step
        if payload:
            assert buffer is not None and len(payload) <= ctypes.sizeof(buffer)
            ctypes.memmove(buffer, payload, len(payload))
        size.contents.value = reported
        return code


def api_for(payload, *, allocation=None, reported=None):
    allocation = len(payload) if allocation is None else allocation
    reported = len(payload) if reported is None else reported
    return FakeApi((122, allocation, b""), (0, reported, payload))


def observe(api, *, pid=PID, server_port=SERVER_PORT, client_port=CLIENT_PORT):
    return owner._observe_connection_owner_with_api(
        pid, server_port, client_port, get_extended_tcp_table=api
    )


class LoopbackOwnerTests(unittest.TestCase):
    def assert_refused(self, api, code, **request):
        with self.assertRaises(FoundryError) as caught:
            observe(api, **request)
        self.assertEqual(code, caught.exception.code)
        self.assertLessEqual(len(api.calls), owner.MAX_TABLE_CALLS)
        for buffer in api.buffers:
            self.assertEqual(bytes(ctypes.sizeof(buffer)), buffer.raw)
        return caught.exception

    def test_windows_abi_uses_dword_fields_and_native_alignment(self):
        self.assertEqual(4, ctypes.sizeof(owner._DWORD))
        self.assertEqual(24, owner._ROW_STRIDE)
        self.assertEqual(4, owner._ROWS_OFFSET)
        self.assertEqual(28, ctypes.sizeof(owner._TcpTableOwnerPid))
        self.assertEqual(20, owner._TcpRowOwnerPid.dwOwningPid.offset)

    def test_exact_server_side_connection(self):
        api = api_for(table(row()))
        result = observe(api)
        self.assertEqual(owner.ConnectionOwnerObservation(PID, SERVER_PORT, CLIENT_PORT), result)
        self.assertEqual({"pid", "server_port", "client_port"}, set(vars(result)))
        with self.assertRaises(FrozenInstanceError):
            result.pid = 987
        self.assertEqual([(True, 0, 0, 2, 4, 0), (False, 28, 0, 2, 4, 0)], api.calls)
        self.assertEqual(bytes(28), api.buffers[0].raw)

    def test_client_reverse_row_does_not_authenticate_server(self):
        self.assert_refused(
            api_for(table(row(local_port=CLIENT_PORT, remote_port=SERVER_PORT))),
            "connection_owner_missing",
        )

    def test_client_and_unrelated_rows_do_not_mask_correct_server(self):
        payload = table(
            row(local_port=CLIENT_PORT, remote_port=SERVER_PORT, pid=999),
            row(local_addr=0x04030201, remote_addr=0x08070605, pid=222),
            row(),
        )
        self.assertEqual(PID, observe(api_for(payload)).pid)

    def test_no_loopback_ranges_wildcards_nonlocal_or_hostname_equivalents(self):
        for address in (0, 0x0200007F, 0x0100000A, 0x7F000001, 0xFFFFFFFF):
            for field in ("local_addr", "remote_addr"):
                with self.subTest(address=address, field=field):
                    self.assert_refused(api_for(table(row(**{field: address}))),
                                        "connection_owner_missing")

    def test_ports_are_exact_network_order_low_words(self):
        # The DWORD itself is native endian; the contained low WORD is network endian.
        self.assertEqual(PID, observe(api_for(table(row(
            local_raw=0xDEAD0000 | network_port(SERVER_PORT),
            remote_raw=0xBEEF0000 | network_port(CLIENT_PORT),
        )))).pid)
        for fields in (
            {"local_raw": SERVER_PORT}, {"remote_raw": CLIENT_PORT},
            {"local_port": SERVER_PORT + 1}, {"remote_port": CLIENT_PORT - 1},
            {"local_port": 0}, {"remote_port": 0},
            {"local_raw": SERVER_PORT << 16}, {"remote_raw": CLIENT_PORT << 16},
        ):
            with self.subTest(fields=fields):
                self.assert_refused(api_for(table(row(**fields))), "connection_owner_missing")

    def test_boundary_port_numbers(self):
        for server, client in ((1, 65535), (65535, 1), (256, 255), (255, 256)):
            with self.subTest(server=server, client=client):
                result = observe(api_for(table(row(local_port=server, remote_port=client))),
                                 server_port=server, client_port=client)
                self.assertEqual((server, client), (result.server_port, result.client_port))

    def test_only_established_exact_row_is_accepted(self):
        for state in (1, 3, 4, 6, 7, 8, 9, 10, 11, 12):
            with self.subTest(state=state):
                self.assert_refused(api_for(table(row(state=state))),
                                    "connection_owner_not_established")

    def test_wrong_table_or_unknown_state_refuses_even_after_matching_row(self):
        for state in (0, 2, 13, 0xFFFFFFFF):
            with self.subTest(state=state):
                self.assert_refused(api_for(table(row(), row(state=state, local_port=1))),
                                    "connection_owner_malformed")

    def test_wrong_pid_is_not_disclosed_or_replaced(self):
        for pid in (0, PID + 1, 0xFFFFFFFF):
            with self.subTest(pid=pid):
                error = self.assert_refused(api_for(table(row(pid=pid))),
                                           "connection_owner_mismatch")
                self.assertEqual({}, error.details)

    def test_same_pid_different_pid_or_other_state_duplicates_are_ambiguous(self):
        for duplicate in (row(), row(pid=PID + 1), row(state=11)):
            with self.subTest(duplicate=duplicate):
                self.assert_refused(api_for(table(row(), duplicate)), "connection_owner_ambiguous")

    def test_empty_table_and_wrong_tuple_refuse(self):
        self.assert_refused(api_for(table()), "connection_owner_missing")
        self.assert_refused(api_for(table(row(local_port=123))), "connection_owner_missing")

    def test_missing_header_or_declared_row_refuses(self):
        for payload in (b"", b"\x01", b"\x01\x00\x00", table(count=1), table(row(), count=2)):
            with self.subTest(payload=payload):
                api = api_for(payload, allocation=max(4, len(payload)))
                self.assert_refused(api, "connection_owner_incomplete")

    def test_truncated_last_row_cannot_be_ignored_after_a_match(self):
        self.assert_refused(api_for(table(row(), count=2, tail=bytes(23))),
                            "connection_owner_incomplete")

    def test_success_size_must_not_exceed_allocation_or_omit_declared_rows(self):
        for reported in (0, 3, 27, 29):
            with self.subTest(reported=reported):
                self.assert_refused(api_for(table(row()), reported=reported),
                                    "connection_owner_incomplete")

    def test_estimated_success_size_can_keep_unused_capacity(self):
        # pdwSize is an estimate, not a guarantee of an exact final byte count.
        self.assertEqual(PID, observe(api_for(table(row()), allocation=128, reported=128)).pid)
        self.assertEqual(PID, observe(api_for(table(row()), allocation=128, reported=28)).pid)

    def test_count_is_checked_before_iteration(self):
        for count in (owner.MAX_TABLE_ROWS + 1, 0xFFFFFFFF):
            with self.subTest(count=count):
                self.assert_refused(api_for(table(count=count)), "connection_owner_limit")

    def test_exact_row_limit_accepts_and_scans_last_row(self):
        unrelated = row(local_port=1)
        payload = table(*([unrelated] * (owner.MAX_TABLE_ROWS - 1) + [row()]))
        self.assertEqual(PID, observe(api_for(payload)).pid)

    def test_probe_size_is_bounded_before_allocation(self):
        for reported, error in ((0, "connection_owner_incomplete"),
                                (3, "connection_owner_incomplete"),
                                (owner.MAX_TABLE_BYTES + 1, "connection_owner_limit"),
                                (0xFFFFFFFF, "connection_owner_limit")):
            with self.subTest(reported=reported), patch.object(
                owner.ctypes, "create_string_buffer", side_effect=AssertionError("No allocation")
            ) as allocate:
                api = FakeApi((122, reported, b""))
                self.assert_refused(api, error)
                allocate.assert_not_called()

    def test_exact_allocation_limit_is_allowed(self):
        api = api_for(table(row()), allocation=owner.MAX_TABLE_BYTES,
                      reported=owner.MAX_TABLE_BYTES)
        self.assertEqual(PID, observe(api).pid)
        self.assertEqual(owner.MAX_TABLE_BYTES, ctypes.sizeof(api.buffers[0]))
        self.assertFalse(any(api.buffers[0].raw))

    def test_one_growth_retry_is_bounded_and_old_buffer_is_erased(self):
        payload = table(row())
        api = FakeApi((122, 4, b""), (122, 28, b"abcd"), (0, 28, payload))
        self.assertEqual(PID, observe(api).pid)
        self.assertEqual([0, 4, 28], [call[1] for call in api.calls])
        self.assertTrue(all(not any(buffer.raw) for buffer in api.buffers))

    def test_continuous_growth_stops_without_a_fourth_call(self):
        api = FakeApi((122, 4, b""), (122, 28, b"abcd"), (122, 52, table(row())))
        self.assert_refused(api, "connection_owner_incomplete")
        self.assertEqual(3, len(api.calls))

    def test_nonprogressing_growth_or_growth_past_limit_refuses(self):
        for reported, error in ((4, "connection_owner_malformed"),
                                (3, "connection_owner_incomplete"),
                                (owner.MAX_TABLE_BYTES + 1, "connection_owner_limit")):
            with self.subTest(reported=reported):
                self.assert_refused(FakeApi((122, 4, b""), (122, reported, b"abcd")), error)

    def test_probe_success_without_a_table_is_not_evidence(self):
        self.assert_refused(FakeApi((0, 28, b"")), "connection_owner_incomplete")

    def test_denied_status_stops_without_fallback_or_retry(self):
        for steps in (((5, 0, b""),), ((122, 28, b""), (5, 28, table(row())))):
            with self.subTest(steps=steps):
                api = FakeApi(*steps)
                self.assert_refused(api, "connection_owner_denied")
                self.assertEqual(len(steps), len(api.calls))

    def test_other_error_status_stops_without_retry(self):
        for status in (1, 50, 87, 299, 0xFFFFFFFF):
            for at_read in (False, True):
                with self.subTest(status=status, at_read=at_read):
                    steps = [(status, 28, table(row()) if at_read else b"")]
                    if at_read:
                        steps.insert(0, (122, 28, b""))
                    api = FakeApi(*steps)
                    error = self.assert_refused(api, "connection_owner_unavailable")
                    self.assertEqual({"winerror": status}, error.details)
                    self.assertEqual(len(steps), len(api.calls))

    def test_malformed_status_is_not_treated_as_success(self):
        for status in (None, False, 0.0, "0", -1, 0x100000000):
            with self.subTest(status=status):
                self.assert_refused(FakeApi((status, 28, b"")), "connection_owner_malformed")

    def test_native_exception_is_sanitized_and_buffer_erased(self):
        api = FakeApi((122, 28, b""), OSError("unrelated private endpoint"))
        error = self.assert_refused(api, "connection_owner_unavailable")
        self.assertNotIn("private endpoint", str(error))

    def test_buffer_erased_when_matching_raises_unexpected_exception(self):
        api = api_for(table(row()))
        with patch.object(owner, "_match_table", side_effect=RuntimeError("fixture")):
            with self.assertRaises(RuntimeError):
                observe(api)
        self.assertFalse(any(api.buffers[0].raw))

    def test_allocation_failure_is_a_refusal(self):
        api = FakeApi((122, 28, b""))
        with patch.object(owner.ctypes, "create_string_buffer", side_effect=MemoryError):
            self.assert_refused(api, "connection_owner_unavailable")
        self.assertEqual(1, len(api.calls))

    def test_invalid_requests_never_load_or_call_api(self):
        cases = [{"pid": value} for value in (None, True, False, 0, -1, 1.0, "123", 0x100000000)]
        for field in ("server_port", "client_port"):
            cases.extend({field: value} for value in (None, True, False, 0, -1, 65536, 1.0, "123"))
        cases.append({"client_port": SERVER_PORT})
        for case in cases:
            with self.subTest(case=case), patch.object(owner, "_load_native_api") as load:
                api = FakeApi()
                self.assert_refused(api, "invalid_connection_owner_request", **case)
                self.assertEqual([], api.calls)
                values = dict(pid=PID, server_port=SERVER_PORT, client_port=CLIENT_PORT)
                values.update(case)
                with self.assertRaises(FoundryError):
                    owner.observe_connection_owner(values["pid"], values["server_port"], values["client_port"])
                load.assert_not_called()

    def test_non_windows_public_call_refuses_without_loading(self):
        with patch.object(owner.os, "name", "posix"), patch.object(owner, "_load_native_api") as load:
            with self.assertRaises(FoundryError) as caught:
                owner.observe_connection_owner(PID, SERVER_PORT, CLIENT_PORT)
            self.assertEqual("connection_owner_unavailable", caught.exception.code)
            load.assert_not_called()

    def test_public_api_has_no_alternate_owner_source_parameter(self):
        with self.assertRaises(TypeError):
            owner.observe_connection_owner(PID, SERVER_PORT, CLIENT_PORT, get_extended_tcp_table=FakeApi())

    def test_native_loader_is_lazy_system32_only_and_has_exact_signature(self):
        class FakeDll:
            GetExtendedTcpTable = FakeApi((122, 28, b""))

        with patch.object(owner.ctypes, "WinDLL", create=True, return_value=FakeDll()) as load:
            api = owner._load_native_api()
            load.assert_called_once_with("iphlpapi.dll", winmode=0x800)
            target = FakeDll.GetExtendedTcpTable
            self.assertIs(owner._DWORD, target.restype)
            self.assertEqual([ctypes.c_void_p, ctypes.POINTER(owner._DWORD), ctypes.c_int32,
                              owner._DWORD, ctypes.c_int32, owner._DWORD], target.argtypes)
            size = owner._DWORD(0)
            self.assertEqual(122, api(None, ctypes.byref(size), 0, 2, 4, 0))

    def test_unavailable_native_library_is_a_refusal_without_fallback(self):
        with patch.object(owner.ctypes, "WinDLL", create=True, side_effect=OSError("not available")) as load:
            with self.assertRaises(FoundryError) as caught:
                owner._load_native_api()
            self.assertEqual("connection_owner_unavailable", caught.exception.code)
            self.assertEqual(1, load.call_count)

    def test_import_performs_no_native_observation(self):
        # Isolate the import so other tests can retain this module's class identities.
        script = """
import ctypes, sys
sys.path.insert(0, sys.argv[1])
def forbid_native(*args, **kwargs):
    raise AssertionError('No native calls on import')
ctypes.WinDLL = forbid_native
import sims4foundry_cli.loopback_owner
"""
        subprocess.run(
            [sys.executable, "-I", "-c", script, str(Path(owner.__file__).parent.parent)],
            check=True, capture_output=True, timeout=10,
        )


_NATIVE_CI = (
    sys.platform == "win32"
    and os.environ.get("GITHUB_ACTIONS") == "true"
    and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    and os.environ.get("FOUNDRY_LOOPBACK_OWNER_NATIVE_FIXTURE") == "1"
)


@unittest.skipUnless(_NATIVE_CI, "Requires opt-in newly owned GitHub-hosted Windows fixture")
class HostedWindowsConnectionOwnerFixture(unittest.TestCase):
    def test_owned_server_process_connected_pair(self):
        # This test does not discover or contact a user/game process. The PID is
        # returned by our own Popen, whose Windows process handle stays retained.
        script = """
import socket, sys
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    listener.settimeout(10)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    print(listener.getsockname()[1], flush=True)
    connection, peer = listener.accept()
    with connection:
        print('accepted', flush=True)
        sys.stdin.buffer.read(1)
"""
        process = subprocess.Popen(
            [sys.executable, "-I", "-u", "-c", script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        lines = queue.Queue()

        def read_lines():
            for line in iter(process.stdout.readline, b""):
                lines.put(line.strip())

        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        try:
            port = int(lines.get(timeout=10))
            self.assertIsNone(process.poll())
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                connection.settimeout(10)
                connection.connect(("127.0.0.1", port))
                self.assertEqual(b"accepted", lines.get(timeout=10))
                server_address, server_port = connection.getpeername()
                client_address, client_port = connection.getsockname()
                self.assertEqual(("127.0.0.1", "127.0.0.1"), (server_address, client_address))
                self.assertIsNone(process.poll())
                observed = owner.observe_connection_owner(process.pid, server_port, client_port)
                self.assertIsNone(process.poll())
                self.assertEqual(owner.ConnectionOwnerObservation(process.pid, server_port, client_port), observed)
        finally:
            if process.poll() is None:
                try:
                    process.stdin.write(b"x")
                    process.stdin.flush()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    # Only the exact disposable child created by this fixture.
                    process.kill()
                    process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
            reader.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
