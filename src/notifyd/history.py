"""Closed notifications, newest last; optionally kept in a JSON lines file across restarts."""

import json
import os
from collections import deque
from pathlib import Path


def default_path():
    state = os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state'
    return Path(state) / 'notifyd' / 'history.jsonl'


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
            lines = self.path.read_text().splitlines()
        except FileNotFoundError:
            return
        except OSError as exc:
            self._warn(f'history {self.path}: {exc.strerror}')
            return
        for line in lines:
            try:
                self.entries.append(json.loads(line))
            except ValueError:
                continue    # a line cut short by a crash
        self.written = len(lines)

    def append(self, entry):
        self.entries.append(entry)
        if not self.path:
            return
        try:
            if self.written >= 2 * max(self.entries.maxlen or 1, 1):
                self.rewrite()
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open('a') as out:
                    out.write(json.dumps(entry, ensure_ascii=False) + '\n')
                self.written += 1
        except OSError as exc:
            self._warn(f'history {self.path}: {exc.strerror}')

    def rewrite(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in self.entries))
        tmp.replace(self.path)
        self.written = len(self.entries)

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
