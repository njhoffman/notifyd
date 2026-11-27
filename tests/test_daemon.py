"""The daemon end to end on a private session bus: notifyd daemon (log only, and GTK under Xvfb),
notifyd send, notifyd ctl and gdbus as the clients."""

import json
import os
import re
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
    notices = [n for n in json.loads(run(bus, 'ctl', 'list', '--json').stdout) if n['stack_tag'] == 'notifyd-config']
    assert [n['summary'] for n in notices] == ['config error, kept the last good one']   # one, replaced
    (tmp_path / 'notifyd.yaml').write_text('preset: card\n')
    assert run(bus, 'ctl', 'reload').stdout.startswith('reloaded')
    assert 'card' in run(bus, 'ctl', 'status').stdout
    # it shows for 3 s (and the file watcher may have reloaded first), so look in the log, not on screen
    said = [e['n']['summary'] for e in events(tmp_path)
            if e['event'] in ('shown', 'replaced') and e.get('n', {}).get('stack_tag') == 'notifyd-config']
    assert said[-1] == 'config reloaded' and 'config error, kept the last good one' in said


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


NOTIFYD_SEND = [sys.executable, str(ROOT / 'bin' / 'notifyd-send')]


def test_notifyd_send_takes_notify_send_py_forms(bus, daemon, tmp_path):
    daemon("rules:\n  - name: hinted\n    match: {hint: {x-b: '2', x-c: 'true'}}\n", '--renderer', 'none')
    nid = subprocess.run([*NOTIFYD_SEND, '-p', '-a', 'py', '--title-end', '★ 4', '--fg', '#fff', 'Two\\nlines',
                          'a\\tb', '--hint', 'string:x-a:1', 'int:x-b:2', 'boolean:x-c:true'],
                         env=bus, capture_output=True, text=True, check=True).stdout.strip()
    shown = [e['n'] for e in events(tmp_path) if e['event'] == 'shown']
    assert shown[-1]['id'] == int(nid) and shown[-1]['rules'] == ['hinted']     # all three hints arrived
    assert shown[-1]['summary'] == 'Two\nlines' and shown[-1]['body'] == 'a\tb' and shown[-1]['title_end'] == '★ 4'
    # --replaces-id, and --action with several KEY:NAME values: the first chosen ends the wait
    sender = subprocess.Popen([*NOTIFYD_SEND, '-p', '--replaces-id', nid, 'Pick', '--action', 'yes:Yes', 'no:No'],
                              env=bus, stdout=subprocess.PIPE, text=True)
    assert sender.stdout.readline().strip() == nid
    listed = json.loads(run(bus, 'ctl', 'list', '--json').stdout)
    assert [n['actions'] for n in listed if n['id'] == int(nid)] == [['yes', 'no']]
    run(bus, 'ctl', 'action', nid, 'no')
    assert sender.wait(10) == 0 and sender.stdout.read().strip() == 'no'


def test_notifyd_send_dont_quit_on_action(bus, daemon):
    daemon('', '--renderer', 'none')
    sender = subprocess.Popen([*NOTIFYD_SEND, '-p', '--resident', '--dont-quit-on-action', '-A', 'a=A', '-A', 'b=B',
                               'Resident'], env=bus, stdout=subprocess.PIPE, text=True)
    nid = sender.stdout.readline().strip()
    run(bus, 'ctl', 'action', nid, 'a')
    assert sender.stdout.readline().strip() == 'a'
    assert sender.poll() is None                    # resident: still open, still waiting
    run(bus, 'ctl', 'action', nid, 'b')
    assert sender.stdout.readline().strip() == 'b'
    run(bus, 'ctl', 'close', nid)
    assert sender.wait(10) == 0


def test_notifyd_send_quits_on_the_first_action_of_a_resident_one(bus, daemon):
    daemon('', '--renderer', 'none')
    sender = subprocess.Popen([*NOTIFYD_SEND, '-p', '--resident', '-A', 'a=A', 'Resident'], env=bus,
                              stdout=subprocess.PIPE, text=True)
    nid = sender.stdout.readline().strip()
    run(bus, 'ctl', 'action', nid, 'a')
    assert sender.wait(10) == 0 and sender.stdout.read().strip() == 'a'
    assert [n['id'] for n in json.loads(run(bus, 'ctl', 'list', '--json').stdout)] == [int(nid)]   # still open


