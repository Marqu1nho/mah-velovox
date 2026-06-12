# readaloud

A hotkey-triggered, markdown-aware text-to-speech reader for macOS. Reads the
current selection (or the focused window) aloud with a natural voice and
intelligent prosody — including inside full-screen terminal TUIs (Claude Code,
vim, tmux) where Apple's Speak Selection fails.

One hotkey toggles read/stop, so it maps cleanly to a single mouse button.
All configuration lives in one YAML file.

## How it works

```
mouse button ──▶ keystroke (default ⌃⌥⌘S)
                      │
               ┌──────▼───────┐  simulates ⌘C, preserves the clipboard,
               │  Hammerspoon │  captures the selection, manages start/stop
               └──────┬───────┘
                      │ text via stdin
               ┌──────▼───────┐
               │  readaloud   │  Python CLI (uv-managed .venv)
               │  pipeline    │  clean → parse → script → speak
               └──────────────┘
```

- **clean** strips ANSI escapes, box-drawing/spinner glyphs, prompt markers,
  and re-joins hard-wrapped terminal lines.
- **parse** does a lightweight markdown structure pass (headers, lists, code
  fences, tables, blockquotes, rules).
- **script** turns that into a flat speech script with prosody: headers read
  slower with pauses, code blocks are announced not recited, lists get short
  pauses, paragraphs are sentence-split so *stop* feels instant.
- **speak** uses one of two engines: `say` (macOS, zero-latency, inherits your
  system voice) or `kokoro` (local neural TTS).

## Install

