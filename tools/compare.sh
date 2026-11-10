#!/usr/bin/env bash
# Runs tools/compliance.py against each daemon (notifyd, dunst, deadd) on its own D-Bus session and
# Xvfb display, and prints a table. Daemons that are not installed are left out.
set -uo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
declare -A cmd=(
  [notifyd]="$root/bin/notifyd daemon --renderer gtk --log-format json"
  [dunst]="dunst -conf /dev/null"
  [deadd]="deadd-notification-center"
)
n=60
for daemon in notifyd dunst deadd; do
  bin=${cmd[$daemon]%% *}
  command -v "$bin" >/dev/null || { echo "skip $daemon (not installed)" >&2; continue; }
  n=$((n + 1))
  dbus-run-session -- bash -c "
    export DISPLAY=:$n GIO_USE_VFS=local XDG_STATE_HOME=$work/$daemon XDG_CONFIG_HOME=$work/$daemon-config
    Xvfb :$n -screen 0 1280x720x24 >/dev/null 2>&1 & x=\$!
    sleep 0.7
    ${cmd[$daemon]} >/dev/null 2>&1 & d=\$!
    for _ in \$(seq 50); do
      busctl --user status org.freedesktop.Notifications >/dev/null 2>&1 && break; sleep 0.1
    done
    python3 '$root/tools/compliance.py' --json > '$work/$daemon.json'
    kill \$d \$x 2>/dev/null; wait 2>/dev/null
  " 2>/dev/null
done
python3 - "$work" <<'PY'
import json, pathlib, sys
work = pathlib.Path(sys.argv[1])
results = {p.stem: json.loads(p.read_text() or '{}') for p in sorted(work.glob('*.json'))}
order = [d for d in ('notifyd', 'dunst', 'deadd') if d in results]
keys = []
for d in order:
    keys += [k for k in results[d] if k not in keys]
width = max(map(len, keys)) if keys else 10
print(f'{"":<{width}}  ' + '  '.join(f'{d:<12}' for d in order))
for key in keys:
    cells = []
    for d in order:
        value = results[d].get(key, '-')
        cells.append(f'{("yes" if value else "NO") if isinstance(value, bool) else str(value)[:12]:<12}')
    print(f'{key:<{width}}  ' + '  '.join(cells))
PY
