"""Synthetic retained-witness to exit-workflow composition; no native input."""
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import unittest

if not os.path.isabs(__file__):
    raise ImportError('Absolute lexical test path required.')
_src = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src'))
_package = os.path.join(_src, 'sims4foundry_cli')
_modules = [('sims4foundry_cli', os.path.join(_package, '__init__.py'))]
_modules += [('sims4foundry_cli.' + stem, os.path.join(_package, stem + '.py'))
             for stem in ('neutral_exit', 'retained_process_witness', 'process_exit', 'windows', 'launch',
                          'io', 'errors', 'path_identity', 'neutral_records', 'companion_build')]
def matches(module, filename):
    actual = getattr(module, '__file__', None)
    return (type(actual) is str and os.path.isabs(actual) and
            os.path.normcase(os.path.normpath(actual)) == os.path.normcase(os.path.normpath(filename)))
for name, filename in _modules:
    if name in sys.modules and not matches(sys.modules[name], filename):
        raise ImportError('Refusing a foreign cached Foundry module: ' + name)
sys.path.insert(0, _src)
from sims4foundry_cli import neutral_exit, retained_process_witness as witnesses, process_exit
from sims4foundry_cli.io import digest
for name, filename in _modules:
    if name in sys.modules and not matches(sys.modules[name], filename):
        raise ImportError('Loaded Foundry module has an unexpected lexical origin: ' + name)


WINDOW = {'schema': 1, 'kind': 'foundry_windows_identity', 'pid': 123,
    'creation_filetime': 456, 'hwnd': 4, 'thread_id': 5, 'dpi': 96, 'integrity': 8192,
    'client_width': 1280, 'client_height': 720, 'executable': r'C:\Fixture\TS4_x64.exe',
    'executable_sha256': 'a' * 64, 'window_class': 'Canvas-fixture', 'client_origin': [0, 0]}
PROCESS = {name: WINDOW[name] for name in ('pid', 'creation_filetime', 'executable', 'executable_sha256')}
REQUEST = {'request_id': 'b' * 32, 'policy': 'discard_and_exit', 'identity': WINDOW,
           'save_guid': '1234', 'household_id': '5678', 'profile_binding_sha256': 'c' * 64}


class Api:
    def __init__(self):
        self.open_calls = 0
        self.close_calls = 0
        self.wait_result = process_exit.WAIT_TIMEOUT
    def open(self, pid):
        self.open_calls += 1
        return 'fake-witness'
    def identity(self, handle):
        return PROCESS['executable'], PROCESS['creation_filetime']
    def wait(self, handle, milliseconds):
        return self.wait_result
    def close(self, handle):
        self.close_calls += 1


class Provider:
    def __init__(self):
        self.api = Api()
        self.registry = witnesses.RetainedProcessRegistry(self.api, lambda identity: True)
        self.surface = 'live'
        self.sends = []
    def retain_process(self, identity):
        raw = self.registry.acquire(REQUEST['request_id'], PROCESS)
        return witnesses.WindowBoundLease(raw, identity, lambda value: deepcopy(value))
    def readback(self, identity, binding):
        choices = ['open_exit_menu'] if self.surface == 'live' else ['confirm_save_and_exit', 'confirm_discard_and_exit']
        return {'identity': identity, **binding, 'surface': self.surface,
                'view_sha256': 'd' * 64, 'available_actions': choices}
    def send_once(self, identity, binding, request_id, action, view):
        self.sends.append(action)
        if action == 'open_exit_menu':
            self.surface = 'exit_confirmation'
        else:
            self.api.wait_result = process_exit.WAIT_OBJECT_0
        return {'request_id': request_id, 'action': action, 'view_sha256': digest(view), 'accepted': True}


class WitnessExitIntegration(unittest.TestCase):
    def test_exact_witness_is_retained_before_confirmation_without_reopen(self):
        with tempfile.TemporaryDirectory(prefix='foundry-witness-exit-') as directory:
            registry = Path(directory) / 'registry'
            registry.mkdir()
            provider = Provider()
            workflow = neutral_exit.ExitWorkflow(deepcopy(REQUEST), Path(directory) / 'session', registry, provider)
            result = workflow.run()
            self.assertTrue(result['provider_terminated_observed'])
            self.assertFalse(result['process_exit_verified'])
            self.assertFalse(result['normal_exit_verified'])
            self.assertFalse(result['save_verified'])
            self.assertEqual(provider.api.open_calls, 1)
            self.assertEqual(provider.sends, ['open_exit_menu', 'confirm_discard_and_exit'])
            self.assertEqual(provider.registry.recover(REQUEST['request_id'], PROCESS).snapshot(), PROCESS)
            self.assertEqual(provider.api.open_calls, 1)
            release = workflow.close(abandon_unresolved=True)
            self.assertTrue(release['provider_release_acknowledged'])
            self.assertFalse(release['os_release_verified'])
            self.assertEqual(provider.api.close_calls, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
