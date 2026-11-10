# Research: notification daemons and current usage

Surveyed 2026-11-10. Sources: the freedesktop Desktop Notifications spec (1.2), the
documentation and configs of the daemons below, this desktop's configs
(`~/.config/deadd/`, `~/.config/dunst/`, `~/.config/picom/picom.conf`,
`~/.shell/scripts/notify-daemon`) and the projects that send notifications here
(musicman, backdrop, displayd, i3 scripts).

## 1. The protocol (what every daemon must do)

`org.freedesktop.Notifications` on the session bus, `/org/freedesktop/Notifications`:

| Method / signal | Notes |
|---|---|
| `Notify(app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout) -> id` | `replaces_id` must replace in place and return the same id (deadd returns a new one; backdrop works around it) |
| `CloseNotification(id)` | emits `NotificationClosed(id, 3)` |
| `GetCapabilities()` | advertise `body`, `body-markup`, `body-hyperlinks`, `body-images`, `actions`, `action-icons`, `icon-static`, `persistence`, `x-*` extras |
| `GetServerInformation()` | name, vendor, version, spec version `1.2` |
| `NotificationClosed(id, reason)` | 1 expired, 2 dismissed, 3 closed by call, 4 undefined |
| `ActionInvoked(id, key)` / `ActivationToken(id, token)` | actions, incl. `default` |

Standard hints: `urgency` (byte 0/1/2), `category`, `desktop-entry`, `image-data`
(iiibiiay), `image-path`, `icon_data` (legacy), `resident`, `transient`,
`sound-file`, `sound-name`, `suppress-sound`, `action-icons`, `x`, `y`.
Common vendor hints worth honoring: `value` (int 0-100, progress bar; dunst/deadd/swaync),
`x-dunst-stack-tag` / `x-canonical-private-synchronous` / `synchronous` (replace by
tag), `hlcolor`, `fgcolor`, `bgcolor`, `frcolor` (dunst per-notification colors),
`x-dunst-progress-bar-*`.

Body markup per spec: `<b> <i> <u> <a href> <img src alt>` only. In practice senders
(musicman, backdrop) also emit Pango `<span foreground= background= size= alpha= weight=>`,
which dunst renders and deadd strips.

## 2. Daemons compared

| Feature | deadd | dunst | mako | swaync | wired | others |
|---|---|---|---|---|---|---|
| Toolkit / platform | Haskell, GTK3, X11 | C, cairo/pango, X11+Wayland | C, Wayland only | Vala, GTK3/4, Wayland | Rust, cairo, X11+Wayland | fnott (Wayland), xfce4-notifyd, runst (Rust, minimal), tiramisu (prints to stdout) |
| Styling | GTK CSS + YAML | ini keys (colors, frame, font, format string) | ini + criteria sections | GTK CSS | RON layout tree (blocks: text, image, progress, buttons) | |
| Per-app rules | `modifications` (match app-name/title/body regex -> modify timeout, class-name, image-size, actions, ...) | rules: match appname, summary, body, category, urgency, stack_tag, desktop_entry, msg_urgency -> set timeout, urgency, colors, format, icon, `skip_display`, `history_ignore`, `script`, `set_stack_tag`, `override_pause_level` | `[app-name=x urgency=low]` criteria, any style key | `notification-visibility` rules + scripts | per-layout criteria | |
| Notification center / history | yes (sidebar, toggled with SIGUSR1; buttons, sliders) | history list via `dunstctl history`/`history-pop`, rofi/dmenu picker | `makoctl history`, `restore` | full control center with widgets (DND, MPRIS, volume, buttons) | no | xfce4-notifyd logs to sqlite |
| DND / modes | `notify-send.py` toggle via config | `dunstctl set-paused`, pause levels (0-100), rules can override | modes (`makoctl mode -a dnd`), styling per mode | DND + inhibitors | pause | |
| Progress bar (`value` hint) | yes | yes | no (text only) | yes | yes | |
| Replace by tag | no (new id) | `x-dunst-stack-tag`, `x-canonical-private-synchronous` | `group-by` | yes | yes | |
| Grouping / stacking duplicates | no | `stack_duplicates`, count shown | `group-by=app-name,summary`, `max-visible` | groups per app | no | |
| Scripts on notify | no | `script` per rule, env with all fields | `on-notify` exec | per-rule scripts | no | |
| Actions / buttons | yes, icons, `action-commands` in config | context menu (dmenu/rofi), `do_action` on click | `makoctl invoke`, menu | inline buttons, inline reply | buttons block | |
| Idle handling | no | `idle_threshold`: no timeout while user idle | no | no | no | |
| Fullscreen | shows | `fullscreen = show/delay/pushback` | layer | layer | | |
| Follow focus / mouse | `follow-mouse` | `follow = mouse/keyboard/none` | `output` | | | |
| Markup | `<b><i><u><a>` only | full Pango | full Pango | Pango | Pango | |
| Image hints | image-path needs `file://` (backdrop found a plain path showed a placeholder) | path or URI, `image-data` | yes | yes | yes | |
| CLI control | signals only | `dunstctl` (close, close-all, history-pop, action, context, count, is-paused, set-paused, reload, rule enable/disable) | `makoctl` | `swaync-client` | `wired --run` | |
| Logging | none useful | `-verbosity`, plain | plain | `G_MESSAGES_DEBUG` | `RUST_LOG` | tiramisu: every notification as JSON/text on stdout |

