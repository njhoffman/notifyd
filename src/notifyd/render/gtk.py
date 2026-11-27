"""GTK3 popups on X11.

Each notification is an override-redirect window with an RGBA visual (translucency needs a
compositor), WM class notifyd/Notifyd and the title "notifyd": picom's shadow-exclude here skips
windows without a name (`! name~=''`) and those of class Dunst, so both matter for deadd's shadow.

Layout (deadd's):  [app icon] app name ........ time
                   [image]  title
                            body
                            progress bar
                            [action] [action]
"""

import os
import sys
import time
import warnings
from urllib.parse import unquote, urlparse

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from notifyd.config import ConfigError, load_preset  # noqa: E402
from notifyd.images import (  # noqa: E402, F401
    MAX_IMAGE_SIDE,
    desktop_icon,
    image_data_problem,
    pixbuf_from_image_data,
)
from notifyd.markup import markup_error, prepare, safe_link  # noqa: E402
from notifyd.render.center import Center  # noqa: E402
from notifyd.render.css import build_css, resolve_style, style_class  # noqa: E402

BUTTONS = {1: 'left', 2: 'middle', 3: 'right'}
IMAGE_HINTS = ('image-data', 'image_data')
IMAGE_PATH_HINTS = ('image-path', 'image_path')


def file_path(value):
    """A local path from a path or file:// URI, else None."""
    if not value:
        return None
    if value.startswith('file://'):
        value = unquote(urlparse(value).path)
    value = os.path.expanduser(value)
    return value if os.path.isabs(value) and os.path.isfile(value) else None


def scale_to(pixbuf, size):
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if max(width, height) <= size:
        return pixbuf
    factor = size / max(width, height)
    return pixbuf.scale_simple(max(int(width * factor), 1), max(int(height * factor), 1),
                               GdkPixbuf.InterpType.BILINEAR)


def pixbuf_from_data(data, size):
    return scale_to(pixbuf_from_image_data(data), size)


def load_pixbuf(source, size):
    """A pixbuf at most SIZE px from a file path/URI or an icon name; None when neither works."""
    if not source or size <= 0:
        return None
    path = file_path(source)
    try:
        if path:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
        theme = Gtk.IconTheme.get_default()
        if theme.has_icon(source):
            return scale_to(theme.load_icon(source, size, Gtk.IconLookupFlags.FORCE_SIZE), size)
    except GLib.Error:
        return None
    return None


