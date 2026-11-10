"""`notifyd import deadd`: deadd.yml (+ deadd.css) -> a notifyd config.

Style values that equal the deadd preset are left out, so switching presets later still changes
the look; only what differs from it ends up under `style:`. Keys with no notifyd equivalent are
reported, not dropped silently.
"""

import re

import yaml

from notifyd.config import format_duration, load_preset

DEADD_MATCH = {'app-name': 'app', 'title': 'summary', 'body': 'body', 'app-icon': 'icon', 'urgency': 'urgency'}
HIDE_WHEN_EMPTY = {'app-icon': 'app-icon', 'app-name': 'app-name', 'title': 'title', 'body': 'body',
                   'image': 'image'}


def _get(data, *keys, default=None):
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data


def import_deadd_yaml(data, report):
    out = {}
    if 'width' in data:
        out['width'] = data['width']
    position = {}
    margin_top, margin_right = data.get('margin-top'), data.get('margin-right')
    if margin_top is not None or margin_right is not None:
        position['margin'] = [margin_top or 0, margin_right or 0]
    if 'follow-mouse' in data:
        position['follow'] = 'mouse' if data['follow-mouse'] else 'none'
    if 'monitor' in data:
        position['monitor'] = data['monitor']
    if position:
        out['position'] = position
    notification = data.get('notification') or {}
    timeout = _get(notification, 'popup', 'default-timeout')
    if timeout is not None:
        out['timeout'] = format_duration(int(timeout))
    if _get(notification, 'popup', 'max-lines-in-body') is not None:
        out['body'] = {'max-lines': _get(notification, 'popup', 'max-lines-in-body')}
    icon_size = _get(notification, 'app-icon', 'icon-size')
    if icon_size is not None:
        out['icon'] = {'size': icon_size}
    image = notification.get('image') or {}
    if image:
        out['image'] = {k: image[k] for k in ('size', 'margin-right') if k in image}
        for key in ('margin-top', 'margin-bottom', 'margin-left'):
            if key in image:
                report.append(f'notification.image.{key}: not supported (only margin-right)')
    if notification.get('use-markup') is False:
        out['markup'] = 'plain'
    if _get(notification, 'popup', 'click-behavior'):
        report.append('notification.popup.click-behavior: set click.left/right in the notifyd config instead')
    for key in data:
        if key not in ('width', 'margin-top', 'margin-right', 'follow-mouse', 'monitor', 'notification'):
            report.append(f'{key}: no notifyd equivalent (ignored)')
    if data.get('notification-center') is not None:
        report.append('notification-center: the notification center is not built yet (ignored)')
    rules = []
    for index, mod in enumerate(notification.get('modifications') or []):
        rule = {}
        match = {}
        for key, value in (mod.get('match') or {}).items():
            if key in DEADD_MATCH:
                match[DEADD_MATCH[key]] = str(value)
            else:
                report.append(f'modifications[{index}].match.{key}: no notifyd equivalent (ignored)')
        if match:
            rule['match'] = match
        hide = []
        for key, value in (mod.get('modify') or {}).items():
            if key in HIDE_WHEN_EMPTY and value == '':
                hide.append(HIDE_WHEN_EMPTY[key])
            elif key == 'timeout':
                rule['timeout'] = format_duration(int(value))
            elif key == 'image-size':
                rule.setdefault('image', {})['size'] = value
            elif key == 'margin-right':
                rule.setdefault('image', {})['margin-right'] = value
            elif key == 'class-name':
                rule['class'] = value
            elif key in ('title', 'body', 'app-name'):
                rule[{'title': 'summary', 'body': 'body', 'app-name': 'app'}[key]] = value
            elif key == 'app-icon':
                rule.setdefault('icon', {})['name'] = value
            elif key == 'image':
                rule.setdefault('image', {})['path'] = value
            elif key == 'urgency':
                rule['urgency'] = value
            else:
                report.append(f'modifications[{index}].modify.{key}: no notifyd equivalent (ignored)')
        if hide:
            rule['hide'] = hide
        rules.append(rule)
    if rules:
        out['rules'] = rules
    return out


