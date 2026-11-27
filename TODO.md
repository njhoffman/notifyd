# TODO

notifyd: one notification daemon for this desktop that combines deadd's look and notification
center with dunst's rules, stacking and CLI, plus preset styles, a notify-send replacement and
colorful logging. Research is in `docs/research.md`, the design in `docs/design.md`, the config
reference in `docs/config.md`. Every item has a priority, `(p1)` high, `(p2)` normal or `(p3)` low,
and a category: bug, feature, refactor, docs, test, chore or perf. Decisions taken on 2026-11-10
(Python + GTK3, YAML, Pango plus preset-named styles, rules override sender timeouts, notification
center after popups + history) are under Completed.

Reviewed: 2026-11-26

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

## 12. Markup, spec and robustness

Found in a review on 2026-11-26 of the code and of dunst, deadd, mako, swaync and wired-notify issues.

- [x] **Invalid markup is flagged.** (p2) [feature] Text Pango rejects is shown as typed with
      nothing stripped, plus a `markup error` mark in the popup and center header (tooltip: the
      reason) and a `warn` log line; a bare `<` is escaped like a bare `&`. Done in 0.12.0.
- [x] **`$mod+grave` cannot close the center.** (p2) [bug] The center's keyboard grab swallows the
      i3 binding; `center.toggle-key` (default `<Super>grave`) hides it. Done in 0.12.0.
- [x] **`<a href>` links render as escaped text.** (p1) [bug] `markup.parses()` uses
      `Pango.parse_markup`, which has no `<a>` (only GtkLabel does), so any link makes the whole
      body fall back to text, now with a `markup error` mark, although `body-hyperlinks` is
      advertised (`server.py:20`) and docs/config.md lists `<a href>`. Validate with `<a>` mapped
      to `<span>` but give the label the original; `activate-link` with an http/https/mailto
      allowlist (GTK opens `file://` too), and keep the click from dismissing the card. Tests in
      popup and center. Upstream: mako#116, swaync#755, deadd PR#94.
- [x] **Malformed `image-data` segfaults the daemon.** (p1) [bug] `pixbuf_from_data`
      (`render/gtk.py:56`) hands the sender's rowstride to `Pixbuf.new_from_bytes`, which only
      checks `len >= w*h*channels`; a 10x10 image with a huge rowstride crashed python (exit 139).
      Any session client can kill the daemon. Require `bits == 8`, `channels == 3 + has_alpha`,
      `0 < w,h <= 4096`, `rowstride >= w*channels`, `len >= rowstride*(h-1) + w*channels`; add
      image-data tests (there are none). Upstream: mako#629, swaync#610.
- [x] **HTML named entities break the text.** (p2) [bug] `&nbsp;`, `&hellip;`, `&copy;` are not
      Pango entities, so the whole body is shown as typed; a raw `&` inside an attribute
      (`href="?a=1&b=2"`) is not fixed either. Map `html.entities.name2codepoint` names to `&#N;`
      in `markup._normalize` and escape bare `&` in attribute values. Upstream: dunst#1399,
      swaync#16, wired#43.
- [x] **Unknown HTML tags show every tag.** (p3) [feature] Decision needed: one `<p>` or `<div>`
      from a web app makes the whole body literal (by design since 0.12.0: nothing is stripped).
      Middle way: unwrap only block tags (`<p>`, `<div>` to newlines) and keep showing the rest as
      typed. Upstream: deadd PR#30, deadd#93.
- [x] **Leading and trailing newlines draw blank lines.** (p3) [bug] `'line\n\n'` reaches the
      label unchanged. Strip summary and body edges in `manager.notify`. Upstream: dunst#1559.
- [x] **A renderer exception leaves the manager inconsistent.** (p2) [bug] `_show`
      (`manager.py:175`) adds the id to `displayed` before `renderer.show`; when that raises (a
      non-numeric `width` or `image.margin-right`, an unreadable preset raising `OSError` in
      `style_for`) no timer starts and the id holds a `max-visible` slot forever. Catch, log and
      roll back; type-check numeric settings in `config.normalize`.
