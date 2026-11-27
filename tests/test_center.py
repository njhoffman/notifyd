"""The notification center's rows: the mode picker, the volume of the default output (pactl).

pactl is a stub on PATH that answers from files and logs what it was asked; the GTK part runs in a
child process under Xvfb, like the renderer test in test_daemon.py.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from notifyd.config import load_config
from notifyd.rules import known_modes

ROOT = Path(__file__).resolve().parent.parent

PACTL = r"""#!/bin/sh
echo "$*" >> "$PACTL_DIR/log"
[ -e "$PACTL_DIR/fail" ] && exit 1
case $1 in
  get-sink-volume) v=$(cat "$PACTL_DIR/volume")
                   echo "Volume: front-left: 42598 /  $v% / -11.23 dB,   front-right: 42598 /  $v% / -11.23 dB"
                   echo "        balance 0.00" ;;
  get-sink-mute) echo "Mute: $(cat "$PACTL_DIR/mute")" ;;
esac
"""


@pytest.fixture
def pactl(tmp_path, monkeypatch):
    """A stub pactl first on PATH; its dir (volume, mute, log, fail) in PACTL_DIR."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'pactl').write_text(PACTL)
    (bin_dir / 'pactl').chmod(0o755)
    (tmp_path / 'volume').write_text('65')
    (tmp_path / 'mute').write_text('no')
    monkeypatch.setenv('PATH', f'{bin_dir}:{os.environ["PATH"]}')
    monkeypatch.setenv('PACTL_DIR', str(tmp_path))
    return tmp_path


def log_lines(path):
    return (path / 'log').read_text().splitlines() if (path / 'log').exists() else []


def test_known_modes_from_rules_in_order_once_with_the_current_one():
    config = load_config(text="""
rules:
- match: {mode: dnd, urgency: low}
  timeout: 1s
- match: {mode: [focus, dnd]}
  timeout: 2s
- match: {mode: /^meet/}
  timeout: 1s
- match: {app: x}
  timeout: 1s
""")
    assert known_modes(config.rules) == ['normal', 'dnd', 'focus']          # regex modes have no name to pick
    assert known_modes(config.rules, 'presenting') == ['normal', 'dnd', 'focus', 'presenting']
    assert known_modes(load_config(text='').rules) == ['normal']


def test_volume_reads_and_changes_the_default_output(pactl):
    from notifyd import volume
    assert volume.available()
    assert volume.get() == {'volume': 65, 'muted': False}
    (pactl / 'mute').write_text('yes')
    (pactl / 'volume').write_text('120')
    assert volume.get() == {'volume': 120, 'muted': True}
    volume.set_volume(30.4).wait_check(None)
    volume.set_volume(-5).wait_check(None)
    volume.set_volume(400).wait_check(None)
    volume.toggle_mute().wait_check(None)
    assert log_lines(pactl)[-4:] == ['set-sink-volume @DEFAULT_SINK@ 30%', 'set-sink-volume @DEFAULT_SINK@ 0%',
                                     'set-sink-volume @DEFAULT_SINK@ 150%', 'set-sink-mute @DEFAULT_SINK@ toggle']
    (pactl / 'fail').touch()                     # no sound server: no row
    assert volume.get() is None


def test_volume_without_pactl(monkeypatch, tmp_path):
    from notifyd import volume
    monkeypatch.setenv('PATH', str(tmp_path))
    assert not volume.available()
    assert volume.get() is None
    assert volume.set_volume(50) is None


