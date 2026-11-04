"""Read-only CLI inspection of fabricated local journal history."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sims4foundry_cli.cli import main
from sims4foundry_cli import companion_journal as ledger, companion_protocol as wire
from sims4foundry_cli.companion_transport import ExpectedContext, Submission


def item(number=1, observe=False):
    return Submission(wire.Request(f"{number:032x}", "a" * 32,
        wire.ProcessEpoch(1234, 133000000000000000),
        wire.Operation.OBSERVE if observe else wire.Operation.SET_SPEED,
        wire.ObserveArguments() if observe else wire.SetSpeedArguments(2)),
        5.0, None if observe else ExpectedContext("10", "20", 4, "99"))


class JournalCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ledger"
        self.journal = ledger.create_journal(self.root)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def command(self, *extra, directory=None):
        out = io.StringIO()
        # None of these mutation/exchange seams may be reached from status.
        with patch.object(ledger, "create_journal", side_effect=AssertionError("created")), \
             patch.object(ledger.Journal, "submit_once", side_effect=AssertionError("submitted")), \
             patch.object(ledger.Journal, "reconcile", side_effect=AssertionError("exchanged")), \
             patch("socket.socket", side_effect=AssertionError("networked")), redirect_stdout(out):
            code = main(["companion", "journal-status", "--directory",
                         str(directory or self.root), *extra])
        return code, json.loads(out.getvalue())

    def test_empty_status_preserves_files_and_is_explicitly_local(self):
        before = self.snapshot()
        code, result = self.command()
        self.assertEqual(0, code)
        self.assertTrue(result["ok"])
        self.assertTrue(result["read_only"])
        self.assertEqual("local_companion_journal", result["scope"])
        self.assertEqual([], result["entries"])
        for field in ("network_accessed", "game_accessed", "independent_evidence", "live_qualified"):
            self.assertIs(result[field], False)
        self.assertEqual(before, self.snapshot())

    def test_unresolved_intent_is_reported_without_changing_it(self):
        self.journal.record_intent(item())
        before = self.snapshot()
        code, result = self.command()
        self.assertEqual(0, code)  # Successful history read, not a game operation.
        status = result["entries"][0]
        self.assertEqual("intent_recorded", status["state"])
        self.assertEqual("not_attempted", status["transmission"])
        self.assertTrue(status["unresolved"])
        self.assertFalse(status["independent_evidence"])
        self.assertFalse(status["live_qualified"])
        self.assertEqual(before, self.snapshot())

    def test_exact_request_filter_never_creates_missing_request(self):
        self.journal.record_intent(item())
        self.journal.record_intent(item(2, observe=True))
        before = self.snapshot()
        code, result = self.command("--request-id", f"{2:032x}")
        self.assertEqual(0, code)
        self.assertEqual([f"{2:032x}"], [row["request_id"] for row in result["entries"]])
        code, result = self.command("--request-id", "f" * 32)
        self.assertEqual(2, code)
        self.assertEqual("intent_missing", result["code"])
        self.assertEqual(before, self.snapshot())

    def test_missing_directory_is_not_initialized(self):
        missing = Path(self.temp.name) / "absent"
        code, result = self.command(directory=missing)
        self.assertEqual(2, code)
        self.assertEqual("journal_corrupt", result["code"])
        self.assertFalse(missing.exists())

    def test_empty_filter_is_not_treated_as_read_all(self):
        self.journal.record_intent(item())
        before = self.snapshot()
        code, result = self.command("--request-id", "")
        self.assertEqual(2, code)
        self.assertEqual("intent_missing", result["code"])
        self.assertNotIn("entries", result)
        self.assertEqual(before, self.snapshot())

    def test_missing_lock_is_not_recreated(self):
        (self.root / "operation.lock").unlink()
        before = self.snapshot()
        code, result = self.command()
        self.assertEqual(2, code)
        self.assertEqual("journal_corrupt", result["code"])
        self.assertEqual(before, self.snapshot())

    def test_corrupt_history_is_not_repaired(self):
        self.journal.record_intent(item())
        (self.root / "head.json").write_text("{")
        before = self.snapshot()
        code, result = self.command()
        self.assertEqual(2, code)
        self.assertEqual("journal_corrupt", result["code"])
        self.assertEqual(before, self.snapshot())

    def test_busy_journal_is_not_entered_or_modified(self):
        before = self.snapshot()
        with self.journal._locked():
            code, result = self.command()
        self.assertEqual(2, code)
        self.assertEqual("busy", result["code"])
        self.assertEqual(before, self.snapshot())

    def test_mutation_or_network_flags_are_not_cli_routes(self):
        before = self.snapshot()
        for args in (["create"], ["submit"], ["reconcile"], ["reset"],
                     ["journal-status", "--execute"], ["journal-status", "--port", "1234"],
                     ["journal-status", "--authorize", "true"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(["companion", *args, "--directory", str(self.root)])
            self.assertEqual(2, caught.exception.code)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
