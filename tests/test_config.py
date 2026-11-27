import re

import pytest

from notifyd.config import (
    DEFAULTS,
    ConfigError,
    deep_merge,
    format_duration,
    load_config,
    load_preset,
    parse_duration,
    preset_names,
)


@pytest.mark.parametrize('value, ms', [
    (8000, 8000), ('8s', 8000), ('1500ms', 1500), ('2m', 120000), ('1.5s', 1500), ('never', 0), (0, 0), ('250', 250),
])
def test_parse_duration(value, ms):
    assert parse_duration(value) == ms


@pytest.mark.parametrize('value', ['soon', -1, True, '8 hours', None, [1]])
def test_parse_duration_rejects(value):
    with pytest.raises(ConfigError):
        parse_duration(value)


@pytest.mark.parametrize('ms, text', [(0, 'never'), (8000, '8s'), (1500, '1500ms'), (120000, '2m')])
def test_format_duration(ms, text):
    assert format_duration(ms) == text


def test_deep_merge_merges_dicts_and_replaces_the_rest():
    base = {'a': {'x': 1, 'y': 2}, 'b': [1, 2], 'c': 1}
    merged = deep_merge(base, {'a': {'y': 3}, 'b': [9]})
    assert merged == {'a': {'x': 1, 'y': 3}, 'b': [9], 'c': 1}
    assert base['a']['y'] == 2   # not changed in place


def test_empty_config_is_the_defaults():
    config = load_config(text='')
    assert config.settings['preset'] == 'deadd'
    assert config.settings['timeout'] == DEFAULTS['timeout']
    assert config.rules == [] and config.warnings == []


def test_missing_file_is_the_defaults(tmp_path):
    config = load_config(tmp_path / 'nope.yaml')
    assert config.settings['width'] == 350


def test_user_values_override_defaults():
    config = load_config(text='width: 400\ntimeout: 12s\nposition: {anchor: bottom-left}\n')
    assert config.settings['width'] == 400
    assert config.settings['timeout'] == 12000
    assert config.settings['position']['anchor'] == 'bottom-left'
    assert config.settings['position']['gap'] == 10   # the rest of position stays


def test_unknown_keys_warn_and_are_ignored():
    config = load_config(text='widht: 400\n')
    assert config.warnings == ["unknown key 'widht' (ignored)"]
    assert config.settings['width'] == 350


def test_unknown_hide_part_warns():
    config = load_config(text='hide: [app-name, sparkles]\n')
    assert config.settings['hide'] == ['app-name']
    assert 'sparkles' in config.warnings[0]


@pytest.mark.parametrize('text', ['[1, 2]', 'timeout: soon', 'markup: html', 'preset: nonexistent', 'a: [b',
                                  'rules: {app: x}'])
def test_broken_configs_raise(text):
    with pytest.raises(ConfigError):
        load_config(text=text)


def test_bundled_presets_load_and_have_valid_colors():
    names = preset_names()
    assert {'deadd', 'dunst', 'minimal', 'card', 'tokyonight', 'high-contrast'} <= set(names)
    for name in names:
        style = load_preset(name)['style']
        for key, value in style.items():
            values = value.values() if isinstance(value, dict) else [value]
            for item in values:
                # an unquoted rgba(...) in a YAML flow mapping splits at its commas
                if isinstance(item, str) and item.startswith('rgba'):
                    assert item.endswith(')'), f'{name}.{key}: {item}'


def test_deadd_preset_matches_deadd_css():
    style = load_preset('deadd')['style']
    assert style['background'] == 'rgba(0, 0, 0, 0.8)'
    assert style['foreground'] == '#acf'
    assert style['title'] == {'size': '16px', 'weight': 'bold'}
    assert style['critical'] == {'background': 'rgba(255, 0, 0, 0.5)', 'foreground': '#faa'}


def test_user_presets_shadow_bundled_ones(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    (tmp_path / 'notifyd' / 'presets').mkdir(parents=True)
    (tmp_path / 'notifyd' / 'presets' / 'deadd.yaml').write_text('description: mine\nstyle: {foreground: red}\n')
    assert load_preset('deadd')['style'] == {'foreground': 'red'}


def test_preset_descriptions_are_read_whole():
    # an unquoted ' #acf' in YAML starts a comment and cut the description there
    from pathlib import Path

    import notifyd
    presets = Path(notifyd.__file__).parent / 'presets'
    for name in preset_names():
        path = presets / f'{name}.yaml'
        if not path.exists():
            continue                           # a user preset elsewhere
        first = path.read_text().splitlines()[0]
        assert first.startswith('description: '), path
        raw = first[len('description: '):].strip().strip('\'"')
        assert load_preset(name)['description'] == raw, name


@pytest.mark.parametrize('text, message', [
    ('width: wide', 'width: expected a whole number >= 0'),
    ('image: {size: -3}', 'image.size: expected a whole number >= 0'),
    ('icon: {size: 1.5}', 'icon.size: expected a whole number >= 0'),
    ('body: {max-lines: true}', 'body.max-lines'),
    ('position: {gap: x}', 'position.gap: expected a whole number,'),
    ('rules:\n- match: {app: a}\n  width: big', 'width: expected a whole number >= 0'),
])
def test_numeric_settings_are_checked(text, message):
    with pytest.raises(ConfigError, match=re.escape(message)):
        load_config(text=text)


def test_numeric_settings_accept_digit_strings_and_negative_offsets():
    config = load_config(text="width: '420'\nposition: {gap: -4}\nimage: {margin-right: -2}")
    assert config.settings['width'] == 420
    assert config.settings['position']['gap'] == -4
    assert config.settings['image']['margin-right'] == -2


def test_an_unreadable_preset_is_a_config_error(tmp_path, monkeypatch):
    from notifyd import config as config_module
    monkeypatch.setattr(config_module, 'preset_dirs', lambda: [tmp_path])
    (tmp_path / 'broken.yaml').write_bytes(b'\xff\xfe style: x')
    with pytest.raises(ConfigError, match='preset broken'):
        config_module.load_preset('broken')


def test_fullscreen_setting_is_checked():
    assert load_config(text='fullscreen: delay').settings['fullscreen'] == 'delay'
    assert load_config(text='').settings['fullscreen'] == 'show'      # not the user's own config
    with pytest.raises(ConfigError, match='fullscreen: expected show or delay'):
        load_config(text='fullscreen: pushback')


def test_lock_settings():
    config = load_config(text='lock: {pause: true}')
    assert config.settings['lock'] == {'pause': True, 'processes': ['i3lock', 'xsecurelock', 'slock',
                                                                     'betterlockscreen']}
    with pytest.raises(ConfigError, match=r'lock\.pause'):
        load_config(text='lock: {pause: maybe}')
    with pytest.raises(ConfigError, match=r'lock\.processes'):
        load_config(text='lock: {processes: i3lock}')
    with pytest.raises(ConfigError, match='lock'):
        load_config(text='rules:\n- match: {app: a}\n  lock: {pause: true}\n')


def test_ignore_close_is_a_boolean():
    assert load_config(text='').settings['ignore-close'] is False
    with pytest.raises(ConfigError, match='ignore-close'):
        load_config(text='ignore-close: sometimes')


def test_mpris_ignore_is_a_list_of_names():
    assert load_config(text='center: {mpris-ignore: [firefox]}').settings['center']['mpris-ignore'] == ['firefox']
    with pytest.raises(ConfigError, match=r'center\.mpris-ignore'):
        load_config(text='center: {mpris-ignore: firefox}')
