"""The notification center: deadd's sidebar with what is open and what was closed.

A full-height panel on one side of the monitor under the pointer, styled by the current preset.
Its header has the pause (do not disturb) toggle, the mode picker, and clear; then what a media
player plays and the volume of the default output; below are the open notifications, then the
history, newest first. Clicking a closed one shows it again, x forgets it.
It hides when the pointer leaves (center.hide-on-leave), on `notifyd ctl center`, or on SIGUSR1,
which also opens it (the i3 binding deadd used).

Laying out the cards is what costs (GTK measures every wrapped label), so the panel is kept
between openings and rebuilt only after something changed: a notification, the mode, a player, the
preset. What changes silently (the clock, the volume) is updated in place on each opening. The
history shows the newest center.cards entries, with a button for more.

While it is open it grabs the keyboard (it is an override-redirect popup, so it cannot take focus
the usual way): j/k move between cards and scroll them into view, Enter acts like a left click,
d removes one; see KEYS. center.keyboard: false leaves the keyboard alone.
"""

import time

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from notifyd import art, mpris, volume
from notifyd.config import ConfigError, load_preset
from notifyd.markup import prepare
from notifyd.render.css import build_css, resolve_style, style_class
from notifyd.rules import known_modes

VOLUME_DELAY = 50   # ms after the slider stops moving before pactl is run

REFRESH_EVENTS = {'shown', 'replaced', 'expired', 'dismissed', 'closed', 'suppressed', 'paused', 'resumed', 'mode',
                  'reload', 'history', 'queued', 'waiting'}
# Idle priorities around GTK's own (resize 110, redraw 120): a rebuild runs before them, since a
# scrollbar fading out redraws for a second and would hold back anything at the default 200; the
# scroll to a selected card runs between them, once the card has its place.
REBUILD_PRIORITY = GLib.PRIORITY_HIGH_IDLE
SCROLL_PRIORITY = GLib.PRIORITY_HIGH_IDLE + 15
CARDS = 20          # history cards shown at first, and how many more each "show more" adds
GRAB_TRIES, GRAB_RETRY = 10, 50   # i3 can still hold the keyboard right after its binding ran
VOLUME_STEP = 5

# key name -> Center method name (and argument)
KEYS = {
    'Escape': ('hide',), 'q': ('hide',),
    'j': ('move', 1), 'Down': ('move', 1), 'k': ('move', -1), 'Up': ('move', -1),
    'g': ('move_to', 0), 'Home': ('move_to', 0), 'G': ('move_to', -1), 'End': ('move_to', -1),
    'Return': ('activate',), 'KP_Enter': ('activate',),
    'd': ('remove',), 'Delete': ('remove',),
    'C': ('clear',), 'p': ('toggle_pause',), 'm': ('next_mode',),
    'space': ('player', 'PlayPause'), 'n': ('player', 'Next'), 'N': ('player', 'Previous'),
    'plus': ('step_volume', VOLUME_STEP), 'equal': ('step_volume', VOLUME_STEP),
    'KP_Add': ('step_volume', VOLUME_STEP), 'minus': ('step_volume', -VOLUME_STEP),
    'KP_Subtract': ('step_volume', -VOLUME_STEP),
}

_MODS = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.MOD1_MASK \
    | Gdk.ModifierType.SUPER_MASK


def toggle_key_matches(accelerator, keyval, state):
    """Whether KEYVAL with STATE is ACCELERATOR ("<Super>grave"): the key the window manager binds to
    toggle the center, which never reaches it while the center holds the keyboard."""
    if not accelerator:
        return False
    want, mods = Gtk.accelerator_parse(str(accelerator))
    if not want:
        return False
    if state & Gdk.ModifierType.MOD4_MASK:   # X reports Super as Mod4
        state |= Gdk.ModifierType.SUPER_MASK
    return Gdk.keyval_to_lower(keyval) == Gdk.keyval_to_lower(want) and (state & _MODS) == (mods & _MODS)


