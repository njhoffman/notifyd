"""The daemon end to end on a private session bus: notifyd daemon (log only, and GTK under Xvfb),
notifyd send, notifyd ctl and gdbus as the clients."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from notifyd import __version__
from notifyd.config import load_config

ROOT = Path(__file__).resolve().parent.parent
NOTIFYD = [sys.executable, str(ROOT / 'bin' / 'notifyd')]

pytestmark = pytest.mark.skipif(not shutil.which('dbus-daemon'), reason='needs dbus-daemon')


@pytest.fixture
def bus(tmp_path):
    proc = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address=1', '--nosyslog'],
                            stdout=subprocess.PIPE, text=True)
    address = proc.stdout.readline().strip()
    env = {**os.environ, 'DBUS_SESSION_BUS_ADDRESS': address, 'GIO_USE_VFS': 'local',
           'XDG_RUNTIME_DIR': str(tmp_path), 'XDG_CONFIG_HOME': str(tmp_path / 'config'), 'NO_COLOR': '1',
           'XDG_STATE_HOME': str(tmp_path / 'state')}
    env.pop('DISPLAY', None)
    yield env
    proc.terminate()
    proc.wait(5)


def run(env, *args, check=True):
    result = subprocess.run([*NOTIFYD, *args], env=env, capture_output=True, text=True, timeout=20)
    if check:
        assert result.returncode == 0, result.stderr
    return result


def wait_until(predicate, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def daemon(bus, tmp_path):
    def start(config='', *extra, env=None):
        env = env or bus
        path = tmp_path / 'notifyd.yaml'
        path.write_text(config)
        log = open(tmp_path / 'daemon.log', 'w')  # noqa: SIM115 (the daemon writes to it)
        proc = subprocess.Popen([*NOTIFYD, 'daemon', '-c', str(path), '--log-format', 'json', *extra],
                                env=env, stdout=log, stderr=log)
        started.append(proc)
        ready = wait_until(lambda: proc.poll() is not None or run(env, 'ctl', 'status', check=False).returncode == 0)
        assert ready and proc.poll() is None, (tmp_path / 'daemon.log').read_text()
        return proc
    started = []
    yield start
    for proc in started:
        proc.terminate()
        proc.wait(5)


def events(tmp_path):
    lines = (tmp_path / 'daemon.log').read_text().splitlines()
    return [json.loads(line) for line in lines if line.startswith('{')]


def gdbus(env, method, *args):
    return subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.Notifications',
                           '--object-path', '/org/freedesktop/Notifications', '--method',
                           f'org.freedesktop.Notifications.{method}', *args],
                          env=env, capture_output=True, text=True, timeout=10)


def test_server_information_and_capabilities(bus, daemon):
    daemon('', '--renderer', 'none')
    assert gdbus(bus, 'GetServerInformation').stdout.strip() == f"('notifyd', 'njhoffman', '{__version__}', '1.2')"
    caps = gdbus(bus, 'GetCapabilities').stdout
    for cap in ('body-markup', 'actions', 'x-dunst-stack-tag', 'persistence'):
        assert cap in caps


def test_send_replace_tag_and_name(bus, daemon, tmp_path):
    daemon('rules:\n  - match: {app: backdrop}\n    timeout: 12s\n', '--renderer', 'none')
    first = run(bus, 'send', '-p', '-a', 'backdrop', '-u', 'low', 'one').stdout.strip()
    assert run(bus, 'send', '-p', '-r', first, '-a', 'backdrop', 'two').stdout.strip() == first
    tagged = run(bus, 'send', '-p', '--tag', 'vol', '-a', 'Volume', '10%').stdout.strip()
    assert run(bus, 'send', '-p', '--tag', 'vol', '-a', 'Volume', '20%').stdout.strip() == tagged
    named = run(bus, 'send', '-p', '--replace-name', 'job', 'start').stdout.strip()
    assert run(bus, 'send', '-p', '--replaces-process', 'job', 'done').stdout.strip() == named
    status = dict(line.split(None, 1) for line in run(bus, 'ctl', 'status').stdout.splitlines())
    assert status['displayed'] == '3'
    shown = [e for e in events(tmp_path) if e['event'] in ('shown', 'replaced')]
    assert [e['event'] for e in shown] == ['shown', 'replaced', 'shown', 'replaced', 'shown', 'replaced']
    assert shown[0]['n']['timeout'] == 12000 and shown[0]['n']['rules'] == ['backdrop']


def test_close_history_pause_and_reload(bus, daemon, tmp_path):
    daemon('timeout: 30s\n', '--renderer', 'none')
    nid = run(bus, 'send', '-p', '-a', 'app', 'hello', '<b>world</b>').stdout.strip()
    run(bus, 'ctl', 'close', nid)
    history = json.loads(run(bus, 'ctl', 'history', '--json').stdout)
    assert history[-1]['summary'] == 'hello' and history[-1]['closed'] == 'closed'
    # who sent it: the sending process (python3 here, running bin/notifyd) and its parent (this test)
    sender = history[-1]['sender']
    assert sender['pid'] > 0 and sender['process'] == os.path.basename(sys.executable)[:15]
    assert sender['parent']
    run(bus, 'send', '-a', 'app', '-h', 'string:desktop-entry:musicman', 'from an app')
    shown = [e for e in events(tmp_path) if e['event'] == 'shown'][-1]
    assert shown['n']['sender']['desktop_entry'] == 'musicman'
    assert 'hello' in run(bus, 'ctl', 'history').stdout
    assert run(bus, 'ctl', 'pause').stdout.strip() == 'paused'
    run(bus, 'send', '-a', 'app', 'held')
    assert run(bus, 'ctl', 'resume').stdout.strip() == 'resumed'
    assert [e['event'] for e in events(tmp_path)][-2:] == ['resumed', 'shown']
    (tmp_path / 'notifyd.yaml').write_text('timeout: [broken\n')
    result = run(bus, 'ctl', 'reload', check=False)
    assert result.returncode == 1 and result.stdout.startswith('error')
    (tmp_path / 'notifyd.yaml').write_text('preset: card\n')
    assert run(bus, 'ctl', 'reload').stdout.startswith('reloaded')
    assert 'card' in run(bus, 'ctl', 'status').stdout


def test_actions_print_the_chosen_key(bus, daemon, tmp_path):
    daemon('', '--renderer', 'none')
    sender = subprocess.Popen([*NOTIFYD, 'send', '-p', '-a', 'displayd', '-A', 'change=Change layout', 'Monitors'],
                              env=bus, stdout=subprocess.PIPE, text=True)
    nid = sender.stdout.readline().strip()
    typo = subprocess.run([*NOTIFYD, 'ctl', 'action', nid, 'chnage'], env=bus, capture_output=True, text=True)
    assert typo.returncode == 1 and 'with that action' in typo.stderr   # nothing signalled, still open
    run(bus, 'ctl', 'action', nid, 'change')
    assert sender.wait(10) == 0
    assert sender.stdout.read().strip() == 'change'
    assert [e['event'] for e in events(tmp_path)][-2:] == ['action', 'dismissed']


def test_wait_returns_when_closed(bus, daemon):
    daemon('', '--renderer', 'none')
    sender = subprocess.Popen([*NOTIFYD, 'send', '-p', '-w', 'waiting'], env=bus, stdout=subprocess.PIPE, text=True)
    nid = sender.stdout.readline().strip()
    run(bus, 'ctl', 'close', nid)
    assert sender.wait(10) == 0


def test_wait_ends_when_the_daemon_dies(bus, daemon):
    proc = daemon('', '--renderer', 'none')
    sender = subprocess.Popen([*NOTIFYD, 'send', '-p', '-w', 'waiting'], env=bus, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    assert sender.stdout.readline().strip()
    proc.kill()                                   # no NotificationClosed: before, send waited forever
    proc.wait(5)
    assert sender.wait(10) == 1
    assert 'the notification daemon went away' in sender.stderr.read()


def test_second_daemon_names_the_owner(bus, daemon, tmp_path):
    daemon('', '--renderer', 'none')
    other = subprocess.run([*NOTIFYD, 'daemon', '--renderer', 'none', '--log-format', 'pretty'], env=bus,
                           capture_output=True, text=True, timeout=20)
    assert other.returncode == 1
    assert 'cannot own org.freedesktop.Notifications: owned by notifyd' in other.stderr


def test_list_mode_log_file_and_persistent_history(bus, daemon, tmp_path):
    log_file = tmp_path / 'logs' / 'notifyd.log'
    config = f'log: {{file: {log_file}}}\nrules:\n  - match: {{mode: dnd, urgency: [low, normal]}}\n    skip: true\n'
    proc = daemon(config, '--renderer', 'none')
    shown = run(bus, 'send', '-p', '-a', 'mail', 'New mail').stdout.strip()
    listing = run(bus, 'ctl', 'list', '--ids').stdout
    assert listing == f'{shown}\tmail: New mail\n'
    assert json.loads(run(bus, 'ctl', 'list', '--json').stdout)[0]['state'] == 'shown'
    assert run(bus, 'ctl', 'mode').stdout.strip() == 'normal'
    assert run(bus, 'ctl', 'mode', 'dnd').stdout.strip() == 'dnd'
    run(bus, 'send', '-a', 'chat', 'hidden while dnd')
    assert [e['event'] for e in events(tmp_path)][-1] == 'suppressed'
    run(bus, 'ctl', 'close', shown)
    proc.terminate()
    proc.wait(5)
    text = log_file.read_text()
    assert 'suppressed' in text and 'hidden while dnd' in text and '\033[' not in text
    daemon('', '--renderer', 'none')
    summaries = [e['summary'] for e in json.loads(run(bus, 'ctl', 'history', '--json').stdout)]
    assert summaries[:2] == ['hidden while dnd', 'New mail']


def test_protocol_compliance(bus, daemon):
    daemon('', '--renderer', 'none')
    result = subprocess.run([sys.executable, str(ROOT / 'tools' / 'compliance.py'), '--json'], env=bus,
                            capture_output=True, text=True, timeout=30, check=False)
    checks = json.loads(result.stdout)
    failed = [key for key, value in checks.items() if value is False]
    assert failed == []


def test_ctl_forget_and_center_without_display(bus, daemon):
    daemon('', '--renderer', 'none')
    for name in ('a', 'b'):
        run(bus, 'ctl', 'close', run(bus, 'send', '-p', name).stdout.strip())
    assert run(bus, 'ctl', 'forget', '1').stdout.strip() == 'forgot 1'
    assert [e['summary'] for e in json.loads(run(bus, 'ctl', 'history', '--json').stdout)] == ['b']
    assert run(bus, 'ctl', 'forget', '--all').stdout.strip() == 'forgot 1'
    assert run(bus, 'ctl', 'forget', check=False).returncode == 1
    assert run(bus, 'ctl', 'center').stdout.strip() == 'hidden'     # no renderer, no center


def test_bar_follows_pause_and_mode(bus, daemon):
    daemon('', '--renderer', 'none')
    bar = subprocess.Popen([*NOTIFYD, 'ctl', 'bar', '--follow', '--idle', 'ok'], env=bus, stdout=subprocess.PIPE,
                           text=True)
    try:
        assert bar.stdout.readline().strip() == 'ok'
        run(bus, 'ctl', 'pause')
        assert bar.stdout.readline().strip() == 'dnd'
        run(bus, 'send', 'held back')
        assert bar.stdout.readline().strip() == 'dnd 1'
        run(bus, 'ctl', 'resume')
        assert bar.stdout.readline().strip() == 'ok'
        run(bus, 'ctl', 'mode', 'presentation')
        assert bar.stdout.readline().strip() == 'presentation'
    finally:
        bar.terminate()
        bar.wait(5)


def test_ctl_without_notifyd(bus):
    result = run(bus, 'ctl', 'status', check=False)
    assert result.returncode == 1 and 'notifyd is not running' in result.stderr


@pytest.mark.skipif(not shutil.which('Xvfb'), reason='needs Xvfb')
def test_gtk_renderer_under_xvfb(bus, daemon, tmp_path):
    display = f':{90 + os.getpid() % 9}'
    # tall enough for all the previews at once: what does not fit waits (and `send -A` would wait on it)
    xvfb = subprocess.Popen(['Xvfb', display, '-screen', '0', '1280x4000x24'], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        env = {**bus, 'DISPLAY': display}
        assert wait_until(lambda: subprocess.run(['xdpyinfo', '-display', display], capture_output=True)
                          .returncode == 0 if shutil.which('xdpyinfo') else time.sleep(1) or True)
        daemon('', env=env)
        run(env, 'ctl', 'preview', 'deadd')
        run(env, 'ctl', 'preview', 'card')
        run(env, 'send', '-a', 'test', '-A', 'a=One', '--progress', '40', '--image', '/nonexistent.png',
            'Title & more', '<span class="accent">x</span> <b>unclosed')
        time.sleep(0.5)
        log = events(tmp_path)
        assert not [e for e in log if e['event'] == 'error'], log
        assert sum(e['event'] in ('shown', 'replaced') for e in log) >= 11
        if shutil.which('xwininfo'):
            tree = subprocess.run(['xwininfo', '-root', '-tree', '-display', display], capture_output=True,
                                  text=True).stdout
            assert '"notifyd": ("notifyd" "Notifyd")' in tree
    finally:
        xvfb.terminate()
        xvfb.wait(5)


RENDERER_SCRIPT = r"""
import io, sys
sys.path.insert(0, sys.argv[1])
from notifyd.config import load_config
from notifyd.log import EventLog
from notifyd.manager import Manager
from notifyd.render.gtk import GtkRenderer
sys.path.insert(0, sys.argv[2])
from conftest import FakeScheduler

