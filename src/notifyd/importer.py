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


HEADER = '''# notifyd config, imported from {source}.
# Everything is optional; see docs/config.md. Rules apply in order, and a rule's timeout wins over
# the one the sender asked for.
'''


def dump_config(config, source='deadd-notification-center'):
    rules = config.pop('rules', None)
    text = HEADER.format(source=source) + '\n'
    text += yaml.safe_dump(config, sort_keys=False, default_flow_style=None, allow_unicode=True)
    if rules:
        text += '\nrules:\n' + '\n'.join(
            yaml.safe_dump([rule], sort_keys=False, default_flow_style=None, allow_unicode=True, width=110)
            for rule in rules)
    return text


# -- dunst -----------------------------------------------------------------------

DUNST_MATCH = {'appname': 'app', 'summary': 'summary', 'body': 'body', 'category': 'category',
               'desktop_entry': 'desktop-entry', 'stack_tag': 'stack-tag', 'icon': 'icon', 'msg_urgency': 'urgency'}
DUNST_CLICK = {'close_current': 'dismiss', 'close_all': 'dismiss-all', 'do_action': 'default-action',
               'open_url': 'default-action', 'none': 'none'}
DUNST_IGNORED_GLOBAL = {'monitor', 'scale', 'progress_bar', 'progress_bar_frame_width',
                        'progress_bar_min_width',
                        'progress_bar_max_width', 'separator_height', 'separator_color', 'text_icon_padding',
                        'line_height', 'sort', 'alignment', 'vertical_alignment', 'ellipsize', 'word_wrap',
                        'ignore_newline', 'show_indicators', 'icon_position', 'icon_path', 'icon_theme',
                        'enable_recursive_icon_lookup', 'sticky_history', 'show_age_threshold', 'indicate_hidden',
                        'dmenu', 'browser', 'always_run_script', 'title', 'class', 'ignore_dbusclose',
                        'hide_duplicate_count', 'height', 'notification_limit_shown'}


def _dunst_value(raw):
    value = raw.strip()
    for quote in ('"', "'"):
        if len(value) >= 2 and value[0] == value[-1] == quote:
            return value[1:-1]
    return value


def parse_dunstrc(text):
    """{section: {key: value}} from a dunstrc (ini with quotes, ; and # comments)."""
    sections, current = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in ';#':
            continue
        header = re.fullmatch(r'\[(.+)\]', line)
        if header:
            current = sections.setdefault(header.group(1).strip(), {})
            continue
        if current is None or '=' not in line:
            continue
        key, value = line.split('=', 1)
        current[key.strip()] = _dunst_value(value)
    return sections


def _bool(value):
    return str(value).lower() in ('yes', 'true', '1', 'on')


def _dunst_duration(value):
    """dunst durations: plain numbers are seconds; '10s', '500ms', '2m' as written."""
    value = str(value).strip()
    if re.fullmatch(r'\d+(\.\d+)?', value):
        number = float(value)
        return format_duration(int(number * 1000))
    return value


def _glob(value):
    """dunst matches with globs (or regex with `matching = regex`): to an exact string or a /regex/."""
    if not any(ch in value for ch in '*?['):
        return value
    pattern = ''.join('.*' if ch == '*' else '.' if ch == '?' else re.escape(ch) for ch in value)
    return f'/^{pattern}$/'