class Center:
    def __init__(self, renderer):
        self.renderer = renderer
        self.manager = renderer.manager
        self.window = None
        self.pending = False
        self.volume_timer = None
        self.dragging = False      # a slider held down: rebuilding would pull it from under the pointer
        self.dirty = True          # something changed since the panel was built
        self.built = None          # (style scope, now playing) the panel was built for
        self.limit = None          # history cards shown; None: center.cards
        self.clock = None
        self.volume_widgets = None # (mute button, scale) to update in place
        self.cards = []            # [(event box, entry, opened)] top to bottom, for the keyboard
        self.selected = None       # (opened, id) of the selected card
        self.selected_index = None # its place among the open or the closed cards, for its neighbor once gone
        self.more = None           # the next limit when "show more" is there
        self.scroll = None
        self.grabbed = False
        renderer.log.listeners.append(self.on_event)
        self.bus = None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            # a player changing song or state: the now-playing row follows while the center is open
            self.bus.signal_subscribe(None, 'org.freedesktop.DBus.Properties', 'PropertiesChanged', mpris.PATH,
                                      mpris.PLAYER, Gio.DBusSignalFlags.NONE, self.on_player)
        except GLib.Error:
            self.bus = None

    @property
    def visible(self):
        return self.window is not None and self.window.get_visible()

    def settings(self):
        return self.manager.config.settings.get('center') or {}

    def toggle(self, what='toggle'):
        show = not self.visible if what == 'toggle' else what == 'show'
        if show:
            self.show()
        else:
            self.hide()
        return self.visible

    # -- window ---------------------------------------------------------------
    def show(self):
        if self.window is None:
            self.window = Gtk.Window(type=Gtk.WindowType.POPUP)
            self.window.set_title('notifyd center')
            self.window.set_type_hint(Gdk.WindowTypeHint.DOCK)
            self.window.set_app_paintable(True)
            if self.renderer.visual is not None:
                self.window.set_visual(self.renderer.visual)
            self.window.get_style_context().add_class('notifyd')
            self.window.add_events(Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.KEY_PRESS_MASK)
            self.window.connect('leave-notify-event', self.on_leave)
            self.window.connect('key-press-event', self.on_key)
        playing = self.now_playing()
        if self.dirty or self.built != (self.scope()[0], playing):
            self.rebuild(playing)
        else:
            self.update_live()
        self.place()
        self.window.show_all()
        self.grab_keyboard()
        self.renderer.log.emit('info', message='center shown')

    def hide(self):
        self.ungrab_keyboard()
        if self.window is not None:
            self.window.hide()
        if self.limit is not None:   # "show more" lasts while it is open
            self.limit = None
            self.dirty = True

    def grab_keyboard(self, tries=GRAB_TRIES):
        if not self.visible or self.grabbed or not self.settings().get('keyboard', True):
            return False
        seat = Gdk.Display.get_default().get_default_seat()
        status = seat.grab(self.window.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None, None)
        if status == Gdk.GrabStatus.SUCCESS:
            self.grabbed = True
        elif tries > 1:
            GLib.timeout_add(GRAB_RETRY, self.grab_keyboard, tries - 1)
        else:
            self.renderer.log.warn(f'center: no keyboard grab ({status.value_nick}); mouse only')
        return False

    def ungrab_keyboard(self):
        if self.grabbed:
            Gdk.Display.get_default().get_default_seat().ungrab()
            self.grabbed = False

    def place(self):
        area = self.renderer.monitor_geometry()
        width = int(self.settings().get('width') or 400)
        self.window.set_size_request(width, area.height)
        self.window.resize(width, area.height)
        x = area.x if self.settings().get('side') == 'left' else area.x + area.width - width
        self.window.move(x, area.y)

    def on_leave(self, _widget, event):
        # A grab (the mode picker's menu opening) also sends a leave: only a real one hides it.
        if event.mode != Gdk.CrossingMode.NORMAL:
            return
        if event.detail != Gdk.NotifyType.INFERIOR and self.settings().get('hide-on-leave', True):
            self.hide()

    def on_event(self, event):
        if event['event'] not in REFRESH_EVENTS:
            return
        self.dirty = True
        if self.visible and not self.pending:
            self.pending = True
            GLib.idle_add(self.refresh, priority=REBUILD_PRIORITY)

    def on_player(self, *_args):
        self.on_event({'event': 'shown'})

    def refresh(self):
        self.pending = False
        if self.dragging:
            self.pending = True    # once the slider is let go
            return False
        if self.visible:
            self.rebuild()
            self.window.show_all()
        return False

    # -- content --------------------------------------------------------------
    def scope(self):
        try:
            preset = load_preset(self.manager.config.settings['preset'])['style']
        except ConfigError:
            preset = load_preset('deadd')['style']
        style = resolve_style(preset, self.manager.config.settings.get('style'))
        scope = style_class(style)
        if scope not in self.renderer.styles:
            self.renderer.styles[scope] = build_css(style, scope)
            self.renderer.load_css()
        return scope, style

    def now_playing(self):
        return mpris.now_playing(self.bus) if self.bus and self.settings().get('mpris', True) else None

    def update_live(self):
        """What changes without an event, in place: the clock and the volume."""
        self.clock.set_text(time.strftime('%H:%M'))
        if self.volume_widgets:
            level = volume.get()
            if level is not None:
                mute, scale = self.volume_widgets
                for widget, handler, set_value in ((mute, self.on_mute, lambda: mute.set_active(level['muted'])),
                                                   (scale, self.on_volume,
                                                    lambda: scale.set_value(min(100, level['volume'])))):
                    widget.handler_block_by_func(handler)
                    set_value()
                    widget.handler_unblock_by_func(handler)
                self.set_mute_icon(mute)

    def rebuild(self, playing=False):
        if playing is False:
            playing = self.now_playing()
        scope, style = self.scope()
        self.dirty = False
        self.built = (scope, playing)
        context = self.window.get_style_context()
        for name in [c for c in context.list_classes() if c.startswith('nd-')]:
            context.remove_class(name)
        context.add_class(scope)
        child = self.window.get_child()
        if child:
            self.window.remove(child)
            child.destroy()
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.get_style_context().add_class('notification')
        panel.get_style_context().add_class('center')

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.clock = self.renderer.label(time.strftime('%H:%M'), 'center-time', wrap=False)
        header.pack_start(self.clock, True, True, 0)
        pause = Gtk.ToggleButton(label='Do not disturb')
        pause.set_active(self.manager.paused)
        pause.connect('toggled', lambda b: self.manager.pause(b.get_active()))
        header.pack_end(pause, False, False, 0)
        panel.pack_start(header, False, False, 0)

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if self.settings().get('modes', True):
            status.pack_start(self.mode_picker(), False, False, 0)
            text = f'{len(self.manager.held_back)} held back' if self.manager.held_back else ''
        else:
            text = f'mode {self.manager.mode}' + (f'  ·  {len(self.manager.held_back)} held back'
                                                 if self.manager.held_back else '')
        status.pack_start(self.renderer.label(GLib.markup_escape_text(text), 'appname', wrap=False), True, True, 0)
        clear = Gtk.Button(label='Clear')
        clear.connect('clicked', lambda _b: self.clear())
        status.pack_end(clear, False, False, 0)
        panel.pack_start(status, False, False, 0)

        if playing:
            panel.pack_start(self.player_row(playing), False, False, 0)
        level = volume.get() if self.settings().get('volume', True) and volume.available() else None
        self.volume_widgets = None
        if level is not None:
            panel.pack_start(self.volume_row(level), False, False, 0)

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.cards = []
        self.more = None
        open_entries = self.manager.listing()
        closed = list(reversed(list(self.manager.history)))
        limit = self.limit or int(self.settings().get('cards') or CARDS)
        for entry in open_entries:
            rows.pack_start(self.card(entry, style, opened=True), False, False, 0)
        if open_entries and closed:
            rows.pack_start(Gtk.Separator(), False, False, 4)
        for entry in closed[:limit]:
            rows.pack_start(self.card(entry, style, opened=False), False, False, 0)
        if len(closed) > limit:
            step = int(self.settings().get('cards') or CARDS)
            more = Gtk.Button(label=f'Show {min(step, len(closed) - limit)} more of {len(closed) - limit}')
            more.set_relief(Gtk.ReliefStyle.NONE)
            more.get_style_context().add_class('center-more')
            more.connect('clicked', lambda _b: self.show_more(limit + step))
            rows.pack_start(more, False, False, 4)
            self.more = limit + step
        if not open_entries and not closed:
            rows.pack_start(self.renderer.label('<i>No notifications</i>', 'body'), False, False, 12)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(rows)
        self.scroll = scroll
        self.restore_selection()
        panel.pack_start(scroll, True, True, 0)
        self.window.add(panel)

    def mode_picker(self):
        picker = Gtk.ComboBoxText()
        picker.get_style_context().add_class('center-mode')
        modes = known_modes(self.manager.config.rules, self.manager.mode)
        for name in modes:
            picker.append(name, name)
        picker.set_active_id(self.manager.mode)
        picker.set_tooltip_text('mode (rules match it with match: {mode: NAME})')
        picker.connect('changed', lambda p: p.get_active_id() and self.manager.set_mode(p.get_active_id()))
        return picker

    def volume_row(self, level):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class('center-card')
        row.get_style_context().add_class('center-volume')
        mute = Gtk.ToggleButton()
        mute.set_relief(Gtk.ReliefStyle.NONE)
        mute.set_active(level['muted'])
        self.set_mute_icon(mute)
        mute.set_tooltip_text('mute')
        mute.connect('toggled', self.on_mute)
        row.pack_start(mute, False, False, 0)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_value(min(100, level['volume']))
        scale.set_digits(0)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.connect('format-value', lambda _s, value: f'{int(value)}%')
        scale.connect('value-changed', self.on_volume)
        scale.connect('button-press-event', self.on_drag, True)
        scale.connect('button-release-event', self.on_drag, False)
        row.pack_start(scale, True, True, 0)
        self.volume_widgets = (mute, scale)
        return row

    def set_mute_icon(self, mute):
        mute.set_image(Gtk.Image.new_from_icon_name(
            'audio-volume-muted-symbolic' if mute.get_active() else 'audio-volume-high-symbolic', Gtk.IconSize.BUTTON))

    def show_more(self, limit):
        self.limit = limit
        self.rebuild()
        self.window.show_all()

    def on_drag(self, _scale, _event, held):
        self.dragging = held
        if not held and self.pending:
            GLib.idle_add(self.refresh, priority=REBUILD_PRIORITY)
        return False

    def on_mute(self, button):
        volume.toggle_mute()
        self.set_mute_icon(button)

    def on_volume(self, scale):
        # debounced: a drag runs pactl once it pauses, not for every pixel
        if self.volume_timer is not None:
            GLib.source_remove(self.volume_timer)
        self.volume_timer = GLib.timeout_add(VOLUME_DELAY, self.apply_volume, scale.get_value())

    def apply_volume(self, value):
        self.volume_timer = None
        volume.set_volume(value)
        return False

    def player_row(self, playing):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class('center-card')
        row.get_style_context().add_class('center-player')
        slot = Gtk.Box()                          # the art, now or once it is downloaded
        row.pack_start(slot, False, False, 0)
        self.fill_art(slot, playing['art'])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_valign(Gtk.Align.CENTER)
        detail = ' · '.join(p for p in (playing['artist'], playing['album']) if p)
        state = f"{playing['player']} · {playing['status'].lower()}"
        for value, part in ((playing['title'], 'title'), (detail, 'body'), (state, 'appname')):
            if value:
                # one line each, cut with ... (wrapping a narrow column broke words mid-way)
                label = self.renderer.label(GLib.markup_escape_text(value), part, wrap=False)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_tooltip_text(value)
                text.pack_start(label, False, False, 0)
        row.pack_start(text, True, True, 0)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        for label, method in (('⏮', 'Previous'), ('⏸' if playing['status'] == 'Playing' else '▶', 'PlayPause'),
                              ('⏭', 'Next')):
            button = Gtk.Button(label=label)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.connect('clicked', lambda _b, m=method: mpris.command(self.bus, playing['name'], m))
            buttons.pack_start(button, False, False, 0)
        buttons.set_valign(Gtk.Align.CENTER)
        row.pack_end(buttons, False, False, 0)
        return row

    ART_SIZE = 56

    def fill_art(self, slot, url):
        """Put the cover in SLOT: a local file or icon now, an http(s) one from the cache, else once
        it is fetched (center.remote-art) - in place, so the panel is not rebuilt for it."""
        if not url:
            return
        source = url
        if art.remote(url):
            if not self.settings().get('remote-art', True):
                return
            source = art.fetch(url, lambda path: path and self.place_art(slot, str(path)))
            if source is None:
                return
        self.place_art(slot, str(source))

    def place_art(self, slot, source):
        pixbuf = self.renderer.load_image(source, self.ART_SIZE)
        if pixbuf is None or slot.get_parent() is None:   # unreadable, or the row was rebuilt meanwhile
            return
        for child in slot.get_children():
            slot.remove(child)
        slot.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), False, False, 0)
        slot.show_all()

    def card(self, entry, style, opened):
        classes = style.get('classes') or {}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        context = box.get_style_context()
        context.add_class('center-card')
        context.add_class(entry.get('urgency', 'normal'))
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.pack_start(self.renderer.label(GLib.markup_escape_text(entry.get('app') or ''), 'appname', wrap=False),
                       True, True, 0)
        stamp = time.strftime('%H:%M', time.localtime(entry.get('received', time.time())))
        top.pack_start(self.renderer.label(stamp, 'time', wrap=False), False, False, 0)
        flag = self.renderer.markup_flag(entry.get('summary'), entry.get('body'), 'full', classes)
        if flag is not None:
            top.pack_start(flag, False, False, 0)
        close = Gtk.Button(label='×')
        close.set_relief(Gtk.ReliefStyle.NONE)
        nid = entry['id']
        if opened:
            close.connect('clicked', lambda _b: self.manager.close(nid))
        else:
            close.connect('clicked', lambda _b: self.forget(nid))
        top.pack_end(close, False, False, 0)
        box.pack_start(top, False, False, 0)
        if entry.get('summary'):
            box.pack_start(self.renderer.label(prepare(entry['summary'], 'full', classes), 'title'), False, False, 0)
        if entry.get('body'):
            body = self.renderer.label(prepare(entry['body'], 'full', classes), 'body')
            body.set_lines(3)
            body.set_ellipsize(Pango.EllipsizeMode.END)
            box.pack_start(body, False, False, 0)
        events = Gtk.EventBox()
        events.set_visible_window(False)
        events.add(box)
        if not opened:
            events.connect('button-release-event',
                           lambda _w, e: e.time != self.renderer.link_clicked and self.manager.pop(nid) and False)
        self.cards.append((events, entry, opened))
        return events

    # -- keyboard -------------------------------------------------------------
    def on_key(self, _widget, event):
        if toggle_key_matches(self.settings().get('toggle-key'), event.keyval, event.state):
            self.hide()
            return True
        found = KEYS.get(Gdk.keyval_name(event.keyval))
        if found is None or event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            return False
        getattr(self, found[0])(*found[1:])
        return True

    def index(self):
        keys = [(opened, entry['id']) for _box, entry, opened in self.cards]
        return keys.index(self.selected) if self.selected in keys else None

    def select(self, index):
        for number, (box, _entry, _opened) in enumerate(self.cards):
            context = box.get_child().get_style_context()
            (context.add_class if number == index else context.remove_class)('selected')
        if index is None:
            self.selected = self.selected_index = None
            return
        box, entry, opened = self.cards[index]
        group = [number for number, card in enumerate(self.cards) if card[2] == opened]
        self.selected, self.selected_index = (opened, entry['id']), group.index(index)
        GLib.idle_add(self.scroll_to, box, priority=SCROLL_PRIORITY)

    def scroll_to(self, box):
        if self.scroll is None or box.get_parent() is None:
            return False
        rows = self.scroll.get_child()
        rows = rows.get_child() if isinstance(rows, Gtk.Viewport) else rows
        position = box.translate_coordinates(rows, 0, 0)
        if position:
            top = position[1]
            self.scroll.get_vadjustment().clamp_page(top, top + box.get_allocated_height())
        return False

    def restore_selection(self):
        """The selected card after a rebuild: the same one, else the one now in its place."""
        if self.selected is None or not self.cards:
            self.select(None)
            return
        index = self.index()
        if index is None:
            # gone (closed, forgotten, shown again): the card now in its place in the same group
            group = [number for number, card in enumerate(self.cards) if card[2] == self.selected[0]]
            group = group or list(range(len(self.cards)))
            index = group[min(self.selected_index or 0, len(group) - 1)]
        self.select(index)

    def move(self, delta):
        if not self.cards:
            return
        index = self.index()
        if index is None:
            index = 0 if delta > 0 else len(self.cards) - 1
        elif index + delta >= len(self.cards) and self.more:
            self.show_more(self.more)              # j at the last card loads more
            index = min(index + delta, len(self.cards) - 1)
        else:
            index = max(0, min(len(self.cards) - 1, index + delta))
        self.select(index)

    def move_to(self, index):
        if self.cards:
            self.select(index % len(self.cards))

    def activate(self):
        index = self.index()
        if index is None:
            return
        _box, entry, opened = self.cards[index]
        if opened:
            self.manager.click(entry['id'], 'left')
        else:
            self.manager.pop(entry['id'])

    def remove(self):
        index = self.index()
        if index is None:
            return
        _box, entry, opened = self.cards[index]
        if opened:
            self.manager.close(entry['id'])
        else:
            self.forget(entry['id'])

    def toggle_pause(self):
        self.manager.pause(not self.manager.paused)

    def next_mode(self):
        modes = known_modes(self.manager.config.rules, self.manager.mode)
        self.manager.set_mode(modes[(modes.index(self.manager.mode) + 1) % len(modes)])

    def player(self, method):
        playing = self.built[1] if self.built else None
        if playing and self.bus:
            mpris.command(self.bus, playing['name'], method)

    def step_volume(self, delta):
        if not self.volume_widgets:
            return
        _mute, scale = self.volume_widgets
        scale.set_value(max(0, min(100, scale.get_value() + delta)))   # the slider runs pactl (debounced)

    def forget(self, nid):
        self.manager.forget(nid)

    def clear(self):
        self.manager.close_all()
        self.manager.forget()
