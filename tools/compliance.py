#!/usr/bin/env python3
"""Protocol checks against whatever daemon owns org.freedesktop.Notifications on this session bus.

    tools/compliance.py [--json]

Each check sends what musicman, backdrop and displayd send and looks at what comes back: ids,
NotificationClosed reasons, capabilities. `just compliance` runs it against notifyd, dunst and deadd,
each on its own bus and X display, and prints them side by side.
"""

import json
import sys
import time

from gi.repository import Gio, GLib

NAME = 'org.freedesktop.Notifications'
PATH = '/org/freedesktop/Notifications'


class Client:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.closed = {}
        self.bus.signal_subscribe(None, NAME, 'NotificationClosed', PATH, None, Gio.DBusSignalFlags.NONE,
                                  lambda *a: self.closed.__setitem__(*a[5].unpack()))

    def call(self, method, params=None, reply=None):
        return self.bus.call_sync(NAME, PATH, NAME, method, params, GLib.VariantType(reply) if reply else None,
                                  Gio.DBusCallFlags.NONE, 5000, None)

    def notify(self, summary, body='', app='compliance', replaces=0, hints=None, actions=(), timeout=-1):
        variants = {k: v if isinstance(v, GLib.Variant) else GLib.Variant('s', v) for k, v in (hints or {}).items()}
        params = GLib.Variant('(susssasa{sv}i)', (app, replaces, '', summary, body, list(actions), variants, timeout))
        return self.call('Notify', params, '(u)').unpack()[0]

    def close(self, nid):
        self.call('CloseNotification', GLib.Variant('(u)', (nid,)))

    def pump(self, seconds):
        end = time.monotonic() + seconds
        context = GLib.MainContext.default()
        while time.monotonic() < end:
            context.iteration(False)
            time.sleep(0.01)


def checks(c):
    out = {}
    name, _vendor, version, spec = c.call('GetServerInformation', None, '(ssss)').unpack()
    out['server'] = f'{name} {version}'
    out['spec'] = spec
    caps = set(c.call('GetCapabilities', None, '(as)').unpack()[0])
    for cap in ('body-markup', 'actions', 'persistence', 'x-dunst-stack-tag', 'x-canonical-private-synchronous'):
        out[f'cap {cap}'] = cap in caps

    first = c.notify('replace me')
    again = c.notify('replaced', replaces=first)
    out['replace id keeps the id'] = again == first

    a = c.notify('tag a', hints={'x-dunst-stack-tag': 'compliance'})
    b = c.notify('tag b', hints={'x-dunst-stack-tag': 'compliance'})
    c.pump(0.3)
    out['stack tag: same id'] = a == b
    out['stack tag: old one closed or reused'] = a == b or a in c.closed

    sync_a = c.notify('sync a', hints={'x-canonical-private-synchronous': 'compliance-sync'})
    sync_b = c.notify('sync b', hints={'x-canonical-private-synchronous': 'compliance-sync'})
    c.pump(0.3)
    out['synchronous hint replaces'] = sync_a == sync_b or sync_a in c.closed

    nid = c.notify('close me')
    c.close(nid)
    c.pump(0.5)
    out['CloseNotification -> reason 3'] = c.closed.get(nid) == 3

    nid = c.notify('expire me', timeout=400)
    c.pump(2.0)
    out['expire_timeout 400ms -> reason 1'] = c.closed.get(nid) == 1

    nid = c.notify('markup', '<span foreground="#7aa2f7">blue</span> <b>bold</b> Tom & Jerry')
    out['markup and a bare & accepted'] = nid > 0

    nid = c.notify('image', hints={'image-path': '/usr/share/icons/hicolor/48x48/apps/firefox.png'})
    out['image-path plain path accepted'] = nid > 0

    nid = c.notify('actions', actions=['default', 'Open', 'change', 'Change layout'])
    out['actions accepted'] = nid > 0

    nid = c.notify('progress', hints={'value': GLib.Variant('i', 40)})
    out['value hint accepted'] = nid > 0

    unknown = c.notify('replace unknown', replaces=987654)
    out['replace of an unknown id gets an id'] = unknown > 0
    return out


def main():
    try:
        result = checks(Client())
    except GLib.Error as exc:
        print(json.dumps({'error': exc.message}))
        return 1
    if '--json' in sys.argv:
        print(json.dumps(result))
    else:
        for key, value in result.items():
            print(f'{key:<40} {value}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