- [x] **Held-back notifications are lost on shutdown.** (p2) [bug] `close_all` covers `displayed +
      waiting` only, so notifications held back by do-not-disturb never reach history or get
      `NotificationClosed` when the daemon stops (`daemon.py:284`); `paused` is not in
      `state.json` either. Close them with reason 4 (or persist the queue) and persist `paused`.
- [x] **Closing a center card sends reason 3.** (p2) [bug] The center's close button and `d`
      (`render/center.py`) call `manager.close(nid)`, default CLOSED (3: the sender closed it);
      a user dismissal is DISMISSED (2), as the center's Clear already sends.
- [x] **`history.jsonl` is world-readable.** (p2) [bug] `history.py` creates it 0644 under the
      default umask, and it holds full bodies (one-time codes, messages). Create it 0600 and its
      directory 0700.
- [x] **`replaces_id` of a gone notification gets a new id.** (p2) [bug] The spec returns a
      non-zero `replaces_id` as is (dunst does); `manager._replace_target` issues a new id, so
      scripts that keep a fixed id (`notify-send.sh -r 100`) lose track. Reuse the id when it is
      free; test `replaces_id` and a stack tag pointing at different open notifications
      (crashed mako). Upstream: mako#167, mako#653.
- [x] **`ctl action` signals keys that were never offered.** (p3) [bug] Neither `server.py` nor
      `manager.invoke` checks the key is in `n.actions`; a typo signals the app and closes the
      notification. Refuse unknown keys.
- [x] **Previews run rule commands, sounds and fill history.** (p3) [bug] `ctl preview`'s `extra`
      (`daemon.py:204`) sets preset, timeout and skip only, so a sample matching song rules fires
      `run:`, buttons and `sound:`, and five samples land in persistent history. Add `run: None,
      sound: False` and no history.
- [x] **`notifyd send -w` hangs when the daemon goes away.** (p3) [bug] `send.py` waits for
      signals from any sender and does not watch `NameOwnerChanged`. Quit when the bus name
      vanishes; subscribe to the owner's unique name.
- [x] **Advertise `sound`; consider `ActivationToken`.** (p3) [feature] Sound hints are handled but
      the `sound` capability is not advertised, so clients may play their own too; the spec 1.2
      `ActivationToken` signal before `ActionInvoked` is not sent.
- [x] **Option to ignore an app's own CloseNotification.** (p3) [feature] dunst's
      `ignore_dbusclose` as a rule key `ignore-close: true` (`server.py`, manager). Upstream:
      mako#561.
- [x] **Docs misstate which keys a rule can set.** (p3) [docs] docs/config.md leaves `center` out
      of the keys rules cannot set; the man page says a rule takes "any of those keys", but
      `position`, `max-visible`, `log`, `click` and `center` are refused (`rules.py:39`).

## 13. Rendering and the center

- [x] **The stack can run off the screen.** (p2) [bug] `GtkRenderer.layout` sums heights without
      checking the work area, so tall cards (the summary has no line cap) or `max-visible: 0`
      place windows off-screen. Queue what does not fit (`manager._room`) and add
      `title.max-lines`. Upstream: dunst#1533, dunst#625.
- [x] **Icon from the `desktop-entry` hint.** (p2) [feature] Electron and browser apps send an
      empty `app_icon` but a `desktop-entry`; fall back to
      `Gio.DesktopAppInfo.new(entry + '.desktop').get_icon()` in `fill`. Upstream: deadd PR#23,
      mako#581, swaync#447.
- [x] **History keeps too little for the center and `pop`.** (p2) [bug] `summary_dict` stores no
      `app_icon`, image path or markup mode, so center cards have no icon and render in `full`
      even for `markup: plain` rules, and `pop` brings back a bare notification that misses
      sender rules and reruns `run:`. Store them (optionally a small cached thumbnail of
      image-data) and pass them back on `pop`. Upstream: dunst#1065, mako#542, swaync#163.
