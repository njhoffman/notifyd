"""tools/preview.sh: the presets side by side (one private daemon per column) and one at a time."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from notifyd.config import preset_names

ROOT = Path(__file__).resolve().parent.parent
PREVIEW = ROOT / 'tools' / 'preview.sh'

needs_bus = pytest.mark.skipif(not shutil.which('dbus-run-session'), reason='needs dbus-run-session')


def run_preview(tmp_path, *args):
    env = {**os.environ, 'NO_COLOR': '1', 'TMPDIR': str(tmp_path), 'XDG_RUNTIME_DIR': str(tmp_path)}
    for name in ('DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NOTIFYD_PRIVATE_DIR', 'NOTIFYD_WITH_DAEMON_BUS'):
        env.pop(name, None)
    return subprocess.run(['bash', str(PREVIEW), *args], env=env, capture_output=True, text=True, timeout=120,
                          check=False)


def shown(path):
    events = [json.loads(line) for line in path.read_text().splitlines() if line.startswith('{')]
    return [e['n'] for e in events if e['event'] == 'shown']


@needs_bus
def test_side_by_side_shows_every_preset_once_in_its_own_style(tmp_path):
    logs = tmp_path / 'logs'
    result = run_preview(tmp_path, '--renderer', 'none', '--seconds', '0', '--log-dir', str(logs))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ''
    columns = sorted(logs.glob('column-*.json'))
    assert columns
    cards = [n for column in columns for n in reversed(shown(column))]      # each column's newest is on top
    assert [n['app'] for n in cards] == preset_names()                      # all, in order, top to bottom
    for n in cards:
        assert n['rules'] == [f'preset-{n["app"]}'] and n['urgency'] == 'normal'
        assert n['title_end'] == '★ 4.5' and n['actions'] == ['open', 'later']
    assert list(tmp_path.glob('notifyd-*')) == []                          # temporary directories removed


@needs_bus
def test_side_by_side_takes_presets_and_urgency(tmp_path):
    logs = tmp_path / 'logs'
    result = run_preview(tmp_path, '--renderer', 'none', '--seconds', '0', '--log-dir', str(logs),
                         '--urgency', 'critical', 'minimal', 'card')
    assert result.returncode == 0, result.stderr
    cards = [n for column in sorted(logs.glob('column-*.json')) for n in reversed(shown(column))]
    assert [(n['app'], n['urgency']) for n in cards] == [('minimal', 'critical'), ('card', 'critical')]


@needs_bus
def test_each_shows_the_samples_of_one_preset_at_a_time(tmp_path):
    logs = tmp_path / 'logs'
    result = run_preview(tmp_path, '--each', '--renderer', 'none', '--seconds', '0', '--log-dir', str(logs),
                         'dunst', 'minimal')
    assert result.returncode == 0, result.stderr
    assert ' 1/2  dunst' in result.stdout and ' 2/2  minimal' in result.stdout
    cards = shown(logs / 'each.json')
    assert len(cards) == 10 and len({n['app'] for n in cards}) > 1          # ctl preview's five samples, twice


def test_options(tmp_path):
    result = run_preview(tmp_path, '--help')
    assert result.returncode == 0 and result.stdout.startswith('usage: tools/preview.sh')
    for args, message in ((['nope'], "no preset 'nope'"), (['--bogus'], 'unknown option'),
                          (['--urgency', 'high'], 'low, normal or critical'), (['--seconds', 'x'], 'a number'),
                          (['--renderer', 'qt'], 'gtk or none')):
        result = run_preview(tmp_path, *args)
        assert result.returncode == 2 and message in result.stderr, (args, result.stderr)