stream = io.StringIO()
log = EventLog(stream=stream, color='never')
manager = Manager(load_config(text='position: {follow: none}'), log, FakeScheduler())
renderer = GtkRenderer(manager, log)
manager.renderer = renderer
first = manager.notify('a', summary='one')
manager.notify('a', summary='same')
manager.notify('a', summary='same')                      # a duplicate: one window, counted
assert len(renderer.windows) == 2, renderer.windows
newest = manager.displayed[0]
assert manager.active[newest].duplicates == 2
x1, y1 = renderer.windows[newest].get_position()
assert renderer.windows[first].get_position()[1] > y1   # older ones go below
assert renderer.area is not None
renderer.screen.emit('monitors-changed')
renderer.center.toggle('show')
assert renderer.center.visible
manager.close(first)
assert len(manager.history) == 1
manager.forget()
assert len(manager.history) == 0
renderer.center.refresh()
assert renderer.center.toggle() is False
assert 'monitors changed' in stream.getvalue()
assert renderer.area is not None                        # laid out again on the new geometry

# links: GtkLabel renders them, only http(s)/mailto open, a link click never dismisses
from gi.repository import Gio, Gtk, Pango
import notifyd.render.gtk as gtk_module
opened = []
gtk_module.Gio.AppInfo.launch_default_for_uri = lambda uri, ctx: opened.append(uri)
def labels(widget):
    found = [widget] if isinstance(widget, Gtk.Label) else []
    for child in (widget.get_children() if isinstance(widget, Gtk.Container) else []):
        found += labels(child)
    return found