Requires [uv](https://docs.astral.sh/uv/) and macOS (Apple Silicon).

```sh
git clone <this repo> readaloud && cd readaloud
./install.sh             # add --no-kokoro to skip the ~300 MB model download
```

`install.sh` is idempotent. It:

1. Verifies `uv` is installed (instructs you if not).
2. Runs `uv sync` in the repo (creates `.venv/`, installs the package + deps).
3. Resolves the **absolute** CLI path (`<repo>/.venv/bin/readaloud`) and writes
   it to `~/.hammerspoon/readaloud_paths.lua`, so Hammerspoon always invokes the
   CLI by absolute path — never relying on `PATH`.
4. Downloads the kokoro model files into `~/.local/share/readaloud/models/`
   (skipped with `--no-kokoro`, and skipped if already present).
5. Copies `config.example.yaml` → `~/.config/readaloud/config.yaml` (never
   overwrites an existing config).
6. Installs Hammerspoon via `brew install --cask hammerspoon` if absent
   (instructs you if Homebrew is missing), symlinks `readaloud.lua` into
   `~/.hammerspoon/`, and idempotently adds `require("readaloud")` to
   `~/.hammerspoon/init.lua`.

### Permissions

Hammerspoon needs **Accessibility** permission for the ⌘C simulation and the
window-read AX walk:

> System Settings → Privacy & Security → Accessibility → enable **Hammerspoon**

Launch Hammerspoon (or reload its config) after granting it. The module flashes
a warning alert if the permission is missing.

### Mouse button

readaloud doesn't talk to your mouse — it just listens for a keystroke. Map your
mouse button to the toggle hotkey (default ⌃⌥⌘S) in your mouse software
(Logi Options+, SteerMouse, BetterTouchTool, etc.).

## Voices

### `say_voice: system` (default — the Siri loophole)

With `engine: say` and `voice.say_voice: system`, readaloud invokes
`/usr/bin/say` with **no `-v` flag**, so it inherits the macOS **Spoken Content**
system voice:

> System Settings → Accessibility → Read & Speak → System Voice

If that's set to a Siri voice (e.g. Siri Voice 2), readaloud speaks in it. This
is an undocumented fallback, verified working on macOS Tahoe 26.5. **Changing the
voice there changes readaloud's voice too** — there is no separate setting to
keep in sync.

> Note: Apple does **not** allow explicit Siri-voice targeting. `say -v "Siri
> Voice 2"` fails and Siri voices are hidden from voice-list APIs. The no-`-v`
> path is the only way to reach a Siri voice from `say`.

### Named Premium voices

To use a specific high-quality voice, download it first:

> System Settings → Accessibility → Read & Speak → Manage Voices → download e.g.
> **Zoe (Premium)**

then set:

```yaml
voice:
  say_voice: "Zoe (Premium)"
```

readaloud adds `-v "Zoe (Premium)"` to each `say` invocation.

### kokoro (local neural TTS)

```yaml
engine: kokoro
voice:
  kokoro_voice: af_heart
  speed: 1.1
```

The model files are downloaded by `install.sh`. kokoro synthesizes
chunk-by-chunk in a background thread and starts playback after the first chunk
(low perceived latency). It runs fully offline.

## Configuration

All knobs live in `~/.config/readaloud/config.yaml`. The config is re-read on
every invocation, so engine/voice/prosody changes take effect on the **next
read** without reinstalling. **Hotkey changes require a Hammerspoon reload**
(the hotkeys are bound when the Lua module loads).

| key | default | meaning |
| --- | --- | --- |
| `engine` | `say` | `say` \| `kokoro` |
| `hotkeys.toggle` | `[ctrl, alt, cmd, S]` | read selection / stop |
| `hotkeys.read_window` | `[ctrl, alt, cmd, W]` | read focused window text |
| `hotkeys.show_alerts` | `true` | flash start/stop alerts |
| `voice.say_voice` | `system` | `system` (Spoken Content voice) or a named voice |
| `voice.base_wpm` | `190` | say speaking rate (words/min) |
| `voice.kokoro_voice` | `af_heart` | kokoro voice name |
| `voice.speed` | `1.1` | kokoro speed (1.0 = natural) |
| `headers.rate_factor` | `0.85` | headers read slower |
| `headers.pause_before_ms` | `500` | pause before a header |
| `headers.pause_after_ms` | `400` | pause after a header |
| `headers.treat_all_caps_lines_as_headers` | `true` | ALL-CAPS lines → pseudo-headers |
| `pauses.paragraph_ms` | `350` | pause after a paragraph |
| `pauses.list_item_ms` | `200` | pause after a list item |
| `pauses.horizontal_rule_ms` | `600` | pause for a horizontal rule |
| `code_blocks.mode` | `skip` | `skip` (announce) \| `read` \| `silent-skip` |
| `code_blocks.announce_template` | `code block, {lines} lines` | spoken when skipping |
| `clean.rejoin` | `smart` | `smart` \| `always` \| `never` (hard-wrap repair) |
| `clean.urls` | `domain` | `domain` \| `full` \| `skip` |
| `clean.paths` | `basename` | `basename` \| `full` \| `skip` |
| `clean.emoji` | `skip` | `skip` \| `name` |
| `window_read.max_chars` | `20000` | cap for the window-read AX walk |
| `limits.max_selection_chars` | `60000` | guard against accidental ⌘A reads |

## CLI

```sh
readaloud --stdin                 # read piped text aloud
readaloud --window                # read piped window text (applies max_chars)
readaloud --config PATH ...       # use an alternate config file
readaloud --print-config-json     # print the merged config (defaults + yours)
readaloud --print-script          # print the speech-script chunks for stdin as
                                  # JSON, without speaking — inspect prosody
```

Example — inspect how a markdown blob will be spoken:

```sh
printf '## Title\n\nSome text.' | readaloud --print-script
```

## Development

```sh
uv sync
uv run pytest
```

`clean.py`, `parse.py`, and `script.py` are pure functions with unit tests over
realistic Claude-Code-TUI fixtures (box borders, spinners, ANSI, hard-wrapped
paragraphs, headers, fenced code, lists).

## Non-goals (v1)

No word-by-word highlighting, MP3 export, GUI settings window, cloud voices, or
document (PDF/web) reading.
