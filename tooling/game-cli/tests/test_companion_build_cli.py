"""CLI delegation only; compiler/archive tests live in test_companion_build."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from sims4foundry_cli import cli
from sims4foundry_cli.errors import FoundryError


class CompanionBuildCLITests(unittest.TestCase):
    def arguments(self):
        return ['companion-build', '--source', 'neutral-source',
                '--output', 'new.ts4script', '--python37', 'trusted-python',
                '--compiler-sha256', 'a' * 64]

    def test_explicit_arguments_forwarded_without_other_actions(self):
        result = {'ok': True, 'installed': False, 'live_qualified': False}
        with patch('sims4foundry_cli.companion_build.build_companion', return_value=result) as build:
            self.assertIs(result, cli.run(cli.parser().parse_args(self.arguments())))
        build.assert_called_once_with('neutral-source', 'new.ts4script',
                                      'trusted-python', 'a' * 64, None)

    def test_explicit_manifest_forwarded(self):
        with patch('sims4foundry_cli.companion_build.build_companion', return_value={'ok': True}) as build:
            cli.run(cli.parser().parse_args(self.arguments() + ['--manifest-output', 'new.json']))
        self.assertEqual('new.json', build.call_args.args[-1])

    def test_build_refusal_is_machine_readable_nonzero(self):
        with patch('sims4foundry_cli.companion_build.build_companion',
                   side_effect=FoundryError('wrong_compiler', 'Compiler pin did not match.')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(2, cli.main(self.arguments()))
        result = json.loads(output.getvalue())
        self.assertFalse(result['ok'])
        self.assertEqual('wrong_compiler', result['code'])

    def test_required_arguments_cannot_be_omitted(self):
        for option in ('--source', '--output', '--python37', '--compiler-sha256'):
            args = self.arguments()
            index = args.index(option)
            del args[index:index + 2]
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    cli.parser().parse_args(args)
                self.assertEqual(2, caught.exception.code)

    def test_no_install_execute_or_apex_option_exists(self):
        for option in ('--execute', '--install', '--apex', '--activate', '--qualified'):
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    cli.parser().parse_args(self.arguments() + [option])
                self.assertEqual(2, caught.exception.code)


if __name__ == '__main__':
    unittest.main()