linked = manager.notify('a', summary='s', body='see <a href="https://x.org/?a=1&b=2">the site</a> & <b>more</b>')
body = [l for l in labels(renderer.windows[linked]) if 'the site' in l.get_text()]
assert body and body[0].get_text() == 'see the site & more', [l.get_text() for l in labels(renderer.windows[linked])]
assert not [l for l in labels(renderer.windows[linked]) if l.get_text() == 'markup error']
assert body[0].emit('activate-link', 'file:///etc/passwd') is True
assert body[0].emit('activate-link', 'https://x.org/?a=1&b=2') is True
assert opened == ['https://x.org/?a=1&b=2'], opened
assert 'link not opened' in stream.getvalue()
class Click: button, time = 1, 4242
renderer.link_clicked = 4242
renderer.on_click(None, Click, linked)
assert linked in manager.displayed                       # the click that opened a link did not dismiss it
broken = manager.notify('a', summary='s', body='<agent-message>x</agent-message>')
assert [l for l in labels(renderer.windows[broken]) if l.get_text() == 'markup error']

# a malformed image-data hint is refused with a warning instead of crashing GdkPixbuf
bad = manager.notify('a', summary='bad image', hints={'image-data': (10, 10, 4000000, False, 8, 3, bytes(400))})
assert bad in renderer.windows
assert f'#{bad} image-data not drawn: image-data: 400 bytes of pixels' in stream.getvalue()

