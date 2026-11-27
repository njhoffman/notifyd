"""`notifyd-send` (also `notifyd send`): notify-send and notify-send.py in one, for any notification daemon.

notify-send's flags work as they do there (-a -u -t -i -c -h -A -r -p -e -w -v), notify-send.py's
too (--replaces-id, --replaces-process, several values after one --hint or --action, \\n and \\t in
the text, --dontQuitOnAction, --close). On top, flags for hints that otherwise need -h TYPE:NAME:VALUE:
  --replace-name NAME   replace the last notification sent with NAME (ids kept in $XDG_RUNTIME_DIR)
  --tag TAG             x-dunst-stack-tag: the daemon replaces the last one with this app and tag
  --image PATH|ICON     image-path hint (a file:// URI, which deadd needs, or an icon name)
  --progress N          value hint: a progress bar at N percent
  --title-end MARKUP    x-notifyd-title-end: drawn at the right of the title (notifyd only)
  --desktop-entry, --sound-name, --sound-file, --suppress-sound, --resident, --fg, --bg, --frame, --highlight
With actions it waits and prints the key of the action chosen, then exits (as notify-send does).
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from gi.repository import Gio, GLib

from notifyd import __version__

BUS_NAME = 'org.freedesktop.Notifications'
OBJECT_PATH = '/org/freedesktop/Notifications'
IFACE = 'org.freedesktop.Notifications'
URGENCIES = {'low': 0, 'normal': 1, 'critical': 2}
HINT_TYPES = {'int': 'i', 'double': 'd', 'string': 's', 'byte': 'y', 'boolean': 'b', 'bool': 'b', 'variant': 'v'}
# flag dest -> (hint, how): 's' a string, 'path' an absolute path, 'b' true when given
FLAG_HINTS = {
    'title_end': ('x-notifyd-title-end', 's'),
    'desktop_entry': ('desktop-entry', 's'),
    'sound_name': ('sound-name', 's'),
    'sound_file': ('sound-file', 'path'),
    'suppress_sound': ('suppress-sound', 'b'),
    'resident': ('resident', 'b'),
    'action_icons': ('action-icons', 'b'),
    'fg': ('fgcolor', 's'),
    'bg': ('bgcolor', 's'),
    'frame': ('frcolor', 's'),
    'highlight': ('hlcolor', 's'),
}
# notify-send.py's `--hint A B C` and `--action K:A L:B`: the values after the first that look like one
MULTI = {
    '--hint': re.compile(r'(?is)(?:' + '|'.join(HINT_TYPES) + r'):[^:]+:.*'),
    '--action': re.compile(r'(?s)[^\s:=-][^\s:=]*:\S.*'),
}
ESCAPES = re.compile(r'\\([nt\\])')


class SendError(Exception):
    pass


def parse_hint(text):
    """'TYPE:NAME:VALUE' -> (name, GLib.Variant); a variant VALUE is GVariant text (`<int32 5>`, `[1, 2]`)."""
    parts = text.split(':', 2)
    if len(parts) != 3:
        raise SendError(f'hint {text!r}: expected TYPE:NAME:VALUE')
    kind, name, value = parts
    signature = HINT_TYPES.get(kind.lower())
    if not signature:
        raise SendError(f'hint {text!r}: type must be one of {", ".join(sorted(set(HINT_TYPES)))}')
    if signature == 'v':
        try:
            return name, GLib.Variant.parse(None, value, None, None)
        except GLib.Error as exc:
            raise SendError(f'hint {text!r}: {value!r} is not GVariant text ({exc.message})') from exc
    try:
        if signature in ('i', 'y'):
            parsed = int(value)
        elif signature == 'd':
            parsed = float(value)
        elif signature == 'b':
            if value.lower() not in ('true', 'false', '1', '0'):
                raise ValueError(value)
            parsed = value.lower() in ('true', '1')
        else:
            parsed = value
    except ValueError as exc:
        raise SendError(f'hint {text!r}: {value!r} is not a valid {kind}') from exc
    return name, GLib.Variant(signature, parsed)


def parse_actions(values):
    """notify-send's [NAME=]Text and notify-send.py's NAME:Text -> flat [key, label, ...]."""
    flat = []
    for index, value in enumerate(values or []):
        for separator in ('=', ':'):
            if separator in value:
                key, label = value.split(separator, 1)
                break
        else:
            key, label = str(index), value
        flat += [key, label]
    return flat


def expand_multi(argv):
    """notify-send.py takes several values after one --hint or --action; argparse takes one, so repeat the
    flag before each further value shaped like one. A summary like 'Build: done' is not one (space after
    the colon); put the summary before such a list, as notify-send.py needs anyway."""
    out, i = [], 0
    while i < len(argv):
        token = argv[i]
        out.append(token)
        i += 1
        if token == '--':
            out += argv[i:]
            break
        if token in MULTI and i < len(argv):
            out.append(argv[i])
            i += 1
            while i < len(argv) and MULTI[token].fullmatch(argv[i]):
                out += [token, argv[i]]
                i += 1
    return out


def unescape(text):
    """notify-send.py's \\n and \\t (and \\\\ for a backslash) in the summary and body."""
    return ESCAPES.sub(lambda m: {'n': '\n', 't': '\t', '\\': '\\'}[m[1]], text)


