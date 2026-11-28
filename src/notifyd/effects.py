"""What notifications do besides being drawn: sounds, rule commands, and the user's idle time.

Each is a small object the manager calls, so tests can swap in recorders.
"""

import ctypes
import ctypes.util
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

from gi.repository import Gio, GLib


def notification_env(n):
    """Environment for a rule's `run:` or a button's command (dunst's script variables, renamed)."""
    return {
        'NOTIFYD_ID': str(n.id),
        'NOTIFYD_APP': n.app,
        'NOTIFYD_SUMMARY': n.summary,
        'NOTIFYD_BODY': n.body,
        'NOTIFYD_URGENCY': n.urgency_name,
        'NOTIFYD_CATEGORY': n.category,
        'NOTIFYD_ICON': n.app_icon,
        'NOTIFYD_STACK_TAG': n.stack_tag,
        'NOTIFYD_RULES': ','.join(n.rules),
    }


class Runner:
    """Runs shell commands without waiting: the notification as NOTIFYD_* variables and as JSON on stdin."""

    def __init__(self, log=None):
        self.log = log

    def __call__(self, command, n, what='run', env=None):
        env = {**os.environ, **notification_env(n), **(env or {})}
        data = (json.dumps(n.summary_dict(), ensure_ascii=False) + '\n').encode()
        try:
            proc = subprocess.Popen(['sh', '-c', command], env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            if self.log:
                self.log.warn(f'{what} {command!r}: {exc.strerror}')
            return None

        def reap():
            try:
                _out, err = proc.communicate(data, timeout=600)
            except (subprocess.TimeoutExpired, OSError):
                proc.kill()
                return
            if proc.returncode and self.log:
                tail = err.decode(errors='replace').strip().splitlines()[-1:] if err else []
                self.log.warn(f'{what} {command!r} exited {proc.returncode}' + (f': {tail[0]}' if tail else ''))
        threading.Thread(target=reap, daemon=True).start()
        return proc


def _kill(proc):
    """Stop PROC and what it started (its own session: the group of its pid)."""
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        proc.kill()


class Filter:
    """Runs a rule's `filter` command without blocking: the notification as NOTIFYD_* variables and as
    JSON on stdin. DONE(result, error) is called on the main loop with the JSON object it printed ({}
    for no output), or None and why it gave nothing usable (it failed, timed out, printed no object)."""

    def __call__(self, command, n, timeout_ms, done):
        env = {**os.environ, **notification_env(n), 'NOTIFYD_EVENT': 'filter'}
        data = (json.dumps(n.summary_dict(), ensure_ascii=False) + '\n').encode()

        def answer(result, error):
            GLib.idle_add(lambda: done(result, error) and False)
        try:
            proc = subprocess.Popen(['sh', '-c', command], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            answer(None, exc.strerror)
            return

        def work():
            try:
                out, err = proc.communicate(data, timeout=max(timeout_ms, 1) / 1000)
            except subprocess.TimeoutExpired:
                _kill(proc)
                answer(None, f'no answer in {timeout_ms} ms')   # first: the notification must not wait on us
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:      # a child of its own (setsid) still holds the pipes
                    for pipe in (proc.stdin, proc.stdout, proc.stderr):
                        if pipe:
                            pipe.close()
                    proc.wait()
                return
            except OSError as exc:
                answer(None, exc.strerror)
                return
            if proc.returncode:
                tail = err.decode(errors='replace').strip().splitlines()[-1:]
                answer(None, f'exited {proc.returncode}' + (f': {tail[0]}' if tail else ''))
                return
            text = out.decode(errors='replace').strip()
            if not text:
                answer({}, None)
                return
            try:
                result = json.loads(text)
            except ValueError as exc:
                answer(None, f'printed no JSON object ({exc})')
                return
            answer(result, None) if isinstance(result, dict) else answer(None, 'printed JSON that is not an object')
        threading.Thread(target=work, daemon=True).start()


class SoundPlayer:
    """Plays a sound file (paplay, pw-play or aplay) or a freedesktop sound name (canberra-gtk-play)."""

    def __init__(self, log=None):
        self.log = log
        self.file_player = next((p for p in ('paplay', 'pw-play', 'aplay') if shutil.which(p)), None)
        self.name_player = shutil.which('canberra-gtk-play')

    def command(self, sound):
        """The player's command for SOUND (a path, a file:// URI or a sound name). A sender's hint is never
        taken as an option: a path starting with '-' is passed as ./PATH, such a name is refused."""
        if '/' in sound or sound.startswith('file://'):
            path = unquote(urlparse(sound).path) if sound.startswith('file://') else sound
            if path.startswith('-'):
                path = './' + path
            return [self.file_player, path] if self.file_player else None
        if sound.startswith('-'):
            return None
        return [self.name_player, '-i', sound] if self.name_player else None

    def __call__(self, sound):
        command = self.command(sound)
        if command is None:
            if self.log:
                self.log.warn(f'no player for sound {sound!r} (install paplay or canberra-gtk-play)')
            return
        try:
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
        except OSError as exc:
            if self.log:
                self.log.warn(f'sound {sound!r}: {exc.strerror}')
            return
        threading.Thread(target=proc.wait, daemon=True).start()


def sound_for(n):
    """The sound a notification asks for, given its hints and settings; None for silence.

    A rule's `sound:` wins (false silences); else suppress-sound, else sound-file, else sound-name.
    """
    setting = n.settings.get('sound')
    if setting is False or (n.hints.get('suppress-sound') and setting is None):
        return None
    if isinstance(setting, str) and setting:
        return setting
    for hint in ('sound-file', 'sound-name'):
        value = n.hints.get(hint)
        if isinstance(value, str) and value:
            return value
    return None


class _ScreenSaverInfo(ctypes.Structure):
    _fields_ = [('window', ctypes.c_ulong), ('state', ctypes.c_int), ('kind', ctypes.c_int),
                ('til_or_since', ctypes.c_ulong), ('idle', ctypes.c_ulong), ('event_mask', ctypes.c_ulong)]


class XIdle:
    """Milliseconds since the last keyboard or pointer input on the X display (XScreenSaver extension);
    0 when that cannot be asked."""

    def __init__(self):
        self.display = None
        try:
            self.x11 = ctypes.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
            self.xss = ctypes.CDLL(ctypes.util.find_library('Xss') or 'libXss.so.1')
        except OSError:
            return
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_ScreenSaverInfo)
        self.xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_ScreenSaverInfo)]
        self.display = self.x11.XOpenDisplay(None)
        if self.display:
            self.root = self.x11.XDefaultRootWindow(self.display)
            self.info = self.xss.XScreenSaverAllocInfo()

    def __call__(self):
        if not self.display:
            return 0
        if not self.xss.XScreenSaverQueryInfo(self.display, self.root, self.info):
            return 0
        return int(self.info.contents.idle)


