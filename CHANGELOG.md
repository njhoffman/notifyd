# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
