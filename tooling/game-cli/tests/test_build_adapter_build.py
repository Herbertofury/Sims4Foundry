"""Adapter package build tests use fixture sources and a fake compiler."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from sims4foundry_cli import build_adapter_build as adapter
from sims4foundry_cli import companion_build as base
from sims4foundry_cli.errors import FoundryError


class AdapterBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        for index, name in enumerate(adapter.MODULES):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('VALUE = %d\nraise RuntimeError("must not execute")\n' % index).encode())
        self.compiler = self.root / 'fixture-python37'
        self.compiler.write_bytes(b'fake compiler; never executed')
        self.pin = hashlib.sha256(self.compiler.read_bytes()).hexdigest()
        self.output = self.root / 'dist' / 'FoundryBuildAdapter.ts4script'

    def fake_compile(self, compiler, request, scratch):
        rows = []
        for entry in json.loads(request):
            source = base64.b64decode(entry['source'])
            source_hash = hashlib.sha256(source).digest()[:8]
            data = base.PYC_PREFIX + source_hash + b'fixture'
            rows.append({'module':entry['module'][:-3] + '.pyc',
                         'source_sha256':hashlib.sha256(source).hexdigest(),
                         'source_hash':source_hash.hex(),
                         'payload':base64.b64encode(data).decode('ascii')})
        return json.dumps({'python':'3.7.17', 'magic':'420d0d0a', 'modules':rows}).encode()

    def build(self, **kwargs):
        return adapter.build_adapter(kwargs.pop('source_root', self.source),
                kwargs.pop('output', self.output), kwargs.pop('python37', self.compiler),
                kwargs.pop('compiler_sha256', self.pin), **kwargs)

    def test_fixed_namespaced_archive_deterministic_and_no_execution(self):
        with mock.patch.object(base, '_run_compiler', side_effect=self.fake_compile):
            first = self.build()
            second_path = self.root / 'second' / self.output.name
            second = self.build(output=second_path)
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertEqual(self.output.read_bytes(), second_path.read_bytes())
        self.assertEqual('foundry_companion', first['requires'])
        self.assertFalse(first['adapter_code_executed'])
        self.assertFalse(first['game_contacted'])
        self.assertFalse(first['live_qualified'])
        self.assertEqual(first, json.loads(self.output.with_name(self.output.name+'.manifest.json').read_bytes()))
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(12, len(archive.namelist()))
            self.assertIsNone(archive.testzip())
            self.assertTrue(all(name.startswith('foundry_build_adapter/') for name in archive.namelist()))
            for name in adapter.MODULES:
                self.assertEqual((self.source/name).read_bytes(), archive.read(name))
                self.assertEqual(base.PYC_PREFIX, archive.read(name[:-3]+'.pyc')[:8])

    def test_missing_extra_or_linked_inputs_are_refused(self):
        missing = self.source / adapter.MODULES[0]
        missing.unlink()
        with self.assertRaises(FoundryError): self.build()
        missing.write_bytes(b'fixture')
        extra = self.source / 'foundry_build_adapter' / 'other.py'
        extra.write_bytes(b'fixture')
        with self.assertRaises(FoundryError): self.build()
        extra.unlink()
        try:
            extra.symlink_to(missing)
        except OSError:
            self.skipTest('Host does not permit symlink creation')
        with self.assertRaises(FoundryError): self.build()

    def test_wrong_hash_or_existing_destination_is_refused(self):
        with mock.patch.object(base, '_run_compiler', side_effect=AssertionError('must not execute')):
            with self.assertRaises(FoundryError): self.build(compiler_sha256='0'*64)
        self.assertFalse(self.output.parent.exists())
        self.output.parent.mkdir()
        self.output.write_bytes(b'unchanged')
        with self.assertRaises(FoundryError): self.build()
        self.assertEqual(b'unchanged', self.output.read_bytes())

    def test_failed_manifest_write_leaves_archive_and_reports_incomplete(self):
        with mock.patch.object(base, '_run_compiler', side_effect=self.fake_compile):
            with mock.patch.object(adapter, 'new_json', side_effect=OSError('lost receipt')):
                with self.assertRaises(FoundryError) as error: self.build()
        self.assertEqual('build_publication_incomplete', error.exception.code)
        self.assertTrue(self.output.exists())
        with self.assertRaises(FoundryError): self.build()

    def test_cache_is_not_packaged_and_cli_is_readable(self):
        cache = self.source/'foundry_build_adapter'/'__pycache__'
        cache.mkdir()
        (cache/'foreign.pyc').write_bytes(b'not included')
        with mock.patch.object(base, '_run_compiler', side_effect=self.fake_compile):
            self.assertTrue(self.build()['ok'])
        with zipfile.ZipFile(self.output) as archive:
            self.assertFalse(any('__pycache__' in name for name in archive.namelist()))
        self.assertTrue(callable(adapter.main))
