"""Windows DPAPI key fixtures use only an owned temporary directory."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli import windows_registry_key as keys


@unittest.skipUnless(os.name == 'nt', 'DPAPI is available only on Windows')
class WindowsRegistryKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'key.json'
        self.scope = 'a'*64
    def test_owned_key_round_trip_and_other_process(self):
        key = keys.create_key(self.path, self.scope)
        self.assertEqual(32, len(key))
        self.assertEqual(key, keys.load_key(self.path, self.scope))
        script = ('import hashlib,sys; from sims4foundry_cli.windows_registry_key import load_key; '
                  'print(hashlib.sha256(load_key(sys.argv[1],sys.argv[2])).hexdigest())')
        child = subprocess.run([sys.executable,'-c',script,str(self.path),self.scope],
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
        self.assertEqual(0, child.returncode, child.stderr.decode(errors='replace'))
        self.assertEqual(hashlib.sha256(key).hexdigest(), child.stdout.decode().strip())
        with self.assertRaises(FoundryError): keys.create_key(self.path, self.scope)
    def test_wrong_scope_or_changed_record_is_rejected(self):
        keys.create_key(self.path, self.scope)
        with self.assertRaises(FoundryError): keys.load_key(self.path, 'b'*64)
        record = json.loads(self.path.read_text())
        record['protected'] = '!invalid!'
        self.path.write_text(json.dumps(record))
        with self.assertRaises(FoundryError): keys.load_key(self.path, self.scope)


if __name__ == '__main__': unittest.main()