def test_second_daemon_names_the_owner(bus, daemon, tmp_path):
    daemon('', '--renderer', 'none')
    other = subprocess.run([*NOTIFYD, 'daemon', '--renderer', 'none', '--log-format', 'pretty'], env=bus,
                           capture_output=True, text=True, timeout=20)
    assert other.returncode == 1
    # it asks for two names; whichever refusal comes back first is the one it reports
    assert re.search(r'cannot own (org\.freedesktop\.Notifications|io\.github\.njhoffman\.Notifyd): '
                     r'owned by notifyd', other.stderr), other.stderr


def test_list_mode_log_file_and_persistent_history(bus, daemon, tmp_path):
    log_file = tmp_path / 'logs' / 'notifyd.log'
    # timeout never: on a loaded machine the steps below can outlast the 8 s default, and it would expire first
    config = (f'timeout: never\nlog: {{file: {log_file}}}\n'
              'rules:\n  - match: {mode: dnd, urgency: [low, normal]}\n    skip: true\n')
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
# an action: link invokes that notification's own action (here in the title end), never opens anything
acting = manager.notify('a', summary='s', actions=['default', 'Open', 'go', 'Go'],
                        hints={'x-notifyd-title-end': '<a href="action:default">proj</a>'})
end = [l for l in labels(renderer.windows[acting]) if l.get_text() == 'proj']
assert end, [l.get_text() for l in labels(renderer.windows[acting])]
invoked = []
real_invoke = manager.invoke
manager.invoke = lambda nid, key: invoked.append((nid, key)) or real_invoke(nid, key)
assert end[0].emit('activate-link', 'action:nope') is True
assert 'link action:nope: no such action' in stream.getvalue()
assert end[0].emit('activate-link', 'action:default') is True
manager.invoke = real_invoke
assert invoked == [(acting, 'nope'), (acting, 'default')], invoked
assert acting not in manager.active                       # invoking closed it, as a button does
assert len(opened) == 1
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

# the title-end hint: right of the title on its line, never cut, quieter by style; hiding the title hides it
ended = manager.notify('b', summary=' '.join(['word'] * 400), body='x',
                       hints={'x-notifyd-title-end': '<span foreground="#ff8fc7">h</span> 4.5'})
found = labels(renderer.windows[ended])
end = [l for l in found if l.get_text() == 'h 4.5']
title = [l for l in found if l.get_text().startswith('word word')]
assert end and title, [l.get_text() for l in found]
assert end[0].get_ellipsize() == Pango.EllipsizeMode.NONE and end[0].get_style_context().has_class('title-end')
assert end[0].get_parent() is title[0].get_parent()                       # the same line
assert end[0].get_parent().get_style_context().has_class('title-row')
assert not [l for l in found if l.get_text() == 'markup error']
broken = manager.notify('b', summary='t', hints={'x-notifyd-title-end': '<b>open'})
assert [l for l in labels(renderer.windows[broken]) if l.get_text() == 'markup error']
hidden = manager.notify('b', summary='t', hints={'x-notifyd-title-end': '4.5'}, extra={'hide': ['title']})
assert not [l for l in labels(renderer.windows[hidden]) if l.get_text() == '4.5']
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

# CSS of styles no window uses is pruned once enough of them gathered
manager.close_all()
spin(50)
for i in range(renderer.STYLE_SLACK + 3):
    manager.close(manager.notify('a', summary=f'c{i}', hints={'fgcolor': f'#0000{i:02x}'}))
assert len(renderer.styles) <= renderer.STYLE_SLACK + 2, len(renderer.styles)
kept = manager.notify('a', summary='kept', hints={'fgcolor': '#123456'})
for i in range(renderer.STYLE_SLACK + 3):
    manager.close(manager.notify('a', summary=f'd{i}', hints={'fgcolor': f'#00ff{i:02x}'}))
assert renderer.window_scopes[kept] in renderer.styles        # an open window keeps its style
manager.close_all()
assert renderer.window_scopes == {}

# symbolic icons take the given color; a name the theme lacks falls back to a shorter one
from notifyd.render.gtk import load_pixbuf, rgba
theme = Gtk.IconTheme.get_default()
symbolic = sorted(n for n in theme.list_icons(None) if n.endswith('-symbolic'))
if symbolic:
    pixbuf = load_pixbuf(symbolic[0], 24, rgba('#ff0000'))
    data, channels = pixbuf.get_pixels(), pixbuf.get_n_channels()
    drawn = [data[i:i + channels] for i in range(0, len(data), channels) if channels == 4 and data[i + 3] > 200]
    assert drawn and all(px[0] > 200 and px[1] < 60 and px[2] < 60 for px in drawn), drawn[:3]
plain = sorted(n for n in theme.list_icons(None) if '-' in n and not n.endswith('-symbolic'))
if plain:
    assert load_pixbuf(plain[0] + '-nosuchvariant', 24) is not None
