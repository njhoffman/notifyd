import io
import json

import pytest

from notifyd.config import load_preset
from notifyd.log import EventLog, format_pretty, one_line
from notifyd.model import Notification
from notifyd.render.css import build_css, style_class
from notifyd.send import SendError, build_hints, build_parser, image_uri, parse_actions, parse_hint

# -- log -------------------------------------------------------------------

def sample():
    n = Notification(app='backdrop', summary='#412 video', body='<b>0:18</b> · 25 fps\nocean', id=12,
                     hints={'urgency': 0})
    n.rules, n.timeout = ['backdrop'], 12000
    return n


def test_pretty_line_without_color():
    line = format_pretty({'event': 'shown', 'time': 0, 'n': sample().summary_dict()})
    assert 'shown' in line and '#12' in line and 'backdrop' in line and 'low' in line
    assert '#412 video | 0:18 · 25 fps | ocean' in line   # markup stripped, lines joined
    assert line.rstrip().endswith('12s  [backdrop]')
    assert '\033[' not in line


def test_pretty_line_with_color_has_ansi():
    assert '\033[' in format_pretty({'event': 'shown', 'time': 0, 'n': sample().summary_dict()}, color=True)


def test_one_line_truncates():
    assert one_line('a' * 100, 10) == 'a' * 9 + '…'


def test_json_format_and_listeners():
    stream = io.StringIO()
    log = EventLog(fmt='json', stream=stream, color='never')
    seen = []
    log.listeners.append(seen.append)
    log.emit('shown', sample())
    event = json.loads(stream.getvalue())
    assert event['event'] == 'shown' and event['n']['id'] == 12
    assert seen[0]['n']['summary'] == '#412 video'


def test_level_filters_output_but_not_listeners():
    stream = io.StringIO()
    log = EventLog(stream=stream, color='never', level='warn')
    seen = []
    log.listeners.append(seen.append)
    log.info('quiet')
    log.warn('loud')
    assert 'quiet' not in stream.getvalue() and 'loud' in stream.getvalue()
    assert len(seen) == 2


def test_no_color_env(monkeypatch):
    monkeypatch.setenv('NO_COLOR', '1')
    assert EventLog(stream=io.StringIO(), color='auto').color is False
    assert EventLog(stream=io.StringIO(), color='always').color is True


# -- css -------------------------------------------------------------------

def test_deadd_css_is_scoped_and_has_the_deadd_values():
    style = load_preset('deadd')['style']
    scope = style_class(style)
    css = build_css(style, scope)
    assert scope.startswith('nd-')
    assert f'.{scope} .notification {{ background-color: rgba(0, 0, 0, 0.8); color: #acf;' in css
    assert f'.{scope} .notification .title {{ font-size: 16px; font-weight: bold; }}' in css
    assert f'.{scope} .notification.critical {{ background-color: rgba(255, 0, 0, 0.5); color: #faa; }}' in css
    assert 'border-style' not in css.split('.notification {')[1].split('}')[0]   # no frame


def test_every_preset_builds_css_gtk_accepts():
    gi = pytest.importorskip('gi')
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk as gi_gtk

    from notifyd.config import preset_names
    for name in preset_names():
        style = load_preset(name)['style']
        provider = gi_gtk.CssProvider()
        provider.load_from_data(build_css(style, style_class(style)).encode())   # raises on a parse error


def test_raw_css_ampersand_is_the_scope():
    css = build_css({'css': '& .title { letter-spacing: 1px; }'}, 'nd-x')
    assert '.nd-x .title { letter-spacing: 1px; }' in css


def test_different_styles_get_different_classes():
    assert style_class({'a': 1}) != style_class({'a': 2})
    assert style_class({'a': 1, 'b': 2}) == style_class({'b': 2, 'a': 1})


# -- send ------------------------------------------------------------------

@pytest.mark.parametrize('text, name, signature, value', [
    ('int:value:42', 'value', 'i', 42),
    ('string:x-dunst-stack-tag:vol', 'x-dunst-stack-tag', 's', 'vol'),
    ('string:image-path:file:///a/b.png', 'image-path', 's', 'file:///a/b.png'),   # colons in the value
    ('boolean:transient:true', 'transient', 'b', True),
    ('byte:urgency:2', 'urgency', 'y', 2),
    ('double:x:1.5', 'x', 'd', 1.5),
])
def test_parse_hint(text, name, signature, value):
    hint, variant = parse_hint(text)
    assert hint == name and variant.get_type_string() == signature and variant.unpack() == value


@pytest.mark.parametrize('text', ['int:value', 'float:x:1', 'int:value:lots', 'boolean:b:maybe'])
def test_parse_hint_rejects(text):
    with pytest.raises(SendError):
        parse_hint(text)


def test_parse_actions_accepts_both_syntaxes():
    assert parse_actions(['change=Change layout', 'keep:Keep', 'Plain']) == \
        ['change', 'Change layout', 'keep', 'Keep', '2', 'Plain']


def test_build_hints_from_flags(tmp_path):
    picture = tmp_path / 'a b.png'
    picture.write_bytes(b'')
    args = build_parser().parse_args(['s', '-u', 'critical', '-c', 'im', '-e', '--tag', 't', '--progress', '150',
                                      '--image', str(picture), '-h', 'string:x:y'])
    hints = {k: v.unpack() for k, v in build_hints(args).items()}
    assert hints == {'urgency': 2, 'category': 'im', 'transient': True, 'x-dunst-stack-tag': 't', 'value': 100,
                     'image-path': picture.as_uri(), 'x': 'y'}
    assert '%20' in hints['image-path']


def test_image_uri_keeps_uris():
    assert image_uri('file:///x.png') == 'file:///x.png'

