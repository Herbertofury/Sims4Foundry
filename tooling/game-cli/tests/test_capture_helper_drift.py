"""Helper identity checks with fabricated inert files, never native execution."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from sims4foundry_cli import windows
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import sha256
from test_windows_cli import identity, png


class CaptureHelperDriftTests(unittest.TestCase):
    def test_helper_changed_during_identity_probe_is_not_dispatched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'ffmpeg.exe'
            helper.write_bytes(b'pinned inert fixture')
            expected = sha256(helper)
            destination = root / 'capture'
            def observer(*args):
                helper.write_bytes(b'replacement inert fixture')
                return identity()
            def output(argv, **kwargs):
                Path(argv[-1]).write_bytes(png())
                return mock.Mock(returncode=0)
            runner = mock.Mock(side_effect=output)
            with self.assertRaises(FoundryError) as caught:
                windows.capture(identity(), helper, expected, destination,
                                observer=observer, runner=runner)
            self.assertEqual(caught.exception.code, 'capture_tool_identity')
            runner.assert_not_called()
            self.assertFalse((destination / 'capture-complete.json').exists())
            self.assertEqual(helper.read_bytes(), b'replacement inert fixture')

    def test_helper_changed_during_intent_publication_is_not_dispatched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'ffmpeg.exe'
            helper.write_bytes(b'pinned inert fixture')
            expected = sha256(helper)
            destination = root / 'capture'
            publish = windows.new_json
            def publish_and_change(path, value):
                publish(path, value)
                if path.name == 'capture-intent.json':
                    helper.write_bytes(b'replacement after intent')
            runner = mock.Mock()
            with mock.patch.object(windows, 'new_json', side_effect=publish_and_change):
                with self.assertRaises(FoundryError) as caught:
                    windows.capture(identity(), helper, expected, destination,
                                    observer=lambda *args: identity(), runner=runner)
            self.assertEqual(caught.exception.code, 'capture_tool_identity')
            runner.assert_not_called()
            self.assertTrue((destination / 'capture-intent.json').exists())
            self.assertFalse((destination / 'capture-complete.json').exists())
