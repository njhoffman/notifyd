"""Notification text: Pango markup, preset-named styles, and safe fallbacks.

Senders write Pango markup (the spec's <b> <i> <u> <a> <img>, plus <span> as dunst renders it).
A span can name a style of the preset instead of spelling out its attributes:

    <span class="accent">text</span>   ->  <span foreground="#7aa2f7" weight="bold">text</span>

with `style.classes.accent: {foreground: '#7aa2f7', weight: bold}` in the preset or config.
Markup that does not parse is shown as typed rather than dropped, and `markup_error` says why, so
the popup and the center can flag it instead of silently showing tags.
"""

import html
import html.entities
import re

import gi

gi.require_version('Pango', '1.0')
from gi.repository import GLib, Pango  # noqa: E402

_CLASS_ATTR = re.compile(r'''\sclass\s*=\s*(["'])([^"']*)\1''')
_SPAN_OPEN = re.compile(r'<span\b[^>]*>', re.I)
_BR = re.compile(r'<br\s*/?>', re.I)
# HTML block tags web apps send: a closing one ends a line, an opening one goes (other unknown tags
# still make the text show as typed, flagged)
_BLOCK_OPEN = re.compile(r'<(?:p|div)(?:\s[^<>]*)?>', re.I)
_BLOCK_CLOSE = re.compile(r'</(?:p|div)\s*>', re.I)
_IMG = re.compile(r'<img\b[^>]*>', re.I)
_ALT = re.compile(r'''\balt\s*=\s*(["'])(.*?)\1''', re.I | re.S)
_BARE_AMP = re.compile(r'&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#[0-9]+|#x[0-9a-fA-F]+);)')
_NAMED_ENTITY = re.compile(r'&([a-zA-Z][a-zA-Z0-9]*);')
_XML_ENTITIES = {'amp', 'lt', 'gt', 'quot', 'apos'}
_BARE_LT = re.compile(r'<(?![A-Za-z/!?])')   # "a < b", "<3": cannot start a tag
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


# <a href="..." [title="..."]>: GtkLabel takes it, Pango does not know it
_A_TAG = re.compile(r'''<(?:a((?:\s+(?:href|title)\s*=\s*(?:"[^"]*"|'[^']*'))+)\s*|(/a)\s*)>''')
_HREF = re.compile(r'''\shref\s*=\s*(["'])(.*?)\1''', re.S)
LINK_SCHEMES = ('http', 'https', 'mailto')


def pango_view(text):
    """TEXT with its links as spans, as Pango sees what GtkLabel shows. A link without an href, a
    nested one or a stray </a> is left as it is, so the text does not parse (GtkLabel refuses those)."""
    depth = 0

    def swap(found):
        nonlocal depth
        if found.group(2):          # </a>
            if depth != 1:
                return found.group(0)
            depth = 0
            return '</span>'
        # attribute values are markup too: a bare & in one fails here, and _normalize escapes it
        if depth or not _HREF.search(found.group(1)) or _BARE_AMP.search(found.group(1)) or '<' in found.group(1):
            return found.group(0)
        depth = 1
        return '<span>'
    return _A_TAG.sub(swap, text) if '<a' in text.lower() else text


def parses(text):
    try:
        Pango.parse_markup(pango_view(text), -1, '\0')
        return True
    except GLib.Error:
        return False


_TAG = re.compile(r'<[^>]*>')


def plain_text(markup):
    """The text of MARKUP without tags (also when it is not valid markup)."""
    try:
        return Pango.parse_markup(pango_view(markup), -1, '\0')[2]
    except GLib.Error:
        return html.unescape(_TAG.sub('', markup))


def safe_link(uri):
    """Whether a clicked link may be opened: only http(s) and mailto, never file: and the like."""
    scheme, colon, _rest = (uri or '').partition(':')
    return bool(colon) and scheme.lower() in LINK_SCHEMES


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


def _entity(found):
    """An HTML entity Pango does not know (&nbsp; &hellip;) as a number; an unknown one as text."""
    name = found.group(1)
    if name in _XML_ENTITIES:
        return found.group(0)
    code = html.entities.name2codepoint.get(name)
    return f'&#{code};' if code else f'&amp;{name};'


def _normalize(text, classes):
    """TEXT with <br>, <img>, HTML entities and span classes turned into Pango, and a bare & or <
    fixed when that helps."""
    text = _BR.sub('\n', text or '')
    if _BLOCK_CLOSE.search(text) or _BLOCK_OPEN.search(text):
        text = _BLOCK_OPEN.sub('', _BLOCK_CLOSE.sub('\n', text)).strip('\n')
    if '&' in text:
        text = _NAMED_ENTITY.sub(_entity, text)
    text = _IMG.sub(_alt_text, text)
    text = expand_classes(text, classes)
    if not parses(text):
        fixed = _BARE_LT.sub('&lt;', _BARE_AMP.sub('&amp;', text))   # "Tom & Jerry", "a < b"
        if parses(fixed):
            return fixed
    return text


def markup_error(text, mode='full', classes=None):
    """Why TEXT is not valid markup after the usual fixes ('' when it is, or in plain mode)."""
    if mode == 'plain' or not text or ('<' not in text and '&' not in text):
        return ''
    try:
        Pango.parse_markup(pango_view(_normalize(text, classes)), -1, '\0')
        return ''
    except GLib.Error as exc:
        return exc.message or 'invalid markup'


def prepare(text, mode='full', classes=None):
    """TEXT as Pango markup ready for a label, in MODE: full, strip (text only) or plain (as typed)."""
    text = text or ''
    if mode == 'plain':
        return escape(text)
    text = _normalize(text, classes)
    if not parses(text):
        text = escape(text)
    if mode == 'strip':
        return escape(plain_text(text))
    return text


_OPEN_OR_CLOSE = re.compile(r'<(/?)([a-zA-Z]+)\b[^>]*?(/?)>')


def cap_lines(markup, lines):
    """MARKUP cut to its first LINES lines (paragraphs) with an ellipsis, open tags closed, or None
    when it has no more than that. Pango's line limit counts per paragraph, so a body of many short
    lines would otherwise show them all."""
    if lines <= 0 or markup.count('\n') < lines:
        return None
    kept = '\n'.join(markup.split('\n')[:lines])
    stack = []
    for found in _OPEN_OR_CLOSE.finditer(kept):
        closing, name, empty = found.groups()
        if empty:
            continue
        if closing:
            if stack and stack[-1] == name:
                stack.pop()
        else:
            stack.append(name)
    capped = kept + '…' + ''.join(f'</{name}>' for name in reversed(stack))
    return capped if parses(capped) else escape(plain_text(kept)) + '…'

