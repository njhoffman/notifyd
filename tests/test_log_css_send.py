import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from notifyd.config import load_preset
from notifyd.log import EventLog, format_pretty, one_line
from notifyd.model import Notification
from notifyd.render.css import build_css, style_class
from notifyd.send import (
    SendError,
    build_hints,
    build_parser,
    expand_multi,
    image_uri,
    parse_actions,
    parse_hint,
    standalone_main,
    unescape,
)

ROOT = Path(__file__).resolve().parent.parent

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


def test_pretty_line_names_the_sender_when_shown():
    n = sample()
    assert 'sender' not in n.summary_dict()                    # unknown: left out
    n.sender = {'pid': 42, 'process': 'notify-send', 'parent': 'musicman'}
    shown = format_pretty({'event': 'shown', 'time': 0, 'n': n.summary_dict()})
    assert shown.rstrip().endswith('[backdrop]  via notify-send (musicman)')
    assert 'via' not in format_pretty({'event': 'expired', 'time': 0, 'n': n.summary_dict()})
    n.sender = {'pid': 42, 'process': 'backdrop'}
    assert format_pretty({'event': 'shown', 'time': 0, 'n': n.summary_dict()}).rstrip().endswith('via backdrop')


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


def test_title_end_is_quieter_by_default_and_takes_its_own_style():
    css = build_css({'title': {'weight': 'bold'}}, 'nd-x')
    assert ('.nd-x .notification .title-end { font-weight: normal; font-style: normal; font-size: 85%; '
            'opacity: 0.65; }') in css
    css = build_css({'title-end': {'size': 12, 'color': '#888', 'opacity': 1}}, 'nd-x')
    assert '.nd-x .notification .title-end { font-size: 12px; color: #888; opacity: 1; }' in css


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



def test_image_uri_takes_icon_names_and_relative_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert image_uri('dialog-information') == 'dialog-information'     # no slash, no such file: an icon name
    (tmp_path / 'cover.png').write_bytes(b'')
    assert image_uri('cover.png') == (tmp_path / 'cover.png').as_uri()
    assert image_uri('./missing.png') == (tmp_path / 'missing.png').as_uri()


def test_parse_hint_variant():
    name, variant = parse_hint('variant:x-list:[1, 2]')
    assert name == 'x-list' and variant.get_type_string() == 'ai' and variant.unpack() == [1, 2]
    assert parse_hint('variant:x:<int32 5>')[1].unpack() == 5
    with pytest.raises(SendError, match='GVariant'):
        parse_hint('variant:x:[1,')


@pytest.mark.parametrize('argv, expected', [
    # notify-send.py: several values after one --hint / --action
    (['--hint', 'int:a:1', 'string:b:x y', 'Sum'], ['--hint', 'int:a:1', '--hint', 'string:b:x y', 'Sum']),
    (['Sum', 'Body', '--action', 'yes:Yes', 'no:No'], ['Sum', 'Body', '--action', 'yes:Yes', '--action', 'no:No']),
    # the first value is taken as is; later ones must look like one
    (['--action', 'keep=Keep', 'Monitors'], ['--action', 'keep=Keep', 'Monitors']),
    (['--action', 'a:A', 'Build: done'], ['--action', 'a:A', 'Build: done']),     # space after the colon
    (['--hint', 'int:a:1', 'float:b:1'], ['--hint', 'int:a:1', 'float:b:1']),      # not a hint type
    (['--hint', 'int:a:1', '--', 'string:b:c'], ['--hint', 'int:a:1', '--', 'string:b:c']),
    (['-h', 'int:a:1', 'string:b:c'], ['-h', 'int:a:1', 'string:b:c']),             # short flags take one
    (['--hint'], ['--hint']),
])
def test_expand_multi(argv, expected):
    assert expand_multi(argv) == expected


def test_unescape():
    assert unescape(r'a\nb\tc') == 'a\nb\tc'
    assert unescape(r'C:\\new') == 'C:\\new'                 # \\ is one backslash, and the n stays an n
    assert unescape(r'trailing\\') == 'trailing\\'
    assert unescape('no escapes') == 'no escapes'


