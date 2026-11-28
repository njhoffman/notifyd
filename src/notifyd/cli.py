"""notifyd's command line.

    notifyd daemon [-c CONFIG] [--renderer gtk|none] [--replace] [--log-format pretty|json] [--color ...]
    notifyd send SUMMARY [BODY] [notify-send flags]
    notifyd ctl status | list | history [-n N] [--json|--pick] | pop [ID]
              | forget ID|--all | center [toggle|show|hide] | close [ID] [--app X ...] | action [ID [KEY]]
              | reply ID TEXT | close-all | pause|toggle [--for DUR] | resume | mode [NAME] [--for DUR]
              | bar [--follow] | reload | preset NAME | preview [PRESET] | watch [--json]
    notifyd presets [--pick | --names]
    notifyd import deadd [--yml PATH] [--css PATH] | dunst [--rc PATH]  [-o OUT] [--force]
    notifyd check [-c CONFIG]
    notifyd rules [-c CONFIG] [--app A] [--summary S] [--body B] [--urgency U] [--hint NAME=VALUE]
    notifyd completion zsh|bash|fish
    notifyd help [COMMAND...] | version
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from notifyd import __version__


def gio():
    from gi.repository import Gio, GLib
    return Gio, GLib


# -- ctl -----------------------------------------------------------------------

# fields `ctl close` matches on (any of the rules' match fields)
CLOSE_FIELDS = ('app', 'summary', 'body', 'category', 'urgency', 'stack-tag', 'desktop-entry')


def duration_seconds(value, where):
    """Whole seconds of a --for DURATION (30m, 1h, 90s, 500ms rounds up), or None when not given."""
    if value is None:
        return None
    from notifyd.config import ConfigError, parse_duration
    if str(value).strip().replace('.', '', 1).isdigit():
        raise SystemExit(f'notifyd ctl {where}: give a unit, e.g. {value}m or {value}s')
    try:
        ms = parse_duration(value, where)
    except ConfigError as exc:
        raise SystemExit(f'notifyd ctl {exc}') from exc
    if ms <= 0:
        raise SystemExit(f'notifyd ctl {where}: expected a duration above 0, got {value!r}')
    if ms > 30 * 86400 * 1000:
        raise SystemExit(f'notifyd ctl {where}: at most 30 days (720h), got {value!r}')
    return -(-ms // 1000)


def control_call(method, params=None, reply='(s)'):
    Gio, GLib = gio()
    from notifyd.server import CONTROL_IFACE, CONTROL_NAME, OBJECT_PATH, owner_description
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        result = connection.call_sync(CONTROL_NAME, OBJECT_PATH, CONTROL_IFACE, method, params,
                                      GLib.VariantType(reply), Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error as exc:
        if 'ServiceUnknown' in (exc.message or '') or 'was not provided' in (exc.message or ''):
            owner = owner_description(connection, 'org.freedesktop.Notifications')
            raise SystemExit(f'notifyd is not running (notifications go to {owner or "no daemon"})') from exc
        raise SystemExit(f'notifyd ctl: {exc.message}') from exc
    return result.unpack()[0]


def ctl(args):
    _Gio, GLib = gio()
    from notifyd.log import format_pretty, one_line, use_color
    command = args.command
    if command == 'status':
        status = json.loads(control_call('Status'))
        width = max(map(len, status))
        for key, value in status.items():
            print(f'{key:<{width}}  {value}')
    elif command == 'history':
        entries = json.loads(control_call('History', GLib.Variant('(u)', (args.n,))))
        if args.pick:
            return pick_history(entries)
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
        else:
            color = use_color('auto', sys.stdout)
            for entry in entries:
                print(format_pretty({'event': entry.get('closed') or 'closed', 'time': entry['received'],
                                     'n': entry}, color))
    elif command == 'close':
        criteria = {field: value for field, value in ((f, getattr(args, f.replace('-', '_'), None))
                                                      for f in CLOSE_FIELDS) if value is not None}
        if args.id is None and not criteria:
            raise SystemExit('notifyd ctl close: give an ID or fields to match (--app, --summary, ...)')
        if args.id is not None and criteria:
            raise SystemExit('notifyd ctl close: give an ID or fields to match, not both')
        if criteria:
            print(control_call('CloseMatching', GLib.Variant('(s)', (json.dumps(criteria),)), '(u)'))
        elif not control_call('Close', GLib.Variant('(u)', (args.id,)), '(b)'):
            raise SystemExit(f'no notification {args.id}')
    elif command == 'list':
        entries = json.loads(control_call('List'))
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
        elif args.ids:
            for entry in entries:
                print(f'{entry["id"]}\t{entry["app"]}: {one_line(entry["summary"], 60)}')
        else:
            color = use_color('auto', sys.stdout)
            for entry in entries:
                print(format_pretty({'event': entry['state'], 'time': entry['received'], 'n': entry}, color))
    elif command == 'center':
        visible = control_call('Center', GLib.Variant('(s)', (args.what,)), '(b)')
        print('shown' if visible else 'hidden')
    elif command == 'forget':
        if args.id is None and not args.all:
            raise SystemExit('notifyd ctl forget: give an ID or --all')
        removed = control_call('Forget', GLib.Variant('(u)', (0 if args.all else args.id,)), '(u)')
        print(f'forgot {removed}')
    elif command == 'pop':
        nid = control_call('Pop', GLib.Variant('(u)', (args.id or 0,)), '(u)')
        if not nid:
            raise SystemExit('nothing in history' if not args.id else f'no notification {args.id} in history')
        print(nid)
    elif command == 'mode':
        seconds = duration_seconds(args.for_, 'mode --for')
        if seconds and not args.name:
            raise SystemExit('notifyd ctl mode --for: give the mode to switch to')
        if seconds:
            print(control_call('SetModeFor', GLib.Variant('(su)', (args.name, seconds))))
        else:
            print(control_call('SetMode', GLib.Variant('(s)', (args.name or '',))))
    elif command == 'action':
        if args.id is None:                       # the newest on screen with a default (or single) action
            if not control_call('InvokeDefault', None, '(u)'):
                raise SystemExit('no notification on screen with a default action')
        elif not control_call('Invoke', GLib.Variant('(us)', (args.id, args.key or '')), '(b)'):
            raise SystemExit(f'no notification {args.id} with that action')
    elif command == 'reply':
        if not control_call('Reply', GLib.Variant('(us)', (args.id, args.text)), '(b)'):
            raise SystemExit(f'no notification {args.id} that takes a reply')
    elif command == 'close-all':
        print(control_call('CloseAll', None, '(u)'))
    elif command in ('pause', 'resume', 'toggle'):
        on = command == 'pause'
        if command == 'toggle':
            on = not json.loads(control_call('Status'))['paused']
        seconds = duration_seconds(getattr(args, 'for_', None), f'{command} --for')
        if on and seconds:
            paused = control_call('PauseFor', GLib.Variant('(u)', (seconds,)), '(b)')
        else:
            paused = control_call('Pause', GLib.Variant('(b)', (on,)), '(b)')
        print('paused' if paused else 'resumed')
    elif command == 'reload':
        result = control_call('Reload')
        print(result)
        return 1 if result.startswith('error') else 0
    elif command == 'preset':
        result = control_call('SetPreset', GLib.Variant('(s)', (args.name,)))
        print(result)
        return 1 if result.startswith('error') else 0
    elif command == 'preview':
        control_call('Preview', GLib.Variant('(s)', (args.preset or '',)), '(u)')
    elif command == 'watch':
        return watch(args.json)
    elif command == 'bar':
        return bar(args)
    return 0


def pick_history(entries):
    """fzf over the history, newest first; enter shows the chosen one again."""
    from notifyd.log import one_line
    if not shutil.which('fzf'):
        raise SystemExit('notifyd ctl history --pick needs fzf')
    lines = []
    for entry in reversed(entries):
        stamp = time.strftime('%H:%M', time.localtime(entry.get('received', 0)))
        text = one_line(entry.get('summary', '') + (' | ' + entry['body'] if entry.get('body') else ''), 90)
        lines.append(f'{entry["id"]}\t{stamp}  {entry.get("app", ""):<14.14}  {text}')
    result = subprocess.run(['fzf', '--delimiter', '\t', '--with-nth', '2', '--prompt', 'history> ',
                             '--header', 'enter shows it again'], input='\n'.join(lines), text=True,
                            stdout=subprocess.PIPE, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return result.returncode
    return ctl(argparse.Namespace(command='pop', id=int(result.stdout.split('\t', 1)[0])))


BAR_EVENTS = {'paused', 'resumed', 'locked', 'unlocked', 'mode', 'queued', 'shown', 'reload', 'expired', 'missed',
              'dismissed', 'closed'}
BAR_FIELDS = ('paused', 'mode', 'held', 'shown', 'missed')


def bar_line(status, fmt, idle_text):
    """The status bar text: FMT with {mode} {held} {shown} {paused} {missed}, or IDLE_TEXT when nothing is
    special (missed and shown ones count only when FMT shows them)."""
    missed = status.get('missed') if '{missed}' in fmt else 0
    shown = status.get('displayed') if '{shown}' in fmt else 0
    if (not status.get('paused') and status.get('mode') == 'normal' and not status.get('held_back')
            and not missed and not shown):
        return idle_text
    text = fmt.format(mode='' if status.get('mode') == 'normal' else status.get('mode'),
                      held=status.get('held_back') or '', shown=status.get('displayed', 0),
                      paused='dnd' if status.get('paused') else '', missed=status.get('missed') or '')
    return ' '.join(text.split())


def bar(args):
    """One line for a status bar; with --follow a new line whenever it changes (polybar tail = true)."""
    Gio, GLib = gio()
    from notifyd.server import CONTROL_IFACE, CONTROL_NAME, OBJECT_PATH
    try:
        args.format.format(**dict.fromkeys(BAR_FIELDS, ''))
    except (KeyError, IndexError, ValueError) as exc:
        print(f'notifyd ctl bar: --format {args.format!r}: {exc!r} is not one of '
              f'{" ".join("{" + f + "}" for f in BAR_FIELDS)}', file=sys.stderr)
        return 2

    def current():
        try:
            return bar_line(json.loads(control_call('Status')), args.format, args.idle)
        except SystemExit:
            return args.offline
    last = current()
    print(last, flush=True)
    if not args.follow:
        return 0
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    loop = GLib.MainLoop()

    def update(*_args):
        nonlocal last
        line = current()
        if line != last:
            last = line
            try:
                print(line, flush=True)
            except BrokenPipeError:
                loop.quit()

    def on_event(_conn, _sender, _path, _iface, _member, params):
        if json.loads(params.unpack()[0]).get('event') in BAR_EVENTS:
            update()
    connection.signal_subscribe(CONTROL_NAME, CONTROL_IFACE, 'Event', OBJECT_PATH, None,
                                Gio.DBusSignalFlags.NONE, on_event)
    # the daemon starting or stopping
    connection.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
                                '/org/freedesktop/DBus', CONTROL_NAME, Gio.DBusSignalFlags.NONE, update)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    return 0


def watch(as_json):
    Gio, GLib = gio()
    from notifyd.log import format_pretty, use_color
    from notifyd.server import CONTROL_IFACE, CONTROL_NAME, OBJECT_PATH
    control_call('Status')   # fails clearly when notifyd is not running
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    color = use_color('auto', sys.stdout)

    def on_event(_conn, _sender, _path, _iface, _member, params):
        text = params.unpack()[0]
        try:
            print(text if as_json else format_pretty(json.loads(text), color), flush=True)
        except BrokenPipeError:
            loop.quit()
    connection.signal_subscribe(CONTROL_NAME, CONTROL_IFACE, 'Event', OBJECT_PATH, None,
                                Gio.DBusSignalFlags.NONE, on_event)
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    return 0


# -- presets ---------------------------------------------------------------------

def presets(args):
    from notifyd.config import load_preset, preset_names
    names = preset_names()
    if args.names:
        print('\n'.join(names))
        return 0
    if not args.pick:
        width = max(map(len, names))
        for name in names:
            print(f'{name:<{width}}  {load_preset(name)["description"]}')
        return 0
    if not shutil.which('fzf'):
        raise SystemExit('notifyd presets --pick needs fzf')
    me = shlex.quote(os.path.realpath(sys.argv[0])) if sys.argv[0].endswith('notifyd') else 'notifyd'
    lines = '\n'.join(f'{name}\t{load_preset(name)["description"]}' for name in names)
    result = subprocess.run(
        ['fzf', '--delimiter', '\t', '--with-nth', '1,2', '--prompt', 'preset> ',
         '--header', 'moving previews the preset; enter makes it current (until the next reload)',
         '--bind', f'focus:execute-silent({me} ctl preview {{1}})',
         '--preview-window', 'hidden'],
        input=lines, text=True, stdout=subprocess.PIPE)
    if result.returncode != 0 or not result.stdout.strip():
        return result.returncode
    name = result.stdout.split('\t', 1)[0].strip()
    return ctl(argparse.Namespace(command='preset', name=name))


# -- icons -------------------------------------------------------------------------

def copy_command():
    """A shell command that copies stdin to the clipboard, or None."""
    for tool, command in (('wl-copy', 'wl-copy'), ('xclip', 'xclip -selection clipboard'),
                          ('xsel', 'xsel --clipboard --input')):
        if shutil.which(tool):
            return command
    return None


def icon_preview(theme, theme_name, name, size):
    """What `icons --preview NAME` prints: where the icon comes from, then the icon itself (chafa), sized
    to fzf's preview pane."""
    from gi.repository import GLib, Gtk
    info = theme.lookup_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE | Gtk.IconLookupFlags.GENERIC_FALLBACK)
    print(f'{name}  ({theme_name})')
    if info is None:
        print('not in this theme')
        return 1
    print(info.get_filename() or '(built in)')
    sizes = sorted(s for s in theme.get_icon_sizes(name) if s > 0)
    scalable = -1 in theme.get_icon_sizes(name)
    print('sizes: ' + ', '.join(map(str, sizes)) + (' + scalable' if scalable else '') if sizes or scalable else '')
    if not shutil.which('chafa'):
        print('(install chafa to see the icon here)')
        return 0
    cache = Path(GLib.get_user_cache_dir()) / 'notifyd'
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / 'icon-preview.png'
    try:
        info.load_icon().savev(str(target), 'png', [], [])
    except GLib.Error as exc:
        print(f'cannot draw it: {exc.message}')
        return 1
    columns = int(os.environ.get('FZF_PREVIEW_COLUMNS') or 40)
    lines = max(int(os.environ.get('FZF_PREVIEW_LINES') or 20) - 4, 4)
    sys.stdout.flush()
    # on the background the theme is drawn for: most themes' glyphs are dark (breeze), *-dark ones light
    background = '#2b2b2b' if 'dark' in theme_name.lower() else '#e8e8e8'
    subprocess.run(['chafa', '--size', f'{columns}x{lines}', '--bg', background, str(target)], check=False)
    return 0


