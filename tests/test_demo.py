"""tools/demo.sh: the whole tour on a private daemon (log only), checked against the daemon's event log."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / 'tools' / 'demo.sh'

needs_bus = pytest.mark.skipif(not shutil.which('dbus-run-session'), reason='needs dbus-run-session')


def run_demo(tmp_path, *args, timeout=120):
    env = {**os.environ, 'NO_COLOR': '1', 'TMPDIR': str(tmp_path), 'XDG_RUNTIME_DIR': str(tmp_path)}
    for name in ('DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NOTIFYD_DEMO_BUS'):
        env.pop(name, None)
    return subprocess.run(['bash', str(DEMO), *args], env=env, capture_output=True, text=True, timeout=timeout,
                          check=False)


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.startswith('{')]


@pytest.fixture(scope='module')
def tour(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp('demo')
    log = tmp_path / 'events.json'
    result = run_demo(tmp_path, '--fast', '--renderer', 'none', '--log', str(log))
    return result, events(log) if log.exists() else [], tmp_path


@needs_bus
def test_the_tour_runs_every_section(tour):
    result, log, tmp_path = tour
    assert result.returncode == 0, result.stdout + result.stderr
    for title in ('Fields', 'Urgency', 'Markup', 'Pictures', 'Progress', 'Actions', 'Replacing', 'Stacking',
                  'Styles', 'Rules', 'Presets', 'notification center'):
        assert title in result.stdout
    assert result.stdout.rstrip().endswith('done')
    assert 'gvfs' not in result.stderr and "can't be made" not in result.stderr
    assert list(tmp_path.glob('notifyd-demo.*')) == []           # its temporary directory is removed
    shown = {e['n']['summary']: e['n'] for e in log if e['event'] in ('shown', 'replaced')}
    assert {'Just a summary', 'Night Drive', 'Downloading', 'Lunch at noon?', 'Backup'} <= set(shown)
    assert shown['Night Drive']['title_end'].endswith('★★★★☆') and shown['Night Drive']['image'].startswith('file://')
    assert [shown[s]['urgency'] for s in ('Low urgency', 'Normal urgency', 'Critical urgency')] == \
        ['low', 'normal', 'critical']


@needs_bus
def test_the_tour_fires_every_rule(tour):
    result, log, _ = tour
    fired = {rule for e in log if e.get('n') for rule in e['n']['rules']}
    assert fired == {'battery', 'player', 'hook', 'do-not-disturb', 'chat', 'downloads', 'quick', 'card', 'styled'}
    battery = next(e['n'] for e in log if e['event'] == 'shown' and e['n']['rules'] == ['battery'])
    assert battery['summary'] == 'Battery at 7%' and battery['urgency'] == 'critical'
    assert [e['n']['summary'] for e in log if e['event'] == 'suppressed'] == ['You will not see this']
    assert 'run.log: Hooked (hook)' in result.stdout


@needs_bus
def test_the_tour_replaces_stacks_and_acts(tour):
    result, log, _ = tour
    assert max(e['n']['duplicates'] for e in log if e.get('n') and e['n']['summary'] == 'Same notification') == 3
    chat = [e for e in log if e.get('n') and e['n']['app'] == 'demo-chat' and e['event'] in ('shown', 'replaced')]
    assert [e['event'] for e in chat] == ['shown', 'replaced', 'replaced'] and chat[-1]['n']['duplicates'] == 3
    downloads = [e['event'] for e in log if e.get('n') and e['n']['summary'] == 'Downloading']
    assert downloads.count('shown') == 1 and downloads.count('replaced') == 7
    assert [e['action'] for e in log if e['event'] == 'action'] == ['yes']
    assert 'notifyd-send printed: yes' in result.stdout


@needs_bus
def test_one_section(tmp_path):
    log = tmp_path / 'events.json'
    result = run_demo(tmp_path, '--fast', '--renderer', 'none', '--log', str(log), '--section', 'urgency')
    assert result.returncode == 0, result.stderr
    assert 'Urgency' in result.stdout and 'Fields' not in result.stdout
    assert [e['n']['urgency'] for e in events(log) if e['event'] == 'shown'] == ['low', 'normal', 'critical']


def test_options(tmp_path):
    result = run_demo(tmp_path, '--list')
    assert result.returncode == 0 and result.stdout.split()[:3] == ['fields', 'urgency', 'markup']
    result = run_demo(tmp_path, '--help')
    assert result.returncode == 0 and result.stdout.startswith('usage: tools/demo.sh')
    for args, message in ((['--section', 'nope'], "no section 'nope'"), (['--bogus'], 'unknown option'),
                          (['--renderer', 'qt'], 'gtk or none')):
        result = run_demo(tmp_path, *args)
        assert result.returncode == 2 and message in result.stderr
    result = run_demo(tmp_path, '--fast')                        # gtk with no DISPLAY
    assert result.returncode == 1 and 'no DISPLAY' in result.stderr