class GtkRenderer:
    def __init__(self, manager, log):
        ok, _argv = Gtk.init_check(sys.argv)
        if not ok:
            raise RuntimeError('no X display (is DISPLAY set?)')
        self.manager = manager
        self.log = log
        self.link_clicked = None   # the event time of the last link click, which must not dismiss
        self.measured = None       # (notification, window) built by fits() for show()
        self.fades = {}            # window -> its running fade's GLib source
        self.fade_out = {}         # notification id -> its fade.out (ms), from its own settings
        self.screen = Gdk.Screen.get_default()
        self.visual = self.screen.get_rgba_visual()
        if self.visual is None or not self.screen.is_composited():
            log.warn('no compositor: notifications are opaque and have no shadow')
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(self.screen, self.provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.styles = {}     # scope class -> css
        self.window_scopes = {}   # notification id -> the scope class its window uses
        self.windows = {}    # id -> Gtk.Window
        self.area = None     # the work area the stack is on; chosen when the first one opens
        # displayd (xrandr) changing the layout: lay out again on the new geometry
        self.screen.connect('monitors-changed', self.on_monitors_changed)
        self.center = Center(self)

    # -- styles ---------------------------------------------------------
    def style_for(self, n):
        try:
            preset = load_preset(n.settings.get('preset') or 'deadd')['style']
        except ConfigError as exc:
            self.log.warn(str(exc))
            preset = load_preset('deadd')['style']
        style = resolve_style(preset, n.settings.get('style'))
        scope = style_class(style)
        if scope not in self.styles:
            self.styles[scope] = build_css(style, scope)
            self.load_css()
        return scope, style

    STYLE_SLACK = 8   # unused scopes kept before pruning, so a close does not reload the CSS each time

    def prune_styles(self):
        """Drop the CSS of styles no open window (or the center) uses. Distinct styles pile up when
        senders vary them per notification (dunst color hints); each one made the whole provider
        larger. Pruning reloads it once, so it waits until STYLE_SLACK unused ones gathered."""
        used = set(self.window_scopes.values())
        built = getattr(self.center, 'built', None)
        if built:
            used.add(built[0])
        unused = [scope for scope in self.styles if scope not in used]
        if len(unused) > self.STYLE_SLACK:
            for scope in unused:
                del self.styles[scope]
            self.load_css()

    def load_css(self):
        try:
            self.provider.load_from_data('\n'.join(self.styles.values()).encode())
        except GLib.Error as exc:
            self.log.error(f'style CSS: {exc.message}')

    def reload(self):
        self.styles = {}
        for nid in list(self.windows):
            n = self.manager.active.get(nid)
            if n:
                self.update(n)

    # -- windows --------------------------------------------------------
    def fits(self, n):
        """Whether N's window fits in the work area below (or above) the ones on screen. It is built
        now, unshown, to measure it, and show() uses it. The first one always fits, so a very tall
        notification still shows."""
        self.discard_measured()
        shown = [w for nid, w in self.windows.items() if nid in self.manager.displayed and nid != n.id]
        if not shown:
            return True
        window = self.new_window(n)
        self.measured = (n, window)
        position = self.manager.config.settings['position']
        margin = position.get('margin', [30, 5])
        margin_v = margin if isinstance(margin, (int, float)) else margin[0]
        gap = int(position.get('gap', 10))
        area = self.area or self.monitor_geometry()
        used = margin_v + sum(self.window_height(w) + gap for w in shown)
        if used + self.window_height(window) + margin_v <= area.height:
            return True
        self.discard_measured()                # it waits; it is built again when there is room
        return False

    @staticmethod
    def window_height(window):
        return window.get_preferred_size()[1].height

    def discard_measured(self):
        if self.measured is not None:
            n, window = self.measured
            if n.id not in self.windows:
                self.window_scopes.pop(n.id, None)
            window.destroy()
            self.measured = None

    def new_window(self, n):
        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_title('notifyd')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            window.set_wmclass('notifyd', 'Notifyd')
        window.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        window.set_accept_focus(False)
        window.set_app_paintable(True)
        if self.visual is not None:
            window.set_visual(self.visual)
        window.get_style_context().add_class('notifyd')
        self.fill(window, n)
        window.get_child().show_all()          # measurable before the window itself is shown
        return window

    def show(self, n):
        if self.measured is not None and self.measured[0] is n:
            window, self.measured = self.measured[1], None
        else:
            self.discard_measured()
            window = self.new_window(n)
        self.windows[n.id] = window
        self.fade_out[n.id] = (n.settings.get('fade') or {}).get('out') or 0   # a rule's fade.out counts
        fade_in = (n.settings.get('fade') or {}).get('in') or 0
        if fade_in:
            window.set_opacity(0)
        window.show_all()
        self.layout()
        if fade_in:
            self.fade(window, 0.0, 1.0, fade_in)

    def update(self, n):
        window = self.windows.get(n.id)
        if window is None:
            self.show(n)
            return
        child = window.get_child()
        if child:
            window.remove(child)
            child.destroy()
        self.fill(window, n)
        self.fade_out[n.id] = (n.settings.get('fade') or {}).get('out') or 0
        window.show_all()
        window.resize(1, 1)   # shrink to the new content
        self.layout()

    def close(self, nid):
        window = self.windows.pop(nid, None)
        fade_out = self.fade_out.pop(nid, 0)
        self.window_scopes.pop(nid, None)
        self.prune_styles()
        if window is not None:
            if fade_out and window.get_visible():
                self.fade(window, window.get_opacity(), 0.0, fade_out, then=window.destroy)
            else:
                self.stop_fade(window)         # a fade-in must not touch it once it is destroyed
                window.destroy()
        if not self.windows:
            self.area = None
        self.layout()

    def fade(self, window, start, end, ms, then=None):
        """Opacity from START to END over MS (about 60 steps a second), then THEN(). A new fade of the
        same window replaces the running one (a close during the fade-in fades out from where it is)."""
        self.stop_fade(window)
        steps = max(int(ms / 16), 1)
        state = {'step': 0}

        def tick():
            state['step'] += 1
            window.set_opacity(start + (end - start) * state['step'] / steps)
            if state['step'] >= steps:
                self.fades.pop(window, None)
                if then:
                    then()
                return False
            return True
        self.fades[window] = GLib.timeout_add(16, tick)

    def stop_fade(self, window):
        source = self.fades.pop(window, None)
        if source is not None:
            GLib.source_remove(source)

    def on_monitors_changed(self, _screen):
        self.area = None
        self.log.info('monitors changed; placing notifications again')
        self.layout()

    def fill(self, window, n):
        settings = n.settings
        scope, style = self.style_for(n)
        context = window.get_style_context()
        for name in [c for c in context.list_classes() if c.startswith('nd-')]:
            context.remove_class(name)
        context.add_class(scope)
        self.window_scopes[n.id] = scope
        width = int(settings.get('width') or 350)
        window.set_size_request(width, -1)
        window.set_default_size(width, -1)
        hide = set(settings.get('hide') or [])
        classes = style.get('classes') or {}
        mode = settings.get('markup', 'full')

        events = Gtk.EventBox()
        events.set_visible_window(False)
        events.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
                          | Gdk.EventMask.BUTTON_RELEASE_MASK)
        events.connect('enter-notify-event', self.on_enter, n.id)
        events.connect('leave-notify-event', self.on_leave, n.id)
        events.connect('button-release-event', self.on_click, n.id)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card_context = card.get_style_context()
        card_context.add_class('notification')
        card_context.add_class(n.urgency_name)
        if settings.get('class'):
            card_context.add_class(str(settings['class']))
        events.add(card)

        # image: image-data, image-path, a rule's image.path, else an app icon that is a file
        image_settings = settings.get('image') or {}
        image_size = int(image_settings.get('size') or 0)
        icon_settings = settings.get('icon') or {}
        icon_source = icon_settings.get('name') or n.app_icon or desktop_icon(n.desktop_entry)
        picture = None
        if 'image' not in hide:
            if image_settings.get('path'):
                picture = load_pixbuf(image_settings['path'], image_size)
            for hint in IMAGE_HINTS:
                if picture is None and isinstance(n.hints.get(hint), tuple):
                    picture = self.image_from_data(n, hint, image_size)
            for hint in IMAGE_PATH_HINTS:
                if picture is None and n.hints.get(hint):
                    picture = load_pixbuf(str(n.hints[hint]), image_size)
            if picture is None and file_path(icon_source):
                picture = load_pixbuf(icon_source, image_size)
                icon_source = None
            if picture is None and isinstance(n.hints.get('icon_data'), tuple):
                picture = self.image_from_data(n, 'icon_data', image_size)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.get_style_context().add_class('header')
        if 'app-icon' not in hide:
            icon = load_pixbuf(icon_source, int(icon_settings.get('size') or 0)) if icon_source else None
            if icon is not None:
                widget = Gtk.Image.new_from_pixbuf(icon)
                widget.get_style_context().add_class('appicon')
                header.pack_start(widget, False, False, 0)
        if 'app-name' not in hide and n.app:
            header.pack_start(self.label(GLib.markup_escape_text(n.app), 'appname', wrap=False), True, True, 0)
        if n.duplicates > 1:
            header.pack_start(self.label(f'×{n.duplicates}', 'count', wrap=False), False, False, 0)
        if 'time' not in hide:
            header.pack_end(self.label(time.strftime('%H:%M', time.localtime(n.received)), 'time', wrap=False),
                            False, False, 0)
        flag = self.markup_flag(n.summary, n.body, mode, classes)
        if flag is not None:
            header.pack_end(flag, False, False, 0)
        if header.get_children():
            card.pack_start(header, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        content.get_style_context().add_class('content')
        if picture is not None:
            image = Gtk.Image.new_from_pixbuf(picture)
            image.get_style_context().add_class('image')
            image.set_valign(Gtk.Align.START)
            image.set_margin_end(int(image_settings.get('margin-right', 0)) + 12)
            content.pack_start(image, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        if 'title' not in hide and n.summary:
            title = self.label(prepare(n.summary, mode, classes), 'title')
            title_lines = int((settings.get('title') or {}).get('max-lines') or 0)
            if title_lines:
                title.set_lines(title_lines)
                title.set_ellipsize(Pango.EllipsizeMode.END)
            text.pack_start(title, False, False, 0)
        if 'body' not in hide and n.body:
            body = self.label(prepare(n.body, mode, classes), 'body')
            lines = int((settings.get('body') or {}).get('max-lines') or 0)
            if lines:
                body.set_lines(lines)
                body.set_ellipsize(Pango.EllipsizeMode.END)
            text.pack_start(body, False, False, 0)
        if 'progress' not in hide and n.progress is not None:
            bar = Gtk.ProgressBar()
            bar.set_fraction(n.progress / 100)
            bar.set_margin_top(4)
            text.pack_start(bar, False, False, 0)
        content.pack_start(text, True, True, 0)
        card.pack_start(content, False, False, 0)

        buttons = [(key, label) for key, label in n.actions if key != 'default']
        if buttons and 'actions' not in hide:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, homogeneous=True)
            row.set_margin_top(8)
            for key, label in buttons:
                button = Gtk.Button(label=label)
                button.connect('clicked', lambda _b, k=key, nid=n.id: self.manager.invoke(nid, k))
                row.pack_start(button, True, True, 0)
            card.pack_start(row, False, False, 0)
        window.add(events)

    def markup_flag(self, summary, body, mode, classes):
        """A "markup error" label when SUMMARY or BODY is invalid markup (shown as typed), else None."""
        errors = [f'{part}: {error}' for part, text in (('summary', summary), ('body', body))
                  if (error := markup_error(text, mode, classes))]
        if not errors:
            return None
        flag = self.label('markup error', 'markup-error', wrap=False)
        flag.set_ellipsize(Pango.EllipsizeMode.NONE)
        flag.set_tooltip_text('\n'.join(errors) + '\nshown as typed')
        return flag

    def image_from_data(self, n, hint, size):
        """The pixbuf of N's image-data HINT, or None (with a warning) when it is malformed."""
        try:
            return pixbuf_from_data(n.hints[hint], size)
        except (GLib.Error, TypeError, ValueError) as exc:
            self.log.warn(f'#{n.id} {hint} not drawn: {getattr(exc, "message", None) or exc}')
            return None

    def on_link(self, _label, uri):
        """A link in a label was clicked: open http(s) and mailto, refuse the rest; never dismiss."""
        self.link_clicked = Gtk.get_current_event_time()
        if not safe_link(uri):
            self.log.warn(f'link not opened (only http, https and mailto): {uri}')
            return True
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
            self.log.info(f'opened {uri}')
        except GLib.Error as exc:
            self.log.warn(f'link {uri}: {exc.message}')
        return True

    def label(self, markup, css_class, wrap=True):
        label = Gtk.Label()
        label.set_markup(markup)
        if '<a ' in markup:
            label.connect('activate-link', self.on_link)
        label.set_xalign(0)
        if wrap:
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_max_width_chars(1)    # take the width the window gives, wrap within it
            label.set_hexpand(True)
        else:
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_single_line_mode(True)
        label.get_style_context().add_class(css_class)
        return label

    def load_image(self, source, size):
        """A pixbuf from a path, file:// URI or icon name (no network), or None."""
        return load_pixbuf(source, size)

    # -- placement ------------------------------------------------------
    def monitor_geometry(self):
        display = Gdk.Display.get_default()
        position = self.manager.config.settings['position']
        monitor = None
        if position.get('follow') == 'mouse':
            seat = display.get_default_seat()
            if seat and seat.get_pointer():
                _screen, x, y = seat.get_pointer().get_position()
                monitor = display.get_monitor_at_point(x, y)
        if monitor is None:
            monitor = display.get_monitor(int(position.get('monitor') or 0)) or display.get_primary_monitor()
        if monitor is None:
            monitor = display.get_monitor(0)
        return monitor.get_workarea()

    def fullscreen(self):
        """Whether the focused window (_NET_ACTIVE_WINDOW) covers the whole monitor the stack is on: a
        fullscreen video or game. Under i3 a tiled or floating window never covers the bar's strip."""
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            active = self.screen.get_active_window()
        if active is None:
            return False
        display = Gdk.Display.get_default()
        monitor = display.get_monitor_at_window(active)
        if monitor is None:
            return False
        geometry, frame = monitor.get_geometry(), active.get_frame_extents()
        if not (frame.x <= geometry.x and frame.y <= geometry.y
                and frame.x + frame.width >= geometry.x + geometry.width
                and frame.y + frame.height >= geometry.y + geometry.height):
            return False
        area = self.area or self.monitor_geometry()
        return display.get_monitor_at_point(area.x + area.width // 2, area.y + area.height // 2) == monitor

    def layout(self):
        if not self.windows:
            return
        position = self.manager.config.settings['position']
        anchor = position.get('anchor', 'top-right')
        margin = position.get('margin', [30, 5])
        margin_v, margin_h = (margin, margin) if isinstance(margin, (int, float)) else (margin[0], margin[-1])
        gap = int(position.get('gap', 10))
        # one monitor for the whole stack: the one under the pointer when the first opened
        if self.area is None:
            self.area = self.monitor_geometry()
        area = self.area
        offset = margin_v
        for nid in self.manager.displayed:
            window = self.windows.get(nid)
            if window is None:
                continue
            _minimum, natural = window.get_preferred_size()
            width, height = max(window.get_size()[0], natural.width), natural.height
            if anchor.endswith('left'):
                x = area.x + margin_h
            elif anchor.endswith('center'):
                x = area.x + (area.width - width) // 2
            else:
                x = area.x + area.width - margin_h - width
            if anchor.startswith('bottom'):
                y = area.y + area.height - offset - height
            else:
                y = area.y + offset
            window.move(int(x), int(y))
            offset += height + gap

    # -- input ----------------------------------------------------------
    def on_enter(self, _widget, event, nid):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.manager.hold(nid)

    def on_leave(self, _widget, event, nid):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.manager.release(nid)

    def on_click(self, _widget, event, nid):
        if self.link_clicked == event.time:     # the label opened a link with this click
            return True
        button = BUTTONS.get(event.button)
        if button:
            self.manager.click(nid, button)
        return True