assert load_pixbuf('zzz-no-such-icon', 24) is None

# hovering a popup whose body max-lines cut shows all of it, and it shrinks back on leave
manager.close_all()
spin(50)
manager.config.settings['body'] = {'max-lines': 2}
long_one = manager.notify('a', summary='long', body='\n'.join(f'line {i}' for i in range(10)))
short_one = manager.notify('a', summary='short', body='one line')
spin(150)
label, lines, _full, _capped = renderer.bodies[long_one]
assert label.get_text() == 'line 0\nline 1…'                    # many short lines are capped too
before = renderer.windows[long_one].get_preferred_size()[1].height
renderer.expand(long_one, True)
spin(100)
assert label.get_lines() == -1 and long_one in renderer.expanded and 'line 9' in label.get_text()
assert renderer.windows[long_one].get_preferred_size()[1].height > before
renderer.expand(long_one, False)
spin(100)
assert label.get_lines() == 2 and long_one not in renderer.expanded and label.get_text().endswith('…')
renderer.expand(short_one, True)                   # nothing cut: nothing to expand
assert short_one not in renderer.expanded
manager.close(long_one)
assert long_one not in renderer.bodies
manager.config.settings['body'] = {'max-lines': 6}

# the timeout bar drains with the timer (off by default)
manager.close_all()
plain = manager.notify('a', summary='no bar')
assert plain not in renderer.timeout_bars
manager.config.settings['timeout-bar'] = True
timed = manager.notify('a', summary='bar', extra={'timeout': 4000})
bar, timeout, _source = renderer.timeout_bars[timed]
assert timeout == 4000 and 'timeout-bar' in bar.get_style_context().list_classes()
manager.scheduler.advance(1000)
renderer.tick_timeout_bar(timed)
assert abs(bar.get_fraction() - 0.75) < 0.01, bar.get_fraction()
spin(100)
assert bar.get_allocated_height() == 2 and bar.color.to_string() == 'rgb(0,215,215)'   # thin and cyan
styled = manager.notify('a', summary='styled', extra={
    'timeout': 4000, 'style': {'timeout-bar': {'color': '#ff8800', 'height': 5}}})
spin(100)
styled_bar = renderer.timeout_bars[styled][0]
assert styled_bar.get_allocated_height() == 5 and styled_bar.color.to_string() == 'rgb(255,136,0)'
untimed = manager.notify('a', summary='never', extra={'timeout': 0})
assert untimed not in renderer.timeout_bars                 # nothing to count down
manager.close(timed)
assert timed not in renderer.timeout_bars
manager.config.settings['timeout-bar'] = False

# +N on the last popup while some wait for room (max-visible here)
manager.close_all()
manager.config.settings['max-visible'] = 2
ids = [manager.notify('a', summary=f'w{i}') for i in range(5)]
oldest_shown = manager.displayed[-1]
labels_now = {nid: (label.get_visible(), label.get_text()) for nid, label in renderer.waiting_labels.items()}
assert labels_now[oldest_shown] == (True, '+3'), labels_now
assert not labels_now[manager.displayed[0]][0]
manager.close(oldest_shown)                                  # one moves up: two wait now
assert renderer.waiting_labels[manager.displayed[-1]].get_text() == '+2'
manager.config.settings['indicate-waiting'] = False
renderer.waiting_changed()
assert not any(label.get_visible() for label in renderer.waiting_labels.values())
manager.config.settings['indicate-waiting'] = True
while manager.waiting:
    manager.close(manager.displayed[-1])
assert not any(label.get_visible() for label in renderer.waiting_labels.values())
manager.config.settings['max-visible'] = 6

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


def bare_server(sound):
    from notifyd.server import Server
    server = Server.__new__(Server)
    server.manager = type('M', (), {'sound': sound})()
    server.sent = []
    server._signal = lambda iface, name, value: server.sent.append((name, value.unpack()))
    return server


def test_sound_capability_only_with_a_player():
    from notifyd.effects import SoundPlayer
    player = SoundPlayer()
    player.file_player, player.name_player = None, '/usr/bin/canberra-gtk-play'
    assert 'sound' in bare_server(player).capabilities()
    player.name_player = None
    assert 'sound' not in bare_server(player).capabilities()
    assert 'sound' not in bare_server(lambda _name: None).capabilities()   # tests' plain callables


def test_activation_token_comes_before_action_invoked():
    server = bare_server(None)
    server.emit_action(7, 'open')
    (first, (nid, token)), second = server.sent
    assert first == 'ActivationToken' and nid == 7 and token.startswith('notifyd-') and token.endswith('_TIME0')
    assert second == ('ActionInvoked', (7, 'open'))