LOCKERS = ['i3lock', 'xsecurelock', 'slock', 'betterlockscreen']


class LockState:
    """Whether the screen is locked: logind's LockedHint for this session (set by lockers started
    through `loginctl lock-session` or xss-lock), or a locker process running (PROCESSES, matched
    on /proc/PID/comm, which the kernel cuts to 15 characters)."""

    def __init__(self, processes=None, proc='/proc', connection=None):
        self.processes = {str(p)[:15] for p in (LOCKERS if processes is None else processes)}
        self.proc = Path(proc)
        self.connection = connection
        self.hint = False        # logind's LockedHint as last answered
        self.asking = False

    def logind_locked(self):
        """logind's LockedHint, asked without waiting (the main loop must not stall on a slow logind): the
        answer to the previous poll, so a lock shows at most one poll later."""
        if self.connection is None:
            return False
        if not self.asking:
            self.asking = True
            self.connection.call(
                'org.freedesktop.login1', '/org/freedesktop/login1/session/auto', 'org.freedesktop.DBus.Properties',
                'Get', GLib.Variant('(ss)', ('org.freedesktop.login1.Session', 'LockedHint')),
                GLib.VariantType('(v)'), Gio.DBusCallFlags.NONE, 2000, None, self._answered, None)
        return self.hint

    def _answered(self, connection, result, _data):
        self.asking = False
        try:
            self.hint = bool(connection.call_finish(result).unpack()[0])
        except GLib.Error:
            self.hint = False

    def locker_running(self):
        if not self.processes:
            return False
        try:
            entries = [e for e in os.listdir(self.proc) if e.isdigit()]
        except OSError:
            return False
        for pid in entries:
            try:
                if (self.proc / pid / 'comm').read_text().strip() in self.processes:
                    return True
            except OSError:
                continue
        return False

    def __call__(self):
        return self.logind_locked() or self.locker_running()

