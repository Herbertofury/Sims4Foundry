import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from sims4foundry_cli import mods, neutral_profile, neutral_profile_lock


BOOTSTRAP = """
import os
import sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from sims4foundry_cli import discovery, neutral_profile, neutral_testmods
from sims4foundry_cli.cli import main

def no_replace(source, destination):
    if os.path.lexists(destination):
        raise FileExistsError(str(destination))
    os.rename(source, destination)

def forbidden_discovery(*args):
    raise AssertionError('No native process discovery in CLI fixture')

neutral_testmods.os = SimpleNamespace(name='nt', scandir=os.scandir,
                                     fsync=os.fsync, rename=no_replace)
discovery._snapshot_image_pids = forbidden_discovery
neutral_testmods._no_game_candidates = lambda: None
neutral_profile._WINDOWS = True
neutral_profile._rename_directory = no_replace
neutral_profile._no_game_candidates = lambda: None
raise SystemExit(main(sys.argv[2:]))
"""


class TestModCli(unittest.TestCase):
    def test_cli_installs_and_retires_temporary_mods(self):
        base = str(Path(__file__).resolve().parents[1] / 'src')
        bootstrap = BOOTSTRAP
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'; source.mkdir()
            (source / 'candidate.package').write_bytes(b'DBPF test payload')
            with zipfile.ZipFile(source / 'test.ts4script', 'w') as archive:
                archive.writestr('test.py', 'fixture')
            profile = root / 'The Sims 4'
            (profile / 'Mods').mkdir(parents=True)
            untouched = profile / 'Mods' / 'original.package'
            untouched.write_bytes(b'DBPF original')
            manifest = root / 'inventory.json'
            manifest.write_text(json.dumps(mods.inventory(source)))
            session = root / 'session'
            def cli(*args):
                result = subprocess.run([sys.executable, '-c', bootstrap, base, 'test-mods', *args],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(0, result.returncode, result.stderr + result.stdout)
                return json.loads(result.stdout)
            installed = cli('install', '--manifest', str(manifest), '--profile', str(profile),
                            '--session', str(session))
            self.assertEqual(2, installed['artifact_count'])
            folder = profile / 'Mods' / 'FoundryTest'
            self.assertEqual({'candidate.package', 'test.ts4script'}, {x.name for x in folder.iterdir()})
            self.assertEqual('install_recorded', cli('status', '--session', str(session))['state'])
            retired = cli('retire', '--session', str(session))
            self.assertEqual({'candidate.package', 'test.ts4script'},
                             {x.name for x in Path(retired['recovered']).iterdir()})
            self.assertEqual(b'DBPF original', untouched.read_bytes())
            self.assertFalse(folder.exists())

    def test_cli_install_and_retire_share_cross_process_profile_lock(self):
        base = str(Path(__file__).resolve().parents[1] / 'src')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = root / 'The Sims 4'
            (profile / 'Mods').mkdir(parents=True)
            source = root / 'source'
            source.mkdir()
            (source / 'candidate.package').write_bytes(b'DBPF fixture')
            manifest = root / 'inventory.json'
            manifest.write_text(json.dumps(mods.inventory(source)))
            session = root / 'session'
            def invoke(*args):
                result = subprocess.run([sys.executable, '-c', BOOTSTRAP, base, 'test-mods', *args],
                                        capture_output=True, text=True, timeout=5)
                return result, json.loads(result.stdout)
            args = ('install', '--manifest', str(manifest), '--profile', str(profile), '--session', str(session))
            with neutral_profile_lock.profile_lock(profile):
                result, value = invoke(*args)
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertEqual('busy', value['code'])
                self.assertFalse(session.exists())
                self.assertFalse((profile / 'Mods' / 'FoundryTest').exists())
            result, value = invoke(*args)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(value['installed'])
            with neutral_profile_lock.profile_lock(profile):
                result, value = invoke('retire', '--session', str(session))
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertEqual('busy', value['code'])
                self.assertFalse((session / 'retire-intent.json').exists())
            result, value = invoke('retire', '--session', str(session))
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(value['retired'])


    def test_cli_marked_profile_binding_and_pending_launch_admission(self):
        base = str(Path(__file__).resolve().parents[1] / 'src')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = root / 'The Sims 4'
            profile.mkdir()
            (profile / 'original-save.dat').write_bytes(b'original fixture')
            source = root / 'source'
            source.mkdir()
            (source / 'candidate.package').write_bytes(b'DBPF fixture')
            manifest = root / 'inventory.json'
            manifest.write_text(json.dumps(mods.inventory(source)))
            profile_session = root / 'profile-session'
            session = root / 'mod-session'
            def invoke(*args):
                result = subprocess.run([sys.executable, '-c', BOOTSTRAP, base, *args],
                                        capture_output=True, text=True, timeout=5)
                return result, json.loads(result.stdout)
            result, value = invoke('test-profile', 'activate', '--profile', str(profile), '--session', str(profile_session))
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(value['activated'])
            args = ('test-mods', 'install', '--manifest', str(manifest), '--profile', str(profile), '--session', str(session))
            result, value = invoke(*args)
            self.assertEqual(2, result.returncode, result.stderr)
            self.assertEqual('profile_session_required', value['code'])
            self.assertFalse(session.exists())
            result, value = invoke(*args, '--profile-session', str(profile_session))
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(value['installed'])
            with patch.object(neutral_profile, '_WINDOWS', True), neutral_profile.active_profile_guard(profile_session):
                neutral_profile.reserve_launch(profile_session, root / 'launch-session', 'e' * 32)
            result, value = invoke('test-mods', 'retire', '--session', str(session))
            self.assertEqual(2, result.returncode, result.stderr)
            self.assertEqual('launch_pending', value['code'])
            self.assertFalse((session / 'retire-intent.json').exists())
            another = root / 'another-mod-session'
            result, value = invoke('test-mods', 'install', '--manifest', str(manifest), '--profile', str(profile),
                                   '--profile-session', str(profile_session), '--session', str(another))
            self.assertEqual(2, result.returncode, result.stderr)
            self.assertEqual('launch_pending', value['code'])
            self.assertFalse(another.exists())
            self.assertEqual(b'DBPF fixture', (profile / 'Mods' / 'FoundryTest' / 'candidate.package').read_bytes())



if __name__ == '__main__':
    unittest.main()
