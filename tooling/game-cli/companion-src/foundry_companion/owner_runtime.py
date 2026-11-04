"""Explicit, one-shot neutral owner-tick lifecycle. Importing installs nothing.

The host owns process/profile admission, independent peer authentication, durable
history and qualification. No permissive host implementation is supplied. Game
modules are resolved only by the injected loader during explicit installation;
native command resolution during a tick is a lookup in a supplied module cache.
"""

from dataclasses import dataclass
import threading
from types import ModuleType
import weakref

from .native import NativeAPI, NativeRefusal
from .build_request import BuildRequest
from .queue import LiveQueueRequest, ProcessEpoch, QueueBusy, Request
from .transport import LoopbackTransport, MAX_CONNECTION_SECONDS


class OwnerRuntimeError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LiveReaders:
    """Trusted synchronous, bounded, owner-only readers; not a game provider."""
    context_reader: object
    sim_info_reader: object
    instance_reader: object
    admission: object = None


@dataclass(frozen=True)
class BuildReaders:
    """Explicit trusted callbacks, no production provider or automatic admission."""
    deployment_sha256: str
    context_reader: object
    object_reader: object
    vector3: object
    yaw_quaternion: object
    surface_identifier: object
    world_surface_type: int
    transform: object
    location: object
    test_location: object
    admission: object = None


@dataclass(frozen=True)
class RuntimeStatus:
    state: str
    enabled: bool
    hook_is_current: bool
    last_tick: object
    cleanup_complete: object


# A claimed core cannot be rebound by a second instance, including after failed
# startup or clean shutdown. Weak keys avoid retaining disposable fixture modules;
# a real core module lives for the process lifetime. Only lifecycle code uses this
# lock, never a tick. Claims are deliberately never released within that lifetime.
_CLAIMED_CORES = weakref.WeakKeyDictionary()
_CLAIM_LOCK = threading.Lock()
_COMMAND_MODULES = frozenset((
    'server_commands.clock_commands', 'server_commands.cas_commands',
    'server_commands.argument_helpers', 'sims4.commands'))


