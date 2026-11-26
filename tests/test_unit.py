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
    assert service()['Restart'] == 'always'
