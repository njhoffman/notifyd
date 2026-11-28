"""The GTK renderer's cards under Xvfb: each test runs a short script in a child process (GTK needs the
display before it is imported) against a Manager with a FakeScheduler, as the renderer test in
test_daemon.py does."""

import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(not shutil.which('Xvfb'), reason='needs Xvfb')

PRELUDE = r"""
import io, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
from conftest import FakeScheduler
from notifyd.config import load_config
from notifyd.log import EventLog
from notifyd.manager import Manager
from notifyd.render.gtk import GtkRenderer
from gi.repository import Gtk

stream = io.StringIO()
log = EventLog(stream=stream, color='never')

def setup(config='position: {follow: none}'):
    manager = Manager(load_config(text=config), log, FakeScheduler())
    renderer = GtkRenderer(manager, log)
    manager.renderer = renderer
    return manager, renderer

def widgets(widget, kind=Gtk.Widget):
    found = [widget] if isinstance(widget, kind) else []
    for child in (widget.get_children() if isinstance(widget, Gtk.Container) else []):
        found += widgets(child, kind)
    return found
"""


@pytest.fixture(scope='module')
def display():
    name = f':{70 + os.getpid() % 9}'
    xvfb = subprocess.Popen(['Xvfb', name, '-screen', '0', '1280x1024x24'], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    time.sleep(0.7)
    yield name
    xvfb.terminate()
    xvfb.wait(5)


def run_gtk(display, script):
    code = PRELUDE + textwrap.dedent(script) + "\nprint('ok')\n"
    result = subprocess.run([sys.executable, '-c', code, str(ROOT / 'src'), str(ROOT / 'tests')],
                            env={**os.environ, 'DISPLAY': display, 'GIO_USE_VFS': 'local', 'NO_AT_BRIDGE': '1'},
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0 and result.stdout.strip().endswith('ok'), result.stdout + result.stderr
    return result.stdout


def test_action_icons(display):
    run_gtk(display, """
        manager, renderer = setup()
        actions = ['media-skip-forward', 'Next', 'no-such-icon-x', 'Odd']
        with_icons = manager.notify('a', summary='player', actions=actions, hints={'action-icons': True})
        buttons = widgets(renderer.windows[with_icons], Gtk.Button)
        assert [b.get_label() for b in buttons] == [None, 'Odd'], [b.get_label() for b in buttons]
        assert buttons[0].get_tooltip_text() == 'Next'                   # the label is the tooltip
        assert isinstance(buttons[0].get_image(), Gtk.Image)
        assert buttons[1].get_tooltip_text() is None                     # not an icon: its text, as before
        plain = manager.notify('a', summary='plain', actions=['media-skip-forward', 'Next'])
        assert [b.get_label() for b in widgets(renderer.windows[plain], Gtk.Button)] == ['Next']
    """)


def test_missing_icons_and_pictures_warn_once_per_source(display):
    run_gtk(display, """
        manager, renderer = setup()
        def warnings():
            return [line for line in stream.getvalue().splitlines() if 'warn' in line and ' #' in line]
        manager.notify('a', app_icon='/nonexistent/icon.png', summary='one')
        manager.notify('a', app_icon='/nonexistent/icon.png', summary='two')     # the same source: once
        manager.notify('a', summary='three', hints={'image-path': 'no-such-icon-qq'})
        manager.notify('a', app_icon='dialog-information', summary='fine')        # in every theme: no warning
        found = warnings()
        assert len(found) == 2, found
        assert "app icon '/nonexistent/icon.png': no such file" in found[0], found
        assert "image (image-path hint) 'no-such-icon-qq': not in icon theme" in found[1], found
        assert '`notifyd icons PATTERN`' in found[1]
        renderer.reload()             # a reload redraws the open ones, and what is still missing warns again
        assert len(warnings()) == 4, warnings()
        manager.notify('a', app_icon='/nonexistent/icon.png', summary='four')   # already said since the reload
        assert len(warnings()) == 4, warnings()
    """)


def test_body_line_spacing_puts_a_gap_between_wrapped_lines(display):
    run_gtk(display, """
        from notifyd.render.gtk import SpacedLabel, line_spacing
        assert [line_spacing(v) for v in (None, 2, '3px', 'x', -1)] == [0, 2, 3, 0, 0]
        chips = ' '.join(f'<span background="#1f3347"> tag{i} </span>' for i in range(30))

        def body(config):
            manager, renderer = setup(config)
            nid = manager.notify('a', summary='song', body=chips)
            label = renderer.bodies.get(nid, [None])[0] or next(
                w for w in widgets(renderer.windows[nid], Gtk.Label)
                if w.get_style_context().has_class('body'))
            while Gtk.events_pending():
                Gtk.main_iteration()
            return label

        plain = body('position: {follow: none}\\nbody: {max-lines: 0}')
        spaced = body('position: {follow: none}\\nbody: {max-lines: 0}\\nstyle: {body: {line-spacing: 3}}')
        assert not isinstance(plain, SpacedLabel) and isinstance(spaced, SpacedLabel)
        lines = spaced.get_layout().get_line_count()
        assert lines > 2, lines                                          # the chips wrap
        assert spaced.get_layout().get_spacing() == 3 * 1024             # drawn with the gap
        gap = spaced.get_allocated_height() - plain.get_allocated_height()
        assert gap == (lines - 1) * 3, (gap, lines)                      # and given room for it
    """)
