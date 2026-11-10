from pathlib import Path

import yaml

from notifyd.config import load_config
from notifyd.importer import dump_config, import_deadd, parse_css

FIXTURES = Path(__file__).parent / 'fixtures'


def imported():
    return import_deadd((FIXTURES / 'deadd.yml').read_text(), (FIXTURES / 'deadd.css').read_text())


def test_this_desktops_deadd_config():
    config, report = imported()
    assert config['preset'] == 'deadd'
    assert config['width'] == 350
    assert config['position'] == {'margin': [30, 5], 'follow': 'mouse'}
    assert config['timeout'] == '8s'
    assert config['icon'] == {'size': 25}
    assert config['image'] == {'size': 75, 'margin-right': 0}
    # deadd.css here is exactly what the deadd preset has, so no style overrides
    assert 'style' not in config
    assert any('deadd.css' in line for line in report)


def test_modifications_become_rules():
    rules = {rule['match']['app']: rule for rule in imported()[0]['rules']}
    assert rules['Spotify'] == {'match': {'app': 'Spotify'}, 'timeout': '3s', 'class': 'Spotify',
                                'image': {'size': 80}}
    assert rules['backdrop'] == {'match': {'app': 'backdrop'}, 'image': {'size': 110}}
    assert rules['mpd-notification']['hide'] == ['title', 'app-icon', 'app-name']
    assert rules['mpd-notification']['image'] == {'margin-right': 15}
    assert rules['musicman']['hide'] == ['app-icon', 'app-name']


def test_dumped_config_loads_back():
    config, _ = imported()
    text = dump_config(dict(config))
    assert text.startswith('# notifyd config')
    loaded = load_config(text=text)
    assert loaded.warnings == []
    assert [rule.name for rule in loaded.rules] == ['Spotify', 'backdrop', 'Volume', 'tmux-session',
                                                   'mpd-notification', 'musicman']
    assert yaml.safe_load(text)['rules'][0]['timeout'] == '3s'


def test_css_differences_from_the_preset_are_kept():
    css = '''
    .blurredBG.notification { background: rgba(10, 10, 10, 0.9); }
    label { color: #ACF; }                 /* same as the preset, case aside */
    .title { font-weight: normal; font-size: 16px; }
    .notification.content { margin-left: 20px; margin-right: 20px; }
    '''
    config, _ = import_deadd('width: 300\n', css)
    assert config['style'] == {'background': 'rgba(10, 10, 10, 0.9)', 'title': {'weight': 'normal'},
                               'padding': [10, 20]}


def test_unsupported_keys_are_reported():
    yml = '''
notification-center: {hide-on-mouse-leave: true}
shortcuts: {}
notification:
  popup: {click-behavior: {dismiss: mouse1}}
  modifications:
    - match: {app-name: x, time: '1'}
      modify: {transient: true}
'''
    config, report = import_deadd(yml)
    text = '\n'.join(report)
    for needle in ('shortcuts', 'notification-center', 'click-behavior', 'match.time', 'modify.transient'):
        assert needle in text
    assert config['rules'] == [{'match': {'app': 'x'}}]


def test_parse_css_splits_selectors_and_drops_comments():
    rules = parse_css('/* c */ a, b.c { color: red; margin : 1px } d{}')
    assert rules == [('a', {'color': 'red', 'margin': '1px'}), ('b.c', {'color': 'red', 'margin': '1px'}),
                     ('d', {})]


# -- dunst -------------------------------------------------------------------------

def test_this_desktops_dunstrc():
    from notifyd.importer import import_dunst
    config, report = import_dunst((FIXTURES / 'dunstrc').read_text())
    assert config['preset'] == 'dunst'
    assert config['width'] == 250
    assert config['position'] == {'anchor': 'top-right', 'margin': [50, 15], 'follow': 'none'}
    assert config['timeout'] == '6s' and config['timeout-critical'] == '8s'
    assert config['click'] == {'left': 'dismiss', 'middle': 'default-action', 'right': 'dismiss-all'}
    # the dunst preset is this dunstrc, so only what it cannot know remains
    assert config['style'] == {'progress': {'height': 10}}
    assert 'idle-threshold' not in config         # 0: off
    assert any('format' in line for line in report)
    loaded = load_config(text=dump_config(dict(config), 'dunst'))
    assert loaded.warnings == []


DUNSTRC_RULES = '''
[global]
    font = "DejaVu Sans 10"
    transparency = 20
    idle_threshold = 120
    follow = keyboard
    shrink = yes

[urgency_low]
    background = "#222222"
    foreground = "#888888"
    timeout = 4

[urgency_normal]
    background = "#285577"
    timeout = 10

[spotify]
    appname = Spotify
    new_icon = spotify-client
    timeout = 12
    set_stack_tag = song

[mute_chat]
    appname = "*chat*"
    summary = "Re:?*"
    skip_display = yes
    history_ignore = yes
    script = ~/bin/log-chat

[urgent_mail]
    category = email.arrived
    msg_urgency = critical
    background = "#aa0000"
    frame_color = "#ff0000"
    format = "%s"

[regex]
    match_type = regex
    body = ^[0-9]+%$

[disabled]
    enabled = false
    appname = x
'''


def test_dunst_rules_and_globals():
    from notifyd.importer import import_dunst
    config, report = import_dunst(DUNSTRC_RULES)
    assert config['style']['font-family'] == 'DejaVu Sans'
    assert config['style']['font-size'] == '10pt'
    assert config['style']['background'] == 'rgba(40, 85, 119, 0.8)'      # transparency 20
    assert config['style']['low'] == {'background': 'rgba(34, 34, 34, 0.8)', 'foreground': '#888888'}
    assert config['idle-threshold'] == '2m'
    assert config['timeout'] == '10s'
    rules = {rule['name']: rule for rule in config['rules']}
    assert rules['low urgency'] == {'name': 'low urgency', 'match': {'urgency': 'low'}, 'timeout': '4s'}
    assert rules['spotify'] == {'name': 'spotify', 'match': {'app': 'Spotify'}, 'icon': {'name': 'spotify-client'},
                                'timeout': '12s', 'stack-tag': 'song'}
    assert rules['mute_chat']['match'] == {'app': '/^.*chat.*$/', 'summary': '/^Re:..*$/'}
    assert rules['mute_chat']['skip'] is True and rules['mute_chat']['history'] == {'keep': False}
    assert rules['mute_chat']['run'] == '~/bin/log-chat'
    assert rules['urgent_mail']['match'] == {'category': 'email.arrived', 'urgency': 'critical'}
    assert rules['urgent_mail']['style'] == {'background': '#aa0000', 'border-color': '#ff0000'}
    assert rules['regex']['match'] == {'body': '/^[0-9]+%$/'}
    assert 'disabled' not in rules
    text = '\n'.join(report)
    assert 'follow = keyboard' in text and 'global.shrink' in text and '[urgent_mail] format' in text
    loaded = load_config(text=dump_config(dict(config), 'dunst'))
    assert [rule.name for rule in loaded.rules][:2] == ['low urgency', 'spotify']


def test_glob_rules_match_like_dunst():
    from notifyd.importer import import_dunst
    from notifyd.model import Notification
    from notifyd.rules import resolve
    config, _ = import_dunst(DUNSTRC_RULES)
    loaded = load_config(text=dump_config(dict(config), 'dunst'))
    n = resolve(loaded, Notification(app='slack-chat-app', summary='Re: lunch'))
    assert 'mute_chat' in n.rules and n.settings['skip'] is True
    n = resolve(loaded, Notification(app='slack-chat-app', summary='Hi'))
    assert 'mute_chat' not in n.rules
