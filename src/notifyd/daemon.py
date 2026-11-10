"""`notifyd daemon`: wires config, log, manager, renderer and the D-Bus server into a main loop."""

import signal

from gi.repository import Gio, GLib

from notifyd import __version__
from notifyd.config import ConfigError, default_path, load_config, load_preset
from notifyd.effects import Runner, SoundPlayer, XIdle
from notifyd.history import default_path as history_default_path
from notifyd.log import EventLog
from notifyd.manager import GLibScheduler, Manager
from notifyd.model import CLOSED
from notifyd.server import BUS_NAME, CONTROL_NAME, Server, owner_description

# What `notifyd ctl preview` shows: one of each kind a preset has to handle
SAMPLES = [
    dict(app='notifyd', summary='Low urgency', body='Something happened in the background.',
         app_icon='dialog-information', hints={'urgency': 0}),
    dict(app='musicman', summary='★ 4.5  Song title', body='<b>Artist</b>  ·  Album\n'
         '<span class="accent">indie</span>  <span class="muted">late night</span>',
         app_icon='audio-x-generic', hints={'urgency': 1, 'image-path': 'audio-x-generic'}),
    dict(app='Volume', summary='Volume 60%', body='', app_icon='audio-volume-medium',
         hints={'urgency': 1, 'value': 60}),
    dict(app='displayd', summary='Monitors changed', body='HDMI-1 connected: <i>2560×1440</i>',
         app_icon='video-display', actions=['change', 'Change layout', 'keep', 'Keep'], hints={'urgency': 1}),
    dict(app='notifyd', summary='Critical', body='Battery at 4%. This one stays until dismissed.',
         app_icon='dialog-error', hints={'urgency': 2}),
]


def set_process_name(name='notifyd'):
    """/proc/PID/comm is python3 otherwise: pgrep, ps -o comm and owner messages would not find us."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(15, name.encode(), 0, 0, 0)   # PR_SET_NAME
    except (OSError, AttributeError):
        pass


class Daemon:
    def __init__(self, config_path=None, renderer='gtk', replace=False, log_format=None, color=None,
                 level=None):
        self.config_path = config_path or default_path()
        self.renderer_name = renderer
        self.replace = replace
        self.overrides = {k: v for k, v in (('format', log_format), ('color', color), ('level', level)) if v}
        self.log = EventLog()
        self.exit_code = 0
        self.loop = None
        self.acquired = set()

    def configure_log(self, config):
        settings = {**config.settings['log'], **self.overrides}
        self.log.configure(settings.get('format', 'pretty'), settings.get('color', 'auto'),
                           settings.get('level', 'info'), settings.get('file'))

    # -- control (called over D-Bus) ---------------------------------------
    def reload(self):
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            self.log.error(f'reload failed, keeping the old config: {exc}')
            return f'error: {exc}'
        self.configure_log(config)
        for warning in config.warnings:
            self.log.warn(warning)
        self.manager.reload(config)
        if self.renderer is not None and hasattr(self.renderer, 'reload'):
            self.renderer.reload()
        return 'reloaded' + (f' with {len(config.warnings)} warning(s)' if config.warnings else '')

    def set_preset(self, name):
        try:
            load_preset(name)
        except ConfigError as exc:
            return f'error: {exc}'
        self.manager.config.settings['preset'] = name
        if self.renderer is not None and hasattr(self.renderer, 'reload'):
            self.renderer.reload()
        self.log.info(f'preset {name} (until the next reload)')
        return f'preset {name}'

    def preview(self, preset):
        preset = preset or self.manager.config.settings['preset']
        load_preset(preset)
        for index, sample in enumerate(SAMPLES):
            hints = {**sample.get('hints', {}), 'x-dunst-stack-tag': f'notifyd-preview-{index}'}
            self.manager.notify(sample['app'], 0, sample.get('app_icon', ''), sample['summary'], sample['body'],
                                sample.get('actions', []), hints, -1,
                                extra={'preset': preset, 'timeout': 15000, 'skip': False})
        return len(SAMPLES)

    # -- running --------------------------------------------------------
    def make_renderer(self):
        if self.renderer_name == 'none':
            return None
        from notifyd.render.gtk import GtkRenderer  # needs a display
        return GtkRenderer(self.manager, self.log)

    def own(self, connection, name):
        flags = Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT | Gio.BusNameOwnerFlags.DO_NOT_QUEUE
        if self.replace:
            flags |= Gio.BusNameOwnerFlags.REPLACE

        def acquired(_conn, owned):
            self.acquired.add(owned)
            if owned == BUS_NAME:
                preset = self.manager.config.settings['preset']
                self.log.info(f'notifyd {__version__} owns {BUS_NAME}; preset {preset}, renderer {self.renderer_name}')

        def lost(_conn, lost_name):
            if lost_name in self.acquired:
                self.log.warn(f'{lost_name} was taken over by another daemon; exiting')
            else:
                owner = owner_description(connection, lost_name)
                self.log.error(f'cannot own {lost_name}: owned by {owner or "another process"} '
                               f'(stop it, `notify-daemon use notifyd`, or run with --replace)')
                self.exit_code = 1
            self.loop.quit()
        return Gio.bus_own_name_on_connection(connection, name, flags, acquired, lost)

    def watch_config(self):
        path = Gio.File.new_for_path(str(self.config_path))
        monitor = path.monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)

        def changed(_monitor, _file, _other, event):
            if event in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
                         Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.RENAMED):
                self.reload()
        monitor.connect('changed', changed)
        return monitor

    def run(self, connection=None):
        set_process_name()
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            self.log.error(str(exc))
            return 2
        self.configure_log(config)
        for warning in config.warnings:
            self.log.warn(warning)
        history_path = history_default_path() if config.settings['history'].get('persist') else None
        idle = XIdle() if self.renderer_name == 'gtk' else None
        self.manager = Manager(config, self.log, GLibScheduler(), history_path=history_path,
                               state_path=history_default_path().with_name('state.json'),
                               runner=Runner(self.log), sound=SoundPlayer(self.log), idle=idle)
        try:
            self.renderer = self.make_renderer()
        except RuntimeError as exc:
            self.log.error(f'cannot draw notifications: {exc} (use --renderer none for a log-only daemon)')
            return 1
        if self.renderer is not None:
            self.manager.renderer = self.renderer
        connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.loop = GLib.MainLoop()
        self.server = Server(connection, self.manager, self, self.log)
        owners = [self.own(connection, CONTROL_NAME), self.own(connection, BUS_NAME)]
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.stop)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.stop)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, lambda: self.reload() and True)
        self._monitor = self.watch_config()
        try:
            self.loop.run()
        finally:
            self.manager.close_all(CLOSED)
            for owner in owners:
                Gio.bus_unown_name(owner)
            self.server.unregister()
        return self.exit_code

    def stop(self):
        self.log.info('stopping')
        self.loop.quit()
        return False

