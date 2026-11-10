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


def test_ctl_without_notifyd(bus):
    result = run(bus, 'ctl', 'status', check=False)
    assert result.returncode == 1 and 'notifyd is not running' in result.stderr


@pytest.mark.skipif(not shutil.which('Xvfb'), reason='needs Xvfb')
def test_gtk_renderer_under_xvfb(bus, daemon, tmp_path):
    display = f':{90 + os.getpid() % 9}'
    xvfb = subprocess.Popen(['Xvfb', display, '-screen', '0', '1280x720x24'], stdout=subprocess.DEVNULL,
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