def _with_alpha(color, transparency):
    """#rrggbb with dunst's transparency (0-100) as rgba()."""
    found = re.fullmatch(r'#([0-9a-fA-F]{6})', color or '')
    if not found or not transparency:
        return color
    r, g, b = (int(found.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    return f'rgba({r}, {g}, {b}, {round(1 - transparency / 100, 2)})'


def _font(value):
    parts = value.split()
    if len(parts) > 1 and re.fullmatch(r'\d+(\.\d+)?', parts[-1]):
        return {'font-family': ' '.join(parts[:-1]), 'font-size': f'{parts[-1]}pt'}
    return {'font-family': value}


def import_dunst(text):
    """(config dict, report lines) from a dunstrc."""
    report = []
    sections = parse_dunstrc(text)
    g = sections.get('global', {})
    out, style, position = {'preset': 'dunst'}, {}, {}
    transparency = int(g.get('transparency', 0) or 0)
    if 'width' in g:
        widths = re.findall(r'\d+', g['width'])
        if widths:
            out['width'] = int(widths[-1])
    if 'origin' in g:
        position['anchor'] = g['origin'].replace('_', '-')
    if 'offset' in g:
        found = re.fullmatch(r'\(?\s*(-?\d+)\s*[x,]\s*(-?\d+)\s*\)?', g['offset'])
        if found:
            position['margin'] = [int(found.group(2)), int(found.group(1))]
    if 'follow' in g:
        position['follow'] = 'none' if g['follow'] == 'none' else 'mouse'
        if g['follow'] == 'keyboard':
            report.append('follow = keyboard: notifyd follows the mouse instead')
    if position:
        out['position'] = position
    if g.get('font'):
        style.update(_font(g['font']))
    if 'frame_width' in g:
        style['border-width'] = int(g['frame_width'])
    if 'frame_color' in g:
        style['border-color'] = g['frame_color']
    if 'corner_radius' in g:
        style['radius'] = int(g['corner_radius'])
    if 'padding' in g or 'horizontal_padding' in g:
        style['padding'] = [int(g.get('padding', 8)), int(g.get('horizontal_padding', g.get('padding', 8)))]
    if 'markup' in g:
        out['markup'] = {'full': 'full', 'strip': 'strip', 'no': 'plain'}.get(g['markup'], 'full')
    if 'notification_limit' in g:
        out['max-visible'] = int(g['notification_limit'])
    if 'history_length' in g:
        out['history'] = {'size': int(g['history_length'])}
    if 'stack_duplicates' in g:
        out['stack-duplicates'] = _bool(g['stack_duplicates'])
    if 'idle_threshold' in g and _dunst_duration(g['idle_threshold']) != 'never':
        out['idle-threshold'] = _dunst_duration(g['idle_threshold'])
    click = {button: DUNST_CLICK.get(g[f'mouse_{button}_click'].split(',')[0].strip())
             for button in ('left', 'middle', 'right') if f'mouse_{button}_click' in g}
    if click:
        out['click'] = {k: v for k, v in click.items() if v}
    for key in g:
        handled = {'width', 'origin', 'offset', 'follow', 'font', 'frame_width', 'frame_color', 'corner_radius',
                   'padding', 'horizontal_padding', 'markup', 'notification_limit', 'history_length',
                   'stack_duplicates', 'idle_threshold', 'transparency', 'format', 'mouse_left_click',
                   'mouse_middle_click', 'mouse_right_click', 'min_icon_size', 'max_icon_size', 'icon_size',
                   'progress_bar_height', 'highlight', 'fullscreen'}
        if key in DUNST_IGNORED_GLOBAL:
            continue
        if key not in handled:
            report.append(f'global.{key}: no notifyd equivalent (ignored)')
    if 'format' in g:
        report.append(f'global.format {g["format"]!r}: notifyd always shows summary and body; use hide: and rules')
    if 'max_icon_size' in g:
        out['image'] = {'size': int(g['max_icon_size'])}
    if 'progress_bar_height' in g:
        style.setdefault('progress', {})['height'] = int(g['progress_bar_height'])
    if 'highlight' in g:
        style.setdefault('progress', {})['color'] = g['highlight']

    timeouts = {}
    for urgency in ('low', 'normal', 'critical'):
        section = sections.get(f'urgency_{urgency}', {})
        part = {}
        if 'background' in section:
            part['background'] = _with_alpha(section['background'], transparency)
        if 'foreground' in section:
            part['foreground'] = section['foreground']
        if 'frame_color' in section:
            part['border-color'] = section['frame_color']
        if part:
            style[urgency] = part
        if 'timeout' in section:
            timeouts[urgency] = _dunst_duration(section['timeout'])
        for key in section:
            if key not in ('background', 'foreground', 'frame_color', 'timeout', 'highlight', 'icon',
                           'default_icon'):
                report.append(f'urgency_{urgency}.{key}: no notifyd equivalent (ignored)')
        if section.get('highlight'):
            style.setdefault(urgency, {})
            report.append(f'urgency_{urgency}.highlight: progress color is per preset, not per urgency')
    if 'normal' in timeouts:
        out['timeout'] = timeouts['normal']
    if 'critical' in timeouts:
        out['timeout-critical'] = timeouts['critical']
    if 'normal' in style:
        # the normal colors are the base ones
        normal = style.pop('normal')
        for key, target in (('background', 'background'), ('foreground', 'foreground'),
                            ('border-color', 'border-color')):
            if key in normal:
                style[target] = normal[key]

    rules = []
    if 'low' in timeouts and timeouts['low'] != timeouts.get('normal'):
        rules.append({'name': 'low urgency', 'match': {'urgency': 'low'}, 'timeout': timeouts['low']})
    for name, section in sections.items():
        if name in ('global', 'experimental') or name.startswith('urgency_'):
            continue
        if section.get('enabled') and not _bool(section['enabled']):
            continue
        regex = section.get('match_type', section.get('matching')) == 'regex'
        rule, match, rule_style = {'name': name}, {}, {}
        for key, value in section.items():
            if key in DUNST_MATCH:
                if key == 'msg_urgency':
                    match['urgency'] = value.lower()
                else:
                    match[DUNST_MATCH[key]] = f'/{value}/' if regex else _glob(value)
            elif key == 'timeout':
                rule['timeout'] = _dunst_duration(value)
            elif key == 'urgency':
                rule['urgency'] = value.lower()
            elif key == 'skip_display':
                if _bool(value):
                    rule['skip'] = True
            elif key == 'history_ignore':
                if _bool(value):
                    rule['history'] = {'keep': False}
            elif key in ('background', 'foreground', 'frame_color'):
                rule_style[{'frame_color': 'border-color'}.get(key, key)] = value
            elif key == 'new_icon':
                rule['icon'] = {'name': value}
            elif key == 'set_stack_tag':
                rule['stack-tag'] = value
            elif key == 'script':
                rule['run'] = value
            elif key == 'set_transient':
                if _bool(value):
                    report.append(f'[{name}] set_transient: not supported (ignored)')
            elif key in ('enabled', 'match_type', 'matching'):
                continue
            else:
                report.append(f'[{name}] {key}: no notifyd equivalent (ignored)')
        if rule_style:
            rule['style'] = rule_style
        rule['match'] = match
        rules.append({'name': rule.pop('name'), 'match': rule.pop('match'), **rule})
    if rules:
        out['rules'] = rules
    preset_style = load_preset('dunst')['style']
    base_background = _normalize(style.get('background', preset_style.get('background')))
    for urgency in ('low', 'critical'):
        part = style.get(urgency, {})
        if _normalize(part.get('background')) == base_background:
            part.pop('background')
        if urgency in style and not part:
            style.pop(urgency)
    if style:
        stripped = strip_preset_values(style, preset_style)
        if stripped:
            out['style'] = stripped
    return out, report
