"""Config loading: defaults, the user's YAML file, presets, durations.

Everything in the file is optional. A rule (see rules.py) is a partial config applied to the
notifications it matches, so the keys below are also what a rule can set.
"""

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PRESET_DIR = Path(__file__).parent / 'presets'

DEFAULTS = {
    'preset': 'deadd',
    'position': {
        'anchor': 'top-right',   # top-left, top-center, top-right, bottom-left, bottom-center, bottom-right
        'margin': [30, 5],       # distance from the anchored edges: [vertical, horizontal]
        'gap': 10,               # space between stacked notifications
        'follow': 'mouse',       # mouse (the monitor under the pointer), focus (the focused window's) or none
        'monitor': 0,            # without follow (or when it finds none): an index or an output name (DP-1)
    },
    'width': 350,
    'timeout': 8000,             # ms; 0 never expires
    'timeout-critical': 0,       # critical notifications without a rule timeout
    'max-visible': 6,            # more wait until one closes
    'stack-duplicates': True,    # an identical notification (app, summary, body, urgency) replaces it, counted
    'group-by': [],              # e.g. [app]: a new one with the same app replaces the open one, counted
    'icon': {'size': 25, 'name': None},
    'image': {'size': 75, 'path': None, 'margin-right': 0},
    'title': {'max-lines': 0},   # 0: as many as it needs
    'body': {'max-lines': 6},
    'markup': 'full',            # full (Pango), strip (text only), plain (shown as typed)
    'lock': {'pause': False, 'processes': ['i3lock', 'xsecurelock', 'slock', 'betterlockscreen']},
    'indicate-waiting': True,    # +N on the last popup while N wait for room
    'timeout-bar': False,        # true: a thin bar along the popup drains as its timer runs
    'ignore-close': False,       # true (in a rule): the app closing its own notification is ignored
    'fullscreen': 'show',        # or delay: wait while the focused window is fullscreen (not critical ones)
    'hide': [],                  # parts not shown: app-icon, app-name, time, title, body, image
    'class': None,               # extra CSS class for the notification
    'skip': False,               # true: not shown (still logged, and kept in history)
    'sound': None,               # a sound file or freedesktop sound name; false: silent (else the sender's hints)
    'buttons': [],               # extra buttons that run commands: [{label: Next, run: playerctl next}]
    'copy-code': True,           # a "Copy 123456" button when the text holds a one-time code
    'copy-code-clear': 60000,    # ms until a copied code leaves the clipboard (if still there); 0 never
    'actions': {'drop': []},     # the sender's actions left out, by key or label (text or /regex/)
    'run': None,                 # a shell command run for each matching notification (NOTIFYD_* env, JSON on stdin)
    'run-on': ['receive'],       # when run runs: any of receive, action (one is invoked), close
    'filter': None,              # a command that may rewrite it before it shows: prints a JSON object (see docs)
    'filter-timeout': 500,       # ms the filter may take; then it shows unchanged
    'idle-threshold': 0,         # no expiry while the user has been idle this long (0: off)
    'history': {'size': 100, 'keep': True, 'persist': True},   # persist: $XDG_STATE_HOME/notifyd/history.jsonl
    'log': {'format': 'pretty', 'color': 'auto', 'level': 'info', 'file': None},
    'click': {'left': 'dismiss', 'middle': 'dismiss-all', 'right': 'default-action'},
    'center': {'width': 400, 'side': 'right', 'hide-on-leave': True, 'mpris': True, 'volume': True, 'modes': True,
               'cards': 20, 'keyboard': True, 'remote-art': True, 'mpris-ignore': [], 'buttons': [], 'group': False,
               'toggle-key': '<Super>grave'},   # the WM's toggle binding, which the grab swallows
    'fade': {'in': 0, 'out': 150},   # ms; picom fades windows in, but not translucent ones out
    'style': {},
    'rules': [],
}

TOP_LEVEL = set(DEFAULTS)
DURATION_KEYS = ('timeout', 'timeout-critical', 'idle-threshold', 'filter-timeout', 'copy-code-clear')
GROUP_FIELDS = ('app', 'summary', 'category', 'urgency')
FOLLOW = ('mouse', 'focus', 'none')
ANCHORS = tuple(f'{v}-{h}' for v in ('top', 'bottom') for h in ('left', 'center', 'right'))
RUN_EVENTS = ('receive', 'action', 'close')
HIDE_PARTS = {'app-icon', 'app-name', 'time', 'title', 'body', 'image', 'actions', 'progress'}