What is worth taking from each:

- **deadd**: the look (black translucent card, no frame, compositor shadow), the
  notification center sidebar, per-notification `class-name` mapped to CSS, buttons in the
  center, image sizing per app.
- **dunst**: the rule engine (match on any field incl. `stack_tag`, `category`,
  `desktop_entry`; modify any style key, `skip_display`, `history_ignore`, `script`),
  stack tags, duplicates counter, pause levels, `idle_threshold`, `fullscreen` policy,
  full Pango markup, a real CLI (`dunstctl`).
- **mako**: modes as a first-class concept (DND, presentation, quiet) that rules can key on;
  terse criteria syntax `[app-name=x urgency=low]`.
- **swaync**: control-center widgets (DND toggle, MPRIS, volume) and inline reply.
- **wired**: layout as data (blocks), which is what makes preset styles more than color swaps.
- **tiramisu**: notifications as a structured stream on stdout, which is the basis for the
  colorful log and for scripting (`notifyd watch --json`).

## 3. This desktop today

- **Daemon switcher**: `~/.shell/scripts/notify-daemon` picks deadd (default), dunst, wired
  or runst via `SHELL_NOTIFY_DAEMON`, stops the others, writes a D-Bus activation file
  (`~/.local/share/dbus-1/services/org.freedesktop.Notifications.service`) and runs the
  daemon's user unit (`deadd-notification-center.service`, `WantedBy=xsession.target`).
  notifyd should become one more entry (`[notifyd]=notifyd`, unit `notifyd.service`) so
  switching back to deadd stays one command.
- **deadd config** (`~/.config/deadd/deadd.yml`): width 350, margin-top 30, margin-right 5,
  follow-mouse, default timeout 8000 ms, app icon 25 px, image 75 px. Rules: Spotify
  (timeout 3000, class Spotify, image 80), backdrop (image 110), Volume and tmux-session
  (no app icon/name, image 40), mpd-notification (no title/icon/name, margin-right 15),
  musicman (no icon/name, image 60). `~/.config/deadd.yml` is an older copy without the
  backdrop rule.
- **deadd CSS** (`~/.config/deadd/deadd.css`): card and center `rgba(0,0,0,0.8)`, labels
  `#acf`, title bold 16 px, app name and time 12 px, critical background
  `rgba(255,0,0,0.5)` with label `#faa`, content margins 15 px.
- **Shadow**: drawn by picom (`shadow = true`, opacity 0.8, offset -6/-3, radius 5), not by
  deadd. picom excludes `class_g = 'Dunst'` from shadows and other rules; notifyd's window
  class must not match those, and should be set explicitly (`Notifyd`) so picom rules can
  target it.
- **dunst config** (`~/.config/dunst/dunstrc`): Iosevka Term 13, `format = "<b>%s</b>%p\n%b"`,
  progress bar on, history 100, stack duplicates, urgency colors on `#000811`.
- **notify-send.py** (`~/.shell/scripts/notify-send.py`, vendored `notify_send_py`): adds
  `--replaces-process` (replace by a name, kept in a state file) and `--action` that blocks
  and prints the chosen key.

## 4. Senders

| Sender | How it notifies | What it relies on | Problems seen |
|---|---|---|---|
| musicman (`cli/lib/notify/notification.js`) | `notify-send --print-id --replace-id --expire-time=5000 --app-name=<player>` with `image-path` cover | Pango `<span foreground background>` chips for tags, replace id | app-name is the player (`MPD` or `Spotify`, `cli/lib/player/players.js:64,221`), so deadd's `musicman` rule never matches and the `Spotify` rule (timeout 3000) shortens song notifications; chips lose their color in deadd |
| backdrop (`src/utils/notify.js`) | `notify-send --print-id --app-name=backdrop --urgency=low --replace-id` + `x-dunst-stack-tag:backdrop-<monitor>` + `image-path` file URI | Pango `<span size alpha foreground weight>`, replace id | deadd answers a replace with a new id, so backdrop closes the old id with gdbus; spans stripped by deadd |
| displayd (`lib/core.py` `notify_cmd`) | `notify-send.py -a displayd -u U -t 8000 -i video-display --replaces-process displayd ... --action change:Change layout`, else `notify-send -A` | actions (blocking wait for the chosen key), replace by process name | none known; depends on `ActionInvoked` working |
| i3 volume (`~/.config/i3/bin/i3_volume.sh`) | libnotify with app-name `Volume` | probably the `value` hint for a bar | |
| zsh-auto-notify, tmux-session, mpd-notification | plain notify-send | | |

## 5. Implications for notifyd

1. Honor `replaces_id` properly (same id back) and replace-by-tag (`x-dunst-stack-tag`,
   `x-canonical-private-synchronous`, `synchronous`): fixes backdrop's workaround and makes
   musicman's replace work without an id round trip.
2. Render full Pango markup in summary and body (fixes musicman's chips and backdrop's
   rich body at once), with an option to strip it per rule.
3. Rules must be able to override the sender's own `expire_timeout` (musicman sends 5000
   explicitly) and must match on more than app-name (category, desktop-entry, hints,
   summary/body regex), since app-name alone is not stable across senders.
4. Keep deadd's look as the default preset, with the shadow left to picom.
