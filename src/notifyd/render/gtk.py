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
gi.require_version('Atk', '1.0')
from gi.repository import Atk, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from notifyd.config import ConfigError, load_preset  # noqa: E402
from notifyd.icons import current_theme_name, why_missing  # noqa: E402
from notifyd.images import (  # noqa: E402, F401
    MAX_IMAGE_SIDE,
    desktop_icon,
    image_data_problem,
    pixbuf_from_image_data,
)
from notifyd.markup import action_link, cap_lines, markup_error, plain_text, prepare, safe_link, split_action_links  # noqa: E402
from notifyd.model import TITLE_END_HINT  # noqa: E402
from notifyd.render.center import Center  # noqa: E402
from notifyd.render.css import build_css, resolve_style, style_class  # noqa: E402

BUTTONS = {1: 'left', 2: 'middle', 3: 'right'}
TIMEOUT_BAR_TICK = 100   # ms between timeout-bar updates
TIMEOUT_BAR_DEFAULTS = {'color': '#00d7d7', 'background': 'transparent', 'height': 2}   # thin and cyan
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


def rgba(color):
    """A Gdk.RGBA from a CSS color string, or None."""
    value = Gdk.RGBA()
    return value if isinstance(color, str) and value.parse(color) else None


def load_pixbuf(source, size, color=None):
    """A pixbuf at most SIZE px from a file path/URI or an icon name; None when neither works. A
    `*-symbolic` icon is drawn in COLOR (a Gdk.RGBA: the card's text color), so a dark glyph does
    not vanish on a dark card; a name the theme lacks falls back to a shorter one
    (audio-volume-high-foo -> audio-volume-high)."""
    if not source or size <= 0:
        return None
    path = file_path(source)
    try:
        if path:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
        info = Gtk.IconTheme.get_default().lookup_icon(
            source, size, Gtk.IconLookupFlags.FORCE_SIZE | Gtk.IconLookupFlags.GENERIC_FALLBACK)
        if info is None:
            return None
        if color is not None and info.is_symbolic():
            return scale_to(info.load_symbolic(color, None, None, None)[0], size)
        return scale_to(info.load_icon(), size)
    except GLib.Error:
        return None


class TimeoutBar(Gtk.DrawingArea):
    """A bar of exactly HEIGHT px, FRACTION of it in COLOR over BACKGROUND, drawn with cairo: a
    GtkProgressBar keeps the theme's minimum height (6 px here) whatever the CSS says."""

    def __init__(self, color, background, height):
        super().__init__()
        self.fraction = 1.0
        self.color = rgba(color) or rgba(TIMEOUT_BAR_DEFAULTS['color'])
        self.background = rgba(background)
        try:
            height = max(int(str(height).removesuffix('px')), 1)
        except ValueError:
            height = TIMEOUT_BAR_DEFAULTS['height']
        self.set_size_request(-1, height)
        self.set_hexpand(True)
        self.get_style_context().add_class('timeout-bar')
        self.connect('draw', self.on_draw)

    def set_fraction(self, fraction):
        fraction = min(max(fraction, 0.0), 1.0)
        if fraction != self.fraction:
            self.fraction = fraction
            self.queue_draw()

    def get_fraction(self):
        return self.fraction

    def on_draw(self, _widget, cr):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        if self.background is not None and self.background.alpha > 0:
            Gdk.cairo_set_source_rgba(cr, self.background)
            cr.rectangle(0, 0, width, height)
            cr.fill()
        Gdk.cairo_set_source_rgba(cr, self.color)
        cr.rectangle(0, 0, width * self.fraction, height)
        cr.fill()
        return False


def line_spacing(value):
    """Px between the lines of a label from a style's `line-spacing` (2, '2px'); 0 when unset or invalid."""
    try:
        return max(int(float(str(value or 0).removesuffix('px'))), 0)
    except ValueError:
        return 0


