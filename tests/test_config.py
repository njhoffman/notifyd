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