- [x] **Fade settings are inconsistent.** (p3) [bug] `fade.in` comes from the notification's
      settings but `fade.out` from the global config (`render/gtk.py`), so a rule's `fade.out` is
      ignored, and the fade-in timer is not cancelled on close (it sets opacity on a destroyed
      window). Keep per-window fade state.
- [x] **Generated CSS grows until reload.** (p3) [perf] Each distinct style (dunst color hints
      vary per notification) adds a CSS scope that is never pruned, and each one reloads the whole
      provider. Prune to open windows or use per-window providers.
- [x] **Center rebuilds block the main loop.** (p3) [perf] Each player PropertiesChanged rebuilds
      the open center with synchronous `ListNames`/`GetAll` per player and two `pactl` runs (1 s
      timeout each); a hung player (or Firefox blocked on a synchronous Notify) freezes popups and
      D-Bus replies. Async calls, cache from signals, debounce. Upstream: astal#204.
- [x] **Symbolic icons are not recolored.** (p3) [feature] `load_pixbuf` uses `load_icon`, so
      `*-symbolic` icons keep their dark SVG color on dark cards, and no `GENERIC_FALLBACK`
      (`audio-volume-high-foo`). Use `lookup_icon(...).load_symbolic(fg)`. Upstream: dunst#1481.
- [x] **HiDPI images are blurry.** (p3) [perf] Pixbufs load at logical size and ignore
      `get_scale_factor()`; load at size x scale and draw via a cairo surface. Upstream:
      dunst#1470, dunst PR#1566.
- [x] **Remote art timeout and eviction.** (p3) [bug] The 3 s timeout in `art.py` is per socket
      operation, so a slow server holds the thread for minutes; cache hits do not touch mtime, so
      the prune evicts oldest-written, not least-used. Total deadline; `os.utime` on hit.
- [ ] **Read a cut-off notification in full.** (p3) [feature] Bodies stop at `max-lines` (3 in the
      center). Expand on hover, a `click.*: expand` value, and Enter in the center. Upstream:
      dunst#514.
- [ ] **Timeout bar.** (p3) [feature] A thin bar that drains with the timer and freezes on hover;
      the manager already keeps the deadline. Upstream: dunst PR#1550, deadd#56, swaync#765.
- [ ] **`+N` while popups wait.** (p3) [feature] dunst's `indicate_hidden`: show how many are
      waiting on the last card; optionally `position.order: newest-first|oldest-first`.
- [ ] **Group by app in the center.** (p3) [feature] Collapsible per-app groups with a count and
      "clear group". Upstream: swaync#61, mako#234.
- [ ] **Filter MPRIS players.** (p3) [feature] `center.mpris-ignore: [firefox, chromium]`, so
      browser tabs do not take the now-playing row. Upstream: swaync PR#324.
- [ ] **Center buttons that run commands.** (p3) [feature] A configured row of buttons or toggles
      (with a state command), e.g. wifi, bluetooth, displayd layouts. Upstream: swaync
      buttons-grid, swaync#277.
- [ ] **Accessibility.** (p3) [feature] Set an ATK role and accessible name and description on
      popups so Orca announces them; there is no `get_accessible` call today. Upstream: wired#20,
      swaync#48.

## 14. Behavior and scripting

- [x] **Fullscreen policy.** (p2) [feature] Override-redirect popups cover fullscreen video and
      games; `importer.py` already reports dunst's `fullscreen` as unsupported. Watch
      `_NET_ACTIVE_WINDOW` for `_NET_WM_STATE_FULLSCREEN` and add a rule key `fullscreen:
      show|delay|pushback` plus a `match: {fullscreen: true}` field. Upstream: dunst#553,
      dunst PR#1539, mako#535, swaync#256.
