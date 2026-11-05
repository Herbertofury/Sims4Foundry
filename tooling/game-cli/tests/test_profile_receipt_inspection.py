"""Receipt-only inspection against owned synthetic records, never a game."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
import uuid
from unittest.mock import patch

SRC = Path(os.path.abspath(__file__)).parent.parent / 'src'
sys.path.insert(0, str(SRC))


def require_package_origins():
    """Check cached modules before importing and all imported modules after."""
    for name, module in tuple(sys.modules.items()):
        if name != 'sims4foundry_cli' and not name.startswith('sims4foundry_cli.'):
            continue
        if module is None or not isinstance(getattr(module, '__file__', None), str):
            raise RuntimeError('Refusing a cached Foundry module without a source path.')
        parts = name.split('.')
        path = SRC.joinpath(*parts)
        expected = path / '__init__.py' if hasattr(module, '__path__') else path.with_suffix('.py')
        expected = os.path.normcase(os.path.abspath(str(expected)))
        actual = os.path.normcase(os.path.abspath(module.__file__))
        if actual != expected:
            raise RuntimeError('Refusing a cached or foreign Foundry source module.')


require_package_origins()
from sims4foundry_cli import cli, neutral_profile, neutral_profile_lock, neutral_launch_recovery
from sims4foundry_cli import io as foundry_io
from sims4foundry_cli.errors import FoundryError
require_package_origins()


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')


class ReceiptInspectionTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.mkdtemp(prefix='foundry-receipt-test-')
        temp_root = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
        root = os.path.normcase(os.path.abspath(fixture))
        if os.path.commonpath((temp_root, root)) != temp_root or root == temp_root:
            raise RuntimeError('Synthetic fixture target is outside the temp directory.')
        # Retain these owned fixtures: no recursive cleanup through paths that
        # another actor might have substituted while the test was running.
        self.root = Path(fixture)
        self.profile = self.root / 'The Sims 4'
        self.profile.mkdir()
        self.session = self.root / 'session'
        self.session.mkdir()
        session_id = uuid.uuid4().hex
        self.recovery = self.root / ('.foundry-test-' + session_id)
        self.recovery.mkdir()
        base = {'schema': 1, 'kind': 'foundry_profile_activation', 'session_id': session_id,
                'profile': str(self.profile), 'original_identity': neutral_profile._identity(self.profile)}
        self.record = dict(base, record_sha256=foundry_io.digest(base))
        self.prepared = {'record_sha256': self.record['record_sha256'],
                         'test_identity': neutral_profile._identity(self.recovery)}
        self.completed = {'record_sha256': self.record['record_sha256'], 'test_recovery': str(self.recovery)}
        write(self.recovery / '.foundry-test-profile.json',
              {'session_id': session_id, 'record_sha256': self.record['record_sha256']})
        for name, value in (('activation-intent.json', self.record),
                            ('activation-prepared.json', self.prepared),
                            ('activation-complete.json', self.prepared),
                            ('restore-intent.json', self.prepared),
                            ('restore-complete.json', self.completed)):
            write(self.session / name, value)
        self.windows = patch.object(neutral_profile, '_require_windows')
        self.windows.start()
        self.addCleanup(self.windows.stop)
        self.rename = patch.object(neutral_profile, '_rename_directory', side_effect=AssertionError('unexpected rename'))
        self.rename_mock = self.rename.start()
        self.addCleanup(self.rename.stop)
        self.write_receipt = patch.object(neutral_profile, 'new_json', side_effect=AssertionError('unexpected receipt write'))
        self.write_receipt_mock = self.write_receipt.start()
        self.addCleanup(self.write_receipt.stop)
        self.process = patch.object(neutral_profile, '_no_game_candidates', side_effect=AssertionError('unexpected process query'))
        self.process_mock = self.process.start()
        self.addCleanup(self.process.stop)

    def tearDown(self):
        self.rename_mock.assert_not_called()
        self.write_receipt_mock.assert_not_called()
        self.process_mock.assert_not_called()

    def test_exact_completion_observes_identity_and_initializes_lock(self):
        lock = self.root / '.foundry-profile-lifecycle.lock'
        self.assertFalse(lock.exists())
        receipt = (self.session / 'restore-complete.json').read_bytes()
        result = neutral_profile.reconcile_restored_receipt(self.session)
        self.assertEqual(result['outcome'], 'restoration_receipt_observed')
        self.assertFalse(result['move_performed'])
        self.assertFalse(result['profile_data_mutation_attempted'])
        self.assertTrue(result['lock_file_may_be_created_or_initialized'])
        self.assertFalse(result['launch_authorized'])
        self.assertFalse(result['saved_data_integrity_verified'])
        self.assertEqual(lock.read_bytes(), b'\0')
        self.assertEqual((self.session / 'restore-complete.json').read_bytes(), receipt)
        with self.assertRaises(FoundryError) as caught:
            neutral_profile.recover(self.session)
        self.assertEqual(caught.exception.code, 'already_restored')

    def test_missing_or_stale_completion_refuses(self):
        path = self.session / 'restore-complete.json'
        path.unlink()
        with self.assertRaises(FoundryError):
            neutral_profile.reconcile_restored_receipt(self.session)
        write(path, {'record_sha256': 'stale', 'test_recovery': str(self.recovery)})
        with self.assertRaises(FoundryError):
            neutral_profile.reconcile_restored_receipt(self.session)

    def test_boolean_receipt_tampering_refuses(self):
        typed = {'record_sha256': self.record['record_sha256'],
                 'test_identity': dict(self.prepared['test_identity'])}
        typed['test_identity']['device'] = 1
        altered = {'record_sha256': typed['record_sha256'], 'test_identity': dict(typed['test_identity'])}
        altered['test_identity']['device'] = True
        for name in ('activation-prepared.json', 'activation-complete.json'):
            write(self.session / name, typed)
        write(self.session / 'restore-intent.json', altered)
        with self.assertRaises(FoundryError):
            neutral_profile.reconcile_restored_receipt(self.session)

    def test_same_marker_different_directory_identity_refuses(self):
        other = self.root / 'other-synthetic-directory'
        other.mkdir()
        write(other / '.foundry-test-profile.json',
              {'session_id': self.record['session_id'], 'record_sha256': self.record['record_sha256']})
        changed = {'record_sha256': self.record['record_sha256'],
                   'test_identity': neutral_profile._identity(other)}
        self.assertNotEqual(changed['test_identity'], self.prepared['test_identity'])
        for name in ('activation-prepared.json', 'activation-complete.json', 'restore-intent.json'):
            write(self.session / name, changed)
        with self.assertRaises(FoundryError):
            neutral_profile.reconcile_restored_receipt(self.session)

    def test_pending_launch_refuses(self):
        write(self.session / 'launch-reservation.json', {})
        with self.assertRaises(FoundryError) as caught:
            neutral_profile.reconcile_restored_receipt(self.session)
        self.assertEqual(caught.exception.code, 'launch_pending')

    def test_cli_routes_and_discloses_scope(self):
        parser = cli.parser()
        subparsers = next(action for action in parser._actions if isinstance(getattr(action, 'choices', None), dict))
        text = ' '.join(subparsers.choices['test-profile'].format_help().split())
        for phrase in ('point-in-time identity', 'lock may be created or initialized', 'does not move a profile',
                       'verify save integrity', 'authorize launch'):
            self.assertIn(phrase, text)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(['test-profile', 'inspect-restoration-receipt', '--session', str(self.session)])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result['outcome'], 'restoration_receipt_observed')
        self.assertFalse(result['launch_authorized'])
        self.assertFalse(result['saved_data_integrity_verified'])
        self.assertFalse(result['move_performed'])

    def test_cli_refuses_irrelevant_profile_argument(self):
        args = cli.parser().parse_args(['test-profile', 'inspect-restoration-receipt',
                                        '--session', str(self.session), '--profile', 'unused'])
        with self.assertRaises(FoundryError) as caught:
            cli.run(args)
        self.assertEqual(caught.exception.code, 'invalid_arguments')

    def test_foreign_cached_module_refuses_before_use(self):
        foreign = types.ModuleType('sims4foundry_cli.synthetic_foreign')
        foreign.__file__ = os.path.join(tempfile.gettempdir(), 'foreign-source.py')
        with patch.dict(sys.modules, {foreign.__name__: foreign}):
            with self.assertRaisesRegex(RuntimeError, 'foreign'):
                require_package_origins()


if __name__ == '__main__':
    unittest.main(failfast=True)
