# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
