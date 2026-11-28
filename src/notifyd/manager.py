"""The state of all notifications: ids, replacement, timers, the waiting queue, pause and history.

No D-Bus and no toolkit here: the server calls notify/close/invoke, a renderer shows what it is
told to show and reports clicks and hovers back, and timers come from a scheduler, so the whole
thing runs in tests with fakes.
"""

import itertools
import json
import time
from pathlib import Path

from notifyd.config import ConfigError
from notifyd.effects import sound_for
from notifyd.files import write_private
from notifyd.history import History
from notifyd.images import prune_thumbnails, save_thumbnail
from notifyd.markup import markup_error, one_time_code, plain_text
from notifyd.model import (
    CLOSED,
    COPY_CODE_ACTION,
    DISMISSED,
    EXPIRED,
    REASON_NAMES,
    REPLY_ACTION,
    TITLE_END_HINT,
    UNDEFINED,
    Notification,
    actions_from_list,
)
from notifyd.rules import MATCH_FIELDS, _normalize_actual, _value_matcher, apply_filter, drop_actions, resolve


def _span(seconds):
    """SECONDS as a short duration: 90 -> 1m30s, 3600 -> 1h."""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return ''.join(f'{v}{u}' for v, u in ((hours, 'h'), (minutes, 'm'), (secs, 's')) if v) or '0s'


