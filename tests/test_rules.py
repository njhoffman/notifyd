import pytest

from notifyd.config import ConfigError, load_config
from notifyd.model import Notification
from notifyd.rules import hint_style, resolve


def resolved(config_text, **fields):
    hints = fields.pop('hints', {})
    n = Notification(hints=hints, **fields)
    return resolve(load_config(text=config_text), n)


SONGS = '''
rules:
  - name: songs
    match: {app: [musicman, MPD, Spotify]}
    timeout: 12s
    hide: [app-icon, app-name]
    image: {size: 60}
'''


def test_list_match_applies_settings_and_timeout():
    n = resolved(SONGS, app='MPD', summary='Song')
    assert n.rules == ['songs']
    assert n.timeout == 12000
    assert n.settings['hide'] == ['app-icon', 'app-name']
    assert n.settings['image'] == {'size': 60, 'path': None, 'margin-right': 0}


def test_no_match_keeps_defaults():
    n = resolved(SONGS, app='firefox')
    assert n.rules == []
    assert n.timeout == 8000
    assert n.settings['hide'] == []


def test_rule_timeout_wins_over_the_sender():
    # musicman sends --expire-time=5000; the rule must still make it 12s
    assert resolved(SONGS, app='MPD', requested_timeout=5000).timeout == 12000


def test_sender_timeout_used_without_a_rule_timeout():
    assert resolved('', app='x', requested_timeout=3000).timeout == 3000
    assert resolved('', app='x', requested_timeout=0).timeout == 0   # 0: never expires (spec)


def test_critical_defaults_to_never():
    assert resolved('', app='x', hints={'urgency': 2}).timeout == 0
    assert resolved('timeout-critical: 30s', app='x', hints={'urgency': 2}).timeout == 30000


def test_regex_match_with_flags_and_captures():
    config = '''
rules:
  - match: {summary: '/^(?P<pct>\\d+)% battery/i'}
    urgency: critical
    summary: 'Battery {pct}%'
    body: '{body} (from {app})'
'''
    n = resolved(config, app='upower', summary='5% BATTERY left', body='plug in')
    assert n.summary == 'Battery 5%'
    assert n.body == 'plug in (from upower)'
    assert n.urgency_name == 'critical'


def test_unknown_template_fields_stay_as_written():
    config = "rules:\n  - match: {app: x}\n    summary: '{nope} {summary}'\n"
    assert resolved(config, app='x', summary='s').summary == '{nope} s'


def test_match_on_urgency_category_and_hints():
    config = '''
rules:
  - name: low-mail
    match: {urgency: low, category: email.arrived, hint: {x-mail-account: work}}
    skip: true
'''
    hit = resolved(config, app='mail', hints={'urgency': 0, 'category': 'email.arrived', 'x-mail-account': 'work'})
    miss = resolved(config, app='mail', hints={'urgency': 1, 'category': 'email.arrived', 'x-mail-account': 'work'})
    assert hit.settings['skip'] is True and hit.rules == ['low-mail']
    assert miss.settings['skip'] is False


def test_boolean_hint_match():
    config = 'rules:\n  - match: {hint: {resident: true}}\n    class: sticky\n'
    assert resolved(config, hints={'resident': True}).settings['class'] == 'sticky'
    assert resolved(config, hints={'resident': False}).settings['class'] is None


def test_later_rules_override_and_see_rewrites():
    config = '''
rules:
  - match: {app: Spotify}
    app: musicman
    timeout: 3s
  - match: {app: musicman}
    timeout: 12s
    style: {foreground: '#fff'}
'''
    n = resolved(config, app='Spotify')
    assert n.app == 'musicman'
    assert n.rules == ['Spotify', 'musicman']
    assert n.timeout == 12000
    assert n.settings['style'] == {'foreground': '#fff'}


def test_stack_tag_rewrite():
    n = resolved("rules:\n  - match: {app: Volume}\n    stack-tag: 'vol-{app}'\n", app='Volume')
    assert n.stack_tag == 'vol-Volume'


def test_extra_settings_win_for_previews():
    n = resolve(load_config(text=SONGS), Notification(app='MPD'), extra={'preset': 'card', 'timeout': 15000})
    assert n.settings['preset'] == 'card'
    assert n.timeout == 15000


@pytest.mark.parametrize('rule', [
    "- match: {appname: x}",            # unknown match field
    "- match: {app: x}\n    colour: red",   # unknown setting
    "- match: {summary: '/[/'}",        # bad regex
    "- match: {app: x}\n    urgency: urgent",
    "- match: {app: x}\n    timeout: soon",
    "- match: [x]",
    "- just-a-string",
])
def test_bad_rules_raise(rule):
    with pytest.raises(ConfigError):
        load_config(text='rules:\n  ' + rule + '\n')


def test_dunst_color_hints_become_style_and_rules_override_them():
    n = resolved('', app='x', hints={'fgcolor': '#ff0000', 'bgcolor': '#000000', 'frcolor': '#00ff00',
                                    'hlcolor': '#0000ff'})
    style = n.settings['style']
    assert {k: style[k] for k in ('foreground', 'background', 'border-color', 'progress')} == \
        {'foreground': '#ff0000', 'background': '#000000', 'border-color': '#00ff00', 'progress': {'color': '#0000ff'}}
    ruled = resolved("rules:\n  - match: {app: x}\n    style: {foreground: '#fff'}\n", app='x',
                     hints={'fgcolor': '#ff0000'})
    assert ruled.settings['style'] == {'foreground': '#fff'}      # the rule's foreground wins, parts and all