def icons_command(args):
    import fnmatch

    from notifyd import icons
    if args.theme and not icons.theme_installed(args.theme):
        raise SystemExit(f'notifyd icons: no icon theme {args.theme!r} installed')
    theme, theme_name = icons.icon_theme(args.theme)
    if args.preview:
        return icon_preview(theme, theme_name, args.preview, args.size)
    if args.contexts:
        for context in sorted(theme.list_contexts()):
            print(f'{context:<16} {len(theme.list_icons(context))}')
        return 0
    rows = icons.list_icons(theme, args.context)
    if args.pattern:
        pattern = args.pattern.lower()
        if any(c in pattern for c in '*?['):
            rows = [(n, c) for n, c in rows if fnmatch.fnmatch(n.lower(), pattern)]
        else:
            rows = [(n, c) for n, c in rows if pattern in n.lower()]
    if not rows:
        print(f'notifyd icons: nothing matches in {theme_name!r}', file=sys.stderr)
        return 1
    if args.names:
        print('\n'.join(n for n, _c in rows))
        return 0
    if not args.pick:
        width = max(len(n) for n, _c in rows)
        for name, context in rows:
            print(f'{name:<{width}}  {context}')
        print(f'{len(rows)} icon(s) in {theme_name!r}', file=sys.stderr)
        return 0
    if not shutil.which('fzf'):
        raise SystemExit('notifyd icons --pick needs fzf')
    me = os.path.realpath(sys.argv[0]) if sys.argv[0].endswith('notifyd') else shutil.which('notifyd') or 'notifyd'
    sender = os.path.join(os.path.dirname(me), 'notifyd-send')
    sender = sender if os.path.exists(sender) else 'notifyd-send'
    theme_option = f' --theme {shlex.quote(args.theme)}' if args.theme else ''
    binds = ['--bind', f'focus:execute-silent({shlex.quote(sender)} -e -a icons --tag notifyd-icons -t 5000 '
             f'-i {{1}} --image {{1}} {{1}} {{2}})']
    copier = copy_command()
    header = 'moving shows it in a popup too; enter prints the name'
    if copier:
        binds += ['--bind', f'ctrl-y:execute-silent(printf %s {{1}} | {copier})']
        header += '; ctrl-y copies it'
    result = subprocess.run(
        ['fzf', '--delimiter', '\t', '--with-nth', '1,2', '--prompt', f'icon ({theme_name})> ', '--header', header,
         '--preview', f'{shlex.quote(me)} icons{theme_option} --preview {{1}}', '--preview-window', 'right,50%',
         *binds],
        input='\n'.join(f'{n}\t{c}' for n, c in rows), text=True, stdout=subprocess.PIPE)
    if result.returncode == 0 and result.stdout.strip():
        print(result.stdout.split('\t', 1)[0].strip())
    return result.returncode


