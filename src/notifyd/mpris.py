"""MPRIS media players on the session bus: what plays, and previous/play-pause/next.

For the notification center's now-playing row (swaync has one; deadd does not).
"""

from gi.repository import Gio, GLib

PREFIX = 'org.mpris.MediaPlayer2.'
PATH = '/org/mpris/MediaPlayer2'
PLAYER = 'org.mpris.MediaPlayer2.Player'
STATUS_ORDER = {'Playing': 0, 'Paused': 1, 'Stopped': 2}


def players(connection):
    try:
        names = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                                     'ListNames', None, GLib.VariantType('(as)'), Gio.DBusCallFlags.NONE, 1000,
                                     None).unpack()[0]
    except GLib.Error:
        return []
    return sorted(n for n in names if n.startswith(PREFIX))


def player_state(connection, name):
    """{'name', 'status', 'title', 'artist', 'album', 'art'} for player NAME, or None (blocks: for the CLI
    and tests; the center uses Players)."""
    try:
        props = connection.call_sync(name, PATH, 'org.freedesktop.DBus.Properties', 'GetAll',
                                     GLib.Variant('(s)', (PLAYER,)), GLib.VariantType('(a{sv})'),
                                     Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
    except GLib.Error:
        return None
    return _state(name, props)


def now_playing(connection):
    """The playing player (else a paused one) with a title, or None."""
    return pick(player_state(connection, n) for n in players(connection))


def command(connection, name, method):
    """Previous, PlayPause or Next, without waiting for the reply."""
    connection.call(name, PATH, PLAYER, method, None, None, Gio.DBusCallFlags.NONE, 1000, None, None, None)


def _state(name, props):
    meta = props.get('Metadata') or {}
    artist = meta.get('xesam:artist') or []
    return {
        'name': name,
        'player': name[len(PREFIX):].split('.')[0],
        'status': props.get('PlaybackStatus', 'Stopped'),
        'title': meta.get('xesam:title') or '',
        'artist': ', '.join(artist) if isinstance(artist, list) else str(artist),
        'album': meta.get('xesam:album') or '',
        'art': meta.get('mpris:artUrl') or '',
    }


def pick(states, ignore=()):
    """The playing player (else a paused one) with a title, or None; players named in IGNORE (the part
    after org.mpris.MediaPlayer2., such as firefox or chromium; any case) are passed over."""
    ignored = {str(name).lower() for name in ignore}
    states = [s for s in states if s and s['title'] and s['status'] in ('Playing', 'Paused')
              and s['player'].lower() not in ignored]
    if not states:
        return None
    return min(states, key=lambda s: STATUS_ORDER.get(s['status'], 3))


class Players:
    """What every MPRIS player plays, kept from the bus without blocking the main loop: players are
    found and read with async calls, then followed through their PropertiesChanged and
    NameOwnerChanged signals. ON_CHANGE() runs after each change."""

    def __init__(self, connection, on_change=lambda: None):
        self.connection = connection
        self.on_change = on_change
        self.props = {}      # well-known name -> {'PlaybackStatus': ..., 'Metadata': {...}}
        self.owners = {}     # unique name -> well-known name
        connection.signal_subscribe(None, 'org.freedesktop.DBus.Properties', 'PropertiesChanged', PATH, PLAYER,
                                    Gio.DBusSignalFlags.NONE, self._on_properties)
        connection.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
                                    '/org/freedesktop/DBus', None, Gio.DBusSignalFlags.NONE, self._on_owner)
        connection.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'ListNames', None,
                        GLib.VariantType('(as)'), Gio.DBusCallFlags.NONE, 2000, None, self._on_names)

    def now_playing(self, ignore=()):
        return pick((_state(name, props) for name, props in sorted(self.props.items())), ignore)

    def _on_names(self, connection, result):
        try:
            names = connection.call_finish(result).unpack()[0]
        except GLib.Error:
            return
        for name in names:
            if name.startswith(PREFIX):
                self._add(name)

    def _add(self, name):
        self.connection.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                             'GetNameOwner', GLib.Variant('(s)', (name,)), GLib.VariantType('(s)'),
                             Gio.DBusCallFlags.NONE, 2000, None, self._on_owner_of, name)

    def _on_owner_of(self, connection, result, name):
        try:
            owner = connection.call_finish(result).unpack()[0]
        except GLib.Error:
            return
        self.owners[owner] = name
        connection.call(name, PATH, 'org.freedesktop.DBus.Properties', 'GetAll', GLib.Variant('(s)', (PLAYER,)),
                        GLib.VariantType('(a{sv})'), Gio.DBusCallFlags.NONE, 2000, None, self._on_all, name)

    def _on_all(self, connection, result, name):
        try:
            props = connection.call_finish(result).unpack()[0]
        except GLib.Error:
            return
        if name in self.owners.values():
            self.props[name] = props
            self.on_change()

    def _on_properties(self, _conn, sender, _path, _iface, _member, params):
        _iface_name, changed, _invalidated = params.unpack()
        name = self.owners.get(sender)
        if name is None:
            return                     # not known yet; its GetAll brings the state
        self.props.setdefault(name, {}).update(changed)
        self.on_change()

    def _on_owner(self, _conn, _sender, _path, _iface, _member, params):
        name, old, new = params.unpack()
        if not name.startswith(PREFIX):
            return
        if old:
            self.owners.pop(old, None)
            if self.props.pop(name, None) is not None:
                self.on_change()
        if new:
            self._add(name)
