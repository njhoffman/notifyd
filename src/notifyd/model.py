"""A notification as received, plus what the rules made of it."""

import time
from dataclasses import dataclass, field

URGENCY_NAMES = ('low', 'normal', 'critical')

# NotificationClosed reasons (spec 1.2)
EXPIRED, DISMISSED, CLOSED, UNDEFINED = 1, 2, 3, 4
REASON_NAMES = {EXPIRED: 'expired', DISMISSED: 'dismissed', CLOSED: 'closed', UNDEFINED: 'undefined'}

# hints that name a tag: a new notification with the same app and tag replaces the old one
TAG_HINTS = ('x-dunst-stack-tag', 'x-canonical-private-synchronous', 'synchronous')


@dataclass
class Notification:
    app: str = ''
    summary: str = ''
    body: str = ''
    app_icon: str = ''
    actions: list = field(default_factory=list)      # [(key, label)]
    hints: dict = field(default_factory=dict)
    requested_timeout: int = -1                      # as sent: -1 server default, 0 never, else ms
    replaces_id: int = 0
    id: int = 0
    mode: str = 'normal'                             # the daemon's mode when it arrived
    duplicates: int = 1                              # how many identical ones this stands for
    commands: dict = field(default_factory=dict)     # action key -> command, for buttons added by rules
    received: float = field(default_factory=time.time)
    # filled in by the rules
    settings: dict = field(default_factory=dict)
    rules: list = field(default_factory=list)        # names of the rules that matched
    timeout: int = 0                                 # the one in effect, ms (0 never)

    @property
    def urgency(self):
        value = self.hints.get('urgency', 1)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 1
        return min(max(value, 0), 2)

    @property
    def urgency_name(self):
        return URGENCY_NAMES[self.urgency]

    @property
    def category(self):
        return str(self.hints.get('category', ''))

    @property
    def desktop_entry(self):
        return str(self.hints.get('desktop-entry', ''))

    @property
    def stack_tag(self):
        for hint in TAG_HINTS:
            value = self.hints.get(hint)
            if value not in (None, ''):
                return str(value)
        return ''

    @property
    def progress(self):
        value = self.hints.get('value')
        if value is None:
            return None
        try:
            return min(max(int(value), 0), 100)
        except (TypeError, ValueError):
            return None

    @property
    def resident(self):
        return bool(self.hints.get('resident', False))

    @property
    def transient(self):
        return bool(self.hints.get('transient', False))

    def action_label(self, key):
        return next((label for k, label in self.actions if k == key), None)

    def summary_dict(self):
        """What logs, history and the watch stream show (no image data)."""
        return {
            'id': self.id,
            'app': self.app,
            'summary': self.summary,
            'body': self.body,
            'urgency': self.urgency_name,
            'category': self.category,
            'stack_tag': self.stack_tag,
            'actions': [key for key, _ in self.actions],
            'rules': list(self.rules),
            'timeout': self.timeout,
            'received': self.received,
            'mode': self.mode,
            'duplicates': self.duplicates,
        }


def actions_from_list(flat):
    """[key1, label1, key2, label2, ...] as sent over D-Bus -> [(key, label)]."""
    flat = list(flat or [])
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]
