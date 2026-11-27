"""The desktop's icon theme: which one is current, what it has, and why an icon or image did not load.

Used by the renderer (a warning for a missing icon or picture), `notifyd check` (a rule's icon.name and
image.path) and `notifyd icons` (browse the theme). Works without a display: the theme then comes from
GTK's settings.ini or GNOME's gsettings, as GTK itself would pick it.
"""

import configparser
import difflib
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

FALLBACK_THEME = 'hicolor'


def configured_theme():
    """The icon theme name the desktop asks for, without a display: GTK 3's settings.ini, else
    gsettings' org.gnome.desktop.interface icon-theme, else hicolor."""
    config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'gtk-3.0' / 'settings.ini'
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(config)
        name = parser.get('Settings', 'gtk-icon-theme-name', fallback='').strip()
        if name:
            return name
    except configparser.Error:
        pass
    source = Gio.SettingsSchemaSource.get_default()
    if source is not None and source.lookup('org.gnome.desktop.interface', True) is not None:
        name = Gio.Settings.new('org.gnome.desktop.interface').get_string('icon-theme')
        if name:
            return name
    return FALLBACK_THEME


def current_theme_name():
    """The theme GTK uses here: the display's GtkSettings when there is one, else configured_theme()."""
    settings = Gtk.Settings.get_default()
    name = settings.props.gtk_icon_theme_name if settings is not None else None
    return name or configured_theme()


def icon_theme(name=None):
    """(Gtk.IconTheme, its name): NAME, or the current one (the display's own theme object when there is
    a display, so it sees what the renderer sees)."""
    if name is None and Gtk.Settings.get_default() is not None:
        return Gtk.IconTheme.get_default(), current_theme_name()
    name = name or current_theme_name()
    theme = Gtk.IconTheme.new()
    theme.set_custom_theme(name)
    return theme, name


def theme_installed(name):
    """Whether an icon theme NAME (a directory with an index.theme) is in the icon search path."""
    dirs = [Path(GLib.get_user_data_dir()) / 'icons', Path.home() / '.icons']
    dirs += [Path(d) / 'icons' for d in GLib.get_system_data_dirs()]
    return any((d / name / 'index.theme').is_file() for d in dirs)


def list_icons(theme, context=None):
    """[(name, context)] of THEME's icons (sorted by name), in CONTEXT only when given. An icon in
    several contexts is listed once, with the first."""
    contexts = [context] if context else sorted(theme.list_contexts())
    found = {}
    for ctx in contexts:
        for name in theme.list_icons(ctx):
            found.setdefault(name, ctx)
    return sorted(found.items())


def close_names(theme, name, count=3):
    """Up to COUNT names in THEME that look like NAME (a typo, a vendor prefix, -symbolic)."""
    names = theme.list_icons(None)
    lowered = name.lower()
    contains = sorted((n for n in names if lowered in n.lower() or n.lower() in lowered), key=len)
    picks = list(dict.fromkeys(contains[:count] + difflib.get_close_matches(name, names, n=count, cutoff=0.7)))
    return picks[:count]


def looks_like_path(source):
    return source.startswith(('/', '~', 'file://')) or '/' in source


def local_path(source):
    """SOURCE as a local path (expanding ~ and file:// URIs), or None for an icon name or another URI."""
    if source.startswith('file://'):
        return unquote(urlparse(source).path)
    if '://' in source:
        return None
    if looks_like_path(source):
        return os.path.expanduser(source)
    return None


def why_missing(source, theme=None, theme_name=None):
    """Why SOURCE (a path, file:// URI or icon name) gives no picture, as a short reason, or None when it
    loads. THEME (a Gtk.IconTheme, named THEME_NAME) defaults to the current one."""
    if not source:
        return None
    if '://' in source and not source.startswith('file://'):
        return 'not a local file (only paths, file:// URIs and icon names are drawn)'
    path = local_path(source)
    if path is not None:
        if not os.path.isabs(path):
            return 'a relative path (the daemon cannot know what it is relative to)'
        if not os.path.exists(path):
            return 'no such file'
        if not os.path.isfile(path):
            return 'not a file'
        try:
            image_format = GdkPixbuf.Pixbuf.get_file_info(path)[0]
        except GLib.Error as exc:
            return f'not readable ({exc.message})'
        return None if image_format is not None else 'not an image GdkPixbuf can read'
    if theme is None:
        theme, theme_name = icon_theme()
    if theme.has_icon(source) or theme.lookup_icon(source, 32, Gtk.IconLookupFlags.GENERIC_FALLBACK):
        return None
    reason = f'not in icon theme {theme_name or "?"!r}'
    close = close_names(theme, source)
    if close:
        reason += f' (close: {", ".join(close)})'
    return reason
