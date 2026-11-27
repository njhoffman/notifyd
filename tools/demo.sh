#!/usr/bin/env bash
# A tour of notifyd, sent with notifyd-send: the fields of a notification, urgency, markup, pictures,
# progress, actions, replacing, stacking, styles, rules, presets and the center. It runs a private
# daemon (its own session bus, examples/demo.yaml, a temporary history) on your display, so the daemon
# you use and its history are not touched. `tools/demo.sh --help` for the options.
set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
SECTIONS=(fields urgency markup pictures progress actions replacing stacking styles rules presets center)
NEEDS_RULES=(stacking styles rules)       # use examples/demo.yaml's rules: skipped with --live

usage() {
  cat <<EOF
usage: tools/demo.sh [--step] [--fast] [--section NAME]... [--live] [--renderer gtk|none] [--log FILE] [--list]

A tour of notifyd's fields, styles and rules, sent with notifyd-send. A private daemon (its own
session bus, examples/demo.yaml, a temporary history) shows it on your display; the daemon you
run and its history are not touched.

  --step            wait for Enter after each section
  --fast            no pauses (for tests; nothing stays up long enough to read)
  --section NAME    run only this section (repeatable, or comma-separated); --list names them
  --live            send to the daemon you run instead (skips: ${NEEDS_RULES[*]})
  --renderer NAME   the private daemon's renderer: gtk (default) or none (log only, no display needed)
  --log FILE        keep the private daemon's event log (JSON lines) in FILE
  --list            list the sections and exit
  --help            show this help and exit

sections: ${SECTIONS[*]}
EOF
}

step='' fast='' live='' renderer=gtk log='' only=()
while (($#)); do
  case $1 in
    --step) step=1 ;;
    --fast) fast=1 ;;
    --live) live=1 ;;
    --section) [[ -n ${2:-} ]] || { echo "demo: --section needs a name" >&2; exit 2; }
               IFS=, read -ra names <<<"$2"; only+=("${names[@]}"); shift ;;
    --section=*) IFS=, read -ra names <<<"${1#*=}"; only+=("${names[@]}") ;;
    --renderer) renderer=${2:-}; shift ;;
    --renderer=*) renderer=${1#*=} ;;
    --log) log=${2:-}; shift ;;
    --log=*) log=${1#*=} ;;
    --list) printf '%s\n' "${SECTIONS[@]}"; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "demo: unknown option $1 (--help lists them)" >&2; exit 2 ;;
  esac
  shift
done
for name in "${only[@]}"; do
  [[ " ${SECTIONS[*]} " == *" $name "* ]] || { echo "demo: no section '$name' (--list names them)" >&2; exit 2; }
done
[[ $renderer == gtk || $renderer == none ]] || { echo "demo: --renderer is gtk or none" >&2; exit 2; }

send=$root/bin/notifyd-send
notifyd=$root/bin/notifyd
export NOTIFYD_DEMO_SEND=$send

# a private daemon (tools/with-daemon.sh): run again inside it (the options are already checked)
if [[ -z $live && -z ${NOTIFYD_PRIVATE_DIR:-} ]]; then
  exec "$root/tools/with-daemon.sh" --renderer "$renderer" ${log:+--log "$log"} "$root/examples/demo.yaml" -- \
    "$0" ${step:+--step} ${fast:+--fast} --renderer "$renderer" "${only[@]/#/--section=}"
fi
if [[ -n $live ]]; then
  work=$(mktemp -d "${TMPDIR:-/tmp}/notifyd-demo.XXXXXX")
  trap 'rm -rf "$work"' EXIT
else
  work=$NOTIFYD_PRIVATE_DIR                  # with-daemon.sh removes it
fi
trap 'exit 130' INT TERM

if [[ -t 1 && -z ${NO_COLOR:-} ]]; then
  bold=$'\e[1m' dim=$'\e[2m' blue=$'\e[38;5;111m' green=$'\e[38;5;114m' reset=$'\e[0m'
else
  bold='' dim='' blue='' green='' reset=''
fi
title() { printf '\n%s== %s ==%s\n' "$bold$blue" "$*" "$reset"; }
say() { printf '  %s\n' "$*"; }
shell() { printf '  %s$ %s%s\n' "$dim" "$*" "$reset"; }     # show what a step runs
pause() { [[ -n $fast ]] || sleep "$1"; }
clear_all() { [[ -n $live ]] || "$notifyd" ctl close-all >/dev/null 2>&1; }

# a 96x96 gradient as "cover art" for the pictures section
cover=$work/cover.png
python3 "$root/tools/cover.py" "$cover"

# -- sections ----------------------------------------------------------------------------------------

