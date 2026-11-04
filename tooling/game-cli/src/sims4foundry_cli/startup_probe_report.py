"""Bounded explicit-file inspection. Correlation is never game authority."""
import re

from . import companion_build as build
from .errors import FoundryError
from .io import canonical, exact_fields, exact_int, finite_number, loads, plain_path, safe_relative
from .startup_probe_build import ENTRY, MODULES, probe_config

MAX_REPORT_BYTES = 16384
MAX_MANIFEST_BYTES = 65536
REPORT_LIMITATIONS = [
    'Thread identity does not prove game ownership or outside-tick import.',
    'PID is not independent process identity or process lifetime proof.',
    'Output placement is operator selected, not profile isolation proof.',
    'Cleanup is a snapshot; a later chained hook may retain this wrapper.']
BUILD_LIMITATIONS = [
    'The game may not import the root candidate; compilation is not loader qualification.',
    'Configured output placement and access must be verified separately before an authorized run.',
    'Diagnostic evidence is self-reported and cannot authorize profile access or commands.',
    'A matching startup/tick Thread does not prove startup occurred outside a tick.',
    'No game controls, companion activation, key provisioning, or installation are included.',
    'The executable hash does not authenticate compiler libraries or vendor provenance.']
RESULTS = {'tick_observed', 'thread_mismatch', 'tick_thread_unavailable',
           'core_unavailable', 'on_tick_unavailable', 'hook_conflict', 'timeout', 'diagnostic_fault'}


def _require(condition):
    if not condition:
        raise FoundryError('probe_report_schema', 'Invalid or contradictory startup probe evidence.')


def _fields(value, fields):
    _require(type(value) is dict)
    exact_fields(value, fields.split())


def _constant(value, expected):
    _require(type(value) is type(expected) and value == expected)


def _text(value, characters, budget=None, nullable=False):
    if value is None and nullable:
        return
    _require(type(value) is str and len(value) <= characters)
    # Runtime loader strings can contain escaped surrogates; do not silently
    # impose Unicode-scalar validation absent from the producer contract.
    if budget is not None:
        _require(len(canonical(value)) - 2 <= budget)


def _hash(value, length=64):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None)


def _identity(value):
    _fields(value, 'object_id ident')
    _require(value['object_id'] is None or exact_int(value['object_id'], 1, 2**64 - 1))
    _require(exact_int(value['ident'], 1, 2**64 - 1))


