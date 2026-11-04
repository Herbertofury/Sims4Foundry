"""One-shot, correlated EA launch-dialog acknowledgment via Windows UI Automation.

The helper targets only the selected EA process and exact English permission
dialog. It does not approve Windows UAC, modify integrity, or use desktop-wide
clicks. Process and fresh log context are rechecked before the single commit.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

from . import neutral_ea_process, neutral_launch
from .errors import FoundryError
from .io import external_path, new_json

_WINDOWS = os.name == 'nt'
_popen = subprocess.Popen
_thread = threading.Thread

SCRIPT = r'''
$ErrorActionPreference = 'Stop'
function Emit($phase) {
    [Console]::Out.WriteLine((@{phase=$phase} | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
}
function CheckDeadline {
    # A common Windows performance counter avoids wall-clock/timezone guesses.
    if (-not [Diagnostics.Stopwatch]::IsHighResolution -or
        [Diagnostics.Stopwatch]::Frequency -ne [long]$cfg.qpc_frequency -or
        [Diagnostics.Stopwatch]::GetTimestamp() -ge [long]$cfg.deadline_qpc) {
        throw 'deadline expired or clock unavailable'
    }
}
function Descendants($root) {
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $pending = New-Object 'System.Collections.Generic.Stack[object]'
    $found = New-Object 'System.Collections.Generic.List[object]'
    $pending.Push($root)
    while ($pending.Count -gt 0) {
        CheckDeadline
        $child = $walker.GetFirstChild($pending.Pop())
        while ($null -ne $child) {
            CheckDeadline
            if ($found.Count -ge 4096) { throw 'tree bound' }
            if ($child.Current.ProcessId -ne [int]$cfg.pid) { throw 'process scope changed' }
            $found.Add($child)
            $pending.Push($child)
            $child = $walker.GetNextSibling($child)
        }
    }
    return $found.ToArray()
}
function SameProcess($cfg) {
    try {
        $p = Get-Process -Id ([int]$cfg.pid) -ErrorAction Stop
        return ($p.ProcessName -ceq 'EADesktop' -and
            $p.StartTime.ToFileTimeUtc() -eq [long]$cfg.creation_filetime -and
            [string]::Equals($p.Path, [string]$cfg.image, [StringComparison]::OrdinalIgnoreCase))
    } catch { return $false }
}
try {
    $cfg = [Console]::In.ReadLine() | ConvertFrom-Json
    CheckDeadline
    if (-not (SameProcess $cfg)) { Emit 'identity-unavailable'; exit 0 }
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $property = [System.Windows.Automation.AutomationElement]::ProcessIdProperty
    $condition = New-Object System.Windows.Automation.PropertyCondition($property, [int]$cfg.pid)
    $surfaces = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children, $condition)
    $choices = @{}
    if ($surfaces.Count -gt 4096) { throw 'surface bound' }
    foreach ($surface in $surfaces) {
        CheckDeadline
        $elements = @(Descendants $surface)
        foreach ($heading in $elements) {
            if ($heading.Current.Name -cne 'This game requires permissions') { continue }
            $parent = $heading
            for ($depth = 0; $depth -lt 4; $depth++) {
                $parent = [System.Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($parent)
                if ($null -eq $parent -or $parent.Current.ProcessId -ne [int]$cfg.pid) { break }
                $children = @(Descendants $parent)
                $messages = @($children | Where-Object { $_.Current.Name -ceq 'This game requires administrative privileges. Do you want to grant access and launch the game?' })
                $buttons = @($children | Where-Object { $_.Current.Name -ceq 'OK' -and $_.Current.IsEnabled -and $_.Current.ProcessId -eq [int]$cfg.pid -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button })
                if ($messages.Count -eq 1 -and $buttons.Count -eq 1) {
                    $id = [string]::Join(',', $buttons[0].GetRuntimeId())
                    $choices[$id] = @{scope=$parent; button=$buttons[0]; id=$id}
                    break
                }
            }
        }
    }
    if ($choices.Count -ne 1) { Emit 'not-matched'; exit 0 }
    $choice = @($choices.Values)[0]
    Emit 'ready'
    $commit = [Console]::In.ReadLine() | ConvertFrom-Json
    CheckDeadline
    if ($null -eq $commit -or $commit.nonce -cne $cfg.nonce) { Emit 'aborted'; exit 0 }
    if (-not (SameProcess $cfg)) { Emit 'identity-changed'; exit 0 }
    $children = @(Descendants $choice.scope)
    if ($children.Count -gt 4096 -or $choice.scope.Current.ProcessId -ne [int]$cfg.pid) { throw 'scope changed' }
    $headings = @($children | Where-Object { $_.Current.Name -ceq 'This game requires permissions' })
    $messages = @($children | Where-Object { $_.Current.Name -ceq 'This game requires administrative privileges. Do you want to grant access and launch the game?' })
    $buttons = @($children | Where-Object { $_.Current.Name -ceq 'OK' -and $_.Current.IsEnabled -and $_.Current.ProcessId -eq [int]$cfg.pid -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button })
    if ($headings.Count -ne 1 -or $messages.Count -ne 1 -or $buttons.Count -ne 1 -or
        [string]::Join(',', $buttons[0].GetRuntimeId()) -cne $choice.id) { Emit 'dialog-changed'; exit 0 }
    $pattern = $buttons[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    if (-not (SameProcess $cfg)) { Emit 'identity-changed'; exit 0 }
    CheckDeadline
    Emit 'invoke-intent'
    CheckDeadline
    $pattern.Invoke()
    Emit 'acknowledged'
} catch { Emit 'helper-error'; exit 1 }
'''


def _remaining(deadline, monotonic):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError('EA operation deadline expired.')
    return remaining


def _performance_counter():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    counter = kernel.QueryPerformanceCounter
    frequency = kernel.QueryPerformanceFrequency
    for operation in (counter, frequency):
        operation.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
        operation.restype = wintypes.BOOL
    value, rate = ctypes.c_longlong(), ctypes.c_longlong()
    if not frequency(ctypes.byref(rate)) or not counter(ctypes.byref(value)) or rate.value <= 0:
        raise FoundryError('ea_helper_unavailable', 'Windows performance clock is unavailable.')
    return value.value, rate.value


def _terminate(process):
    """Bounded confirmation only; an uncertain OS outcome is never replayed."""
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=.25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=.25)
        return process.poll() is not None
    except Exception:
        return False


class _UiHelper:
    """A read-only startup worker, bounded pipes, and exactly one commit writer.

    If Popen returns late, its worker sees cancellation and terminates that
    owned helper without sending configuration or a commit. Native provider/OS
    calls are not assumed interruptible; failure to join is reported uncertain.
    """
    def __init__(self, config):
        if not _WINDOWS:
            raise FoundryError('windows_required', 'EA UI Automation requires Windows.')
        self.deadline = config['deadline_monotonic']
        self.process = None
        self.reader = None
        self.writer = None
        self.starter = None
        self.stop = threading.Event()
        self.started = threading.Event()
        self.lines = queue.Queue(maxsize=8)
        self.error = None
        self.commit_started = False
        self.termination_lock = threading.Lock()
        self.termination_started = False
        self.termination_result = None
        system = os.environ.get('SystemRoot')
        if not system:
            raise FoundryError('ea_helper_unavailable', 'Windows SystemRoot is unavailable.')
        executable = Path(system) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        # Sample QPC first so sampling overhead shortens, never extends, the
        # original monotonic budget. The helper requires the same QPC frequency.
        counter, frequency = _performance_counter()
        budget = int(_remaining(self.deadline, time.monotonic) * frequency) - 1
        if not 0 < budget <= 30 * frequency:
            raise TimeoutError('EA helper deadline expired.')
        wire = {k:v for k,v in config.items() if k != 'deadline_monotonic'}
        wire['deadline_qpc'] = counter + budget
        wire['qpc_frequency'] = frequency
        encoded = json.dumps(wire, ensure_ascii=True) + '\n'
        if len(encoded) > 4096:
            raise ValueError('EA helper configuration exceeds its pipe bound.')
        gate = threading.Event()
        def reader():
            try:
                while not self.stop.is_set():
                    line = self.process.stdout.readline(8193)
                    if not line:
                        break
                    if len(line) > 8192:
                        raise ValueError('EA helper output exceeds its bound.')
                    self.lines.put_nowait(line)
            except BaseException as error:
                self.error = error
            finally:
                try:
                    self.lines.put_nowait(None)
                except queue.Full:
                    self.error = ValueError('EA helper output exceeds its queue bound.')
        def start():
            gate.wait()
            try:
                if self.stop.is_set():
                    return
                _remaining(self.deadline, time.monotonic)
                process = _popen([str(executable), '-NoProfile', '-NonInteractive', '-Command', SCRIPT],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.process = process
                if self.stop.is_set():
                    self._stop_process()
                    return
                _remaining(self.deadline, time.monotonic)
                self.reader = _thread(target=reader, name='FoundryEAConsentOutput', daemon=True)
                self.reader.start()
                if self.stop.is_set():
                    return
                _remaining(self.deadline, time.monotonic)
                process.stdin.write(encoded)
                process.stdin.flush()
                _remaining(self.deadline, time.monotonic)
            except BaseException as error:
                self.error = error
            finally:
                if self.stop.is_set() or self.error is not None:
                    if self.process is not None:
                        retired = self._stop_process()
                        if self.reader is not None and self.reader.ident is not None:
                            self.reader.join(timeout=.1)
                        if retired and (self.reader is None or not self.reader.is_alive()):
                            try:
                                self.process.stdin.close()
                                self.process.stdout.close()
                            except Exception:
                                self.termination_result = False
                self.started.set()
        try:
            self.starter = _thread(target=start, name='FoundryEAConsentStart', daemon=True)
            self.starter.start()
            gate.set()
            if not self.started.wait(_remaining(self.deadline, time.monotonic)):
                raise TimeoutError('EA helper startup deadline expired.')
            _remaining(self.deadline, time.monotonic)
            if self.error is not None:
                raise RuntimeError('EA helper startup failed.') from self.error
        except BaseException as error:
            self.stop.set()
            gate.set()
            clean = self.close()
            if clean is not True and isinstance(error, Exception):
                raise FoundryError('ea_helper_cleanup_uncertain', 'EA helper startup cleanup could not be confirmed.') from error
            raise

    def event(self, deadline):
        deadline = min(deadline, self.deadline)
        if self.error is not None:
            raise RuntimeError('EA helper output failed.') from self.error
        try:
            line = self.lines.get(timeout=_remaining(deadline, time.monotonic))
        except queue.Empty as error:
            raise TimeoutError('EA helper deadline expired.') from error
        _remaining(deadline, time.monotonic)
        if line is None or len(line) > 8192:
            raise ValueError('EA helper output is unavailable.')
        value = json.loads(line)
        if not isinstance(value, dict) or set(value) != {'phase'} or not isinstance(value['phase'], str):
            raise ValueError('EA helper output is invalid.')
        return value['phase']

    def commit(self, nonce):
        if self.commit_started or self.stop.is_set():
            raise ValueError('EA helper commit cannot be repeated.')
        _remaining(self.deadline, time.monotonic)
        self.commit_started = True
        completed = threading.Event()
        gate = threading.Event()
        outcome = []
        def write():
            gate.wait()
            try:
                if self.stop.is_set():
                    raise TimeoutError('EA helper was cancelled before commit write.')
                _remaining(self.deadline, time.monotonic)
                self.process.stdin.write(json.dumps({'nonce':nonce}) + '\n')
                self.process.stdin.flush()
            except BaseException as error:
                outcome.append(error)
            finally:
                completed.set()
        try:
            self.writer = _thread(target=write, name='FoundryEAConsentCommit', daemon=True)
            self.writer.start()
            gate.set()
            if not completed.wait(_remaining(self.deadline, time.monotonic)):
                raise TimeoutError('EA helper commit outcome is unknown.')
            _remaining(self.deadline, time.monotonic)
            if outcome:
                raise RuntimeError('EA helper commit outcome is unknown.') from outcome[0]
        except BaseException:
            self.stop.set()
            gate.set()
            raise

    def _stop_process(self):
        if self.process is None:
            return True
        with self.termination_lock:
            if self.termination_started:
                return self.termination_result is True
            self.termination_started = True
        # Do not retry a denied/uncertain cleanup through a second caller.
        self.termination_result = _terminate(self.process)
        return self.termination_result

    def close(self):
        self.stop.set()
        clean = True
        try:
            if self.process is not None:
                clean = self._stop_process()
            for worker in (self.starter, self.writer, self.reader):
                if worker is not None and worker.ident is not None:
                    worker.join(timeout=.1)
                    clean = not worker.is_alive() and clean
            if self.starter is not None and self.starter.is_alive():
                clean = False  # Its late-return path still owns termination.
            if self.process is not None and clean:
                # Never close a pipe while a blocked writer/reader owns its lock.
                self.process.stdin.close()
                self.process.stdout.close()
            return clean
        except Exception:
            return False


def _bind_lifetime(context, process):
    # Logs must explicitly identify UTC. Millisecond rendering is not evidence
    # of sub-millisecond ordering: require creation no later than the displayed
    # millisecond's lower bound, with no guessed clock-skew/timestamp tolerance.
    stamp = context.get('timestamp_utc_filetime')
    created = process.get('creation_filetime')
    if (context.get('timestamp_timezone') != 'UTC'
            or context.get('timestamp_resolution_100ns') != 10000
            or type(stamp) is not int or not 0 < stamp <= 2**64 - 1 or stamp % 10000
            or type(created) is not int or not 0 < created <= stamp
            or type(process.get('pid')) is not int or process['pid'] != context.get('ea_pid')):
        raise FoundryError('ea_process_lifetime', 'EA lifetime is not proven to precede the explicit UTC log record.')


def acknowledge(session_directory, ea_image, ea_sha256, *, timeout_seconds=20.0,
                context_reader=None, process_reader=None, helper_factory=None,
                monotonic=None, guard_factory=None):
    if type(timeout_seconds) not in (int,float) or not 0 < timeout_seconds <= 30:
        raise FoundryError('invalid_timeout', 'EA acknowledgment timeout must be at most 30 seconds.')
    monotonic = monotonic or time.monotonic
    deadline = monotonic() + float(timeout_seconds)
    session = external_path(session_directory)
    intent_file = session / 'ea-consent-intent.json'
    if intent_file.exists() or intent_file.is_symlink():
        raise FoundryError('ea_consent_pending', 'An EA consent attempt already exists; observe its outcome without replay.')
    context_reader = context_reader or neutral_launch.ea_context
    process_reader = process_reader or (lambda pid, image, sha: neutral_ea_process.inspect(
        pid, image, sha, deadline=deadline, monotonic=monotonic))
    helper_factory = helper_factory or _UiHelper
    guard_factory = guard_factory or neutral_launch.consent_guard
    _remaining(deadline, monotonic)
    with guard_factory(session) as validate_profile:
        # Recheck after acquiring the shared lock, excluding another cooperating
        # consent that completed while this caller was waiting for admission.
        if intent_file.exists() or intent_file.is_symlink():
            raise FoundryError('ea_consent_pending', 'An EA consent attempt already exists; observe without replay.')
        _remaining(deadline, monotonic)
        profile = validate_profile()
        _remaining(deadline, monotonic)
        context = context_reader(session)
        _remaining(deadline, monotonic)
        if context.get('ok') is not True or context.get('outcome') != 'ea_request_observed':
            return {'ok':False, 'outcome':'pending', 'message':'No matching fresh EA request was observed.', 'input_sent':False}
        process = process_reader(context['ea_pid'], ea_image, ea_sha256)
        _bind_lifetime(context, process)
        _remaining(deadline, monotonic)
        nonce = uuid.uuid4().hex
        config = {'pid':process['pid'], 'creation_filetime':str(process['creation_filetime']),
                  'image':process['image'], 'nonce':nonce, 'deadline_monotonic':deadline}
        flags = {'committed':False, 'intent_started':False}
        helper = None
        clean = True
        def revalidate():
            _remaining(deadline, monotonic)
            fresh = context_reader(session)
            _remaining(deadline, monotonic)
            current = process_reader(context['ea_pid'], ea_image, ea_sha256)
            _bind_lifetime(fresh, current)
            _remaining(deadline, monotonic)
            latest_profile = validate_profile()
            _remaining(deadline, monotonic)
            if fresh != context or current != process or latest_profile != profile:
                raise FoundryError('ea_context_changed', 'EA request, lifetime or recorded profile changed.')
        try:
            helper = helper_factory(config)
            _remaining(deadline, monotonic)
            phase = helper.event(deadline)
            _remaining(deadline, monotonic)
            if phase != 'ready':
                result = {'ok':False, 'outcome':'not_ready', 'phase':phase, 'input_sent':False}
            else:
                revalidate()
                record = {'schema':1, 'kind':'foundry_ea_consent_intent', 'nonce':nonce,
                          'request_id':context['request_id'], 'ea_pid':process['pid'],
                          'ea_creation_filetime':process['creation_filetime'],
                          'ea_record_utc_filetime':context['timestamp_utc_filetime'],
                          'ea_image_sha256':process['image_sha256'], 'ea_record_sha256':context['record_sha256']}
                _remaining(deadline, monotonic)
                flags['intent_started'] = True  # Publication failure may be after the durable link.
                new_json(intent_file, record)
                _remaining(deadline, monotonic)
                revalidate()  # Includes exact marker/current Documents binding after persistence.
                _remaining(deadline, monotonic)
                flags['committed'] = True  # A failed write is an uncertain action, never replayed.
                helper.commit(nonce)
                marker = helper.event(deadline)
                _remaining(deadline, monotonic)
                if marker != 'invoke-intent':
                    result = {'ok':False, 'outcome':'unresolved', 'phase':marker,
                              'input_sent':False if marker in ('identity-changed','dialog-changed','aborted') else None}
                else:
                    phase = helper.event(deadline)
                    _remaining(deadline, monotonic)
                    result = {'ok':True, 'outcome':'acknowledged', 'request_id':context['request_id'],
                              'ea_pid':process['pid'], 'input_sent':True, 'game_started_verified':False,
                              'windows_uac_approved':False, 'action_replayed':False,
                              'request_specific_dialog_verified':False}
                    if phase != 'acknowledged':
                        result = {'ok':False, 'outcome':'unresolved', 'phase':phase,
                                  'input_sent':None, 'invoke_intent_observed':True}
        except Exception as error:
            uncertain = (flags['committed'] or flags['intent_started']
                         or getattr(error, 'code', None) == 'ea_helper_cleanup_uncertain')
            result = {'ok':False, 'outcome':'unresolved' if uncertain else 'not_ready',
                      'message':'EA helper did not complete; inspect before any retry.' if uncertain
                                else 'EA helper did not reach a validated dialog.',
                      'input_sent':None if flags['committed'] else False,
                      'error_type':type(error).__name__}
        finally:
            # Cancellation must close an already created helper too. The native
            # constructor owns cleanup if startup throws before it returns.
            if helper is not None:
                try:
                    clean = helper.close() is True
                except BaseException:
                    clean = False
        if not clean:
            return {'ok':False, 'outcome':'unresolved',
                    'message':'EA helper cleanup could not be confirmed.',
                    'input_sent':None if flags['committed'] else False}
        if result.get('outcome') == 'acknowledged':
            try:
                new_json(session / 'ea-consent-result.json', result)
            except Exception:
                return {'ok':False, 'outcome':'unresolved',
                        'message':'EA acknowledgment occurred but its receipt failed.', 'input_sent':True}
        return result
