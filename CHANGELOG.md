# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.51.8] - 2026-11-28

### Fixed

- The default-action key (`ctl action` without an id) goes by arrival, so a notification that waited
  for room does not outrank later ones, and skips an action with an empty key. A lone inline reply
  still counts: it opens the reply field.
- A copied one-time code is cleared even when a clipboard manager has taken the clipboard over (it is
  emptied, only while it still holds the code), without a nested main loop in the timer.
- After a reload, or a replace that moves a notification to another stack, a stack over `max-visible`
  sends its newest extras back to the front of the waiting queue; what shows next after a reload is
  measured with the new styles.
- Desktop-entry icons are looked up through `GioUnix.DesktopAppInfo` where GLib has it, so PyGObject
  3.56 no longer warns that `Gio.DesktopAppInfo` is deprecated.

## [0.51.7] - 2026-11-27

### Fixed

- A media player's metadata of the wrong type (a number for the title, a list with numbers in it for
  the artist) is left out instead of breaking the notification center while that player is active.
- Two cover downloads finishing together no longer lose one to the other's cache prune.
- The desktop-entry icon cache is capped (256), since senders can send any desktop-entry.
- `notifyd import deadd` reports what it cannot use instead of a traceback: `default-timeout: 5s`
  is read as a duration, a malformed modification is skipped, and bad YAML or a config that is not a
  mapping is an error with the file name. `notification-center` is reported once, and no longer as
  "not built yet".

## [0.51.6] - 2026-11-27

### Fixed

- `notifyd-send` reports numbers it cannot send as usage errors instead of tracebacks: `-r`/`--close`
  outside 0 to 4294967295, `-t` outside -1 to 2147483647, and `byte:`/`int:` hints out of range.
- A `sound-file` or `sound-name` hint is never taken as one of the player's options (`--server=...`);
  `file://` URIs are percent-decoded, so paths with spaces play.
- A `filter` command whose own child keeps its output open no longer holds the notification back
  forever: it shows unchanged at the timeout as intended.

## [0.51.5] - 2026-11-27

### Fixed

- `copy-code` copies the code, not the first number after the word: "Your OTP for txn of Rs 5000 is
  482913" (the form Indian bank texts use) copies 482913, and amounts with decimals no longer hide it.
  A code counts right after its word ("login code: 734 912", "PIN 5521"), after "is" later in the
  sentence, or before "is your ... code" / "to verify"; "Zip code 90210", "Error code 5003",
  "code review for PR 4821" and "pin the 2023 roadmap" get no button. "Your token is 482913" and
  "Enter 482913 to verify" work again (lost in 0.49.5).

## [0.51.4] - 2026-11-27

### Fixed

- `notifyd-send --replace-name` keeps its ids only in a directory that is this user's own (the /tmp
  fallback without `XDG_RUNTIME_DIR` included), writes them to a fresh temporary file and never
  through a link, so another local user cannot make it overwrite a file; concurrent calls no longer
  lose each other's ids, and an ids file that is not a name-to-id mapping is ignored instead of a
  traceback.
- The log file (`log.file`) is readable by the user only, as history and state are: it holds
  notification bodies, one-time codes too. An existing one is tightened when opened.

## [0.51.3] - 2026-11-27

### Fixed

- `history.size` is checked: a negative or non-numeric size, `size: null` (an unbounded history,
  rewritten in full every two notifications) or `history` that is not a mapping is a config error;
  they crashed the daemon at start, or left a reload half applied.
- A damaged history file no longer stops the daemon or breaks the center and `ctl pop`: bytes that are
  not UTF-8 are replaced, and lines that are not a usable entry (no id, an unknown urgency, text fields
  that are not text) are left out with a warning.
- A config file that is not UTF-8 is a config error (with the byte) instead of a crash, on start and on
  `ctl reload`.

## [0.51.2] - 2026-11-27

### Fixed

- An app that closes its notification (CloseNotification, or `ctl close ID`) while an update of it waits
  for its `filter` no longer sees that update pop up afterwards as a new notification (0.49.2).
- Stopping the daemon sends one NotificationClosed per held notification, also when an update of it was
  still in its filter (it sent two, and kept the update in the history).
- A timed pause or mode in the state file that ends more than 30 days ahead (0.49.0 could write one)
  ends at start with a warning instead of keeping the daemon from starting.

## [0.51.1] - 2026-11-27

### Fixed

- A sender can no longer restyle or hide other notifications through dunst's color hints
  (`bgcolor`, `fgcolor`, `frcolor`, `hlcolor`): a hint is used only when it is a color (#rgb,
  #rrggbb, rgb()/rgba(), a name; dunst's #rrggbbaa now works too), and no style value can end its CSS
  rule. A hint like `red; } * { opacity: 0; }` used to hide every later popup and the center.
- A style value GTK cannot read (a typo such as `radius: 5 px`) costs only the notifications using it
  their style, with an error in the log; it used to drop the style of every popup.

## [0.51.0] - 2026-11-27

### Added

- `notifyd ctl action` without an id invokes the default action of the newest notification on screen
  that has one, or exactly one action of its sender's (notifyd's own buttons and Copy do not count), so a
  key binding can answer the last popup, e.g. open the Claude Code session it names. It exits 1 when
  nothing on screen has one (D-Bus: `InvokeDefault`).

## [0.50.0] - 2026-11-27

### Added

- A copied one-time code leaves the clipboard and the primary selection after `copy-code-clear`
  (default 1m) if they still hold it, so a later paste or middle click does not bring it back;
  anything copied since stays. `never` keeps it (offered to a clipboard manager as before).