class ConfigError(Exception):
    pass


def config_home():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'notifyd'


def default_path():
    return config_home() / 'config.yaml'


def deep_merge(base, over):
    """BASE with OVER on top: dicts merge key by key, anything else is replaced. Returns a new dict."""
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


_DURATION = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$')


def parse_duration(value, key='timeout'):
    """Milliseconds from 8000, '8s', '1500ms', '2m', '1h', 'never' (0) or 0."""
    if isinstance(value, bool):
        raise ConfigError(f'{key}: expected a duration such as 8s, got {value!r}')
    if isinstance(value, (int, float)):
        if value < 0:
            raise ConfigError(f'{key}: a duration cannot be negative ({value})')
        return int(value)
    if isinstance(value, str):
        if value.strip() == 'never':
            return 0
        found = _DURATION.match(value)
        if found:
            number, unit = float(found.group(1)), found.group(2) or 'ms'
            return int(number * {'ms': 1, 's': 1000, 'm': 60000, 'h': 3600000}[unit])
    raise ConfigError(f'{key}: expected a duration such as 8s, 1500ms or never, got {value!r}')


def format_duration(ms):
    if ms == 0:
        return 'never'
    if ms % 60000 == 0:
        return f'{ms // 60000}m'
    if ms % 1000 == 0:
        return f'{ms // 1000}s'
    return f'{ms}ms'


# settings the renderer reads as whole numbers: (path, may be negative)
NUMBER_KEYS = ((('width',), False), (('image', 'size'), False), (('image', 'margin-right'), True),
               (('icon', 'size'), False), (('title', 'max-lines'), False), (('body', 'max-lines'), False),
               (('position', 'monitor'), False), (('history', 'size'), False),
               (('position', 'gap'), True))


def _check_numbers(settings, where):
    for path, negative in NUMBER_KEYS:
        parent = settings
        for key in path[:-1]:
            parent = parent.get(key) if isinstance(parent, dict) else None
        if not isinstance(parent, dict) or parent.get(path[-1]) is None:
            continue
        value = parent[path[-1]]
        if path == ('position', 'monitor') and isinstance(value, str) and not value.lstrip('-').isdigit():
            continue                                         # an output name
        if isinstance(value, str) and value.strip().lstrip('-').isdigit():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or (value < 0 and not negative):
            kind = 'a whole number' if negative else 'a whole number >= 0'
            raise ConfigError(f'{where}: {".".join(path)}: expected {kind}, got {parent[path[-1]]!r}')
        parent[path[-1]] = value


def _check_position(settings, where):
    """position.follow is one of FOLLOW; position.monitor an index or an output name (kept as text)."""
    position = settings.get('position')
    if not isinstance(position, dict):
        return
    if 'anchor' in position and position['anchor'] not in ANCHORS:
        raise ConfigError(f'{where}: position.anchor: expected one of {", ".join(ANCHORS)}, '
                          f'got {position["anchor"]!r}')
    if 'follow' in position and position['follow'] not in FOLLOW:
        raise ConfigError(f'{where}: position.follow: expected one of {", ".join(FOLLOW)}, '
                          f'got {position["follow"]!r}')
    monitor = position.get('monitor')
    if isinstance(monitor, str) and monitor.strip() and not monitor.strip().lstrip('-').isdigit():
        position['monitor'] = monitor.strip()


