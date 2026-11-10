# notifyd

A desktop notification daemon (freedesktop `org.freedesktop.Notifications`) for X11 that
combines the best parts of deadd-notification-center and dunst:

- deadd's look as the default: a black translucent card, no frame, shadow from the compositor
- a short YAML config, with an importer for an existing deadd config
- rules that restyle, retime, re-icon, rewrite or hide notifications as they arrive
- preset styles you can switch between and preview live
- full Pango markup, plus named styles (`<span class="accent">`) that follow the preset
- `notifyd send`, a notify-send replacement with notify-send.py's extras
- a colorful, structured log of every notification and what the rules did to it

## Install

Needs Python 3.11+, PyGObject with GTK 3, and PyYAML (all from the distribution packages:
`python3-gi`, `gir1.2-gtk-3.0`, `python3-yaml`). A compositor (picom) gives translucency and shadows.

```sh
just install                 # links bin/notifyd and the man page into ~/.local, writes completions
just import-deadd            # writes ~/.config/notifyd/config.yaml from ~/.config/deadd
notifyd daemon --replace     # take over from the running daemon (Ctrl-C hands nothing back;
                             # restart deadd with `notify-daemon ensure`)
```

As a service: link `share/systemd/notifyd.service` into `~/.config/systemd/user/` and enable it
instead of deadd's.

## Use

```sh
notifyd send -a backdrop --image ~/pic.jpg 'New wallpaper' '<span class="muted">ocean, waves</span>'
notifyd send --tag volume --progress 60 -a Volume 'Volume 60%'
notifyd send -A 'change=Change layout' 'Monitors changed'     # prints "change" when clicked
notifyd ctl watch            # the live, colored event log
notifyd ctl history
notifyd presets --pick       # browse presets with a live preview
notifyd rules --app MPD --summary 'Song'   # which rules match, and the result
```

`notifyd help [COMMAND]` and `man notifyd` cover every command; `notifyd completion zsh|bash|fish`
prints a completion script. `docs/config.md` describes the config, rules, styles and markup;
`examples/config.yaml` is this desktop's deadd setup with longer song and wallpaper notifications.

## Development

`just test` (pytest; the daemon tests start their own D-Bus and Xvfb), `just lint` (ruff),
`just run` (the daemon from the checkout). `docs/research.md` surveys other daemons,
`docs/design.md` has the architecture, `TODO.md` the open work.
