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
from notifyd.model import DISMISSED
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
# the center's keys: (key names, what they read as in the help, method and arguments); the help
# window (? or ctrl+/) is built from the same table, so it cannot drift from what the keys do
BINDINGS = [
    (('j', 'Down'), 'j / down', 'next card', ('move', 1)),
    (('k', 'Up'), 'k / up', 'previous card', ('move', -1)),
    (('g', 'Home'), 'g / home', 'first card', ('move_to', 0)),
    (('G', 'End'), 'G / end', 'last card', ('move_to', -1)),
    (('Return', 'KP_Enter'), 'enter', 'act on the card (shows a closed one again)', ('activate',)),
    (('d', 'Delete'), 'd / delete', 'close it, or forget it from history', ('remove',)),
    (('e',), 'e', 'whole body of the card, or three lines', ('toggle_expand',)),
    (('i',), 'i', 'details of the card: app, icon, sender, rules, times', ('show_details',)),
    (('a',), 'a', 'history by app, or one list', ('toggle_grouping',)),
    (('C',), 'C', 'clear everything', ('clear',)),
    (('r',), 'r', 'type a reply (a card that takes one; enter sends, esc leaves it)', ('reply_selected',)),
    (('p',), 'p', 'do not disturb on or off', ('toggle_pause',)),
    (('m',), 'm', 'next mode', ('next_mode',)),
    (('space',), 'space', 'play / pause', ('player', 'PlayPause')),
    (('n',), 'n', 'next track', ('player', 'Next')),
    (('N',), 'N', 'previous track', ('player', 'Previous')),
    (('plus', 'equal', 'KP_Add'), '+', 'volume up', ('step_volume', VOLUME_STEP)),
    (('minus', 'KP_Subtract'), '-', 'volume down', ('step_volume', -VOLUME_STEP)),
    (('question',), '? / ctrl+/', 'this help', ('toggle_help',)),
    (('Escape', 'q'), 'esc / q / ctrl+[', 'close the center', ('hide',)),
]
KEYS = {name: action for names, _label, _what, action in BINDINGS for name in names}

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


DETAIL_CHARS = 400   # longest value `i` shows in full
# what `i` shows of a card, in this order (empty ones are left out)
DETAIL_FIELDS = ('app', 'summary', 'body', 'app_icon', 'image', 'thumb', 'urgency', 'category', 'stack_tag',
                 'rules', 'timeout', 'markup', 'mode', 'actions', 'duplicates', 'sender', 'hints', 'state',
                 'received', 'closed', 'closed_at', 'id')


def with_icon(button, icon, tooltip=None):
    """BUTTON with the theme icon ICON (symbolic ones take the text color) before its label, if any."""
    if icon and Gtk.IconTheme.get_default().has_icon(icon):
        button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
        button.set_always_show_image(True)
    elif icon and not button.get_label():
        button.set_label(icon)                      # an unknown icon name on a bare button: show the name
    if tooltip:
        button.set_tooltip_text(tooltip)
    return button