# -- CSS -------------------------------------------------------------------

_COMMENT = re.compile(r'/\*.*?\*/', re.S)
_RULE = re.compile(r'([^{}]+)\{([^{}]*)\}')


def parse_css(text):
    """[(selector, {property: value})] for a flat stylesheet; comma selectors are split."""
    rules = []
    for selectors, body in _RULE.findall(_COMMENT.sub('', text)):
        props = {}
        for declaration in body.split(';'):
            if ':' in declaration:
                key, value = declaration.split(':', 1)
                props[key.strip().lower()] = value.strip()
        for selector in selectors.split(','):
            rules.append((' '.join(selector.split()), props))
    return rules


def import_deadd_css(text, report):
    style = {}
    used = 0
    for selector, props in parse_css(text):
        before = (len(style), sum(len(v) for v in style.values() if isinstance(v, dict)))
        if selector in ('.blurredBG.notification', '.blurredBG.normal', '.blurredBG.low', '.blurredBG'):
            if 'background' in props:
                style['background'] = props['background']
        elif selector == '.blurredBG.notification.critical':
            if 'background' in props:
                style.setdefault('critical', {})['background'] = props['background']
        elif selector in ('label', 'label.notification'):
            if 'color' in props:
                style['foreground'] = props['color']
        elif selector in ('label.critical',):
            if 'color' in props:
                style.setdefault('critical', {})['foreground'] = props['color']
        elif selector in ('.title', '.appname', '.time'):
            part = selector[1:]
            for key, target in (('font-weight', 'weight'), ('font-size', 'size'), ('color', 'color')):
                if key in props:
                    style.setdefault(part, {})[target] = props[key]
        elif selector == '.notification.content':
            left, right = props.get('margin-left'), props.get('margin-right')
            if left or right:
                horizontal = int(re.sub(r'\D', '', left or right) or 0)
                style['padding'] = [None, horizontal]
        after = (len(style), sum(len(v) for v in style.values() if isinstance(v, dict)))
        if after != before:
            used += 1
    report.append(f'deadd.css: {used} rule(s) used for colors, fonts and padding; the notification center, '
                  'buttons and other rules have no notifyd equivalent yet')
    return style


def _normalize(value):
    return re.sub(r'\s+', '', str(value)).lower() if isinstance(value, str) else value


def strip_preset_values(style, preset_style):
    """STYLE without the values PRESET_STYLE already has (one level deep; spaces and case in colors
    do not count)."""
    out = {}
    for key, value in style.items():
        base = preset_style.get(key)
        if key == 'padding':
            preset_padding = base or [0, 0]
            value = [preset_padding[0] if value[0] is None else value[0], value[1]]
        if isinstance(value, dict):
            base = base or {}
            inner = {k: v for k, v in value.items() if _normalize(base.get(k)) != _normalize(v)}
            if inner:
                out[key] = inner
        elif _normalize(base) != _normalize(value):
            out[key] = value
    return out


def import_deadd(yml_text, css_text=None):
    """(config dict, report lines)."""
    report = []
    data = yaml.safe_load(yml_text) or {}
    config = {'preset': 'deadd', **import_deadd_yaml(data, report)}
    if css_text:
        style = strip_preset_values(import_deadd_css(css_text, report), load_preset('deadd')['style'])
        if style:
            config['style'] = style
    return config, report


HEADER = '''# notifyd config, imported from deadd-notification-center.
# Everything is optional; see docs/config.md. Rules apply in order, and a rule's timeout wins over
# the one the sender asked for.
'''


def dump_config(config):
    rules = config.pop('rules', None)
    text = HEADER + '\n' + yaml.safe_dump(config, sort_keys=False, default_flow_style=None, allow_unicode=True)
    if rules:
        text += '\nrules:\n' + '\n'.join(
            yaml.safe_dump([rule], sort_keys=False, default_flow_style=None, allow_unicode=True, width=110)
            for rule in rules)
    return text
