# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
