import io
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from notifyd.effects import Runner, SoundPlayer, XIdle, notification_env, sound_for
from notifyd.log import EventLog
from notifyd.model import Notification


def sample():
    n = Notification(app='MPD', summary='Song', body='<b>Artist</b>', id=7, hints={'urgency': 0, 'category': 'x'})
    n.rules = ['songs']
    return n


def test_environment_names():
    env = notification_env(sample())
    assert env['NOTIFYD_ID'] == '7' and env['NOTIFYD_APP'] == 'MPD' and env['NOTIFYD_URGENCY'] == 'low'
    assert env['NOTIFYD_BODY'] == '<b>Artist</b>' and env['NOTIFYD_RULES'] == 'songs'


def wait_for(path, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists() and path.read_text().endswith('\n'):
            return path.read_text()
        time.sleep(0.02)
    raise AssertionError(f'{path} never written')


def test_runner_passes_env_and_json(tmp_path):
    out = tmp_path / 'out'
    Runner()(f'printf "%s %s\\n" "$NOTIFYD_APP" "$NOTIFYD_SUMMARY" > {out}.env; cat > {out}', sample())
    data = json.loads(wait_for(out))
    assert data['app'] == 'MPD' and data['id'] == 7
    assert (tmp_path / 'out.env').read_text() == 'MPD Song\n'


def test_runner_logs_failures():
    stream = io.StringIO()
    proc = Runner(EventLog(stream=stream, color='never'))('echo broken >&2; exit 3', sample(), 'button')
    proc.wait(5)
    end = time.monotonic() + 5
    while 'exited 3' not in stream.getvalue() and time.monotonic() < end:
        time.sleep(0.02)
    assert "button 'echo broken >&2; exit 3' exited 3: broken" in stream.getvalue()


def test_sound_player_commands(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: f'/usr/bin/{name}' if name in ('paplay', 'canberra-gtk-play')
                        else None)
    player = SoundPlayer()
    assert player.command('/a/b.oga') == ['paplay', '/a/b.oga']
    assert player.command('file:///a/b.oga')[1] == '/a/b.oga'
    assert player.command('bell') == ['/usr/bin/canberra-gtk-play', '-i', 'bell']


def test_sound_player_without_players(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    stream = io.StringIO()
    SoundPlayer(EventLog(stream=stream, color='never'))('bell')
    assert 'no player for sound' in stream.getvalue()


@pytest.mark.parametrize('settings, hints, expected', [
    ({}, {'sound-name': 'bell'}, 'bell'),
    ({}, {'sound-file': '/a.oga', 'sound-name': 'bell'}, '/a.oga'),
    ({}, {'sound-name': 'bell', 'suppress-sound': True}, None),
    ({'sound': 'ding'}, {'suppress-sound': True}, 'ding'),
    ({'sound': False}, {'sound-name': 'bell'}, None),
    ({}, {}, None),
])
def test_sound_for(settings, hints, expected):
    n = Notification(hints=hints)
    n.settings = settings
    assert sound_for(n) == expected


def test_xidle_without_display(monkeypatch):
    monkeypatch.delenv('DISPLAY', raising=False)
    assert XIdle()() == 0


@pytest.mark.skipif(not shutil.which('Xvfb'), reason='needs Xvfb')
def test_xidle_under_xvfb():
    display = f':{70 + os.getpid() % 9}'
    xvfb = subprocess.Popen(['Xvfb', display], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.7)
        code = 'from notifyd.effects import XIdle; import time; i = XIdle(); a = i(); time.sleep(0.3); print(a, i())'
        result = subprocess.run([sys.executable, '-c', code], env={**os.environ, 'DISPLAY': display,
                                'PYTHONPATH': os.path.join(os.path.dirname(__file__), '..', 'src')},
                                capture_output=True, text=True, timeout=10, check=False)
        first, second = map(int, result.stdout.split())
        assert second >= first + 200
    finally:
        xvfb.terminate()
        xvfb.wait(5)


def fake_proc(tmp_path, comms):
    proc = tmp_path / 'proc'
    for pid, comm in enumerate(comms, start=100):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / 'comm').write_text(comm + '\n')
    (proc / 'self').mkdir(parents=True, exist_ok=True)
    return proc


def test_lock_state_finds_a_locker_process(tmp_path):
    from notifyd.effects import LockState
    proc = fake_proc(tmp_path, ['zsh', 'i3', 'i3lock'])
    assert LockState(proc=proc)() is True
    assert LockState(['xsecurelock'], proc=proc)() is False
    assert LockState([], proc=proc)() is False
    long_name = fake_proc(tmp_path / 'b', ['betterlockscree'])         # comm is cut to 15 characters
    assert LockState(proc=long_name)() is True
    assert LockState(proc=tmp_path / 'missing')() is False


def test_lock_state_reads_logind(tmp_path):
    from notifyd.effects import LockState

    class Connection:
        def __init__(self, locked):
            self.locked = locked

        def call_sync(self, *_args):
            from gi.repository import GLib
            return GLib.Variant('(v)', (GLib.Variant('b', self.locked),))
    proc = fake_proc(tmp_path, ['zsh'])
    assert LockState(proc=proc, connection=Connection(True))() is True
    assert LockState(proc=proc, connection=Connection(False))() is False


def test_runner_adds_the_event_environment(tmp_path):
    out = tmp_path / 'event'
    proc = Runner()(f'printf "%s %s" "$NOTIFYD_EVENT" "$NOTIFYD_ACTION" > {out}', sample(),
                    env={'NOTIFYD_EVENT': 'action', 'NOTIFYD_ACTION': 'reply'})
    proc.wait(5)
    assert out.read_text() == 'action reply'