MIN_RESUME_MS = 1000   # a notification left by the pointer stays at least this long
IDLE_RECHECK_MS = 2000  # while the user is idle, expiry is asked again this often


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
    def __init__(self, config, log, scheduler, renderer=None, on_closed=None, on_action=None, history_path=None,
                 state_path=None, runner=None, sound=None, idle=None, thumbs_path=None, filter_runner=None):
        self.config = config
        self.log = log
        self.scheduler = scheduler
        self.renderer = renderer or NullRenderer()
        self.on_closed = on_closed or (lambda nid, reason: None)
        self.on_action = on_action or (lambda nid, key: None)
        self.on_reply = lambda nid, text: None    # the server emits NotificationReplied
        self.next_id = 1
        self.active = {}        # id -> Notification: shown, waiting or held back by pause
        self.displayed = []     # ids on screen, newest first
        self.waiting = []       # ids for which there was no room, oldest first
        self.held_back = []     # ids that arrived while paused, oldest first
        self.deferred = []      # ids waiting for a fullscreen window to go (fullscreen: delay), oldest first
        self.fullscreen_check = None
        self.timers = {}        # id -> (handle, deadline)
        self.hovered = {}       # id -> remaining ms
        self.paused = False
        self.pause_until = None     # wall-clock time a timed pause (ctl pause --for) ends, else None
        self.pause_timer = None
        self.mode_until = None      # a timed mode (ctl mode NAME --for) ends then, back to mode_after
        self.mode_after = None
        self.mode_timer = None
        self.missed = 0             # expired without being clicked or closed since the center was last opened
        self.locked = False     # the screen is locked (lock.pause): hold back everything, critical too
        self.history = History(config.settings['history']['size'], history_path, log)
        self.runner = runner or (lambda command, n, what='run', env=None: None)
        # filter_runner(command, n, timeout_ms, done): done(result, error) later; none: shown unfiltered
        self.filter_runner = filter_runner or (lambda command, n, timeout_ms, done: done({}, None))
        self.filtering = {}     # id -> (Notification, replace target): waiting for its filter command
        self.sound = sound or (lambda name: None)
        self.idle = idle or (lambda: 0)
        self.state_path = Path(state_path) if state_path else None
        self.thumbs_path = Path(thumbs_path) if thumbs_path else None   # image-data kept for the history
        self.thumb_seq = itertools.count()       # in thumbnail names: two closes in one millisecond differ
        state = self._load_state()
        self.mode = state.get('mode') or 'normal'
        self.missed = state.get('missed') if isinstance(state.get('missed'), int) else 0
        # ids go on from the kept history, so pop and forget never mix up an old one with a new one
        self.next_id = max((e.get('id', 0) for e in self.history), default=0) % 0xFFFFFFFF + 1
        if state.get('paused') is True:
            self._restore_held(state.get('held'))
        self.saved_timers = state                 # start() picks a timed pause or mode up again
        self._prune_thumbnails()                  # of entries that did not survive the restart

    # -- incoming -------------------------------------------------------
    def notify(self, app='', replaces_id=0, app_icon='', summary='', body='', actions=(), hints=None,
               timeout=-1, extra=None, sender=None):
        # edge newlines and spaces would draw blank lines (chat apps and scripts send them)
        summary, body = (summary or '').strip(), (body or '').strip()
        n = Notification(app=app, summary=summary, body=body, app_icon=app_icon,
                         actions=actions_from_list(actions), hints=dict(hints or {}),
                         requested_timeout=timeout, replaces_id=replaces_id, mode=self.mode)
        n.sender = dict(sender or {})
        n.sent = {'app': app, 'summary': summary, 'body': body, 'hints': dict(n.hints), 'extra': extra}
        if n.hints.get('desktop-entry'):
            n.sender['desktop_entry'] = str(n.hints['desktop-entry'])
        n.fullscreen = self._fullscreen()
        resolve(self.config, n, extra)
        drop_actions(n)                               # before the rule's own buttons join them
        for index, button in enumerate(n.settings.get('buttons') or []):
            key = f'notifyd-button-{index}'
            n.actions.append((key, button['label']))
            n.commands[key] = button['run']
        target = self._replace_target(n)
        n.id = target or self._free_replaces_id(n) or self._new_id()
        self.filtering.pop(n.id, None)            # a newer one with its id: the older filter's answer is moot
        if n.settings.get('filter'):
            self.filtering[n.id] = (n, target)
            self.filter_runner(n.settings['filter'], n, int(n.settings.get('filter-timeout') or 500),
                               lambda result, error: self._filtered(n, result, error))
            return n.id
        return self._admit(n, target)

    def _filtered(self, n, result, error):
        """N's filter answered: apply what it printed and let N in, unless N was replaced or closed
        meanwhile."""
        pending = self.filtering.get(n.id)
        if pending is None or pending[0] is not n:
            return
        del self.filtering[n.id]
        target = pending[1] if pending[1] in self.active else None
        command = n.settings['filter']
        if error:
            self.log.warn(f'#{n.id} filter {command!r}: {error}; shown unchanged')
        else:
            for problem in apply_filter(n, result):
                self.log.warn(f'#{n.id} filter {command!r}: {problem}')
        self._admit(n, target)

    def _admit(self, n, target):
        """Let N (ready, filtered when it has a filter) in: shown, replacing TARGET, held back or skipped."""
        self._offer_code(n)
        for part, text in (('summary', n.summary), ('body', n.body)):
            error = markup_error(text, n.settings.get('markup', 'full'))
            if error:
                self.log.warn(f'#{n.id} {part} is not valid markup, shown as typed: {error}')
        self._hook(n, 'receive')
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
        elif self.locked or (self.paused and n.urgency < 2):
            self.active[n.id] = n
            self.held_back.append(n.id)
            self.log.emit('queued', n, reason='locked' if self.locked else 'paused')
            self._save_state()                    # held ones outlive a restart (or a crash)
        elif n.fullscreen and n.urgency < 2 and n.settings.get('fullscreen') == 'delay':
            self.active[n.id] = n
            self.deferred.append(n.id)
            self.log.emit('queued', n, reason='fullscreen')
            self._watch_fullscreen()
        else:
            self.active[n.id] = n
            self._place(n)
        return n.id

    def _free_replaces_id(self, n):
        """The spec returns a non-zero replaces_id as it is, also when that notification is gone (a
        script keeping a fixed id, notify-send -r 100): reuse it when it is free, that is, nothing
        open has it and it was issued before (so the counter does not hand it out again soon)."""
        nid = n.replaces_id
        if isinstance(nid, int) and 0 < nid < self.next_id and nid not in self.active:
            return nid
        return 0

    def _fullscreen(self):
        check = getattr(self.renderer, 'fullscreen', None)
        try:
            return bool(check and check())
        except Exception as exc:   # an X error must not lose the notification
            self.log.warn(f'fullscreen check: {type(exc).__name__}: {exc}')
            return False

    FULLSCREEN_POLL = 1000   # ms between checks while some wait for fullscreen to end

    def _watch_fullscreen(self):
        if self.fullscreen_check is None:
            self.fullscreen_check = self.scheduler.call_later(self.FULLSCREEN_POLL, self._check_fullscreen)

    def _check_fullscreen(self):
        self.fullscreen_check = None
        if not self.deferred:
            return
        if self._fullscreen():
            self._watch_fullscreen()
            return
        deferred, self.deferred = self.deferred, []
        for nid in deferred:
            n = self.active.get(nid)
            if n is None:
                continue
            if self.paused and n.urgency < 2:   # do not disturb came on meanwhile: it waits for that now
                self.held_back.append(nid)
                self.log.emit('queued', n, reason='paused')
            else:
                self._place(n)
        if self.paused:
            self._save_state()

    def reserve_id(self):
        """An id for a reply that shows nothing (a control message)."""
        return self._new_id()

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
        group = n.settings.get('group-by') or []
        if group:
            key = [getattr(n, field) for field in group]
            for nid, other in self.active.items():
                if [getattr(other, field) for field in group] == key:
                    n.duplicates = other.duplicates + 1
                    n.grouped = True
                    return nid
        if n.settings.get('stack-duplicates'):
            for nid, other in self.active.items():
                if (other.app, other.summary, other.body, other.urgency) == (n.app, n.summary, n.body, n.urgency):
                    n.duplicates = other.duplicates + 1
                    return nid
        return 0

    def _replace(self, n, old):
        reason = None
        if n.duplicates > old.duplicates:
            reason = f'{"grouped" if n.grouped else "duplicate"} x{n.duplicates}'
        self.active[n.id] = n
        if n.id in self.displayed:
            self._render('update', n.id, n)       # on failure the old window stays, with a fresh timer
            self._cancel_timer(n.id)
            if n.id in self.hovered:
                self.hovered[n.id] = n.timeout
            else:
                self._start_timer(n)
        self.log.emit('replaced', n, **({'reason': reason} if reason else {}))

    def _stack_count(self, stack):
        return sum(1 for nid in self.displayed if nid in self.active and self.active[nid].stack == stack)

    def _room(self, n):
        """Whether N's stack has room under max-visible (each stack counts its own)."""
        limit = self.config.settings.get('max-visible') or 0
        return not limit or self._stack_count(n.stack) < limit

    def _fill_waiting(self):
        """Show what waits, oldest first, as far as its stack has room; in each stack one that does not fit
        stops the ones after it (they keep their order), while other stacks go on."""
        blocked = set()
        for nid in list(self.waiting):
            n = self.active.get(nid)
            if n is None or n.stack in blocked:
                continue
            if self._room(n) and self._fits(n):
                self.waiting.remove(nid)
                self._show(n)
            else:
                blocked.add(n.stack)

    def _waiting_changed(self):
        """The waiting queue grew or shrank: the renderer's +N follows (when it has one)."""
        hook = getattr(self.renderer, 'waiting_changed', None)
        if hook is None:
            return
        try:
            hook()
        except Exception as exc:   # a cosmetic count must not break placing notifications
            self.log.error(f'waiting count: {type(exc).__name__}: {exc}')

    def _fits(self, n):
        fits = getattr(self.renderer, 'fits', None)
        return fits is None or fits(n)

    def _place(self, n):
        if self._room(n) and self._fits(n):
            self._show(n)
        else:
            self.waiting.append(n.id)
            self.log.emit('waiting', n, reason=f'{self._stack_count(n.stack)} on screen')
            self._waiting_changed()

    def _render(self, what, nid, arg):
        """renderer.WHAT(ARG); an exception (a bad style value, an unreadable preset) is logged instead
        of leaving the manager half way through a change. True when it worked."""
        try:
            getattr(self.renderer, what)(arg)
            return True
        except Exception as exc:   # any renderer failure: the queues must not be left half changed
            self.log.error(f'#{nid} could not be drawn ({what}): {type(exc).__name__}: {exc}')
            return False

    def _show(self, n):
        self.displayed.insert(0, n.id)
        if not self._render('show', n.id, n):
            self.close(n.id, UNDEFINED)           # into history, NotificationClosed, slot freed
            return
        self._start_timer(n)
        self.log.emit('shown', n)
        sound = sound_for(n)
        if sound:
            self.sound(sound)

    # -- timers ---------------------------------------------------------
    def _start_timer(self, n, ms=None):
        ms = n.timeout if ms is None else ms
        if ms <= 0:
            return
        nid = n.id
        handle = self.scheduler.call_later(ms, lambda: self._expire(nid))
        self.timers[nid] = (handle, self.scheduler.now() + ms)

    def _expire(self, nid):
        """Time is up for NID, unless the user is away (idle-threshold): then it waits for them."""
        n = self.active.get(nid)
        if n is None:
            return
        threshold = n.settings.get('idle-threshold') or 0
        if threshold and self.idle() >= threshold:
            self.timers.pop(nid, None)
            self._start_timer(n, IDLE_RECHECK_MS)
            return
        self.close(nid, EXPIRED)

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

    def remaining(self, nid):
        """Milliseconds left on NID's clock (frozen while hovered), or None when it has none."""
        if nid in self.hovered:
            return self.hovered[nid]
        timer = self.timers.get(nid)
        return max(timer[1] - self.scheduler.now(), 0) if timer else None

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
        pending = self.filtering.pop(nid, None)   # closed while its filter runs: it never shows
        n = self.active.pop(nid, None)
        if n is None:
            if pending is None:
                return False
            if not quiet:
                self._remember(pending[0], reason)
                self.log.emit(REASON_NAMES.get(reason, 'closed'), pending[0])
                self.on_closed(nid, reason)
            return True
        self._cancel_timer(nid)
        self.hovered.pop(nid, None)
        shown = nid in self.displayed
        held = nid in self.held_back
        for queue in (self.displayed, self.waiting, self.held_back, self.deferred):
            if nid in queue:
                queue.remove(nid)
        if shown:
            self._render('close', nid, nid)
        if not quiet:
            self._remember(n, reason)
            self.log.emit(REASON_NAMES.get(reason, 'closed'), n)
            self.on_closed(nid, reason)
            self._hook(n, 'close', NOTIFYD_CLOSE_REASON=REASON_NAMES.get(reason, 'closed'))
            if reason == EXPIRED:                  # it timed out unclicked: missed until the center opens
                self.missed += 1
                self._save_state()
        if held:
            self._save_state()
        self._fill_waiting()
        self._waiting_changed()
        return True

    def sender_close(self, nid):
        """The app closes its own notification (CloseNotification); a rule's `ignore-close: true` keeps
        it open (dunst's ignore_dbusclose). `ctl close` and the user still close it."""
        n = self.active.get(nid)
        if n is not None and n.settings.get('ignore-close'):
            self.log.emit('kept', n, reason='the sender closed it; ignore-close')
            return False
        return self.close(nid, CLOSED)

    def close_matching(self, criteria, reason=CLOSED):
        """Close every open notification (on screen, waiting, held back) whose fields match CRITERIA, a
        mapping of match fields as in rules (text or /regex/). How many closed."""
        if not isinstance(criteria, dict) or not criteria:
            raise ValueError('close: give at least one field to match')
        unknown = [key for key in criteria if key not in MATCH_FIELDS]
        if unknown:
            raise ValueError(f'close: unknown field {unknown[0]!r} (fields: {", ".join(sorted(MATCH_FIELDS))})')
        try:
            matchers = [(MATCH_FIELDS[key], _value_matcher(value, f'close --{key}'))
                        for key, value in criteria.items()]
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc
        ids = [nid for nid, n in list(self.active.items())
               if all(matcher(_normalize_actual(get(n))) is not None for get, matcher in matchers)]
        for nid in ids:
            self.close(nid, reason)
        return len(ids)

    def close_all(self, reason=DISMISSED):
        ids = list(self.displayed) + list(self.waiting) + list(self.deferred)
        for nid in ids:
            self.close(nid, reason)
        return len(ids)

    def invoke(self, nid, key):
        """Invoke action KEY of NID; False (nothing signalled, nothing closed) when it does not offer it."""
        n = self.active.get(nid)
        if n is None or key not in {k for k, _label in n.actions}:
            return False
        if key == COPY_CODE_ACTION:               # notifyd's own: the sender never hears of it
            return self._copy_code(n)
        if key == REPLY_ACTION:                   # it wants text: the renderer asks for it (the center)
            start = getattr(self.renderer, 'start_reply', None)
            return bool(start and start(nid) is not False)
        self.log.emit('action', n, action=key)
        self._hook(n, 'action', NOTIFYD_ACTION=key)
        if key in n.commands:
            self.runner(n.commands[key], n, 'button')
        else:
            self.on_action(nid, key)
        if not n.resident:
            self.close(nid, DISMISSED)
        return True

    def reply(self, nid, text):
        """Answer NID's inline-reply action with TEXT: NotificationReplied, the action hook (NOTIFYD_REPLY),
        and it closes unless resident. False when NID is not open or takes no reply."""
        n = self.active.get(nid)
        if n is None or not n.can_reply or not text.strip():
            return False
        self.log.emit('action', n, action=REPLY_ACTION, reason='replied')
        self._hook(n, 'action', NOTIFYD_ACTION=REPLY_ACTION, NOTIFYD_REPLY=text)
        self.on_reply(nid, text)
        if not n.resident:
            self.close(nid, DISMISSED)
        return True

    def _offer_code(self, n):
        """copy-code: a "Copy CODE" action when N's summary or body holds a one-time code."""
        if not n.settings.get('copy-code', True) or any(key == COPY_CODE_ACTION for key, _ in n.actions):
            return
        if n.settings.get('markup', 'full') == 'plain':
            text = f'{n.summary}\n{n.body}'
        else:
            text = f'{plain_text(n.summary)}\n{plain_text(n.body)}'
        code = one_time_code(text)
        if code:
            n.code = code
            n.actions.append((COPY_CODE_ACTION, f'Copy {code}'))

    def _copy_code(self, n):
        """Put N's one-time code on the clipboard (the renderer's), then close N unless resident."""
        copy = getattr(self.renderer, 'copy_text', None)
        if copy is None or copy(n.code) is False:
            self.log.warn(f'#{n.id} code not copied: no clipboard (needs the GTK renderer)')
            return False
        self.log.emit('action', n, action=COPY_CODE_ACTION, reason='copied')
        if not n.resident:
            self.close(n.id, DISMISSED)
        return True

    def _hook(self, n, event, **env):
        """Run N's rule command (`run`) for EVENT when its `run-on` lists it, with NOTIFYD_EVENT and ENV."""
        if n.settings.get('run') and event in (n.settings.get('run-on') or ['receive']):
            self.runner(n.settings['run'], n, 'run', env={'NOTIFYD_EVENT': event, **env})

    def click(self, nid, button):
        """A click on NID with BUTTON (left, middle, right), as config.click says."""
        n = self.active.get(nid)
        if n is None:
            return
        what = n.settings.get('click', {}).get(button, 'dismiss')
        if isinstance(what, str) and what.startswith('exec:'):
            command = what[len('exec:'):].strip()
            if command:
                self.runner(command, n, 'click', env={'NOTIFYD_EVENT': 'click', 'NOTIFYD_BUTTON': button})
            self.close(nid, DISMISSED)
        elif what == 'dismiss':
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
    def pause(self, on=True, seconds=None):
        """Pause (do not disturb) or resume; SECONDS ends a pause on its own after that long. Any pause or
        resume replaces a timed one."""
        timed = self.pause_until is not None
        self._cancel_pause_timer()
        if on and seconds:
            self._time_pause(time.time() + seconds)
        if on == self.paused:
            if timed or self.pause_until is not None:
                self._save_state()
            return self.paused
        self.paused = on
        self.log.emit('paused' if on else 'resumed', message=f'{len(self.held_back)} held back' if not on
                      else f'for {_span(seconds)}' if self.pause_until is not None else '')
        if not on:
            self._release_held()
        self._save_state()
        return self.paused

    def set_locked(self, on):
        """The screen locked or unlocked (lock.pause): while locked everything is held back, critical
        ones too, since popups can stack above an X locker. Unlocking shows them unless paused."""
        on = bool(on)
        if on == self.locked:
            return self.locked
        self.locked = on
        self.log.emit('locked' if on else 'unlocked', message='' if on else f'{len(self.held_back)} held back')
        if not on:
            self._release_held()
        return self.locked

    def _release_held(self):
        """Show what was held back, as far as pause and lock allow (critical ones only pause lets through)."""
        if self.locked:
            return
        held, self.held_back = self.held_back, []
        for nid in held:
            n = self.active.get(nid)
            if n is None:
                continue
            if self.paused and n.urgency < 2:
                self.held_back.append(nid)
            else:
                self._place(n)
        if self.paused:
            self._save_state()

    def shutdown(self):
        """The daemon stops: keep the paused state and the held-back queue for the next start (their
        ids end here, so their senders hear UNDEFINED), and close the rest."""
        self._save_state()
        held, self.held_back = self.held_back, []
        for nid in held:
            if not self.paused:                 # held only by the lock: not kept, so into history
                self.held_back.append(nid)
                self.close(nid, UNDEFINED)
            elif self.active.pop(nid, None) is not None:
                self.on_closed(nid, UNDEFINED)
        return self.close_all(CLOSED)

    @staticmethod
    def _held_record(n):
        """N as JSON for state.json: what notify() needs to bring it back (image-data bytes are not kept)."""
        def plain(value):
            return isinstance(value, (str, int, float, bool)) or value is None
        actions = [[key, label] for key, label in n.actions if not key.startswith('notifyd-')]   # ours come back
        return {'app': n.app, 'summary': n.summary, 'body': n.body, 'app_icon': n.app_icon,
                'actions': actions, 'hints': {k: v for k, v in n.hints.items() if plain(v)},
                'timeout': n.requested_timeout, 'sender': n.sender, 'received': n.received}

    def _restore_held(self, records):
        """Pause again and hold back what was held when the daemon stopped (new ids; rules apply again,
        but their `run:` commands ran when these first arrived)."""
        self.paused = True
        restored = 0
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            try:
                actions = [item for pair in record.get('actions') or [] for item in pair]
                nid = self.notify(app=str(record.get('app', '')), summary=str(record.get('summary', '')),
                                  body=str(record.get('body', '')), app_icon=str(record.get('app_icon', '')),
                                  actions=actions, hints=dict(record.get('hints') or {}),
                                  timeout=int(record.get('timeout', -1)), extra={'run': None, 'filter': None},
                                  sender=record.get('sender') or {})
            except (TypeError, ValueError, AttributeError) as exc:
                self.log.warn(f'state: a held-back notification could not be restored: {exc}')
                continue
            n = self.active.get(nid)
            if n is not None and isinstance(record.get('received'), (int, float)):
                n.received = record['received']
            restored += 1
        self.log.emit('paused', message=f'restored, {restored} held back' if restored else 'restored')

    def set_mode(self, mode, seconds=None):
        """Switch to MODE; SECONDS switches back to the mode before it after that long (the one before a
        timed mode when one is running). Any switch replaces a timed one."""
        after = self.mode_after if self.mode_until is not None else self.mode
        timed = self.mode_until is not None
        self._cancel_timer_handle('mode_timer')
        self.mode_until = self.mode_after = None
        if seconds and mode != after:
            self._time_mode(time.time() + seconds, after)
        if mode != self.mode:
            self.mode = mode
            self.log.emit('mode', message=mode + (f' for {_span(seconds)}' if self.mode_until else ''))
            self._save_state()
        elif timed or self.mode_until is not None:
            self._save_state()
        return self.mode

    # -- timed pause and mode -------------------------------------------
    def _cancel_timer_handle(self, name):
        handle = getattr(self, name)
        if handle is not None:
            self.scheduler.cancel(handle)
            setattr(self, name, None)

    def _cancel_pause_timer(self):
        self._cancel_timer_handle('pause_timer')
        self.pause_until = None

    def _time_pause(self, until):
        self.pause_until = until
        self.pause_timer = self.scheduler.call_later(max(until - time.time(), 0) * 1000, self._pause_over)

    def _pause_over(self):
        self.pause_timer = None
        self.pause_until = None
        if self.paused:
            self.pause(False)

    def _time_mode(self, until, after):
        self.mode_until, self.mode_after = until, after
        self.mode_timer = self.scheduler.call_later(max(until - time.time(), 0) * 1000, self._mode_over)

    def _mode_over(self):
        after = self.mode_after or 'normal'
        self.mode_timer = None
        self.mode_until = self.mode_after = None
        self.set_mode(after)

    def start(self):
        """The daemon is wired (renderer and server attached): pick up a timed pause or mode the state file
        kept. Ending one that ran out shows what was held back, which needs both: before them those
        would be on screen with no window and close without a NotificationClosed."""
        state, self.saved_timers = self.saved_timers, None
        if state:
            self._restore_timers(state)

    def _restore_timers(self, state):
        """Pick up a timed pause or mode the state file kept: what ended while notifyd was down ends now."""
        until = state.get('paused_until')
        if self.paused and isinstance(until, (int, float)):
            if until <= time.time():
                self.pause(False)
            else:
                self._time_pause(until)
        until, after = state.get('mode_until'), state.get('mode_after')
        if isinstance(until, (int, float)) and isinstance(after, str):
            if until <= time.time():
                self.set_mode(after)
            else:
                self._time_mode(until, after)

    def seen(self):
        """The center opened: what was missed has been seen."""
        if self.missed:
            self.missed = 0
            self.log.emit('missed', level='debug', message='0')
            self._save_state()

    def _load_state(self):
        if not self.state_path:
            return {}
        try:
            state = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def _save_state(self):
        if not self.state_path:
            return
        try:
            held = [self._held_record(self.active[nid]) for nid in self.held_back if nid in self.active]
            write_private(self.state_path, json.dumps({
                'mode': self.mode, 'paused': self.paused, 'held': held, 'missed': self.missed,
                'paused_until': self.pause_until, 'mode_until': self.mode_until, 'mode_after': self.mode_after}))
        except OSError as exc:
            self.log.warn(f'state {self.state_path}: {exc.strerror}')

    def forget(self, nid=None, entry=None):
        """Remove ENTRY, else NID's entries, else all of the history; how many entries went."""
        removed = self.history.remove(nid, entry)
        if removed:
            self.log.emit('history', message=f'forgot {removed}')
            self._prune_thumbnails()
        return removed

    def entry_hints(self, entry):
        """The hints a history entry brings back: urgency, category, the title end, its picture and
        desktop entry."""
        hints = {'urgency': ('low', 'normal', 'critical').index(entry.get('urgency', 'normal'))}
        if entry.get('category'):
            hints['category'] = entry['category']
        if entry.get('title_end'):
            hints[TITLE_END_HINT] = entry['title_end']
        image = entry.get('image') or entry.get('thumb')
        if image:
            hints['image-path'] = image
        if (entry.get('sender') or {}).get('desktop_entry'):
            hints['desktop-entry'] = entry['sender']['desktop_entry']
        return hints

    def entry_notification(self, entry):
        """A history entry as a notification with today's rules applied (not shown, not counted): the
        center draws its card with the same look, hidden parts and picture sizes as the popup had."""
        n = Notification(app=entry.get('app', ''), summary=entry.get('summary', ''), body=entry.get('body', ''),
                         app_icon=entry.get('app_icon', ''), hints=self.entry_hints(entry),
                         mode=entry.get('mode', self.mode))
        n.sender = dict(entry.get('sender') or {})
        n.id = entry.get('id', 0)
        n.received = entry.get('received', n.received)
        n.duplicates = entry.get('duplicates', 1)
        try:
            resolve(self.config, n, {'run': None, 'sound': False})
        except Exception as exc:   # an entry the rules cannot take is still drawn, plainly
            self.log.warn(f'#{n.id} history card: {type(exc).__name__}: {exc}')
            n.settings = self.config.base()
        if entry.get('markup'):
            n.settings['markup'] = entry['markup']
        return n

    def pop(self, nid=0, entry=None):
        """Show a closed notification again (ENTRY, else the last one with NID, else the last one); its
        new id, or 0 when none."""
        if entry is None:
            entries = [e for e in self.history if not nid or e['id'] == nid]
            if not entries:
                return 0
            entry = entries[-1]
        hints = self.entry_hints(entry)
        # a look back, not a new arrival: rules style it again, but their commands and sounds stay quiet
        return self.notify(entry.get('app', ''), 0, entry.get('app_icon', ''), entry.get('summary', ''),
                           entry.get('body', ''), [], hints, extra={'run': None, 'sound': False},
                           sender=entry.get('sender'))

    # settings a reload carries over to open notifications: their look, not their clock or actions
    RELOAD_LOOK = ('preset', 'style', 'hide', 'markup', 'image', 'icon', 'title', 'body', 'width',
                   'timeout-bar', 'fade', 'class')

    def reload(self, config):
        self.config = config
        self.history.resize(config.settings['history']['size'])
        for n in self.active.values():
            self._restyle(n)
        self.log.emit('reload', message=str(config.path or 'defaults'))

    def _restyle(self, n):
        """Apply the (new) rules to what N was sent and take over their look; its text, timer, actions
        and commands stay as they are, and `run:` does not fire again."""
        if not n.sent:
            return
        fresh = Notification(app=n.sent['app'], summary=n.sent['summary'], body=n.sent['body'],
                             app_icon=n.app_icon, hints=dict(n.sent['hints']), mode=n.mode)
        fresh.sender, fresh.fullscreen = n.sender, n.fullscreen
        try:
            resolve(self.config, fresh, n.sent.get('extra'))
        except Exception as exc:   # a rule that cannot apply keeps the old look
            self.log.warn(f'#{n.id} reload: {type(exc).__name__}: {exc}')
            return
        for key in self.RELOAD_LOOK:
            if key in fresh.settings:
                n.settings[key] = fresh.settings[key]
            else:
                n.settings.pop(key, None)
        n.rules = fresh.rules

    def _remember(self, n, reason=None):
        if n.transient or not n.settings.get('history', {}).get('keep', True):
            return
        entry = n.summary_dict()
        entry['closed'] = REASON_NAMES.get(reason) if reason else 'suppressed'
        entry['closed_at'] = time.time()
        if n.settings.get('markup', 'full') != 'full':
            entry['markup'] = n.settings['markup']
        if self.thumbs_path and 'image' not in entry:
            # named by id, time and a sequence number: ids repeat when a sender reuses a replaces_id, and two
            # closes can fall in one millisecond
            name = f'{n.id}-{int(entry["closed_at"] * 1000)}-{next(self.thumb_seq)}'
            thumb = save_thumbnail(n.hints, self.thumbs_path, name)
            if thumb:
                entry['thumb'] = str(thumb)
        self.history.append(entry)
        self._prune_thumbnails()

    def _prune_thumbnails(self):
        """Thumbnails go with their history entries; one a popped entry's image points at stays."""
        if not (self.thumbs_path and self.thumbs_path.is_dir()):
            return
        keep = {Path(str(entry.get(key))).name for entry in self.history for key in ('thumb', 'image')
                if entry.get(key) and Path(str(entry[key])).parent == self.thumbs_path}
        prune_thumbnails(self.thumbs_path, keep)

    def listing(self):
        """Open notifications: on screen (newest first), waiting, held back."""
        out = []
        for state, ids in (('shown', self.displayed), ('waiting', self.waiting), ('held', self.held_back),
                           ('fullscreen', self.deferred)):
            for nid in ids:
                out.append({**self.active[nid].summary_dict(), 'state': state})
        return out

    def status(self):
        return {
            'paused': self.paused,
            'paused_until': self.pause_until,
            'locked': self.locked,
            'mode': self.mode,
            'mode_until': self.mode_until,
            'missed': self.missed,
            'displayed': len(self.displayed),
            'waiting': len(self.waiting),
            'held_back': len(self.held_back),
            'deferred': len(self.deferred),
            'history': len(self.history),
            'preset': self.config.settings['preset'],
            'config': str(self.config.path) if self.config.path else None,
        }
