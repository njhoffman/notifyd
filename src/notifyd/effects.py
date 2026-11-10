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

    def __call__(self, command, n, what='run'):
        env = {**os.environ, **notification_env(n)}
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


class SoundPlayer:
    """Plays a sound file (paplay, pw-play or aplay) or a freedesktop sound name (canberra-gtk-play)."""

    def __init__(self, log=None):
        self.log = log
        self.file_player = next((p for p in ('paplay', 'pw-play', 'aplay') if shutil.which(p)), None)
        self.name_player = shutil.which('canberra-gtk-play')

    def command(self, sound):
        if '/' in sound or sound.startswith('file://'):
            path = sound.removeprefix('file://')
            return [self.file_player, path] if self.file_player else None
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
