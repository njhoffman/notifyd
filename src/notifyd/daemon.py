"""`notifyd daemon`: wires config, log, manager, renderer and the D-Bus server into a main loop."""

import signal

from gi.repository import Gio, GLib

try:                                     # PyGObject 3.56 deprecates GLib.unix_signal_add; 24.04 has no GLibUnix
    from gi.repository import GLibUnix
    signal_add = GLibUnix.signal_add
except ImportError:
    signal_add = GLib.unix_signal_add

from notifyd import __version__, art
from notifyd.config import ConfigError, default_path, load_config, load_preset, preset_dirs
from notifyd.effects import Filter, LockState, Runner, SoundPlayer, XIdle
from notifyd.history import default_path as history_default_path
from notifyd.log import EventLog
from notifyd.manager import GLibScheduler, Manager
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


# what previews override: shown long enough to look at, never skipped, and no side effects (rule
# commands, buttons, sounds) or history entries from samples that happen to match the user's rules
LOCK_POLL = 2   # seconds between screen-lock checks (lock.pause)
PREVIEW_EXTRA = {'timeout': 15000, 'skip': False, 'run': None, 'filter': None, 'sound': False, 'buttons': [],
                 'history': {'keep': False}}


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
        self.lock_state = None   # (processes, LockState) while lock.pause is on
        self.manager = None
        self.config_broken = False   # the last reload failed: the next good one says so
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
            self.config_notice('config error, kept the last good one', str(exc), timeout=0)
            self.config_broken = True
            return f'error: {exc}'
        if self.config_broken:
            self.config_broken = False
            self.config_notice('config reloaded', str(self.config_path), timeout=3000)
        self.configure_log(config)
        for warning in config.warnings:
            self.log.warn(warning)
        self.manager.reload(config)               # it reloads the renderer's looks too
        return 'reloaded' + (f' with {len(config.warnings)} warning(s)' if config.warnings else '')

    def config_notice(self, summary, body, timeout):
        """A notification about the config, replacing the last one: an error stays until it is fixed."""
        if self.manager is None:
            return
        self.manager.notify('notifyd', summary=summary, body=GLib.markup_escape_text(body),
                            hints={'x-dunst-stack-tag': 'notifyd-config', 'urgency': 1},
                            extra={'timeout': timeout, 'skip': False, 'run': None, 'sound': False,
                                   'history': {'keep': False}})

    def set_preset(self, name):
        try:
            load_preset(name)
        except ConfigError as exc:
            return f'error: {exc}'
        self.manager.config.settings['preset'] = name
        self.manager.restyle()                    # open ones too, not only what comes next
        self.log.info(f'preset {name} (until the next reload)')
        return f'preset {name}'

    def center(self, what):
        if self.renderer is None:
            return False
        return self.renderer.center.toggle(what or 'toggle')

    def preview(self, preset):
        preset = preset or self.manager.config.settings['preset']
        load_preset(preset)
        for index, sample in enumerate(SAMPLES):
            hints = {**sample.get('hints', {}), 'x-dunst-stack-tag': f'notifyd-preview-{index}'}
            self.manager.notify(sample['app'], 0, sample.get('app_icon', ''), sample['summary'], sample['body'],
                                sample.get('actions', []), hints, -1,
                                extra={**PREVIEW_EXTRA, 'preset': preset})
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
        """Reload when the config file or one of the user's preset files changes, so styles can be
        tuned with the center and popups open (they follow at once)."""
        events = (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
                  Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.RENAMED, Gio.FileMonitorEvent.DELETED)

        def changed(source, file, other, event):
            names = [f.get_basename() for f in (file, other) if f is not None]
            if event in events and (source is config or any(n.endswith('.yaml') for n in names)):
                self.reload()
        config = Gio.File.new_for_path(str(self.config_path)).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        config.connect('changed', changed)
        monitors = [config]
        presets = preset_dirs()[0]
        if presets.is_dir():
            monitor = Gio.File.new_for_path(str(presets)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            monitor.connect('changed', changed)
            monitors.append(monitor)
        return monitors

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
                               runner=Runner(self.log), filter_runner=Filter(), sound=SoundPlayer(self.log),
                               idle=idle,
                               thumbs_path=art.cache_dir().with_name('thumbs'))
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
        self.manager.start()                      # a timed pause that ran out meanwhile shows its held ones now
        owners = [self.own(connection, CONTROL_NAME), self.own(connection, BUS_NAME)]
        signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.stop)
        signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.stop)
        signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, lambda: self.reload() and True)
        # deadd's i3 binding: kill -USR1 toggles the notification center
        signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, lambda: self.center('toggle') or True)
        self._monitor = self.watch_config()
        GLib.timeout_add_seconds(LOCK_POLL, self.check_lock)
        try:
            self.loop.run()
        finally:
            self.manager.shutdown()
            for owner in owners:
                Gio.bus_unown_name(owner)
            self.server.unregister()
        return self.exit_code

    def check_lock(self):
        """Every LOCK_POLL seconds with lock.pause on: hold notifications back while the screen is locked."""
        lock = self.manager.config.settings.get('lock') or {}
        if not lock.get('pause'):
            if self.manager.locked:
                self.manager.set_locked(False)
            return True
        processes = tuple(lock.get('processes') or ())
        if self.lock_state is None or self.lock_state[0] != processes:
            try:
                system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            except GLib.Error:
                system = None                    # no logind: locker processes only
            self.lock_state = (processes, LockState(list(processes), connection=system))
        self.manager.set_locked(self.lock_state[1]())
        return True

    def stop(self):
        self.log.info('stopping')
        self.loop.quit()
        return False

