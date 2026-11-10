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
