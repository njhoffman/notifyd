# TODO

notifyd: one notification daemon for this desktop that combines deadd's look and notification
center with dunst's rules, stacking and CLI, plus preset styles, a notify-send replacement and
colorful logging. Research is in `docs/research.md`, the design in `docs/design.md`, the config
reference in `docs/config.md`. Every item has a priority, `(p1)` high, `(p2)` normal or `(p3)` low,
and a category: bug, feature, refactor, docs, test, chore or perf. Decisions taken on 2026-11-10
(Python + GTK3, YAML, Pango plus preset-named styles, rules override sender timeouts, notification
center after popups + history) are under Completed.

Reviewed: 2026-11-10

## 2. Core daemon

- [x] **D-Bus server.** (p1) [feature] Own `org.freedesktop.Notifications`: `Notify`,
      `CloseNotification`, `GetCapabilities`, `GetServerInformation` (spec 1.2), signals
      `NotificationClosed` with correct reasons and `ActionInvoked`. Fail clearly when another
      daemon owns the name, naming it (as `notify-daemon owner` does).
- [x] **Replace semantics.** (p1) [feature] `replaces_id` updates in place and returns the same
      id (deadd returns a new id, so backdrop closes the old one with gdbus). Also replace by
      tag: `x-dunst-stack-tag`, `x-canonical-private-synchronous`, `synchronous`.
- [x] **Hints.** (p1) [feature] urgency, category, desktop-entry, `image-path` (path or file://
      URI), `image-data`, `icon_data`, `value` (progress bar), `resident`, `transient`.
- [x] **dunst color hints.** (p3) [feature] `fgcolor`/`bgcolor`/`frcolor`/`hlcolor` as style
      overrides that rules can still override (`rules.hint_style`).
- [x] **Sound hints.** (p3) [feature] `sound-file`/`sound-name`/`suppress-sound` (play with
      canberra-gtk-play or paplay), and a `sound:` rule key.
- [x] **Expiry.** (p2) [feature] Timeouts paused while hovered, resumed with what was left
      (at least 1 s); critical never expires by default (`timeout-critical`).
- [x] **Idle threshold.** (p3) [feature] dunst's `idle_threshold`: no expiry while the user is
      idle (XScreenSaver idle time via Xlib or `xprintidle`), so nothing is missed at lunch.
- [x] **History in memory.** (p2) [feature] The last `history.size` (100) with closed reason and
      matched rules; `notifyd ctl history`.
- [x] **History on disk.** (p3) [feature] `$XDG_STATE_HOME/notifyd/history.jsonl`, compacted at
      twice `history.size`; a torn last line is skipped.
- [x] **History pop.** (p3) [feature] Re-show a closed notification (dunst `history-pop`), from
      `ctl history`: `notifyd ctl pop [ID]`.
- [x] **History picker.** (p3) [feature] rofi or fzf over `ctl history --json`, popping the one
      chosen: `notifyd ctl history --pick` (fzf).
- [x] **Pause.** (p2) [feature] `ctl pause/resume/toggle`: everything but critical is held back
      and replayed on resume.
- [x] **Modes.** (p2) [feature] mako-style named modes (`dnd`, `quiet`, `presentation`) that
      rules can match on (`match: {mode: ...}`), set with `notifyd ctl mode`, shown in `status`.
- [x] **Remember the mode.** (p3) [feature] The mode resets to normal when the daemon restarts;
      kept in `$XDG_STATE_HOME/notifyd/state.json`.
- [ ] **Mode in the status bar.** (p3) [feature] A polybar module showing the mode and paused
      state (`notifyd ctl status` or a watch on the Event signal), clicking toggles dnd.
- [x] **Duplicates.** (p3) [feature] Identical notifications stack with a `×N` count
      (`stack-duplicates`).
- [ ] **Group by app.** (p3) [feature] mako's `group-by`: one card per app with the latest on top
      and a count, expanded on hover.
