"""Read-only report routing with no runtime activation or native calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sims4foundry_cli import cli, startup_probe_report


class ProbeReportCliTests(unittest.TestCase):
    def test_exact_files_and_expectations_are_routed_once(self):
        args = cli.parser().parse_args(['startup-probe-report', '--report', 'report.json',
                                       '--manifest', 'manifest.json', '--run-id', 'a'*32,
                                       '--expected-pid', '123', '--archive', 'probe.ts4script'])
        expected = {'ok': True, 'game_qualified': False}
        with patch.object(startup_probe_report, 'inspect_startup_probe', return_value=expected) as call:
            self.assertIs(expected, cli.run(args))
        call.assert_called_once_with('report.json', 'manifest.json', expected_run_id='a'*32,
                                     expected_pid=123, archive_path='probe.ts4script')

    def test_missing_files_are_unknown_not_game_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = cli.parser().parse_args(['startup-probe-report', '--report', str(root/'report.json'),
                                           '--manifest', str(root/'manifest.json'), '--run-id', 'a'*32,
                                           '--expected-pid', '123'])
            result = cli.run(args)
            self.assertEqual('unknown-missing-evidence', result['status'])
            self.assertFalse(result['game_qualified'])
            self.assertFalse(result['commands_authorized'])
            self.assertFalse(result['report_available'])
            self.assertFalse(result['manifest_available'])
            self.assertEqual([], list(root.iterdir()))


if __name__ == '__main__':
    unittest.main()
