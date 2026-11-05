"""Check lexical source binding against pre-cached foreign modules."""
import os
import subprocess
import sys
import unittest


class ImportOriginTests(unittest.TestCase):
    def test_foreign_cached_package_or_submodule_is_refused_without_replacement(self):
        target = os.path.join(os.path.dirname(__file__), 'test_neutral_exit.py')
        src = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src')
        script = r'''
import importlib.util, os, sys, types
target, src, scope = sys.argv[1:]
if scope == 'package':
    fake = types.ModuleType('sims4foundry_cli')
    fake.__file__ = os.path.join(os.path.abspath(os.sep), 'foreign-fixture', '__init__.py')
    sys.modules['sims4foundry_cli'] = fake
else:
    sys.path.insert(0, src)
    import sims4foundry_cli
    stem = 'io' if scope == 'dependency' else 'neutral_exit'
    fake = types.ModuleType('sims4foundry_cli.' + stem)
    fake.__file__ = os.path.join(os.path.abspath(os.sep), 'foreign-fixture', stem + '.py')
    sys.modules['sims4foundry_cli.' + stem] = fake
name = 'sims4foundry_cli' if scope == 'package' else 'sims4foundry_cli.' + stem
spec = importlib.util.spec_from_file_location('origin_guard_fixture', target)
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
except ImportError as error:
    assert 'Refusing a cached Foundry module' in str(error)
    assert sys.modules[name] is fake
else:
    raise AssertionError('Foreign cache was not refused')
'''
        for scope in ('package', 'submodule', 'dependency'):
            with self.subTest(scope=scope):
                completed = subprocess.run([sys.executable, '-B', '-c', script, target, src, scope],
                                           capture_output=True, text=True, timeout=10)
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
