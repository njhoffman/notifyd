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
        'follow': 'mouse',       # mouse (the monitor under the pointer) or none (position.monitor)
        'monitor': 0,
    },
    'width': 350,
    'timeout': 8000,             # ms; 0 never expires
    'timeout-critical': 0,       # critical notifications without a rule timeout
    'max-visible': 6,            # more wait until one closes
    'stack-duplicates': True,    # an identical notification (app, summary, body, urgency) replaces it, counted
    'icon': {'size': 25, 'name': None},
    'image': {'size': 75, 'path': None, 'margin-right': 0},
    'body': {'max-lines': 6},
    'markup': 'full',            # full (Pango), strip (text only), plain (shown as typed)
    'hide': [],                  # parts not shown: app-icon, app-name, time, title, body, image
    'class': None,               # extra CSS class for the notification
    'skip': False,               # true: not shown (still logged, and kept in history)
    'sound': None,               # a sound file or freedesktop sound name; false: silent (else the sender's hints)
    'buttons': [],               # extra buttons that run commands: [{label: Next, run: playerctl next}]
    'run': None,                 # a shell command run for each matching notification (NOTIFYD_* env, JSON on stdin)
    'idle-threshold': 0,         # no expiry while the user has been idle this long (0: off)
    'history': {'size': 100, 'keep': True, 'persist': True},   # persist: $XDG_STATE_HOME/notifyd/history.jsonl
    'log': {'format': 'pretty', 'color': 'auto', 'level': 'info', 'file': None},
    'click': {'left': 'dismiss', 'middle': 'dismiss-all', 'right': 'default-action'},
    'style': {},
    'rules': [],
}

TOP_LEVEL = set(DEFAULTS)
DURATION_KEYS = ('timeout', 'timeout-critical', 'idle-threshold')
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


_DURATION = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(ms|s|m)?\s*$')


def parse_duration(value, key='timeout'):
    """Milliseconds from 8000, '8s', '1500ms', '2m', 'never' (0) or 0."""
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
            return int(number * {'ms': 1, 's': 1000, 'm': 60000}[unit])
    raise ConfigError(f'{key}: expected a duration such as 8s, 1500ms or never, got {value!r}')


def format_duration(ms):
    if ms == 0:
        return 'never'
    if ms % 60000 == 0:
        return f'{ms // 60000}m'
    if ms % 1000 == 0:
        return f'{ms // 1000}s'
    return f'{ms}ms'


def normalize(settings, where='config'):
    """Parse durations and check value shapes in place; returns the list of warnings."""
    warnings = []
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
    if 'buttons' in settings:
        buttons = settings['buttons'] or []
        if not isinstance(buttons, list) or not all(
                isinstance(b, dict) and isinstance(b.get('label'), str) and isinstance(b.get('run'), str)
                for b in buttons):
            raise ConfigError(f'{where}: buttons: expected a list of {{label: TEXT, run: COMMAND}}')
        settings['buttons'] = buttons
    if settings.get('run') is not None and not isinstance(settings['run'], str):
        raise ConfigError(f'{where}: run: expected a shell command')
    if settings.get('sound') not in (None, False) and not isinstance(settings['sound'], str):
        raise ConfigError(f'{where}: sound: expected a file, a sound name, or false')
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
            if not isinstance(data, dict) or not isinstance(data.get('style', {}), dict):
                raise ConfigError(f'preset {name}: expected a mapping with a style mapping')
            preset = {'name': name, 'description': data.get('description', ''), 'style': data.get('style') or {}}
            _preset_cache[path] = (mtime, preset)
            return preset
    raise ConfigError(f'unknown preset {name!r} (have: {", ".join(preset_names())})')