# -- import, check, rules -----------------------------------------------------------

def import_command(args):
    from notifyd.config import ConfigError, default_path
    from notifyd.importer import dump_config, import_deadd, import_dunst
    home = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')

    def read(path):
        try:
            return path.read_text()
        except OSError as exc:
            raise SystemExit(f'notifyd import: {path}: {exc.strerror}') from exc
    if args.source == 'deadd':
        yml = Path(args.yml) if args.yml else home / 'deadd' / 'deadd.yml'
        css = Path(args.css) if args.css else yml.with_suffix('.css')
        try:
            config, report = import_deadd(read(yml), css.read_text() if css.is_file() else None)
        except ConfigError as exc:
            raise SystemExit(f'notifyd import: {yml}: {exc}') from exc
        text = dump_config(config, 'deadd-notification-center')
    else:
        config, report = import_dunst(read(Path(args.rc) if args.rc else home / 'dunst' / 'dunstrc'))
        text = dump_config(config, 'dunst')
    for line in report:
        print(f'note: {line}', file=sys.stderr)
    if not args.output:
        sys.stdout.write(text)
        return 0
    out = default_path() if args.output == 'config' else Path(args.output).expanduser()
    if out.exists() and not args.force:
        raise SystemExit(f'notifyd import: {out} exists (--force replaces it)')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f'wrote {out}', file=sys.stderr)
    return 0