- [x] **Monitor choice.** (p2) [feature] `position.follow: mouse` (deadd's follow-mouse) or a
      fixed `position.monitor`, within the monitor's work area.
- [x] **Re-place on monitor changes.** (p2) [bug] The stack is laid out when a notification
      opens or closes; after displayd changes the layout (xrandr) open ones stay where they were
      until then. Now `monitors-changed` lays them out again, and the stack keeps the monitor it
      opened on until it is empty.

## 3. Rules and interception

- [x] **Rule engine.** (p1) [feature] Ordered rules; `match` on app, summary, body, icon,
      category, urgency, desktop-entry, stack tag and any hint; exact, list or `/regex/i`. A
      matching rule applies any config key, later rules overriding earlier ones.
- [x] **Rewrites.** (p2) [feature] summary/body/app templates with named groups, urgency,
      stack tag, icon (`icon.name`), image (`image.path`), `skip`, `history.keep`.
- [x] **Actions from rules.** (p3) [feature] Add buttons with commands to matching notifications
      (deadd `action-commands`, e.g. playerctl buttons on song notifications).
- [x] **Scripts.** (p3) [feature] `run:` on match with the notification as env vars and JSON on
      stdin (dunst `script`), never blocking the display.
- [x] **Rule tracing.** (p2) [feature] `notifyd rules --app X --summary Y ...` shows which rules
      match and the resulting settings and timeout; the log lists matched rules per event.

## 4. Rendering and presets

- [x] **Popup window.** (p1) [feature] Override-redirect GTK3 window, RGBA visual, WM class
      `Notifyd`, title `notifyd` (picom's `! name~=''` excludes nameless windows from shadows),
      deadd's layout.
- [x] **Pango markup.** (p1) [feature] Full Pango in summary and body, per-rule
      `markup: full|strip|plain`, named styles (`<span class="accent">`), fallbacks for bare `&`
      and broken markup.
- [x] **deadd preset.** (p1) [feature] The default, from `~/.config/deadd/deadd.css`.
- [x] **More presets.** (p2) [feature] dunst, minimal, card, tokyonight, high-contrast, as data
      files; user presets in `~/.config/notifyd/presets/`.
- [x] **Live preview.** (p2) [feature] `ctl preview [PRESET]`, `ctl preset NAME`,
      `notifyd presets --pick` (fzf: moving previews, enter switches).
- [x] **Progress bars and actions.** (p2) [feature] `value` hint as a bar, action buttons,
      clicks per `click.left/middle/right`.
- [x] **Hot reload.** (p2) [feature] On file change, SIGHUP or `ctl reload`; a broken file keeps
      the old config and says why.
- [ ] **Check translucency and shadow on the real desktop.** (p1) [test] Under Xvfb picom
      draws a black root, so the 0.8 opacity and the shadow were not seen. Run
      `notifyd daemon --replace` on i3 + picom and compare with deadd side by side.
- [x] **Slide/fade animations.** (p3) [feature] Fade in/out (window opacity steps on a GLib
      timer). picom fades windows in, but `no-fading-destroyed-argb` skips translucent windows on
      the way out, so notifyd fades out (`fade.out`, 150 ms) and leaves fading in to picom.
- [ ] **Preset gallery.** (p3) [docs] Screenshots of each preset in `docs/presets.md`
      (`render.sh`-style Xvfb capture once translucency renders there).

## 5. Sender and control CLI

- [x] **notifyd send.** (p1) [feature] notify-send flags plus `--replace-name`
      (`--replaces-process`), `--tag`, `--image`, `--progress`, `--close`; waits and prints the
      chosen action.
- [x] **notifyd ctl.** (p2) [feature] status, history, close, action, close-all, pause, resume,
      toggle, reload, preset, preview, watch; over the `io.github.njhoffman.Notifyd` interface.
- [x] **Shell completion.** (p3) [feature] `notifyd completion zsh|bash|fish`, generated from
      the argparse tree; preset names complete live.
- [x] **Complete notification ids.** (p3) [feature] `ctl close`/`ctl action` ids could complete
      from the daemon: `ctl list --ids`, with the summary as the description in zsh and fish.
- [ ] **Run the fish completion.** (p3) [test] fish is not installed here, so the fish script is
      only checked for its shape (`tests/test_cli.py`); `fish --no-execute` and `complete -C`
      tests skip. Check it on a machine with fish.
- [x] **Help and version.** (p2) [feature] `-h/--help` on every command with examples,
      `notifyd help [COMMAND]`, `notifyd version`, `-V`.
- [x] **Man page.** (p2) [docs] `share/man/man1/notifyd.1`; a test fails when a command or
      option is missing from it or its version is stale.
- [x] **Process name.** (p2) [bug] The daemon ran as `python3` in `/proc/PID/comm`, so a second
      daemon reported "owned by python3" and `pgrep notifyd` found nothing; it now sets its name
      with `prctl(PR_SET_NAME)`.

## 6. Logging

- [x] **Colored event log.** (p1) [feature] One aligned line per event, colored by urgency and
      event; `NO_COLOR` and non-terminals plain.
- [x] **Structured output.** (p2) [feature] `--log-format json`, `notifyd ctl watch [--json]`.
- [x] **Log levels.** (p3) [feature] `-v/-q`, `log.level`.
- [x] **Log file.** (p3) [feature] `log.file`, plain lines (rotation is left to logrotate).

## 7. Integration with this desktop

- [x] **deadd importer.** (p1) [feature] `notifyd import deadd [-o config]`; style values equal to
      the deadd preset are left out, unsupported keys are reported.
- [x] **dunst importer.** (p3) [feature] `notifyd import dunst`: urgency colors into a preset,
      `[rule]` sections into rules.
- [x] **Song and wallpaper timeouts.** (p1) [feature] `examples/config.yaml`: songs (musicman,
      MPD, Spotify) and backdrop at 12 s; deadd's Spotify 3 s rule dropped.
- [ ] **Install on this desktop.** (p1) [chore] `just install`, `just import-deadd`, then add
      the songs/wallpaper rules from `examples/config.yaml`; link `share/systemd/notifyd.service`
      into `~/.config/systemd/user/`.
- [ ] **notify-daemon entry.** (p2) [chore] Tracked in `~/.shell/TODO.md` section 4 (its repo).
      Add `notifyd` to `~/.shell/scripts/notify-daemon` (`ORDER`, `BIN`, `UNIT=notifyd.service`) so `notify-daemon use notifyd|deadd` switches. Its
      `owner()` reads `/proc/PID/exe` (python3 for notifyd) before `comm`; it needs to try `comm`
      too, and its `pkill -f` pattern must match `notifyd daemon`.
- [x] **picom rules.** (p2) [chore] Checked `~/.config/picom/picom.conf`: no rule targets class
      `Notifyd`, and the window has a name, so it gets the shadow like deadd.
- [x] **Notification center toggle.** (p3) [feature] `$mod+grave` sends SIGUSR1 to deadd
      (`~/.config/i3/conf.d/60-apps.conf:101`); move it to a `notifyd ctl center` once the
      center exists (section 10). Done: notifyd toggles its center on SIGUSR1 too, so the
      binding needs no change; `notifyd ctl center` also works.
- [ ] **Stale deadd config copy.** (p3) [chore] `~/.config/deadd.yml` is an older copy of
      `~/.config/deadd/deadd.yml` (no backdrop rule); the importer reads the latter, and the old
      file can go.

## 10. Notification center

- [x] **Center sidebar.** (p2) [feature] deadd's sidebar: history as cards, clear all, the pause
      toggle, opened with `notifyd ctl center` (and SIGUSR1 for the current i3 binding).
- [ ] **Center widgets.** (p3) [feature] swaync-style extras in the center: MPRIS now playing with
      buttons, volume slider, mode picker; and keyboard use (it is a popup without focus now).

## 9. Project

- [x] **Spec compliance check.** (p2) [test] A script sending every hint and action combination
      used by musicman, backdrop and displayd, compared across notifyd, deadd and dunst:
      `just compliance` (`tools/compare.sh`); results in `docs/research.md` section 6.
- [ ] **Packaging.** (p3) [chore] `pipx install .` works from pyproject; check it pulls no
      PyGObject wheel build (system gi), or document `--system-site-packages`.

## Completed

### 1. Decisions (completed 2026-11-10)

- [x] **Language and toolkit.** (p1) [chore] Python + GTK3: GTK CSS for presets and live preview,
      native Pango, same stack as displayd. GTK4 cannot position windows on X11.
- [x] **Config format.** (p1) [chore] YAML.
- [x] **Agree on the config schema.** (p1) [docs] As in `docs/config.md`.
- [x] **Markup syntax for senders.** (p1) [chore] Pango plus preset-named styles
      (`<span class="accent">`).
- [x] **Timeout precedence.** (p2) [chore] A rule's timeout wins over the sender's.
- [x] **Notification center scope.** (p2) [chore] Popups + history first, the center later.

### 8. Project setup (completed 2026-11-10)

- [x] **Scaffold.** (p1) [chore] `src/notifyd`, `bin/notifyd`, pyproject (version in
      `notifyd/__init__.py`), ruff, Justfile (`test`, `lint`, `run`, `preview`, `install`,
      `import-deadd`), systemd unit.
- [x] **Tests.** (p1) [test] 118 pytest cases: config, rules, markup, manager (fake clock),
      importer, log, CSS, send parsing, and the daemon end to end on a private D-Bus, with the
      GTK renderer under Xvfb.