# what does not fit in the work area (720 px here) waits, oldest first, and shows when room frees up
manager.close_all()
long = 'line\n' * 12
first_tall = manager.notify('a', summary='tall 1', body=long)
assert renderer.fits(manager.active[first_tall]) is True        # the first always fits
tall = [first_tall] + [manager.notify('a', summary=f'tall {i}', body=long) for i in range(2, 9)]
shown, waiting = list(manager.displayed), list(manager.waiting)
assert shown and waiting and len(shown) + len(waiting) == len(tall), (shown, waiting)
assert waiting == tall[len(shown):]                              # in arrival order
bottom = max(w.get_position()[1] + w.get_preferred_size()[1].height for w in renderer.windows.values())
assert bottom <= renderer.area.y + renderer.area.height, bottom
assert renderer.measured is None                                # the measuring window went to show() or away
manager.close(shown[-1])
assert manager.displayed[0] == waiting[0] and manager.waiting == waiting[1:]
manager.close_all()
manager.config.settings['title'] = {'max-lines': 2}
capped = manager.notify('b', summary=' '.join(['word'] * 400), body='x')
title = [l for l in labels(renderer.windows[capped]) if l.get_text().startswith('word word')]
assert title and title[0].get_lines() == 2 and title[0].get_ellipsize() == Pango.EllipsizeMode.END
manager.close_all()