def sender_timeout(text):
    """`rules --timeout`: a duration (8s, 1500ms, 8000, never) as the ms a sender would ask for."""
    from notifyd.config import ConfigError, parse_duration
    try:
        return parse_duration(text, '--timeout')
    except ConfigError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def load_or_exit(path):
    """The config at PATH (the default one when None); a config error, or a PATH given that does not exist,
    prints it and exits 2."""
    from notifyd.config import ConfigError, load_config
    if path is not None and not Path(path).expanduser().exists():
        print(f'notifyd: {path}: no such file', file=sys.stderr)
        raise SystemExit(2)
    try:
        return load_config(path)
    except ConfigError as exc:
        print(f'notifyd: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc


def picture_warnings(config):
    """Warnings for icon.name and image.path settings (top level and in rules) that would not draw."""
    from notifyd.icons import icon_theme, why_missing
    sources = [('', config.settings)] + [(f'rule {rule.name!r}: ', rule.modify) for rule in config.rules]
    theme = None
    found = []
    for where, settings in sources:
        for key, part, what in (('icon', 'name', 'icon.name'), ('image', 'path', 'image.path')):
            value = (settings.get(key) or {}).get(part) if isinstance(settings.get(key), dict) else None
            if not value:
                continue
            if theme is None:
                theme = icon_theme()
            reason = why_missing(str(value), *theme)
            if reason:
                found.append(f'{where}{what} {value!r}: {reason}')
    return found


def check(args):
    config = load_or_exit(args.config)
    warnings = list(config.warnings) + picture_warnings(config)
    for warning in warnings:
        print(f'warning: {warning}')
    print(f'{config.path}: ok, preset {config.settings["preset"]}, {len(config.rules)} rule(s)')
    return 1 if warnings else 0


def rules_command(args):
    from notifyd.config import format_duration
    from notifyd.model import URGENCY_NAMES, Notification
    from notifyd.rules import resolve
    config = load_or_exit(args.config)
    hints = {'urgency': URGENCY_NAMES.index(args.urgency)}
    for text in args.hint:
        name, _, value = text.partition('=')
        hints[name] = value
    n = Notification(app=args.app, summary=args.summary, body=args.body, hints=hints,
                     requested_timeout=args.timeout)
    resolve(config, n)
    print(f'matched: {", ".join(n.rules) or "no rules"}')
    print(f'timeout: {format_duration(n.timeout)}')
    print(f'app: {n.app}  summary: {n.summary}  urgency: {n.urgency_name}')
    shown = {k: n.settings[k] for k in ('preset', 'width', 'icon', 'image', 'hide', 'class', 'skip', 'markup', 'style')
             if n.settings.get(k) not in (None, [], {})}
    print(json.dumps(shown, indent=2, ensure_ascii=False))
    return 0


# -- entry ----------------------------------------------------------------------

DESCRIPTION = """Desktop notification daemon for X11: deadd's look, dunst's rules, preset styles,
a notify-send replacement and a colorful event log."""

EPILOG = """examples:
  notifyd daemon --replace              take over from the running notification daemon
  notifyd send 'Hello' '<b>world</b>'   send a notification
  notifyd ctl watch                     follow the daemon's event log
  notifyd presets --pick                pick a preset with a live preview
  notifyd help ctl                      help for a command

config: ~/.config/notifyd/config.yaml (notifyd check validates it); see notifyd(1)."""


class Formatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog):
        super().__init__(prog, max_help_position=32)


