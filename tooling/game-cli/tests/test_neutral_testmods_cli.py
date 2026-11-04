import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from sims4foundry_cli import mods


class TestModCli(unittest.TestCase):
    def test_cli_installs_and_retires_temporary_mods(self):
        base = str(Path(__file__).resolve().parents[1])
        bootstrap = ("import sys;sys.path.insert(0,sys.argv[1]);"
                     "from sims4foundry_cli import neutral_testmods;"
                     "neutral_testmods._no_game_candidates=lambda:None;"
                     "from sims4foundry_cli.cli import main;"
                     "raise SystemExit(main(sys.argv[2:]))")
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


if __name__ == '__main__':
    unittest.main()