# fades: per window, a close during the fade-in replaces it, and a rule's fade.out counts
import time as _time
from gi.repository import GLib
def spin(ms):
    end = _time.monotonic() + ms / 1000
    while _time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        _time.sleep(0.005)
manager.close_all()
spin(50)
renderer.fades.clear()
quick = manager.notify('a', summary='fade in only', extra={'fade': {'in': 300, 'out': 0}})
window = renderer.windows[quick]
assert window in renderer.fades
manager.close(quick)                               # no fade-out: the fade-in stops with the window
assert window not in renderer.fades and renderer.fades == {}
slow = manager.notify('a', summary='rule fade out', extra={'fade': {'in': 300, 'out': 120}})
window = renderer.windows[slow]
spin(40)
manager.close(slow)                                # mid fade-in: one fade, now going out
assert list(renderer.fades) == [window]
spin(400)
assert renderer.fades == {} and renderer.fade_out == {}

# fullscreen: the focused window covering the stack's monitor (no window manager here, so it is faked)
import types
manager.close_all()
spin(50)
real_screen = renderer.screen
cover = Gtk.Window(type=Gtk.WindowType.POPUP)
cover.move(0, 0)
cover.set_size_request(1280, 720)
cover.show_all()
spin(100)
renderer.screen = types.SimpleNamespace(get_active_window=lambda: cover.get_window())
assert renderer.fullscreen() is True
cover.resize(1, 1)
cover.set_size_request(640, 400)
spin(100)
assert renderer.fullscreen() is False
renderer.screen = types.SimpleNamespace(get_active_window=lambda: None)
assert renderer.fullscreen() is False
renderer.screen = real_screen
cover.destroy()

manager.close_all()
assert renderer.area is None and renderer.windows == {}
print('ok', x1, y1)
"""


@pytest.mark.skipif(not shutil.which('Xvfb'), reason='needs Xvfb')
def test_renderer_relayout_on_monitor_change_and_duplicate_badge():
    display = f':{80 + os.getpid() % 9}'
    xvfb = subprocess.Popen(['Xvfb', display, '-screen', '0', '1280x720x24'], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.7)
        result = subprocess.run([sys.executable, '-c', RENDERER_SCRIPT, str(ROOT / 'src'), str(ROOT / 'tests')],
                                env={**os.environ, 'DISPLAY': display, 'GIO_USE_VFS': 'local'},
                                capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        _ok, x, y = result.stdout.split()
        assert (int(x), int(y)) == (1280 - 5 - 350, 30)    # newest at top-right: margin [30, 5], width 350
    finally:
        xvfb.terminate()
        xvfb.wait(5)


def test_mpris_now_playing_and_commands(bus):
    from gi.repository import Gio

    from notifyd import mpris
    players = []
    try:
        for status, title, name in (('Paused', 'Old', 'paused'), ('Playing', 'New', 'playing')):
            proc = subprocess.Popen([sys.executable, str(ROOT / 'tests' / 'fake_mpris.py'), status, title, name],
                                    env=bus, stdout=subprocess.PIPE, text=True)
            players.append(proc)
            assert proc.stdout.readline().strip() == 'ready'
        connection = Gio.DBusConnection.new_for_address_sync(
            bus['DBUS_SESSION_BUS_ADDRESS'],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        assert mpris.players(connection) == ['org.mpris.MediaPlayer2.paused', 'org.mpris.MediaPlayer2.playing']
        playing = mpris.now_playing(connection)
        assert playing['title'] == 'New' and playing['artist'] == 'Artist' and playing['player'] == 'playing'
        mpris.command(connection, playing['name'], 'Next')
        connection.flush_sync(None)
        assert players[1].stdout.readline().strip() == 'Next'
    finally:
        for proc in players:
            proc.terminate()
            proc.wait(5)


def test_check_lock_follows_the_setting():
    from notifyd.daemon import Daemon
    calls = []

    class Manager:
        locked = False
        config = load_config(text='lock: {pause: true, processes: [mylocker]}')

        def set_locked(self, on):
            calls.append(on)
            Manager.locked = on
    daemon = Daemon()
    daemon.manager = Manager()
    state = [True]
    daemon.lock_state = (('mylocker',), lambda: state[0])
    assert daemon.check_lock() is True and calls == [True]
    state[0] = False
    daemon.check_lock()
    assert calls == [True, False]
    state[0] = True
    daemon.check_lock()
    daemon.manager.config = load_config(text='')                      # lock.pause off: unlock, stop checking
    daemon.check_lock()
    assert calls == [True, False, True, False]