def add_command(sub, name, help_text, description=None, epilog=None, **kwargs):
    return sub.add_parser(name, help=help_text, description=description or help_text[0].upper() + help_text[1:] + '.',
                          epilog=epilog, formatter_class=Formatter, **kwargs)


def config_option(parser):
    option = parser.add_argument('-c', '--config', metavar='PATH',
                                 help='config file (default: ~/.config/notifyd/config.yaml)')
    option.complete = 'files'


def build_parser():
    from notifyd.send import build_parser as send_parser
    parser = argparse.ArgumentParser(prog='notifyd', description=DESCRIPTION, epilog=EPILOG, formatter_class=Formatter)
    parser.add_argument('-V', '--version', action='version', version=f'notifyd {__version__}')
    sub = parser.add_subparsers(dest='cmd', required=True, metavar='COMMAND', title='commands')

    daemon = add_command(sub, 'daemon', 'run the notification daemon',
                         epilog='SIGHUP or a change to the config file reloads it; SIGINT/SIGTERM stop the daemon.')
    config_option(daemon)
    daemon.add_argument('--renderer', choices=('gtk', 'none'), default='gtk',
                        help='gtk draws popups; none only logs (default: gtk)')
    daemon.add_argument('--replace', action='store_true', help='take the bus name from the running daemon')
    daemon.add_argument('--log-format', choices=('pretty', 'json'), help='event log format (default: pretty)')
    daemon.add_argument('--color', choices=('auto', 'always', 'never'), help='color the log (default: auto)')
    daemon.add_argument('-v', '--verbose', action='store_const', const='debug', dest='level', help='log debug events')
    daemon.add_argument('-q', '--quiet', action='store_const', const='warn', dest='level',
                        help='log only warnings and errors')

    send = send_parser()
    sub.add_parser('send', help='send a notification (notify-send flags)', description=send.description,
                   epilog=send.epilog, parents=[send], add_help=False, formatter_class=Formatter)

    control = add_command(sub, 'ctl', 'control the running daemon')
    commands = control.add_subparsers(dest='command', required=True, metavar='COMMAND', title='commands')
    add_command(commands, 'status', 'show paused state, counts, preset and config')
    history = add_command(commands, 'history', 'show closed notifications, oldest first')
    history.add_argument('-n', type=int, default=20, metavar='N', help='the last N (0: all; default: 20)')
    history.add_argument('--json', action='store_true', help='as JSON')
    history.add_argument('--pick', action='store_true', help='choose one in fzf and show it again')
    listing = add_command(commands, 'list', 'show open notifications: on screen, waiting, held back')
    listing.add_argument('--json', action='store_true', help='as JSON')
    listing.add_argument('--ids', action='store_true', help='id, a tab, app and summary (for scripts)')
    forget = add_command(commands, 'forget', 'remove a notification from history, or all of it')
    forget.add_argument('id', type=int, nargs='?', help='its id in notifyd ctl history')
    forget.add_argument('--all', action='store_true', help='clear the whole history')
    center = add_command(commands, 'center', 'show or hide the notification center (SIGUSR1 toggles it too)')
    center.add_argument('what', nargs='?', choices=('toggle', 'show', 'hide'), default='toggle',
                        help='default: toggle')
    pop = add_command(commands, 'pop', 'show a closed notification again (the last one, or ID)')
    pop.add_argument('id', type=int, nargs='?', help='its id in notifyd ctl history')
    close = add_command(commands, 'close', 'close a notification, or every open one whose fields match')
    close.add_argument('id', type=int, nargs='?', help='notification id (notifyd ctl list shows them)').complete = 'ids'
    for field in CLOSE_FIELDS:
        close.add_argument(f'--{field}', metavar='TEXT', help=f'its {field} is TEXT, or matches /regex/')
    action = add_command(commands, 'action', 'invoke an action of a notification; without an id, the default '
                         'action of the newest one on screen that has one (for a key binding)')
    action.add_argument('id', type=int, nargs='?',
                        help='notification id (default: the newest on screen with a default or single action)'
                        ).complete = 'ids'
    action.add_argument('key', nargs='?', help='action key (default: "default", else the first action)')
    reply = add_command(commands, 'reply', 'answer a notification that takes a reply (inline-reply)')
    reply.add_argument('id', type=int, help='notification id').complete = 'ids'
    reply.add_argument('text', help='the reply')
    add_command(commands, 'close-all', 'close every notification on screen or waiting')
    pause_parser = add_command(commands, 'pause', 'hold back all but critical notifications (do not disturb)')
    pause_parser.add_argument('--for', dest='for_', metavar='DURATION',
                              help='resume on its own after DURATION (30m, 1h, 90s)')
    add_command(commands, 'resume', 'show what was held back, and new ones again')
    toggle_parser = add_command(commands, 'toggle', 'pause or resume')
    toggle_parser.add_argument('--for', dest='for_', metavar='DURATION',
                               help='when it pauses: resume on its own after DURATION')
    add_command(commands, 'reload', 'reload the config file')
    mode = add_command(commands, 'mode', 'show or set the mode rules can match on (match: {mode: NAME})')
    mode.add_argument('name', nargs='?', help='e.g. dnd, presentation, normal (the default)')
    mode.add_argument('--for', dest='for_', metavar='DURATION',
                      help='switch back to the mode before it after DURATION (30m, 1h)')
    preset = add_command(commands, 'preset', 'switch the preset until the next reload')
    preset.add_argument('name', help='preset name (notifyd presets lists them)').complete = 'presets'
    preview = add_command(commands, 'preview', 'show sample notifications in a preset')
    preview.add_argument('preset', nargs='?', help='preset name (default: the current one)').complete = 'presets'
    bar_parser = add_command(commands, 'bar', 'a status line for polybar and the like',
                             epilog='polybar:\n'
                                    '  [module/notifyd]\n'
                                    '  type = custom/script\n'
                                    '  exec = notifyd ctl bar --follow\n'
                                    '  tail = true\n'
                                    '  click-left = notifyd ctl toggle\n'
                                    '  click-right = notifyd ctl center')
    bar_parser.add_argument('--follow', action='store_true', help='print a new line whenever it changes')
    bar_parser.add_argument('--format', default='{paused} {mode} {held}', metavar='FMT',
                            help='with {paused} {mode} {held} {shown} {missed} (missed: expired unclicked since '
                                 'the center was last opened; default: "{paused} {mode} {held}")')
    bar_parser.add_argument('--idle', default='', metavar='TEXT',
                            help='shown when not paused, mode normal and nothing held back (default: empty)')
    bar_parser.add_argument('--offline', default='', metavar='TEXT', help='shown when notifyd is not running')
    watch_parser = add_command(commands, 'watch', 'follow the event log of the running daemon')
    watch_parser.add_argument('--json', action='store_true', help='one JSON object per line')

    presets_parser = add_command(sub, 'presets', 'list presets, or pick one with a live preview')
    presets_parser.add_argument('--pick', action='store_true', help='choose in fzf; moving previews, enter switches')
    presets_parser.add_argument('--names', action='store_true', help='names only, one per line')

    icons_parser = add_command(
        sub, 'icons', "list the icon theme's icons, or pick one with a preview",
        epilog='examples:\n'
               '  notifyd icons volume              names containing "volume", with their context\n'
               "  notifyd icons 'battery-*' --names  a glob, names only\n"
               '  notifyd icons --context Status    one context (--contexts lists them)\n'
               '  notifyd icons --pick              fzf: the icon drawn in the preview (chafa) and shown\n'
               '                                    in a popup as you move; enter prints the name\n'
               '  notifyd icons --theme Papirus-Dark --pick   another installed theme\n\n'
               'An icon name works as -i (app icon) and --image in notifyd-send, and as icon.name in a rule.')
    icons_parser.add_argument('pattern', nargs='?', help='a part of the name, or a glob (*, ?)')
    icons_parser.add_argument('--context', metavar='NAME', help='only this context (Status, Devices, ...)')
    icons_parser.add_argument('--contexts', action='store_true', help='list the contexts with how many icons each')
    icons_parser.add_argument('--theme', metavar='NAME', help='another installed theme (default: the current one)')
    icons_parser.add_argument('--names', action='store_true', help='names only, one per line')
    icons_parser.add_argument('--pick', action='store_true', help='browse in fzf with a preview; enter prints the name')
    icons_parser.add_argument('--preview', metavar='NAME', help="print one icon's file and sizes and draw it (chafa)")
    icons_parser.add_argument('--size', type=int, default=64, metavar='PX', help='the size --preview draws (64)')

    importer = add_command(sub, 'import', "convert another daemon's config",
                           epilog='Prints the config unless -o is given; notes on what has no equivalent go to stderr.')
    importer.add_argument('source', choices=('deadd', 'dunst'), help='the daemon to import from')
    importer.add_argument('--yml', metavar='PATH',
                          help='deadd.yml (default: ~/.config/deadd/deadd.yml)').complete = 'files'
    importer.add_argument('--css', metavar='PATH', help='deadd.css (default: next to the yml)').complete = 'files'
    importer.add_argument('--rc', metavar='PATH', help='dunstrc (default: ~/.config/dunst/dunstrc)').complete = 'files'
    importer.add_argument('-o', '--output', metavar='PATH',
                          help='file to write, or "config" for ~/.config/notifyd/config.yaml').complete = 'files'
    importer.add_argument('--force', action='store_true', help='overwrite an existing file')

    checker = add_command(sub, 'check', 'validate the config', epilog='Exits 1 when there are warnings, 2 on errors.')
    config_option(checker)

    rules = add_command(sub, 'rules', 'show which rules match a notification, and the result')
    config_option(rules)
    rules.add_argument('--app', default='', help='app name')
    rules.add_argument('--summary', default='', help='summary (title)')
    rules.add_argument('--body', default='', help='body')
    rules.add_argument('--urgency', choices=('low', 'normal', 'critical'), default='normal', help='urgency')
    rules.add_argument('--timeout', type=sender_timeout, default=-1, metavar='DURATION',
                       help='the timeout the sender asks for (8s, 1500ms, never; default: none)')
    rules.add_argument('--hint', action='append', default=[], metavar='NAME=VALUE', help='a hint (repeatable)')

    completion = add_command(sub, 'completion', 'print a shell completion script',
                             epilog='install (or `just completions`):\n'
                                    '  zsh:  notifyd completion zsh > ~/.local/share/zsh/site-functions/_notifyd\n'
                                    '        (any directory in $fpath)\n'
                                    '  bash: notifyd completion bash \\\n'
                                    '          > ~/.local/share/bash-completion/completions/notifyd\n'
                                    '  fish: notifyd completion fish > ~/.config/fish/completions/notifyd.fish')
    completion.add_argument('shell', choices=('zsh', 'bash', 'fish'), help='the shell')

    helper = add_command(sub, 'help', 'show help for notifyd or one of its commands')
    helper.add_argument('topic', nargs='*', metavar='COMMAND', help='e.g. ctl, or ctl history').complete = 'commands'
    add_command(sub, 'version', 'print the version')
    return parser


def subparser(parser, path):
    """The parser for command PATH (['ctl', 'history']), or None."""
    for name in path:
        actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        if not actions or name not in actions[0].choices:
            return None
        parser = actions[0].choices[name]
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['send']:
        from notifyd.send import main as send_main
        return send_main(argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == 'daemon':
        from notifyd.daemon import Daemon
        return Daemon(args.config, args.renderer, args.replace, args.log_format, args.color, args.level).run()
    if args.cmd == 'ctl':
        return ctl(args)
    if args.cmd == 'presets':
        return presets(args)
    if args.cmd == 'icons':
        return icons_command(args)
    if args.cmd == 'import':
        return import_command(args)
    if args.cmd == 'check':
        return check(args)
    if args.cmd == 'rules':
        return rules_command(args)
    if args.cmd == 'completion':
        from notifyd.completion import script
        sys.stdout.write(script(args.shell, parser))
        return 0
    if args.cmd == 'version':
        print(f'notifyd {__version__}')
        return 0
    if args.cmd == 'help':
        target = subparser(parser, args.topic)
        if target is None:
            print(f'notifyd help: no command {" ".join(args.topic)!r}', file=sys.stderr)
            return 2
        target.print_help()
        return 0
    return 2