def card_key(entry, opened):
    """What tells cards apart: an open one by id; a history one by id and when it closed, since ids
    repeat when a sender reuses a replaces_id (backdrop sends 500 each time)."""
    return (opened, entry['id'], None if opened else entry.get('closed_at'))


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
        self.selected = None       # card_key() of the selected card
        self.selected_index = None # its place among the open or the closed cards, for its neighbor once gone
        self.more = None           # the next limit when "show more" is there
        self.scroll = None
        self.grabbed = False
        renderer.log.listeners.append(self.on_event)
        self.bus = None
        self.players = None
        self.expanded = set()      # (opened, id) of cards whose whole body shows (`e`)
        self.button_widgets = []   # (button, spec) of center.buttons
        self.expanded_groups = set()   # apps whose history group shows every card (center.group)
        self.help_window = None        # the key help (? or ctrl+/) while it is open
        self.group_override = None     # By app / `a` until the next reload; None: center.group
        self.volume_level = None   # the last level read (reads are async, so the center never waits on pactl)
        self.reply_entries = {}    # id -> the text field of an open card that takes a reply
        self.reply_drafts = {}     # id -> what was typed there, kept over rebuilds
        self.reply_focus = None    # the id whose field has the keyboard, kept over rebuilds
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            # players kept from their signals: the now-playing row follows them without a blocking call
            self.players = mpris.Players(self.bus, on_change=self.on_player)
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
        self.request_volume()                     # the row updates (or appears) when pactl answers
        self.manager.seen()                       # what expired unclicked has been seen now
        self.refresh_button_states()
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
        self.hide_help()
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
        if event.detail == Gdk.NotifyType.INFERIOR or not self.settings().get('hide-on-leave', True):
            return
        # a popup or the help mapping under the pointer sends a leave too: the pointer is still over us
        x, y = self.window.get_position()
        width, height = self.window.get_size()
        if x <= event.x_root < x + width and y <= event.y_root < y + height:
            return
        self.hide()

    def on_event(self, event):
        if event['event'] not in REFRESH_EVENTS:
            return
        if event['event'] == 'reload':
            self.group_override = None             # the config's center.group again
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
        if self.players is None or not self.settings().get('mpris', True):
            return None
        return self.players.now_playing(self.settings().get('mpris-ignore') or ())

    def volume_wanted(self):
        return self.settings().get('volume', True) and volume.available()

    def request_volume(self):
        if self.volume_wanted():
            volume.get_async(self.on_volume_level)

    def on_volume_level(self, level):
        appeared = (level is None) != (self.volume_level is None)
        self.volume_level = level
        if not self.visible:
            return
        if appeared:
            self.on_event({'event': 'shown'})      # the row comes or goes: rebuild
        elif not self.dragging:
            self.update_live()

    def update_live(self):
        """What changes without an event, in place: the clock and the volume."""
        self.clock.set_text(time.strftime('%H:%M'))
        if self.volume_widgets:
            level = self.volume_level
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

        # the top part keeps the panel's padding; the cards below run edge to edge, each with its own
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top.get_style_context().add_class('center-top')
        panel.pack_start(top, False, False, 0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.clock = self.renderer.label(time.strftime('%H:%M'), 'center-time', wrap=False)
        header.pack_start(self.clock, True, True, 0)
        pause = with_icon(Gtk.ToggleButton(label='Do not disturb'), 'notifications-disabled-symbolic',
                          'hold back all but critical notifications (p)')
        pause.set_active(self.manager.paused)
        pause.connect('toggled', lambda b: self.manager.pause(b.get_active()))
        header.pack_end(pause, False, False, 0)
        top.pack_start(header, False, False, 0)

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if self.settings().get('modes', True):
            status.pack_start(self.mode_picker(), False, False, 0)
            text = f'{len(self.manager.held_back)} held back' if self.manager.held_back else ''
        else:
            text = f'mode {self.manager.mode}' + (f'  ·  {len(self.manager.held_back)} held back'
                                                 if self.manager.held_back else '')
        status.pack_start(self.renderer.label(GLib.markup_escape_text(text), 'appname', wrap=False), True, True, 0)
        clear = with_icon(Gtk.Button(label='Clear'), 'edit-clear-all-symbolic',
                          'close every notification and forget the history (C)')
        clear.connect('clicked', lambda _b: self.clear())
        status.pack_end(clear, False, False, 0)
        by_app = with_icon(Gtk.ToggleButton(label='By app'), 'view-app-grid-symbolic',
                           'history grouped by app, or one list (a)')
        by_app.get_style_context().add_class('center-group-toggle')
        by_app.set_active(self.grouped())
        by_app.connect('toggled', lambda b: b.get_active() != self.grouped() and self.toggle_grouping())
        status.pack_end(by_app, False, False, 0)
        top.pack_start(status, False, False, 0)

        if playing:
            top.pack_start(self.player_row(playing), False, False, 0)
        level = self.volume_level if self.volume_wanted() else None
        self.volume_widgets = None
        if level is not None:
            top.pack_start(self.volume_row(level), False, False, 0)
        buttons = self.buttons_row()
        if buttons is not None:
            top.pack_start(buttons, False, False, 0)

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.cards = []
        self.reply_entries = {}
        self.reply_drafts = {nid: text for nid, text in self.reply_drafts.items() if nid in self.manager.active}
        self.more = None
        open_entries = self.manager.listing()
        closed = list(reversed(list(self.manager.history)))
        limit = self.limit or int(self.settings().get('cards') or CARDS)
        for entry in open_entries:
            rows.pack_start(self.card(entry, style, opened=True), False, False, 0)
        if open_entries and closed:
            rows.pack_start(Gtk.Separator(), False, False, 4)
        if self.grouped():
            self.grouped_history(rows, closed[:limit], style)
        else:
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
        if self.reply_focus in self.reply_entries:   # typing a reply: the new field takes the keyboard
            self.focus_reply(self.reply_focus)
        elif self.reply_focus is not None:         # its card went (replied elsewhere, closed)
            self.reply_focus = None

    def grouped_history(self, rows, entries, style):
        """center.group: history in one group per app, newest app first; a group shows its newest card
        until its header is clicked, and its × forgets the whole group."""
        groups = {}
        for entry in entries:                          # newest first already
            groups.setdefault(entry.get('app') or '', []).append(entry)
        for app, members in groups.items():
            rows.pack_start(self.group_header(app, members), False, False, 0)
            shown = members if app in self.expanded_groups else members[:1]
            for entry in shown:
                rows.pack_start(self.card(entry, style, opened=False), False, False, 0)

    def group_header(self, app, members):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.get_style_context().add_class('center-group')
        expanded = app in self.expanded_groups
        arrow = '▾' if expanded or len(members) == 1 else '▸'
        title = GLib.markup_escape_text(app or 'no app')
        box.pack_start(self.renderer.label(f'{arrow} <b>{title}</b>', 'appname', wrap=False), True, True, 0)
        box.pack_start(self.renderer.label(str(len(members)), 'count', wrap=False), False, False, 0)
        clear = Gtk.Button(label='×')
        clear.set_relief(Gtk.ReliefStyle.NONE)
        clear.set_tooltip_text(f'forget these {len(members)}')
        clear.connect('clicked', lambda _b: self.forget_group(members))
        box.pack_end(clear, False, False, 0)
        events = Gtk.EventBox()
        events.set_visible_window(False)
        events.add(box)
        events.connect('button-release-event', lambda _w, _e: self.toggle_group(app) or False)
        return events

    def grouped(self):
        """History by app: the By app button / `a` while set, else center.group."""
        if self.group_override is not None:
            return self.group_override
        return bool(self.settings().get('group'))

    def toggle_grouping(self):
        self.group_override = not self.grouped()
        self.dirty = True
        self.refresh()

    def toggle_group(self, app):
        self.expanded_groups ^= {app}
        self.dirty = True
        self.refresh()

    def forget_group(self, members):
        for entry in members:
            self.manager.forget(entry=entry)
        self.dirty = True
        self.refresh()

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

    # -- buttons (center.buttons) ----------------------------------------------
    BUTTON_STATE_DELAY = 600   # ms after a click before its state command runs again

    def buttons_row(self):
        """A row of center.buttons: each runs its `run`; one with `state` shows as on while that exits 0."""
        config = self.settings().get('buttons') or []
        if not config:
            self.button_widgets = []
            return None
        row = Gtk.FlowBox()
        row.set_selection_mode(Gtk.SelectionMode.NONE)
        row.set_max_children_per_line(4)
        row.set_homogeneous(True)
        row.get_style_context().add_class('center-buttons')
        self.button_widgets = []
        for spec in config:
            button = with_icon(Gtk.Button(label=spec['label']), spec.get('icon'), spec.get('run'))
            button.get_style_context().add_class('center-button')
            button.connect('clicked', self.on_center_button, spec)
            row.add(button)
            self.button_widgets.append((button, spec))
        self.refresh_button_states()
        return row

    def on_center_button(self, button, spec):
        self.spawn(['sh', '-c', spec['run']])
        if spec.get('state'):
            GLib.timeout_add(self.BUTTON_STATE_DELAY, lambda: self.check_button_state(button, spec) and False)

    def refresh_button_states(self):
        for button, spec in self.button_widgets:
            if spec.get('state'):
                self.check_button_state(button, spec)

    def check_button_state(self, button, spec):
        """Run SPEC's state command without waiting; BUTTON gets the `on` class while it exits 0."""
        proc = self.spawn(['sh', '-c', spec['state']])
        if proc is None:
            return

        def done(proc, result):
            try:
                proc.wait_finish(result)
            except GLib.Error:
                return
            context = button.get_style_context()
            (context.add_class if proc.get_successful() else context.remove_class)('on')
        proc.wait_async(None, done)

    def spawn(self, argv):
        try:
            return Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as exc:
            self.renderer.log.warn(f'center button: {exc.message}')
            return None

    def on_drag(self, _scale, _event, held):
        self.dragging = held
        if not held and self.pending:
            GLib.idle_add(self.refresh, priority=REBUILD_PRIORITY)
        return False

    def on_mute(self, button):
        volume.toggle_mute()
        if self.volume_level is not None:
            self.volume_level = {**self.volume_level, 'muted': button.get_active()}
        self.set_mute_icon(button)

    def on_volume(self, scale):
        # debounced: a drag runs pactl once it pauses, not for every pixel
        if self.volume_timer is not None:
            GLib.source_remove(self.volume_timer)
        self.volume_timer = GLib.timeout_add(VOLUME_DELAY, self.apply_volume, scale.get_value())

    def apply_volume(self, value):
        self.volume_timer = None
        volume.set_volume(value)
        if self.volume_level is not None:
            self.volume_level = {**self.volume_level, 'volume': round(value)}
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
        playing_now = playing['status'] == 'Playing'
        for icon, method, tip in (('media-skip-backward-symbolic', 'Previous', 'previous track (N)'),
                                  ('media-playback-pause-symbolic' if playing_now else 'media-playback-start-symbolic',
                                   'PlayPause', 'pause (space)' if playing_now else 'play (space)'),
                                  ('media-skip-forward-symbolic', 'Next', 'next track (n)')):
            button = with_icon(Gtk.Button(), icon, tip)
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
        scale = self.renderer.scale()
        pixbuf = self.renderer.load_image(source, self.ART_SIZE * scale)
        if pixbuf is None or slot.get_parent() is None:   # unreadable, or the row was rebuilt meanwhile
            return
        for child in slot.get_children():
            slot.remove(child)
        slot.pack_start(self.renderer.picture_widget(pixbuf, scale), False, False, 0)
        slot.show_all()

    CARD_LINES = 3      # body lines on a card until `e` shows it all

    def card(self, entry, style, opened):
        """ENTRY's card, built as its popup is (its own style, preset, hidden parts and picture sizes),
        with a close button, the body capped at CARD_LINES until `e`, and a click that shows it again."""
        n = self.manager.active.get(entry['id']) if opened else None
        if n is None:
            n = self.manager.entry_notification(entry)
        scope, card_style = self.renderer.style_for(n)
        # a clickable close mark, as tall as the header's text (a GtkButton adds the theme's button height)
        close = Gtk.EventBox()
        close.set_visible_window(False)
        close.add(self.renderer.label('×', 'center-close', wrap=False))
        close.set_tooltip_text('close' if opened else 'forget')
        nid = entry['id']
        action = (lambda: self.manager.close(nid, DISMISSED)) if opened else (lambda: self.forget(entry))
        close.connect('button-release-event', lambda _w, e: e.button == 1 and (action() or True))
        lines = 0 if card_key(entry, opened) in self.expanded else self.CARD_LINES
        card, parts = self.renderer.build_card(n, card_style, body_lines=lines, header_end=close, actions=opened,
                                               reply=self.send_reply if opened else None)
        if parts.get('reply') is not None:
            self.keep_reply(nid, parts['reply'])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)       # carries the card's own style scope
        context = box.get_style_context()
        context.add_class(scope)
        context.add_class('center-card')
        context.add_class(n.urgency_name)
        box.pack_start(card, False, False, 0)
        events = Gtk.EventBox()
        events.set_visible_window(False)
        events.add(box)
        # a click on an action link of an open card invokes it (a closed card's links act on nothing)
        events.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        events.connect('motion-notify-event', self.renderer.on_motion)
        if opened:
            events.connect('button-release-event', self.renderer.action_link_hit)
        else:
            events.connect('button-release-event',
                           lambda _w, e: e.time != self.renderer.link_clicked and self.manager.pop(entry=entry)
                           and False)
        self.cards.append((events, entry, opened))
        return events

    # -- inline reply ---------------------------------------------------------
    def keep_reply(self, nid, entry):
        """Track NID's reply field: its draft and focus survive the center being built again."""
        self.reply_entries[nid] = entry
        entry.set_text(self.reply_drafts.get(nid, ''))
        entry.connect('changed', lambda e: self.reply_drafts.__setitem__(nid, e.get_text()))
        entry.connect('focus-in-event', lambda *_a: setattr(self, 'reply_focus', nid) and False)

    def focus_reply(self, nid):
        entry = self.reply_entries.get(nid)
        if entry is None or self.window is None:
            return False
        self.reply_focus = nid
        self.window.set_focus(entry)
        entry.set_position(-1)
        keys = [card_key(e, opened) for _box, e, opened in self.cards]
        if (True, nid, None) in keys:
            self.select(keys.index((True, nid, None)))
        return True

    def reply_to(self, nid):
        """Open the center on NID's card with its reply field focused (a popup's Reply button)."""
        if self.manager.active.get(nid) is None:
            return False
        self.reply_focus = nid
        self.dirty = True
        if self.visible:
            self.refresh()
        else:
            self.toggle('show')
        return self.focus_reply(nid)

    def send_reply(self, nid, text):
        self.reply_drafts.pop(nid, None)
        if self.reply_focus == nid:
            self.reply_focus = None
            if self.window is not None:
                self.window.set_focus(None)
        self.manager.reply(nid, text)

    def reply_selected(self):
        """`r`: type a reply to the selected card, when it is open and takes one."""
        index = self.index()
        if index is None:
            return
        _box, entry, opened = self.cards[index]
        if opened:
            self.focus_reply(entry['id'])

    # -- keyboard -------------------------------------------------------------
    def on_key(self, _widget, event):
        if toggle_key_matches(self.settings().get('toggle-key'), event.keyval, event.state):
            self.hide()
            return True
        name = Gdk.keyval_name(event.keyval)
        focus = self.window.get_focus() if self.window is not None else None
        if isinstance(focus, Gtk.Entry):           # typing a reply: keys are text, Esc leaves the field
            if name == 'Escape' or (name == 'bracketleft' and event.state & Gdk.ModifierType.CONTROL_MASK):
                self.reply_focus = None
                self.window.set_focus(None)
                return True
            return False
        help_key = name == 'question' or (name == 'slash' and event.state & Gdk.ModifierType.CONTROL_MASK)
        if self.help_window is not None:           # any key closes the help (and does nothing else)
            self.hide_help()
            return True
        if help_key:
            self.toggle_help()
            return True
        if name == 'bracketleft' and event.state & Gdk.ModifierType.CONTROL_MASK:
            name = 'Escape'                        # ctrl+[ is escape, as in a terminal
        found = KEYS.get(name)
        if name == 'Escape':
            self.hide()
            return True
        if found is None or event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            return False
        getattr(self, found[0])(*found[1:])
        return True

    def index(self):
        keys = [card_key(entry, opened) for _box, entry, opened in self.cards]
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
        self.selected, self.selected_index = card_key(entry, opened), group.index(index)
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
            self.manager.pop(entry=entry)

    def remove(self):
        index = self.index()
        if index is None:
            return
        _box, entry, opened = self.cards[index]
        if opened:
            self.manager.close(entry['id'], DISMISSED)
        else:
            self.forget(entry)

    # -- help (? or ctrl+/) -----------------------------------------------------
    def toggle_help(self):
        if self.help_window is not None:
            self.hide_help()
        else:
            self.show_help()

    def show_help(self):
        """A small window over the center listing every key, like which-key; any key closes it."""
        rows = [(f'<b>{GLib.markup_escape_text(label)}</b>', GLib.markup_escape_text(what))
                for _names, label, what, _action in BINDINGS]
        self.show_overlay('keys', rows, 'help-key')

    def show_details(self):
        """`i`: every field of the selected card, in the same kind of window as the help."""
        index = self.index()
        if index is None:
            return
        _box, entry, opened = self.cards[index]
        n = self.manager.active.get(entry['id']) if opened else None
        fields = dict(entry)
        if n is not None:
            fields.update({'app_icon': n.app_icon, 'state': entry.get('state', 'open')})
            fields.setdefault('image', n.image_path)
            fields['hints'] = ', '.join(sorted(k for k, v in n.hints.items() if not isinstance(v, tuple))) or None
        rows = []
        for key in DETAIL_FIELDS:
            value = fields.get(key)
            if value in (None, '', [], {}):
                continue
            if key in ('received', 'closed_at'):
                value = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(value))
            elif key == 'sender':
                value = '  '.join(f'{k} {v}' for k, v in value.items())
            elif isinstance(value, list):
                value = ', '.join(map(str, value))
            elif key == 'timeout':
                value = f'{value} ms' if value else 'never'
            value = str(value)
            if len(value) > DETAIL_CHARS:                  # a long body would make the window taller than the screen
                value = value[:DETAIL_CHARS].rstrip() + '…'
            rows.append((f'<b>{GLib.markup_escape_text(key)}</b>', GLib.markup_escape_text(value)))
        self.show_overlay(f'#{entry["id"]}', rows, 'detail-key', wrap=True)

    def show_overlay(self, title, rows, key_class, wrap=False):
        """A small window over the center: TITLE, then ROWS of (key markup, value markup); any key
        closes it (the help and the details use it)."""
        if self.window is None:
            return
        self.hide_help()
        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_title('notifyd keys')
        window.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        if self.renderer.visual is not None:
            window.set_visual(self.renderer.visual)
        window.set_app_paintable(True)
        context = window.get_style_context()
        context.add_class('notifyd')
        context.add_class(self.scope()[0])
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame.get_style_context().add_class('notification')
        frame.get_style_context().add_class('center-help')
        frame.pack_start(self.renderer.label(f'<b>{GLib.markup_escape_text(title)}</b>', 'title', wrap=False),
                         False, False, 0)
        grid = Gtk.Grid(column_spacing=14, row_spacing=2)
        for row, (key_markup, value_markup) in enumerate(rows):
            key = self.renderer.label(key_markup, key_class, wrap=False)
            key.set_valign(Gtk.Align.START)
            grid.attach(key, 0, row, 1, 1)
            value = self.renderer.label(value_markup, 'body', wrap=wrap)
            if wrap:
                value.set_max_width_chars(48)
                value.set_selectable(False)
            grid.attach(value, 1, row, 1, 1)
        frame.pack_start(grid, False, False, 0)
        frame.pack_start(self.renderer.label('<i>any key closes this</i>', 'time', wrap=False), False, False, 0)
        window.add(frame)
        window.show_all()
        x, y = self.window.get_position()
        width, height = self.window.get_size()
        help_width, help_height = window.get_size()
        window.move(x + max((width - help_width) // 2, 0), y + max((height - help_height) // 3, 0))
        self.help_window = window

    def hide_help(self):
        if self.help_window is not None:
            self.help_window.destroy()
            self.help_window = None

    def toggle_expand(self):
        """`e`: the selected card's whole body, or back to three lines."""
        if self.selected is None:
            return
        self.expanded ^= {self.selected}
        self.dirty = True
        self.refresh()

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

    def forget(self, entry):
        self.manager.forget(entry=entry)

    def clear(self):
        self.manager.close_all()
        self.manager.forget()
