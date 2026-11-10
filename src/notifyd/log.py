"""The event log: one line per thing that happens to a notification.

    16:42:05  shown     #12  backdrop      low       #412  video  3840×2160 | 0:18 · 25 fps   12s  [backdrop]

pretty (aligned, colored by urgency and event; NO_COLOR and non-terminals get no color) or json
(one object per line, also what `notifyd ctl watch --json` streams).
"""

import json
import os
import sys
import time

from notifyd.markup import plain_text

EVENTS = ('shown', 'replaced', 'queued', 'waiting', 'suppressed', 'expired', 'dismissed', 'closed',
          'action', 'paused', 'resumed', 'mode', 'history', 'reload', 'error', 'info', 'warn')

RESET = '\033[0m'
DIM = '\033[2m'
BOLD = '\033[1m'
COLORS = {
    'shown': '\033[38;5;114m', 'replaced': '\033[38;5;80m', 'queued': '\033[38;5;179m',
    'waiting': '\033[38;5;179m', 'suppressed': '\033[38;5;244m', 'expired': '\033[38;5;244m',
    'dismissed': '\033[38;5;110m', 'closed': '\033[38;5;110m', 'action': '\033[38;5;177m',
    'paused': '\033[38;5;179m', 'resumed': '\033[38;5;114m', 'mode': '\033[38;5;179m',
    'history': '\033[38;5;110m', 'reload': '\033[38;5;80m',
    'error': '\033[38;5;203m', 'info': '\033[38;5;110m', 'warn': '\033[38;5;179m',
}
URGENCY_COLORS = {'low': '\033[38;5;66m', 'normal': '\033[38;5;153m', 'critical': '\033[1;38;5;203m'}
APP_COLOR = '\033[38;5;147m'
RULE_COLOR = '\033[38;5;140m'
LEVELS = {'debug': 0, 'info': 1, 'warn': 2, 'error': 3}


def use_color(setting, stream):
    if setting in (True, 'always'):
        return True
    if setting in (False, 'never') or os.environ.get('NO_COLOR'):
        return False
    return hasattr(stream, 'isatty') and stream.isatty()


def one_line(text, width):
    text = ' | '.join(part.strip() for part in plain_text(text or '').splitlines() if part.strip())
    return text if len(text) <= width else text[:width - 1] + '…'


def format_pretty(event, color=False, width=None):
    """A log line for EVENT (a dict with at least 'event'; 'n' a notification summary_dict)."""
    paint = (lambda code, text: f'{code}{text}{RESET}') if color else (lambda code, text: text)
    kind = event['event']
    stamp = time.strftime('%H:%M:%S', time.localtime(event.get('time', time.time())))
    parts = [paint(DIM, stamp), paint(COLORS.get(kind, ''), f'{kind:<10}')]
    n = event.get('n')
    if n:
        urgency = n.get('urgency', 'normal')
        parts += [
            paint(DIM, f'#{n["id"]:<4}'),
            paint(APP_COLOR, f'{(n.get("app") or "-")[:14]:<14}'),
            paint(URGENCY_COLORS.get(urgency, ''), f'{urgency:<9}'),
        ]
        text = n.get('summary', '')
        if n.get('body'):
            text = f'{text} | {n["body"]}' if text else n['body']
        width = width or 70
        parts.append(paint(BOLD if urgency == 'critical' else '', one_line(text, width)))
        extra = []
        if kind in ('shown', 'replaced') and 'timeout' in n:
            extra.append(paint(DIM, f'{n["timeout"] / 1000:g}s' if n['timeout'] else 'sticky'))
        if n.get('duplicates', 1) > 1 and kind == 'shown':
            extra.append(paint(DIM, f'x{n["duplicates"]}'))
        if event.get('action'):
            extra.append(paint(COLORS['action'], f'-> {event["action"]}'))
        if event.get('reason'):
            extra.append(paint(DIM, event['reason']))
        if n.get('rules'):
            extra.append(paint(RULE_COLOR, '[' + ', '.join(n['rules']) + ']'))
        parts += extra
    if event.get('message'):
        parts.append(event['message'])
    return '  '.join(parts)


class EventLog:
    def __init__(self, fmt='pretty', color='auto', level='info', stream=None):
        self.stream = stream or sys.stderr
        self.file = None
        self.file_path = None
        self.configure(fmt, color, level)
        self.listeners = []

    def configure(self, fmt='pretty', color='auto', level='info', file=None):
        """FILE: a path that gets every logged line too, uncolored (None: none)."""
        self.fmt = fmt
        self.color = use_color(color, self.stream)
        self.level = LEVELS.get(level, 1)
        path = os.path.expanduser(file) if file else None
        if path != self.file_path:
            if self.file:
                self.file.close()
            self.file, self.file_path = None, path
            if path:
                try:
                    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
                    self.file = open(path, 'a', buffering=1)   # noqa: SIM115 (kept open while configured)
                except OSError as exc:
                    self.file_path = None
                    self.warn(f'log file {path}: {exc.strerror}')

    def emit(self, kind, n=None, level='info', **extra):
        event = {'event': kind, 'time': time.time(), **extra}
        if n is not None:
            event['n'] = n.summary_dict()
        for listener in list(self.listeners):
            listener(event)
        if LEVELS.get(level, 1) < self.level:
            return event
        as_json = self.fmt == 'json'
        line = json.dumps(event, ensure_ascii=False) if as_json else format_pretty(event, self.color)
        try:
            print(line, file=self.stream, flush=True)
        except (BrokenPipeError, ValueError):
            pass
        if self.file:
            try:
                self.file.write((line if as_json or not self.color else format_pretty(event)) + '\n')
            except (OSError, ValueError):
                pass
        return event

    def info(self, message):
        return self.emit('info', message=message)

    def warn(self, message):
        return self.emit('warn', level='warn', message=message)

    def error(self, message):
        return self.emit('error', level='error', message=message)
