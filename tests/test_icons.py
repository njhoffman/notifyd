"""notifyd icons, icon checks in notifyd check, and the reasons icons.why_missing gives, against a small
icon theme made in a temporary data directory (no display)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NOTIFYD = [sys.executable, str(ROOT / 'bin' / 'notifyd')]

INDEX = """[Icon Theme]
Name=testtheme
Comment=for notifyd's tests
Directories=status/16,apps/16

[status/16]
Size=16
Context=Status
Type=Fixed

[apps/16]
Size=16
Context=Applications
Type=Fixed
"""


def png(path, rgb=(200, 40, 40)):
    from gi.repository import GdkPixbuf
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 16, 16)
    pixbuf.fill(rgb[0] << 24 | rgb[1] << 16 | rgb[2] << 8 | 0xff)
    path.parent.mkdir(parents=True, exist_ok=True)
    pixbuf.savev(str(path), 'png', [], [])


@pytest.fixture
def env(tmp_path):
    theme = tmp_path / 'data' / 'icons' / 'testtheme'
    theme.mkdir(parents=True)
    (theme / 'index.theme').write_text(INDEX)
    for rel in ('status/16/battery-low.png', 'status/16/battery-full.png', 'apps/16/myapp.png'):
        png(theme / rel)
    gtk = tmp_path / 'config' / 'gtk-3.0'
    gtk.mkdir(parents=True)
    (gtk / 'settings.ini').write_text('[Settings]\ngtk-icon-theme-name=testtheme\n')
    # /usr/share without its icon themes: GdkPixbuf still finds the MIME database and glycin's loaders
    (tmp_path / 'system').mkdir()
    for entry in Path('/usr/share').iterdir():
        if entry.name != 'icons':
            (tmp_path / 'system' / entry.name).symlink_to(entry)
    environment = {**os.environ, 'XDG_DATA_HOME': str(tmp_path / 'data'), 'XDG_DATA_DIRS': str(tmp_path / 'system'),
                   'XDG_CONFIG_HOME': str(tmp_path / 'config'), 'XDG_CACHE_HOME': str(tmp_path / 'cache'),
                   'GIO_USE_VFS': 'local', 'PATH': '/usr/bin:/bin'}
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'LD_PRELOAD'):
        environment.pop(name, None)
    return environment


def run(env, *args):
    return subprocess.run([*NOTIFYD, *args], env=env, capture_output=True, text=True, timeout=30, check=False)


def test_lists_the_current_themes_icons_with_their_context(env):
    result = run(env, 'icons')
    assert result.returncode == 0, result.stderr
    assert result.stdout.split('\n')[:3] == ['battery-full  Status', 'battery-low   Status',
                                              'myapp         Applications']
    assert "3 icon(s) in 'testtheme'" in result.stderr


@pytest.mark.parametrize('args, names', [
    (['battery'], ['battery-full', 'battery-low']),
    (['BATTERY-L*'], ['battery-low']),                       # a glob, case ignored
    (['--context', 'Applications'], ['myapp']),
    (['--theme', 'testtheme', 'app'], ['myapp']),
])
def test_filters(env, args, names):
    result = run(env, 'icons', '--names', *args)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == names


def test_contexts_and_errors(env):
    assert run(env, 'icons', '--contexts').stdout.split() == ['Applications', '1', 'Status', '2']
    nothing = run(env, 'icons', 'zzz')
    assert nothing.returncode == 1 and "nothing matches in 'testtheme'" in nothing.stderr
    missing = run(env, 'icons', '--theme', 'NoSuchTheme')
    assert missing.returncode == 1 and "no icon theme 'NoSuchTheme' installed" in missing.stderr


def test_preview_names_the_file_and_sizes(env):
    result = run(env, 'icons', '--preview', 'battery-low')
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == 'battery-low  (testtheme)'
    assert lines[1].endswith('testtheme/status/16/battery-low.png') and lines[2] == 'sizes: 16'
    gone = run(env, 'icons', '--preview', 'battery-gone')
    assert gone.returncode == 1 and 'not in this theme' in gone.stdout


def test_check_flags_icons_and_pictures_that_do_not_load(env, tmp_path):
    (tmp_path / 'not-an-image.png').write_text('text')
    config = tmp_path / 'config.yaml'
    config.write_text(f"""icon: {{name: battery-low}}
rules:
  - name: songs
    match: {{app: x}}
    icon: {{name: batery-low}}
    image: {{path: {tmp_path}/missing.png}}
  - name: wallpaper
    match: {{app: y}}
    image: {{path: {tmp_path}/not-an-image.png}}
""")
    result = run(env, 'check', '-c', str(config))
    assert result.returncode == 1
    assert result.stdout.splitlines()[:3] == [
        "warning: rule 'songs': icon.name 'batery-low': not in icon theme 'testtheme'"
        " (close: battery-low, battery-full)",
        f"warning: rule 'songs': image.path '{tmp_path}/missing.png': no such file",
        f"warning: rule 'wallpaper': image.path '{tmp_path}/not-an-image.png': not an image GdkPixbuf can read",
    ]
