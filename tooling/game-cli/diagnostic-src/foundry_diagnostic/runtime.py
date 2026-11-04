"""One-shot, credential-free loader/thread self-report, never game control.

Only start() installs a temporary original-first hook and starts one writer.
No game module is imported. The hook does bounded memory-only work. Its sample
is not proof of game ownership, outside-tick startup, profile or OS lifetime.
The tiny list/dict arbitration operations rely on CPython's GIL; other Python
implementations are refused before activation.
"""

import json
import math
import os
import stat
import sys
import threading
import time
from types import FunctionType, ModuleType


MAX_WAIT_SECONDS = 30.0
MAX_EVIDENCE_BYTES = 16384
_POLL_SECONDS = 0.01
_PIN_DIRECTORY = (os.name != 'nt' and hasattr(os, 'O_NOFOLLOW')
                  and hasattr(os, 'O_DIRECTORY')
                  and all(function in os.supports_dir_fd for function in
                          (os.open, os.stat, os.link, os.unlink)))
# Preserve the claim across importlib.reload. Removing/replacing trusted runtime
# modules is outside this self-report's trust model, not a supported retry path.
_START_TOKEN = globals().get('_START_TOKEN', [True])


class DiagnosticError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _validate(config, module_origin, loader_type):
    if type(config) is not dict or set(config) != {
            'run_id', 'output_path', 'wait_seconds'}:
        raise DiagnosticError('invalid_config')
    run_id = config['run_id']
    if (type(run_id) is not str or len(run_id) != 32
            or any(c not in '0123456789abcdef' for c in run_id)):
        raise DiagnosticError('invalid_run_id')
    path = config['output_path']
    if (type(path) is not str or not path or len(path) > 4096
            or '\x00' in path or not os.path.isabs(path)
            or not path.endswith('.json')
            or os.path.basename(path) in ('', '.', '..')
            or '..' in path.replace('\\', '/').split('/')):
        raise DiagnosticError('invalid_output_path')
    components = [part.casefold() for part in path.replace('\\', '/').split('/')]
    if (any(part.startswith('the sims 4')
            or part in ('game', 'mods', 'saves', 'tray') for part in components)
            or any(part.endswith('.ts4script') for part in components)):
        raise DiagnosticError('output_must_be_external')
    if any(ord(char) < 32 or 0xd800 <= ord(char) <= 0xdfff for char in path):
        raise DiagnosticError('invalid_output_path')
    if os.name == 'nt':
        drive, tail = os.path.splitdrive(path)
        if (len(drive) != 2 or drive[0] not in
                'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
                or drive[1] != ':' or not tail.startswith(('\\', '/'))):
            raise DiagnosticError('invalid_output_path')
        parts = tail.replace('\\', '/').split('/')[1:]
    else:
        if path.startswith('//') or '\\' in path:
            raise DiagnosticError('invalid_output_path')
        parts = path.split('/')[1:]
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    reserved.update(prefix + number for prefix in ('COM', 'LPT')
                    for number in '123456789\u00b9\u00b2\u00b3')
    for part in parts:
        if (not part or part in ('.', '..') or part.endswith((' ', '.'))
                or any(char in ':*?"<>|' for char in part)
                or part.split('.')[0].rstrip(' ').upper() in reserved):
            raise DiagnosticError('invalid_output_path')
    wait = config['wait_seconds']
    if (type(wait) not in (int, float) or not 0 <= wait <= MAX_WAIT_SECONDS
            or not math.isfinite(wait)):
        raise DiagnosticError('invalid_wait_seconds')
    for value in (module_origin, loader_type):
        if value is not None and type(value) is not str:
            raise DiagnosticError('invalid_loader_report')
    if sys.implementation.name != 'cpython':
        raise DiagnosticError('unsupported_python_implementation')
    return run_id, path, float(wait)


def _identity(thread):
    # Object identity is retained in memory for comparison, not reconstructed
    # from an ident which the OS/runtime may recycle. No name/repr callbacks.
    return {'object_id': None if thread is None else id(thread),
            'ident': threading.get_ident()}


