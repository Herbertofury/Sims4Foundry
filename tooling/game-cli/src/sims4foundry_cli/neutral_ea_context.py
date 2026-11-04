"""Read-only correlation of a fresh EA request with one Foundry handoff."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import time

from .errors import FoundryError
from .io import plain_path
from .path_identity import same_windows_path

MAX_TAIL = 2 * 1024 * 1024


def _open_log(path):
    path = plain_path(path)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or not before.st_dev or not before.st_ino):
        raise FoundryError('ea_log_identity', 'EA log must be a regular file with a stable identity.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise FoundryError('ea_log_changed', 'EA log changed before it was opened.')
        return fd, opened
    except BaseException:
        os.close(fd)
        raise


def capture(log):
    path = plain_path(log)
    fd, info = _open_log(path)
    try:
        if info.st_size:
            os.lseek(fd, info.st_size - 1, os.SEEK_SET)
            if os.read(fd, 1) != b'\n':
                raise FoundryError('ea_log_incomplete', 'Wait for the current EA log line to finish before launch.')
        for after in (os.fstat(fd), plain_path(path).lstat()):
            if (after.st_dev,after.st_ino,after.st_size) != (info.st_dev,info.st_ino,info.st_size):
                raise FoundryError('ea_log_changed', 'EA log changed while its cursor was captured.')
        return {'path':str(path), 'device':info.st_dev, 'inode':info.st_ino, 'offset':info.st_size}
    finally:
        os.close(fd)


def _header(line):
    matches = re.findall(r'\[([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z)\]\s+PID:\s*([0-9]+)\s+TID:\s*([0-9]+)\s+', line)
    if len(matches) != 1:
        raise FoundryError('ea_record_invalid', 'EA request header is not recognized.')
    stamp, pid, tid = matches[0]
    pid, tid = int(pid), int(tid)
    if not 0 < pid <= 0xffffffff or not 0 < tid <= 0xffffffff:
        raise FoundryError('ea_record_invalid', 'EA request process or thread ID is invalid.')
    try:
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        timestamp = parsed.timestamp()
        delta = parsed - datetime(1601, 1, 1, tzinfo=timezone.utc)
        filetime = ((delta.days * 86400 + delta.seconds) * 10000000
                    + delta.microseconds * 10)
    except ValueError as error:
        raise FoundryError('ea_record_invalid', 'EA request timestamp is invalid.') from error
    return pid, tid, timestamp, filetime


def _expected(expected):
    if (not isinstance(expected, dict) or set(expected) != {'offer_id','content_id','launcher'}
            or type(expected['offer_id']) is not str
            or re.fullmatch(r'[A-Za-z0-9:_-]{1,200}', expected['offer_id']) is None
            or type(expected['content_id']) is not str
            or re.fullmatch(r'[0-9]{1,10}', expected['content_id']) is None
            or type(expected['launcher']) is not str
            or not Path(expected['launcher']).is_absolute()
            or Path(expected['launcher']).name.casefold() != 'ts4_launcher_x64.exe'):
        raise FoundryError('ea_context_invalid', 'Expected exact account and Sims launcher identities.')
    return expected


def observe(cursor, expected, handoff_at, *, now=None):
    """Read one bounded fresh log tail, never interact with EA or the game."""
    if (not isinstance(cursor, dict) or set(cursor) != {'path','device','inode','offset'}
            or type(cursor['path']) is not str
            or any(type(cursor[k]) is not int or cursor[k] < 0 for k in ('device','inode','offset'))
            or not cursor['device'] or not cursor['inode']):
        raise FoundryError('ea_context_invalid', 'EA log cursor is invalid.')
    _expected(expected)
    now = time.time() if now is None else now
    if (type(handoff_at) not in (int,float) or type(now) not in (int,float)
            or not now - 120 <= handoff_at <= now + 2):
        raise FoundryError('ea_context_expired', 'The original EA handoff context is expired.')
    path = plain_path(cursor['path'])
    fd, before = _open_log(path)
    try:
        if ((before.st_dev,before.st_ino) != (cursor['device'],cursor['inode'])
                or before.st_size < cursor['offset'] or before.st_size - cursor['offset'] > MAX_TAIL):
            raise FoundryError('ea_log_changed', 'EA log rotated, shrank or exceeded the observation bound.')
        count = before.st_size - cursor['offset']
        os.lseek(fd, cursor['offset'], os.SEEK_SET)
        pieces = []
        remaining = count
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                raise FoundryError('ea_log_changed', 'EA log shrank during observation.')
            pieces.append(chunk)
            remaining -= len(chunk)
        raw = b''.join(pieces)
        for after in (os.fstat(fd), plain_path(path).lstat()):
            if ((after.st_dev,after.st_ino) != (cursor['device'],cursor['inode'])
                    or not stat.S_ISREG(after.st_mode) or after.st_size < before.st_size
                    or after.st_size - cursor['offset'] > MAX_TAIL):
                raise FoundryError('ea_log_changed', 'EA log changed during observation.')
    finally:
        os.close(fd)
    if not raw:
        return {'ok':False, 'outcome':'pending', 'reason':'no_fresh_ea_request'}
    if not raw.endswith(b'\n'):
        return {'ok':False, 'outcome':'pending', 'reason':'incomplete_log_line'}
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeError as error:
        raise FoundryError('ea_record_invalid', 'EA log contains invalid UTF-8.') from error
    starts = [i for i,line in enumerate(lines) if 'Handling game launch request' in line]
    legacy = [i for i,line in enumerate(lines) if 'Processing launch request:' in line]
    if len(starts) > 1 or (starts and any(i < starts[0] for i in legacy)):
        raise FoundryError('ea_competing_launches', 'Multiple fresh EA launch requests prevent correlation.')
    if starts:
        first = lines[starts[0]]
        pid, tid, stamp, filetime = _header(first)
        match = re.search(r'Handling game launch request \(InitialRequest\): IDs=\[offerKey\.offerId=\[\], offerId=\[\], contentIds=\[([0-9]{1,10})\], slug=\[\]\], offerKey\.externalType=\[\], requestOwner=\[EA\], requestSource=\[RTP\], gameArguments=\[\]$', first)
        if not match or match[1] != expected['content_id']:
            raise FoundryError('ea_record_mismatch', 'Fresh EA request does not match the selected game.')
        selected = [first]
        stage = 1
        last_stamp = stamp
        seen_serialization = False
        for line in lines[starts[0] + 1:]:
            kind = next((key for key,marker in (
                ('exe','[GAME] Got exePath='), ('issue','[GAME] Issuing launch:'),
                ('success','Successful launch. IDs:'), ('serialized','Processing launch request:'))
                if marker in line), None)
            if kind is None:
                continue
            line_pid, line_tid, line_stamp, _ = _header(line)
            if (line_pid,line_tid) != (pid,tid) or not last_stamp <= line_stamp <= stamp + 2:
                raise FoundryError('ea_record_mismatch', 'EA pipeline identity or timing changed.')
            last_stamp = line_stamp
            if kind == 'exe':
                item = re.search(r'\[GAME\] Got exePath=\[([^\]]+)\] cmdArgs=\[\] from defaultLauncher\.$', line)
                if not item or not same_windows_path(item[1], expected['launcher']):
                    raise FoundryError('ea_record_mismatch', 'EA selected another launcher.')
                if stage == 1:
                    selected.append(line)
                    stage = 2
            elif kind == 'issue':
                item = re.search(r'\[GAME\] Issuing launch: workingDirectory\[([^\]]+)\] offer\[([A-Za-z0-9:_-]+)\] content\[([0-9]{1,10})\] gameArgs \[\]$', line)
                if (not item or stage != 2 or item[2] != expected['offer_id']
                        or item[3] != expected['content_id']
                        or not same_windows_path(item[1], str(Path(expected['launcher']).parent))):
                    raise FoundryError('ea_record_mismatch', 'EA launch request identity differs.')
                selected.append(line)
                stage = 3
            elif kind == 'success':
                item = re.search(r'Successful launch\. IDs: offerKey\.offerId=\[([A-Za-z0-9:_-]+)\], offerId=\[\], contentIds=\[([0-9]{1,10})\], slug=\[the-sims-4\]$', line)
                if not item or stage != 3 or (item[1],item[2]) != (expected['offer_id'],expected['content_id']):
                    raise FoundryError('ea_record_mismatch', 'EA success record differs.')
                selected.append(line)
                stage = 4
            else:
                item = re.search(r'Processing launch request: offerId\[([A-Za-z0-9:_-]+)\] contentId\[([0-9]{1,10})\] exe\[([^\]]+)\] cwd\[([^\]]+)\] args\[\] locale\[[A-Za-z]{2}_[A-Za-z]{2}\] isTrial\[(?:true|false)\] isElevated\[(?:true|false)\] requestSource\[RTP\] isLauncherElevated\[(?:true|false)\]$', line)
                if (not item or stage != 4 or seen_serialization
                        or (item[1],item[2]) != (expected['offer_id'],expected['content_id'])
                        or not same_windows_path(item[3],expected['launcher'])
                        or not same_windows_path(item[4],str(Path(expected['launcher']).parent))):
                    raise FoundryError('ea_competing_launches', 'EA serialized a different or repeated request.')
                seen_serialization = True
        if stage != 4:
            return {'ok':False, 'outcome':'pending', 'reason':'incomplete_ea_pipeline'}
    else:
        if not legacy:
            return {'ok':False, 'outcome':'pending', 'reason':'no_fresh_ea_request'}
        if len(legacy) != 1:
            raise FoundryError('ea_competing_launches', 'Multiple fresh EA requests prevent correlation.')
        line = lines[legacy[0]]
        pid, tid, stamp, filetime = _header(line)
        item = re.search(r'Processing launch request: offerId\[([A-Za-z0-9:_-]+)\] contentId\[([0-9]{1,10})\] exe\[([^\]]+)\] cwd\[([^\]]+)\] args\[\] locale\[[A-Za-z]{2}_[A-Za-z]{2}\] isTrial\[(?:true|false)\] isElevated\[(?:true|false)\] requestSource\[RTP\] isLauncherElevated\[(?:true|false)\]$', line)
        if (not item or (item[1],item[2]) != (expected['offer_id'],expected['content_id'])
                or not same_windows_path(item[3],expected['launcher'])
                or not same_windows_path(item[4],str(Path(expected['launcher']).parent))):
            raise FoundryError('ea_record_mismatch', 'Fresh EA serialization does not match the selected game.')
        selected = [line]
    if not handoff_at - 2 <= stamp <= now + 2:
        raise FoundryError('ea_record_mismatch', 'Fresh EA request timestamp does not match this handoff.')
    return {'ok':True, 'outcome':'ea_request_observed', 'ea_pid':pid,
            'record_sha256':hashlib.sha256('\n'.join(selected).encode('utf-8')).hexdigest(),
            'timestamp':stamp, 'timestamp_utc_filetime':filetime,
            'timestamp_timezone':'UTC', 'timestamp_resolution_100ns':10000,
            'game_started_verified':False,
            'dialog_acknowledged':False, 'read_only':True}