## [0.49.5] - 2026-11-27

### Fixed

- `copy-code` offers a Copy button only for real one-time codes: the code words count as whole words
  ("Barcode 123456", "pinned" no longer do), "security", "login" and "token" only as "... code", and
  digits before the word need "is your" or "as your" ("482913 is your verification code"). Years,
  battery sizes and phone numbers next to such words no longer get a button.

## [0.49.4] - 2026-11-27

### Fixed

- `ctl pause --for` and `ctl mode --for` take at most 30 days. Longer ones overflowed GLib's timer and
  left a pause end in the state with the daemon not paused.

## [0.49.3] - 2026-11-27

### Fixed

- `ctl reload` moves open, waiting and held notifications to a changed `position` (or a rule's): they
  used to stay in the old corner while new ones started another stack. A larger `max-visible` shows
  waiting ones at once.
- A replacement that a rule's `position` puts in another stack frees its place in the old one, so the
  oldest waiting notification there shows (it used to wait while the stack stood empty). The stack it
  moves to may hold one over `max-visible` until one closes.

## [0.49.2] - 2026-11-27

### Fixed

- An update that replaces an open notification while its `filter` runs is no longer lost when the
  open one closes first (it expired or was dismissed): it shows as a new notification once the filter
  answers.
- Notifications still waiting for their filter count for stack-tag, `group-by` and duplicate
  replacement, so a burst of filtered volume notifications replaces one popup instead of opening one
  each. `ctl close-all` and `ctl close --app ...` close them too.
- A notification brought back from the history (`ctl pop`) is not filtered a second time.

## [0.49.1] - 2026-11-27

### Fixed

- A timed pause (`ctl pause --for`) that ran out while notifyd was down no longer puts what it held
  back on screen before the daemon can draw it: those popups had no window, expired unseen, counted as
  missed and sent no NotificationClosed. The pause now ends once the daemon is up.

## [0.49.0] - 2026-11-27

### Added

- A notification whose text holds a one-time code ("Your verification code is 123456", "G-482913",
  "login code: 734 912") gets a `Copy 123456` button: it puts the code on the clipboard and the primary
  selection and dismisses the notification, without telling the sender. A code needs a word such as
  code, OTP, PIN, passcode or verification near it, so years, prices and order numbers do not count.
  `copy-code: false` in a rule turns it off.

## [0.48.0] - 2026-11-27

### Added

- Inline reply (`inline-reply`, as KDE Connect and chat apps send it): the popup shows a Reply button
  that opens the center on that card with a text field holding the keyboard; Enter or Send emits
  `NotificationReplied` and dismisses it. `r` in the center focuses the selected card's field, `Esc`
  leaves it, and a draft survives the center being rebuilt. The sender's
  `x-kde-reply-placeholder-text` and `x-kde-reply-submit-button-text` are used.
- `notifyd ctl reply ID TEXT` replies from a script; a rule's `run-on: [action]` hook gets the text as
  `NOTIFYD_REPLY`.

## [0.47.0] - 2026-11-27

### Added

- A rule can set `position` (`anchor`, `margin`, `gap`, `follow`, `monitor`): what it matches goes in
  a stack of its own, laid out from its own anchor on its own monitor, so a volume bar can sit at the
  bottom center while chat stacks at the top right. Each stack has its own `max-visible` room, waiting
  order and `+N`, so a full chat stack never holds the volume bar back.

### Changed

- An unknown `position.anchor` is a config error; it used to fall back to the right edge.

## [0.46.0] - 2026-11-27

### Added

- `notifyd ctl pause --for 1h` (and `toggle --for`): do not disturb that resumes on its own, also
  across a restart of the daemon. A plain `pause` or `resume` replaces it.
- `notifyd ctl mode NAME --for 30m` switches back to the mode before it after that long.
- `notifyd ctl close --app Slack --summary '/build/i'` closes every open notification whose fields
  match (also `--body`, `--category`, `--urgency`, `--stack-tag`, `--desktop-entry`), as rules match.
- `{missed}` in `notifyd ctl bar --format`: how many notifications expired without being clicked or
  closed since the center was last opened (kept across a restart). Opening the center clears it; a bar
  whose format leaves it out behaves as before.
- Durations take hours: `1h`.

## [0.45.0] - 2026-11-27

### Added

- A rule's `filter: CMD` rewrites a notification before it shows: the command gets the notification
  as `NOTIFYD_*` variables and JSON, and prints a JSON object with any of `summary`, `body`, `icon`,
  `urgency`, `progress`, `hints` and `skip`. The notification waits for it up to `filter-timeout`
  (default 500ms) while D-Bus replies and other notifications go on; a failure or a timeout shows it
  unchanged with a warning. A notification replaced or closed while its filter runs ignores the late
  answer.

## [0.44.0] - 2026-11-27

### Added

- `position.monitor` takes an output name as `xrandr` lists it (`DP-1`, any case), so the stack stays
  on that screen when a layout change renumbers the monitors. An unknown name warns once and uses the
  primary monitor.
- `position.follow: focus` puts the stack on the focused window's monitor (`position.monitor` when
  nothing has focus).
- `notifyd import dunst` maps `follow = keyboard` to `follow: focus` (it used to follow the mouse) and
  keeps dunst's `monitor`.

### Changed

- An unknown `position.follow` value is a config error; it used to act as `none`.

## [0.43.0] - 2026-11-27

### Added

