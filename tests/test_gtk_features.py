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


def test_popups_are_announced_to_screen_readers(display):
    run_gtk(display, """
        from gi.repository import Atk
        manager, renderer = setup()
        normal = manager.notify('mail', summary='<b>New</b> mail', body='from <i>Ann</i> &amp; Bo')
        accessible = renderer.windows[normal].get_accessible()
        assert accessible.get_role() == Atk.Role.NOTIFICATION
        assert (accessible.get_name(), accessible.get_description()) == ('New mail', 'from Ann & Bo')
        critical = manager.notify('bat', summary='battery low', hints={'urgency': 2})
        assert renderer.windows[critical].get_accessible().get_role() == Atk.Role.ALERT
        untitled = manager.notify('app', summary='')
        assert renderer.windows[untitled].get_accessible().get_name() == 'app'      # never nameless
        manager.notify('mail', summary='replaced', body='', replaces_id=normal)
        assert renderer.windows[normal].get_accessible().get_name() == 'replaced'   # kept current
    """)


def test_monitor_by_name_and_follow_focus(display):
    run_gtk(display, """
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        name = display.get_monitor(0).get_model()                  # Xvfb's one output ('screen')
        manager, renderer = setup(f'position: {{follow: none, monitor: {name.upper()}}}')
        assert renderer.monitor_geometry().width == 1280          # by name, any case
        assert 'no such monitor' not in stream.getvalue()
        manager, renderer = setup('position: {follow: none, monitor: HDMI-9}')
        assert renderer.monitor_geometry().width == 1280          # unknown: the primary one, with a warning
        assert renderer.monitor_geometry().width == 1280
        assert stream.getvalue().count("position.monitor 'HDMI-9': no such monitor") == 1   # once
        manager, renderer = setup('position: {follow: focus}')     # no window manager: no focused window
        assert renderer.monitor_geometry().width == 1280          # falls back to position.monitor
        nid = manager.notify('a', summary='placed')
        assert renderer.windows[nid].get_visible()
    """)


def test_opening_the_center_clears_the_missed_count(display):
    run_gtk(display, """
        manager, renderer = setup()
        manager.missed = 3
        renderer.center.toggle('show')
        assert manager.missed == 0
        renderer.center.toggle('hide')
    """)


def test_a_rule_position_draws_a_second_stack(display):
    run_gtk(display, '''
        manager, renderer = setup('position: {follow: none, anchor: top-right}\\nmax-visible: 1\\n'
                                  'rules:\\n  - match: {app: Volume}\\n'
                                  '    position: {anchor: bottom-center, margin: 40}\\n')
        chat = manager.notify('chat', summary='message', timeout=0)
        waits = manager.notify('chat', summary='another', timeout=0)
        volume = manager.notify('Volume', summary='50%', timeout=0)
        vol_waits = [manager.notify('Volume', summary=f'{v}%', timeout=0) for v in (60, 70)]
        while Gtk.events_pending():
            Gtk.main_iteration()
        assert manager.displayed == [volume, chat] and manager.waiting == [waits, *vol_waits]
        assert len(renderer.areas) == 2
        (cx, cy), (vx, vy) = renderer.windows[chat].get_position(), renderer.windows[volume].get_position()
        width = renderer.windows[volume].get_size()[0]
        assert cy < 100 and vy > 800                                  # top-right and bottom of a 1024 screen
        assert cx > vx and abs(vx + width // 2 - 640) <= 2            # the volume one is centered
        labels = {nid: label for nid, label in renderer.waiting_labels.items() if label.get_visible()}
        assert {nid: label.get_text() for nid, label in labels.items()} == {chat: '+1', volume: '+2'}
        manager.close(volume)
        assert vol_waits[0] in manager.displayed and waits in manager.waiting
        manager.close_all()
        assert renderer.areas == {}
    ''')


def test_inline_reply_in_the_center(display):
    run_gtk(display, """
        import time
        from gi.repository import Gdk, GLib

        def spin(ms=100):
            end = time.monotonic() + ms / 1000
            while time.monotonic() < end:
                GLib.MainContext.default().iteration(False)
                time.sleep(0.005)

        def key(name):
            event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            event.key.keyval, event.key.state = Gdk.keyval_from_name(name), 0
            return renderer.center.on_key(None, event.key)

        manager, renderer = setup('position: {follow: none}\\ncenter: {keyboard: false}')
        replies = []
        manager.on_reply = lambda nid, text: replies.append((nid, text))
        nid = manager.notify('chat', summary='Ann: lunch?', actions=['inline-reply', 'Answer'],
                             hints={'x-kde-reply-placeholder-text': 'Write to Ann'})
        buttons = widgets(renderer.windows[nid], Gtk.Button)
        assert [b.get_label() for b in buttons] == ['Answer']                 # the popup has a button
        buttons[0].clicked()                                                  # it opens the center
        spin()
        center = renderer.center
        entry = center.reply_entries[nid]
        assert center.visible and center.window.get_focus() is entry
        assert entry.get_placeholder_text() == 'Write to Ann'
        assert key('j') is False                                              # keys are text while typing
        entry.set_text('sure')
        manager.notify('other', summary='arrives meanwhile')                  # the center is built again
        spin(200)
        entry = center.reply_entries[nid]
        assert entry.get_text() == 'sure' and center.window.get_focus() is entry   # draft and focus kept
        assert key('Escape') is True and center.window.get_focus() is None and center.visible
        assert key('j') is True                                               # back to the card keys
        center.select(center.index() or 0)
        entry.set_text('sure, 12:30')
        entry.emit('activate')
        assert replies == [(nid, 'sure, 12:30')] and nid not in manager.active
    """)
