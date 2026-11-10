"""The state of all notifications: ids, replacement, timers, the waiting queue, pause and history.

No D-Bus and no toolkit here: the server calls notify/close/invoke, a renderer shows what it is
told to show and reports clicks and hovers back, and timers come from a scheduler, so the whole
thing runs in tests with fakes.
"""

import time

from notifyd.history import History
from notifyd.model import CLOSED, DISMISSED, EXPIRED, REASON_NAMES, Notification, actions_from_list
from notifyd.rules import resolve

MIN_RESUME_MS = 1000   # a notification left by the pointer stays at least this long


class GLibScheduler:
    def __init__(self):
        from gi.repository import GLib
        self.GLib = GLib

    def now(self):
        return time.monotonic() * 1000

    def call_later(self, ms, fn):
        def fire():
            fn()
            return False
        return self.GLib.timeout_add(max(int(ms), 0), fire)

    def cancel(self, handle):
        source = self.GLib.main_context_default().find_source_by_id(handle)
        if source and not source.is_destroyed():
            source.destroy()


class NullRenderer:
    """Shows nothing: notifyd as a log-only daemon (and in tests)."""

    def show(self, n):
        pass

    def update(self, n):
        pass

    def close(self, nid):
        pass


class Manager:
    def __init__(self, config, log, scheduler, renderer=None, on_closed=None, on_action=None, history_path=None):
        self.config = config
        self.log = log
        self.scheduler = scheduler
        self.renderer = renderer or NullRenderer()
        self.on_closed = on_closed or (lambda nid, reason: None)
        self.on_action = on_action or (lambda nid, key: None)
        self.next_id = 1
        self.active = {}        # id -> Notification: shown, waiting or held back by pause
        self.displayed = []     # ids on screen, newest first
        self.waiting = []       # ids for which there was no room, oldest first
        self.held_back = []     # ids that arrived while paused, oldest first
        self.timers = {}        # id -> (handle, deadline)
        self.hovered = {}       # id -> remaining ms
        self.paused = False
        self.mode = 'normal'
        self.history = History(config.settings['history']['size'], history_path, log)

    # -- incoming -------------------------------------------------------
    def notify(self, app='', replaces_id=0, app_icon='', summary='', body='', actions=(), hints=None,
               timeout=-1, extra=None):
        n = Notification(app=app, summary=summary, body=body, app_icon=app_icon,
                         actions=actions_from_list(actions), hints=dict(hints or {}),
                         requested_timeout=timeout, replaces_id=replaces_id, mode=self.mode)
        resolve(self.config, n, extra)
        target = self._replace_target(n)
        n.id = target or self._new_id()
        if n.settings.get('skip'):
            if target:
                self.close(target, CLOSED, quiet=True)
            self._remember(n)
            self.log.emit('suppressed', n)
            # after the reply to Notify, so a waiting sender sees its id first
            self.scheduler.call_later(0, lambda: self.on_closed(n.id, EXPIRED))
            return n.id
        if target:
            self._replace(n, self.active[target])
        elif self.paused and n.urgency < 2:
            self.active[n.id] = n
            self.held_back.append(n.id)
            self.log.emit('queued', n, reason='paused')
        else:
            self.active[n.id] = n
            self._place(n)
        return n.id

    def _new_id(self):
        nid = self.next_id
        self.next_id = self.next_id + 1 if self.next_id < 0xFFFFFFFF else 1
        return nid

    def _replace_target(self, n):
        if n.replaces_id and n.replaces_id in self.active:
            return n.replaces_id
        tag = n.stack_tag
        if tag:
            for nid, other in self.active.items():
                if other.app == n.app and other.stack_tag == tag:
                    return nid
        if n.settings.get('stack-duplicates'):
            for nid, other in self.active.items():
                if (other.app, other.summary, other.body, other.urgency) == (n.app, n.summary, n.body, n.urgency):
                    n.duplicates = other.duplicates + 1
                    return nid
        return 0

    def _replace(self, n, old):
        reason = f'duplicate x{n.duplicates}' if n.duplicates > old.duplicates else None
        self.active[n.id] = n
        if n.id in self.displayed:
            self.renderer.update(n)
            self._cancel_timer(n.id)
            if n.id in self.hovered:
                self.hovered[n.id] = n.timeout
            else:
                self._start_timer(n)
        self.log.emit('replaced', n, **({'reason': reason} if reason else {}))

    def _room(self):
        limit = self.config.settings.get('max-visible') or 0
        return not limit or len(self.displayed) < limit

    def _place(self, n):
        if self._room():
            self._show(n)
        else:
            self.waiting.append(n.id)
            self.log.emit('waiting', n, reason=f'{len(self.displayed)} on screen')

    def _show(self, n):
        self.displayed.insert(0, n.id)
        self.renderer.show(n)
        self._start_timer(n)
        self.log.emit('shown', n)

    # -- timers ---------------------------------------------------------
    def _start_timer(self, n, ms=None):
        ms = n.timeout if ms is None else ms
        if ms <= 0:
            return
        nid = n.id
        handle = self.scheduler.call_later(ms, lambda: self.close(nid, EXPIRED))
        self.timers[nid] = (handle, self.scheduler.now() + ms)

    def _cancel_timer(self, nid):
        timer = self.timers.pop(nid, None)
        if timer:
            self.scheduler.cancel(timer[0])
        return timer

    def hold(self, nid):
        """The pointer is over NID: stop its clock."""
        if nid not in self.displayed or nid in self.hovered:
            return
        timer = self._cancel_timer(nid)
        self.hovered[nid] = max(timer[1] - self.scheduler.now(), 0) if timer else 0

    def release(self, nid):
        """The pointer left NID: its clock runs again, for what was left (at least a second)."""
        if nid not in self.hovered:
            return
        remaining = self.hovered.pop(nid)
        n = self.active.get(nid)
        if n and n.timeout > 0:
            self._start_timer(n, max(remaining, MIN_RESUME_MS))

    # -- closing --------------------------------------------------------
    def close(self, nid, reason=CLOSED, quiet=False):
        n = self.active.pop(nid, None)
        if n is None:
            return False
        self._cancel_timer(nid)
        self.hovered.pop(nid, None)
        shown = nid in self.displayed
        for queue in (self.displayed, self.waiting, self.held_back):
            if nid in queue:
                queue.remove(nid)
        if shown:
            self.renderer.close(nid)
        if not quiet:
            self._remember(n, reason)
            self.log.emit(REASON_NAMES.get(reason, 'closed'), n)
            self.on_closed(nid, reason)
        while self.waiting and self._room():
            self._show(self.active[self.waiting.pop(0)])
        return True

    def close_all(self, reason=DISMISSED):
        ids = list(self.displayed) + list(self.waiting)
        for nid in ids:
            self.close(nid, reason)
        return len(ids)

    def invoke(self, nid, key):
        n = self.active.get(nid)
        if n is None:
            return False
        self.log.emit('action', n, action=key)
        self.on_action(nid, key)
        if not n.resident:
            self.close(nid, DISMISSED)
        return True

    def click(self, nid, button):
        """A click on NID with BUTTON (left, middle, right), as config.click says."""
        n = self.active.get(nid)
        if n is None:
            return
        what = n.settings.get('click', {}).get(button, 'dismiss')
        if what == 'dismiss':
            self.close(nid, DISMISSED)
        elif what == 'dismiss-all':
            self.close_all()
        elif what == 'default-action':
            if n.action_label('default') is not None:
                self.invoke(nid, 'default')
            elif n.actions:
                self.invoke(nid, n.actions[0][0])
            else:
                self.close(nid, DISMISSED)

    # -- pause, reload, history ------------------------------------------
    def pause(self, on=True):
        if on == self.paused:
            return self.paused
        self.paused = on
        self.log.emit('paused' if on else 'resumed', message=f'{len(self.held_back)} held back' if not on else '')
        if not on:
            held, self.held_back = self.held_back, []
            for nid in held:
                if nid in self.active:
                    self._place(self.active[nid])
        return self.paused

    def set_mode(self, mode):
        if mode != self.mode:
            self.mode = mode
            self.log.emit('mode', message=mode)
        return self.mode

    def reload(self, config):
        self.config = config
        self.history.resize(config.settings['history']['size'])
        self.log.emit('reload', message=str(config.path or 'defaults'))

    def _remember(self, n, reason=None):
        if n.transient or not n.settings.get('history', {}).get('keep', True):
            return
        entry = n.summary_dict()
        entry['closed'] = REASON_NAMES.get(reason) if reason else 'suppressed'
        entry['closed_at'] = time.time()
        self.history.append(entry)

    def listing(self):
        """Open notifications: on screen (newest first), waiting, held back."""
        out = []
        for state, ids in (('shown', self.displayed), ('waiting', self.waiting), ('held', self.held_back)):
            for nid in ids:
                out.append({**self.active[nid].summary_dict(), 'state': state})
        return out

    def status(self):
        return {
            'paused': self.paused,
            'mode': self.mode,
            'displayed': len(self.displayed),
            'waiting': len(self.waiting),
            'held_back': len(self.held_back),
            'history': len(self.history),
            'preset': self.config.settings['preset'],
            'config': str(self.config.path) if self.config.path else None,
        }
