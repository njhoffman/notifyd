#!/usr/bin/env bash
# Screenshots of every preset's preview under Xvfb into docs/presets/NAME.png (needs Xvfb and
# ImageMagick). Without a compositor the cards are drawn over black, so translucency is not shown.
set -uo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
n=50
for preset in $("$root/bin/notifyd" presets --names); do
  n=$((n + 1))
  dbus-run-session -- bash -c "
    export DISPLAY=:$n GIO_USE_VFS=local XDG_STATE_HOME=$work/state XDG_CONFIG_HOME=$work/config
    Xvfb :$n -screen 0 1280x720x24 >/dev/null 2>&1 & x=\$!
    sleep 0.7
    printf 'preset: $preset\nposition: {follow: none}\nfade: {out: 0}\n' > $work/$preset.yaml
    '$root/bin/notifyd' daemon -c $work/$preset.yaml >/dev/null 2>&1 & d=\$!
    for _ in \$(seq 50); do '$root/bin/notifyd' ctl status >/dev/null 2>&1 && break; sleep 0.1; done
    '$root/bin/notifyd' ctl preview $preset
    sleep 1.5
    import -window root $work/$preset.png
    kill \$d \$x; wait 2>/dev/null
  " >/dev/null 2>&1
  convert "$work/$preset.png" -crop 380x620+895+20 +repage -trim -bordercolor black -border 8 \
    -resize 80% "$root/docs/presets/$preset.png" && echo "docs/presets/$preset.png"
done