class SpacedLabel(Gtk.Label):
    """A label with SPACING px between its lines (wrapped or not), drawn as a gap: Pango's line_height
    attribute grows each span's background along with the line, so wrapped chips still touch. The spacing
    is the layout's, but GTK3 rebuilds a wrapped label's layout on each height request, so it is set again
    before drawing, and the requested height adds it once per line break."""

    def __init__(self, spacing):
        super().__init__()
        self.spacing = spacing

    def extra_height(self, width):
        """The spacing the lines of this label at WIDTH (px, with padding and border; -1: unwrapped) add."""
        layout = self.get_layout().copy()
        if width >= 0:
            context = self.get_style_context()
            state = context.get_state()
            pad, border = context.get_padding(state), context.get_border(state)
            inner = width - pad.left - pad.right - border.left - border.right
            layout.set_width(max(inner, 1) * Pango.SCALE)
        layout.set_spacing(0)
        return (layout.get_line_count() - 1) * self.spacing

    def do_get_preferred_height_and_baseline_for_width(self, width):
        size = Gtk.Label.do_get_preferred_height_and_baseline_for_width(self, width)
        extra = self.extra_height(width)
        return (size.minimum_height + extra, size.natural_height + extra,
                size.minimum_baseline, size.natural_baseline)

    def do_get_preferred_height(self):
        minimum, natural = Gtk.Label.do_get_preferred_height(self)
        extra = self.extra_height(-1)
        return minimum + extra, natural + extra

    def do_draw(self, cr):
        self.get_layout().set_spacing(self.spacing * Pango.SCALE)
        return Gtk.Label.do_draw(self, cr)


