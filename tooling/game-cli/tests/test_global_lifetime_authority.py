"""Cross-workspace reservation tests with an owned synthetic OS location."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from sims4foundry_cli import cli, global_lifetime_authority as authority
from sims4foundry_cli.errors import FoundryError
from test_guarded_input import identity


class GlobalLifetimeAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = mock.patch.object(authority,'_local_appdata',return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        authority.initialize()

    def claim(self, session='1'*32, witness='2'*32):
        return authority.claim(identity(),session,witness,'a'*64)

    def test_distinct_workspaces_share_exact_lifetime_reservation(self):
        first = self.claim()
        observed = authority.inspect(identity())
        self.assertEqual(first['reservation_sha256'],observed['reservation_sha256'])
        self.assertEqual('1'*32,observed['session_id'])
        self.assertFalse(observed['same_user_authenticated'])
        self.assertFalse(observed['rollback_protected'])
        with self.assertRaises(FoundryError) as error: self.claim('3'*32,'4'*32)
        self.assertEqual('global_lifetime_reserved',error.exception.code)
        self.assertTrue(error.exception.details['no_replay'])
        changed = deepcopy(identity())
        changed['executable_sha256'] = 'b'*64
        with self.assertRaises(FoundryError):
            authority.claim(changed,'3'*32,'4'*32,'b'*64)
        with self.assertRaises(FoundryError): authority.inspect(changed)

    def test_simultaneous_claims_have_one_winner(self):
        def attempt(number):
            try:
                self.claim(str(number)*32,str(number+2)*32)
                return 'won'
            except FoundryError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt,(1,2)))
        self.assertEqual(['global_lifetime_reserved','won'],sorted(results))

    def test_lost_claim_reply_is_inspected_not_recreated(self):
        publish = authority.new_json
        def lose_reply(path, row):
            publish(path,row)
            raise OSError('lost fixture reply')
        with mock.patch.object(authority,'new_json',side_effect=lose_reply):
            with self.assertRaises(FoundryError) as error: self.claim()
        self.assertEqual('global_claim_unresolved',error.exception.code)
        self.assertTrue(error.exception.details['no_replay'])
        self.assertEqual('1'*32,authority.inspect(identity())['session_id'])
        with self.assertRaises(FoundryError): self.claim('3'*32,'4'*32)

    def test_separate_processes_cannot_claim_same_lifetime(self):
        script = ("import sys; from pathlib import Path; "
                  "from sims4foundry_cli import global_lifetime_authority as a; "
                  "from sims4foundry_cli.errors import FoundryError; "
                  "from test_guarded_input import identity; "
                  "a._local_appdata=lambda:Path(sys.argv[1]); "
                  "exec(\"try:\\n a.claim(identity(),sys.argv[2]*32,sys.argv[3]*32,'a'*64)\\n print('won')\\n"
                  "except FoundryError as e:\\n print(e.code)\")")
        outputs = []
        for session,witness in (('1','2'),('3','4')):
            completed = subprocess.run([sys.executable,'-c',script,str(self.root),session,witness],
                                       check=True,capture_output=True,text=True,timeout=15)
            outputs.append(completed.stdout.strip())
        self.assertEqual(['won','global_lifetime_reserved'],outputs)

    def test_initialization_never_replaces_existing_authority(self):
        with self.assertRaises(FoundryError) as error: authority.initialize()
        self.assertEqual('global_authority_exists',error.exception.code)

    def test_cli_status_and_inspection_use_the_same_authority(self):
        self.claim()
        original = self.root/'identity.json'
        original.write_text(json.dumps(identity()),encoding='utf-8')
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(0,cli.main(['global-authority','status']))
        self.assertEqual('global_authority_observed',json.loads(output.getvalue())['outcome'])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0,cli.main(['global-authority','inspect','--identity',str(original)]))
        self.assertEqual('global_reservation_observed',json.loads(output.getvalue())['outcome'])
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(2,cli.main(['global-authority','initialize']))
        self.assertEqual('global_authority_exists',json.loads(output.getvalue())['code'])

    def test_tampered_reservation_is_refused_without_replacement(self):
        result = self.claim()
        path = self.root/'Sims4Foundry'/'live-authority-v1'/'lifetimes'/(result['scope']+'.json')
        path.write_text('{}',encoding='utf-8')
        with self.assertRaises(FoundryError): authority.inspect(identity())
        with self.assertRaises(FoundryError): self.claim('3'*32,'4'*32)
        self.assertEqual('{}',path.read_text())


if __name__ == '__main__': unittest.main()
