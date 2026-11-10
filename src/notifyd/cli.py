"""notifyd's command line.

    notifyd daemon [-c CONFIG] [--renderer gtk|none] [--replace] [--log-format pretty|json] [--color ...]
    notifyd send SUMMARY [BODY] [notify-send flags]
    notifyd ctl status | list | history [-n N] [--json] | close ID | action ID [KEY] | close-all
              | pause | resume | toggle | mode [NAME] | reload | preset NAME | preview [PRESET] | watch [--json]
    notifyd presets [--pick | --names]
    notifyd import deadd [--yml PATH] [--css PATH] [-o OUT] [--force]
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
    from notifyd.log import format_pretty, one_line, use_color
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
    elif command == 'mode':
        print(control_call('SetMode', GLib.Variant('(s)', (args.name or '',))))
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
    listing = add_command(commands, 'list', 'show open notifications: on screen, waiting, held back')
    listing.add_argument('--json', action='store_true', help='as JSON')
    listing.add_argument('--ids', action='store_true', help='id, a tab, app and summary (for scripts)')
    close = add_command(commands, 'close', 'close a notification')
    close.add_argument('id', type=int, help='notification id (notifyd ctl list shows them)').complete = 'ids'
    action = add_command(commands, 'action', 'invoke an action of a notification')
    action.add_argument('id', type=int, help='notification id').complete = 'ids'
    action.add_argument('key', nargs='?', help='action key (default: "default", else the first action)')
    add_command(commands, 'close-all', 'close every notification on screen or waiting')
    add_command(commands, 'pause', 'hold back all but critical notifications')
    add_command(commands, 'resume', 'show what was held back, and new ones again')
    add_command(commands, 'toggle', 'pause or resume')
    add_command(commands, 'reload', 'reload the config file')
    mode = add_command(commands, 'mode', 'show or set the mode rules can match on (match: {mode: NAME})')
    mode.add_argument('name', nargs='?', help='e.g. dnd, presentation, normal (the default)')
    preset = add_command(commands, 'preset', 'switch the preset until the next reload')
    preset.add_argument('name', help='preset name (notifyd presets lists them)').complete = 'presets'
    preview = add_command(commands, 'preview', 'show sample notifications in a preset')
    preview.add_argument('preset', nargs='?', help='preset name (default: the current one)').complete = 'presets'
    watch_parser = add_command(commands, 'watch', 'follow the event log of the running daemon')
    watch_parser.add_argument('--json', action='store_true', help='one JSON object per line')

    presets_parser = add_command(sub, 'presets', 'list presets, or pick one with a live preview')
    presets_parser.add_argument('--pick', action='store_true', help='choose in fzf; moving previews, enter switches')
    presets_parser.add_argument('--names', action='store_true', help='names only, one per line')

    importer = add_command(sub, 'import', "convert another daemon's config",
                           epilog='Prints the config unless -o is given; notes on what has no equivalent go to stderr.')
    importer.add_argument('source', choices=('deadd',), help='the daemon to import from')
    importer.add_argument('--yml', metavar='PATH',
                          help='deadd.yml (default: ~/.config/deadd/deadd.yml)').complete = 'files'
    importer.add_argument('--css', metavar='PATH', help='deadd.css (default: next to the yml)').complete = 'files'
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
    rules.add_argument('--timeout', type=int, default=-1, metavar='MS', help='the timeout the sender asks for')
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
