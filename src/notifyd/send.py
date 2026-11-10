"""`notifyd send`: notify-send with notify-send.py's extras, for any notification daemon.

notify-send flags work as they do there (-a -u -t -i -c -h -A -r -p -e -w). On top:
  --replace-name NAME   replace the last notification sent with NAME (ids kept in $XDG_RUNTIME_DIR)
                        (--replaces-process is the notify-send.py spelling)
  --tag TAG             x-dunst-stack-tag: the daemon replaces the last one with this app and tag
  --image PATH          image-path hint (a file:// URI, which deadd needs)
  --progress N          value hint: a progress bar at N percent
  --close ID            close a notification instead of sending one
With actions, it waits and prints the key of the action chosen (as notify-send does).
"""

import argparse
import json
import os
import sys
from pathlib import Path

from gi.repository import Gio, GLib

BUS_NAME = 'org.freedesktop.Notifications'
OBJECT_PATH = '/org/freedesktop/Notifications'
IFACE = 'org.freedesktop.Notifications'
URGENCIES = {'low': 0, 'normal': 1, 'critical': 2}
HINT_TYPES = {'int': 'i', 'double': 'd', 'string': 's', 'byte': 'y', 'boolean': 'b', 'bool': 'b'}


class SendError(Exception):
    pass


def parse_hint(text):
    """'TYPE:NAME:VALUE' -> (name, GLib.Variant)."""
    parts = text.split(':', 2)
    if len(parts) != 3:
        raise SendError(f'hint {text!r}: expected TYPE:NAME:VALUE')
    kind, name, value = parts
    signature = HINT_TYPES.get(kind.lower())
    if not signature:
        raise SendError(f'hint {text!r}: type must be one of {", ".join(sorted(set(HINT_TYPES)))}')
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


def image_uri(path):
    if '://' in path:
        return path
    return Path(path).expanduser().resolve().as_uri()


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
  notifyd send 'Build done' 'all 118 tests passed'
  notifyd send -u critical -a battery 'Battery low' '<b>4%</b> left'
  notifyd send --tag volume --progress 60 -a Volume 'Volume 60%'
  notifyd send -A 'change=Change layout' -A keep=Keep 'Monitors changed'   # prints the key chosen
  notifyd send --replace-name backup 'Backup' 'running...'; notifyd send --replace-name backup 'Backup' 'done'

-h is --hint, as in notify-send; help is --help only.
"""


def build_parser():
    parser = argparse.ArgumentParser(
        prog='notifyd send', add_help=False, epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Send a desktop notification through any notification daemon (notify-send compatible).')
    parser.add_argument('summary', nargs='?', default=None, help='the title')
    parser.add_argument('body', nargs='?', default='', help='the text; Pango markup')
    parser.add_argument('-a', '--app-name', default='notifyd-send', metavar='NAME',
                        help='the sending application (rules match on it)')
    parser.add_argument('-u', '--urgency', choices=sorted(URGENCIES), default=None, help='low, normal or critical')
    parser.add_argument('-t', '--expire-time', type=int, default=-1, metavar='MS',
                        help='how long to show it, ms (0: until closed; rules may override)')
    parser.add_argument('-i', '--icon', default='', metavar='ICON', help='app icon: an icon name or a path')
    parser.add_argument('-c', '--category', default=None, metavar='TYPE', help='category hint, e.g. email.arrived')
    parser.add_argument('-h', '--hint', action='append', default=[], metavar='TYPE:NAME:VALUE',
                        help='extra hint; TYPE is int, double, string, byte or boolean (repeatable)')
    parser.add_argument('-A', '--action', action='append', default=[], metavar='[NAME=]TEXT',
                        help='a button (also NAME:TEXT); waits and prints the NAME chosen (repeatable)')
    parser.add_argument('-r', '--replace-id', type=int, default=0, metavar='ID', help='replace notification ID')
    parser.add_argument('--replace-name', '--replaces-process', dest='replace_name', default=None, metavar='NAME',
                        help='replace the last notification sent with this NAME')
    parser.add_argument('-p', '--print-id', action='store_true', help='print the notification id')
    parser.add_argument('-e', '--transient', action='store_true', help='keep it out of history')
    parser.add_argument('-w', '--wait', action='store_true', help='wait until it is closed')
    parser.add_argument('--tag', default=None, help='stack tag: replaces the last one with this app and tag')
    image = parser.add_argument('--image', default=None, metavar='PATH', help='picture shown in the notification')
    image.complete = 'files'
    parser.add_argument('--progress', type=int, default=None, metavar='N', help='progress bar at N percent')
    parser.add_argument('--close', type=int, default=None, metavar='ID', help='close notification ID and exit')
    parser.add_argument('--help', action='help', help='show this help and exit')
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
    return hints


def call(connection, method, params, reply):
    return connection.call_sync(BUS_NAME, OBJECT_PATH, IFACE, method, params,
                                GLib.VariantType(reply) if reply else None, Gio.DBusCallFlags.NONE, 5000, None)


class Waiter:
    """Subscribed before Notify is sent, so a close that comes at once is not missed."""

    def __init__(self, connection, out):
        self.connection = connection
        self.out = out
        self.nid = None
        self.closed = set()
        self.loop = GLib.MainLoop()
        self.subscriptions = [connection.signal_subscribe(None, IFACE, member, OBJECT_PATH, None,
                                                          Gio.DBusSignalFlags.NONE, self.on_signal)
                              for member in ('ActionInvoked', 'NotificationClosed')]

    def on_signal(self, _conn, _sender, _path, _iface, member, params):
        values = params.unpack()
        if member == 'NotificationClosed':
            self.closed.add(values[0])
            if values[0] == self.nid:
                self.loop.quit()
        elif member == 'ActionInvoked' and values[0] == self.nid:
            print(values[1], file=self.out, flush=True)

    def wait(self, nid):
        """Until NID closes, printing the key of an action chosen meanwhile."""
        self.nid = nid
        try:
            if nid not in self.closed:
                self.loop.run()
        finally:
            for sub in self.subscriptions:
                self.connection.signal_unsubscribe(sub)


def main(argv=None, out=sys.stdout, err=sys.stderr):
    parser = build_parser()
    args = parser.parse_args(argv)
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
        waiter = Waiter(connection, out) if args.wait or actions else None
        params = GLib.Variant('(susssasa{sv}i)', (args.app_name, replace_id, args.icon, args.summary, args.body,
                                                  actions, hints, args.expire_time))
        nid = call(connection, 'Notify', params, '(u)').unpack()[0]
        if args.replace_name:
            save_id(args.replace_name, nid)
        if args.print_id:
            print(nid, file=out, flush=True)
        if waiter:
            waiter.wait(nid)
    except SendError as exc:
        print(f'notifyd send: {exc}', file=err)
        return 2
    except GLib.Error as exc:
        print(f'notifyd send: {exc.message}', file=err)
        return 1
    return 0
