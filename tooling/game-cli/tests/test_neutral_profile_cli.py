import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sims4foundry_cli import mods


class ProfileCliWorkflow(unittest.TestCase):
    def test_profile_and_test_mod_workflow_preserves_both_profiles(self):
        base = str(Path(__file__).resolve().parents[1] / 'src')
        bootstrap = """
import os
import sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from sims4foundry_cli import neutral_testmods, neutral_profile, discovery
from sims4foundry_cli.cli import main

def no_replace(source, destination):
    if os.path.lexists(destination):
        raise FileExistsError(str(destination))
    os.rename(source, destination)

def forbidden_discovery(*args):
    raise AssertionError('No native process discovery in fixture')

neutral_profile._WINDOWS = True
neutral_profile._rename_directory = no_replace
neutral_testmods.os = SimpleNamespace(name='nt', scandir=os.scandir,
                                     fsync=os.fsync, rename=no_replace)
discovery._snapshot_image_pids = forbidden_discovery
neutral_testmods._no_game_candidates = lambda: None
neutral_profile._no_game_candidates = lambda: None
raise SystemExit(main(sys.argv[2:]))
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = root / 'The Sims 4'; profile.mkdir()
            (profile / 'save.dat').write_bytes(b'original user save')
            (profile / 'Options.ini').write_bytes(b'[options]\nfixture = 1\n')
            source = root / 'candidate'; source.mkdir()
            (source / 'candidate.package').write_bytes(b'DBPF mod candidate')
            manifest = root / 'inventory.json'
            manifest.write_text(json.dumps(mods.inventory(source)))
            profile_session = root / 'profile-session'
            mod_session = root / 'mod-session'
            def cli(*args):
                result = subprocess.run([sys.executable, '-c', bootstrap, base, *args],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(0, result.returncode, result.stderr + result.stdout)
                return json.loads(result.stdout)
            self.assertTrue(cli('test-profile', 'activate', '--profile', str(profile),
                                '--session', str(profile_session))['activated'])
            self.assertEqual('activation_recorded',
                             cli('test-profile', 'status', '--session', str(profile_session))['state'])
            self.assertTrue(cli('test-mods', 'install', '--manifest', str(manifest),
                                '--profile', str(profile), '--session', str(mod_session),
                                '--profile-session', str(profile_session))['installed'])
            (profile / 'saves' / 'test-save.dat').write_bytes(b'test result')
            retired = cli('test-mods', 'retire', '--session', str(mod_session))
            self.assertEqual(b'DBPF mod candidate',
                             (Path(retired['recovered']) / 'candidate.package').read_bytes())
            restored = cli('test-profile', 'restore', '--session', str(profile_session))
            self.assertEqual(b'original user save', (profile / 'save.dat').read_bytes())
            recovery = Path(restored['test_profile_preserved'])
            self.assertEqual(b'test result', (recovery / 'saves' / 'test-save.dat').read_bytes())
            self.assertEqual(b'DBPF mod candidate',
                             (recovery / Path(retired['recovered']).name / 'candidate.package').read_bytes())
            self.assertEqual('restored_recorded',
                             cli('test-profile', 'status', '--session', str(profile_session))['state'])


if __name__ == '__main__':
    unittest.main()
