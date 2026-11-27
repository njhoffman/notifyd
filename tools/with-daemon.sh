#!/usr/bin/env bash
# Run COMMAND against a private notifyd: its own session bus (dbus-run-session), CONFIG, and a temporary
# state and cache, so the daemon you run and its history are not touched. The daemon draws on $DISPLAY
# (or only logs, with --renderer none) and stops when COMMAND ends; COMMAND's exit status is returned.
#
#   tools/with-daemon.sh [--renderer gtk|none] [--log FILE] CONFIG -- COMMAND [ARG]...
#
# COMMAND sees NOTIFYD_PRIVATE_DIR (a temporary directory, removed afterwards) and the private bus.
set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
renderer=gtk log=''
while (($#)); do
  case $1 in
    --renderer) renderer=${2:-}; shift ;;
    --log) log=${2:-}; shift ;;
    --) shift; break ;;
    -*) echo "with-daemon: unknown option $1" >&2; exit 2 ;;
    *) config=$1 ;;
  esac
  shift
done
[[ -n ${config:-} && $# -gt 0 ]] || { echo "usage: with-daemon.sh [--renderer gtk|none] [--log FILE] CONFIG -- COMMAND..." >&2; exit 2; }
[[ $renderer == gtk || $renderer == none ]] || { echo "with-daemon: --renderer is gtk or none" >&2; exit 2; }
config=$(realpath "$config")
[[ -z $log ]] || log=$(realpath -m "$log")

if [[ -z ${NOTIFYD_WITH_DAEMON_BUS:-} ]]; then
  command -v dbus-run-session >/dev/null || { echo "with-daemon: needs dbus-run-session" >&2; exit 1; }
  [[ $renderer == none || -n ${DISPLAY:-} ]] || { echo "with-daemon: no DISPLAY (use --renderer none)" >&2; exit 1; }
  # GIO_USE_VFS=local: no gvfs on the private bus (it would start one and fail to mount); NO_AT_BRIDGE: no
  # accessibility bus started on it either
  NOTIFYD_WITH_DAEMON_BUS=1 GIO_USE_VFS=local NO_AT_BRIDGE=1 exec dbus-run-session -- "$0" --renderer "$renderer" \
    ${log:+--log "$log"} "$config" -- "$@"
fi
unset NOTIFYD_WITH_DAEMON_BUS          # a nested with-daemon.sh starts its own bus

work=$(mktemp -d "${TMPDIR:-/tmp}/notifyd-private.XXXXXX")
daemon_pid=''
cleanup() {
  [[ -n $daemon_pid ]] && kill "$daemon_pid" 2>/dev/null && wait "$daemon_pid" 2>/dev/null
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export NOTIFYD_PRIVATE_DIR=$work XDG_STATE_HOME=$work/state XDG_CACHE_HOME=$work/cache
"$root/bin/notifyd" daemon -c "$config" --renderer "$renderer" --log-format json >"${log:-$work/daemon.log}" 2>&1 &
daemon_pid=$!
for _ in $(seq 100); do
  "$root/bin/notifyd" ctl status >/dev/null 2>&1 && break
  if ! kill -0 "$daemon_pid" 2>/dev/null; then
    echo "with-daemon: the daemon did not start:" >&2
    cat "${log:-$work/daemon.log}" >&2
    exit 1
  fi
  sleep 0.1
done
"$root/bin/notifyd" ctl status >/dev/null 2>&1 || { echo "with-daemon: the daemon did not answer" >&2; exit 1; }

"$@"
