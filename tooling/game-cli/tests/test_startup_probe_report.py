"""Owned synthetic files and fake core only; no game/process/profile operations."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import ModuleType
import unittest
from unittest import mock

from sims4foundry_cli import companion_build as common
from sims4foundry_cli import startup_probe_build as builder
from sims4foundry_cli import startup_probe_report as reader
from sims4foundry_cli.errors import FoundryError
from test_companion_build import compiler_response
from test_startup_diagnostic import load_runtime


class StartupProbeReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / 'evidence.json'
        self.archive = self.root / 'probe.ts4script'
        self.manifest = self.root / 'probe.ts4script.manifest.json'
        source = self.root / 'source'
        for name in builder.MODULES:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"raise RuntimeError('never execute archive')\n")
        compiler = self.root / 'fake-compiler'
        compiler.write_bytes(b'never execute compiler')
        with mock.patch.object(common, '_run_compiler', side_effect=lambda c, r, s: json.dumps(compiler_response(r)).encode()):
            self.build = builder.build_startup_probe(source, self.archive, compiler,
                hashlib.sha256(compiler.read_bytes()).hexdigest(), run_id='a' * 32,
                probe_output=r'C:\Evidence\probe.json', wait_seconds=1)
        # The real producer runs only against an owned fake core and temp path.
        runtime = load_runtime()
        core = ModuleType('sims4.core_services')
        core.on_tick = lambda: 'owned fixture'
        with mock.patch.dict('sys.modules', {'sims4.core_services': core}):
            run = runtime.start(dict(run_id='a' * 32, output_path=str(self.report), wait_seconds=1),
                                module_origin='owned fixture', loader_type='FakeLoader')
            core.on_tick()
            run._worker.join(3)
            self.assertFalse(run._worker.is_alive())
            self.assertEqual('published', run.status['publication'])
        self.record = json.loads(self.report.read_bytes())

    def inspect(self, **changes):
        args = dict(expected_run_id='a' * 32, expected_pid=os.getpid(), archive_path=self.archive)
        args.update(changes)
        return reader.inspect_startup_probe(self.report, self.manifest, **args)

    def write(self, record=None, manifest=None):
        if record is not None:
            self.report.write_text(json.dumps(record, ensure_ascii=True))
        if manifest is not None:
            self.manifest.write_text(json.dumps(manifest, ensure_ascii=True))

    def test_actual_emitted_record_correlates_but_never_qualifies(self):
        before = {p: p.read_bytes() for p in (self.report, self.manifest, self.archive)}
        result = self.inspect()
        self.assertEqual('tick_observed', result['result_self_report'])
        self.assertTrue(result['archive_bytes_match_manifest'])
        for key in ('live_qualified', 'loader_qualified', 'game_qualified', 'profile_authority',
                    'os_process_lifetime_proof', 'independent_evidence', 'commands_authorized'):
            self.assertIs(result[key], False)
        self.assertEqual('unknown', result['owner_thread_authority'])
        self.assertEqual('unknown', result['import_outside_tick'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_missing_files_are_unknown_not_successful_probe(self):
        for path in (self.report, self.manifest, self.archive):
            path.unlink()
        result = self.inspect()
        self.assertEqual('unknown-missing-evidence', result['status'])
        self.assertEqual('unknown', result['pid_self_report_match'])
        self.assertEqual('unknown', result['archive_bytes_match_manifest'])

    def test_missing_manifest_still_validates_present_report(self):
        self.manifest.unlink()
        result = self.inspect()
        self.assertEqual('unknown', result['run_id_match'])
        self.assertTrue(result['pid_self_report_match'])
        self.record['game_qualified'] = True
        self.write(record=self.record)
        with self.assertRaises(FoundryError):
            self.inspect()

    def test_independent_expectations_are_required_strictly(self):
        for changes in ({'expected_pid': True}, {'expected_pid': 0}, {'expected_pid': os.getpid() + 1},
                        {'expected_run_id': 'b' * 32}, {'expected_run_id': 'A' * 32},
                        {'expected_run_id': 'a' * 32 + '\n'}):
            with self.subTest(changes=changes), self.assertRaises(FoundryError):
                self.inspect(**changes)

    def test_all_publishable_result_variants(self):
        for status in sorted(reader.RESULTS):
            row = copy.deepcopy(self.record)
            row['result'] = status
            row['hook_cleanup'] = 'pending'
            if status == 'thread_mismatch':
                row['tick_thread']['object_id'] += 1
                row['tick_thread']['same_thread_object_as_startup'] = False
                # Recycled ident is valid, not a contradiction.
            elif status == 'tick_thread_unavailable':
                row['tick_thread']['object_id'] = None
                row['tick_thread']['same_thread_object_as_startup'] = None
            elif status != 'tick_observed':
                row['tick_thread'] = None
                if status in ('core_unavailable', 'on_tick_unavailable', 'hook_conflict'):
                    row['hook_cleanup'] = 'not_installed'
                if status in ('core_unavailable', 'on_tick_unavailable'):
                    row['startup_stack_self_report']['exact_original_code_available'] = False
                    row['startup_stack_self_report']['exact_original_code_on_stack'] = None
            self.write(record=row)
            with self.subTest(status=status):
                self.assertEqual(status, self.inspect()['result_self_report'])

    def test_in_memory_only_statuses_are_not_publishable_records(self):
        for status in ('startup_inside_known_tick', 'output_unavailable', 'worker_failed',
                       'hook_install_failed', 'worker_start_failed', 'fixture_stopped'):
            row = copy.deepcopy(self.record)
            row['result'], row['tick_thread'] = status, None
            self.write(record=row)
            with self.subTest(status=status), self.assertRaises(FoundryError):
                self.inspect()

    def test_report_schema_type_and_contradiction_rejections(self):
        cases = [('schema', True), ('extra', 1), ('game_qualified', 1), ('game_qualified', True),
                 ('owner_thread_authority', True), ('wait_seconds', True), ('wait_seconds', 2),
                 ('wait_seconds', 31), ('pid_self_report', True), ('result', []),
                 ('worker_launch_at_serialization', 'cancelled_before_worker_claim'),
                 ('limitations', []), ('tick_thread', None), ('startup_thread', [])]
        for key, value in cases:
            row = copy.deepcopy(self.record)
            row[key] = value
            self.write(record=row)
            with self.subTest(key=key, value=value), self.assertRaises(FoundryError):
                self.inspect()
        for section, key, value in [
                ('tick_thread', 'same_ident_as_startup', False),
                ('tick_thread', 'same_thread_object_as_startup', 1),
                ('tick_thread', 'object_id', None),
                ('tick_thread', 'successful_original_return', False),
                ('startup_stack_self_report', 'exact_original_code_on_stack', True),
                ('startup_stack_self_report', 'depth_checked', True),
                ('startup_stack_self_report', 'truncated', True),
                ('python_self_report', 'version_info', [3, True, 0]),
                ('loader_self_report', 'module_origin_truncated', True)]:
            row = copy.deepcopy(self.record)
            row[section][key] = value
            self.write(record=row)
            with self.subTest(section=section, key=key), self.assertRaises(FoundryError):
                self.inspect()

    def test_loader_astral_byte_budget_and_surrogates(self):
        for text, truncated in [('\U0001f600' * 512, True), ('\ud800', False), (None, False)]:
            row = copy.deepcopy(self.record)
            row['loader_self_report']['module_origin'] = text
            row['loader_self_report']['module_origin_truncated'] = truncated
            self.write(record=row)
            self.inspect()
        row['loader_self_report']['module_origin'] = '\U0001f600' * 513
        self.write(record=row)
        with self.assertRaises(FoundryError):
            self.inspect()

    def test_manifest_exact_types_names_hashes_and_contradictions(self):
        mutations = [lambda m: m.update(schema=True), lambda m: m.update(extra=1),
            lambda m: m.update(live_qualified=True), lambda m: m.update(bytes=m['bytes'] + 1),
            lambda m: m.update(artifact='../probe.ts4script'), lambda m: m.update(member_count=True),
            lambda m: m['compiler'].update(source_executed=True),
            lambda m: m['compiler'].update(version='3.7.1234567890'),
            lambda m: m['probe_config'].update(run_id='b' * 32),
            lambda m: m['modules'][0].update(bytes=True),
            lambda m: m['modules'][0].update(sha256='x' * 64),
            lambda m: m['modules'].__setitem__(1, m['modules'][0]),
            lambda m: m['compiled_modules'][0].update(source_hash='bad')]
        for mutate in mutations:
            value = copy.deepcopy(self.build)
            mutate(value)
            self.write(manifest=value)
            with self.assertRaises(FoundryError):
                self.inspect()

    def test_json_duplicate_nonfinite_encoding_and_bounds(self):
        for raw in (b'{"schema":1,"schema":1}', b'{"x":NaN}', b'{"x":1e999}',
                    b'\xff', '{}'.encode('utf-16'), b'[' * 2000, b' ' * (reader.MAX_REPORT_BYTES + 1)):
            self.report.write_bytes(raw)
            with self.subTest(raw=raw[:40]), self.assertRaises(FoundryError):
                self.inspect()

    def test_archive_is_only_hashed_not_parsed_or_executed(self):
        # Hash agreement with opaque bytes is deliberately not ZIP authenticity.
        opaque = b'x' * self.build['bytes']
        self.archive.write_bytes(opaque)
        value = copy.deepcopy(self.build)
        value['sha256'] = hashlib.sha256(opaque).hexdigest()
        self.write(manifest=value)
        self.assertTrue(self.inspect()['archive_bytes_match_manifest'])
        self.archive.write_bytes(opaque[:-1] + b'y')
        with self.assertRaises(FoundryError):
            self.inspect()

    def test_missing_archive_and_omitted_archive_are_unknown(self):
        self.assertEqual('unknown', self.inspect(archive_path=None)['archive_bytes_match_manifest'])
        self.archive.unlink()
        self.assertEqual('unknown', self.inspect()['archive_bytes_match_manifest'])

    def test_link_special_and_hardlink_refused(self):
        for kind in ('symlink', 'hardlink', 'directory', 'fifo'):
            if kind == 'fifo' and not hasattr(os, 'mkfifo'):
                continue
            original = self.root / ('original-' + kind)
            self.report.rename(original)
            try:
                if kind == 'symlink':
                    self.report.symlink_to(original)
                elif kind == 'hardlink':
                    os.link(original, self.report)
                elif kind == 'directory':
                    self.report.mkdir()
                else:
                    os.mkfifo(self.report)
                with self.subTest(kind=kind), self.assertRaises(FoundryError):
                    self.inspect()
            finally:
                if self.report.is_dir():
                    self.report.rmdir()
                elif self.report.exists() or self.report.is_symlink():
                    self.report.unlink()
                original.rename(self.report)

    def test_disappearance_during_report_or_archive_read_is_refused(self):
        for target in (self.report, self.archive):
            original = common.plain_path
            calls = []
            def disappearing(path):
                resolved = original(path)
                if resolved == target:
                    calls.append(path)
                    if len(calls) == 2:
                        target.unlink()
                return resolved
            data = target.read_bytes()
            with mock.patch.object(common, 'plain_path', side_effect=disappearing):
                with self.assertRaises(FoundryError) as error:
                    self.inspect()
            self.assertEqual('build_input_changed', error.exception.code)
            target.write_bytes(data)

    def test_reader_inherits_stable_regular_change_checks(self):
        original = common._same_open_source
        with mock.patch.object(common, '_same_open_source', return_value=False):
            with self.assertRaises(FoundryError) as error:
                self.inspect()
        self.assertEqual('build_input_changed', error.exception.code)
        self.assertIs(common._same_open_source, original)


if __name__ == '__main__':
    unittest.main()
