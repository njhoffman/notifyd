"""notifyd's command line.

    notifyd daemon [-c CONFIG] [--renderer gtk|none] [--replace] [--log-format pretty|json] [--color ...]
    notifyd send SUMMARY [BODY] [notify-send flags]
    notifyd ctl status | history [-n N] [--json] | close ID | action ID [KEY] | close-all | pause | resume | toggle
              | reload | preset NAME | preview [PRESET] | watch [--json]
    notifyd presets [--pick]
    notifyd import deadd [--yml PATH] [--css PATH] [-o OUT] [--force]
    notifyd check [-c CONFIG]
    notifyd rules [-c CONFIG] [--app A] [--summary S] [--body B] [--urgency U] [--hint NAME=VALUE]
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from notifyd import __version__


def gio():
    from gi.repository import Gio, GLib
    return Gio, GLib


# -- ctl -----------------------------------------------------------------------

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
    from notifyd.log import format_pretty, use_color
    command = args.command
    if command == 'status':
        status = json.loads(control_call('Status'))
        width = max(map(len, status))
        for key, value in status.items():
            print(f'{key:<{width}}  {value}')
    elif command == 'history':
        entries = json.loads(control_call('History', GLib.Variant('(u)', (args.n,))))
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
        else:
            color = use_color('auto', sys.stdout)
            for entry in entries:
                print(format_pretty({'event': entry.get('closed') or 'closed', 'time': entry['received'],
                                     'n': entry}, color))
    elif command == 'close':
        if not control_call('Close', GLib.Variant('(u)', (args.id,)), '(b)'):
            raise SystemExit(f'no notification {args.id}')
    elif command == 'action':
        if not control_call('Invoke', GLib.Variant('(us)', (args.id, args.key or '')), '(b)'):
            raise SystemExit(f'no notification {args.id} with that action')
    elif command == 'close-all':
        print(control_call('CloseAll', None, '(u)'))
    elif command in ('pause', 'resume', 'toggle'):
        on = command == 'pause'
        if command == 'toggle':
            on = not json.loads(control_call('Status'))['paused']
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


# -- import, check, rules -----------------------------------------------------------

def import_command(args):
    from notifyd.config import default_path
    from notifyd.importer import dump_config, import_deadd
    home = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    yml = Path(args.yml) if args.yml else home / 'deadd' / 'deadd.yml'
    css = Path(args.css) if args.css else yml.with_suffix('.css')
    try:
        yml_text = yml.read_text()
    except OSError as exc:
        raise SystemExit(f'notifyd import: {yml}: {exc.strerror}') from exc
    css_text = css.read_text() if css.is_file() else None
    config, report = import_deadd(yml_text, css_text)
    text = dump_config(config)
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


def load_or_exit(path):
    from notifyd.config import ConfigError, load_config
    try:
        return load_config(path)
    except ConfigError as exc:
        raise SystemExit(f'notifyd: {exc}') from exc


def check(args):
    config = load_or_exit(args.config)
    for warning in config.warnings:
        print(f'warning: {warning}')
    print(f'{config.path}: ok, preset {config.settings["preset"]}, {len(config.rules)} rule(s)')
    return 1 if config.warnings else 0


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

def build_parser():
    parser = argparse.ArgumentParser(prog='notifyd', description='Desktop notification daemon.')
    parser.add_argument('--version', action='version', version=f'notifyd {__version__}')
    sub = parser.add_subparsers(dest='cmd', required=True)

    daemon = sub.add_parser('daemon', help='run the notification daemon')
    daemon.add_argument('-c', '--config')
    daemon.add_argument('--renderer', choices=('gtk', 'none'), default='gtk',
                        help='none: log only, show nothing')
    daemon.add_argument('--replace', action='store_true', help='take the bus name from the running daemon')
    daemon.add_argument('--log-format', choices=('pretty', 'json'))
    daemon.add_argument('--color', choices=('auto', 'always', 'never'))
    daemon.add_argument('-v', '--verbose', action='store_const', const='debug', dest='level')
    daemon.add_argument('-q', '--quiet', action='store_const', const='warn', dest='level')

    sub.add_parser('send', help='send a notification (notify-send flags; notifyd send --help)', add_help=False)

    control = sub.add_parser('ctl', help='control the running daemon')
    commands = control.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    history = commands.add_parser('history')
    history.add_argument('-n', type=int, default=20, help='last N (0: all)')
    history.add_argument('--json', action='store_true')
    close = commands.add_parser('close')
    close.add_argument('id', type=int)
    action = commands.add_parser('action', help='invoke an action (default: the default or first one)')
    action.add_argument('id', type=int)
    action.add_argument('key', nargs='?')
    for name in ('close-all', 'pause', 'resume', 'toggle', 'reload'):
        commands.add_parser(name)
    preset = commands.add_parser('preset')
    preset.add_argument('name')
    preview = commands.add_parser('preview')
    preview.add_argument('preset', nargs='?')
    watch_parser = commands.add_parser('watch')
    watch_parser.add_argument('--json', action='store_true')

    presets_parser = sub.add_parser('presets', help='list presets, or pick one with a live preview')
    presets_parser.add_argument('--pick', action='store_true')

    importer = sub.add_parser('import', help='convert another daemon\'s config')
    importer.add_argument('source', choices=('deadd',))
    importer.add_argument('--yml')
    importer.add_argument('--css')
    importer.add_argument('-o', '--output', help='file to write, or "config" for ~/.config/notifyd/config.yaml')
    importer.add_argument('--force', action='store_true')

    checker = sub.add_parser('check', help='validate the config')
    checker.add_argument('-c', '--config')

    rules = sub.add_parser('rules', help='show which rules match a notification')
    rules.add_argument('-c', '--config')
    rules.add_argument('--app', default='')
    rules.add_argument('--summary', default='')
    rules.add_argument('--body', default='')
    rules.add_argument('--urgency', choices=('low', 'normal', 'critical'), default='normal')
    rules.add_argument('--timeout', type=int, default=-1, help='the timeout the sender asks for, ms')
    rules.add_argument('--hint', action='append', default=[], metavar='NAME=VALUE')
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['send']:
        from notifyd.send import main as send_main
        return send_main(argv[1:])
    args = build_parser().parse_args(argv)
    if args.cmd == 'daemon':
        from notifyd.daemon import Daemon
        return Daemon(args.config, args.renderer, args.replace, args.log_format, args.color, args.level).run()
    if args.cmd == 'ctl':
        return ctl(args)
    if args.cmd == 'presets':
        return presets(args)
    if args.cmd == 'import':
        return import_command(args)
    if args.cmd == 'check':
        return check(args)
    if args.cmd == 'rules':
        return rules_command(args)
    return 2
