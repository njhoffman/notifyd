"""Notification text: Pango markup, preset-named styles, and safe fallbacks.

Senders write Pango markup (the spec's <b> <i> <u> <a> <img>, plus <span> as dunst renders it).
A span can name a style of the preset instead of spelling out its attributes:

    <span class="accent">text</span>   ->  <span foreground="#7aa2f7" weight="bold">text</span>

with `style.classes.accent: {foreground: '#7aa2f7', weight: bold}` in the preset or config.
Markup that does not parse is shown as plain text rather than dropped.
"""

import html
import re

import gi

gi.require_version('Pango', '1.0')
from gi.repository import GLib, Pango  # noqa: E402

_CLASS_ATTR = re.compile(r'''\sclass\s*=\s*(["'])([^"']*)\1''')
_SPAN_OPEN = re.compile(r'<span\b[^>]*>', re.I)
_BR = re.compile(r'<br\s*/?>', re.I)
_IMG = re.compile(r'<img\b[^>]*>', re.I)
_ALT = re.compile(r'''\balt\s*=\s*(["'])(.*?)\1''', re.I | re.S)
_BARE_AMP = re.compile(r'&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#[0-9]+|#x[0-9a-fA-F]+);)')
# Pango span attributes (and the short forms) a class may set
SPAN_ATTRS = {
    'font', 'font_desc', 'font_family', 'face', 'size', 'font_size', 'style', 'weight', 'variant', 'stretch',
    'foreground', 'fgcolor', 'color', 'background', 'bgcolor', 'alpha', 'fgalpha', 'bgalpha',
    'underline', 'underline_color', 'overline', 'strikethrough', 'strikethrough_color', 'rise',
    'letter_spacing', 'font_features', 'allow_breaks', 'insert_hyphens', 'baseline_shift', 'font_scale',
    'text_transform',
}


def escape(text):
    return GLib.markup_escape_text(text or '')


def parses(text):
    try:
        Pango.parse_markup(text, -1, '\0')
        return True
    except GLib.Error:
        return False


_TAG = re.compile(r'<[^>]*>')


def plain_text(markup):
    """The text of MARKUP without tags (also when it is not valid markup)."""
    try:
        return Pango.parse_markup(markup, -1, '\0')[2]
    except GLib.Error:
        return html.unescape(_TAG.sub('', markup))


def expand_classes(text, classes):
    """Replace class="a b" on spans with the attributes CLASSES gives those names."""
    if 'class' not in text:
        return text

    def span(found):
        tag = found.group(0)
        attr = _CLASS_ATTR.search(tag)
        if not attr:
            return tag
        attrs = {}
        for name in attr.group(2).split():
            for key, value in (classes or {}).get(name, {}).items():
                key = str(key).replace('-', '_')
                if key in SPAN_ATTRS:
                    attrs[key] = value
        rendered = ''.join(f' {key}="{escape(str(value))}"' for key, value in attrs.items())
        return tag[:attr.start()] + rendered + tag[attr.end():]
    return _SPAN_OPEN.sub(span, text)


def _alt_text(found):
    alt = _ALT.search(found.group(0))
    return alt.group(2) if alt else ''


def prepare(text, mode='full', classes=None):
    """TEXT as Pango markup ready for a label, in MODE: full, strip (text only) or plain (as typed)."""
    text = text or ''
    if mode == 'plain':
        return escape(text)
    text = _BR.sub('\n', text)
    text = _IMG.sub(_alt_text, text)
    text = expand_classes(text, classes)
    if not parses(text):
        fixed = _BARE_AMP.sub('&amp;', text)   # "Tom & Jerry" is the usual culprit
        text = fixed if parses(fixed) else escape(text)
    if mode == 'strip':
        return escape(plain_text(text))
    return text