fields() {
  title 'Fields: summary, body, app, icon, category'
  shell "notifyd-send 'Just a summary'"
  "$send" 'Just a summary'
  pause 1.5
  "$send" -a demo 'A summary and a body' 'The body wraps over lines and takes <b>Pango</b> markup.\nA \\n in the text is a line break.'
  pause 1.5
  shell "notifyd-send -a Mail -i mail-unread -c email.arrived 'New mail' ..."
  "$send" -a Mail -i mail-unread -c email.arrived 'New mail' 'The app name, icon and category: rules can match on each.'
  pause 1.5
  "$send" -a demo -e 'Transient' 'Sent with -e: kept out of the history.'
  pause 3
}

urgency() {
  title 'Urgency: low, normal, critical'
  say 'presets color urgency; critical ones stay until closed (demo.yaml: 10s)'
  "$send" -a demo -u low 'Low urgency' 'Updates are available.'
  pause 1
  "$send" -a demo -u normal 'Normal urgency' 'The build finished.'
  pause 1
  "$send" -a demo -u critical 'Critical urgency' 'The disk is almost full.'
  pause 4
}

markup() {
  title 'Markup: Pango, named styles, links, HTML'
  "$send" -a demo 'Pango markup' '<b>bold</b> <i>italic</i> <u>underline</u> <s>struck</s> <tt>mono</tt>
x<sup>2</sup> H<sub>2</sub>O <small>small</small> <big>big</big> <span foreground="#ff9e64">any color</span>'
  pause 1.5
  say 'every bundled preset defines the classes accent, muted, good, warn, bad and tag'
  "$send" -a demo 'Named styles' '<span class="accent">accent</span> <span class="muted">muted</span> <span class="good">good</span> <span class="warn">warn</span> <span class="bad">bad</span> <span class="tag">tag</span>'
  pause 1.5
  "$send" -a demo 'Links and HTML' 'A <a href="https://github.com/njhoffman/notifyd">link</a> opens in the browser.<br>Entities Pango lacks work: &hellip; &copy; &nbsp;&rarr;'
  pause 1.5
  say 'markup that does not parse is shown as typed, with a "markup error" mark'
  "$send" -a demo 'Markup error' 'An <b>unclosed tag stays visible'
  pause 4
}

pictures() {
  title 'Pictures and the title end'
  shell "notifyd-send --image cover.png --title-end '★★★★☆' -a demo-music 'Night Drive' ..."
  "$send" -a demo-music --image "$cover" --title-end '<span foreground="#ff8fc7">♥</span> ★★★★☆' \
    'Night Drive' '<b>The Midnight</b> · Endless Summer\n<span class="tag">synthwave</span> <span class="muted">4:12</span>'
  pause 2
  "$send" -a demo --image dialog-information 'An icon name as the picture' '--image takes a file, a URI or an icon name.'
  pause 2
  "$send" -a demo --desktop-entry org.gnome.Nautilus 'The app icon from a .desktop file' '--desktop-entry, with no -i'
  pause 4
}

progress() {
  title 'Progress'
  shell "notifyd-send -a demo-download --progress N 'Downloading' 'N%'   # a rule gives it a stack tag"
  for p in 0 15 30 45 60 75 90 100; do
    "$send" -a demo-download --progress "$p" 'Downloading' "notifyd.tar.gz · $p%"
    pause 0.4
  done
  pause 1
  "$send" -a demo --progress 65 --highlight '#9ece6a' 'Battery charging' '65% · --highlight colors the bar'
  pause 4
}

actions() {
  title 'Actions'
  say 'click a button (or the link in the body): notifyd-send prints the key; in 5s the demo picks one'
  shell "notifyd-send -A 'yes=Sounds good' -A 'no=Not now' 'Lunch at noon?' ..."
  : >"$work/action.out"
  "$send" -p -a demo-actions -A 'yes=Sounds good' -A 'no=Not now' 'Lunch at noon?' \
    'Pick a button, or <a href="action:yes">answer yes from the body</a>.' >"$work/action.out" &
  local sender=$! id=''
  for _ in $(seq 100); do id=$(head -n1 "$work/action.out"); [[ -n $id ]] && break; sleep 0.05; done
  for _ in $(seq 50); do kill -0 "$sender" 2>/dev/null || break; [[ -n $fast ]] || sleep 0.1; done
  kill -0 "$sender" 2>/dev/null && "$notifyd" ctl action "$id" yes >/dev/null 2>&1
  wait "$sender"
  local chosen
  chosen=$(sed -n 2p "$work/action.out")
  say "${green}notifyd-send printed: ${chosen:-nothing (it closed without an action)}$reset"
  pause 2
}

