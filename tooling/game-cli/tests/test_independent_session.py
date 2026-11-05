"""Cooperative session integration using owned files and fabricated keys."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from sims4foundry_cli import cli, fixture_launcher, independent_session
from sims4foundry_cli.errors import FoundryError


class IndependentSessionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.authority = self.root/'authority'
        self.session = self.root/'session'
        self.keys = {}
        def create(path, scope):
            self.keys[(str(path),scope)] = b'k'*32
            return b'k'*32
        def load(path, scope):
            return self.keys[(str(path),scope)]
        for item in (patch.object(independent_session.windows_registry_key,'create_key',side_effect=create),
                     patch.object(independent_session.windows_registry_key,'load_key',side_effect=load)):
            item.start()
            self.addCleanup(item.stop)

    def test_real_sqlite_and_journal_round_trip_with_fake_keys(self):
        created = independent_session.initialize_authority(self.authority)
        self.assertFalse(created['live_dispatch_available'])
        result = independent_session.initialize_session(self.authority,self.session,fixture_launcher.FIXTURE_IDENTITY)
        self.assertTrue(result['ok'])
        self.assertFalse(result['registry_pending'])
        self.assertFalse(result['process_accessed'])
        self.assertFalse(result['game_qualified'])
        self.assertFalse(result['same_user_authenticated'])
        self.assertEqual(result,independent_session.status(self.authority,self.session))
        self.assertTrue((self.authority/'anchor.sqlite').is_file())
        self.assertTrue((self.session/'journal'/'session.json').is_file())
        with self.assertRaises(FoundryError): independent_session.submit_input_once()

    def test_lifetime_reservation_refuses_other_session_and_image_hash(self):
        independent_session.initialize_authority(self.authority)
        independent_session.initialize_session(self.authority,self.session,fixture_launcher.FIXTURE_IDENTITY)
        other = deepcopy(fixture_launcher.FIXTURE_IDENTITY)
        other['executable_sha256'] = 'b'*64
        with self.assertRaises(FoundryError) as error:
            independent_session.initialize_session(self.authority,self.root/'other',other)
        self.assertEqual('lifetime_reserved',error.exception.code)
        self.assertTrue(independent_session.status(self.authority,self.session)['ok'])

    def test_lost_key_reply_keeps_original_lifetime_reserved(self):
        independent_session.initialize_authority(self.authority)
        def lost_reply(path, scope):
            self.keys[(str(path),scope)] = b'k'*32
            raise OSError('lost fixture acknowledgment')
        with patch.object(independent_session.windows_registry_key,'create_key',side_effect=lost_reply):
            with self.assertRaises(OSError):
                independent_session.initialize_session(self.authority,self.session,fixture_launcher.FIXTURE_IDENTITY)
        self.assertFalse(independent_session.status(self.authority,self.session)['ok'])
        with self.assertRaises(FoundryError) as error:
            independent_session.initialize_session(self.authority,self.root/'other',fixture_launcher.FIXTURE_IDENTITY)
        self.assertEqual('lifetime_reserved',error.exception.code)


@unittest.skipUnless(os.name == 'nt', 'Windows DPAPI integration')
class IndependentSessionWindowsTests(unittest.TestCase):
    def test_owned_session_reopens_with_real_dpapi_and_sqlite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            authority, session = root/'authority', root/'session'
            independent_session.initialize_authority(authority)
            initial = independent_session.initialize_session(authority,session,fixture_launcher.FIXTURE_IDENTITY)
            reopened = independent_session.status(authority,session)
            self.assertEqual(initial,reopened)
            self.assertFalse(reopened['process_accessed'])
            self.assertFalse(reopened['live_dispatch_available'])
            self.assertFalse(reopened['game_qualified'])
    def test_cli_fixture_session_can_initialize_and_report_without_live_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            authority, session = str(root/'authority'), str(root/'session')
            def run(*args):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = cli.main(list(args))
                return code,json.loads(output.getvalue())
            code, created = run('independent-session','authority-init','--authority',authority)
            self.assertEqual(0,code)
            self.assertFalse(created['live_dispatch_available'])
            code, initial = run('independent-session','fixture-initialize','--authority',authority,'--session',session)
            self.assertEqual(0,code)
            code, status = run('independent-session','status','--authority',authority,'--session',session)
            self.assertEqual(0,code)
            self.assertEqual(initial,status)
            code, denied = run('independent-session','input-once','--authority',authority,'--session',session)
            self.assertEqual(2,code)
            self.assertEqual('live_admission_unqualified',denied['code'])


if __name__ == '__main__': unittest.main()