class OwnerRuntime(object):
    """Construct/install/close on the verified game owner, outside a game tick.

    Construction captures a host-declared binding and starts nothing. install()
    is one-shot even when it fails. The host must supply its independently
    reviewed transport policy and native admission; omitted providers deny.
    Injected code is trusted, not sandboxed. No flag or wire field selects it.

    command_modules contains ALREADY loaded modules admitted by the host. This
    runtime never imports them early or performs a loader call on an owner tick.
    A missing module refuses execution. NativeAPI still performs its own fresh
    admission checks before cache resolution and dispatch.
    """
    def __init__(self, session_id, process_epoch, module_loader=None,
                 host_policy=None, native_admission=None, command_modules=None,
                 live_readers=None, build_readers=None):
        if module_loader is not None and not callable(module_loader):
            raise OwnerRuntimeError('invalid_module_loader')
        if command_modules is None:
            command_modules = {}
        if (type(command_modules) is not dict
                or any(type(name) is not str or name not in _COMMAND_MODULES
                       or type(module) is not ModuleType
                       for name, module in command_modules.items())):
            raise OwnerRuntimeError('invalid_command_modules')
        if live_readers is not None:
            if (type(live_readers) is not LiveReaders
                    or not all(callable(reader) for reader in (
                        live_readers.context_reader, live_readers.sim_info_reader,
                        live_readers.instance_reader))):
                raise OwnerRuntimeError('invalid_live_readers')
            live_readers = LiveReaders(live_readers.context_reader,
                                       live_readers.sim_info_reader,
                                       live_readers.instance_reader,
                                       live_readers.admission)
        if build_readers is not None:
            if type(build_readers) is not BuildReaders:
                raise OwnerRuntimeError('invalid_build_readers')
            build_readers = BuildReaders(**{
                name: getattr(build_readers, name) for name in BuildReaders.__dataclass_fields__})
        self._build_readers = build_readers
        self._build = None
        self._owner = threading.current_thread()
        self._transport = LoopbackTransport(session_id, process_epoch, policy=host_policy)
        binding = self._transport.binding
        self._binding = (binding.session_id, binding.process_epoch.pid,
                         binding.process_epoch.creation_filetime)
        self._load = module_loader
        self._admission = native_admission
        self._commands = dict(command_modules)
        self._live_readers = live_readers
        self._native = self._live = None
        self._core = self._original = self._wrapper = None
        self._claim = object()
        self._state = 'new'
        self._enabled = False
        self._tick_depth = 0
        self._last_tick = None
        self._cleanup_complete = None

    @property
    def address(self):
        return self._transport.address

    @property
    def binding(self):
        return self._transport.binding

    @property
    def status(self):
        return RuntimeStatus(self._state, self._enabled, self._hook_is_current(),
                             self._last_tick, self._cleanup_complete)

    def _owned(self):
        if threading.current_thread() is not self._owner:
            raise OwnerRuntimeError('wrong_thread')

    def _hook_is_current(self):
        return (self._core is not None and self._wrapper is not None
                and self._core.__dict__.get('on_tick') is self._wrapper)

    def _restore_own_hook(self):
        if self._hook_is_current():
            self._core.on_tick = self._original
            return True
        return False

    def _resolve_command(self, name):
        # No imports, fallbacks, caller-controlled module names or I/O here.
        if type(name) is not str or name not in self._commands:
            raise NativeRefusal('An admitted already-loaded command module is required.')
        return self._commands[name]

    def _build_owner_active(self):
        # Plain owned runtime state only, no game callback, imports or I/O.
        return (threading.current_thread() is self._owner and self._enabled
                and self._tick_depth == 1 and self._hook_is_current())

    def _execute(self, request):
        self._owned()
        if not self._hook_is_current():
            self._enabled = False
            self._state = 'hook_replaced'
            if self._build is not None:
                self._build.close()
            raise NativeRefusal('The owner hook has been replaced.')
        if not self._enabled or self._tick_depth != 1:
            raise NativeRefusal('Execution requires the active outer owner tick.')
        if type(request) is BuildRequest and self._build is not None:
            return self._build.execute(request)
        if type(request) is Request:
            return self._native.execute(request)
        if type(request) is LiveQueueRequest and self._live is not None:
            return self._live.execute(request)
        raise NativeRefusal('No executor is installed for this exact typed request.')

    def _after_original(self):
        if not self._enabled:
            return
        if not self._hook_is_current():
            # A later mod may still call our wrapper through its own chain. Do
            # not dispatch through it, walk/rewrite it or overwrite its hook.
            self._enabled = False
            self._state = 'hook_replaced'
            if self._build is not None:
                self._build.close()
            self._last_tick = 'hook_replaced'
            return
        try:
            if self._build is not None:
                self._build.poll_context()
            self._transport.owner_tick(self._execute)
            self._last_tick = 'drained'
        except QueueBusy:
            # No request replay or second attempt on this tick. The queue owns
            # whether anything was consumed and its immutable receipt history.
            self._last_tick = 'queue_busy'
        except BaseException:
            # Preserve the game's already-successful original return. The queue
            # has retained any started failure, including control exceptions.
            # No reporting callback, logging, socket cleanup or retry on a tick.
            self._enabled = False
            self._state = 'dispatch_fault'
            if self._build is not None:
                self._build.close()
            self._last_tick = 'dispatch_fault'

    def _make_wrapper(self, original):
        def foundry_on_tick(*args, **kwargs):
            if threading.current_thread() is not self._owner:
                # Do not even update owner state from a foreign invocation.
                return original(*args, **kwargs)
            outer = self._tick_depth == 0
            self._tick_depth += 1
            try:
                result = original(*args, **kwargs)
                if outer:
                    self._after_original()
                return result
            finally:
                self._tick_depth -= 1
        # Do not use functools.wraps: copying an earlier hook's arbitrary
        # attributes can copy false ownership marks or executable descriptors.
        foundry_on_tick.__wrapped__ = original
        return foundry_on_tick

    def install(self, port=0):
        """Resolve core/services, attach once, then explicitly start admission.

        On any failure the hook is disabled, only our own current hook is
        restored, and shutdown is attempted once. Neither this object nor another
        OwnerRuntime may reclaim that core. Original startup errors propagate.
        """
        self._owned()
        if self._state != 'new':
            raise OwnerRuntimeError('lifecycle_closed')
        self._state = 'installing'
        try:
            if type(port) is not int or not 0 <= port <= 65535:
                raise OwnerRuntimeError('invalid_port')
            if self._load is None:
                raise OwnerRuntimeError('module_loader_required')
            core = self._load('sims4.core_services')
            if type(core) is not ModuleType:
                raise OwnerRuntimeError('invalid_core_module')
            original = core.__dict__.get('on_tick')
            if not callable(original):
                raise OwnerRuntimeError('invalid_original_tick')
            self._core, self._original = core, original
            if not _CLAIM_LOCK.acquire(blocking=False):
                raise OwnerRuntimeError('lifecycle_busy')
            try:
                if core in _CLAIMED_CORES:
                    raise OwnerRuntimeError('core_already_claimed')
                _CLAIMED_CORES[core] = self._claim
            finally:
                _CLAIM_LOCK.release()
            services = self._load('services')
            if type(services) is not ModuleType:
                raise OwnerRuntimeError('invalid_services_module')
            session, pid, created = self._binding
            self._native = NativeAPI(services, self._admission,
                                     command_loader=self._resolve_command,
                                     session_id=session,
                                     process_epoch=ProcessEpoch(pid, created))
            if self._live_readers is not None:
                from .live_executor import LiveQueueExecutor
                readers = self._live_readers
                self._live = LiveQueueExecutor(readers.context_reader,
                    readers.sim_info_reader, readers.instance_reader,
                    admission=readers.admission, session_id=session,
                    process_epoch=ProcessEpoch(pid, created))
            if self._build_readers is not None:
                from .build_executor import BuildExecutor
                providers = self._build_readers
                self._build = BuildExecutor(session, ProcessEpoch(pid, created),
                    providers.deployment_sha256, providers.context_reader, providers.object_reader,
                    providers.vector3, providers.yaw_quaternion, providers.surface_identifier,
                    providers.world_surface_type, providers.transform, providers.location,
                    providers.test_location, admission=providers.admission,
                    owner_active=self._build_owner_active)
            if self._state != 'installing':
                raise OwnerRuntimeError('installation_interrupted')
            if core.__dict__.get('on_tick') is not original:
                raise OwnerRuntimeError('hook_changed_during_install')
            self._wrapper = self._make_wrapper(original)
            core.on_tick = self._wrapper
            address = self._transport.start(port)
            if not self._hook_is_current() or self._state != 'installing':
                raise OwnerRuntimeError('installation_interrupted')
            self._state = 'active'
            self._enabled = True
            return address
        except BaseException:
            self._enabled = False
            self._state = 'install_failed'
            if self._build is not None:
                self._build.close()
            self._restore_own_hook()
            try:
                self._cleanup_complete = self._transport.shutdown() is True
            except BaseException:
                self._cleanup_complete = False
            raise

    def close(self, timeout=2.0):
        """Disable dispatch, restore only our current hook, and stop the listener.

        Call outside ticks on the owner. An in-tick call disables further
        dispatch and raises without I/O; the host must close outside the tick.
        False or an exception leaves cleanup pending. Explicit later close calls
        may finish cleanup, but never restart, rebind or discard queue history.
        """
        self._owned()
        if type(timeout) not in (int, float) or not 0 <= timeout <= MAX_CONNECTION_SECONDS:
            raise OwnerRuntimeError('invalid_timeout')
        self._enabled = False
        self._state = 'closing'
        if self._build is not None:
            self._build.close()
        if self._tick_depth:
            self._state = 'cleanup_pending'
            raise OwnerRuntimeError('close_inside_tick')
        self._restore_own_hook()
        self._cleanup_complete = False
        try:
            self._cleanup_complete = self._transport.shutdown(timeout) is True
        finally:
            self._state = 'closed' if self._cleanup_complete else 'cleanup_pending'
        return self._cleanup_complete