replacing() {
  title 'Replacing'
  local id
  id=$("$send" -p -a demo 'Replace by id' 'The first text (-p printed its id).')
  pause 1.5
  shell "notifyd-send -r $id 'Replace by id' ..."
  "$send" -r "$id" -a demo 'Replace by id' 'The same popup, new text (-r ID).'
  pause 1.5
  shell "notifyd-send --replace-name demo-backup 'Backup' ...   # notify-send.py's --replaces-process"
  "$send" --replace-name demo-backup -a demo 'Backup' 'running…'
  pause 1.5
  "$send" --replace-name demo-backup -a demo 'Backup' '<span class="good">done</span> · 1.2 GB'
  pause 1.5
  shell "notifyd-send --tag volume -a demo-volume --progress N 'Volume N%'"
  for v in 30 50 70; do
    "$send" --tag volume -a demo-volume --progress "$v" -i audio-volume-medium "Volume $v%"
    pause 0.6
  done
  pause 3
}

stacking() {
  title 'Stacking'
  say 'identical notifications stack into one, counted ×N'
  for _ in 1 2 3; do "$send" -a demo 'Same notification' 'Sent three times.'; pause 0.5; done
  pause 1
  say "a rule's group-by: [app] keeps one popup per app"
  "$send" -a demo-chat 'Ana' 'hi!'
  pause 0.6
  "$send" -a demo-chat 'Ben' 'hey, all good?'
  pause 0.6
  "$send" -a demo-chat 'Ana' 'lunch at noon?'
  pause 4
}

styles() {
  title 'Styles'
  shell "notifyd-send --fg '#1a1b26' --bg '#e0af68' --frame '#ff9e64' ..."
  "$send" -a demo --fg '#1a1b26' --bg '#e0af68' --frame '#ff9e64' 'Colors per notification' \
    "--fg, --bg and --frame: dunst's color hints"
  pause 1.5
  "$send" -a demo-style 'Styled by a rule' 'background, border, radius, title and body colors, and raw CSS'
  pause 1.5
  "$send" -a demo-card --image "$cover" 'Another preset by a rule' 'This app gets the card preset.'
  pause 1.5
  "$send" -a demo-quick 'Quick' 'A rule hides the icon, app name and time, and gives it 2s.'
  pause 4
}

rules() {
  title 'Rules'
  say 'a regex with named groups rewrites the text and raises the urgency:'
  shell "notifyd rules --app demo-battery --summary 'power: 7% left'"
  "$notifyd" rules -c "$root/examples/demo.yaml" --app demo-battery --summary 'power: 7% left' --body 'on battery' |
    sed -n '1,3s/^/    /p'
  "$send" -a demo-battery 'power: 7% left' 'on battery'
  pause 2
  say 'buttons from a rule run commands (try Shuffle)'
  "$send" -a demo-player -i media-playback-start 'Now playing' 'Rule buttons: Shuffle and Next.'
  pause 3
  say 'a rule runs a command for each match:'
  "$send" -a demo -c demo.hook 'Hooked' 'A rule ran a command for this one.'
  for _ in $(seq 40); do [[ -s $work/run.log ]] && break; sleep 0.05; done
  say "${green}run.log: $(cat "$work/run.log" 2>/dev/null)$reset"
  pause 2
  say 'modes: in dnd a rule skips all but critical ones'
  shell 'notifyd ctl mode dnd'
  "$notifyd" ctl mode dnd >/dev/null
  "$send" -a demo -u low 'You will not see this' 'skipped in dnd (still in the log and history)'
  "$send" -a demo -u critical 'Critical still shows' 'in dnd mode'
  "$notifyd" ctl mode normal >/dev/null
  pause 4
}

presets() {
  title 'Presets'
  local preset
  for preset in card dunst minimal high-contrast; do
    shell "notifyd ctl preview $preset"
    "$notifyd" ctl preview "$preset" >/dev/null
    pause 3
    clear_all
  done
}

center() {
  title 'The notification center'
  say 'the history, now playing, volume, modes: notifyd ctl center (j/k move, ? shows the keys)'
  shell 'notifyd ctl center show'
  "$notifyd" ctl center show >/dev/null 2>&1
  pause 5
  "$notifyd" ctl center hide >/dev/null 2>&1
}

# -- run ---------------------------------------------------------------------------------------------

printf '%snotifyd demo%s %s\n' "$bold" "$reset" \
  "$([[ -n $live ]] && echo '(on the daemon you run)' || echo "(private daemon, $renderer)")"
for name in "${SECTIONS[@]}"; do
  ((${#only[@]})) && [[ " ${only[*]} " != *" $name "* ]] && continue
  if [[ -n $live && " ${NEEDS_RULES[*]} " == *" $name "* ]]; then
    title "$name"; say "skipped: needs examples/demo.yaml's rules (run without --live)"; continue
  fi
  clear_all
  "$name"
  if [[ -n $step ]]; then
    read -rp "  ${dim}[Enter] next${reset} " _ </dev/tty
  fi
done
clear_all
printf '\n%sdone%s\n' "$bold" "$reset"