def _report(value, run_id, pid):
    _fields(value, '''schema kind run_id pid_self_report python_self_report loader_self_report
        startup_thread startup_stack_self_report worker_launch_at_serialization result tick_thread
        wait_seconds hook_cleanup output_path_guard import_outside_tick owner_thread_authority
        evidence_type independent_evidence profile_authority os_process_lifetime_proof game_qualified
        native_services_called credentials_used limitations''')
    for key, expected in [('schema', 1), ('kind', 'foundry_startup_diagnostic'),
                          ('run_id', run_id), ('pid_self_report', pid),
                          ('evidence_type', 'in_process_self_report'), ('limitations', REPORT_LIMITATIONS)]:
        _constant(value[key], expected)
    for key in ('independent_evidence', 'profile_authority', 'os_process_lifetime_proof',
                'game_qualified', 'native_services_called', 'credentials_used'):
        _constant(value[key], False)
    for key in ('import_outside_tick', 'owner_thread_authority'):
        _constant(value[key], 'unknown')
    py = value['python_self_report']
    _fields(py, 'version version_info implementation')
    _text(py['version'], 256)
    _constant(py['implementation'], 'cpython')
    _require(type(py['version_info']) is list and len(py['version_info']) == 3)
    _require(all(exact_int(n, 0, 2**31 - 1) for n in py['version_info']))
    loader = value['loader_self_report']
    _fields(loader, 'entry_called module_origin loader_type module_origin_truncated loader_type_truncated')
    _constant(loader['entry_called'], True)
    for key, limit, budget in [('module_origin', 1024, 6144), ('loader_type', 256, 1536)]:
        _text(loader[key], limit, budget, nullable=True)
        _require(type(loader[key + '_truncated']) is bool)
        _require(loader[key] is not None or not loader[key + '_truncated'])
        if loader[key + '_truncated']:
            _require(len(loader[key]) == limit or len(canonical(loader[key])) - 2 > budget - 12)
    stack = value['startup_stack_self_report']
    _fields(stack, 'depth_checked truncated exact_original_code_available exact_original_code_on_stack')
    _require(exact_int(stack['depth_checked'], 1, 64))
    _require(type(stack['truncated']) is bool and type(stack['exact_original_code_available']) is bool)
    _require(not stack['truncated'] or stack['depth_checked'] == 64)
    # Positive stack match exits before publication, never a file report.
    _constant(stack['exact_original_code_on_stack'], False if stack['exact_original_code_available'] else None)
    _require(value['worker_launch_at_serialization'] in ('worker_owned', 'start_interrupted_worker_owned'))
    _require(type(value['result']) is str and value['result'] in RESULTS)
    _require(finite_number(value['wait_seconds'], 0, 30))
    _require(value['hook_cleanup'] in ('not_installed', 'pending', 'restored_own_hook', 'not_current_pending'))
    _require(value['output_path_guard'] in ('pinned_directory_no_follow', 'metadata_checks_not_race_free'))
    startup = value['startup_thread']
    _identity(startup)
    _require(startup['object_id'] is not None)
    tick, result = value['tick_thread'], value['result']
    if result not in ('tick_observed', 'thread_mismatch', 'tick_thread_unavailable'):
        _constant(tick, None)
    else:
        _fields(tick, 'object_id ident same_thread_object_as_startup same_ident_as_startup successful_original_return')
        _identity({key: tick[key] for key in ('object_id', 'ident')})
        _constant(tick['successful_original_return'], True)
        _constant(tick['same_ident_as_startup'], tick['ident'] == startup['ident'])
        same = None if tick['object_id'] is None else tick['object_id'] == startup['object_id']
        _constant(tick['same_thread_object_as_startup'], same)
        _constant(same, {'tick_observed': True, 'thread_mismatch': False, 'tick_thread_unavailable': None}[result])
        # Different retained Thread objects can have recycled equal idents.
        if same is True:
            _constant(tick['same_ident_as_startup'], True)
        _require(value['hook_cleanup'] != 'not_installed')
    if result in ('core_unavailable', 'on_tick_unavailable'):
        _constant(value['hook_cleanup'], 'not_installed')
        _constant(stack['exact_original_code_available'], False)
    if result in ('timeout', 'diagnostic_fault'):
        _require(value['hook_cleanup'] != 'not_installed')
    return value


def _manifest(value, run_id):
    _fields(value, '''ok schema kind artifact bytes sha256 status archive_scope entry_candidate compiler
        member_count modules compiled_modules probe_config installed profile_modified game_contacted
        diagnostic_code_executed loader_qualified live_qualified credentials_included limitations''')
    for key, expected in [('ok', True), ('schema', 1), ('kind', 'foundry_startup_probe_build'),
                          ('member_count', 6), ('entry_candidate', ENTRY),
                          ('status', 'diagnostic-loader-candidate-not-game-qualified'),
                          ('archive_scope', 'credential-free-startup-diagnostic'), ('limitations', BUILD_LIMITATIONS)]:
        _constant(value[key], expected)
    for key in ('installed', 'profile_modified', 'game_contacted', 'diagnostic_code_executed',
                'loader_qualified', 'live_qualified', 'credentials_included'):
        _constant(value[key], False)
    name = value['artifact']
    _text(name, 180)
    _require(safe_relative(name) and '/' not in name and name.lower().endswith('.ts4script'))
    try:
        _require(len(name.encode('utf-8')) <= 180)
    except UnicodeError as exc:
        raise FoundryError('probe_report_schema', 'Invalid artifact Unicode.') from exc
    _require(exact_int(value['bytes'], 1, build.MAX_ARCHIVE_BYTES))
    _hash(value['sha256'])
    config = value['probe_config']
    _fields(config, 'run_id output_path wait_seconds')
    _constant(config['run_id'], run_id)
    probe_config(config['run_id'], config['output_path'], config['wait_seconds'])
    compiler = value['compiler']
    _fields(compiler, 'implementation version magic python_executable_sha256 driver_sha256 invalidation hash_seed optimization source_executed')
    for key, expected in [('implementation', 'cpython'), ('magic', '420d0d0a'),
                          ('invalidation', 'PEP 552 checked source hash'), ('hash_seed', 0),
                          ('optimization', 0), ('source_executed', False)]:
        _constant(compiler[key], expected)
    _require(type(compiler['version']) is str and re.fullmatch(r'3\.7\.[0-9]{1,2}', compiler['version']) is not None)
    for key in ('python_executable_sha256', 'driver_sha256'):
        _hash(compiler[key])
    names = MODULES + (ENTRY,)
    for key, compiled in [('modules', False), ('compiled_modules', True)]:
        rows = value[key]
        _require(type(rows) is list and len(rows) == 3)
        seen = set()
        for row in rows:
            _fields(row, 'module bytes sha256 source_hash' if compiled else 'module bytes sha256')
            _require(type(row['module']) is str)
            expected = {name[:-3] + '.pyc' for name in names} if compiled else set(names)
            _require(row['module'] in expected and row['module'] not in seen)
            seen.add(row['module'])
            _require(exact_int(row['bytes'], 17 if compiled else 0,
                               build.MAX_COMPILED_BYTES if compiled else build.MAX_SOURCE_BYTES))
            _hash(row['sha256'])
            if compiled:
                _hash(row['source_hash'], 16)
    # Fixed ZIP_STORED layout, without even parsing ZIP contents.
    rows = value['modules'] + value['compiled_modules']
    _constant(value['bytes'], 22 + sum(76 + 2 * len(row['module'].encode('utf-8')) + row['bytes'] for row in rows))
    return value


