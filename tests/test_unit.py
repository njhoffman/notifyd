"""share/systemd/notifyd.service: the user unit `just install` links."""

import configparser
from pathlib import Path

UNIT = Path(__file__).resolve().parents[1] / 'share/systemd/notifyd.service'


def service():
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(UNIT)
    return parser['Service']


def test_unit_claims_no_bus_name():
    # Another unit naming org.freedesktop.Notifications (dunst's) would make
    # systemd refuse whichever of the two it loads second.
    assert 'BusName' not in service()
    assert service().get('Type', 'simple') == 'simple'


def test_unit_runs_the_daemon_and_restarts():
    assert service()['ExecStart'] == '%h/.local/bin/notifyd daemon'
    # on failure only: a daemon replaced by `notifyd daemon --replace` exits cleanly and stays down
    assert service()['Restart'] == 'on-failure'


def test_odd_mpris_metadata_is_left_out():
    from notifyd.mpris import _state
    assert _state('org.mpris.MediaPlayer2.x', {'Metadata': 'oops'})['title'] == ''
    state = _state('org.mpris.MediaPlayer2.x', {'Metadata': {'xesam:title': 5, 'xesam:artist': ['A', 3, None, 'B'],
                                                            'mpris:artUrl': ['x']}, 'PlaybackStatus': 7})
    assert (state['title'], state['artist'], state['art'], state['status']) == ('', 'A, B', '', 'Stopped')


def test_the_desktop_icon_cache_is_capped(monkeypatch):
    from notifyd import images
    monkeypatch.setattr(images, '_desktop_icons', {})
    for i in range(images.DESKTOP_ICONS_KEPT + 50):
        images.desktop_icon(f'no-such-app-{i}')
    assert len(images._desktop_icons) == images.DESKTOP_ICONS_KEPT


def test_an_art_prune_racing_another_keeps_the_download(tmp_path, monkeypatch):
    from notifyd import art
    for i in range(5):
        (tmp_path / f'f{i}').write_bytes(b'x')
    real_stat = type(tmp_path).stat
    def flaky_stat(self, *a, **k):                 # another prune deleted it meanwhile
        if self.name == 'f2':
            raise FileNotFoundError(self)
        return real_stat(self, *a, **k)
    monkeypatch.setattr(type(tmp_path), 'stat', flaky_stat)
    monkeypatch.setattr(art, 'KEEP', 2)
    art._prune(tmp_path)                           # no exception
    assert len([p for p in tmp_path.iterdir()]) <= 3
