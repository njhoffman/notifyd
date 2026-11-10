default:
    @just --list

# run the test suite (pytest; the daemon tests start a private D-Bus and Xvfb)
test *ARGS:
    python3 -m pytest -q {{ ARGS }}

# lint with ruff
lint:
    ruff check src tests bin/notifyd

# run the daemon from the checkout, log only (no windows): `just run --renderer gtk` to draw
run *ARGS:
    bin/notifyd daemon {{ ARGS }}

# preview every preset on the running daemon, a few seconds each
preview:
    #!/usr/bin/env bash
    for preset in $(bin/notifyd presets | awk '{ print $1 }'); do
      echo "$preset"; bin/notifyd ctl preview "$preset"; sleep 4
    done

# symlink bin/notifyd into ~/.local/bin (or $XDG_BIN_HOME); refuses to replace anything that is not a symlink
install:
    #!/usr/bin/env bash
    set -euo pipefail
    src="$(pwd -P)/bin/notifyd"
    dest="${XDG_BIN_HOME:-$HOME/.local/bin}/notifyd"
    mkdir -p "${dest%/*}"
    if [[ -e "$dest" && ! -L "$dest" ]]; then
      echo "install: $dest exists and is not a symlink; remove it first" >&2
      exit 1
    fi
    ln -sfn "$src" "$dest"
    echo "$dest -> $src"

# write ~/.config/notifyd/config.yaml from ~/.config/deadd (refuses to overwrite)
import-deadd:
    bin/notifyd import deadd -o config
