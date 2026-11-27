"""Files only the user may read: history and state hold notification bodies (codes, messages)."""

import os
import stat

FILE_MODE = 0o600
DIR_MODE = 0o700


def private_dir(path):
    """Create directory PATH (and its parents) for the user only; tighten it when it is looser."""
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    _tighten(path, DIR_MODE)


def _tighten(path, mode):
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            path.chmod(mode)
    except OSError:
        pass


def _open(path, flags):
    fd = os.open(path, flags | os.O_CREAT, FILE_MODE)
    if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:     # an older file, made before this was private
        os.fchmod(fd, FILE_MODE)
    return os.fdopen(fd, 'a' if flags & os.O_APPEND else 'w', encoding='utf-8')


def append_private(path, text):
    private_dir(path.parent)
    with _open(path, os.O_WRONLY | os.O_APPEND) as out:
        out.write(text)


def write_private(path, text):
    """Replace PATH with TEXT atomically (through PATH.tmp), readable by the user only."""
    private_dir(path.parent)
    tmp = path.with_suffix('.tmp')
    with _open(tmp, os.O_WRONLY | os.O_TRUNC) as out:
        out.write(text)
    tmp.replace(path)