def normalize(settings, where='config'):
    """Parse durations and check value shapes in place; returns the list of warnings."""
    warnings = []
    _check_position(settings, where)
    _check_numbers(settings, where)
    for key in DURATION_KEYS:
        if key in settings:
            settings[key] = parse_duration(settings[key], f'{where}: {key}')
    if 'hide' in settings:
        hide = settings['hide']
        if isinstance(hide, str):
            hide = [hide]
        if not isinstance(hide, list):
            raise ConfigError(f'{where}: hide: expected a list of parts')
        unknown = [part for part in hide if part not in HIDE_PARTS]
        if unknown:
            warnings.append(f'{where}: hide: unknown part(s) {", ".join(map(str, unknown))} '
                            f'(known: {", ".join(sorted(HIDE_PARTS))})')
        settings['hide'] = [part for part in hide if part in HIDE_PARTS]
    if isinstance(settings.get('fade'), dict):
        for key in ('in', 'out'):
            if key in settings['fade']:
                settings['fade'][key] = parse_duration(settings['fade'][key], f'{where}: fade.{key}')
    if 'group-by' in settings:
        group = settings['group-by'] or []
        group = [group] if isinstance(group, str) else group
        unknown = [f for f in group if f not in GROUP_FIELDS]
        if not isinstance(group, list) or unknown:
            raise ConfigError(f'{where}: group-by: expected a list of {", ".join(GROUP_FIELDS)}')
        settings['group-by'] = group
    if 'buttons' in settings:
        buttons = settings['buttons'] or []
        if not isinstance(buttons, list) or not all(
                isinstance(b, dict) and isinstance(b.get('label'), str) and isinstance(b.get('run'), str)
                for b in buttons):
            raise ConfigError(f'{where}: buttons: expected a list of {{label: TEXT, run: COMMAND}}')
        settings['buttons'] = buttons
    if isinstance(settings.get('actions'), dict) and 'drop' in settings['actions']:
        drop = settings['actions']['drop'] or []
        drop = [drop] if isinstance(drop, str) else drop
        if not isinstance(drop, list) or not all(isinstance(item, str) for item in drop):
            raise ConfigError(f'{where}: actions.drop: expected a list of action keys or labels (text or /regex/)')
        for item in drop:
            regex = re.fullmatch(r'/(.*)/([i]*)', item, re.S)
            if regex:
                try:
                    re.compile(regex.group(1))
                except re.error as exc:
                    raise ConfigError(f'{where}: actions.drop: bad regex {item}: {exc}') from exc
        settings['actions']['drop'] = drop
    elif 'actions' in settings and not isinstance(settings['actions'], dict):
        raise ConfigError(f'{where}: actions: expected {{drop: [KEY or LABEL, ...]}}')
    if 'history' in settings:
        history = settings['history']
        if not isinstance(history, dict):
            raise ConfigError(f'{where}: history: expected {{size: N, keep: true|false, persist: true|false}}')
        if 'size' in history and history['size'] is None:
            raise ConfigError(f'{where}: history.size: expected a whole number >= 0')
    if 'run-on' in settings:
        events = settings['run-on'] or []
        events = [events] if isinstance(events, str) else events
        if not isinstance(events, list) or any(event not in RUN_EVENTS for event in events):
            raise ConfigError(f'{where}: run-on: expected a list of {", ".join(RUN_EVENTS)}')
        settings['run-on'] = events
    if 'copy-code' in settings and not isinstance(settings['copy-code'], bool):
        raise ConfigError(f'{where}: copy-code: expected true or false')
    if settings.get('filter') is not None and not isinstance(settings['filter'], str):
        raise ConfigError(f'{where}: filter: expected a shell command')
    if settings.get('run') is not None and not isinstance(settings['run'], str):
        raise ConfigError(f'{where}: run: expected a shell command')
    if settings.get('sound') not in (None, False) and not isinstance(settings['sound'], str):
        raise ConfigError(f'{where}: sound: expected a file, a sound name, or false')
    center = settings.get('center')
    if isinstance(center, dict) and 'buttons' in center:
        buttons = center['buttons'] or []
        if not isinstance(buttons, list) or not all(
                isinstance(b, dict) and isinstance(b.get('label'), str) and isinstance(b.get('run'), str)
                and isinstance(b.get('state', ''), str) and isinstance(b.get('icon', ''), str) for b in buttons):
            raise ConfigError(f'{where}: center.buttons: expected a list of {{label: TEXT, run: COMMAND, '
                              'state: COMMAND, icon: NAME}} (state and icon are optional)')
        center['buttons'] = buttons
    if isinstance(center, dict) and 'mpris-ignore' in center:
        ignore = center['mpris-ignore'] or []
        if not isinstance(ignore, list) or not all(isinstance(name, str) for name in ignore):
            raise ConfigError(f'{where}: center.mpris-ignore: expected a list of player names')
        center['mpris-ignore'] = ignore
    if isinstance(settings.get('lock'), dict):
        lock = settings['lock']
        if not isinstance(lock.get('pause', False), bool):
            raise ConfigError(f'{where}: lock.pause: expected true or false')
        processes = lock.get('processes', [])
        if not isinstance(processes, list) or not all(isinstance(p, str) for p in processes):
            raise ConfigError(f'{where}: lock.processes: expected a list of process names')
    if not isinstance(settings.get('timeout-bar', False), bool):
        raise ConfigError(f'{where}: timeout-bar: expected true or false')
    if not isinstance(settings.get('ignore-close', False), bool):
        raise ConfigError(f'{where}: ignore-close: expected true or false')
    if 'fullscreen' in settings and settings['fullscreen'] not in ('show', 'delay'):
        raise ConfigError(f'{where}: fullscreen: expected show or delay, got {settings["fullscreen"]!r}')
    if 'markup' in settings and settings['markup'] not in ('full', 'strip', 'plain'):
        raise ConfigError(f'{where}: markup: expected full, strip or plain, got {settings["markup"]!r}')
    return warnings


