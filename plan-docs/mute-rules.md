# Mute rules — per-app ignore lists for captured text

*Status: designed, queued behind the Fable cherry-pick merge (touches the same
files). Implement in clean.py + config.py + lua `--app` passing.*

## Problem

Recurring UI chrome gets read aloud: TUI status lines ("↓ to manage · ctrl+o
to expand", "⎿ Backgrounded agent"), browser boilerplate, hint bars. These are
context-specific — what's noise in VS Code is content elsewhere.

## Config contract

```yaml
mute:
  global:                       # applied to every read
    - "↓ to manage · ctrl+o to expand"
    - "re:ctrl\\+[a-z] to \\w+" # `re:` prefix = regex; otherwise literal
  by_app:                       # keyed by frontmost app name at capture time
    Code:
      - "re:^⏺ .*\\(.*\\)$"
    Arc:
      - "Skip to content"
```

- Plain strings match literally (no escaping needed — most rules are pasted
  chrome full of regex metacharacters). `re:` prefix opts into Python regex.
- Case-sensitive (chrome is verbatim).
- Applies to both selection and window reads.

## Mechanics

1. Hammerspoon knows the frontmost app at capture time (already logged as
   `capture: app=...`); it passes `--app "<name>"` to the CLI.
2. clean.py applies `mute.global` + `mute.by_app[<app>]` early in the
   pipeline: matches are *excised* from the text; lines that become
   empty/symbol-only afterwards are dropped by the existing cleanup pass.
   A line containing a muted phrase mid-sentence keeps its other words.
3. App-name discovery: press the hotkey, read
   `~/.local/state/readaloud/hammerspoon.log` (`app=` field).

## Future

- `drop-line:` prefix to force whole-line removal on match.
- `--app` is the natural hook for the spec's stretch goal #5 (per-app config
  overrides) — voice, speed, clean settings keyed the same way.
