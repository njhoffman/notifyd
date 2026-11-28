# Configuration

`~/.config/notifyd/config.yaml` (or `notifyd daemon -c PATH`). Every key is optional; an empty or
missing file gives the deadd look. The daemon reloads the file when it changes, and when a preset in
`~/.config/notifyd/presets/` does (or on SIGHUP, or `notifyd ctl reload`): open popups and the
notification center take the new look at once, so styles can be tuned with them on screen (their
timers, text and actions stay; `run:` does not fire again). A broken file is shown as a notification
that stays until it is fixed, and the old config is kept. `notifyd check` validates it.

`notifyd import deadd -o config` writes it from `~/.config/deadd/deadd.yml` and `deadd.css`;
`notifyd import dunst` converts a dunstrc (rule sections included) on top of the dunst preset.

## Keys

| Key | Default | Meaning |
|---|---|---|
| `preset` | `deadd` | style preset (`notifyd presets` lists them) |
| `style` | `{}` | preset keys to override (see Styles) |
| `width` | `350` | px |
| `timeout` | `8s` | `8000`, `8s`, `1500ms`, `2m`, `1h`, `never` |
| `timeout-critical` | `never` | for critical notifications no rule gives a timeout |
| `max-visible` | `6` | more wait until one closes (`0`: no limit), in each stack (a rule's `position` makes another); one that would not fit in the monitor's work area waits too (oldest first), except the first on screen |
| `indicate-waiting` | `true` | the last popup of each stack shows `+N` while N wait for room in it (`max-visible`, or no space on screen); daemon-wide |
| `group-by` | `[]` | e.g. `[app]`: a new notification with the same values replaces the open one, shown `×N` (fields: app, summary, category, urgency; set it in a rule to group one app only) |
| `stack-duplicates` | `true` | an identical notification (app, summary, body, urgency) replaces the open one, shown `×N` |
| `position.anchor` | `top-right` | `top-`/`bottom-` + `left`/`center`/`right`; a rule's `position` starts a stack of its own (see Rules) |
| `position.margin` | `[30, 5]` | `[vertical, horizontal]` from the anchored edges |
| `position.gap` | `10` | between notifications |
| `position.follow` | `mouse` | `mouse`: the monitor under the pointer; `focus`: the focused window's; `none`: `position.monitor` (also when no window has focus) |
| `position.monitor` | `0` | an index, or an output name as `xrandr` lists it (`DP-1`, any case), which stays put when the layout changes; an unknown name warns once and uses the primary monitor |
| `icon.size` / `icon.name` | `25` / - | app icon in the header; `name` replaces the sender's; with neither, the icon of the `desktop-entry` hint's `.desktop` file |
| `image.size` / `image.path` / `image.margin-right` | `75` / - / `0` | the picture on the left |
| `title.max-lines` / `body.max-lines` | `0` / `6` | longer summaries and bodies end in … (`0`: no limit), counting both wrapped lines and line breaks; hovering a popup shows its whole body, and `e` does on the selected center card |
| `markup` | `full` | `full` (Pango), `strip` (text only), `plain` (tags shown as typed) |
| `lock.pause` / `lock.processes` | `false` / `[i3lock, xsecurelock, slock, betterlockscreen]` | `pause: true` holds every notification back (critical ones too: popups can stack above an X locker) while the screen is locked, checked every 2 s: logind's `LockedHint` for this session, or one of `processes` running; unlocking shows them, unless do not disturb is on. `ctl status` shows `locked`. Not settable in a rule |
| `timeout-bar` | `false` | `true` (also in a rule): a thin bar along the bottom of the popup drains as its timer runs and stops while it is hovered; its look is the style key `timeout-bar: {color, background, height}` (default cyan `#00d7d7`, transparent background, 2 px; drawn at exactly that height, whatever the GTK theme), e.g. `style: {timeout-bar: {color: '#ff8800', height: 3}}` |
| `ignore-close` | `false` | `true` (usually in a rule): the app closing its own notification (CloseNotification) is ignored, so it stays until it expires or you close it (dunst's `ignore_dbusclose`) |
| `fullscreen` | `show` | `delay`: while the focused window covers the stack's monitor (a video, a game), notifications wait and show when it ends (critical ones always show); `match: {fullscreen: true}` matches on that state. dunst's `pushback` is not possible with override-redirect popups |
| `hide` | `[]` | any of `app-icon app-name time title body image actions progress` |
| `class` | - | extra CSS class on the card |
| `skip` | `false` | not shown; still logged and kept in history |
| `sound` | - | a sound file or freedesktop sound name played when shown; `false` silences (else the sender's `sound-file`/`sound-name` hints, unless `suppress-sound`) |
| `buttons` | `[]` | extra buttons that run commands: `[{label: Next, run: mpc next}]` |
| `actions.drop` | `[]` | the sender's actions to leave out, by key or label, as text or `/regex/i`: `actions: {drop: [settings, /^mute/i]}` drops Chromium's Settings button; a dropped action is not drawn and cannot be invoked |
| `run` | - | a shell command run for each matching notification, shown or skipped |
| `idle-threshold` | `0` | notifications do not expire while you have been idle this long (`0`: off) |
| `history.size` / `history.keep` / `history.persist` | `100` / `true` / `true` | `keep: false` in a rule keeps those out; `persist` keeps it in `$XDG_STATE_HOME/notifyd/history.jsonl` (mode 0600, its directory 0700: it holds whole bodies). Entries keep the app icon, image path, markup mode and sender; an image sent as image-data is kept as a 64 px thumbnail in `$XDG_CACHE_HOME/notifyd/thumbs/` (removed with its entry), so center cards and `ctl pop` show it. `pop` styles it with the rules again but does not rerun their `run:` or sounds |
| `log.format` / `log.color` / `log.level` / `log.file` | `pretty` / `auto` / `info` / - | also `--log-format`, `--color`, `-v`/`-q`; `file` gets plain lines too |
| `center.width` / `side` / `hide-on-leave` / `mpris` / `volume` / `modes` / `cards` / `keyboard` / `remote-art` / `toggle-key` | `400` / `right` / `true` / `true` / `true` / `true` / `20` / `true` / `true` / `<Super>grave` | the notification center (`notifyd ctl center`, SIGUSR1); `mpris` shows what a media player plays, with buttons (`center.mpris-ignore: [firefox, chromium]` passes over players by the name after `org.mpris.MediaPlayer2.`, so browser tabs do not take the row); `center.group: true` starts the center with the history grouped by app (the By app button, or `a`, switches until the next reload), newest app first, each group showing its newest card until its header is clicked (the header's × forgets the group; `cards` counts entries before grouping); `center.buttons: [{label, run, state, icon}]` adds a row of buttons under the volume (`icon`: a theme icon name such as `network-wireless-symbolic`, drawn before the label): a click runs `run` (sh -c, not waited for), and a button with `state` shows as on (style key `button.on`) while that command exits 0, checked when the center opens and after each click; `volume` a slider and mute for the default output (needs `pactl`, PulseAudio or PipeWire); `modes` a picker of `normal` and the modes rules match on by name; `cards` how many history entries it shows before a "show more" button; `keyboard` grabs the keyboard while it is open (keys below); `remote-art` downloads a player's http(s) cover art for the now-playing row (3 s, 2 MB at most, cached in `~/.cache/notifyd/art`; notification images never come from the network); `toggle-key` (a GTK accelerator, `''` for none) hides it: the key your window manager binds to `notifyd ctl center` never reaches it while the center holds the keyboard |
| `fade.in` / `fade.out` | `0` / `150ms` | opacity fades; picom fades windows in but not translucent ones out |
| `click.left` / `middle` / `right` | `dismiss` / `dismiss-all` / `default-action` | or `none`, or `exec: CMD` (runs CMD, see below, and dismisses) |
| `rules` | `[]` | see Rules |

## Rules

Each rule has an optional `name`, a `match`, and any of the keys above except the daemon-wide ones
(`max-visible`, `indicate-waiting`, `log`, `click`, `center`, `lock`, `rules`), which a
rule refuses as unknown. Rules apply in order; each sees what earlier rules rewrote,
and later ones override earlier ones. A rule's `timeout` wins over the one the sender asked for.

A rule's `position` (any of `anchor`, `margin`, `gap`, `follow`, `monitor`, on top of the config's)
puts what it matches in a stack of its own, laid out from its own anchor with its own `max-visible`
room and `+N`, so a volume bar can sit at the bottom center while chat stacks at the top right:

```yaml
rules:
  - match: { app: Volume }
    position: { anchor: bottom-center, margin: 80 }
```

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
after `via`; process names are cut to 15 characters), `fullscreen` (`true` when the focused window was
fullscreen as it arrived) and `hint: {name: value}`. A value is an exact string,
`/regex/` (`/regex/i` ignores case; searched, not anchored), or a list of those (any matches).

Rewrites: `summary`, `body`, `app` are templates with `{summary}`, `{body}`, `{app}`, `{category}`,
`{urgency}` and the regex's named groups; `urgency` sets the urgency; `stack-tag` makes notifications
replace each other. `notifyd rules --app MPD --summary x` shows what a notification would get.

Commands from `run` and `buttons` run with `sh` in the background, with `NOTIFYD_ID`, `NOTIFYD_APP`,
`NOTIFYD_SUMMARY`, `NOTIFYD_BODY`, `NOTIFYD_URGENCY`, `NOTIFYD_CATEGORY`, `NOTIFYD_ICON`,
`NOTIFYD_STACK_TAG` and `NOTIFYD_RULES` set, and the notification as JSON on stdin. A failing
command is logged with its last line of stderr.

`run` runs when a notification arrives; `run-on` picks the events: any of `receive` (the default),
`action` (one of its actions is invoked: `NOTIFYD_ACTION` is its key) and `close` (it closes:
`NOTIFYD_CLOSE_REASON` is `expired`, `dismissed`, `closed` or `undefined`; one replaced by a newer
notification does not count). `NOTIFYD_EVENT` says which. A click can run a command too:
`click.middle: 'exec: CMD'` runs CMD with the same variables (`NOTIFYD_EVENT=click`, `NOTIFYD_BUTTON`)
and dismisses the notification.

A rule's `filter` command can rewrite a notification before it shows. It gets the same variables
(`NOTIFYD_EVENT=filter`) and JSON, and prints a JSON object with any of `summary`, `body`, `icon`,
`urgency` (`low`, `normal`, `critical` or 0-2), `progress` (0-100, `null` drops the bar), `hints`
(merged into the sent ones) and `skip` (`true`: not shown). The notification waits for it, up to
`filter-timeout` (default `500ms`; the D-Bus reply and other notifications never wait); no output
leaves it as it is, and a failure, a timeout or output that is not an object shows it unchanged with a
warning. The rules are not matched again on what it prints, and `run` sees the filtered notification.

```yaml
rules:
  - match: { app: Signal }
    filter: ~/bin/signal-tidy     # e.g. {"body": "...", "urgency": "low"}
    filter-timeout: 300ms
```

```yaml
click: { middle: 'exec: printf %s "$NOTIFYD_BODY" | xclip -selection clipboard' }
rules:
  - match: { app: Slack }
    run: ~/bin/slack-log      # NOTIFYD_EVENT: receive, action or close
    run-on: [receive, action, close]
```

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
  title-end: { size: 12px, color: '#888', opacity: 1 }   # the x-notifyd-title-end hint (see Markup)
  body: { ... }  appname: { ... }  time: { ... }  count: { ... }   # each part also takes opacity
  body: { line-spacing: 2 }          # px between the body's lines (wrapped ones too)
  low: { background: ..., foreground: ..., border-color: ... }   # also normal, critical
  progress: { color: '#acf', background: '...', height: 6 }     # the value hint's bar
  timeout-bar: { color: '#00d7d7', background: transparent, height: 2 }   # with timeout-bar: true
  button: { background: ..., foreground: ..., hover: ..., radius: 3, on: ... }   # on: a center button whose state is on
  accent: '#7aa2f7'                  # on-color of center buttons when button.on is not set
  markup-error: '#e0af68'            # the "markup error" mark
  center-selected: 'rgba(255, 255, 255, 0.45)'   # outline of the keyboard-selected card
  classes:                           # named styles for notification text, see Markup
    accent: { foreground: '#7aa2f7', weight: bold }
  css: '& .title { letter-spacing: 1px; }'   # raw GTK CSS; & is this style's scope
```

For anything else, `css` can target the widgets' classes: `.title`, `.title-row`, `.title-end`, `.body`, `.appname`, `.time`,
`.count`, `.waiting` (the `+N`), `.markup-error`, `.image`, `.appicon`, and in the center
`.center-card`, `.center-group`, `.center-button`, `.center-more`.

The shadow comes from the compositor. The windows are named `notifyd`, class `Notifyd`, so picom
rules can target them (picom's `! name~=''` shadow exclusion does not hit them).

An icon or picture that does not load (no such file, not an image, an icon name the theme lacks) is
logged as a `warn` line once per name or path per run, with close names for a misspelled icon;
`notifyd check` flags `icon.name` and `image.path` settings that would not draw. `notifyd icons
[PATTERN]` lists the current icon theme's names with their context, and `notifyd icons --pick`
browses them in fzf, drawing each in the preview (chafa) and in a popup as you move.

`notifyd ctl preview [PRESET]` shows sample notifications in a preset; `notifyd ctl preset NAME`
switches until the next reload; `notifyd presets --pick` does both from fzf as you move. `just preview`
(`tools/preview.sh`) shows every preset side by side on private daemons, one sample card each, and
`just preview --each` one at a time; `docs/presets.md` has screenshots and each preset's source.

## Markup

Summary and body take Pango markup: `<b> <i> <u> <s> <tt> <sub> <sup> <small> <big> <a href>` and
`<span foreground= background= size= weight= alpha= ...>`. A span can use a preset's named styles,
so senders don't hard-code colors. A link is `<a href="..." [title="..."]>` (lowercase, not
nested); clicking it opens http, https and mailto links with the default handler (anything else,
such as `file:`, is refused with a `warn` log line) and does not dismiss the notification. A link
`<a href="action:KEY">` invokes the notification's own action KEY instead, as its button would
(`default` too, which has no button): the sender gets `ActionInvoked`, and a card in the center
that has already closed does nothing. An action link is drawn as the sender styled its text (no link
color, no underline; the pointer turns into a hand over it), so a tag or word can be the thing to click:

```
notifyd send 'Song' '<b>Artist</b> · Album
<span class="tag">indie</span> <span class="muted">late night</span>'
```

The hint `x-notifyd-title-end` (a string, Pango markup) is drawn at the right end of the title's
line: a rating, a count, a state. It is never cut short; the title is, when `title.max-lines` caps
it. By default it is plainer and dimmer than the title (normal weight and style, 85% size, opacity
0.65), and the `title-end` style key changes that. Hiding `title` hides it too; daemons that do not
know it ignore it:

```
notifyd send -h 'string:x-notifyd-title-end:<span foreground="#ff8fc7">♥</span> 4.5' 'Song' 'Artist'
```

A class takes Pango's span attributes (`foreground`, `background`, `weight`, `size`, `rise`, `line-height`,
...), with `-` for `_`. `line-height` makes a span's background taller along with its line, so it does
not keep wrapped tags apart; `body.line-spacing` (px) leaves a gap between the body's lines instead:

```yaml
rules:
  - match: { app: [MPD, Spotify, musicman] }
    style: { body: { line-spacing: 2 } }   # wrapped tags (span backgrounds) no longer touch
```

Every bundled preset has `accent`, `muted`, `good`, `warn`, `bad` and `tag`. `<br>` and a closing
`</p>` or `</div>` become a line break (an opening `<p>`/`<div>` goes), `<img>` its alt text, HTML entities Pango lacks (`&nbsp;`, `&hellip;`) become characters, an unknown one
(`&foo;`) and a bare `&` or `<` (one that cannot start a tag) are escaped, and markup that still does not parse is shown as
typed, tags and all: nothing is stripped. The popup and the center card then show a `markup error`
mark in the header (its tooltip has Pango's reason; the style key `markup-error` sets its color), and
the event log has a `warn` line for it.

## Sending

`notifyd-send` (or `notifyd send`) works with any notification daemon. It takes notify-send's flags
(`-a -u -t -i -c -e -h TYPE:NAME:VALUE -A [NAME=]Text -r -p -w -v`) and notify-send.py's
(`--replaces-id`, `--replaces-process NAME`, `--dontQuitOnAction`, `--close ID`, several values after
one `--hint` or `--action`, `\n` and `\t` in the summary and body). `-h` is `--hint`, as in
notify-send. With actions it waits, prints the key of the one chosen and exits;
`--dont-quit-on-action` keeps it waiting (and printing) until the notification closes.

Flags for hints that otherwise take `-h TYPE:NAME:VALUE`:

| Flag | Hint |
|---|---|
| `--replace-name NAME` | replaces the last notification sent with NAME (ids in `$XDG_RUNTIME_DIR/notifyd/send-ids.json`) |
| `--tag TAG` | `x-dunst-stack-tag` |
| `--image PATH` | `image-path`: a file (sent as a `file://` URI), a URI or an icon name |
| `--progress N` | `value` |
| `--title-end MARKUP` | `x-notifyd-title-end` (see Markup) |
| `--desktop-entry NAME` | `desktop-entry` |
| `--sound-name NAME`, `--sound-file PATH`, `--suppress-sound` | `sound-name`, `sound-file`, `suppress-sound` |
| `--resident` | `resident`: stays open after an action |
| `--action-icons` | `action-icons`: each action's key is an icon name, drawn as an icon button (its text is the tooltip) |
| `--fg`, `--bg`, `--frame`, `--highlight COLOR` | dunst's `fgcolor`, `bgcolor`, `frcolor`, `hlcolor` |

deadd's control messages work too: a notification with the hint `deadd-notification-center` (true or 1) and a `type` of `pausePopups`, `unpausePopups`, `clearPopups`, `clearInCenter` (forget the history) or `reloadStyle` (reload the config) is carried out, logged, and never shown or kept, so scripts written for deadd keep working:
`notifyd-send -h int:deadd-notification-center:1 -h string:type:clearPopups x`.

`-h variant:NAME:TEXT` sends any GVariant (`-h 'variant:x-list:[1, 2]'`).
`notifyd-send --completion zsh|bash|fish` prints its completion script.

`tools/demo.sh` (`just demo`) sends a tour of all of this with `notifyd-send` to a private daemon
running `examples/demo.yaml`, whose rules are commented with the step each one shows.

## Control

`notifyd ctl status | list [--json|--ids] | history [-n N] [--json] | pop [ID] | forget ID|--all | center [toggle|show|hide] | close [ID] [--app X ...] | action ID [KEY] |
close-all | pause [--for DUR] | resume | toggle [--for DUR] | mode [NAME] [--for DUR] | reload | preset NAME | preview [PRESET] | watch [--json]`.
`pause --for 1h` is do not disturb for an hour (it resumes on its own, also across a restart; a plain
`pause` or `resume` replaces it), and `mode focus --for 30m` switches back to the mode before it after
that. `close --app Slack --summary '/build/i'` closes every open notification (shown, waiting or held
back) whose fields all match, as rules match them (also `--body`, `--category`, `--urgency`,
`--stack-tag`, `--desktop-entry`); it prints how many.
`ctl bar [--follow]` prints a status-bar line (do not disturb, the mode, how many are held back, and
with `{missed}` in `--format` how many expired unclicked since the center was last opened, which
opening the center clears; see `notifyd help ctl bar` for a polybar module). `history --pick` picks one in fzf and shows it again. Each notification records its `sender` (the sending process's pid and name, its parent's name, and the `desktop-entry` hint): in `history --json`, `list --json` and `watch --json`, and as `via PROCESS (PARENT)` on `watch` lines. In the center: `j`/`k` or the arrows move between cards (and load more past the last), `g`/`G` first and last, `Enter` acts like a left click (shows a closed one again), `d`/`Delete` closes or forgets it, `C` clears, `e` shows the whole body of the selected card, `i` its details (app, icon, picture, sender, rules, times), `a` switches the history between grouped by app and one list, `p` toggles do not disturb, `m` the next mode, `space`/`n`/`N` play-pause, next and previous, `+`/`-` the volume, `?` or `Ctrl+/` shows a small window with all of these (any key closes it), `Esc`/`Ctrl+[`/`q` (or `center.toggle-key`) closes it. Center cards are built like the popups, each with its own style, preset, hidden parts and picture
sizes (history entries get today's rules again); the style key `center-selected` outlines the selected card. dunst's color hints (`fgcolor`, `bgcolor`, `frcolor`, `hlcolor`) color a single notification; `fgcolor` colors all its text, the preset's title, app name, time and urgency colors included (app name and time stay a little dimmer), unless a rule that matches sets `foreground`. `watch` streams the daemon's
event log, colored, or as JSON lines.
