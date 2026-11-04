from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sims4foundry_cli import neutral_ea_context as context
from sims4foundry_cli.errors import FoundryError


class EaContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.log = root / 'ea.log'
        self.log.write_bytes(b'old log\n')
        self.cursor = context.capture(self.log)
        self.launcher = str(root / 'Game' / 'Bin' / 'TS4_Launcher_x64.exe')
        self.expected = {'offer_id':'OFFER-1', 'content_id':'123', 'launcher':self.launcher}
        self.stamp = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc).timestamp()
        self.prefix = '[2026-11-04T12:00:00.000Z] PID: 42 TID: 7 '
        self.lines = [
            'Handling game launch request (InitialRequest): IDs=[offerKey.offerId=[], offerId=[], contentIds=[123], slug=[]], offerKey.externalType=[], requestOwner=[EA], requestSource=[RTP], gameArguments=[]',
            '[GAME] Got exePath=[{}] cmdArgs=[] from defaultLauncher.'.format(self.launcher),
            '[GAME] Issuing launch: workingDirectory[{}] offer[OFFER-1] content[123] gameArgs []'.format(str(Path(self.launcher).parent)),
            'Successful launch. IDs: offerKey.offerId=[OFFER-1], offerId=[], contentIds=[123], slug=[the-sims-4]',
        ]

    def append(self, lines):
        with self.log.open('ab') as stream:
            stream.write(('\n'.join(self.prefix + x for x in lines) + '\n').encode())

    def observe(self):
        return context.observe(self.cursor, self.expected, self.stamp, now=self.stamp+3)

    def test_exact_pipeline_and_no_game_success_claim(self):
        self.append(self.lines)
        result = self.observe()
        self.assertEqual('ea_request_observed', result['outcome'])
        self.assertEqual(42, result['ea_pid'])
        self.assertFalse(result['game_started_verified'])
        self.assertFalse(result['dialog_acknowledged'])
        self.assertTrue(result['read_only'])

    def test_incomplete_pipeline_and_line_are_pending(self):
        self.append(self.lines[:2])
        self.assertEqual('incomplete_ea_pipeline', self.observe()['reason'])
        with self.log.open('ab') as stream:
            stream.write(b'incomplete')
        self.assertEqual('incomplete_log_line', self.observe()['reason'])

    def test_wrong_or_competing_request_refuses(self):
        bad = list(self.lines)
        bad[2] = bad[2].replace('content[123]', 'content[456]')
        self.append(bad)
        with self.assertRaises(FoundryError):
            self.observe()

    def test_multiple_initial_requests_refuse(self):
        self.append(self.lines + self.lines)
        with self.assertRaises(FoundryError) as caught:
            self.observe()
        self.assertEqual('ea_competing_launches', caught.exception.code)

    def test_legacy_serialization_and_changed_source(self):
        self.append(['Processing launch request: offerId[OFFER-1] contentId[123] exe[{}] cwd[{}] args[] locale[en_US] isTrial[false] isElevated[false] requestSource[RTP] isLauncherElevated[false]'.format(self.launcher, str(Path(self.launcher).parent))])
        self.assertEqual(42, self.observe()['ea_pid'])
        other = self.log.with_name('other.log')
        other.write_bytes(b'replacement\n')
        other.replace(self.log)
        with self.assertRaises(FoundryError) as caught:
            self.observe()
        self.assertEqual('ea_log_changed', caught.exception.code)

    def test_cursor_rejects_incomplete_existing_line(self):
        with self.log.open('ab') as stream:
            stream.write(b'incomplete')
        with self.assertRaises(FoundryError) as caught:
            context.capture(self.log)
        self.assertEqual('ea_log_incomplete', caught.exception.code)

    def test_legacy_with_arguments_is_not_authorized(self):
        self.append(['Processing launch request: offerId[OFFER-1] contentId[123] exe[{}] cwd[{}] args[unsafe] locale[en_US] isTrial[false] isElevated[false] requestSource[RTP] isLauncherElevated[false]'.format(self.launcher, str(Path(self.launcher).parent))])
        with self.assertRaises(FoundryError):
            self.observe()

    def test_expired_and_wrong_session_timestamp_refuse(self):
        self.append(self.lines)
        with self.assertRaises(FoundryError):
            context.observe(self.cursor, self.expected, self.stamp-200, now=self.stamp+3)
        with self.assertRaises(FoundryError):
            context.observe(self.cursor, self.expected, self.stamp+10, now=self.stamp+12)

    def test_timestamp_explicit_utc_and_exact_millisecond_filetime(self):
        self.prefix = '[2026-11-04T12:00:00.123Z] PID: 42 TID: 7 '
        self.append(self.lines)
        result = self.observe()
        delta = datetime(2026,11,4,12,0,0,123000,tzinfo=timezone.utc) - datetime(1601,1,1,tzinfo=timezone.utc)
        expected = ((delta.days*86400+delta.seconds)*10000000 + delta.microseconds*10)
        self.assertEqual(expected, result['timestamp_utc_filetime'])
        self.assertEqual('UTC', result['timestamp_timezone'])
        self.assertEqual(10000, result['timestamp_resolution_100ns'])

    def test_timezone_is_never_assumed_for_unqualified_log_timestamps(self):
        self.prefix = '[2026-11-04T12:00:00.000] PID: 42 TID: 7 '
        self.append(self.lines)
        with self.assertRaises(FoundryError) as caught: self.observe()
        self.assertEqual('ea_record_invalid', caught.exception.code)

    def test_nonregular_or_oversized_log_is_rejected(self):
        directory = self.log.with_name('directory'); directory.mkdir()
        with self.assertRaises(FoundryError): context.capture(directory)
        with self.log.open('ab') as stream: stream.truncate(self.cursor['offset']+context.MAX_TAIL+1)
        with self.assertRaises(FoundryError) as caught: self.observe()
        self.assertEqual('ea_log_changed', caught.exception.code)


if __name__ == '__main__':
    unittest.main()