def image_uri(path):
    """A file:// URI for a picture, kept as given when it is a URI already or an icon name (no slash, no
    such file)."""
    if '://' in path:
        return path
    file = Path(path).expanduser()
    if '/' not in path and not file.exists():
        return path
    return file.resolve().as_uri()


def ids_file():
    runtime = os.environ.get('XDG_RUNTIME_DIR') or f'/tmp/notifyd-{os.getuid()}'
    return Path(runtime) / 'notifyd' / 'send-ids.json'


def load_ids():
    try:
        return json.loads(ids_file().read_text())
    except (OSError, ValueError):
        return {}


def save_id(name, nid):
    path = ids_file()
    ids = load_ids()
    ids[name] = nid
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(ids))
        tmp.replace(path)
    except OSError:
        pass


EPILOG = """examples:
  {prog} 'Build done' 'all 118 tests passed'
  {prog} -u critical -a battery 'Battery low' '<b>4%</b> left'
  {prog} --tag volume --progress 60 -a Volume 'Volume 60%'
  {prog} -A 'change=Change layout' -A keep=Keep 'Monitors changed'   # prints the key chosen
  {prog} 'Monitors changed' --action change:Change keep:Keep          # notify-send.py's form
  {prog} --replace-name backup 'Backup' 'running...'; {prog} --replace-name backup 'Backup' 'done'
  {prog} --title-end '★ 4.5' --image ~/cover.jpg -a MPD 'Song' 'Artist\\nAlbum'
  {prog} --fg '#ffffff' --bg '#5a1d1d' --frame '#ff5555' 'Disk full' '/home at 99%'

-h is --hint, as in notify-send (notify-send.py's -h is help); help is --help only.
Rules may override the timeout, urgency and look; `notifyd rules --app NAME` shows what applies.
"""