def _bounded(value, limit, encoded_budget):
    if value is None:
        return None
    # ensure_ascii can expand one astral character to twelve JSON bytes.
    # Bound encoded cost as well as characters before retaining user strings.
    pieces, used = [], 0
    for character in value[:limit]:
        cost = len(json.dumps(character, ensure_ascii=True)) - 2
        if used + cost > encoded_budget:
            break
        pieces.append(character)
        used += cost
    return ''.join(pieces)


class DiagnosticRun(object):
    """Read-only status access; no restart, takeover, retry or game operations.

    status is a point-in-time local snapshot. Worker failure cannot be reported
    to a file which could not be published; it remains visible here instead.
    """
    def __init__(self, run_id, path, wait, module_origin, loader_type,
                 startup_stack, core, original):
        self._run_id, self._path, self._wait = run_id, path, wait
        # current_thread() can construct a DummyThread and take a blocking
        # internal lock for an unmanaged native thread. Bind only CPython's
        # existing exact registry, so a tick does neither of those operations.
        registry = getattr(threading, '_active', None)
        self._thread_registry = registry if type(registry) is dict else None
        if startup_stack['exact_original_code_on_stack'] is True:
            self._import_thread = (None if self._thread_registry is None else
                                   self._thread_registry.get(threading.get_ident()))
        else:
            self._import_thread = threading.current_thread()
        self._startup_identity = _identity(self._import_thread)
        self._pid = os.getpid()
        self._python = {'version': sys.version[:256],
                        'version_info': list(sys.version_info[:3]),
                        'implementation': sys.implementation.name}
        self._origin = _bounded(module_origin, 1024, 6144)
        self._loader = _bounded(loader_type, 256, 1536)
        self._origin_truncated = (module_origin is not None
                                  and len(module_origin) > len(self._origin))
        self._loader_truncated = (loader_type is not None
                                  and len(loader_type) > len(self._loader))
        self._deadline = time.monotonic() + wait
        self._core = self._original = self._wrapper = None
        self._entry_token = [True]
        self._result = {}  # At most one immutable-by-convention complete sample.
        self._cleanup = 'not_installed'
        self._publication = 'pending'
        self._publication_error = None
        self._temporary_cleanup = 'not_created'
        self._worker = None
        self._launch_claim = {}
        self._worker_launch = 'not_started'
        self._start_interrupted = False
        self._finished = False
        self._parent_fd = None
        self._ancestors = []
        self._stack = startup_stack
        self._source_core, self._source_original = core, original

    @property
    def status(self):
        sample = self._result.get('one')
        return {'result': None if sample is None else sample['status'],
                'publication': self._publication,
                'publication_error': self._publication_error,
                'hook_cleanup': self._cleanup,
                'temporary_cleanup': self._temporary_cleanup,
                'worker_launch': self._launch_status(),
                'startup_stack_self_report': dict(self._stack),
                'finished': self._finished}

    def _launch_status(self):
        owner = self._launch_claim.get('owner')
        if owner == 'worker':
            return ('start_interrupted_worker_owned' if self._start_interrupted
                    else 'worker_owned')
        if owner == 'startup_cancelled':
            return 'cancelled_before_worker_claim'
        return self._worker_launch

    def _finish_sample(self, status, tick=None):
        sample = {'status': status, 'tick': tick}
        # CPython atomic built-in arbitration: timeout and callback cannot both
        # win. No lock, event, I/O or reporting callback is used on a tick.
        return self._result.setdefault('one', sample) is sample

    def _is_current(self):
        return (self._core is not None and self._wrapper is not None
                and sys.modules.get('sims4.core_services') is self._core
                and self._core.__dict__.get('on_tick') is self._wrapper)

    def _restore_on_startup_thread(self, current):
        if current is not self._import_thread:
            return
        if self._is_current():
            self._core.on_tick = self._original
            self._cleanup = 'restored_own_hook'
        elif self._cleanup == 'pending':
            # Never unwrap, replace or call a later mod's hook ourselves.
            self._cleanup = 'not_current_pending'

    def _after_success(self):
        ident = threading.get_ident()
        current = (None if self._thread_registry is None
                   else self._thread_registry.get(ident))
        if 'one' in self._result:
            self._restore_on_startup_thread(current)
            return
        if not self._is_current():
            self._finish_sample('hook_conflict')
            self._cleanup = 'not_current_pending'
            return
        if time.monotonic() >= self._deadline:
            self._finish_sample('timeout')
            self._restore_on_startup_thread(current)
            return
        if current is None:
            self._finish_sample('tick_thread_unavailable', {
                'object_id': None, 'ident': ident,
                'same_thread_object_as_startup': None,
                'same_ident_as_startup': ident == self._startup_identity['ident'],
                'successful_original_return': True})
            return
        tick = _identity(current)
        tick['same_thread_object_as_startup'] = current is self._import_thread
        tick['same_ident_as_startup'] = (
            tick['ident'] == self._startup_identity['ident'])
        tick['successful_original_return'] = True
        result = ('tick_observed' if current is self._import_thread
                  else 'thread_mismatch')
        self._finish_sample(result, tick)
        self._restore_on_startup_thread(current)

    def _make_wrapper(self, original):
        def foundry_diagnostic_on_tick(*args, **kwargs):
            try:
                self._entry_token.pop()
            except IndexError:
                # Nested or concurrent calls preserve the original semantics
                # and cannot claim a second sample or modify the game hook.
                return original(*args, **kwargs)
            try:
                result = original(*args, **kwargs)
                try:
                    self._after_success()
                except BaseException:
                    # Diagnostic failure must not replace a successful game
                    # return. No original exception is intercepted by this arm.
                    self._finish_sample('diagnostic_fault')
                return result
            finally:
                self._entry_token.append(True)
        return foundry_diagnostic_on_tick

    def _install(self):
        core = self._source_core
        if type(core) is not ModuleType:
            self._finish_sample('core_unavailable')
            return
        original = self._source_original
        if not callable(original):
            self._finish_sample('on_tick_unavailable')
            return
        self._core, self._original = core, original
        self._wrapper = self._make_wrapper(original)
        if (sys.modules.get('sims4.core_services') is not core
                or core.__dict__.get('on_tick') is not original):
            self._finish_sample('hook_conflict')
            return
        core.on_tick = self._wrapper
        self._cleanup = 'pending'

    @staticmethod
    def _capture_startup_stack(original):
        code = original.__code__ if type(original) is FunctionType else None
        found = False if code is not None else None
        depth = 0
        frame = sys._getframe()
        try:
            while frame is not None and depth < 64:
                if code is not None and frame.f_code is code:
                    found = True
                depth += 1
                frame = frame.f_back
            return {'depth_checked': depth,
                    'truncated': frame is not None,
                    'exact_original_code_available': code is not None,
                    'exact_original_code_on_stack': found}
        finally:
            del frame

    @staticmethod
    def _directory_identity(info):
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 1024):
            raise DiagnosticError('output_ancestor_link_or_not_directory')
        return info.st_dev, info.st_ino

    def _prepare_output(self):
        """Read only selected path metadata, before touching the game hook."""
        parent = os.path.dirname(self._path)
        paths = []
        cursor = parent
        while True:
            paths.append(cursor)
            previous = os.path.dirname(cursor)
            if previous == cursor:
                break
            cursor = previous
        paths.reverse()
        if _PIN_DIRECTORY:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            self._parent_fd = os.open(paths[0], flags)
            self._ancestors.append((paths[0], self._directory_identity(
                os.fstat(self._parent_fd))))
            for path in paths[1:]:
                child = os.open(os.path.basename(path), flags,
                                dir_fd=self._parent_fd)
                old = self._parent_fd
                self._parent_fd = child
                os.close(old)
                self._ancestors.append((path, self._directory_identity(
                    os.fstat(child))))
        else:
            for path in paths:
                self._ancestors.append((path, self._directory_identity(os.lstat(path))))
        self._verify_ancestors()
        try:
            if self._parent_fd is not None:
                os.stat(os.path.basename(self._path), dir_fd=self._parent_fd,
                        follow_symlinks=False)
            else:
                os.lstat(self._path)
        except FileNotFoundError:
            return
        raise DiagnosticError('output_exists')

    def _verify_ancestors(self):
        for path, expected in self._ancestors:
            if self._directory_identity(os.lstat(path)) != expected:
                raise DiagnosticError('output_ancestor_changed')

    def _close_parent(self):
        if self._parent_fd is not None:
            descriptor, self._parent_fd = self._parent_fd, None
            try:
                os.close(descriptor)
            except OSError:
                self._publication_error = 'directory_close_failed'

    def _evidence(self):
        sample = self._result['one']
        return {
            'schema': 1, 'kind': 'foundry_startup_diagnostic',
            'run_id': self._run_id, 'pid_self_report': self._pid,
            'python_self_report': self._python,
            'loader_self_report': {
                'entry_called': True, 'module_origin': self._origin,
                'loader_type': self._loader,
                'module_origin_truncated': self._origin_truncated,
                'loader_type_truncated': self._loader_truncated},
            'startup_thread': self._startup_identity,
            'startup_stack_self_report': self._stack,
            'worker_launch_at_serialization': self._launch_status(),
            'result': sample['status'], 'tick_thread': sample['tick'],
            'wait_seconds': self._wait, 'hook_cleanup': self._cleanup,
            'output_path_guard': ('pinned_directory_no_follow' if _PIN_DIRECTORY
                                  else 'metadata_checks_not_race_free'),
            'import_outside_tick': 'unknown',
            'owner_thread_authority': 'unknown',
            'evidence_type': 'in_process_self_report',
            'independent_evidence': False, 'profile_authority': False,
            'os_process_lifetime_proof': False, 'game_qualified': False,
            'native_services_called': False, 'credentials_used': False,
            'limitations': [
                'Thread identity does not prove game ownership or outside-tick import.',
                'PID is not independent process identity or process lifetime proof.',
                'Output placement is operator selected, not profile isolation proof.',
                'Cleanup is a snapshot; a later chained hook may retain this wrapper.'],
        }

    def _publish(self):
        self._verify_ancestors()
        payload = (json.dumps(self._evidence(), sort_keys=True,
                              separators=(',', ':'), ensure_ascii=True)
                   + '\n').encode('utf-8')
        if len(payload) > MAX_EVIDENCE_BYTES:
            raise DiagnosticError('evidence_too_large')
        temporary = self._path + '.' + self._run_id + '.tmp'
        target = self._path
        relative = {}
        if self._parent_fd is not None:
            temporary, target = os.path.basename(temporary), os.path.basename(target)
            relative = {'dir_fd': self._parent_fd}
        descriptor = None
        own_identity = None
        try:
            # O_EXCL rejects pre-existing files and symlinks, with no read,
            # overwrite, directory creation or fallback destination.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, 'O_BINARY', 0)
            descriptor = os.open(temporary, flags, 0o600, **relative)
            info = os.fstat(descriptor)
            own_identity = (info.st_dev, info.st_ino)
            self._temporary_cleanup = 'pending'
            with os.fdopen(descriptor, 'wb') as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._verify_ancestors()
            info = os.stat(temporary, follow_symlinks=False, **relative)
            if (not stat.S_ISREG(info.st_mode)
                    or (info.st_dev, info.st_ino) != own_identity):
                raise DiagnosticError('temporary_identity_changed')
            # Hard-link publication exposes only a complete file and refuses an
            # existing target atomically. No replace/rename fallback is allowed.
            if self._parent_fd is not None:
                os.link(temporary, target, src_dir_fd=self._parent_fd,
                        dst_dir_fd=self._parent_fd, follow_symlinks=False)
            else:
                os.link(temporary, target)
            self._publication = 'published'
            self._verify_ancestors()
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if own_identity is not None:
                try:
                    info = os.stat(temporary, follow_symlinks=False, **relative)
                    if (stat.S_ISREG(info.st_mode)
                            and (info.st_dev, info.st_ino) == own_identity):
                        os.unlink(temporary, **relative)
                        self._temporary_cleanup = 'removed'
                    else:
                        self._temporary_cleanup = 'identity_changed_pending'
                except OSError:
                    self._temporary_cleanup = 'cleanup_failed_pending'

    def _work(self):
        # Thread.start() can create a thread and still raise. Exactly one side
        # owns resources: this worker or the interrupted startup cancellation.
        # A cancelled late worker returns without touching files, hooks or FDs.
        if self._launch_claim.setdefault('owner', 'worker') != 'worker':
            return
        try:
            while 'one' not in self._result:
                if not self._is_current():
                    self._finish_sample('hook_conflict')
                    self._cleanup = 'not_current_pending'
                    break
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    self._finish_sample('timeout')
                    break
                time.sleep(min(_POLL_SECONDS, remaining))
            self._publish()
        except BaseException as error:
            self._finish_sample('worker_failed')
            # Store only a bounded error class, never arbitrary messages/paths.
            if self._publication == 'published':
                self._publication = 'published_location_unverified'
            else:
                self._publication = 'failed'
            self._publication_error = type(error).__name__[:80]
        finally:
            self._close_parent()
            self._finished = True


