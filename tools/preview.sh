#!/usr/bin/env bash
# The presets side by side: one sample card per preset, all on screen at once in columns, each column a
# private notifyd (tools/with-daemon.sh) placed next to the last, so the daemon you run is not touched.
# --each shows one preset at a time instead, with `notifyd ctl preview`'s five samples.
set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
notifyd=$root/bin/notifyd
send=$root/bin/notifyd-send

usage() {
  cat <<EOF
usage: tools/preview.sh [--each] [--urgency low|normal|critical] [--seconds N] [--renderer gtk|none]
                        [--log-dir DIR] [PRESET]...

Shows the presets (all of them, or the PRESETs named) on private daemons, so the daemon you run and
its history are not touched.

  (default)         one sample card per preset, all at once, side by side in columns; until Enter
  --each            one preset at a time, with the five samples of \`notifyd ctl preview\`; Enter for the
                    next (or --seconds N each)
  --urgency U       the urgency of the side-by-side cards (default normal), to compare low or critical
  --seconds N       show for N seconds instead of waiting for Enter (0: just send them, for tests)
  --renderer NAME   gtk (default) or none (log only, no display needed)
  --log-dir DIR     keep the daemons' event logs (JSON lines) in DIR
  --help            show this help and exit
EOF
}

each='' urgency=normal seconds='' renderer=gtk logdir='' column='' presets=()
while (($#)); do
  case $1 in
    --each) each=1 ;;
    --urgency) urgency=${2:-}; shift ;;
    --seconds) seconds=${2:-}; shift ;;
    --renderer) renderer=${2:-}; shift ;;
    --log-dir) logdir=${2:-}; shift ;;
    --column) column=${2:-}; shift ;;          # internal: send this column's cards inside its daemon
    -h|--help) usage; exit 0 ;;
    -*) echo "preview: unknown option $1 (--help lists them)" >&2; exit 2 ;;
    *) presets+=("$1") ;;
  esac
  shift
done
[[ $urgency =~ ^(low|normal|critical)$ ]] || { echo "preview: --urgency is low, normal or critical" >&2; exit 2; }
[[ -z $seconds || $seconds =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "preview: --seconds takes a number" >&2; exit 2; }
[[ $renderer == gtk || $renderer == none ]] || { echo "preview: --renderer is gtk or none" >&2; exit 2; }

# name -> description, from `notifyd presets`
declare -A description=()
while read -r name text; do description[$name]=$text; done < <("$notifyd" presets)
if ((${#presets[@]} == 0)); then
  mapfile -t presets < <("$notifyd" presets --names)
fi
for name in "${presets[@]}"; do
  [[ -n ${description[$name]+set} ]] || { echo "preview: no preset '$name' (notifyd presets lists them)" >&2; exit 2; }
done

# -- inside a column's daemon: its cards, then wait until told to stop ---------------------------------

if [[ -n $column ]]; then
  IFS=, read -r stop ready <<<"$column"
  python3 "$root/tools/cover.py" "$NOTIFYD_PRIVATE_DIR/cover.png" 64
  shown=0
  for ((i = ${#presets[@]} - 1; i >= 0; i--)); do       # the newest goes on top: last first
    name=${presets[i]}
    # actions keep notifyd-send waiting: in the background, ended with the daemon; one at a time, in order
    "$send" -a "$name" -u "$urgency" -i preferences-desktop-theme --image "$NOTIFYD_PRIVATE_DIR/cover.png" \
      --title-end '★ 4.5' --progress 60 -A open=Open -A later=Later "$name" \
      "${description[$name]}
<span class=\"accent\">accent</span> <span class=\"muted\">muted</span> <span class=\"good\">good</span> <span class=\"warn\">warn</span> <span class=\"bad\">bad</span> <span class=\"tag\">tag</span>" \
      >/dev/null 2>&1 &
    shown=$((shown + 1))
    for _ in $(seq 100); do
      [[ $("$notifyd" ctl list --ids 2>/dev/null | wc -l) -ge $shown ]] && break
      sleep 0.05
    done
  done
  : >"$ready"
  while [[ ! -e $stop ]]; do sleep 0.2; done
  exit 0
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/notifyd-preview.XXXXXX")
pids=()
cleanup() {
  : >"$work/stop"
  ((${#pids[@]})) && wait "${pids[@]}" 2>/dev/null
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
[[ -z $logdir ]] || mkdir -p "$logdir"

hold() {   # until Enter, or --seconds
  if [[ -n $seconds ]]; then
    [[ $seconds == 0 ]] || printf 'showing for %ss\n' "$seconds"
    sleep "$seconds"
  elif [[ -t 0 ]]; then
    read -rp "$1" _
  else
    sleep 10
  fi
}

# -- --each: one preset at a time ----------------------------------------------------------------------

if [[ -n $each ]]; then
  if [[ -z ${NOTIFYD_PRIVATE_DIR:-} ]]; then
    printf 'position: {anchor: top-center, margin: [40, 0]}\ntimeout: never\ntimeout-critical: never\n' \
      >"$work/each.yaml"
    printf 'history: {persist: false}\ncenter: {keyboard: false}\n' >>"$work/each.yaml"
    "$root/tools/with-daemon.sh" --renderer "$renderer" ${logdir:+--log "$logdir/each.json"} "$work/each.yaml" -- \
      "$0" --each --renderer "$renderer" ${seconds:+--seconds "$seconds"} "${presets[@]}"
    exit
  fi
  count=${#presets[@]} i=0
  for name in "${presets[@]}"; do
    i=$((i + 1))
    printf '%2d/%d  %-14s %s\n' "$i" "$count" "$name" "${description[$name]}"
    "$notifyd" ctl preview "$name" >/dev/null
    if ((i < count)); then hold '      [Enter] next '; else hold '      [Enter] done '; fi
    "$notifyd" ctl close-all >/dev/null
  done
  exit 0
fi

# -- side by side: one private daemon per column ------------------------------------------------------

# the work area of the monitor under the pointer (the daemons place their stacks there too)
read -r screen_w screen_h < <(
  if [[ $renderer == gtk ]]; then
    python3 - <<'EOF' 2>/dev/null
import gi
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk
display = Gdk.Display.get_default()
_, x, y = display.get_default_seat().get_pointer().get_position()
area = display.get_monitor_at_point(x, y).get_workarea()
print(area.width, area.height)
EOF
  fi
)
screen_w=${screen_w:-1920} screen_h=${screen_h:-1080}

count=${#presets[@]}
card_h=250 gap=14 top=24 side=16      # card_h: the tallest presets' sample card, about
per_column=$(((screen_h - top + gap) / (card_h + gap)))
((per_column < 1)) && per_column=1
columns=$(((count + per_column - 1) / per_column))
per_column=$(((count + columns - 1) / columns))         # spread evenly
width=$(((screen_w - 2 * side - (columns - 1) * gap) / columns))
((width > 360)) && width=360
x0=$(((screen_w - columns * width - (columns - 1) * gap) / 2))

printf 'notifyd presets: %d, in %d column(s) of %d (%s urgency)\n' "$count" "$columns" "$per_column" "$urgency"
for ((c = 0; c < columns; c++)); do
  chunk=("${presets[@]:c*per_column:per_column}")
  ((${#chunk[@]})) || continue
  config=$work/column-$c.yaml
  {
    printf 'preset: %s\nwidth: %d\nmax-visible: 0\nindicate-waiting: false\n' "${chunk[0]}" "$width"
    printf 'timeout: never\ntimeout-critical: never\nstack-duplicates: false\n'
    printf 'position: {anchor: top-left, margin: [%d, %d], gap: %d}\n' "$top" "$((x0 + c * (width + gap)))" "$gap"
    printf 'history: {persist: false}\ncenter: {keyboard: false}\nrules:\n'
    for name in "${chunk[@]}"; do
      printf '  - {name: preset-%s, match: {app: %s}, preset: %s}\n' "$name" "$name" "$name"
    done
  } >"$config"
  "$root/tools/with-daemon.sh" --renderer "$renderer" ${logdir:+--log "$logdir/column-$c.json"} "$config" -- \
    "$0" --column "$work/stop,$work/ready-$c" --urgency "$urgency" "${chunk[@]}" &
  pids+=($!)
  printf '  column %d: %s\n' "$((c + 1))" "${chunk[*]}"
done

for _ in $(seq 300); do
  ready=("$work"/ready-*)
  [[ -e ${ready[0]} && ${#ready[@]} -ge ${#pids[@]} ]] && break
  for pid in "${pids[@]}"; do
    kill -0 "$pid" 2>/dev/null || { echo "preview: a column's daemon did not start" >&2; exit 1; }
  done
  sleep 0.1
done
hold '[Enter] close them '
