"""Rules: match notifications and change how they look, how long they stay, or what they say.

A rule is a partial config plus a `match`:

    - name: songs                      # optional, shown in the log
      match: { app: [musicman, MPD], summary: '/^★/' }
      timeout: 12s                     # any config key: timeout, style, image, icon, hide, preset, ...
      summary: '{summary} (on {app})'  # rewrites: summary, body, app (templates), urgency, stack-tag

Match values are exact strings, /regex/ (with optional i flag), or a list of either (any of them).
Named regex groups can be used in templates. Rules apply in order, each seeing what the earlier ones
rewrote; a timeout set by a rule wins over the one the sender asked for.
"""

import re
from dataclasses import dataclass, field

from notifyd.config import TOP_LEVEL, ConfigError, deep_merge, normalize
from notifyd.model import URGENCY_NAMES

MATCH_FIELDS = {
    'app': lambda n: n.app,
    'summary': lambda n: n.summary,
    'body': lambda n: n.body,
    'icon': lambda n: n.app_icon,
    'category': lambda n: n.category,
    'desktop-entry': lambda n: n.desktop_entry,
    'stack-tag': lambda n: n.stack_tag,
    'urgency': lambda n: n.urgency_name,
    'mode': lambda n: n.mode,
    # who sent it (the D-Bus client's process and its parent; empty when unknown)
    'process': lambda n: n.sender.get('process', ''),
    'parent': lambda n: n.sender.get('parent', ''),
    # the focused window on the stack's monitor was fullscreen when it arrived
    'fullscreen': lambda n: 'true' if n.fullscreen else 'false',
}
# dunst's per-notification color hints, as style keys (rules still override them)
COLOR_HINTS = {'fgcolor': ('foreground',), 'bgcolor': ('background',), 'frcolor': ('border-color',),
               'hlcolor': ('progress', 'color')}
# fgcolor colors all the text, as in dunst, so it also replaces the colors presets give the parts and the
# urgencies; the header's app name and time stay quieter than the text by opacity
FG_PARTS = {'title': {}, 'title-end': {}, 'body': {}, 'count': {}, 'appname': {'opacity': 0.75},
            'time': {'opacity': 0.75}}
REWRITE_KEYS = {'summary', 'body', 'app', 'urgency', 'stack-tag'}
SETTING_KEYS = TOP_LEVEL - {'rules', 'log', 'click', 'max-visible', 'position', 'center', 'lock',
                            'indicate-waiting'}


