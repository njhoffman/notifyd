default:
    @just --list

# run the test suite (pytest; the daemon tests start a private D-Bus and Xvfb)
test *ARGS:
    python3 -m pytest -q {{ ARGS }}

# lint with ruff
lint:
    ruff check src tests tools bin/notifyd bin/notifyd-send

# run the daemon from the checkout, log only (no windows): `just run --renderer gtk` to draw
run *ARGS:
    bin/notifyd daemon {{ ARGS }}

# preview every preset on the running daemon, a few seconds each
preview:
    #!/usr/bin/env bash
    for preset in $(bin/notifyd presets | awk '{ print $1 }'); do
      echo "$preset"; bin/notifyd ctl preview "$preset"; sleep 4
    done

# symlink bin/notifyd, bin/notifyd-send and the man pages into ~/.local (never over a real file) and write the completions
install:
    #!/usr/bin/env bash
    set -euo pipefail
    link() {
      if [[ -e "$2" && ! -L "$2" ]]; then
        echo "install: $2 exists and is not a symlink; remove it first" >&2
        exit 1
      fi
      mkdir -p "${2%/*}"
      ln -sfn "$1" "$2"
      echo "$2 -> $1"
    }
    data="${XDG_DATA_HOME:-$HOME/.local/share}"
    link "$(pwd -P)/bin/notifyd" "${XDG_BIN_HOME:-$HOME/.local/bin}/notifyd"
    link "$(pwd -P)/bin/notifyd-send" "${XDG_BIN_HOME:-$HOME/.local/bin}/notifyd-send"
    link "$(pwd -P)/share/man/man1/notifyd.1" "$data/man/man1/notifyd.1"
    link "$(pwd -P)/share/man/man1/notifyd-send.1" "$data/man/man1/notifyd-send.1"
    # the user unit, linked (not enabled: `notify-daemon use notifyd`, or `systemctl --user enable`)
    link "$(pwd -P)/share/systemd/notifyd.service" "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/notifyd.service"
    if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
      systemctl --user daemon-reload
    fi
    just completions

# write the completion scripts where zsh, bash and fish look for them
completions:
    #!/usr/bin/env bash
    set -euo pipefail
    data="${XDG_DATA_HOME:-$HOME/.local/share}"
    write() { mkdir -p "${3%/*}"; $1 "$2" > "$3"; echo "$3"; }   # $1 split: the generator command
    fish="${XDG_CONFIG_HOME:-$HOME/.config}/fish"
    write 'bin/notifyd completion' zsh "$data/zsh/site-functions/_notifyd"
    write 'bin/notifyd completion' bash "$data/bash-completion/completions/notifyd"
    write 'bin/notifyd-send --completion' zsh "$data/zsh/site-functions/_notifyd-send"
    write 'bin/notifyd-send --completion' bash "$data/bash-completion/completions/notifyd-send"
    if [[ -d "$fish" ]]; then
      write 'bin/notifyd completion' fish "$fish/completions/notifyd.fish"
      write 'bin/notifyd-send --completion' fish "$fish/completions/notifyd-send.fish"
    fi

# protocol checks against notifyd, dunst and deadd side by side (private buses, Xvfb)
compliance:
    tools/compare.sh

# a tour of notifyd's fields, styles and rules on a private daemon (`just demo --help`)
demo *ARGS:
    tools/demo.sh {{ ARGS }}

# render the man page
man:
    man -l share/man/man1/notifyd.1

# write ~/.config/notifyd/config.yaml from ~/.config/deadd (refuses to overwrite)
import-deadd:
    bin/notifyd import deadd -o config