CENTER_SCRIPT = r"""
import io, sys, time
sys.path.insert(0, sys.argv[1])
from notifyd.config import load_config
from notifyd.log import EventLog
from notifyd.manager import Manager
from notifyd.render.gtk import GtkRenderer       # pins GTK 3 before Gtk is imported here
from gi.repository import Gdk, GLib, Gtk, Pango
sys.path.insert(0, sys.argv[2])
from conftest import FakeScheduler

def widgets(root, kind):
    found = [root] if isinstance(root, kind) else []
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            found += widgets(child, kind)
    return found

def spin(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)

def wait_for(condition, ms=3000):
    # spin until CONDITION() holds (async reads such as pactl land a little later), at most MS
    end = time.monotonic() + ms / 1000
    while not condition() and time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    return condition()

def rebuilt(old, kind):
    # the center rebuilds at idle priority, behind GTK's own drawing: wait for new widgets
    end = time.monotonic() + 3
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        found = widgets(center.window, kind)
        if found and found[0] is not old:
            return found[0]
        time.sleep(0.005)
    raise AssertionError(f'center not rebuilt ({kind.__name__})')

def leave(mode, detail=Gdk.NotifyType.NONLINEAR):
    event = Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY)
    event.crossing.mode, event.crossing.detail = mode, detail
    return event.crossing

config = load_config(text='position: {follow: none}\nrules:\n- match: {mode: dnd}\n  timeout: 1s\n'
                          '- match: {mode: focus}\n  timeout: 2s\n')
log = EventLog(stream=io.StringIO(), color='never')
manager = Manager(config, log, FakeScheduler())
renderer = GtkRenderer(manager, log)
manager.renderer = renderer
center = renderer.center
center.toggle('show')

picker, = widgets(center.window, Gtk.ComboBoxText)
ids = [row[1] for row in picker.get_model()]
assert ids == ['normal', 'dnd', 'focus'], ids
assert picker.get_active_id() == 'normal'
picker.set_active_id('focus')
assert manager.mode == 'focus'
picker = rebuilt(picker, Gtk.ComboBoxText)       # the center follows the mode event
assert picker.get_active_id() == 'focus'

scale, = widgets(center.window, Gtk.Scale)
assert scale.get_value() == 65
scale.emit('button-press-event', Gdk.Event.new(Gdk.EventType.BUTTON_PRESS))
for value in (40, 35, 30):                       # a drag: one pactl call once it pauses
    scale.set_value(value)
    spin(10)
manager.notify('a', summary='during the drag')   # no rebuild under the pointer while dragging
spin(150)
assert widgets(center.window, Gtk.Scale)[0] is scale
scale.emit('button-release-event', Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE))
scale = rebuilt(scale, Gtk.Scale)                # the postponed rebuild, once released
mute, = [w for w in widgets(center.window, Gtk.ToggleButton) if w.get_tooltip_text() == 'mute']
assert not mute.get_active()
mute.set_active(True)
spin(150)

center.on_leave(None, leave(Gdk.CrossingMode.GRAB))         # the picker's menu taking the pointer
assert center.visible
center.on_leave(None, leave(Gdk.CrossingMode.NORMAL, Gdk.NotifyType.INFERIOR))   # into a child
assert center.visible
center.on_leave(None, leave(Gdk.CrossingMode.NORMAL))
assert not center.visible

# kept between openings: nothing changed, nothing rebuilt; the volume follows in place
builds = []
original_rebuild = center.rebuild
center.rebuild = lambda *a: builds.append(1) or original_rebuild(*a)
center.toggle('show')
spin(50)
assert builds == [], builds
scale, = widgets(center.window, Gtk.Scale)
assert wait_for(lambda: scale.get_value() == 65), scale.get_value()   # re-read from pactl on opening
center.hide()
open(sys.argv[3] + '/volume', 'w').write('20')
center.toggle('show')
assert wait_for(lambda: scale.get_value() == 20), scale.get_value()   # pactl is read without blocking
assert builds == []
center.hide()
first = manager.notify('a', summary='new')       # a change while hidden: rebuilt on opening
spin(50)
assert builds == []
center.toggle('show')
assert builds == [1]
center.hide()

# history: the newest center.cards, then a button for more, back to the cap once hidden
manager.config.settings['center']['cards'] = 5
for i in range(12):
    manager.close(manager.notify('h', summary=f'h{i}'))
center.toggle('show')
def history_cards():
    return [w for w in widgets(center.window, Gtk.EventBox) if w.get_child()
            and 'center-card' in w.get_child().get_style_context().list_classes()]
assert len(history_cards()) == 5 + len(manager.listing())
more, = [b for b in widgets(center.window, Gtk.Button) if b.get_label() and b.get_label().startswith('Show ')]
assert more.get_label() == f'Show 5 more of {len(manager.history) - 5}', more.get_label()
more.clicked()
assert len(history_cards()) == 10 + len(manager.listing())
center.hide()
center.toggle('show')
assert len(history_cards()) == 5 + len(manager.listing())
center.hide()

# the now-playing text: one line each, cut with an ellipsis instead of wrapped
row = center.player_row({'name': 'x', 'player': 'spotify', 'status': 'Playing', 'art': '',
                         'title': 'Resistance ' * 6, 'artist': 'Muse', 'album': 'The Resistance'})
labels = widgets(row, Gtk.Label)
assert [l.get_ellipsize() for l in labels[:3]] == [Pango.EllipsizeMode.END] * 3
assert not any(l.get_line_wrap() for l in labels[:3])

# remote cover art: drawn without it, filled in place once fetched (cached after)
import http.server, threading
from gi.repository import GdkPixbuf
pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 8)
PNG = pixbuf.save_to_bufferv('png', [], [])[1]
hits = []
class Art(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        hits.append(self.path)
        self.send_response(200); self.send_header('Content-Length', str(len(PNG))); self.end_headers()
        self.wfile.write(PNG)
    def log_message(self, *a): pass
httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Art)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = f'http://127.0.0.1:{httpd.server_address[1]}/cover.png'
playing = {'name': 'x', 'player': 'spotify', 'status': 'Playing', 'art': url, 'title': 't', 'artist': '', 'album': ''}
row = center.player_row(playing)
center.window.get_child().pack_start(row, False, False, 0)   # attached, as in a real panel
slot = row.get_children()[0]
assert not slot.get_children()
end = time.monotonic() + 5
while not slot.get_children() and time.monotonic() < end:
    spin(20)
assert isinstance(slot.get_children()[0], Gtk.Image) and hits == ['/cover.png']
row2 = center.player_row(playing)                # from the cache, right away
slot2 = row2.get_children()[0]
assert slot2.get_children() and hits == ['/cover.png']
manager.config.settings['center']['remote-art'] = False
row3 = center.player_row({**playing, 'art': url + '?other'})
slot3 = row3.get_children()[0]
spin(300)
assert not slot3.get_children() and hits == ['/cover.png']
httpd.shutdown()

manager.config.settings['center']['volume'] = False
manager.config.settings['center']['modes'] = False
log.emit('reload')                               # a config change rebuilds it
center.toggle('show')
assert not widgets(center.window, Gtk.Scale)
assert not widgets(center.window, Gtk.ComboBoxText)
print('ok')
"""