class _Fields(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def _value_matcher(expected, where):
    """A function str -> captures dict (match) or None."""
    if isinstance(expected, list):
        matchers = [_value_matcher(item, where) for item in expected]

        def any_of(actual):
            for matcher in matchers:
                found = matcher(actual)
                if found is not None:
                    return found
            return None
        return any_of
    if isinstance(expected, bool):
        expected = 'true' if expected else 'false'
    expected = str(expected)
    regex = re.fullmatch(r'/(.*)/([i]*)', expected, re.S)
    if regex:
        try:
            pattern = re.compile(regex.group(1), re.I if 'i' in regex.group(2) else 0)
        except re.error as exc:
            raise ConfigError(f'{where}: bad regex {expected}: {exc}') from exc

        def search(actual):
            found = pattern.search(actual)
            return {k: v for k, v in found.groupdict().items() if v is not None} if found else None
        return search
    return lambda actual: {} if actual == expected else None


def _normalize_actual(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return '' if value is None else str(value)


@dataclass
class Rule:
    name: str
    matchers: list = field(default_factory=list)    # [(getter, matcher)]
    modify: dict = field(default_factory=dict)
    rewrite: dict = field(default_factory=dict)
    modes: tuple = ()                               # literal names in match.mode, for the center's picker

    def match(self, n):
        """Captured groups when the rule matches N, else None."""
        captures = {}
        for getter, matcher in self.matchers:
            found = matcher(_normalize_actual(getter(n)))
            if found is None:
                return None
            captures.update(found)
        return captures


def compile_rule(raw, index):
    where = f'rules[{index}]'
    if not isinstance(raw, dict):
        raise ConfigError(f'{where}: expected a mapping')
    raw = dict(raw)
    name = str(raw.pop('name', '') or '')
    match = raw.pop('match', None) or {}
    if not isinstance(match, dict):
        raise ConfigError(f'{where}: match: expected a mapping')
    matchers = []
    for key, expected in match.items():
        if key == 'hint':
            if not isinstance(expected, dict):
                raise ConfigError(f'{where}: match.hint: expected a mapping of hint names to values')
            for hint, value in expected.items():
                matchers.append(((lambda h: lambda n: n.hints.get(h))(hint),
                                 _value_matcher(value, f'{where}: match.hint.{hint}')))
        elif key in MATCH_FIELDS:
            matchers.append((MATCH_FIELDS[key], _value_matcher(expected, f'{where}: match.{key}')))
        else:
            raise ConfigError(f'{where}: match: unknown field {key!r} '
                              f'(known: {", ".join(sorted([*MATCH_FIELDS, "hint"]))})')
    rewrite = {k: raw.pop(k) for k in list(raw) if k in REWRITE_KEYS}
    if 'urgency' in rewrite and rewrite['urgency'] not in URGENCY_NAMES:
        raise ConfigError(f'{where}: urgency: expected one of {", ".join(URGENCY_NAMES)}')
    unknown = [k for k in raw if k not in SETTING_KEYS]
    if unknown:
        raise ConfigError(f'{where}: unknown key(s) {", ".join(map(repr, unknown))}')
    warnings = normalize(raw, where)
    if not name:
        apps = match.get('app')
        name = (apps if isinstance(apps, str) else ','.join(map(str, apps))) if apps else f'rule{index + 1}'
    mode = match.get('mode')
    modes = tuple(str(m) for m in (mode if isinstance(mode, list) else [mode] if mode is not None else [])
                  if not re.fullmatch(r'/.*/i*', str(m), re.S))
    return Rule(name=name, matchers=matchers, modify=raw, rewrite=rewrite, modes=modes), warnings


def known_modes(rules, current='normal'):
    """'normal', then every mode a rule matches on by name, then CURRENT: each once, in that order."""
    names = ['normal', *(m for rule in rules for m in rule.modes), current]
    return list(dict.fromkeys(names))


def compile_rules(raw_rules):
    rules, warnings = [], []
    for index, raw in enumerate(raw_rules):
        if isinstance(raw, Rule):
            rules.append(raw)
            continue
        rule, rule_warnings = compile_rule(raw, index)
        rules.append(rule)
        warnings += rule_warnings
    return rules, warnings


def _apply_rewrite(n, rewrite, captures):
    fields = _Fields(app=n.app, summary=n.summary, body=n.body, category=n.category,
                     urgency=n.urgency_name, **captures)
    for key, template in rewrite.items():
        if key == 'urgency':
            n.hints['urgency'] = URGENCY_NAMES.index(template)
        elif key == 'stack-tag':
            n.hints['x-dunst-stack-tag'] = str(template).format_map(fields)
        else:
            setattr(n, key, str(template).format_map(fields))


def hint_style(hints, rules_set_foreground=False):
    """The style dunst's color hints in HINTS ask for. fgcolor is left out when RULES_SET_FOREGROUND (a matching
    rule's foreground wins over it, parts and all)."""
    style = {}
    for hint, path in COLOR_HINTS.items():
        value = hints.get(hint)
        if not (isinstance(value, str) and value) or (hint == 'fgcolor' and rules_set_foreground):
            continue
        if len(path) == 1:
            style[path[0]] = value
        else:
            style.setdefault(path[0], {})[path[1]] = value
        if hint == 'fgcolor':
            for part, extra in FG_PARTS.items():
                style[part] = {'color': value, **extra}
            for urgency in URGENCY_NAMES:
                style[urgency] = {'foreground': value}
    return style


def resolve(config, n, extra=None):
    """Apply CONFIG's rules to notification N in place: its settings, matched rule names and timeout.
    EXTRA (a settings mapping) goes on top of everything, for previews."""
    ruled = {}            # the matching rules' settings, merged: they go on top of the hints'
    rule_timeout = None
    n.rules = []
    for rule in config.rules:
        captures = rule.match(n)
        if captures is None:
            continue
        ruled = deep_merge(ruled, rule.modify)
        _apply_rewrite(n, rule.rewrite, captures)
        n.rules.append(rule.name)
        if 'timeout' in rule.modify:
            rule_timeout = rule.modify['timeout']
    settings = config.base()
    hinted = hint_style(n.hints, rules_set_foreground='foreground' in (ruled.get('style') or {}))
    if hinted:
        settings = deep_merge(settings, {'style': hinted})
    settings = deep_merge(settings, ruled)
    if extra:
        settings = deep_merge(settings, extra)
        if 'timeout' in extra:
            rule_timeout = extra['timeout']
    n.settings = settings
    if rule_timeout is not None:
        n.timeout = rule_timeout
    elif n.requested_timeout >= 0:
        n.timeout = n.requested_timeout
    elif n.urgency == 2:
        n.timeout = settings['timeout-critical']
    else:
        n.timeout = settings['timeout']
    return n