def _input(path, bound, retain=True):
    # Only initial absence is unknown. Disappearance during the stable reader
    # is a changed input, not permission to discard unstable evidence.
    try:
        plain_path(path).lstat()
    except FileNotFoundError:
        return None
    try:
        return build._regular(path, bound, retain=retain)
    except FileNotFoundError as exc:
        raise FoundryError('build_input_changed', 'Probe input disappeared during inspection.') from exc


def _read(path, bound):
    result = _input(path, bound)
    if result is None:
        return None
    raw, digest, info = result
    try:
        text = raw.decode('utf-8')
    except UnicodeError as exc:
        raise FoundryError('probe_report_encoding', 'Probe JSON must be UTF-8.') from exc
    return loads(text), digest, info.st_size


def inspect_startup_probe(report_path, manifest_path, *, expected_run_id, expected_pid, archive_path=None):
    """Read only selected files. Missing is unknown; malformed is a refusal.

    Obtain expected_pid independently, never from the report. Matching it does
    not prove process identity/lifetime. Archive verification is only byte size
    and SHA-256 agreement with an unauthenticated manifest, not code validation.
    No source, bytecode or archive content is imported/extracted/executed.
    """
    _require(type(expected_run_id) is str and re.fullmatch('[0-9a-f]{32}', expected_run_id) is not None)
    _require(exact_int(expected_pid, 1, 2**32 - 1))
    report = _read(report_path, MAX_REPORT_BYTES)
    manifest = _read(manifest_path, MAX_MANIFEST_BYTES)
    if report is not None:
        _report(report[0], expected_run_id, expected_pid)
    if manifest is not None:
        _manifest(manifest[0], expected_run_id)
    if report is not None and manifest is not None:
        _require(report[0]['wait_seconds'] == manifest[0]['probe_config']['wait_seconds'])
    archive_match = 'unknown'
    if archive_path is not None:
        archive = _input(archive_path, build.MAX_ARCHIVE_BYTES, retain=False)
        if archive is not None:
            _, digest, info = archive
            if manifest is not None:
                _require(digest == manifest[0]['sha256'] and info.st_size == manifest[0]['bytes'])
                archive_match = True
    return {
        'ok': True, 'schema': 1, 'kind': 'foundry_startup_probe_inspection',
        'status': 'self-report-correlated-not-qualified' if report and manifest else 'unknown-missing-evidence',
        'report_available': report is not None, 'manifest_available': manifest is not None,
        'report_sha256': None if report is None else report[1],
        'manifest_sha256': None if manifest is None else manifest[1],
        'run_id_match': True if report and manifest else 'unknown',
        'pid_self_report_match': True if report else 'unknown',
        'archive_bytes_match_manifest': archive_match,
        'self_report': None if report is None else report[0],
        'output_location_verified': False, 'provenance_authenticated': False,
        'result_self_report': None if report is None else report[0]['result'],
        'hook_cleanup_self_report': None if report is None else report[0]['hook_cleanup'],
        'loader_qualified': False, 'live_qualified': False, 'game_qualified': False,
        'profile_authority': False, 'owner_thread_authority': 'unknown',
        'os_process_lifetime_proof': False, 'import_outside_tick': 'unknown',
        'independent_evidence': False, 'commands_authorized': False,
        'limitations': [
            'Valid files and matching run/PID are unauthenticated correlations, not proof of a game run.',
            'Archive size/hash agreement does not authenticate its manifest, source, compiler or execution.',
            'Missing evidence is unknown; no fallback launch, profile access or command is authorized.',
            'Cleanup is a serialization snapshot, not verified removal or process termination.']}
