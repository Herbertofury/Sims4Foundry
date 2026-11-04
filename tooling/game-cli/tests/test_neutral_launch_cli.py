"""CLI wiring with owned temporary profiles and no native/subprocess calls."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sims4foundry_cli import cli, neutral_launch as launch, neutral_profile as profile


class LaunchCliTest(unittest.TestCase):
    def test_launch_and_observe_cli_handoff_only_once(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            root = Path(temp)
            game = root / 'GameInstall'; game.mkdir()
            selected = root / 'The Sims 4'; selected.mkdir()
            ea = root / 'EA Suite' / 'EALauncher.exe'; ea.parent.mkdir()
            ea.write_bytes(b'fixture')
            ea_hash = hashlib.sha256(ea.read_bytes()).hexdigest()
            ea_log = root / 'ea.log'; ea_log.write_bytes(b'old log\n')
            session = root / 'launch-session'
            profile_session = root / 'profile-session'
            sent = []
            stack.enter_context(patch.object(profile, '_WINDOWS', True))
            profile.activate(selected, profile_session, process_check=lambda:None)
            stack.enter_context(patch.object(launch, '_is_windows', return_value=True))
            stack.enter_context(patch.object(launch, '_default_profile', return_value=str(selected)))
            stack.enter_context(patch.object(launch, '_handler_command', return_value='"{}" "%1"'.format(ea)))
            stack.enter_context(patch.object(launch.neutral_testmods, '_no_game_candidates', return_value=None))
            stack.enter_context(patch.object(launch.launch, 'prepare', return_value={
                'ok':True, 'launched':False, 'url':'origin2://game/launch?offerIds=123',
                'account_launch':{'offer_id':'OFFER-1','content_id':'123'}}))
            # Patch the module's dispatcher seam, never shared/global os.
            stack.enter_context(patch.object(launch, '_dispatch', side_effect=sent.append))
            stack.enter_context(patch.object(launch.discovery, 'discover', return_value={
                'status':'one_observed', 'matched_count':1, 'matches':[{'pid':987}]}))
            def run(*args):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = cli.main(list(args))
                self.assertEqual(0, code, output.getvalue())
                return json.loads(output.getvalue())
            launched = run('game-launch', '--game-root', str(game), '--ea-log', str(ea_log),
                           '--executable-sha256', 'a'*64, '--ea-launcher', str(ea),
                           '--launcher-sha256', ea_hash, '--profile-session', str(profile_session),
                           '--session', str(session), '--seconds', '1')
            self.assertEqual('game_observed', launched['outcome'])
            self.assertEqual(['origin2://game/launch?offerIds=123'], sent)
            observed = run('game-launch-observe', '--session', str(session), '--seconds', '1')
            self.assertEqual('game_observed', observed['outcome'])
            self.assertTrue(observed['read_only'])
            self.assertEqual(1, len(sent))


if __name__ == '__main__':
    unittest.main()
