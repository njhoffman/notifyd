# Configuration

`~/.config/notifyd/config.yaml` (or `notifyd daemon -c PATH`). Every key is optional; an empty or
missing file gives the deadd look. The daemon reloads the file when it changes (or on SIGHUP, or
`notifyd ctl reload`); a broken file is reported and the old config kept. `notifyd check` validates it.

`notifyd import deadd -o config` writes it from `~/.config/deadd/deadd.yml` and `deadd.css`;
`notifyd import dunst` converts a dunstrc (rule sections included) on top of the dunst preset.

## Keys

| Key | Default | Meaning |
|---|---|---|
| `preset` | `deadd` | style preset (`notifyd presets` lists them) |
| `style` | `{}` | preset keys to override (see Styles) |
| `width` | `350` | px |
| `timeout` | `8s` | `8000`, `8s`, `1500ms`, `2m`, `never` |
| `timeout-critical` | `never` | for critical notifications no rule gives a timeout |
| `max-visible` | `6` | more wait until one closes (`0`: no limit) |
| `group-by` | `[]` | e.g. `[app]`: a new notification with the same values replaces the open one, shown `×N` (fields: app, summary, category, urgency; set it in a rule to group one app only) |
| `stack-duplicates` | `true` | an identical notification (app, summary, body, urgency) replaces the open one, shown `×N` |
| `position.anchor` | `top-right` | `top-`/`bottom-` + `left`/`center`/`right` |
| `position.margin` | `[30, 5]` | `[vertical, horizontal]` from the anchored edges |
| `position.gap` | `10` | between notifications |
| `position.follow` | `mouse` | `mouse`: the monitor under the pointer; `none`: `position.monitor` |
| `icon.size` / `icon.name` | `25` / - | app icon in the header; `name` replaces the sender's |
| `image.size` / `image.path` / `image.margin-right` | `75` / - / `0` | the picture on the left |
| `body.max-lines` | `6` | longer bodies end in … |
| `markup` | `full` | `full` (Pango), `strip` (text only), `plain` (tags shown as typed) |
| `hide` | `[]` | any of `app-icon app-name time title body image actions progress` |
| `class` | - | extra CSS class on the card |
| `skip` | `false` | not shown; still logged and kept in history |
| `sound` | - | a sound file or freedesktop sound name played when shown; `false` silences (else the sender's `sound-file`/`sound-name` hints, unless `suppress-sound`) |
| `buttons` | `[]` | extra buttons that run commands: `[{label: Next, run: mpc next}]` |
| `run` | - | a shell command run for each matching notification, shown or skipped |
| `idle-threshold` | `0` | notifications do not expire while you have been idle this long (`0`: off) |
| `history.size` / `history.keep` / `history.persist` | `100` / `true` / `true` | `keep: false` in a rule keeps those out; `persist` keeps it in `$XDG_STATE_HOME/notifyd/history.jsonl` |
| `log.format` / `log.color` / `log.level` / `log.file` | `pretty` / `auto` / `info` / - | also `--log-format`, `--color`, `-v`/`-q`; `file` gets plain lines too |
| `center.width` / `side` / `hide-on-leave` / `mpris` / `volume` / `modes` / `cards` / `keyboard` / `remote-art` / `toggle-key` | `400` / `right` / `true` / `true` / `true` / `true` / `20` / `true` / `true` / `<Super>grave` | the notification center (`notifyd ctl center`, SIGUSR1); `mpris` shows what a media player plays, with buttons; `volume` a slider and mute for the default output (needs `pactl`, PulseAudio or PipeWire); `modes` a picker of `normal` and the modes rules match on by name; `cards` how many history entries it shows before a "show more" button; `keyboard` grabs the keyboard while it is open (keys below); `remote-art` downloads a player's http(s) cover art for the now-playing row (3 s, 2 MB at most, cached in `~/.cache/notifyd/art`; notification images never come from the network); `toggle-key` (a GTK accelerator, `''` for none) hides it: the key your window manager binds to `notifyd ctl center` never reaches it while the center holds the keyboard |
| `fade.in` / `fade.out` | `0` / `150ms` | opacity fades; picom fades windows in but not translucent ones out |
| `click.left` / `middle` / `right` | `dismiss` / `dismiss-all` / `default-action` | or `none` |
| `rules` | `[]` | see Rules |

## Rules

Each rule has an optional `name`, a `match`, and any of the keys above (except `position`,
`max-visible`, `log`, `click`, `rules`). Rules apply in order; each sees what earlier rules rewrote,
and later ones override earlier ones. A rule's `timeout` wins over the one the sender asked for.

```yaml
rules:
  - name: songs
    match: { app: [musicman, MPD, Spotify] }
    timeout: 12s
    hide: [app-icon, app-name]
    image: { size: 60 }

  - name: battery
    match: { summary: '/(?P<pct>\d+)% battery/i' }
    urgency: critical
    summary: 'Battery at {pct}%'

  - name: do-not-disturb                  # notifyd ctl mode dnd / notifyd ctl mode normal
    match: { mode: dnd, urgency: [low, normal] }
    skip: true

  - match: { app: chatty, hint: { x-account: work } }
    preset: card
    style: { background: 'rgba(30, 0, 40, 0.9)' }
```

`match` fields: `app`, `summary`, `body`, `icon`, `category`, `desktop-entry`, `stack-tag`,
`urgency` (`low`/`normal`/`critical`), `mode` (set with `notifyd ctl mode NAME`; `normal` by
default), `process` and `parent` (the sending process and its parent, as `ctl watch` shows them
after `via`; process names are cut to 15 characters) and `hint: {name: value}`. A value is an exact string,
`/regex/` (`/regex/i` ignores case; searched, not anchored), or a list of those (any matches).

Rewrites: `summary`, `body`, `app` are templates with `{summary}`, `{body}`, `{app}`, `{category}`,
`{urgency}` and the regex's named groups; `urgency` sets the urgency; `stack-tag` makes notifications
replace each other. `notifyd rules --app MPD --summary x` shows what a notification would get.

Commands from `run` and `buttons` run with `sh` in the background, with `NOTIFYD_ID`, `NOTIFYD_APP`,
`NOTIFYD_SUMMARY`, `NOTIFYD_BODY`, `NOTIFYD_URGENCY`, `NOTIFYD_CATEGORY`, `NOTIFYD_ICON`,
`NOTIFYD_STACK_TAG` and `NOTIFYD_RULES` set, and the notification as JSON on stdin. A failing
command is logged with its last line of stderr.

```yaml
rules:
  - match: { app: [MPD, Spotify] }
    buttons:
      - { label: Next, run: playerctl next }
      - { label: Pause, run: playerctl play-pause }
  - match: { app: /chat/i }
    run: 'jq -c . >> ~/.local/state/chat-notifications.jsonl'
```

## Styles

A preset is `description` + `style`; bundled ones are in `src/notifyd/presets/`, and a file in
`~/.config/notifyd/presets/NAME.yaml` adds or shadows one. Style keys:

```yaml
style:
  font-family: Iosevka Term
  font-size: 13pt
  background: 'rgba(0, 0, 0, 0.8)'   # quote rgba(...) inside { } mappings
  foreground: '#acf'
  radius: 0
  border-width: 0
  border-color: '#3B9Cd7'
  padding: [10, 15]                  # [vertical, horizontal]
  spacing: 4                         # between header, content, actions
  title: { size: 16px, weight: bold, color: '#fff', style: italic, family: Sans }
  body: { ... }  appname: { ... }  time: { ... }
  low: { background: ..., foreground: ..., border-color: ... }   # also normal, critical
  progress: { color: '#acf', background: '...', height: 6 }
  button: { background: ..., foreground: ..., hover: ..., radius: 3 }
  classes:                           # named styles for notification text, see Markup
    accent: { foreground: '#7aa2f7', weight: bold }
  css: '& .title { letter-spacing: 1px; }'   # raw GTK CSS; & is this style's scope
```

The shadow comes from the compositor. The windows are named `notifyd`, class `Notifyd`, so picom
rules can target them (picom's `! name~=''` shadow exclusion does not hit them).

`notifyd ctl preview [PRESET]` shows sample notifications in a preset; `notifyd ctl preset NAME`
switches until the next reload; `notifyd presets --pick` does both from fzf as you move.

## Markup

Summary and body take Pango markup: `<b> <i> <u> <s> <tt> <sub> <sup> <small> <big> <a href>` and
`<span foreground= background= size= weight= alpha= ...>`. A span can use a preset's named styles,
so senders don't hard-code colors. A link is `<a href="..." [title="..."]>` (lowercase, not
nested); clicking it opens http, https and mailto links with the default handler (anything else,
such as `file:`, is refused with a `warn` log line) and does not dismiss the notification:

```
notifyd send 'Song' '<b>Artist</b> · Album
<span class="tag">indie</span> <span class="muted">late night</span>'
```

Every bundled preset has `accent`, `muted`, `good`, `warn`, `bad` and `tag`. `<br>` becomes a line
break, `<img>` its alt text, a bare `&` or `<` (one that cannot start a tag) is escaped, and markup that still does not parse is shown as
typed, tags and all: nothing is stripped. The popup and the center card then show a `markup error`
mark in the header (its tooltip has Pango's reason; the style key `markup-error` sets its color), and
the event log has a `warn` line for it.

## Sending

`notifyd send` takes notify-send's flags (`-a -u -t -i -c -h TYPE:NAME:VALUE -A [NAME=]Text -r -p -e -w`)
and adds `--replace-name NAME` (`--replaces-process`, as notify-send.py), `--tag TAG`, `--image PATH`,
`--progress N` and `--close ID`. With actions it waits and prints the key chosen. It works with any
notification daemon.

## Control

`notifyd ctl status | list [--json|--ids] | history [-n N] [--json] | pop [ID] | forget ID|--all | center [toggle|show|hide] | close ID | action ID [KEY] |
close-all | pause | resume | toggle | mode [NAME] | reload | preset NAME | preview [PRESET] | watch [--json]`.
`ctl bar [--follow]` prints a status-bar line (do not disturb, the mode, how many are held back;
see `notifyd help ctl bar` for a polybar module). `history --pick` picks one in fzf and shows it again. Each notification records its `sender` (the sending process's pid and name, its parent's name, and the `desktop-entry` hint): in `history --json`, `list --json` and `watch --json`, and as `via PROCESS (PARENT)` on `watch` lines. In the center: `j`/`k` or the arrows move between cards (and load more past the last), `g`/`G` first and last, `Enter` acts like a left click (shows a closed one again), `d`/`Delete` closes or forgets it, `C` clears, `p` toggles do not disturb, `m` the next mode, `space`/`n`/`N` play-pause, next and previous, `+`/`-` the volume, `Esc`/`q` (or `center.toggle-key`) closes it. The style key `center-selected` outlines the selected card; `center-card` sets the
background of cards in the center. dunst's color hints (`fgcolor`, `bgcolor`, `frcolor`, `hlcolor`) color a single notification. `watch` streams the daemon's
event log, colored, or as JSON lines.
