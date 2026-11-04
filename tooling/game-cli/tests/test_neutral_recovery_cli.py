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

    def test_retirement_recovery_routes_the_stored_session_only(self):
        args = cli.parser().parse_args(['test-mods', 'recover-retirement', '--session', 'explicit-session'])
        expected = {'ok': True, 'retired': True}
        with patch.object(cli.neutral_testmod_recovery, 'recover_retirement', return_value=expected) as call:
            self.assertIs(expected, cli.run(args))
        call.assert_called_once_with('explicit-session')

    def test_retirement_recovery_does_not_accept_a_binding_override(self):
        from sims4foundry_cli.errors import FoundryError
        args = cli.parser().parse_args(['test-mods', 'recover-retirement', '--session', 'explicit-session',
                                       '--profile-session', 'different-profile'])
        with patch.object(cli.neutral_testmod_recovery, 'recover_retirement') as call:
            with self.assertRaises(FoundryError): cli.run(args)
        call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