- `run-on: [receive, action, close]` runs a rule's `run` command when one of its actions is invoked
  and when it closes, not only when it arrives (still the default). `NOTIFYD_EVENT` says which, with
  `NOTIFYD_ACTION` (the action's key) or `NOTIFYD_CLOSE_REASON` (expired, dismissed, closed,
  undefined); a notification replaced by a newer one runs no close hook.
- A click can run a command: `click.middle: 'exec: CMD'` runs CMD with the notification's `NOTIFYD_*`
  variables (`NOTIFYD_EVENT=click`, `NOTIFYD_BUTTON`) and dismisses it.

## [0.42.0] - 2026-11-27

### Added

- A rule can leave out some of a sender's actions: `actions: {drop: [settings, /^mute/i]}` matches an
  action's key or its label, as text or a regex, so Chromium's "Settings" button goes while the rest
  stay. A dropped action is not drawn and cannot be invoked; a rule's own `buttons` are kept.

## [0.41.0] - 2026-11-27

### Added

- Popups tell screen readers (Orca) what they are: the notification role (alert for a critical one),
  the summary as their name and the body as their description, without markup. A replaced
  notification updates them.

## [0.40.1] - 2026-11-27

### Fixed

- The daemon catches its signals (SIGINT, SIGTERM, SIGHUP, SIGUSR1) with `GLibUnix.signal_add` where
  GLib provides it, so PyGObject 3.56 no longer warns that `GLib.unix_signal_add`
  is deprecated; systems without GLibUnix keep the old call.

## [0.40.0] - 2026-11-27

### Added

- `style.body.line-spacing` (px) leaves a gap between the body's lines, wrapped ones too, so tags (span
  backgrounds) that wrap to a second line no longer touch. In a rule:
  `style: { body: { line-spacing: 2 } }`.

### Fixed

- The docs no longer say a class's `line-height` keeps wrapped tags apart: Pango draws a span's
  background over its whole line, so `line-height` only makes the tags taller.

## [0.39.0] - 2026-11-27

### Added

- A named class can set `line-height` (Pango's `line_height`, 1.50 and later): the lines its text is
  on get taller, so tags that wrap to a second line no longer sit on top of each other (a span's
  background has no padding). In a rule: `style: { classes: { tag: { line-height: 1.3 } } }`.

## [0.38.0] - 2026-11-27

### Added

- A warning for an icon or picture that does not load, once per name or path per run (a reload
  counts again): which part it was (app icon, the image-path hint, an icon.name or image.path setting,
  a .desktop file's icon) and why: no such file, not an image, a relative path, a remote URI, or an
  icon the theme lacks, with close names (`firefx`: close: firefox). They used to be left out silently.
- `notifyd check` flags `icon.name` and `image.path` settings, in the config and its rules, that would
  not draw.
- `notifyd icons [PATTERN]`: the icon theme's names with their context (`--context`, `--contexts`,
  `--theme`, `--names`, a glob); `notifyd icons --pick` browses them in fzf, drawing each in the
  preview (chafa, on the background the theme is drawn for) and in a popup as you move; enter prints
  the name, ctrl-y copies it. Works without a display (the theme then comes from GTK's settings.ini
  or gsettings).

## [0.37.1] - 2026-11-27

### Fixed

- An `action:KEY` link is drawn as the sender styled its text again: GtkLabel drew it in the theme's
  link color, underlined, over the sender's span, so the Claude Code input notification's project tag
  lost its white-on-blue look. notifyd now unwraps action links before GTK sees them and finds a click
  on their text itself (left button; the pointer turns into a hand over one), in popups and on open
  center cards. http, https and mailto links are unchanged.

## [0.37.0] - 2026-11-27

### Added

- Action icons: notifyd advertises the `action-icons` capability, and when a notification sets the
  hint each action's key is an icon name drawn as an icon button, its text the tooltip (a key the icon
  theme lacks stays a text button). `notifyd-send --action-icons` sets the hint.

## [0.36.0] - 2026-11-27

### Added

- Eleven presets modeled on themes people use with other daemons, each naming its source at the top
  of its file: `catppuccin-mocha`, `catppuccin-latte` (light), `dracula` (a left stripe colored by
  urgency, from dracula/swaync), `gruvbox` (wired's default), `nord`, `rose-pine` (background and title
  tinted by urgency), `kanagawa`, `one-dark`, `solarized-light` (text and frame colored by urgency),
  `swaync` (SwayNotificationCenter's default) and `xfce-retro` (xfce4-notifyd's Retro). There are 17
  in all; `docs/presets.md` shows each.
- `tools/with-daemon.sh`: runs a command against a private notifyd (its own session bus, a config, a
  temporary history), for the demo and the preview.

### Changed

- `just preview` shows every preset side by side, one sample card each, all on screen at once (one
  private daemon per column) until Enter; `--urgency critical` compares how each shows urgency, and
  preset names limit it. `--each` steps through them one at a time with `ctl preview`'s five samples.
  It used to cycle the presets on the daemon you run.

## [0.35.1] - 2026-11-27

### Fixed

- Two history entries with the same id closed within one millisecond (a sender reusing its
  `replaces_id`) no longer share one image-data thumbnail: the second overwrote the first's picture,
  and forgetting either entry deleted it for both. Thumbnail names now carry a sequence number.

## [0.35.0] - 2026-11-27

### Added

- deadd's control messages: a notification with the hint `deadd-notification-center` (true or 1) and a
  `type` of `pausePopups`, `unpausePopups`, `clearPopups`, `clearInCenter` or `reloadStyle` pauses,
  resumes, clears the popups, forgets the history or reloads the config, and is never shown or kept.
  Scripts written for deadd (`notify-daemon` clearing popups) keep working.

