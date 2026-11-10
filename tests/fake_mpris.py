"""A fake MPRIS player for tests: owns org.mpris.MediaPlayer2.fake, records calls to stdout."""

import sys

from gi.repository import Gio, GLib

XML = '''<node>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Previous"/><method name="PlayPause"/><method name="Next"/>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="Metadata" type="a{sv}" access="read"/>
  </interface>
</node>'''

STATUS = sys.argv[1] if len(sys.argv) > 1 else 'Playing'
TITLE = sys.argv[2] if len(sys.argv) > 2 else 'Song'
NAME = sys.argv[3] if len(sys.argv) > 3 else 'fake'


def on_call(_conn, _sender, _path, _iface, method, _params, invocation):
    print(method, flush=True)
    invocation.return_value(None)


def get_property(_conn, _sender, _path, _iface, prop):
    if prop == 'PlaybackStatus':
        return GLib.Variant('s', STATUS)
    return GLib.Variant('a{sv}', {'xesam:title': GLib.Variant('s', TITLE),
                                  'xesam:artist': GLib.Variant('as', ['Artist']),
                                  'xesam:album': GLib.Variant('s', 'Album')})


bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
bus.register_object('/org/mpris/MediaPlayer2', info, on_call, get_property, None)
Gio.bus_own_name_on_connection(bus, f'org.mpris.MediaPlayer2.{NAME}', Gio.BusNameOwnerFlags.NONE,
                               lambda *a: print('ready', flush=True), None)
GLib.MainLoop().run()