@dataclass
class Config:
    settings: dict
    path: Path | None = None
    warnings: list = field(default_factory=list)

    @property
    def rules(self):
        return self.settings['rules']

    def base(self):
        """The settings every notification starts from (no rules)."""
        return {k: v for k, v in self.settings.items() if k != 'rules'}


def load_config(path=None, text=None):
    """The config from PATH (default ~/.config/notifyd/config.yaml), or from TEXT. A missing file is
    the defaults; a broken one raises ConfigError."""
    if text is None:
        path = Path(path) if path else default_path()
        try:
            text = path.read_text()
        except FileNotFoundError:
            text = ''
        except OSError as exc:
            raise ConfigError(f'{path}: {exc.strerror}') from exc
        except UnicodeDecodeError as exc:
            raise ConfigError(f'{path}: not UTF-8 text (byte {exc.start})') from exc
    try:
        user = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f'{path or "config"}: {exc}') from exc
    if not isinstance(user, dict):
        raise ConfigError(f'{path or "config"}: expected a mapping at the top level')
    warnings = [f'unknown key {key!r} (ignored)' for key in user if key not in TOP_LEVEL]
    user = {k: v for k, v in user.items() if k in TOP_LEVEL}
    settings = deep_merge(DEFAULTS, user)
    warnings += normalize(settings)
    rules = settings.get('rules') or []
    if not isinstance(rules, list):
        raise ConfigError('rules: expected a list')
    from notifyd.rules import compile_rules  # rules imports this module
    settings['rules'], rule_warnings = compile_rules(rules)
    warnings += rule_warnings
    load_preset(settings['preset'])   # fail early on an unknown preset
    return Config(settings=settings, path=path, warnings=warnings)


# -- presets -----------------------------------------------------------------

def preset_dirs():
    return [config_home() / 'presets', PRESET_DIR]


def preset_names():
    names = set()
    for directory in preset_dirs():
        if directory.is_dir():
            names.update(p.stem for p in directory.glob('*.yaml'))
    return sorted(names)


_preset_cache = {}


def load_preset(name):
    """A preset's data: {'name', 'description', 'style'}. User presets shadow bundled ones."""
    for directory in preset_dirs():
        path = directory / f'{name}.yaml'
        if path.is_file():
            mtime = path.stat().st_mtime
            cached = _preset_cache.get(path)
            if cached and cached[0] == mtime:
                return cached[1]
            try:
                data = yaml.safe_load(path.read_text()) or {}
            except yaml.YAMLError as exc:
                raise ConfigError(f'preset {name}: {exc}') from exc
            except (OSError, UnicodeDecodeError) as exc:   # unreadable: a ConfigError, so callers fall back
                raise ConfigError(f'preset {name}: {path}: {getattr(exc, "strerror", None) or exc}') from exc
            if not isinstance(data, dict) or not isinstance(data.get('style', {}), dict):
                raise ConfigError(f'preset {name}: expected a mapping with a style mapping')
            preset = {'name': name, 'description': data.get('description', ''), 'style': data.get('style') or {}}
            _preset_cache[path] = (mtime, preset)
            return preset
    raise ConfigError(f'unknown preset {name!r} (have: {", ".join(preset_names())})')
