"""Fixture path policy tests use only newly owned temporary directories."""
from pathlib import Path
import tempfile
import unittest

from registered_owned_fixture import _fixture_parent, _trace_input_call
from sims4foundry_cli.errors import FoundryError


class RegisteredFixtureParentTests(unittest.TestCase):
    def test_fixture_diagnostics_redact_unrecognized_error_codes(self):
        diagnostics = []
        def fail(): raise FoundryError('private_account_name','PRIVATE')
        with self.assertRaises(FoundryError): _trace_input_call(fail,diagnostics)
        self.assertEqual(['other'],diagnostics)
        def known(): raise FoundryError('foreground_changed','PRIVATE')
        with self.assertRaises(FoundryError): _trace_input_call(known,diagnostics)
        self.assertEqual(['other','foreground_changed'],diagnostics)

    def test_named_profile_parent_is_refused_without_creating_child(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)/'The Sims 4'
            profile.mkdir()
            with self.assertRaises(FoundryError): _fixture_parent(profile)
            self.assertEqual([],list(profile.iterdir()))

    def test_existing_external_parent_is_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(Path(directory).resolve(),_fixture_parent(directory))


if __name__ == '__main__': unittest.main()
