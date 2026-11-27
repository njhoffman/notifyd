"""history.jsonl and state.json hold notification bodies: only the user may read them."""
import os
import stat

import pytest

from notifyd.files import append_private, write_private
from notifyd.history import History


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture(autouse=True)
def loose_umask():
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_new_files_and_their_directory_are_private(tmp_path):
    path = tmp_path / 'notifyd' / 'history.jsonl'
    append_private(path, 'a\n')
    append_private(path, 'b\n')
    assert path.read_text() == 'a\nb\n'
    assert mode(path) == 0o600 and mode(path.parent) == 0o700
    state = tmp_path / 'notifyd' / 'state.json'
    write_private(state, '{}')
    assert state.read_text() == '{}' and mode(state) == 0o600
    assert not (tmp_path / 'notifyd' / 'state.tmp').exists()


def test_older_loose_files_and_directory_are_tightened_on_the_next_write(tmp_path):
    directory = tmp_path / 'notifyd'
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    old = directory / 'history.jsonl'
    old.write_text('x\n')
    old.chmod(0o644)
    append_private(old, 'y\n')
    assert mode(old) == 0o600 and mode(directory) == 0o700 and old.read_text() == 'x\ny\n'
    state = directory / 'state.json'
    state.write_text('{}')
    state.chmod(0o664)
    write_private(state, '{"mode": "normal"}')
    assert mode(state) == 0o600


def test_history_file_is_private(tmp_path):
    path = tmp_path / 'state' / 'history.jsonl'
    history = History(2, path)
    for i in range(6):                                  # appends, then rewrites past twice the size
        history.append({'id': i, 'body': 'code 123456'})
    assert mode(path) == 0o600 and mode(path.parent) == 0o700
    assert [e['id'] for e in History(2, path).entries] == [4, 5]
