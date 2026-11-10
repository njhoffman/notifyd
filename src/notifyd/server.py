"""D-Bus: org.freedesktop.Notifications (spec 1.2) and notifyd's own control interface.

Both live at /org/freedesktop/Notifications. notifyd also owns io.github.njhoffman.Notifyd, so
`notifyd ctl` reaches notifyd itself and not whichever daemon owns the notification name.
"""

import json

from gi.repository import Gio, GLib

from notifyd import __version__
from notifyd.model import CLOSED

BUS_NAME = 'org.freedesktop.Notifications'
CONTROL_NAME = 'io.github.njhoffman.Notifyd'
OBJECT_PATH = '/org/freedesktop/Notifications'
IFACE = 'org.freedesktop.Notifications'
CONTROL_IFACE = 'io.github.njhoffman.Notifyd'

CAPABILITIES = ['actions', 'body', 'body-hyperlinks', 'body-markup', 'icon-static', 'persistence',
                'x-canonical-private-synchronous', 'x-dunst-stack-tag', 'synchronous', 'x-notifyd']

INTROSPECTION = f'''
<node>
  <interface name="{IFACE}">
    <method name="Notify">
      <arg direction="in" type="s" name="app_name"/>
      <arg direction="in" type="u" name="replaces_id"/>
      <arg direction="in" type="s" name="app_icon"/>
      <arg direction="in" type="s" name="summary"/>
      <arg direction="in" type="s" name="body"/>
      <arg direction="in" type="as" name="actions"/>
      <arg direction="in" type="a{{sv}}" name="hints"/>
      <arg direction="in" type="i" name="expire_timeout"/>
      <arg direction="out" type="u" name="id"/>
    </method>
    <method name="CloseNotification"><arg direction="in" type="u" name="id"/></method>
    <method name="GetCapabilities"><arg direction="out" type="as" name="capabilities"/></method>
    <method name="GetServerInformation">
      <arg direction="out" type="s" name="name"/>
      <arg direction="out" type="s" name="vendor"/>
      <arg direction="out" type="s" name="version"/>
      <arg direction="out" type="s" name="spec_version"/>
    </method>
    <signal name="NotificationClosed"><arg type="u" name="id"/><arg type="u" name="reason"/></signal>
    <signal name="ActionInvoked"><arg type="u" name="id"/><arg type="s" name="action_key"/></signal>
  </interface>
  <interface name="{CONTROL_IFACE}">
    <method name="Status"><arg direction="out" type="s" name="json"/></method>
    <method name="History">
      <arg direction="in" type="u" name="limit"/>
      <arg direction="out" type="s" name="json"/>
    </method>
    <method name="Close">
      <arg direction="in" type="u" name="id"/>
      <arg direction="out" type="b" name="closed"/>
    </method>
    <method name="Invoke">
      <arg direction="in" type="u" name="id"/>
      <arg direction="in" type="s" name="action_key"/>
      <arg direction="out" type="b" name="invoked"/>
    </method>
    <method name="List"><arg direction="out" type="s" name="json"/></method>
    <method name="SetMode">
      <arg direction="in" type="s" name="mode"/>
      <arg direction="out" type="s" name="current"/>
    </method>
    <method name="CloseAll"><arg direction="out" type="u" name="count"/></method>
    <method name="Pause">
      <arg direction="in" type="b" name="on"/>
      <arg direction="out" type="b" name="paused"/>
    </method>
    <method name="Reload"><arg direction="out" type="s" name="result"/></method>
    <method name="SetPreset">
      <arg direction="in" type="s" name="name"/>
      <arg direction="out" type="s" name="result"/>
    </method>
    <method name="Preview">
      <arg direction="in" type="s" name="preset"/>
      <arg direction="out" type="u" name="count"/>
    </method>
    <signal name="Event"><arg type="s" name="json"/></signal>
  </interface>
</node>
'''

IMAGE_HINTS = ('image-data', 'image_data', 'icon_data')


def unpack_hints(variant):
    """a{sv} -> dict; image data keeps its pixels as bytes instead of a list of ints."""
    hints = {}
    for i in range(variant.n_children()):
        entry = variant.get_child_value(i)
        key = entry.get_child_value(0).get_string()
        value = entry.get_child_value(1).get_variant()
        if key in IMAGE_HINTS and value.get_type_string() == '(iiibiiay)':
            fields = [value.get_child_value(j).unpack() for j in range(6)]
            fields.append(value.get_child_value(6).get_data_as_bytes().get_data())
            hints[key] = tuple(fields)
        else:
            hints[key] = value.unpack()
    return hints


