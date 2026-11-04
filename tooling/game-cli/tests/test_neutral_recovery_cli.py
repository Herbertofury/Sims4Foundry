"""Argument routing only. No process discovery, profile access or EA calls."""
import unittest
from unittest.mock import patch

from sims4foundry_cli import cli


class RecoveryCliTests(unittest.TestCase):
    def test_reconcile_routes_exact_sessions_once(self):
        args = cli.parser().parse_args([
            'game-launch-reconcile-not-dispatched',
            '--profile-session', 'explicit-profile-session', '--session', 'explicit-launch-session'])
        expected = {'ok': True, 'outcome': 'resolved_before_dispatch'}
        with patch.object(cli.neutral_launch_recovery, 'reconcile_not_dispatched', return_value=expected) as call:
            self.assertIs(expected, cli.run(args))
        call.assert_called_once_with('explicit-profile-session', 'explicit-launch-session')

    def test_no_automatic_recovery_command_is_added(self):
        commands = cli.parser()._subparsers._group_actions[0].choices
        self.assertNotIn('game-launch-force-release', commands)
        self.assertNotIn('game-launch-retry', commands)
        self.assertIn('startup-probe-build', commands)
        self.assertIn('game-launch-reconcile-not-dispatched', commands)


if __name__ == '__main__':
    unittest.main()
