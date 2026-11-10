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
    """{'name', 'status', 'title', 'artist', 'album', 'art'} for player NAME, or None."""
    try:
        props = connection.call_sync(name, PATH, 'org.freedesktop.DBus.Properties', 'GetAll',
                                     GLib.Variant('(s)', (PLAYER,)), GLib.VariantType('(a{sv})'),
                                     Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
    except GLib.Error:
        return None
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


def now_playing(connection):
    """The playing player (else a paused one) with a title, or None."""
    states = [s for s in (player_state(connection, n) for n in players(connection)) if s and s['title']]
    states = [s for s in states if s['status'] in ('Playing', 'Paused')]
    if not states:
        return None
    return min(states, key=lambda s: STATUS_ORDER.get(s['status'], 3))


def command(connection, name, method):
    """Previous, PlayPause or Next, without waiting for the reply."""
    connection.call(name, PATH, PLAYER, method, None, None, Gio.DBusCallFlags.NONE, 1000, None, None, None)
