import io
import os

import pytest

os.environ.setdefault('GIO_USE_VFS', 'local')   # no gvfs daemon for file monitors in tests

from notifyd.config import load_config
from notifyd.log import EventLog
from notifyd.manager import Manager


class FakeScheduler:
    """Timers on a clock the test moves with advance(ms)."""

    def __init__(self):
        self.time = 0
        self.timers = {}
        self.next = 1

    def now(self):
        return self.time

    def call_later(self, ms, fn):
        handle = self.next
        self.next += 1
        self.timers[handle] = (self.time + ms, fn)
        return handle

    def cancel(self, handle):
        self.timers.pop(handle, None)

    def advance(self, ms):
        end = self.time + ms
        while True:
            due = [(when, handle) for handle, (when, _fn) in self.timers.items() if when <= end]
            if not due:
                break
            when, handle = min(due)
            self.time = when
            _when, fn = self.timers.pop(handle)
            fn()
        self.time = end


class RecordingRenderer:
    def __init__(self):
        self.calls = []

    def show(self, n):
        self.calls.append(('show', n.id))

    def update(self, n):
        self.calls.append(('update', n.id))

    def close(self, nid):
        self.calls.append(('close', nid))


@pytest.fixture
def make_manager(tmp_path):
    """make_manager(config_text) -> (manager, scheduler, renderer, events, closed, actions)."""
    def make(text='', state_path=None):
        config = load_config(text=text)
        log = EventLog(stream=io.StringIO(), color='never')
        events = []
        log.listeners.append(events.append)
        scheduler = FakeScheduler()
        renderer = RecordingRenderer()
        closed, actions = [], []
        ran, sounds, idle = [], [], [0]
        manager = Manager(config, log, scheduler, renderer,
                          on_closed=lambda nid, reason: closed.append((nid, reason)),
                          on_action=lambda nid, key: actions.append((nid, key)),
                          runner=lambda command, n, what='run': ran.append((what, command, n.id)),
                          sound=sounds.append, idle=lambda: idle[0], state_path=state_path)
        manager.ran, manager.sounds, manager.idle_ms = ran, sounds, idle   # for tests to look at and set
        return manager, scheduler, renderer, events, closed, actions
    return make
