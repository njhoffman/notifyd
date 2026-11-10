"""GTK CSS from a preset's style mapping (no toolkit import, so it is testable anywhere).

Style keys, all optional:

    font-family, font-size, background, foreground, radius, border-width, border-color,
    padding: [vertical, horizontal] or one number, spacing (between rows),
    title / body / appname / time: { size, weight, style, color, family },
    low / normal / critical: { background, foreground, border-color },
    progress: { color, background, height },
    button: { background, foreground, hover },
    classes: { name: { Pango span attributes } }   (used by markup, not CSS)
    css: extra raw CSS, appended; `&` in it stands for the style's scope class

Every rule is scoped to the style's own class, so notifications with different styles (a rule
picking another preset, a preview) can be on screen together.
"""

import hashlib
import json

from notifyd.config import deep_merge

PART_PROPS = {'size': 'font-size', 'weight': 'font-weight', 'style': 'font-style', 'color': 'color',
              'family': 'font-family'}


def px(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'{value}px'
    return str(value)


def padding(value):
    if isinstance(value, (list, tuple)):
        return ' '.join(px(v) for v in value)
    return px(value)


def font_family(value):
    value = str(value)
    return value if value.startswith(('"', "'")) or ',' in value else f'"{value}"'


def declarations(props):
    return ' '.join(f'{key}: {value};' for key, value in props.items() if value is not None)


def block(selector, props):
    body = declarations(props)
    return f'{selector} {{ {body} }}\n' if body else ''


def part_props(part):
    props = {}
    for key, value in (part or {}).items():
        prop = PART_PROPS.get(key)
        if prop == 'font-size':
            value = px(value)
        elif prop == 'font-family':
            value = font_family(value)
        if prop:
            props[prop] = value
    return props


def style_class(style):
    digest = hashlib.sha1(json.dumps(style, sort_keys=True, default=str).encode()).hexdigest()
    return f'nd-{digest[:10]}'


def resolve_style(preset_style, overrides):
    return deep_merge(preset_style or {}, overrides or {})


def build_css(style, scope):
    """The CSS for STYLE under class SCOPE."""
    s = f'.{scope}'
    card = f'{s} .notification'
    css = block(f'window{s}', {'background-color': 'transparent'})
    base = {
        'background-color': style.get('background'),
        'color': style.get('foreground'),
        'border-radius': px(style['radius']) if 'radius' in style else None,
        'border-style': 'solid' if style.get('border-width') else None,
        'border-width': px(style['border-width']) if 'border-width' in style else None,
        'border-color': style.get('border-color'),
        'padding': padding(style['padding']) if 'padding' in style else None,
        'font-family': font_family(style['font-family']) if 'font-family' in style else None,
        'font-size': px(style['font-size']) if 'font-size' in style else None,
    }
    css += block(card, base)
    if style.get('foreground'):
        css += block(f'{card} label', {'color': style['foreground']})
    if 'spacing' in style:
        css += block(f'{card} .content, {card} .header', {'margin-bottom': px(style['spacing'])})
    for urgency in ('low', 'normal', 'critical'):
        part = style.get(urgency) or {}
        css += block(f'{card}.{urgency}', {
            'background-color': part.get('background'),
            'border-color': part.get('border-color'),
            'color': part.get('foreground'),
        })
        if part.get('foreground'):
            css += block(f'{card}.{urgency} label', {'color': part['foreground']})
    for name in ('title', 'body', 'appname', 'time'):
        css += block(f'{card} .{name}', part_props(style.get(name)))
    progress = style.get('progress') or {}
    height = px(progress.get('height', 6))
    css += block(f'{card} progressbar trough', {
        'background-color': progress.get('background', 'rgba(255, 255, 255, 0.15)'),
        'min-height': height, 'border': 'none', 'background-image': 'none'})
    css += block(f'{card} progressbar progress', {
        'background-color': progress.get('color', style.get('foreground')),
        'min-height': height, 'border': 'none', 'background-image': 'none'})
    button = style.get('button') or {}
    css += block(f'{card} button', {
        'background-color': button.get('background', 'rgba(255, 255, 255, 0.08)'),
        'color': button.get('foreground', style.get('foreground')),
        'background-image': 'none', 'border': 'none', 'box-shadow': 'none', 'text-shadow': 'none',
        'border-radius': px(button.get('radius', 3)), 'padding': '2px 8px'})
    css += block(f'{card} button label', {'color': button.get('foreground', style.get('foreground'))})
    css += block(f'{card} button:hover', {'background-color': button.get('hover', 'rgba(255, 255, 255, 0.18)')})
    if style.get('css'):
        css += str(style['css']).replace('&', s) + '\n'
    return css