class GtkRenderer:
    def __init__(self, manager, log):
        ok, _argv = Gtk.init_check(sys.argv)
        if not ok:
            raise RuntimeError('no X display (is DISPLAY set?)')
        self.manager = manager
        self.log = log
        self.link_clicked = None   # the event time of the last link click, which must not dismiss
        self.action_labels = {}    # label -> (notification id, [(action key, start, end)]): its action links
        self.hand = None           # the pointer cursor shown over an action link
        self.missing_seen = set()  # icons and pictures that did not load, warned about once per run
        self.measured = None       # (notification, window) built by fits() for show()
        self.fades = {}            # window -> its running fade's GLib source
        self.bodies = {}           # notification id -> (body label, max-lines) when its body is capped
        self.timeout_bars = {}     # notification id -> (TimeoutBar, timeout ms, tick source)
        self.waiting_labels = {}   # notification id -> its header's +N label
        self.expanded = set()      # ids whose capped body is shown in full while hovered
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
        self.areas = {}      # stack -> the work area it is on; chosen when its first one opens
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
        self.missing_seen.clear()      # a reload is when a fix to the config or the theme shows: warn again
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
        active = self.manager.active
        shown = [w for nid, w in self.windows.items() if nid in self.manager.displayed and nid != n.id
                 and nid in active and active[nid].stack == n.stack]
        if not shown:
            return True
        window = self.new_window(n)
        self.measured = (n, window)
        position = n.settings.get('position') or self.manager.config.settings['position']
        margin = position.get('margin', [30, 5])
        margin_v = margin if isinstance(margin, (int, float)) else margin[0]
        gap = int(position.get('gap', 10))
        area = self.areas.get(n.stack) or self.monitor_geometry(position)
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
                self.waiting_labels.pop(n.id, None)
                self.bodies.pop(n.id, None)
                self.stop_timeout_bar(n.id)
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
        self.waiting_labels.pop(nid, None)
        self.stop_timeout_bar(nid)
        self.bodies.pop(nid, None)
        self.expanded.discard(nid)
        self.prune_styles()
        if window is not None:
            if fade_out and window.get_visible():
                self.fade(window, window.get_opacity(), 0.0, fade_out, then=window.destroy)
            else:
                self.stop_fade(window)         # a fade-in must not touch it once it is destroyed
                window.destroy()
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
        self.areas.clear()
        self.log.info('monitors changed; placing notifications again')
        self.layout()

    def fill(self, window, n):
        self.bodies.pop(n.id, None)
        self.expanded.discard(n.id)
        settings = n.settings
        scope, style = self.style_for(n)
        context = window.get_style_context()
        for name in [c for c in context.list_classes() if c.startswith('nd-')]:
            context.remove_class(name)
        context.add_class(scope)
        self.window_scopes[n.id] = scope
        self.announce(window, n)
        width = int(settings.get('width') or 350)
        window.set_size_request(width, -1)
        window.set_default_size(width, -1)

        events = Gtk.EventBox()
        events.set_visible_window(False)
        events.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
                          | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        events.connect('motion-notify-event', self.on_motion)
        events.connect('enter-notify-event', self.on_enter, n.id)
        events.connect('leave-notify-event', self.on_leave, n.id)
        events.connect('button-release-event', self.on_click, n.id)
        card, parts = self.build_card(n, style)
        events.add(card)
        waiting = self.label('', 'waiting', wrap=False)    # +N on the last popup while some wait
        waiting.set_no_show_all(True)
        parts['header'].pack_end(waiting, False, False, 0)
        if parts['header'].get_parent() is None:
            card.pack_start(parts['header'], False, False, 0)
            card.reorder_child(parts['header'], 0)
        self.waiting_labels[n.id] = waiting
        if parts['body'] is not None and parts['body'][1]:
            self.bodies[n.id] = parts['body']             # hovering shows the rest (expand)
        self.stop_timeout_bar(n.id)
        if settings.get('timeout-bar') and n.timeout > 0:
            look = {**TIMEOUT_BAR_DEFAULTS, **(style.get('timeout-bar') or {})}
            bar = TimeoutBar(look['color'], look['background'], look['height'])
            bar.set_margin_top(6)
            card.pack_end(bar, False, False, 0)
            self.timeout_bars[n.id] = (bar, n.timeout, GLib.timeout_add(TIMEOUT_BAR_TICK, self.tick_timeout_bar, n.id))
            self.tick_timeout_bar(n.id)
        window.add(events)

    def build_card(self, n, style, body_lines=None, header_end=None, actions=True):
        """N's card, the same in a popup and in the center: header (icon, app, count, time, markup
        flag, HEADER_END such as the center's close button), picture, title, body capped at
        BODY_LINES (default: its max-lines; 0 for all of it), progress and action buttons.
        Returns (card, {'header': box, 'body': (label, lines, full, capped) or None})."""
        settings = n.settings
        hide = set(settings.get('hide') or [])
        classes = style.get('classes') or {}
        mode = settings.get('markup', 'full')
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card_context = card.get_style_context()
        card_context.add_class('notification')
        card_context.add_class(n.urgency_name)
        if settings.get('class'):
            card_context.add_class(str(settings['class']))
        # image: image-data, image-path, a rule's image.path, else an app icon that is a file
        image_settings = settings.get('image') or {}
        scale = self.scale()
        image_size = int(image_settings.get('size') or 0) * scale      # device pixels, drawn at SCALE
        icon_settings = settings.get('icon') or {}
        icon_source = icon_settings.get('name') or n.app_icon or desktop_icon(n.desktop_entry)
        icon_what = ('icon (icon.name setting)' if icon_settings.get('name') else 'app icon' if n.app_icon
                     else f'icon of {n.desktop_entry}.desktop')
        picture = None
        if 'image' not in hide:
            if image_settings.get('path'):
                picture = self.picture(n, image_settings['path'], image_size, 'image (image.path setting)')
            for hint in IMAGE_HINTS:
                if picture is None and isinstance(n.hints.get(hint), tuple):
                    picture = self.image_from_data(n, hint, image_size)
            for hint in IMAGE_PATH_HINTS:
                if picture is None and n.hints.get(hint):
                    picture = self.picture(n, str(n.hints[hint]), image_size, f'image ({hint} hint)')
            if picture is None and file_path(icon_source):
                picture = load_pixbuf(icon_source, image_size, rgba(self.text_color(n, style)))
                icon_source = None
            if picture is None and isinstance(n.hints.get('icon_data'), tuple):
                picture = self.image_from_data(n, 'icon_data', image_size)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.get_style_context().add_class('header')
        if 'app-icon' not in hide:
            icon = self.picture(n, icon_source, int(icon_settings.get('size') or 0) * scale, icon_what,
                                rgba(self.text_color(n, style))) if icon_source else None
            if icon is not None:
                widget = self.picture_widget(icon, scale)
                widget.get_style_context().add_class('appicon')
                header.pack_start(widget, False, False, 0)
        if 'app-name' not in hide and n.app:
            header.pack_start(self.label(GLib.markup_escape_text(n.app), 'appname', wrap=False), True, True, 0)
        if n.duplicates > 1:
            header.pack_start(self.label(f'×{n.duplicates}', 'count', wrap=False), False, False, 0)
        if 'time' not in hide:
            header.pack_end(self.label(time.strftime('%H:%M', time.localtime(n.received)), 'time', wrap=False),
                            False, False, 0)
        title_end = (n.title_end or None) if 'title' not in hide else None
        flag = self.markup_flag(n.summary, n.body, mode, classes, title_end)
        if flag is not None:
            header.pack_end(flag, False, False, 0)
        if header_end is not None:
            header.pack_end(header_end, False, False, 0)
        if header.get_children():
            card.pack_start(header, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        content.get_style_context().add_class('content')
        if picture is not None:
            image = self.picture_widget(picture, scale)
            image.get_style_context().add_class('image')
            image.set_valign(Gtk.Align.START)
            image.set_margin_end(int(image_settings.get('margin-right', 0)) + 12)
            content.pack_start(image, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        link_nid = n.id if actions else None     # a closed card in the center has no actions to invoke
        if 'title' not in hide and n.summary:
            title = self.label(prepare(n.summary, mode, classes), 'title', nid=link_nid)
            title_lines = int((settings.get('title') or {}).get('max-lines') or 0)
            if title_lines:
                title.set_lines(title_lines)
                title.set_ellipsize(Pango.EllipsizeMode.END)
            if title_end is None:
                text.pack_start(title, False, False, 0)
            else:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.get_style_context().add_class('title-row')
                end = self.label(prepare(title_end, mode, classes), 'title-end', wrap=False, nid=link_nid)
                end.set_ellipsize(Pango.EllipsizeMode.NONE)
                end.set_xalign(1)
                for part in (title, end):
                    part.set_valign(Gtk.Align.BASELINE)
                row.pack_start(title, True, True, 0)
                row.pack_end(end, False, False, 0)
                text.pack_start(row, False, False, 0)
        body_part = None
        if 'body' not in hide and n.body:
            full = prepare(n.body, mode, classes)
            lines = int((settings.get('body') or {}).get('max-lines') or 0) if body_lines is None else body_lines
            capped = cap_lines(full, lines)
            spacing = line_spacing((style.get('body') or {}).get('line-spacing'))
            body = self.label(capped or full, 'body', nid=link_nid, spacing=spacing)
            if lines:
                body.set_lines(lines)             # a long paragraph: Pango caps it (per paragraph)
                body.set_ellipsize(Pango.EllipsizeMode.END)
            body_part = (body, lines, full, capped)
            text.pack_start(body, False, False, 0)
        if 'progress' not in hide and n.progress is not None:
            bar = Gtk.ProgressBar()
            bar.set_fraction(n.progress / 100)
            bar.set_margin_top(4)
            text.pack_start(bar, False, False, 0)
        content.pack_start(text, True, True, 0)
        card.pack_start(content, False, False, 0)

        buttons = [(key, label) for key, label in n.actions if key != 'default'] if actions else []
        if buttons and 'actions' not in hide:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, homogeneous=True)
            row.set_margin_top(8)
            theme = Gtk.IconTheme.get_default()
            for key, label in buttons:
                if n.action_icons and theme is not None and theme.has_icon(key):
                    # spec: with action-icons the key names an icon; the label is its tooltip (and name)
                    button = Gtk.Button.new_from_icon_name(key, Gtk.IconSize.BUTTON)
                    button.set_tooltip_text(label)
                    button.get_style_context().add_class('action-icon')
                else:
                    button = Gtk.Button(label=label)
                button.connect('clicked', lambda _b, k=key, nid=n.id: self.manager.invoke(nid, k))
                row.pack_start(button, True, True, 0)
            card.pack_start(row, False, False, 0)
        return card, {'header': header, 'body': body_part}


    def picture(self, n, source, size, what, color=None):
        """load_pixbuf(SOURCE), with a warning (once per source per run) when SOURCE was given and does not
        load. WHAT says where it came from: 'image (image-path hint)', 'app icon', ..."""
        pixbuf = load_pixbuf(source, size, color)
        if pixbuf is None and source and size > 0 and source not in self.missing_seen:
            self.missing_seen.add(source)
            reason = why_missing(source, Gtk.IconTheme.get_default(), current_theme_name()) or 'could not be drawn'
            if 'icon theme' in reason:
                reason += '; `notifyd icons PATTERN` lists the theme\'s icons'
            self.log.warn(f'#{n.id} {what} {source!r}: {reason}')
        return pixbuf

    @staticmethod
    def announce(window, n):
        """Give N's popup WINDOW an accessible role, name and description, so a screen reader (Orca)
        announces it: a notification (an alert when critical), its summary and its body as text."""
        def text(value):
            return value if n.settings.get('markup', 'full') == 'plain' else plain_text(value or '')
        accessible = window.get_accessible()
        accessible.set_role(Atk.Role.ALERT if n.urgency_name == 'critical' else Atk.Role.NOTIFICATION)
        accessible.set_name(text(n.summary) or n.app or 'notification')
        accessible.set_description(text(n.body))

    def markup_flag(self, summary, body, mode, classes, title_end=None):
        """A "markup error" label when SUMMARY, BODY or TITLE_END (the title-end hint) is invalid markup
        (shown as typed), else None."""
        parts = (('summary', summary), ('body', body), (TITLE_END_HINT, title_end))
        errors = [f'{part}: {error}' for part, text in parts
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

    def on_link(self, _label, uri, nid=None):
        """A link in a label was clicked: `action:KEY` invokes the notification's own action KEY (on
        a card that can still act: NID is set), http(s) and mailto open, the rest is refused."""
        self.link_clicked = Gtk.get_current_event_time()
        key = action_link(uri)
        if key is not None:
            if nid is None or not self.manager.invoke(nid, key):
                self.log.warn(f'link {uri}: no such action on this notification')
            return True
        if not safe_link(uri):
            self.log.warn(f'link not opened (only http, https and mailto): {uri}')
            return True
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
            self.log.info(f'opened {uri}')
        except GLib.Error as exc:
            self.log.warn(f'link {uri}: {exc.message}')
        return True

    def label(self, markup, css_class, wrap=True, nid=None, spacing=0):
        """A label of MARKUP; NID is the notification whose actions its `action:` links invoke. Those links
        are not GtkLabel links (drawn in the theme's link color and underlined, over the sender's styling):
        their text keeps the sender's look, and action_link_hit() finds a click on it. SPACING is px
        between its lines (SpacedLabel)."""
        label = SpacedLabel(spacing) if spacing else Gtk.Label()
        markup, actions = split_action_links(markup)
        label.set_markup(markup)
        if '<a ' in markup:
            label.connect('activate-link', self.on_link, nid)
        if actions and nid is not None:
            self.action_labels[label] = (nid, actions)
            label.connect('destroy', lambda widget: self.action_labels.pop(widget, None))
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

    def load_image(self, source, size, color=None):
        """A pixbuf from a path, file:// URI or icon name (no network), or None; COLOR is a CSS color
        for symbolic icons."""
        return load_pixbuf(source, size, rgba(color))

    def scale(self):
        """The scale factor of the monitor the stack is on (2 under GDK_SCALE=2 or a HiDPI screen)."""
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        return max(int(monitor.get_scale_factor()), 1) if monitor is not None else 1

    @staticmethod
    def picture_widget(pixbuf, scale=1):
        """An image of PIXBUF, loaded at SCALE times its logical size, drawn at that scale so it stays
        sharp on a HiDPI screen (a plain pixbuf image would be drawn at its pixel size)."""
        if scale <= 1:
            return Gtk.Image.new_from_pixbuf(pixbuf)
        return Gtk.Image.new_from_surface(Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, None))

    @staticmethod
    def text_color(n, style):
        """The card's text color for N: its urgency's foreground, else the style's."""
        return (style.get(n.urgency_name) or {}).get('foreground') or style.get('foreground')

    # -- placement ------------------------------------------------------
    def monitor_geometry(self, position=None):
        """The work area of the monitor POSITION (default: the config's) puts a stack on."""
        display = Gdk.Display.get_default()
        position = position or self.manager.config.settings['position']
        monitor = None
        follow = position.get('follow')
        if follow == 'mouse':
            seat = display.get_default_seat()
            if seat and seat.get_pointer():
                _screen, x, y = seat.get_pointer().get_position()
                monitor = display.get_monitor_at_point(x, y)
        elif follow == 'focus':
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', DeprecationWarning)
                active = self.screen.get_active_window()
            monitor = display.get_monitor_at_window(active) if active is not None else None
        if monitor is None:
            monitor = self.configured_monitor(display, position.get('monitor'))
        if monitor is None:
            monitor = display.get_primary_monitor() or display.get_monitor(0)
        return monitor.get_workarea()

    def configured_monitor(self, display, value):
        """position.monitor: an index, or an output name (DP-1, as xrandr lists it; any case). None when
        there is no such monitor, with a warning (once per name) for a name."""
        if isinstance(value, str) and not value.lstrip('-').isdigit():
            monitors = [display.get_monitor(i) for i in range(display.get_n_monitors())]
            found = next((m for m in monitors if (m.get_model() or '').lower() == value.lower()), None)
            if found is None and value not in self.missing_seen:
                self.missing_seen.add(value)
                names = ', '.join(m.get_model() or '?' for m in monitors)
                self.log.warn(f'position.monitor {value!r}: no such monitor (there are: {names}); '
                              'using the primary one')
            return found
        return display.get_monitor(int(value or 0))

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
        area = self.areas.get(self.main_stack()) or self.monitor_geometry()
        return display.get_monitor_at_point(area.x + area.width // 2, area.y + area.height // 2) == monitor

    def main_stack(self):
        """The stack of a notification no rule moves: the config's own position."""
        from notifyd.model import Notification
        return Notification(settings={'position': self.manager.config.settings['position']}).stack

    def stacks(self):
        """{stack: [ids on screen, newest first]} for the windows there are."""
        out = {}
        for nid in self.manager.displayed:
            n = self.manager.active.get(nid)
            if n is not None and nid in self.windows:
                out.setdefault(n.stack, []).append(nid)
        return out

    def layout(self):
        stacks = self.stacks()
        for stack in [s for s in self.areas if s not in stacks]:
            del self.areas[stack]                # an emptied stack picks its monitor again next time
        for stack, ids in stacks.items():
            self.layout_stack(stack, ids)
        self.waiting_changed()

    def layout_stack(self, stack, ids):
        """Place one stack's windows (IDS, newest first) from its anchor, on the monitor it opened on."""
        position = self.manager.active[ids[0]].settings.get('position') or self.manager.config.settings['position']
        anchor = position.get('anchor', 'top-right')
        margin = position.get('margin', [30, 5])
        margin_v, margin_h = (margin, margin) if isinstance(margin, (int, float)) else (margin[0], margin[-1])
        gap = int(position.get('gap', 10))
        if stack not in self.areas:
            self.areas[stack] = self.monitor_geometry(position)
        area = self.areas[stack]
        offset = margin_v
        for nid in ids:
            window = self.windows[nid]
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
            self.expand(nid, True)

    def on_leave(self, _widget, event, nid):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.manager.release(nid)
            self.expand(nid, False)

    def waiting_changed(self):
        """Show +N on the last popup of the stack while N wait for room (indicate-waiting)."""
        active = self.manager.active
        waiting = {}
        for nid in self.manager.waiting:
            if nid in active:
                waiting[active[nid].stack] = waiting.get(active[nid].stack, 0) + 1
        counts = {}                               # the last popup of each stack -> how many wait for it
        if self.manager.config.settings.get('indicate-waiting', True):
            for stack, ids in self.stacks().items():
                if waiting.get(stack):
                    counts[ids[-1]] = waiting[stack]
        for nid, label in self.waiting_labels.items():
            if nid in counts:
                label.set_text(f'+{counts[nid]}')
                label.set_tooltip_text(f'{counts[nid]} more waiting for room')
                label.show()
            else:
                label.hide()

    def tick_timeout_bar(self, nid):
        found = self.timeout_bars.get(nid)
        if found is None:
            return False
        bar, timeout, _source = found
        remaining = self.manager.remaining(nid)
        bar.set_fraction((remaining or 0) / timeout if remaining is not None else 1.0)
        return True

    def stop_timeout_bar(self, nid):
        found = self.timeout_bars.pop(nid, None)
        if found is not None:
            GLib.source_remove(found[2])

    def expand(self, nid, on):
        """Show all of NID's body while it is hovered, when max-lines cut it; back to max-lines after."""
        found = self.bodies.get(nid)
        window = self.windows.get(nid)
        if found is None or window is None:
            return
        label, lines, full, capped = found
        if on and nid not in self.expanded and (capped is not None or label.get_layout().is_ellipsized()):
            self.expanded.add(nid)
            if capped is not None:
                label.set_markup(full)
            label.set_lines(-1)
            label.set_ellipsize(Pango.EllipsizeMode.NONE)
        elif not on and nid in self.expanded:
            self.expanded.discard(nid)
            if capped is not None:
                label.set_markup(capped)
            label.set_lines(lines)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            window.resize(1, 1)                    # shrink back to the capped body
        else:
            return
        self.layout()

    def action_link_at(self, widget, event):
        """(nid, key) of the action link under EVENT's pointer in WIDGET's window, or None."""
        toplevel = widget.get_toplevel()
        for label, (nid, ranges) in list(self.action_labels.items()):
            if not label.get_mapped() or label.get_toplevel() is not toplevel:
                continue
            window = label.get_window()
            _ok, origin_x, origin_y = window.get_origin()
            offset_x, offset_y = label.get_layout_offsets()      # in the label's window, as GTK 3 gives them
            inside, index, _trailing = label.get_layout().xy_to_index(
                int((event.x_root - origin_x - offset_x) * Pango.SCALE),
                int((event.y_root - origin_y - offset_y) * Pango.SCALE))
            if inside:
                for key, start, end in ranges:
                    if start <= index < end:
                        return nid, key
        return None

    def action_link_hit(self, widget, event):
        """A click on an action link invokes that action, as its button would; True when it did, so the
        click does not also dismiss or pop the card."""
        if event.button != 1:
            return False
        found = self.action_link_at(widget, event)
        if found is None:
            return False
        self.link_clicked = event.time
        nid, key = found
        if not self.manager.invoke(nid, key):
            self.log.warn(f'link action:{key}: no such action on this notification')
        return True

    def on_motion(self, widget, event):
        """The pointer turns into a hand over an action link, as over a link."""
        window = widget.get_toplevel().get_window()
        if window is None:
            return False
        if self.action_link_at(widget, event) is not None:
            if self.hand is None:
                self.hand = Gdk.Cursor.new_from_name(window.get_display(), 'pointer')
            window.set_cursor(self.hand)
        else:
            window.set_cursor(None)
        return False

    def on_click(self, widget, event, nid):
        if self.link_clicked == event.time:     # the label opened a link with this click
            return True
        if self.action_link_hit(widget, event):
            return True
        button = BUTTONS.get(event.button)
        if button:
            self.manager.click(nid, button)
        return True
