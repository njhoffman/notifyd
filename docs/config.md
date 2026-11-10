# Configuration

`~/.config/notifyd/config.yaml` (or `notifyd daemon -c PATH`). Every key is optional; an empty or
missing file gives the deadd look. The daemon reloads the file when it changes (or on SIGHUP, or
`notifyd ctl reload`); a broken file is reported and the old config kept. `notifyd check` validates it.

`notifyd import deadd -o config` writes it from `~/.config/deadd/deadd.yml` and `deadd.css`.

## Keys

| Key | Default | Meaning |
|---|---|---|
| `preset` | `deadd` | style preset (`notifyd presets` lists them) |
| `style` | `{}` | preset keys to override (see Styles) |
| `width` | `350` | px |
| `timeout` | `8s` | `8000`, `8s`, `1500ms`, `2m`, `never` |
| `timeout-critical` | `never` | for critical notifications no rule gives a timeout |
| `max-visible` | `6` | more wait until one closes (`0`: no limit) |
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
| `history.size` / `history.keep` | `100` / `true` | `keep: false` in a rule keeps those out |
| `log.format` / `log.color` / `log.level` | `pretty` / `auto` / `info` | also `--log-format`, `--color`, `-v`/`-q` |
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

  - match: { app: chatty, hint: { x-account: work } }
    preset: card
    style: { background: 'rgba(30, 0, 40, 0.9)' }
```

`match` fields: `app`, `summary`, `body`, `icon`, `category`, `desktop-entry`, `stack-tag`,
`urgency` (`low`/`normal`/`critical`), and `hint: {name: value}`. A value is an exact string,
`/regex/` (`/regex/i` ignores case; searched, not anchored), or a list of those (any matches).

Rewrites: `summary`, `body`, `app` are templates with `{summary}`, `{body}`, `{app}`, `{category}`,
`{urgency}` and the regex's named groups; `urgency` sets the urgency; `stack-tag` makes notifications
replace each other. `notifyd rules --app MPD --summary x` shows what a notification would get.

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
so senders don't hard-code colors:

```
notifyd send 'Song' '<b>Artist</b> · Album
<span class="tag">indie</span> <span class="muted">late night</span>'
```

Every bundled preset has `accent`, `muted`, `good`, `warn`, `bad` and `tag`. `<br>` becomes a line
break, `<img>` its alt text, a bare `&` is escaped, and markup that still does not parse is shown as
plain text.

## Sending

`notifyd send` takes notify-send's flags (`-a -u -t -i -c -h TYPE:NAME:VALUE -A [NAME=]Text -r -p -e -w`)
and adds `--replace-name NAME` (`--replaces-process`, as notify-send.py), `--tag TAG`, `--image PATH`,
`--progress N` and `--close ID`. With actions it waits and prints the key chosen. It works with any
notification daemon.

## Control

`notifyd ctl status | history [-n N] [--json] | close ID | action ID [KEY] | close-all | pause | resume |
toggle | reload | preset NAME | preview [PRESET] | watch [--json]`. `watch` streams the daemon's
event log, colored, or as JSON lines.
