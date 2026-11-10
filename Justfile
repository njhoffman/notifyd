default:
    @just --list

# run the test suite (pytest; the daemon tests start a private D-Bus and Xvfb)
test *ARGS:
    python3 -m pytest -q {{ ARGS }}

# lint with ruff
lint:
    ruff check src tests tools bin/notifyd

# run the daemon from the checkout, log only (no windows): `just run --renderer gtk` to draw
run *ARGS:
    bin/notifyd daemon {{ ARGS }}

# preview every preset on the running daemon, a few seconds each
preview:
    #!/usr/bin/env bash
    for preset in $(bin/notifyd presets | awk '{ print $1 }'); do
      echo "$preset"; bin/notifyd ctl preview "$preset"; sleep 4
    done

# symlink bin/notifyd and the man page into ~/.local (never over a real file) and write the completions
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
    link "$(pwd -P)/share/man/man1/notifyd.1" "$data/man/man1/notifyd.1"
    just completions

# write the completion scripts where zsh, bash and fish look for them
completions:
    #!/usr/bin/env bash
    set -euo pipefail
    data="${XDG_DATA_HOME:-$HOME/.local/share}"
    write() { mkdir -p "${2%/*}"; bin/notifyd completion "$1" > "$2"; echo "$2"; }
    write zsh "$data/zsh/site-functions/_notifyd"
    write bash "$data/bash-completion/completions/notifyd"
    if [[ -d "${XDG_CONFIG_HOME:-$HOME/.config}/fish" ]]; then
      write fish "${XDG_CONFIG_HOME:-$HOME/.config}/fish/completions/notifyd.fish"
    fi

# protocol checks against notifyd, dunst and deadd side by side (private buses, Xvfb)
compliance:
    tools/compare.sh

# render the man page
man:
    man -l share/man/man1/notifyd.1

# write ~/.config/notifyd/config.yaml from ~/.config/deadd (refuses to overwrite)
import-deadd:
    bin/notifyd import deadd -o config