@pytest.mark.skipif(not shutil.which('Xvfb'), reason='needs Xvfb')
def test_center_mode_picker_volume_row_and_grab_leave(pactl):
    display = f':{90 + os.getpid() % 9}'
    xvfb = subprocess.Popen(['Xvfb', display, '-screen', '0', '1280x720x24'], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.7)
        result = subprocess.run([sys.executable, '-c', CENTER_SCRIPT, str(ROOT / 'src'), str(ROOT / 'tests'),
                                 str(pactl)],
                                env={**os.environ, 'DISPLAY': display, 'GIO_USE_VFS': 'local',
                                     'DBUS_SESSION_BUS_ADDRESS': 'disabled:', 'XDG_CACHE_HOME': str(pactl / 'cache')},
                                capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'ok'
        changes = [line for line in log_lines(pactl) if line.startswith('set-')]
        assert changes == ['set-sink-volume @DEFAULT_SINK@ 30%', 'set-sink-mute @DEFAULT_SINK@ toggle'], changes
    finally:
        xvfb.terminate()
        xvfb.wait(5)


KEYS_SCRIPT = r"""
import io, sys, time
sys.path.insert(0, sys.argv[1])
from notifyd.config import load_config
from notifyd.log import EventLog
from notifyd.manager import Manager
from notifyd.render.gtk import GtkRenderer       # pins GTK 3 before Gtk is imported here
from gi.repository import Gdk, GLib, Gtk
sys.path.insert(0, sys.argv[2])
from conftest import FakeScheduler

def widgets(root, kind):
    found = [root] if isinstance(root, kind) else []
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            found += widgets(child, kind)
    return found

def wait_for(condition, ms=3000):
    # spin until CONDITION() holds, at most MS
    end = time.monotonic() + ms / 1000
    while not condition() and time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    return condition()

def spin(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)

def press(name, state=0):
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.key.keyval, event.key.state = Gdk.keyval_from_name(name), state
    handled = center.on_key(None, event.key)
    spin(30)                                     # rebuilds after events run at idle
    return handled

def selected():
    index = center.index()
    return None if index is None else center.cards[index][1]['summary']

config = load_config(text='position: {follow: none}\ncenter: {cards: 3}\n'
                          'rules:\n- match: {mode: focus}\n  timeout: 2s\n')
log = EventLog(stream=io.StringIO(), color='never')
manager = Manager(config, log, FakeScheduler())
renderer = GtkRenderer(manager, log)
manager.renderer = renderer
center = renderer.center
for i in range(5):
    manager.close(manager.notify('h', summary=f'h{i}'))   # history, newest first: h4 .. h0

center.toggle('show')
spin(100)
assert center.grabbed
assert selected() is None
assert press('j') and selected() == 'h4'
assert 'selected' in center.cards[0][0].get_child().get_style_context().list_classes()
press('j'); press('Down')
assert selected() == 'h2'
press('j')                                       # past the last of 3 cards: loads more
assert len(center.cards) == 5 and selected() == 'h1'
press('k'); press('Up')
assert selected() == 'h3'
press('G'); assert selected() == 'h0'
press('g'); assert selected() == 'h4'

press('Return')                                  # shows it again; it stays in the history
assert [n.summary for n in manager.active.values()] == ['h4']
spin(100)
assert center.cards[0][2] and center.cards[0][1]['summary'] == 'h4'   # now also an open card on top
assert selected() == 'h4' and not center.cards[center.index()][2]   # still the history one
press('d')                                       # forgets it: the selection moves to its neighbor
spin(100)
assert 'h4' not in [e['summary'] for e in manager.history]
assert selected() == 'h3' and not center.cards[center.index()][2]
press('k')                                       # up into the open ones
assert selected() == 'h4' and center.cards[center.index()][2]
press('d')                                       # closes the open one
spin(100)
assert not manager.active
assert manager.history.entries[-1]['closed'] == 'dismissed'   # the user dismissed it (2), not the sender (3)

paused = manager.paused
press('p'); assert manager.paused != paused
press('p'); assert manager.paused == paused
press('m'); assert manager.mode == 'focus'
press('m'); assert manager.mode == 'normal'

spin(150)                                        # let the rebuilds the mode changes queued finish
press('plus'); press('equal')
assert center.volume_widgets[1].get_value() == 75  # read the slider now: a rebuild replaces the widget
press('minus')
assert center.volume_widgets[1].get_value() == 70
spin(150)

assert not press('j', Gdk.ModifierType.CONTROL_MASK)   # chords are left alone
center.toggle('show')
spin(100)
assert press('bracketleft', Gdk.ModifierType.CONTROL_MASK) and not center.visible   # ctrl+[ is escape
center.toggle('show')
spin(100)
assert not press('bracketleft')                         # a plain [ does nothing
assert not press('x')
press('Escape')
assert not center.visible and not center.grabbed

center.toggle('show')                              # the WM's toggle key, swallowed by the grab, hides it
spin(100)
assert center.visible
assert not press('grave')                         # without Super it is just a key
assert center.visible
assert press('grave', Gdk.ModifierType.MOD4_MASK)
assert not center.visible and not center.grabbed
manager.config.settings['center']['toggle-key'] = '<Control>F12'
center.toggle('show')
spin(100)
assert not press('grave', Gdk.ModifierType.MOD4_MASK) and center.visible
assert press('F12', Gdk.ModifierType.CONTROL_MASK) and not center.visible

manager.config.settings['center']['keyboard'] = False
center.toggle('show')
spin(100)
assert center.visible and not center.grabbed

# history entries that share an id (a sender reusing replaces_id): j moves through every one of them
manager.config.settings['center']['keyboard'] = True
manager.forget()
first = manager.notify('backdrop', summary='w0')
manager.close(first)
for i in range(1, 4):
    manager.close(manager.notify('backdrop', replaces_id=first, summary=f'w{i}'))
center.hide()
center.limit = None
center.toggle('show')
spin(150)
press('g')
seen = [selected()]
for _ in range(3):
    press('j')
    seen.append(selected())
assert seen == ['w3', 'w2', 'w1', 'w0'], seen
press('d')                                          # forgets that one entry, not all four
spin(100)
assert [e['summary'] for e in manager.history] == ['w1', 'w2', 'w3']
manager.config.settings['center']['keyboard'] = False

# ? and ctrl+/ open the key help, built from the same table as the keys; any key closes it, unused
from notifyd.render.center import BINDINGS
manager.config.settings['center']['keyboard'] = True
before = selected()
assert press('question') and center.help_window is not None and center.help_window.get_visible()
texts = [l.get_text() for l in widgets(center.help_window, Gtk.Label)]
assert all(label in texts and what in texts for _n, label, what, _a in BINDINGS), texts
assert press('j') and center.help_window is None and selected() == before   # closed, j did not move
assert press('slash', Gdk.ModifierType.CONTROL_MASK) and center.help_window is not None
assert press('slash', Gdk.ModifierType.CONTROL_MASK) and center.help_window is None
press('question')
center.hide()
assert center.help_window is None
center.toggle('show')
spin(100)
manager.config.settings['center']['keyboard'] = False

# `e` shows the selected card's whole body
manager.config.settings['center']['keyboard'] = True
center.hide()
manager.close(manager.notify('long', summary='long body', body='\n'.join(f'line {i}' for i in range(8))))
center.toggle('show')
spin(150)
press('g')
assert selected() == 'long body'
def body_text():
    card = center.cards[center.index()][0]
    return [l.get_text() for l in widgets(card, Gtk.Label) if 'line 0' in l.get_text()]
assert body_text() == ['line 0\nline 1\nline 2…']
press('e')
assert body_text() == ['\n'.join(f'line {i}' for i in range(8))] and selected() == 'long body'
press('e')
assert body_text() == ['line 0\nline 1\nline 2…']
manager.config.settings['center']['keyboard'] = False

# center.buttons: a click runs the command; `state` exiting 0 marks the button on
import tempfile as _tempfile
flag_dir = _tempfile.mkdtemp()
flag = flag_dir + '/wifi-on'
manager.config.settings['center']['buttons'] = [
    {'label': 'Wifi', 'run': f'touch {flag}', 'state': f'test -e {flag}', 'icon': 'view-grid-symbolic'},
    {'label': 'Plain', 'run': 'true'}]
center.hide()
manager.notify('x', summary='rebuild')
center.toggle('show')
spin(100)
(wifi, wifi_spec), (plain, _p) = center.button_widgets
assert wifi.get_label() == 'Wifi' and plain.get_label() == 'Plain'
assert wifi.get_image().get_icon_name()[0] == 'view-grid-symbolic' and plain.get_image() is None
icons = {w.get_label(): w.get_image().get_icon_name()[0] for w in widgets(center.window, Gtk.Button)
         if w.get_label() in ('Do not disturb', 'By app', 'Clear')}
assert icons == {'Do not disturb': 'notifications-disabled-symbolic', 'By app': 'view-app-grid-symbolic',
                 'Clear': 'edit-clear-all-symbolic'}, icons
assert wait_for(lambda: True) and not wifi.get_style_context().has_class('on')
wifi.clicked()
assert wait_for(lambda: wifi.get_style_context().has_class('on')), 'state follows the click'
import os as _os
_os.remove(flag)
center.hide()
center.toggle('show')                              # opening again checks the state again
assert wait_for(lambda: not wifi.get_style_context().has_class('on'))
manager.config.settings['center']['buttons'] = []

# center.group: one group per app, newest card only until the header is clicked; × forgets the group
manager.forget()
for app, summary in (('mail', 'm1'), ('chat', 'c1'), ('mail', 'm2'), ('mail', 'm3')):
    manager.close(manager.notify(app, summary=summary))
manager.config.settings['center']['group'] = True
manager.config.settings['center']['cards'] = 20        # the card limit counts entries before grouping
center.limit = None
center.hide()
center.toggle('show')
spin(100)
def history_summaries():
    return [entry['summary'] for _box, entry, opened in center.cards if not opened]
assert history_summaries() == ['m3', 'c1'], history_summaries()   # mail is newest, then chat
center.toggle_group('mail')
spin(50)
assert history_summaries() == ['m3', 'm2', 'm1', 'c1']
center.toggle_group('mail')
assert history_summaries() == ['m3', 'c1']
center.forget_group([e for e in manager.history if e['app'] == 'mail'])
spin(50)
assert [e['app'] for e in manager.history] == ['chat'] and history_summaries() == ['c1']
manager.config.settings['center']['group'] = False

# By app (and `a`) switch between grouped and one list, until the next reload
for app, summary in (('mail', 'm1'), ('mail', 'm2')):
    manager.close(manager.notify(app, summary=summary))
center.hide()
center.toggle('show')
spin(100)
assert history_summaries() == ['m2', 'm1', 'c1']                 # center.group is off: one list
toggle, = [w for w in widgets(center.window, Gtk.ToggleButton) if w.get_label() == 'By app']
assert not toggle.get_active()
toggle.set_active(True)
assert wait_for(lambda: history_summaries() == ['m2', 'c1'])      # grouped, mail collapsed
manager.config.settings['center']['keyboard'] = True
press('a')
assert wait_for(lambda: history_summaries() == ['m2', 'm1', 'c1'])
press('a')
assert wait_for(lambda: history_summaries() == ['m2', 'c1'])
manager.reload(manager.config)                                    # a reload goes back to center.group
assert wait_for(lambda: history_summaries() == ['m2', 'm1', 'c1'])

# the × on a history card forgets that card (and only does that)
card = [c for c in center.cards if c[1]['summary'] == 'm1'][0][0]
close, = [w for w in widgets(card, Gtk.EventBox)
          if isinstance(w.get_child(), Gtk.Label) and w.get_child().get_text() == '×']
release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
release.button.button = 1
close.emit('button-release-event', release)
assert wait_for(lambda: 'm1' not in [e['summary'] for e in manager.history])
assert 'm1' not in [n.summary for n in manager.active.values()]  # not shown again by the card's click
manager.config.settings['center']['keyboard'] = False
manager.config.settings['center']['cards'] = 3

# a center card is the popup's card: its own style scope and the rule's hidden parts, open or closed
manager.config.settings['rules'] = load_config(
    text='rules:\n- match: {app: songs}\n  hide: [app-name]\n  style: {background: "#102030"}\n').settings['rules']
song = manager.notify('songs', summary='a song', body='an artist')
popup_scope = renderer.window_scopes[song]
assert wait_for(lambda: any(c[1]['id'] == song and c[2] for c in center.cards))
def card_of(nid, opened):
    return [c for c in center.cards if c[1]['id'] == nid and c[2] == opened][0][0]
wrapper = card_of(song, True).get_child()
assert popup_scope in wrapper.get_style_context().list_classes()          # same look as the popup
assert not [l for l in widgets(wrapper, Gtk.Label) if l.get_text() == 'songs']   # app-name hidden
manager.close(song)
center.refresh()
assert wait_for(lambda: any(c[1]['id'] == song and not c[2] for c in center.cards))
closed_wrapper = card_of(song, False).get_child()
assert popup_scope in closed_wrapper.get_style_context().list_classes()   # rules applied again
assert not [l for l in widgets(closed_wrapper, Gtk.Label) if l.get_text() == 'songs']
manager.config.settings['rules'] = []

# a history card shows the kept thumbnail of an image-data picture, and its markup mode
import tempfile
from pathlib import Path
manager.thumbs_path = Path(tempfile.mkdtemp())
manager.config.settings['rules'] = load_config(text='rules:\n- match: {app: pic}\n  markup: plain\n').settings['rules']
pic = manager.notify('pic', summary='<b>raw</b>', hints={'image-data': (8, 8, 24, False, 8, 3, bytes(192))})
assert wait_for(lambda: any(c[1]['id'] == pic and c[2] for c in center.cards))   # rebuilt at idle
open_card = [c for c in center.cards if c[1]['id'] == pic and c[2]][0][0]
assert widgets(open_card, Gtk.Image), 'the open card shows its image-data'
manager.close(pic)
center.refresh()
assert wait_for(lambda: any(c[1]['id'] == pic and not c[2] for c in center.cards))
card = [c for c in center.cards if c[1]['id'] == pic][0][0]
images = widgets(card, Gtk.Image)
assert images and images[0].get_pixbuf() is not None and manager.history.entries[-1]['thumb']
assert [l for l in widgets(card, Gtk.Label) if l.get_text() == '<b>raw</b>'], 'plain markup kept in history'
print('ok')
"""


@pytest.mark.skipif(not shutil.which('Xvfb'), reason='needs Xvfb')
def test_center_keyboard(pactl):
    display = f':{70 + os.getpid() % 9}'
    xvfb = subprocess.Popen(['Xvfb', display, '-screen', '0', '1280x720x24'], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.7)
        result = subprocess.run([sys.executable, '-c', KEYS_SCRIPT, str(ROOT / 'src'), str(ROOT / 'tests')],
                                env={**os.environ, 'DISPLAY': display, 'GIO_USE_VFS': 'local',
                                     'DBUS_SESSION_BUS_ADDRESS': 'disabled:'},
                                capture_output=True, text=True, timeout=90, check=False)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'ok'
        changes = [line for line in log_lines(pactl) if line.startswith('set-')]
        assert changes == ['set-sink-volume @DEFAULT_SINK@ 70%'], changes   # debounced: the last step
    finally:
        xvfb.terminate()
        xvfb.wait(5)