def build_parser(prog='notifyd send', standalone=False):
    """The send flags. STANDALONE (notifyd-send) adds --completion; `notifyd send` gets it from `notifyd
    completion`."""
    parser = argparse.ArgumentParser(
        prog=prog, add_help=False, epilog=EPILOG.format(prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Send a desktop notification through any notification daemon: notify-send and notify-send.py '
                    'compatible, with flags for the hints notifyd (and dunst) understand.')
    parser.add_argument('summary', nargs='?', default=None, help='the title (\\n and \\t are expanded)')
    parser.add_argument('body', nargs='?', default='', help='the text; Pango markup (\\n and \\t are expanded)')

    note = parser.add_argument_group('notification')
    note.add_argument('-a', '--app-name', default=None, metavar='NAME',
                      help='the sending application (rules match on it; default notifyd-send)')
    note.add_argument('-u', '--urgency', choices=sorted(URGENCIES), default=None, help='low, normal or critical')
    note.add_argument('-t', '--expire-time', type=int, default=-1, metavar='MS',
                      help='how long to show it, ms (0: until closed; rules may override)')
    note.add_argument('-i', '--icon', default='', metavar='ICON', help='app icon: an icon name or a path')
    note.add_argument('-c', '--category', default=None, metavar='TYPE', help='category hint, e.g. email.arrived')
    note.add_argument('-e', '--transient', action='store_true', help='keep it out of history')

    hints = parser.add_argument_group('hints')
    hints.add_argument('-h', '--hint', action='append', default=[], metavar='TYPE:NAME:VALUE',
                       help='extra hint; TYPE is int, double, string, byte, boolean or variant (GVariant text); '
                            'repeatable, and --hint takes several')
    hints.add_argument('--tag', default=None, help='stack tag: replaces the last one with this app and tag')
    image = hints.add_argument('--image', default=None, metavar='PATH',
                               help='picture shown in the notification (a file, URI or icon name)')
    image.complete = 'files'
    hints.add_argument('--progress', type=int, default=None, metavar='N', help='progress bar at N percent')
    hints.add_argument('--title-end', default=None, metavar='MARKUP',
                       help='shown at the right end of the title: a rating, a count (notifyd only)')
    hints.add_argument('--desktop-entry', default=None, metavar='NAME',
                       help='the sender\'s .desktop file name, without .desktop (gives the icon; rules match it)')
    hints.add_argument('--sound-name', default=None, metavar='NAME', help='a freedesktop sound name to play')
    sound = hints.add_argument('--sound-file', default=None, metavar='PATH', help='a sound file to play')
    sound.complete = 'files'
    hints.add_argument('--suppress-sound', action='store_true', help='ask the daemon not to play a sound')
    hints.add_argument('--resident', action='store_true', help='stays open after an action is chosen')
    hints.add_argument('--action-icons', action='store_true',
                       help='each action NAME is an icon name, drawn instead of its text (the text is its tooltip)')
    hints.add_argument('--fg', default=None, metavar='COLOR', help='text color of this notification (fgcolor)')
    hints.add_argument('--bg', default=None, metavar='COLOR', help='background of this notification (bgcolor)')
    hints.add_argument('--frame', default=None, metavar='COLOR', help='border color of this notification (frcolor)')
    hints.add_argument('--highlight', default=None, metavar='COLOR', help='progress bar color (hlcolor)')

    acting = parser.add_argument_group('actions, replacing and waiting')
    acting.add_argument('-A', '--action', action='append', default=[], metavar='[NAME=]TEXT',
                        help='a button (also NAME:TEXT); waits and prints the NAME chosen (repeatable, and '
                             '--action takes several)')
    acting.add_argument('--dont-quit-on-action', '--dontQuitOnAction', dest='dont_quit', action='store_true',
                        help='after an action, keep waiting (and printing) until it closes')
    acting.add_argument('-w', '--wait', action='store_true', help='wait until it is closed')
    acting.add_argument('-r', '--replace-id', '--replaces-id', dest='replace_id', type=int, default=0, metavar='ID',
                        help='replace notification ID')
    acting.add_argument('--replace-name', '--replaces-process', dest='replace_name', default=None, metavar='NAME',
                        help='replace the last notification sent with this NAME')
    acting.add_argument('-p', '--print-id', action='store_true', help='print the notification id')
    close = acting.add_argument('--close', type=int, default=None, metavar='ID', help='close notification ID and exit')
    close.complete = 'ids'

    other = parser.add_argument_group('other')
    other.add_argument('-v', '--version', action='version', version=f'{prog} {__version__}',
                       help='print the version and exit')
    if standalone:
        other.add_argument('--completion', choices=('zsh', 'bash', 'fish'), default=None, metavar='SHELL',
                           help='print a completion script for zsh, bash or fish and exit')
    other.add_argument('--help', action='help', help='show this help and exit')
    return parser


def build_hints(args):
    hints = {}
    for text in args.hint:
        name, variant = parse_hint(text)
        hints[name] = variant
    if args.urgency:
        hints['urgency'] = GLib.Variant('y', URGENCIES[args.urgency])
    if args.category:
        hints['category'] = GLib.Variant('s', args.category)
    if args.transient:
        hints['transient'] = GLib.Variant('b', True)
    if args.tag:
        hints['x-dunst-stack-tag'] = GLib.Variant('s', args.tag)
    if args.image:
        hints['image-path'] = GLib.Variant('s', image_uri(args.image))
    if args.progress is not None:
        hints['value'] = GLib.Variant('i', min(max(args.progress, 0), 100))
    for dest, (hint, how) in FLAG_HINTS.items():
        value = getattr(args, dest, None)
        if how == 'b':
            if value:
                hints[hint] = GLib.Variant('b', True)
        elif value is not None:
            if how == 'path':
                value = str(Path(value).expanduser().resolve())
            hints[hint] = GLib.Variant('s', value)
    return hints


def call(connection, method, params, reply):
    return connection.call_sync(BUS_NAME, OBJECT_PATH, IFACE, method, params,
                                GLib.VariantType(reply) if reply else None, Gio.DBusCallFlags.NONE, 5000, None)


class Waiter:
    """Subscribed before Notify is sent, so a close that comes at once is not missed."""

    def __init__(self, connection, out, quit_on_action=False):
        self.connection = connection
        self.out = out
        self.quit_on_action = quit_on_action
        self.nid = None
        self.closed = set()
        self.owner = None      # the daemon's unique name: only its signals count, and its exit ends the wait
        self.gone = False
        self.loop = GLib.MainLoop()
        self.subscriptions = [connection.signal_subscribe(None, IFACE, member, OBJECT_PATH, None,
                                                          Gio.DBusSignalFlags.NONE, self.on_signal)
                              for member in ('ActionInvoked', 'NotificationClosed')]

    def on_signal(self, _conn, sender, _path, _iface, member, params):
        if self.owner and sender != self.owner:
            return              # another client emitting look-alike signals
        values = params.unpack()
        if member == 'NotificationClosed':
            self.closed.add(values[0])
            if values[0] == self.nid:
                self.loop.quit()
        elif member == 'ActionInvoked' and values[0] == self.nid:
            print(values[1], file=self.out, flush=True)
            if self.quit_on_action:
                self.loop.quit()

    def on_owner_changed(self, _conn, _sender, _path, _iface, _member, params):
        name, old, new = params.unpack()
        if name == BUS_NAME and old == self.owner and new != self.owner:
            self.gone = True
            self.loop.quit()

    def name_owner(self):
        try:
            return self.connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                                             'GetNameOwner', GLib.Variant('(s)', (BUS_NAME,)),
                                             GLib.VariantType('(s)'), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        except GLib.Error:
            return None

    def wait(self, nid):
        """Until NID closes (or, with quit_on_action, an action is chosen), printing the key of each action
        chosen meanwhile; self.gone when the daemon went away first (it exited or restarted: NID will never
        close)."""
        self.nid = nid
        self.owner = self.name_owner()
        self.subscriptions.append(self.connection.signal_subscribe(
            'org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged', '/org/freedesktop/DBus', BUS_NAME,
            Gio.DBusSignalFlags.NONE, self.on_owner_changed))
        try:
            if self.owner is None or self.name_owner() != self.owner:
                self.gone = nid not in self.closed      # it went between Notify and here
            elif nid not in self.closed:
                self.loop.run()
        finally:
            for sub in self.subscriptions:
                self.connection.signal_unsubscribe(sub)


def main(argv=None, out=None, err=None, prog='notifyd send', standalone=False):
    out, err = out or sys.stdout, err or sys.stderr      # looked up now, so a redirected stdout counts
    parser = build_parser(prog, standalone)
    args = parser.parse_args(expand_multi(sys.argv[1:] if argv is None else list(argv)))
    if getattr(args, 'completion', None):
        from notifyd.completion import script
        out.write(script(args.completion, parser, name=prog))
        return 0
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if args.close is not None:
            call(connection, 'CloseNotification', GLib.Variant('(u)', (args.close,)), None)
            return 0
        if args.summary is None:
            parser.error('a summary is required (or --close ID)')
        hints = build_hints(args)
        actions = parse_actions(args.action)
        replace_id = args.replace_id
        if args.replace_name and not replace_id:
            replace_id = int(load_ids().get(args.replace_name, 0))
        waiter = None
        if args.wait or actions:
            waiter = Waiter(connection, out, quit_on_action=bool(actions) and not args.dont_quit)
        params = GLib.Variant('(susssasa{sv}i)', (args.app_name or 'notifyd-send', replace_id, args.icon,
                                                  unescape(args.summary), unescape(args.body), actions, hints,
                                                  args.expire_time))
        nid = call(connection, 'Notify', params, '(u)').unpack()[0]
        if args.replace_name:
            save_id(args.replace_name, nid)
        if args.print_id:
            print(nid, file=out, flush=True)
        if waiter:
            waiter.wait(nid)
            if waiter.gone:
                print(f'{prog}: the notification daemon went away', file=err)
                return 1
    except SendError as exc:
        print(f'{prog}: {exc}', file=err)
        return 2
    except GLib.Error as exc:
        print(f'{prog}: {exc.message}', file=err)
        return 1
    return 0


def standalone_main(argv=None):
    """The `notifyd-send` command."""
    return main(argv, prog='notifyd-send', standalone=True)