class Server:
    """Serves MANAGER on CONNECTION. CONTROL is the daemon (reload, presets, previews)."""

    def __init__(self, connection, manager, control, log):
        self.connection = connection
        self.manager = manager
        self.control = control
        self.log = log
        self.registrations = []
        manager.on_closed = self.emit_closed
        manager.on_action = self.emit_action
        log.listeners.append(self.emit_event)
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        for iface in node.interfaces:
            self.registrations.append(connection.register_object(OBJECT_PATH, iface, self.on_call, None, None))

    def unregister(self):
        for reg in self.registrations:
            self.connection.unregister_object(reg)
        self.registrations = []
        if self.emit_event in self.log.listeners:
            self.log.listeners.remove(self.emit_event)

    # -- signals --------------------------------------------------------
    def _signal(self, iface, name, value):
        try:
            self.connection.emit_signal(None, OBJECT_PATH, iface, name, value)
        except GLib.Error as exc:
            self.log.error(f'emitting {name}: {exc.message}') if name != 'Event' else None

    def emit_closed(self, nid, reason):
        self._signal(IFACE, 'NotificationClosed', GLib.Variant('(uu)', (nid, reason)))

    def emit_action(self, nid, key):
        self._signal(IFACE, 'ActionInvoked', GLib.Variant('(us)', (nid, key)))

    def emit_event(self, event):
        self._signal(CONTROL_IFACE, 'Event', GLib.Variant('(s)', (json.dumps(event, ensure_ascii=False),)))

    # -- calls ----------------------------------------------------------
    def on_call(self, connection, sender, path, iface, method, params, invocation):
        try:
            result = self.dispatch(iface, method, params)
        except Exception as exc:  # a bad call must not take the daemon down
            self.log.error(f'{method} from {sender}: {exc}')
            invocation.return_dbus_error(f'{CONTROL_IFACE}.Error', str(exc))
            return
        invocation.return_value(result)

    def dispatch(self, iface, method, params):
        m = self.manager
        if iface == IFACE:
            if method == 'Notify':
                app, replaces, icon, summary, body = (params.get_child_value(i).unpack() for i in range(5))
                actions = params.get_child_value(5).unpack()
                hints = unpack_hints(params.get_child_value(6))
                timeout = params.get_child_value(7).unpack()
                nid = m.notify(app, replaces, icon, summary, body, actions, hints, timeout)
                return GLib.Variant('(u)', (nid,))
            if method == 'CloseNotification':
                m.close(params.unpack()[0], CLOSED)
                return None
            if method == 'GetCapabilities':
                return GLib.Variant('(as)', (CAPABILITIES,))
            if method == 'GetServerInformation':
                return GLib.Variant('(ssss)', ('notifyd', 'njhoffman', __version__, '1.2'))
        elif iface == CONTROL_IFACE:
            if method == 'Status':
                return GLib.Variant('(s)', (json.dumps({'version': __version__, **m.status()}),))
            if method == 'History':
                limit = params.unpack()[0]
                entries = list(m.history)[-limit:] if limit else list(m.history)
                return GLib.Variant('(s)', (json.dumps(entries, ensure_ascii=False),))
            if method == 'Close':
                return GLib.Variant('(b)', (m.close(params.unpack()[0], CLOSED),))
            if method == 'Invoke':
                nid, key = params.unpack()
                n = m.active.get(nid)
                if n is None:
                    return GLib.Variant('(b)', (False,))
                if not key:
                    key = 'default' if n.action_label('default') is not None else (n.actions[0][0] if n.actions else '')
                return GLib.Variant('(b)', (bool(key) and m.invoke(nid, key),))
            if method == 'List':
                return GLib.Variant('(s)', (json.dumps(m.listing(), ensure_ascii=False),))
            if method == 'SetMode':
                mode = params.unpack()[0].strip()
                return GLib.Variant('(s)', (m.set_mode(mode) if mode else m.mode,))
            if method == 'CloseAll':
                return GLib.Variant('(u)', (m.close_all(),))
            if method == 'Pause':
                return GLib.Variant('(b)', (m.pause(params.unpack()[0]),))
            if method == 'Reload':
                return GLib.Variant('(s)', (self.control.reload(),))
            if method == 'SetPreset':
                return GLib.Variant('(s)', (self.control.set_preset(params.unpack()[0]),))
            if method == 'Preview':
                return GLib.Variant('(u)', (self.control.preview(params.unpack()[0]),))
        raise ValueError(f'unknown method {iface}.{method}')


def owner_description(connection, name):
    """'deadd-notification-center (pid 123)' for the current owner of NAME, else None."""
    try:
        pid = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                                   'GetConnectionUnixProcessID', GLib.Variant('(s)', (name,)),
                                   GLib.VariantType('(u)'), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
    except GLib.Error:
        return None
    try:
        with open(f'/proc/{pid}/comm') as comm:
            return f'{comm.read().strip()} (pid {pid})'
    except OSError:
        return f'pid {pid}'
