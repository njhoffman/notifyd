"""The default audio output's volume and mute, through pactl.

For the notification center's volume row. pactl talks to PulseAudio and to PipeWire's pulse server
alike; without it (or without a sound server) the row is left out. Neither reads (get_async) nor
changes wait for pactl, so a hung sound server never blocks the center.
"""

import re
import shutil
import subprocess

from gi.repository import Gio, GLib

SINK = '@DEFAULT_SINK@'
_PERCENT = re.compile(r'(\d+)%')


def available():
    return shutil.which('pactl') is not None


def _read(*args):
    try:
        result = subprocess.run(['pactl', *args], capture_output=True, text=True, timeout=1, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _parse(volume, mute):
    found = _PERCENT.search(volume or '')
    if not found or mute is None:
        return None
    return {'volume': int(found.group(1)), 'muted': mute.split(':', 1)[-1].strip() == 'yes'}


def get():
    """{'volume': percent of the first channel, 'muted': bool} for the default output, or None (blocks:
    the center uses get_async)."""
    return _parse(_read('get-sink-volume', SINK), _read('get-sink-mute', SINK))


def get_async(callback):
    """Read the volume without blocking: CALLBACK(level or None) runs from the main loop."""
    out = {}

    def done(proc, result, key):
        try:
            _ok, stdout, _stderr = proc.communicate_utf8_finish(result)
            out[key] = stdout if proc.get_successful() else None
        except GLib.Error:
            out[key] = None
        if len(out) == 2:
            callback(_parse(out['volume'], out['mute']))

    for key, args in (('volume', ('get-sink-volume', SINK)), ('mute', ('get-sink-mute', SINK))):
        try:
            proc = Gio.Subprocess.new(['pactl', *args],
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            out[key] = None
            if len(out) == 2:
                callback(_parse(out['volume'], out['mute']))
            continue
        proc.communicate_utf8_async(None, None, done, key)


def _spawn(*args):
    """Run pactl ARGS without waiting (GLib reaps it); the Gio.Subprocess, or None when it cannot start."""
    try:
        flags = Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE
        return Gio.Subprocess.new(['pactl', *args], flags)
    except GLib.Error:   # pactl gone since available() was checked
        return None


def set_volume(percent):
    return _spawn('set-sink-volume', SINK, f'{max(0, min(150, round(percent)))}%')


def toggle_mute():
    return _spawn('set-sink-mute', SINK, 'toggle')
