# Design

Status: agreed 2026-11-10. Every recommendation in section 6 was accepted: Python + GTK3,
YAML, Pango plus preset-named styles, rule timeouts win, center after popups + history.

## 1. Goals

- One daemon that owns `org.freedesktop.Notifications` on X11 (i3 + picom), replacing the
  deadd/dunst switch for daily use while `notify-daemon` can still switch back.
- deadd's look as the default: black card at 0.8 opacity, no visible frame, shadow from
  the compositor, `#acf` text, bold 16 px title.
- A config simpler than deadd's (fewer nesting levels, sensible defaults so a near-empty
  file works) with an importer that turns the current deadd YAML + CSS into it.
- Per-app (per-match) rules that can restyle, retime, re-icon, retitle, hide, or script a
  notification: the "intercept and restyle" feature.
- Preset styles, switchable and previewable live.
- A bundled sender (`notifyd send`) that replaces notify-send and notify-send.py.
- Colorful, structured logging of every notification and rule decision.

## 2. Components

```
notifyd daemon      D-Bus server, rule engine, renderer, history, control socket/D-Bus
notifyd send        notify-send compatible sender (+ notify-send.py extras)
notifyd ctl ...     close, close-all, history, pause/modes, reload, preset, preview, watch
notifyd import      deadd yml+css (and dunstrc) -> notifyd config
```

Pipeline for each `Notify` call:

```
D-Bus Notify -> normalize (hints, image, urgency) -> rules (match -> modify, in order)
  -> mode filter (DND, pause level) -> stack/replace (id, tag) -> render (preset + overrides)
  -> history -> log line / watch stream -> expire / action / close signals
```

## 3. Config shape

YAML, one file, everything optional. See `docs/config.md` (reference) and `examples/config.yaml` for the current deadd
settings expressed this way. Principles:

- Top level: `preset`, `position`, `size`, `timeout`, `markup`, `history`, `log`, `rules`.
- A preset is a named bundle of style keys (colors, opacity, radius, padding, fonts,
  image size, layout); top-level `style:` overrides keys of the chosen preset.
- Rules are a list; each has `match:` (any field, regex with `/.../`, hints) and the same
  style/behavior keys as everything else, so a rule is "a partial config for matching
  notifications". This is the dunst model with deadd's `class-name` idea folded in.

## 4. Styling syntax inside notifications

Senders here already emit Pango markup (`<b>`, `<span foreground=...>`), which is also what
dunst, mako and swaync render. Proposal: Pango markup is the format, plus named styles
from the preset that expand to Pango spans, e.g. `<s:accent>text</s>` or
`<span class="accent">` -> `<span foreground="#7aa2f7">`. That gives theme-aware styling
without inventing a second language, and plain notify-send keeps working.

## 5. Logging

One line per event, colored by urgency, fields aligned: time, id, app, urgency, summary,
rule(s) applied, action (shown / replaced / stacked / suppressed / expired / dismissed /
action:key). `--log-format json` for machines; `notifyd watch` streams the same events
from a running daemon.

## 6. Decisions needed

1. **Language and toolkit.** Must draw translucent, undecorated, positioned windows on X11.
   - Python + GTK3 (PyGObject): GTK CSS gives presets and live preview almost for free, Pango
     markup native, fastest to build, same stack as displayd and notify-send.py. Costs: ~40-60 MB
     RSS, startup ~300 ms, GTK3 is in maintenance.
   - Rust + cairo/pango + x11rb (wired's approach): small and fast, full control of layout,
     but every widget (buttons, center, sliders) is hand-drawn; much more work.
   - GTK4 is a poor fit on X11: it cannot position its own windows (no `gtk_window_move`),
     so it needs Xlib workarounds for every popup.
   Recommendation: Python + GTK3 now; the D-Bus, rule and config layers stay toolkit-free so
   the renderer could be swapped later.
2. **Config format.** YAML (continuity with deadd, importer is trivial) vs TOML (stricter,
   matches displayd). Recommendation: YAML.
3. **Markup syntax** (section 4): Pango + named styles, or Pango only, or a lighter
   shorthand (`{accent}text{/}`) translated to Pango.
4. **Notification center** in v1, or popups + history CLI (rofi picker) first, center later.
   Recommendation: center later; rofi history first.
5. **Timeout precedence**: rule > sender's `expire_timeout` > preset default (deadd's
   behavior, and what the long song/wallpaper notifications need), or sender wins unless the
   rule says `force`. Recommendation: rule wins.
6. **Sender CLI name**: `notifyd send` only, or also install `notify-send`-compatible shim
   names (`notifyd-send`, `notify-send.py`) for scripts.
