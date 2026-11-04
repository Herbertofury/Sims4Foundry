from contextlib import nullcontext
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sims4foundry_cli import neutral_ea_consent as consent
from sims4foundry_cli.errors import FoundryError

class Helper:
    def __init__(self, phases=('ready', 'invoke-intent', 'acknowledged'), clean=True):
        self.phases = list(phases)
        self.commits = []
        self.clean = clean
        self.closed = 0
    def event(self, deadline):
        value = self.phases.pop(0)
        if isinstance(value, BaseException): raise value
        return value
    def commit(self, nonce): self.commits.append(nonce)
    def close(self):
        self.closed += 1
        return self.clean

class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.session = Path(self.tmp.name)
        self.context = {'ok': True, 'outcome': 'ea_request_observed', 'ea_pid': 42,
                        'request_id': 'request', 'record_sha256': 'a' * 64,
                        'timestamp_utc_filetime': 134066448000000000,
                        'timestamp_timezone':'UTC', 'timestamp_resolution_100ns':10000}
        self.process = {'pid': 42, 'creation_filetime': 123, 'image': 'EA.exe',
                        'image_sha256': 'b' * 64}
        self.helper = Helper()
        self.contexts, self.processes = [], []
    def run_attempt(self, **options):
        def context(_):
            return self.contexts.pop(0) if self.contexts else self.context.copy()
        def process(*_):
            return self.processes.pop(0) if self.processes else self.process.copy()
        defaults = dict(context_reader=context, process_reader=process,
                        helper_factory=lambda _:self.helper,
                        guard_factory=lambda _:nullcontext(lambda:{'fixture_profile':'active'}))
        defaults.update(options)
        return consent.acknowledge(self.session, 'EA.exe', 'b' * 64, **defaults)
    def test_success_is_one_shot_and_does_not_claim_game_started(self):
        result = self.run_attempt()
        self.assertTrue(result['ok'])
        self.assertFalse(result['game_started_verified'])
        self.assertEqual(1, len(self.helper.commits))
        self.assertTrue((self.session / 'ea-consent-intent.json').is_file())
        self.assertTrue((self.session / 'ea-consent-result.json').is_file())
        with self.assertRaises(FoundryError): self.run_attempt()
        self.assertEqual(1, len(self.helper.commits))
    def test_no_fresh_request_or_dialog_sends_no_input(self):
        self.contexts = [{'ok': False}]
        self.assertFalse(self.run_attempt()['input_sent'])
        self.helper = Helper(('not-matched',))
        self.assertEqual('not_ready', self.run_attempt()['outcome'])
        self.assertFalse((self.session / 'ea-consent-intent.json').exists())
        self.assertEqual([], self.helper.commits)
    def test_changed_context_before_intent_sends_no_input(self):
        self.contexts = [self.context, dict(self.context, record_sha256='c' * 64)]
        self.assertEqual('not_ready', self.run_attempt()['outcome'])
        self.assertEqual([], self.helper.commits)
        self.assertFalse((self.session / 'ea-consent-intent.json').exists())
    def test_changed_process_after_intent_requires_reconciliation(self):
        self.processes = [self.process, self.process, dict(self.process, creation_filetime=456)]
        result = self.run_attempt()
        self.assertEqual('unresolved', result['outcome'])
        self.assertFalse(result['input_sent'])
        self.assertEqual([], self.helper.commits)
        with self.assertRaises(FoundryError): self.run_attempt()
    def test_timeout_before_and_after_commit(self):
        self.helper = Helper((TimeoutError(),))
        self.assertEqual('not_ready', self.run_attempt()['outcome'])
        self.helper = Helper(('ready', TimeoutError()))
        result = self.run_attempt()
        self.assertEqual('unresolved', result['outcome'])
        self.assertIsNone(result['input_sent'])
        self.assertEqual(1, len(self.helper.commits))
    def test_helper_error_after_commit_is_unknown(self):
        self.helper = Helper(('ready', 'helper-error'))
        result = self.run_attempt()
        self.assertEqual('unresolved', result['outcome'])
        self.assertIsNone(result['input_sent'])
    def test_cleanup_failure_after_commit_is_unknown(self):
        self.helper = Helper(clean=False)
        result = self.run_attempt()
        self.assertEqual('unresolved', result['outcome'])
        self.assertIsNone(result['input_sent'])
    def test_result_receipt_failure_is_not_reported_as_success(self):
        real = consent.new_json
        def write(path, record):
            if path.name == 'ea-consent-result.json': raise OSError('fixture receipt failure')
            return real(path, record)
        with patch.object(consent, 'new_json', side_effect=write):
            result = self.run_attempt()
        self.assertEqual('unresolved', result['outcome'])
        self.assertTrue(result['input_sent'])

    def test_reused_pid_or_missing_timezone_refuses_before_helper(self):
        for field, value in [('creation_filetime', self.context['timestamp_utc_filetime'] + 10000000),
                             ('creation_filetime', self.context['timestamp_utc_filetime'] + 1),
                             ('pid', 43)]:
            with self.subTest(field=field, value=value):
                process = dict(self.process, **{field:value})
                with self.assertRaises(FoundryError) as caught:
                    self.run_attempt(process_reader=lambda *args:process,
                                     helper_factory=lambda _:self.fail('unexpected helper'))
                self.assertEqual('ea_process_lifetime', caught.exception.code)
        self.context.pop('timestamp_timezone')
        with self.assertRaises(FoundryError):
            self.run_attempt(helper_factory=lambda _:self.fail('unexpected helper'))
        self.assertFalse((self.session / 'ea-consent-intent.json').exists())

    def test_changed_profile_before_or_after_intent_never_commits(self):
        for fail_at in (2, 3):
            with self.subTest(fail_at=fail_at):
                self.helper = Helper()
                count = [0]
                def check():
                    count[0] += 1
                    if count[0] == fail_at:
                        raise FoundryError('profile_unverified', 'fixture withdrawal')
                    return {'fixture_profile':'active'}
                result = self.run_attempt(guard_factory=lambda _:nullcontext(check))
                self.assertEqual('not_ready' if fail_at == 2 else 'unresolved', result['outcome'])
                self.assertEqual([], self.helper.commits)
                self.assertFalse(result['input_sent'])
        with self.assertRaises(FoundryError):
            self.run_attempt()

    def test_deadline_includes_initial_context_and_helper_startup(self):
        now = [0.0]
        def slow_context(_):
            now[0] = 2.0
            return self.context
        with self.assertRaises(TimeoutError):
            self.run_attempt(context_reader=slow_context, timeout_seconds=1, monotonic=lambda:now[0],
                             helper_factory=lambda _:self.fail('unexpected helper'))
        now[0] = 0.0
        def slow_helper(_):
            now[0] = 2.0
            return self.helper
        result = self.run_attempt(helper_factory=slow_helper, timeout_seconds=1, monotonic=lambda:now[0])
        self.assertEqual('not_ready', result['outcome'])
        self.assertEqual(1, self.helper.closed)
        self.assertEqual([], self.helper.commits)

    def test_deadline_during_revalidation_never_reaches_intent(self):
        now, count = [0.0], [0]
        def slow_context(_):
            count[0] += 1
            if count[0] == 2: now[0] = 2.0
            return self.context
        result = self.run_attempt(context_reader=slow_context, timeout_seconds=1, monotonic=lambda:now[0])
        self.assertEqual('not_ready', result['outcome'])
        self.assertEqual([], self.helper.commits)
        self.assertFalse((self.session / 'ea-consent-intent.json').exists())

    def test_deadline_during_durable_publication_preserves_no_replay(self):
        now = [0.0]
        real = consent.new_json
        def slow_write(path, value):
            real(path, value)
            now[0] = 2.0
        with patch.object(consent, 'new_json', side_effect=slow_write):
            result = self.run_attempt(timeout_seconds=1, monotonic=lambda:now[0])
        self.assertEqual('unresolved', result['outcome'])
        self.assertFalse(result['input_sent'])
        self.assertEqual([], self.helper.commits)
        with self.assertRaises(FoundryError): self.run_attempt()

    def test_deadline_after_final_profile_check_prevents_commit(self):
        now, count = [0.0], [0]
        def check():
            count[0] += 1
            if count[0] == 3: now[0] = 1.0
            return {'fixture_profile':'active'}
        result = self.run_attempt(guard_factory=lambda _:nullcontext(check),
                                  timeout_seconds=1, monotonic=lambda:now[0])
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual([], self.helper.commits)
        self.assertTrue((self.session / 'ea-consent-intent.json').exists())

    def test_cancellation_always_closes_before_and_after_commit(self):
        self.helper = Helper((KeyboardInterrupt(),))
        with self.assertRaises(KeyboardInterrupt): self.run_attempt()
        self.assertEqual(1, self.helper.closed)
        self.helper = Helper(('ready', KeyboardInterrupt()))
        with self.assertRaises(KeyboardInterrupt): self.run_attempt()
        self.assertEqual(1, self.helper.closed)
        self.assertEqual(1, len(self.helper.commits))
        self.assertTrue((self.session / 'ea-consent-intent.json').exists())
        with self.assertRaises(FoundryError): self.run_attempt()

    def test_publication_error_after_write_remains_uncertain(self):
        real = consent.new_json
        def fail_after_write(path, value):
            real(path, value)
            raise OSError('fixture sync acknowledgment lost')
        with patch.object(consent, 'new_json', side_effect=fail_after_write):
            result = self.run_attempt()
        self.assertEqual('unresolved', result['outcome'])
        self.assertEqual([], self.helper.commits)
        with self.assertRaises(FoundryError): self.run_attempt()

    def test_startup_cleanup_uncertainty_is_not_success_or_fallback(self):
        def uncertain(_):
            raise FoundryError('ea_helper_cleanup_uncertain', 'fixture late startup')
        result = self.run_attempt(helper_factory=uncertain)
        self.assertEqual('unresolved', result['outcome'])
        self.assertFalse(result['input_sent'])
        self.assertEqual([], self.helper.commits)


class ConsentCliTests(unittest.TestCase):
    def test_cli_forwards_explicit_identity_and_session(self):
        from sims4foundry_cli import cli
        args = cli.parser().parse_args(["game-launch-ea-consent", "--session", "session",
                 "--ea-image", "EA.exe", "--ea-sha256", "a" * 64, "--seconds", "5"])
        with patch.object(consent, "acknowledge", return_value={"ok": True}) as action:
            self.assertTrue(cli.run(args)["ok"])
        action.assert_called_once_with("session", "EA.exe", "a" * 64, timeout_seconds=5)