def start(config, module_origin=None, loader_type=None):
    """Consume one activation attempt and return its DiagnosticRun.

    config is exactly {run_id: 32 lowercase hex, output_path: absolute new JSON
    path, wait_seconds: finite 0..30}. Parent directories must already exist.
    The supplied path must be independently selected outside game/profile/save
    locations; this runtime never discovers or verifies profile identity.
    Optional origin/type strings are bounded, unverified root-entry self-report.
    Duplicate or invalid attempts raise DiagnosticError. No attempt is retried.
    """
    try:
        _START_TOKEN.pop()
    except IndexError:
        raise DiagnosticError('already_started')
    run_id, path, wait = _validate(config, module_origin, loader_type)
    core = sys.modules.get('sims4.core_services')
    original = core.__dict__.get('on_tick') if type(core) is ModuleType else None
    stack = DiagnosticRun._capture_startup_stack(original)
    run = DiagnosticRun(run_id, path, wait, module_origin, loader_type,
                        stack, core, original)
    if stack['exact_original_code_on_stack'] is True:
        run._finish_sample('startup_inside_known_tick')
        run._publication = 'not_attempted'
        run._publication_error = 'startup_inside_known_tick'
        run._finished = True
        return run
    try:
        run._prepare_output()
    except BaseException as error:
        run._finish_sample('output_unavailable')
        run._publication = 'failed'
        run._publication_error = (error.code if type(error) is DiagnosticError
                                  else type(error).__name__[:80])
        run._close_parent()
        run._finished = True
        return run
    phase = 'hook_install_failed'
    try:
        run._install()
        phase = 'worker_start_failed'
        worker = threading.Thread(target=run._work,
                                  name='FoundryStartupDiagnostic', daemon=True)
        run._worker = worker
        run._worker_launch = 'starting'
        worker.start()
    except BaseException:
        run._start_interrupted = True
        owner = run._launch_claim.setdefault('owner', 'startup_cancelled')
        if owner == 'worker':
            # The worker may be running or already complete. Do not close its
            # directory descriptor, rewrite its result, restore its hook or
            # assert completion based on an interrupted start() return.
            return run
        run._finish_sample(phase)
        run._publication = 'failed'
        run._publication_error = phase
        run._finished = True
        run._restore_on_startup_thread(threading.current_thread())
        run._close_parent()
    return run