def test_build_hints_from_notifyd_flags(tmp_path):
    args = build_parser().parse_args(['s', '--title-end', '★ 4', '--desktop-entry', 'mpv', '--sound-name', 'bell',
                                      '--sound-file', str(tmp_path / 'a.oga'), '--suppress-sound', '--resident',
                                      '--fg', '#fff', '--bg', '#000', '--frame', '#f00', '--highlight', '#0f0'])
    hints = {k: v.unpack() for k, v in build_hints(args).items()}
    assert hints == {'x-notifyd-title-end': '★ 4', 'desktop-entry': 'mpv', 'sound-name': 'bell',
                     'sound-file': str(tmp_path / 'a.oga'), 'suppress-sound': True, 'resident': True,
                     'fgcolor': '#fff', 'bgcolor': '#000', 'frcolor': '#f00', 'hlcolor': '#0f0'}
    assert build_hints(build_parser().parse_args(['s'])) == {}


def test_notify_send_py_spellings():
    args = build_parser().parse_args(['s', '--replaces-id', '7', '--replaces-process', 'job', '--dontQuitOnAction'])
    assert args.replace_id == 7 and args.replace_name == 'job' and args.dont_quit


def test_standalone_parser(capsys):
    flags = {f for a in build_parser('notifyd-send', standalone=True)._actions for f in a.option_strings}
    assert '--completion' in flags
    assert '--completion' not in {f for a in build_parser()._actions for f in a.option_strings}
    with pytest.raises(SystemExit):
        standalone_main(['--version'])
    assert capsys.readouterr().out.startswith('notifyd-send ')
    assert standalone_main(['--completion', 'zsh']) == 0
    out = capsys.readouterr().out
    assert out.startswith('#compdef notifyd-send') and 'compdef _notifyd-send notifyd-send' in out


def test_send_ids_stay_in_a_directory_of_ours(tmp_path, monkeypatch):
    from notifyd import send
    runtime = tmp_path / 'run'
    runtime.mkdir()
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(runtime))
    send.save_id('backup', 7)
    assert send.load_ids() == {'backup': 7}
    assert oct(os.stat(runtime / 'notifyd').st_mode & 0o777) == '0o700'
    (runtime / 'notifyd' / 'send-ids.json').write_text('{"a": "x", "b": 3, "c": -1, "d": true}')
    assert send.load_ids() == {'b': 3}                             # only usable ids
    (runtime / 'notifyd' / 'send-ids.json').write_text('[]')
    assert send.load_ids() == {}


def test_send_ids_refuse_a_planted_directory(tmp_path, monkeypatch):
    from notifyd import send
    runtime = tmp_path / 'run'
    runtime.mkdir()
    target = tmp_path / 'victim'
    target.write_text('precious')
    (runtime / 'notifyd').symlink_to(tmp_path / 'elsewhere', target_is_directory=True)
    (tmp_path / 'elsewhere').mkdir()
    (tmp_path / 'elsewhere' / 'send-ids.json').symlink_to(target)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(runtime))
    send.save_id('backup', 7)
    assert target.read_text() == 'precious' and send.load_ids() == {}


def test_concurrent_send_ids_are_all_kept(tmp_path):
    code = ('import sys; sys.path.insert(0, sys.argv[1]); from notifyd import send; '
            'send.save_id(sys.argv[2], int(sys.argv[3]))')
    env = {**os.environ, 'XDG_RUNTIME_DIR': str(tmp_path)}
    procs = [subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'src'), f'n{i}', str(i + 1)], env=env)
             for i in range(12)]
    for proc in procs:
        assert proc.wait(20) == 0
    ids = json.loads((tmp_path / 'notifyd' / 'send-ids.json').read_text())
    assert ids == {f'n{i}': i + 1 for i in range(12)}


def test_the_log_file_is_private(tmp_path):
    from notifyd.log import EventLog
    path = tmp_path / 'notifyd.log'
    log = EventLog(stream=io.StringIO(), color='never')
    log.configure(color='never', file=str(path))
    log.info('a body with 123456')
    assert path.exists() and oct(os.stat(path).st_mode & 0o777) == '0o600'


@pytest.mark.parametrize('argv', [['-r', '-5', 'x'], ['--close', '-1'], ['-t', '3000000000', 'x'],
                                  ['-r', '99999999999', 'x'], ['-t', 'soon', 'x']])
def test_send_refuses_numbers_out_of_range(argv, capsys):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(argv)
    assert exit_info.value.code == 2 and 'error:' in capsys.readouterr().err


@pytest.mark.parametrize('hint', ['byte:x:300', 'int:x:3000000000', 'int:x:1e11', 'byte:x:-1'])
def test_send_hints_out_of_range(hint):
    with pytest.raises(SendError):
        parse_hint(hint)
