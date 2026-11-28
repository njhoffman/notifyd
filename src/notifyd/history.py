"""Closed notifications, newest last; optionally kept in a JSON lines file across restarts."""

import json
import os
from collections import deque
from pathlib import Path

from notifyd.files import append_private, write_private


def default_path():
    state = os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state'
    return Path(state) / 'notifyd' / 'history.jsonl'


URGENCIES = ('low', 'normal', 'critical')


def valid_entry(entry):
    """Whether ENTRY (from the history file) is one the center and `pop` can use: an object with a
    whole-number id, text fields that are text and a known urgency."""
    return (isinstance(entry, dict) and isinstance(entry.get('id'), int) and not isinstance(entry['id'], bool)
            and all(isinstance(entry.get(key, ''), str) for key in ('app', 'summary', 'body', 'app_icon'))
            and entry.get('urgency', 'normal') in URGENCIES)


class History:
    def __init__(self, size, path=None, log=None):
        self.entries = deque(maxlen=size)
        self.path = Path(path) if path else None
        self.log = log
        self.written = 0     # lines in the file; it is rewritten when it grows past twice the size
        if self.path:
            self.load()

    def load(self):
        try:
            lines = self.path.read_bytes().decode('utf-8', errors='replace').splitlines()
        except FileNotFoundError:
            return
        except OSError as exc:
            self._warn(f'history {self.path}: {exc.strerror}')
            return
        skipped = 0
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None    # a line cut short by a crash, or damaged
            if valid_entry(entry):
                self.entries.append(entry)
            elif line.strip():
                skipped += 1
        if skipped:
            self._warn(f'history {self.path}: {skipped} damaged line(s) left out')
        self.written = len(lines)

    def append(self, entry):
        self.entries.append(entry)
        if not self.path:
            return
        try:
            if self.written >= 2 * max(self.entries.maxlen or 1, 1):
                self.rewrite()
            else:
                append_private(self.path, json.dumps(entry, ensure_ascii=False) + '\n')
                self.written += 1
        except OSError as exc:
            self._warn(f'history {self.path}: {exc.strerror}')

    def rewrite(self):
        write_private(self.path, ''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in self.entries))
        self.written = len(self.entries)

    def remove(self, nid=None, entry=None):
        """Forget ENTRY itself, else the entries with id NID (all of them when None); how many went.
        Ids repeat when a sender reuses a replaces_id, so the center forgets by entry."""
        before = len(self.entries)
        if entry is not None:
            kept = [e for e in self.entries if e is not entry]
        else:
            kept = [e for e in self.entries if nid is not None and e.get('id') != nid]
        self.entries = deque(kept, maxlen=self.entries.maxlen)
        removed = before - len(self.entries)
        if removed and self.path:
            try:
                self.rewrite()
            except OSError as exc:
                self._warn(f'history {self.path}: {exc.strerror}')
        return removed

    def resize(self, size):
        if size != self.entries.maxlen:
            self.entries = deque(self.entries, maxlen=size)

    def _warn(self, message):
        if self.log:
            self.log.warn(message)

    @property
    def maxlen(self):
        return self.entries.maxlen

    def __len__(self):
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __getitem__(self, index):
        return list(self.entries)[index]
