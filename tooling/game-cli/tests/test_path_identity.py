"""Filename spelling regressions; no Windows or game access."""
import ast
from pathlib import Path, PureWindowsPath
import unittest

from sims4foundry_cli import discovery, launch, path_identity, windows
from sims4foundry_cli.errors import FoundryError
from test_process_lease import identity


class PathIdentityTests(unittest.TestCase):
    def test_ascii_case_and_separator(self):
        self.assertTrue(path_identity.same_windows_path(
            r'C:\Games\TS4_x64.exe', 'c:/games/ts4_X64.EXE'))

    def test_parent_segments_are_not_newly_normalized_for_processes(self):
        first, second = r'C:\Games\Folder\..\TS4_x64.exe', r'C:\Games\TS4_x64.exe'
        self.assertFalse(path_identity.same_windows_path(first, second))
        # The historical launch-log matcher already used lexical normpath.
        self.assertTrue(launch._same_image(first, second))

    def test_non_ascii_must_match_exactly(self):
        self.assertTrue(path_identity.same_windows_path(r'C:\straße\TS4_x64.exe', r'c:\straße\ts4_X64.EXE'))
        for first, second in (('strasse', 'straße'), ('file', 'ﬁle'), ('same', 'ſame'),
                              ('İ', 'i\u0307'), ('É', 'é')):
            with self.subTest(first=first, second=second):
                self.assertFalse(path_identity.same_windows_path(
                    'C:\\' + first + '\\TS4_x64.exe', 'C:\\' + second + '\\TS4_x64.exe'))

    def test_invalid_types_or_nul_are_not_paths(self):
        for value in (None, 5, Path('a'), True, '', 'C:\x00file'):
            with self.subTest(value=value):
                self.assertFalse(path_identity.same_windows_path(value, value))

    def test_launch_account_evidence_rejects_casefold_and_lower_expansion(self):
        for expected, observed in ((r'C:\strasse\TS4_Launcher_x64.exe', r'C:\straße\TS4_Launcher_x64.exe'),
                                   ('C:\\İ\\TS4_Launcher_x64.exe', 'C:\\i\u0307\\TS4_Launcher_x64.exe')):
            self.assertFalse(launch._same_image(expected, observed))

    def test_identity_confusable_game_basename_is_rejected(self):
        for name in ('Tſ4_x64.exe', 'tſ4_x64.exe'):
            with self.subTest(name=name), self.assertRaises(FoundryError):
                windows.validate_identity(dict(identity(), executable='C:\\Game\\' + name))

    def test_discovery_observation_rejects_wrong_casefold_path(self):
        expected = r'C:\strasse\TS4_x64.exe'
        observed = dict(identity(), executable=r'C:\straße\TS4_x64.exe')
        with self.assertRaises(FoundryError) as caught:
            discovery._validate_observation(observed, 42, PureWindowsPath(expected), 'a' * 64)
        self.assertEqual('wrong_process', caught.exception.code)

    def test_existing_native_path_bindings_use_conservative_comparator(self):
        # Guard the narrowly changed call sites without executing any backend.
        root = Path(windows.__file__).parent
        for filename in ('windows.py', 'discovery.py', 'native_input.py'):
            tree = ast.parse((root / filename).read_text())
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == 'same_windows_path']
            self.assertGreaterEqual(len(calls), 1, filename)


if __name__ == '__main__':
    unittest.main()