- [x] **Pause while the screen is locked.** (p2) [feature] Popups can stack above i3lock; hold them
      back while logind `LockedHint` or `org.freedesktop.ScreenSaver` says locked and replay on
      unlock through the pause path (`pause-when-locked`). Upstream: dunst#1495, wired#12,
      swaync#496.
- [ ] **Per-rule position or a second stack.** (p3) [feature] Rules cannot set `position`, so a
      volume OSD cannot sit centered while chat stays top-right: one stack per anchor. Needs a
      design note first (`layout`). Upstream: dunst#265, dunst#1561.
- [ ] **Monitor by name; follow keyboard focus.** (p3) [feature] `position.monitor` is an index
      that moves when displayd changes the layout; match `Gdk.Monitor.get_model()` and add
      `follow: focus` (the active window's monitor). Upstream: dunst#1398, dunst#1545, deadd#196.
- [ ] **Script filters that rewrite a notification.** (p3) [feature] A rule key `filter: CMD` run
      synchronously with a short timeout, merging the JSON it prints (summary, body, progress,
      hints). Upstream: deadd#215, dunst#1494.
- [ ] **Hooks on more events; `click.*` commands.** (p3) [feature] `run:` fires on arrival only:
      `run-on: [receive, action, close]`, and a click value `exec: CMD` with the `NOTIFYD_*`
      environment. Upstream: swaync#388, mako#38, swaync#226.
- [ ] **Drop specific actions with a rule.** (p3) [feature] `hide: [actions]` drops all; add e.g.
      `actions: {drop: [settings]}` for Chromium's "Settings". Upstream: swaync PR#654.
- [ ] **Action icons.** (p3) [feature] Advertise `action-icons` and use
      `Gtk.Button.new_from_icon_name` when the hint is set. Upstream: deadd PR#116.
- [ ] **Inline reply.** (p3) [feature] Advertise `inline-reply`, show an entry for that action (the
      center already grabs the keyboard) and emit `NotificationReplied`; KDE Connect and chat apps
      use it. Upstream: swaync PR#221, astal#250.
- [ ] **Timed do-not-disturb; close by criteria; missed count.** (p3) [feature] `ctl mode dnd
      --for 1h` (kept in `state.json`), `ctl close --app X`, and a "missed N" count in `ctl bar`
      since the center was last opened. Upstream: mako#278, mako#358, swaync#214.
- [ ] **Copy a one-time code.** (p3) [feature] Detect an OTP code in the body and add a "Copy
      123456" button (`Gtk.Clipboard`). Upstream: swaync#766.

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

### 2. Core daemon (completed 2026-11-22)

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
- [x] **Mode in the status bar.** (p3) [feature] A polybar module showing the mode and paused
      state: `notifyd ctl bar --follow`; the module itself is in `~/.config/polybar/TODO.md`.
- [x] **Duplicates.** (p3) [feature] Identical notifications stack with a `×N` count
      (`stack-duplicates`).
- [x] **Group by app.** (p3) [feature] mako's `group-by`: one card per app with the latest on top
      and a count: `group-by: [app]` (rule-settable). Expanding a group on hover is not done.
- [x] **Monitor choice.** (p2) [feature] `position.follow: mouse` (deadd's follow-mouse) or a
      fixed `position.monitor`, within the monitor's work area.
- [x] **Re-place on monitor changes.** (p2) [bug] The stack is laid out when a notification
      opens or closes; after displayd changes the layout (xrandr) open ones stay where they were
      until then. Now `monitors-changed` lays them out again, and the stack keeps the monitor it
      opened on until it is empty.

### 3. Rules and interception (completed 2026-11-22)

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

### 4. Rendering and presets (completed 2026-11-22)

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
- [x] **Check translucency and shadow on the real desktop.** (p1) [test] Under Xvfb picom
      draws a black root, so the 0.8 opacity and the shadow were not seen. Run
      `notifyd daemon --replace` on i3 + picom and compare with deadd side by side.
- [x] **Slide/fade animations.** (p3) [feature] Fade in/out (window opacity steps on a GLib
      timer). picom fades windows in, but `no-fading-destroyed-argb` skips translucent windows on
      the way out, so notifyd fades out (`fade.out`, 150 ms) and leaves fading in to picom.
- [x] **Preset gallery.** (p3) [docs] Screenshots of each preset in `docs/presets.md`
      (`tools/gallery.sh`, Xvfb without a compositor, so translucency is not shown).

### 6. Logging (completed 2026-11-22)

- [x] **Colored event log.** (p1) [feature] One aligned line per event, colored by urgency and
      event; `NO_COLOR` and non-terminals plain.
- [x] **Structured output.** (p2) [feature] `--log-format json`, `notifyd ctl watch [--json]`.
- [x] **Log levels.** (p3) [feature] `-v/-q`, `log.level`.
- [x] **Log file.** (p3) [feature] `log.file`, plain lines (rotation is left to logrotate).

### 7. Integration with this desktop (completed 2026-11-22)

- [x] **deadd importer.** (p1) [feature] `notifyd import deadd [-o config]`; style values equal to
      the deadd preset are left out, unsupported keys are reported.
- [x] **dunst importer.** (p3) [feature] `notifyd import dunst`: urgency colors into a preset,
      `[rule]` sections into rules.
- [x] **Song and wallpaper timeouts.** (p1) [feature] `examples/config.yaml`: songs (musicman,
      MPD, Spotify) and backdrop at 12 s; deadd's Spotify 3 s rule dropped.
- [x] **Install on this desktop.** (p1) [chore] `just install`, `just import-deadd`, then add
      the songs/wallpaper rules from `examples/config.yaml`; link `share/systemd/notifyd.service`
      into `~/.config/systemd/user/`.
- [x] **notify-daemon entry.** (p2) [chore] Tracked in `~/.shell/TODO.md` section 4 (its repo).
      Add `notifyd` to `~/.shell/scripts/notify-daemon` (`ORDER`, `BIN`, `UNIT=notifyd.service`) so `notify-daemon use notifyd|deadd` switches. Its
      `owner()` reads `/proc/PID/exe` (python3 for notifyd) before `comm`; it needs to try `comm`
      too, and its `pkill -f` pattern must match `notifyd daemon`.
- [x] **picom rules.** (p2) [chore] Checked `~/.config/picom/picom.conf`: no rule targets class
      `Notifyd`, and the window has a name, so it gets the shadow like deadd.
- [x] **Notification center toggle.** (p3) [feature] `$mod+grave` sends SIGUSR1 to deadd
      (`~/.config/i3/conf.d/60-apps.conf:101`); move it to a `notifyd ctl center` once the
      center exists (section 10). Done: notifyd toggles its center on SIGUSR1 too, so the
      binding needs no change; `notifyd ctl center` also works.
- [x] **i3 center binding.** (p2) [chore] `$mod+grave` runs `kill -s USR1 $(pidof
      deadd-notification-center)` (`~/.config/i3/conf.d/60-apps.conf:101`); with notifyd it needs
      `notifyd ctl center` (or `pkill -USR1 -x notifyd`: `pidof` matches the executable, which is
      python3 for notifyd). Belongs to the i3 config when notifyd replaces deadd.
- [x] **Stale deadd config copy.** (p3) [chore] `~/.config/deadd.yml` is an older copy of
      `~/.config/deadd/deadd.yml` (no backdrop rule); the importer reads the latter, and the old
      file can go.

### 9. Project (completed 2026-11-22)

- [x] **Spec compliance check.** (p2) [test] A script sending every hint and action combination
      used by musicman, backdrop and displayd, compared across notifyd, deadd and dunst:
      `just compliance` (`tools/compare.sh`); results in `docs/research.md` section 6.
- [x] **Packaging.** (p3) [chore] Installs into a venv made with `--system-site-packages`
      (`pip install --no-build-isolation .` offline), presets included; README says so.

### 10. Notification center (completed 2026-11-26)

- [x] **Center sidebar.** (p2) [feature] deadd's sidebar: history as cards, clear all, the pause
      toggle, opened with `notifyd ctl center` (and SIGUSR1 for the current i3 binding).
- [x] **Center now playing.** (p3) [feature] MPRIS now playing with previous/play-pause/next
      (`notifyd/mpris.py`), refreshed on the players' PropertiesChanged.
- [x] **Center volume and mode picker.** (p3) [feature] swaync-style volume slider and mute for the
      default output (`notifyd/volume.py`, pactl), and a picker of the modes rules match on (0.7.0).
- [x] **Center keyboard use.** (p3) [feature] The center is an override-redirect popup without
      focus: grab the keyboard while it is open (as rofi does), release it on hide; retry the grab
      briefly when i3 still holds it. Keys: Esc/q close, j/k and arrows move between cards, Enter
      shows one again, d/Delete forgets or closes it, C clears, p do not disturb, m next mode,
      space/n/N play-pause/next/previous, +/- volume. Done in 0.8.0.
- [x] **Center remote cover art.** (p3) [feature] Spotify's `mpris:artUrl` is `https://i.scdn.co/...`,
      skipped now. Fetch http(s) art for the now-playing row only (not notification images, which
      keep the no-network rule) in a thread: 3 s timeout, 2 MB cap, cached under
      `~/.cache/notifyd/art/`; draw without it, then fill it in. `center.remote-art`.

### 11. Maintenance (completed 2026-11-25)

- [x] **Preset descriptions cut at `#`.** (p2) [bug] `description:` in `presets/deadd.yaml` and
      `presets/dunst.yaml` is unquoted, so YAML reads ` #acf text` and ` #000811, thin frame...` as
      comments: `notifyd presets` prints "...on" for dunst and "...shadow)," for deadd, and
      `docs/presets.md` carries the cut text. Quote them, and add a test that every preset's
      description matches its file's first line.
- [x] **Man page version is stale.** (p3) [docs] `share/man/man1/notifyd.1` says `notifyd 0.6.0`
      while `__version__` is 0.6.1; nothing keeps them together. Test that `.TH` names the current
      version (or stamp it in `just install`).
- [x] **README install section predates the switch.** (p3) [docs] It still says to restart deadd
      with `notify-daemon ensure` and to link the unit by hand; now `notify-daemon use notifyd`
      (shell 0.126.0) does the switch, and `notify-daemon use deadd` goes back.
- [x] **`just install` links the systemd unit.** (p3) [chore] It links the binary, man page and
      completions but not `share/systemd/notifyd.service`, which had to be linked by hand.
- [x] **History keeps the sender.** (p3) [feature] Entries have no sender pid, process name or
      `desktop-entry` hint, so two senders using one app name (the Spotify client and musicman, both
      "Spotify") could not be told apart from `ctl history --json`. Record them on receipt and show
      them in `--json` and `ctl watch`.
- [x] **Rules match on the sender.** (p3) [feature] `match: {process: musicman}` (and `parent`,
      `desktop-entry`) on the sender recorded since 0.10.0, so a rule can drop one of two senders
      that share an app name (the Spotify client and musicman both send "Spotify").
- [x] **Center has no tests of its own.** (p3) [test] `render/center.py` is only exercised through
      the daemon tests; the widget work (section 10) adds `tests/test_center.py` for its rows.
- [x] **The unit claims the bus name.** (p2) [bug] `share/systemd/notifyd.service` had
      `Type=dbus` and `BusName=org.freedesktop.Notifications`, like dunst's unit; systemd refuses
      the second unit it loads that names the same bus, so after a daemon-reload notifyd's unit
      could not start (seen 2026-11-25 on charon). Done 2026-11-25 (0.11.1): `Type=simple`, no
      `BusName=`; `tests/test_unit.py`.