def test_fgcolor_colors_every_part_and_urgency():
    style = resolved('', app='x', hints={'fgcolor': '#123456'}).settings['style']
    for part in ('title', 'title-end', 'body', 'count', 'appname', 'time'):
        assert style[part]['color'] == '#123456', part
    assert style['appname']['opacity'] < 1 and 'opacity' not in style['title']
    assert {u: style[u] for u in ('low', 'normal', 'critical')} == {u: {'foreground': '#123456'}
                                                                    for u in ('low', 'normal', 'critical')}
    # a rule's part color, or its size, still goes on top
    ruled = resolved("rules:\n  - match: {app: x}\n    style: {title: {color: '#abcdef', size: 20px}}\n", app='x',
                     hints={'fgcolor': '#123456'}).settings['style']
    assert ruled['title'] == {'color': '#abcdef', 'size': '20px'} and ruled['body']['color'] == '#123456'


def test_fgcolor_beats_the_preset_part_colors_in_css():
    from notifyd.config import load_preset
    from notifyd.render.css import build_css, resolve_style
    n = resolved('preset: tokyonight\n', app='x', hints={'fgcolor': '#123456'})
    style = resolve_style(load_preset('tokyonight')['style'], n.settings['style'])   # as the renderer does
    assert style['title']['color'] == '#123456' and style['appname']['color'] == '#123456'
    assert style['title']['size'] == '14px'                                       # the rest of the preset stays
    css = build_css(style, '.n')
    for preset_color in ('#c0caf5', '#7dcfff', '#565f89'):              # title, appname, time
        assert preset_color not in css.lower(), preset_color
    assert '.critical label { color: #123456; }' in css                 # critical text too (its frame stays red)


def test_match_on_the_sending_process_and_its_parent():
    config = """
rules:
  - name: client
    match: {app: Spotify, process: spotify}
    skip: true
  - name: via-musicman
    match: {parent: /^musicman/}
    timeout: 12s
"""
    client = resolved(config, app='Spotify', sender={'pid': 1, 'process': 'spotify'})
    assert client.rules == ['client'] and client.settings['skip']
    songs = resolved(config, app='Spotify', sender={'pid': 2, 'process': 'notify-send', 'parent': 'musicman'})
    assert songs.rules == ['via-musicman'] and not songs.settings.get('skip') and songs.timeout == 12000
    unknown = resolved(config, app='Spotify')                 # no sender: matches no process rule
    assert unknown.rules == []


@pytest.mark.parametrize('rule', [
    "- match: {app: x}\n    actions: {drop: '/[/'}",       # bad regex
    "- match: {app: x}\n    actions: {drop: [1]}",
    "- match: {app: x}\n    actions: [settings]",
])
def test_bad_action_drops_raise(rule):
    with pytest.raises(ConfigError):
        load_config(text='rules:\n  ' + rule + '\n')


def test_one_action_to_drop_may_be_given_alone():
    n = resolved("rules:\n  - match: {app: x}\n    actions: {drop: settings}\n", app='x')
    assert n.settings['actions'] == {'drop': ['settings']}


@pytest.mark.parametrize('rule', [
    "- match: {app: x}\n    run-on: [arrive]",
    "- match: {app: x}\n    run-on: {close: true}",
])
def test_bad_run_on_raises(rule):
    with pytest.raises(ConfigError):
        load_config(text='rules:\n  ' + rule + '\n')


@pytest.mark.parametrize('value, css', [
    ('#e0af68', '#e0af68'), ('#f00', '#f00'), (' red ', 'red'), ('rgba(1, 2, 3, 0.5)', 'rgba(1, 2, 3, 0.5)'),
    ('#ff000080', 'rgba(255, 0, 0, 0.502)'),                 # dunst's #rrggbbaa: GTK 3 CSS lacks it
    ('#f008', 'rgba(255, 0, 0, 0.533)'),
    ('red; } * { opacity: 0; } .zz {', None),                 # a sender adding rules of its own
    ('red}}{{', None), ('url(file:///x)', None), ('', None), (5, None),
])
def test_color_hints_must_be_colors(value, css):
    from notifyd.rules import css_color
    assert css_color(value) == css
    style = hint_style({'bgcolor': value})
    assert style.get('background') == css


def test_css_values_cannot_end_their_rule():
    from notifyd.render.css import declarations
    assert declarations({'color': 'red', 'background-color': 'red; } * { opacity: 0', 'border': None,
                         'font-family': '"x" /* y */'}) == 'color: red;'


def test_a_rules_body_mapping_is_the_setting_not_a_rewrite():
    n = resolved("rules:\n  - match: {app: x}\n    body: {max-lines: 2}\n", app='x', body='kept as sent')
    assert n.settings['body'] == {'max-lines': 2} and n.body == 'kept as sent'
    n = resolved("rules:\n  - match: {app: x}\n    body: '{body} (rewritten)'\n", app='x', body='b')
    assert n.body == 'b (rewritten)'


@pytest.mark.parametrize('template', ['set {x', '{0}', '{a.b}', '{summary!z}', '[1, 2]'])
def test_bad_rewrite_templates_are_config_errors(template):
    value = template if template.startswith('[') else f'"{template}"'
    with pytest.raises(ConfigError):
        load_config(text=f'rules:\n  - match: {{app: x}}\n    summary: {value}\n')
