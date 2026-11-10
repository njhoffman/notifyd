"""The notification center: deadd's sidebar with what is open and what was closed.

A full-height panel on one side of the monitor under the pointer, styled by the current preset.
Its header has the pause (do not disturb) toggle, the mode, and clear; below are the open
notifications, then the history, newest first. Clicking a closed one shows it again, x forgets it.
It hides when the pointer leaves (center.hide-on-leave), on `notifyd ctl center`, or on SIGUSR1,
which also opens it (the i3 binding deadd used).
"""

import time

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from notifyd import mpris
from notifyd.config import ConfigError, load_preset
from notifyd.markup import prepare
from notifyd.render.css import build_css, resolve_style, style_class

REFRESH_EVENTS = {'shown', 'replaced', 'expired', 'dismissed', 'closed', 'suppressed', 'paused', 'resumed', 'mode',
                  'reload', 'history'}


class Center:
    def __init__(self, renderer):
        self.renderer = renderer
        self.manager = renderer.manager
        self.window = None
        self.pending = False
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
            self.window.add_events(Gdk.EventMask.LEAVE_NOTIFY_MASK)
            self.window.connect('leave-notify-event', self.on_leave)
        self.rebuild()
        self.place()
        self.window.show_all()
        self.renderer.log.emit('info', message='center shown')

    def hide(self):
        if self.window is not None:
            self.window.hide()

    def place(self):
        area = self.renderer.monitor_geometry()
        width = int(self.settings().get('width') or 400)
        self.window.set_size_request(width, area.height)
        self.window.resize(width, area.height)
        x = area.x if self.settings().get('side') == 'left' else area.x + area.width - width
        self.window.move(x, area.y)

    def on_leave(self, _widget, event):
        if event.detail != Gdk.NotifyType.INFERIOR and self.settings().get('hide-on-leave', True):
            self.hide()

    def on_event(self, event):
        if event['event'] in REFRESH_EVENTS and self.visible and not self.pending:
            self.pending = True
            GLib.idle_add(self.refresh)

    def on_player(self, *_args):
        self.on_event({'event': 'shown'})

    def refresh(self):
        self.pending = False
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

    def rebuild(self):
        scope, style = self.scope()
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
        clock = self.renderer.label(time.strftime('%H:%M'), 'center-time', wrap=False)
        header.pack_start(clock, True, True, 0)
        pause = Gtk.ToggleButton(label='Do not disturb')
        pause.set_active(self.manager.paused)
        pause.connect('toggled', lambda b: self.manager.pause(b.get_active()))
        header.pack_end(pause, False, False, 0)
        panel.pack_start(header, False, False, 0)

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mode = f'mode {self.manager.mode}' + (f'  ·  {len(self.manager.held_back)} held back'
                                             if self.manager.held_back else '')
        status.pack_start(self.renderer.label(GLib.markup_escape_text(mode), 'appname', wrap=False), True, True, 0)
        clear = Gtk.Button(label='Clear')
        clear.connect('clicked', lambda _b: self.clear())
        status.pack_end(clear, False, False, 0)
        panel.pack_start(status, False, False, 0)

        playing = mpris.now_playing(self.bus) if self.bus and self.settings().get('mpris', True) else None
        if playing:
            panel.pack_start(self.player_row(playing), False, False, 0)

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        open_entries = self.manager.listing()
        closed = list(reversed(list(self.manager.history)))
        for entry in open_entries:
            rows.pack_start(self.card(entry, style, opened=True), False, False, 0)
        if open_entries and closed:
            rows.pack_start(Gtk.Separator(), False, False, 4)
        for entry in closed:
            rows.pack_start(self.card(entry, style, opened=False), False, False, 0)
        if not open_entries and not closed:
            rows.pack_start(self.renderer.label('<i>No notifications</i>', 'body'), False, False, 12)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(rows)
        panel.pack_start(scroll, True, True, 0)
        self.window.add(panel)

    def player_row(self, playing):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class('center-card')
        row.get_style_context().add_class('center-player')
        art = self.renderer.load_image(playing['art'], 56) if playing['art'] else None
        if art is not None:
            row.pack_start(Gtk.Image.new_from_pixbuf(art), False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.pack_start(self.renderer.label(GLib.markup_escape_text(playing['title']), 'title'), False, False, 0)
        detail = ' · '.join(p for p in (playing['artist'], playing['album']) if p)
        if detail:
            text.pack_start(self.renderer.label(GLib.markup_escape_text(detail), 'body'), False, False, 0)
        state = f"{playing['player']} · {playing['status'].lower()}"
        text.pack_start(self.renderer.label(GLib.markup_escape_text(state), 'appname', wrap=False), False, False, 0)
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
            events.connect('button-release-event', lambda _w, _e: self.manager.pop(nid) and False)
        return events

    def forget(self, nid):
        self.manager.forget(nid)

    def clear(self):
        self.manager.close_all()
        self.manager.forget()