HIDPI_SCRIPT = r"""
import io, sys
sys.path.insert(0, sys.argv[1])
from notifyd.config import load_config
from notifyd.log import EventLog
from notifyd.manager import Manager
from notifyd.render.gtk import GtkRenderer
from gi.repository import Gtk
sys.path.insert(0, sys.argv[2])
from conftest import FakeScheduler

log = EventLog(stream=io.StringIO(), color='never')
manager = Manager(load_config(text='position: {follow: none}\nimage: {size: 60}'), log, FakeScheduler())
renderer = GtkRenderer(manager, log)
manager.renderer = renderer
assert renderer.scale() == 2, renderer.scale()
picture = (120, 120, 360, False, 8, 3, bytes(120 * 120 * 3))
nid = manager.notify('a', summary='s', body='b', hints={'image-data': picture})

def images(widget):
    found = [widget] if isinstance(widget, Gtk.Image) else []
    for child in (widget.get_children() if isinstance(widget, Gtk.Container) else []):
        found += images(child)
    return found
image, = images(renderer.windows[nid])
assert image.get_storage_type() == Gtk.ImageType.SURFACE, image.get_storage_type()
width = image.get_preferred_width()[1] - image.get_margin_start() - image.get_margin_end()
assert 58 <= width <= 62, width                 # 60 logical px, from 120 device px
print('ok')
"""


@pytest.mark.skipif(not shutil.which('Xvfb'), reason='needs Xvfb')
def test_pictures_are_sharp_at_scale_2():
    display = f':{60 + os.getpid() % 9}'
    xvfb = subprocess.Popen(['Xvfb', display, '-screen', '0', '1280x720x24'], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.7)
        result = subprocess.run([sys.executable, '-c', HIDPI_SCRIPT, str(ROOT / 'src'), str(ROOT / 'tests')],
                                env={**os.environ, 'DISPLAY': display, 'GDK_SCALE': '2', 'GIO_USE_VFS': 'local'},
                                capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'ok'
    finally:
        xvfb.terminate()
        xvfb.wait(5)


def test_players_follow_the_bus_without_blocking(bus):
    from gi.repository import Gio, GLib

    from notifyd import mpris
    players = []
    try:
        for status, title, name in (('Playing', 'Tab audio', 'firefox'), ('Paused', 'Song', 'spotify')):
            proc = subprocess.Popen([sys.executable, str(ROOT / 'tests' / 'fake_mpris.py'), status, title, name],
                                    env=bus, stdout=subprocess.PIPE, text=True)
            players.append(proc)
            assert proc.stdout.readline().strip() == 'ready'
        connection = Gio.DBusConnection.new_for_address_sync(
            bus['DBUS_SESSION_BUS_ADDRESS'],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        changes = []
        cache = mpris.Players(connection, on_change=lambda: changes.append(1))
        assert cache.now_playing() is None                       # nothing is waited for
        context = GLib.MainContext.default()
        end = time.monotonic() + 5
        while len(cache.props) < 2 and time.monotonic() < end:
            context.iteration(False)
            time.sleep(0.01)
        assert cache.now_playing()['player'] == 'firefox'          # playing beats paused
        assert cache.now_playing(ignore=['Firefox'])['title'] == 'Song'
        assert cache.now_playing(ignore=['firefox', 'spotify']) is None
        players[0].terminate()
        players[0].wait(5)
        end = time.monotonic() + 5
        while 'org.mpris.MediaPlayer2.firefox' in cache.props and time.monotonic() < end:
            context.iteration(False)
            time.sleep(0.01)
        assert cache.now_playing()['player'] == 'spotify' and changes
    finally:
        for proc in players:
            proc.terminate()
            proc.wait(5)


def test_editing_the_config_or_a_preset_reloads(bus, daemon, tmp_path):
    presets = Path(bus['XDG_CONFIG_HOME']) / 'notifyd' / 'presets'
    presets.mkdir(parents=True)
    (presets / 'mine.yaml').write_text('description: mine\nstyle: {background: "#000"}\n')
    daemon('preset: mine\n', '--renderer', 'none')

    def reloads():
        return sum(e['event'] == 'reload' for e in events(tmp_path))
    before = reloads()
    (presets / 'mine.yaml').write_text('description: mine\nstyle: {background: "#123"}\n')
    assert wait_until(lambda: reloads() > before), 'a preset edit reloads'
    before = reloads()
    (tmp_path / 'notifyd.yaml').write_text('preset: mine\ntimeout: 3s\n')
    assert wait_until(lambda: reloads() > before), 'a config edit reloads'