### Fixed

- dunst's `fgcolor` hint (`notifyd-send --fg`) colors all of a notification's text, as in dunst: the
  title, title end, body, count, app name and time (those two a little dimmer) and the urgency's text
  color, where presets that color those parts kept their own colors. A matching rule's `foreground`
  still wins over it.

## [0.34.0] - 2026-11-27

### Added

- `notifyd-send`, the sender as its own command (`notifyd send` stays): notify-send and notify-send.py
  in one, for any notification daemon. `just install` links it and its man page.
- notify-send.py's forms: `--replaces-id`, `--dontQuitOnAction`, several values after one `--hint`
  or `--action` (`--action yes:Yes no:No`), and `\n`, `\t` in the summary and body; notify-send's
  `-v/--version`.
- Flags for the hints notifyd and dunst understand: `--title-end`, `--desktop-entry`, `--sound-name`,
  `--sound-file`, `--suppress-sound`, `--resident`, and `--fg`, `--bg`, `--frame`, `--highlight`
  (dunst's color hints). `-h variant:NAME:TEXT` sends any GVariant.
- `notifyd-send --completion zsh|bash|fish`; `just completions` writes them next to notifyd's.
- `just demo` (`tools/demo.sh`): a tour of the fields, urgency, markup, pictures, progress, actions,
  replacing, stacking, styles, rules, presets and the center, sent with `notifyd-send` to a private
  daemon running `examples/demo.yaml` on your display; `--step`, `--section NAME`, `--live`.

### Changed

- With actions, `notifyd send` exits after the first action chosen, as notify-send and notify-send.py
  do; it used to wait until the notification closed, forever for a resident one.
  `--dont-quit-on-action` waits as before.
- `--image` takes an icon name too (a value with no slash that is not a file), instead of making a
  `file://` URI of it.

## [0.33.0] - 2026-11-27

### Added

- A link `<a href="action:KEY">` in a notification's title, title end or body invokes that notification's own action KEY when clicked (`default` too, which has no button), as its button would; the sender gets `ActionInvoked`. A card in the center that has already closed does nothing. Senders can make a tag or word the thing to click, as the Claude Code input notification in `~/.shell` does.

## [0.32.1] - 2026-11-27

### Fixed

- The `x-notifyd-title-end` hint is kept in the history (`title_end` in `history --json`), so center
  cards and `ctl pop` show it as the popup did.

## [0.32.0] - 2026-11-27

### Added

- The hint `x-notifyd-title-end` (Pango markup) is drawn at the right end of the title's line,
  never cut short, plainer and dimmer than the title; the new `title-end` style key restyles it.
  musicman puts a song's rating and Liked Songs heart there.
- Style parts (`title`, `body`, `appname`, `time`, `count`, `title-end`) take `opacity`.

## [0.31.0] - 2026-11-27

### Added

- `i` in the notification center shows the selected card's details in a small window: app,
  summary, body as sent, app icon, image or thumbnail, urgency, category, stack tag, the rules that
  matched, timeout, markup, mode, actions, sender (pid, process, parent, desktop entry), when it
  arrived and, for history, how and when it closed. Any key closes it.

## [0.30.0] - 2026-11-27

### Added

- The center's buttons have icons: Do not disturb, By app and Clear show a symbolic theme icon
  before their label, the player's previous / play-pause / next are icons, and all of them have
  tooltips naming their key. `center.buttons` take an optional `icon: NAME`.

## [0.29.1] - 2026-11-27

### Added

- `Ctrl+[` closes the notification center, as Escape does (as in a terminal).

## [0.29.0] - 2026-11-27

### Added

- A "By app" button in the center (and the key `a`) switches the history between grouped by app and
  one list; `center.group` is where it starts, and a reload goes back to it.

### Fixed

- Center cards are as compact as the popups: the close mark is as tall as the header text (a
  button made every card about 20 px taller), and the card list runs edge to edge, so the
  center's padding no longer adds to each card's own.

## [0.28.0] - 2026-11-27

### Changed

- Notification center cards are built by the same code as the popups: each has its own style,
  preset, hidden parts (`hide:`), picture sizes, icon and urgency colors, so a notification looks
  the same in the center as on screen. History entries get the rules applied again to draw them.
  The center keeps its close button and its three-line body until `e`.

### Removed

- The style key `center-card` (the background of center cards): they now take their own style.

## [0.27.0] - 2026-11-27

### Added

- Live style tuning: open popups take the new look as soon as the config is saved (their rules are
  applied again to what they were sent; their timers, text and actions stay and `run:` does not
  fire again), the center follows, and editing a preset in `~/.config/notifyd/presets/` reloads too.
- A config that does not load is shown as a notification that stays until it is fixed (the last
  good config stays in use), and the next good save says "config reloaded".

## [0.26.0] - 2026-11-27

### Added

- `?` or `Ctrl+/` in the notification center shows a small window listing every key and what it
  does (built from the same table the keys use); any key closes it.

## [0.25.2] - 2026-11-27

### Fixed

- j/k in the notification center no longer stop at the first of several history cards that share
  an id (a sender that reuses `replaces_id`, such as a wallpaper script sending 500 each time, has
  many). The center now tells history cards apart by id and closing time, so `d` forgets that one
  card instead of every entry with the id, a click brings back the card clicked, and each keeps its
  own thumbnail.

## [0.25.1] - 2026-11-27

### Fixed

- The timeout bar is drawn at exactly its `height` (default now 2 px) in its `color` (default now
  cyan, `#00d7d7`): it was a GtkProgressBar, which kept the theme's 6 px minimum whatever the
  style said. Set it with `style: {timeout-bar: {color, background, height}}`.
- docs/config.md lists every style key, including `timeout-bar`, `count`, `button.on`, `accent`,
  `markup-error`, `center-card` and `center-selected`, and the widget classes raw `css` can target.

## [0.25.0] - 2026-11-27

### Added

- `center.group: true` groups the center's history by app, newest app first: a header with the app
  name and count, the newest card under it until the header is clicked, and a × that forgets the
  whole group. Open notifications stay a flat list above.

## [0.24.0] - 2026-11-27

### Added

- `center.buttons: [{label, run, state}]`: a row of buttons under the center's volume slider. A
  click runs `run`; a button with `state` shows as on (style key `button.on`) while that command
  exits 0, checked without waiting when the center opens and shortly after each click.

## [0.23.0] - 2026-11-27

### Added

- `center.mpris-ignore: [firefox, chromium]`: the now-playing row passes over those players (by
  the name after `org.mpris.MediaPlayer2.`, any case), so a browser tab does not take it.

## [0.22.0] - 2026-11-27

### Added

- `indicate-waiting` (on by default): while notifications wait for room (`max-visible`, or no space
  left on screen), the last popup of the stack shows `+N` in its header.

## [0.21.0] - 2026-11-27

### Added

- `timeout-bar: true` (off by default; also in a rule): a thin bar along the bottom of a popup
  drains as its timer runs and stops while it is hovered. The style key
  `timeout-bar: {color, background, height}` sets its look.

## [0.20.0] - 2026-11-27

### Added

- Hovering a popup whose body `body.max-lines` cut shows all of it (the stack makes room); it
  shrinks back when the pointer leaves. In the center, `e` shows the whole body of the selected
  card, or cuts it back to three lines.

### Fixed

- `body.max-lines` (and the center's three lines) now cap bodies made of many short lines too:
  Pango counts its line limit per paragraph, so ten one-line paragraphs all showed.

## [0.19.1] - 2026-11-27

### Fixed

- Icons and images are sharp on HiDPI screens (`GDK_SCALE=2`, or a monitor with a scale factor):
  they are loaded at the scale factor and drawn at it, in popups, center cards and the
  now-playing row, instead of being drawn at their pixel size. At scale 1 nothing changes.

## [0.19.0] - 2026-11-27

### Added

- Symbolic icons (`*-symbolic`) are drawn in the card's text color, in the popup and on center
  cards, so a dark glyph no longer vanishes on a dark card; an icon name the theme lacks falls back
  to a shorter one (`audio-volume-high-foo` finds `audio-volume-high`).

## [0.18.3] - 2026-11-26

### Fixed

- The open notification center no longer blocks the daemon on media players and pactl: players
  are found and read with async D-Bus calls and followed through their signals (a hung player, or
  one stuck on a synchronous Notify, used to freeze popups for a second per player on every
  change), and the volume is read without waiting for pactl; the slider follows as the answer
  comes in.

## [0.18.2] - 2026-11-26

### Fixed

- The generated CSS no longer grows until a reload: styles no open window uses (dunst color hints
  give each notification its own) are pruned once a few have gathered, instead of each one adding
  to the provider every window is restyled from.

## [0.18.1] - 2026-11-26

### Fixed

- docs/config.md and the man page now list every key a rule cannot set (`position`, `max-visible`,
  `log`, `click`, `center`, `lock`, `rules`); the man page said a rule takes any of them.

## [0.18.0] - 2026-11-26

### Added

- `ignore-close: true` (usually in a rule) keeps a notification open when its app closes it
  (CloseNotification), until it expires or you close it; `ctl close` and clicks still close it.
  `notifyd import dunst` carries `ignore_dbusclose` over, globally and per rule.

## [0.17.0] - 2026-11-26

### Added

- The `sound` capability is advertised when a sound player is installed (sound-file, sound-name and
  suppress-sound are handled), so clients that check for it do not play their own sound as well.
- The spec 1.2 `ActivationToken` signal is sent before `ActionInvoked`, with an X11
  startup-notification id, so an app can raise its window when one of its actions is chosen.

## [0.16.1] - 2026-11-26

### Fixed

- HTML `<p>` and `<div>` from web apps no longer make the whole text show as typed: a closing one
  becomes a line break and an opening one goes. Other unknown tags still show as typed, flagged.

## [0.16.0] - 2026-11-26

### Added

- `lock.pause: true` holds every notification back while the screen is locked (critical ones too,
  since popups can stack above an X locker) and shows them on unlock, unless do not disturb is on.
  Locked means logind's `LockedHint` for the session, or one of `lock.processes` running
  (default i3lock, xsecurelock, slock, betterlockscreen), checked every 2 s. It is separate from
  do not disturb: unlocking never turns that off. `ctl status` shows `locked`.

## [0.15.0] - 2026-11-26

### Added

- `fullscreen: delay` (in the config or a rule): while the focused window covers the stack's
  monitor (a video, a game), non-critical notifications wait and show, in order, when it ends;
  `ctl list` shows them as `fullscreen`. The default stays `show`. Rules can also
  `match: {fullscreen: true}`. `notifyd import dunst` carries `fullscreen` over (`pushback` becomes
  `show`, since an override-redirect popup cannot go under a fullscreen window).

### Fixed

- `notifyd import` wrote an invalid config when every top-level value was a plain value (a flow
  mapping followed by `rules:`).

## [0.14.0] - 2026-11-26

### Added

- A notification with no `app_icon` (Electron and browser apps send none) shows the icon of its
  `desktop-entry` hint's `.desktop` file, in the popup and on its center card; the entry name is
  also tried in lowercase.

## [0.13.6] - 2026-11-26

### Fixed

- Remote cover art keeps to its 3 s limit for the whole download: a server that sends a byte now
  and then could hold the download thread for minutes, since the timeout applied to each read. (A
  server that stalls while sending its headers is still bounded per read only.)
- The art cache keeps the 200 most recently used covers instead of the most recently downloaded:
  a cache hit counts as use.

## [0.13.5] - 2026-11-26

### Fixed

- A rule's `fade.out` is used when its notification closes (only the global one was), and a close
  during the fade-in fades out from where it is instead of two fades fighting over the window; the
  fade-in no longer touches a window that closed without a fade-out.

## [0.13.4] - 2026-11-26

### Fixed

- `notifyd send -w` (and `send` with actions) no longer waits forever when the daemon dies or is
  replaced before the notification closes: it prints "the notification daemon went away" and exits
  1. It also only listens to the daemon that owns the bus name, not to look-alike signals from
  other clients.

## [0.13.3] - 2026-11-26

### Fixed

- `ctl action ID KEY` with a key the notification never offered (a typo) no longer signals the app
  and closes the notification: it does nothing and exits 1.

## [0.13.2] - 2026-11-26

### Fixed

- `ctl preview` no longer runs the `run:` commands, buttons and sounds of rules a sample happens to
  match (the music sample matched song rules), and its samples no longer land in the history.

## [0.13.1] - 2026-11-26

### Fixed

- Leading and trailing newlines and spaces in a summary or body no longer draw blank lines; blank
  lines inside the text stay.

## [0.13.0] - 2026-11-26

### Added

- History entries keep the app icon, the image path, the markup mode and the sender, and an image
  sent as image-data is kept as a 64 px thumbnail in `$XDG_CACHE_HOME/notifyd/thumbs/` (0600,
  removed with its entry). Center cards show the picture and render in the markup mode the popup
  used.

### Changed

- `ctl pop` (and Enter or a click in the center) brings a notification back with its icon, image
  and sender, so sender rules match it again; rules still style it, but their `run:` commands and
  sounds do not fire a second time.

## [0.12.9] - 2026-11-26

### Fixed

- Popups no longer run off the screen: a notification whose window would not fit in the monitor's
  work area below (or above) the ones on screen waits, like one past `max-visible`, and shows when
  room frees up, oldest first. The first one on screen always shows. A window that grows on an
  update can still reach past the edge.

### Added

- `title.max-lines` (default `0`, no limit): longer summaries end in …, like `body.max-lines`.

## [0.12.8] - 2026-11-26

### Fixed

- A notification sent with the `replaces_id` of one that is already gone keeps that id, as the
  spec says and dunst does, instead of getting a new one; scripts that send with a fixed id
  (`notify-send -r 100`) keep track of it. An id that was never issued still gets a new one, so it
  cannot collide with a later notification.

## [0.12.7] - 2026-11-26

### Fixed

- `history.jsonl` and `state.json` (which hold whole notification bodies, one-time codes among
  them) are created readable by you only (0600, their directory 0700) instead of by everyone under
  the default umask. Files made by an older version are tightened on their next write.

## [0.12.6] - 2026-11-26

### Fixed

- Closing a card in the notification center (its close button, or `d`) tells the sender the user
  dismissed it (reason 2) instead of reason 3, which the spec keeps for a sender closing its own.

## [0.12.5] - 2026-11-26

### Fixed

- Notifications held back by do-not-disturb are no longer lost when the daemon stops or restarts:
  the paused state and the held-back queue are kept in `state.json` (written as they change), and
  the next start is paused again with them held back, to show on resume. They get new ids (their
  old ones end with the daemon, so their senders hear reason 4, undefined); rules apply again, but
  `run:` commands do not run a second time; image-data hints are not kept.

## [0.12.4] - 2026-11-26

### Fixed

- A notification the renderer cannot draw no longer holds a `max-visible` slot forever with no
  timer: the error is logged, and the notification is closed (reason 4, undefined) into history
  so its sender hears about it. A failed update keeps the old window; a failed close still frees
  the slot.
- Numeric settings (`width`, `image.size`, `image.margin-right`, `icon.size`, `body.max-lines`,
  `position.monitor`, `position.gap`) are checked when the config loads, in rules too, instead of
  failing when a notification is drawn. An unreadable preset file is a config error, so the
  default preset is used instead of no window at all.

## [0.12.3] - 2026-11-26

### Fixed

- HTML entities Pango does not know (`&nbsp;`, `&hellip;`, `&mdash;`, `&copy;`, which browsers and
  chat apps send) no longer turn the whole text into escaped markup: they become their
  characters. An unknown one (`&foo;`) is shown as typed and the rest of the markup still renders.

## [0.12.2] - 2026-11-26

### Fixed

- A malformed `image-data` (or `icon_data`) hint can no longer crash the daemon. GdkPixbuf only
  checked the buffer against width x height x channels, so a sender with a large rowstride made it
  read past the buffer. The hint is now checked first (8 bits, 3 or 4 channels to match the alpha
  flag, at most 4096 px a side, a rowstride of at least one row, enough bytes for every row); a bad
  one is not drawn, with a `warn` log line, and the notification still shows.

## [0.12.1] - 2026-11-26

### Fixed

- `<a href>` links render as links instead of turning the whole text into escaped markup with a
  `markup error` mark: Pango does not know `<a>`, so the check now reads links as spans, while the
  label gets them as they are. A click opens http, https and mailto links with the default handler,
  refuses anything else (`file:` included) with a `warn` log line, and does not dismiss the
  notification. A bare `&` in an `href` is escaped.

## [0.12.0] - 2026-11-26

### Added

- Text that is not valid Pango markup is still shown as typed, with nothing stripped, but it is now
  flagged: a `markup error` mark in the popup's and the center card's header (its tooltip has
  Pango's reason; the style key `markup-error` sets its color), and a `warn` line in the event log.
- `center.toggle-key` (default `<Super>grave`): the key that hides the open center. While the center
  is open it holds the keyboard, so the window manager's own toggle binding for `notifyd ctl center`
  never fired and the center could not be closed with it. `''` turns it off.

### Fixed

- A bare `<` that cannot start a tag (`a < b`, `<3`) is escaped like a bare `&`, so the rest of
  the markup still renders instead of the whole text being shown as typed.

## [0.11.1] - 2026-11-25

### Fixed

- `notifyd.service` no longer names `org.freedesktop.Notifications` as its `BusName=` (it is
  `Type=simple` now). systemd lets only one loaded unit name a bus name, so with dunst's unit
  installed too, a daemon-reload could leave notifyd's unit unloadable ("Two services allocated
  for the same bus name"): it could not restart or be started by D-Bus activation. notifyd still
  takes the name itself, and D-Bus activation still starts the unit.

## [0.11.0] - 2026-11-22

### Added

- Rules match on who sent a notification: `match: {process: NAME}` and `match: {parent: NAME}`
  (exact, `/regex/` or a list, like the other fields), e.g. `{app: Spotify, process: spotify}`
  with `skip: true` drops the Spotify client's own song notifications and keeps musicman's.

## [0.10.0] - 2026-11-22

### Added

- Each notification records who sent it: the sending process's pid and name, its parent's name,
  and the `desktop-entry` hint, as `sender` in `ctl history --json`, `list --json` and
  `watch --json`, and as `via notify-send (musicman)` on `ctl watch` lines. Two senders using one
  app name (the Spotify client and musicman) can now be told apart.

## [0.9.0] - 2026-11-22

### Added

- The center's now-playing row shows cover art from http(s) URLs, such as Spotify's: downloaded
  in the background (3 s timeout, 2 MB at most), cached in `~/.cache/notifyd/art/` (the newest
  200), filled in without redrawing the center. `center.remote-art: false` keeps it local only.
  Notification images still never come from the network.

## [0.8.1] - 2026-11-22

### Changed

- `just install` also links the systemd user unit into `~/.config/systemd/user/` (it does not
  enable it).
- README: installing as a service is `notify-daemon use notifyd` (or enabling the unit by hand),
  and where its log is.

### Fixed

- The deadd and dunst presets' descriptions were cut at their first color (`#acf`, `#000811`),
  which YAML read as a comment; `notifyd presets` and `docs/presets.md` show them whole.

## [0.8.0] - 2026-11-22

### Added

- The notification center works from the keyboard: while open it takes the keyboard, `j`/`k` or
  the arrows move between cards (scrolling them into view, loading more past the last), `g`/`G` go
  to the first and last, `Enter` acts like a left click, `d`/`Delete` closes or forgets one, `C`
  clears, `p` toggles do not disturb, `m` picks the next mode, `space`/`n`/`N` control the player,
  `+`/`-` the volume, `Esc`/`q` close it. `center.keyboard: false` leaves the keyboard alone.
- Style key `center-selected`: the outline of the selected card.

### Fixed

- The center no longer waits up to a second to follow a change right after scrolling, while the
  scrollbar faded out.

## [0.7.1] - 2026-11-22

### Changed

- The notification center opens faster: the panel is kept between openings and rebuilt only after
  something changed (a notification, the mode, a player, the preset), and the history shows the
  newest `center.cards` (20) entries with a button for more. With 60 in the history, opening went
  from about 450 ms to 75 ms, or about 280 ms after a change.

### Fixed

- The now-playing title, artist and player lines stay on one line, cut with an ellipsis (full text
  in the tooltip), instead of wrapping mid-word in the narrow column.

## [0.7.0] - 2026-11-22

### Added

- The notification center has a volume row for the default output: a slider and a mute button,
  through `pactl` (PulseAudio or PipeWire); left out without it. `center.volume: false` hides it.
- The center's mode is a picker: `normal` and every mode a rule matches on by name
  (`match: {mode: NAME}`), plus the current one. `center.modes: false` shows it as text again.

### Fixed

- Opening a menu in the center (the mode picker) no longer hides it under `hide-on-leave`.
- A notification arriving while the volume slider is held waits to redraw the center until it
  is let go, instead of pulling the slider from under the pointer.
- The man page names the current version (it said 0.6.0 in 0.6.1); a test keeps them together.

## [0.6.1] - 2026-11-22

### Changed

- `examples/config.yaml`: images default to 60 px; the songs and wallpaper rules no longer set
  their own size (wallpaper thumbnails were 110 px).

## [0.6.0] - 2026-11-10

### Added

- `group-by` (e.g. in a rule for a chat app): a new notification with the same app (or summary,
  category, urgency) replaces the open one and is counted, mako-style.
- `notifyd ctl bar [--follow]`: a status line for polybar showing do not disturb, the mode and
  how many notifications are held back, updated live (`notifyd help ctl bar` has a module).
- The notification center shows what an MPRIS media player plays (MPD through mpDris, Spotify,
  browsers), with previous, play/pause and next (`center.mpris`).
- `docs/presets.md`: screenshots of every preset (`tools/gallery.sh` makes them).
- README: how to install it as a package next to the system's PyGObject.

### Fixed

- Critical notifications in the center use the preset's critical text color.

## [0.5.0] - 2026-11-10

### Added

- The notification center, as in deadd: a full-height panel with a do-not-disturb toggle, the
  mode, a clear button, the open notifications and the history. Click a closed one to show it
  again, × to forget it. `notifyd ctl center [toggle|show|hide]`, and SIGUSR1 toggles it, so the
  i3 binding that toggled deadd's center keeps working.
- `notifyd ctl forget ID|--all` and `notifyd ctl history --pick` (fzf; enter shows it again).
- Notifications fade out when they close (`fade.out`, 150 ms): picom fades windows in but not
  translucent ones out. `fade.in` is there too, off by default.
- `tools/compliance.py` and `just compliance`: protocol checks run against notifyd, dunst and
  deadd side by side.

### Fixed

- Notification ids go on from the kept history after a restart, so `ctl pop` and `ctl forget`
  never confuse an old notification with a new one.

## [0.4.0] - 2026-11-10

### Added

- `buttons:` in a rule adds buttons that run commands (e.g. `playerctl next` on song
  notifications), and `run:` runs a command for every matching notification, shown or skipped,
  with the notification in `NOTIFYD_*` variables and as JSON on stdin.
- Sounds: the `sound-file`, `sound-name` and `suppress-sound` hints are played (paplay,
  canberra-gtk-play), and a rule's `sound:` sets or silences them.
- `idle-threshold`: notifications wait while you are away from the keyboard and mouse.
- `notifyd ctl pop [ID]` shows a closed notification again.
- `notifyd import dunst [--rc PATH]` converts a dunstrc, its rule sections included.

### Changed

- The mode set with `notifyd ctl mode` is kept across restarts.

## [0.3.0] - 2026-11-10

### Added

- Modes: `notifyd ctl mode dnd` (or any name), matched by rules with `match: {mode: dnd}`, e.g. to
  suppress all but critical notifications; `ctl status` shows the mode.
- Identical notifications (same app, summary, body and urgency) stack into one with a `×N` count
  instead of piling up (`stack-duplicates: false` turns it off).
- History survives restarts: `$XDG_STATE_HOME/notifyd/history.jsonl` (`history.persist: false`
  keeps it in memory only).
- `log.file`: the event log is also appended to a file, without colors.
- `notifyd ctl list [--json|--ids]` shows open notifications; `ctl close` and `ctl action` complete
  their ids in zsh, bash and fish.
- dunst's `fgcolor`, `bgcolor`, `frcolor` and `hlcolor` hints color a notification (rules can
  still override them).

### Fixed

- Notifications are placed again when the monitor layout changes (displayd, xrandr), and the stack
  stays on one monitor while it is shown instead of following the pointer with each new one.

## [0.2.0] - 2026-11-10

### Added

- `-h`/`--help` on every command, with descriptions and examples; `notifyd help [COMMAND...]`.
  `notifyd send` keeps `-h` for `--hint`, as notify-send does, so its help is `--help`.
- `notifyd version` and `-V`, besides `--version`.
- A man page, `notifyd(1)` (`share/man/man1/notifyd.1`; `just man` shows it).
- `notifyd completion zsh|bash|fish`: completion scripts for commands, options, choices, files
  and live preset names, generated from the command definitions so they stay current.
  `just install` now also links the man page and writes the completions.
- `notifyd presets --names`.

## [0.1.0] - 2026-11-10

### Added

- `notifyd daemon`: a freedesktop notification server (spec 1.2) for X11. Replacing by id keeps
  the id, `x-dunst-stack-tag` and `x-canonical-private-synchronous` replace by tag, `value` draws a
  progress bar, actions become buttons, and hovering a notification stops its clock.
- deadd's look as the default preset (black card at 0.8, no frame, `#acf` text, shadow left to
  the compositor), plus dunst, minimal, card, tokyonight and high-contrast presets. Presets are
  YAML files; your own go in `~/.config/notifyd/presets/`.
- A YAML config in `~/.config/notifyd/config.yaml`, reloaded when it changes. Rules match on app,
  summary, body, category, urgency, hints and more (exact, list or `/regex/`), and can change the
  timeout (over what the sender asked for), style, preset, image, icon, hidden parts and text, or
  suppress a notification. `notifyd rules` shows what a notification would get.
- Full Pango markup in notifications, with named styles from the preset
  (`<span class="accent">`), and plain-text fallbacks for broken markup.
- A colored, aligned event log of everything the daemon does, or JSON lines; `notifyd ctl watch`
  streams it from a running daemon.
- `notifyd send`: notify-send's flags plus `--replace-name`, `--tag`, `--image`, `--progress` and
  `--close`; with actions it prints the one chosen.
- `notifyd ctl`: status, history, close, action, close-all, pause/resume/toggle, reload, preset,
  preview and watch.
- `notifyd presets [--pick]`: list presets, or pick one in fzf with a live preview.
- `notifyd import deadd`: converts deadd.yml and deadd.css, reporting what has no equivalent.
- `examples/config.yaml` (this desktop's deadd settings with 12 s song and wallpaper
  notifications), `docs/config.md`, a systemd user unit and a Justfile.
- Research notes on notification daemons and on how musicman, backdrop and displayd notify
  (`docs/research.md`), and the design (`docs/design.md`).
