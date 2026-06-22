# speakwrite

A hotkey-triggered, on-device dictation app for macOS (native Swift, in `mac/`).
Press the hotkey, talk, and the transcript is pasted at your cursor. It runs on
Apple's on-device speech stack — no cloud, no network.

Config lives at `~/.config/speakwrite/config.json` and is read **at launch**, so
config changes need a relaunch (`make speak`).

## Speech engine

SpeakWrite can use either of Apple's two on-device transcribers. Pick one with
the `engine` key:

```json
{
  "engine": "dictation",
  "dictation": {
    "punctuation": false,
    "emoji": true
  }
}
```

| `engine`      | Class (Apple)         | Punctuation & caps                                    |
| ------------- | --------------------- | ----------------------------------------------------- |
| `"speech"`    | `SpeechTranscriber`   | Always auto-punctuated and auto-capitalized. No knob. |
| `"dictation"` | `DictationTranscriber`| Punctuation/emoji are **opt-in** (see below).         |

### `"speech"` — auto-punctuated (default)

Apple's `SpeechTranscriber` with the `.progressiveTranscription` preset. It
auto-inserts punctuation and capitalization, and the engine exposes **no toggle**
to turn that off. A side effect: when you pause to think mid-sentence, it often
finalizes the segment with a period — so a thinking pause can wrongly end your
sentence.

### `"dictation"` — punctuation on your terms

Apple's `DictationTranscriber`. Punctuation and emoji are separate opt-in flags:

- **`dictation.punctuation`** (default `false`) — when `false`, the engine
  **never auto-inserts periods/commas** and stays mostly lowercase, so a pause
  never ends a sentence. You add punctuation yourself (speak it, or via
  `replacements`). Set `true` to get auto-punctuation back.
- **`dictation.emoji`** (default `false`) — when `true`, spoken emoji names are
  converted to the emoji (say "star-struck" → 🤩, "fire" → 🔥).

This mode suits short bursts of dictated text where you'd rather format
punctuation by hand than fight auto-inserted periods. The first run after
switching may pause briefly while macOS downloads the dictation model (one-time).

To confirm which engine a session actually started, dictate once and check the
log:

```sh
log show --predicate 'eventMessage CONTAINS "speakwrite: engine"' --last 5m --style compact
```

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
5. Ensures `~/.config/readaloud/` exists. No config file is written —
   built-in defaults apply until you create one (see **Configuration** below).
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

### Where the live config lives

```
~/.config/readaloud/config.yaml
```

or `$XDG_CONFIG_HOME/readaloud/config.yaml` if `XDG_CONFIG_HOME` is set.

If the file is absent, built-in defaults apply — no config is required to
start. Engine/voice/prosody/mute changes take effect on the **next read**
without reinstalling. **Hotkey changes require a Hammerspoon reload** (hotkeys
are bound when the Lua module loads, not per-read).

**Why `~/.config` and not the repo?** It is the standard XDG location that
apps and a future GUI look in by default. It survives repo operations (`git
clean`, branch switch, re-clone) and keeps your personal settings separate
from the program source.

### Full annotated example

Every key is shown at its default value. Copy only the keys you want to
change; unspecified keys stay at their defaults.

```yaml
# Which TTS engine to use.
#   say    — macOS /usr/bin/say (zero latency, inherits Spoken Content voice)
#   kokoro — local neural TTS (requires model files downloaded by install.sh)
engine: say

# ---------------------------------------------------------------------------
# Hotkeys
# ---------------------------------------------------------------------------
# Control model:
#   The toggle hotkey = START / PAUSE / RESUME. It never stops a read.
#   To STOP a read, click the top-center transport pill:
#     left zone  = play / pause
#     right zone = stop
# Hotkey changes require a Hammerspoon reload to take effect.
hotkeys:
  toggle: ["ctrl", "alt", "cmd", "S"]       # start / pause / resume
  read_window: ["ctrl", "alt", "cmd", "W"]  # same control model, for window reads
  show_alerts: true                          # flash the transport/status pill on start/stop

# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------
voice:
  # say engine: which voice to use.
  #   system           — no -v flag passed to say; inherits the macOS Spoken
  #                      Content voice (System Settings → Accessibility → Read &
  #                      Speak → System Voice). If that is a Siri voice,
  #                      readaloud speaks in it — this is the only way to reach
  #                      Siri voices.
  #   "Zoe (Premium)"  — any named Premium voice (download in Manage Voices first)
  say_voice: system

  # say engine speaking rate (words per minute).
  base_wpm: 240

  # kokoro engine: voice identifier.
  kokoro_voice: af_heart

  # kokoro engine: playback speed multiplier (1.0 = natural).
  speed: 1.1

# ---------------------------------------------------------------------------
# Alerts (transport / status pill)
# ---------------------------------------------------------------------------
alerts:
  # Vertical center of the status pill, as a percentage of screen height.
  # 0 = top of screen, 50 = center. Default 3.5 puts it near the top edge.
  y_pct: 3.5

  # How long a transient alert (e.g. "stopped") lingers before fading (seconds).
  duration_s: 1.2

# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------
headers:
  # Headers are read at base_wpm * rate_factor — slightly slower for emphasis.
  rate_factor: 0.85

  # Silence inserted before a header (milliseconds).
  pause_before_ms: 500

  # Silence inserted after a header (milliseconds).
  pause_after_ms: 400

  # When true, lines that are ALL CAPS are treated as pseudo-headers and get
  # the same rate/pause treatment as markdown ## headers.
  treat_all_caps_lines_as_headers: true

# ---------------------------------------------------------------------------
# Pauses
# ---------------------------------------------------------------------------
pauses:
  # Silence after a paragraph break (milliseconds).
  paragraph_ms: 350

  # Silence after each list item (milliseconds).
  list_item_ms: 200

  # Silence when a horizontal rule (---) is encountered (milliseconds).
  horizontal_rule_ms: 600

  # Silence inserted between intra-sentence clauses split at commas,
  # semicolons, and colons (milliseconds). The punctuation mark stays
  # attached to the end of each clause so the neural voice intones it as a
  # continuation rather than a full stop.
  #   150  — default; adds a natural breath after commas
  #   0    — disable clause splitting entirely (one chunk per sentence)
  #
  # Note: clause splitting creates more, smaller chunks. With engine: kokoro
  # this is fine (fast synthesis). With engine: say, each extra chunk adds
  # ~1.6 s of render startup, so keep comma_ms modest or set it to 0 when
  # using say.
  comma_ms: 150

# ---------------------------------------------------------------------------
# Code blocks
# ---------------------------------------------------------------------------
code_blocks:
  # How to handle fenced code blocks.
  #   skip         — announce the block ("code block, N lines") but do not read it
  #   read         — read the code verbatim
  #   silent-skip  — silently skip without any announcement
  mode: skip

  # Spoken text when mode is `skip`. {lines} is replaced with the line count.
  announce_template: "code block, {lines} lines"

# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------
clean:
  # Hard-wrap repair: rejoin lines broken by terminal column limits.
  #   smart  — rejoin only when the line looks hard-wrapped (heuristic)
  #   always — always rejoin consecutive non-blank lines
  #   never  — leave line breaks as-is
  rejoin: smart

  # How to speak URLs.
  #   domain  — say only the domain (e.g. "github.com")
  #   full    — read the full URL
  #   skip    — silence URLs entirely
  urls: domain

  # How to speak filesystem paths.
  #   basename — say only the last component (e.g. "config.yaml")
  #   full     — read the full path
  #   skip     — silence paths entirely
  paths: basename

  # How to handle emoji.
  #   skip  — remove emoji silently
  #   name  — speak the emoji's Unicode name (e.g. "thumbs up")
  emoji: skip

# ---------------------------------------------------------------------------
# Mute rules
# ---------------------------------------------------------------------------
# Suppress text that clutters reads (TUI chrome, UI labels, boilerplate).
# Rules are case-sensitive and apply to both selection and window reads.
#
# Rule grammar:
#   plain string        — excised wherever it appears in the line
#   re:<regex>          — Python regex, excised per line (^ and $ anchor to a line)
#   drop-line:<str>     — drop the whole line if it contains <str>
#   drop-line:re:<rx>   — drop the whole line if the regex matches it
#
# blocks entries match a group's START line (plain string or re:<rx>; no
# drop-line: prefix here) and drop that line plus every following line UNTIL
# the next blank line. The blank line itself is preserved. Use blocks for
# multi-line groups that share no per-line marker (e.g. a tool-call header
# followed by indented result lines).
#
# To find an app's name for mute.by_app, check the `app=` field in
# ~/.local/state/readaloud/hammerspoon.log.
mute:
  global: []   # rules applied to every app
  by_app: {}   # per-app rules; key = app name from hammerspoon.log
  blocks: []   # block-drop rules (start-line match → drop until next blank)

# ---------------------------------------------------------------------------
# Spoken substitutions
# ---------------------------------------------------------------------------
# Map literal strings in the source text to how they should be read aloud.
# Applied after mute rules, before any other line processing.
#
# - Literal substring replacement (not regex); case-sensitive.
# - Longest key wins: keys are applied longest-first so a longer pattern
#   (e.g. "->") is never shadowed by a shorter one it contains (e.g. "-").
# - Token padding: each replacement is automatically surrounded by spaces so
#   a glyph adjacent to text (e.g. "A→B") reads as "A to B" not "AtoB".
#   Redundant spaces are collapsed; newlines are never touched.
# - Empty map (the default) = feature off, zero overhead.
#
# Example:
#   replace:
#     "→": to
#     "≈": approximately
#     "w/": with
#     "e.g.": for example
replace: {}

# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------
playback:
  # On resume after a pause, replay the last N milliseconds of already-played
  # audio to restore context before continuing. Set to 0 to disable.
  resume_rewind_ms: 600

# ---------------------------------------------------------------------------
# Window read
# ---------------------------------------------------------------------------
window_read:
  # Maximum characters the window-read AX walk will capture. Caps very long
  # documents so the pipeline stays responsive.
  max_chars: 20000

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
limits:
  # Guard against accidental ⌘A → read-all. Selections longer than this are
  # silently truncated.
  max_selection_chars: 60000
```

### Per-feature notes

#### `engine`

`say` (default) calls macOS `/usr/bin/say` — zero latency and no model files
required. `kokoro` uses local neural TTS, synthesizing chunk-by-chunk in a
background thread; it starts playback after the first chunk and runs fully
offline.

#### `hotkeys`

The toggle hotkey (`ctrl+alt+cmd+S` by default) cycles through three states:
**start → pause → resume**. It never stops a read outright. To **stop**,
click the top-center transport pill — its left zone is play/pause and its
right zone is stop. Hotkey changes need a Hammerspoon reload.

#### `voice`

`say_voice: system` (the default) omits the `-v` flag from `say`, so it
inherits whatever voice is set in **System Settings → Accessibility → Read &
Speak → System Voice**. If that is a Siri voice, readaloud speaks in it —
this is the only way to reach Siri voices from a script.

To use a specific named voice, download it first (System Settings →
Accessibility → Read & Speak → Manage Voices), then set:

```yaml
voice:
  say_voice: "Zoe (Premium)"
```

For kokoro, `kokoro_voice` picks the voice bundle and `speed` is a playback
multiplier (`1.0` = natural rate).

#### `alerts`

The transport/status pill appears at the top center of the screen. `y_pct`
controls its vertical position as a percentage of screen height (default `3.5`
puts it near the top edge). `duration_s` is how long a transient alert like
"stopped" lingers before fading.

#### `headers`, `pauses`, `code_blocks`

These control prosody. Headers are read at `base_wpm * rate_factor` with
configurable silence before and after. Lines in ALL CAPS are optionally
treated as pseudo-headers. Paragraph, list-item, and horizontal-rule pauses
add natural breathing room. Code blocks can be announced (`skip`), read
verbatim (`read`), or silently skipped (`silent-skip`).

#### `clean`

`rejoin: smart` repairs hard-wrapped terminal lines (lines broken by column
limits). `urls` and `paths` control how URLs and filesystem paths are spoken:
`domain` / `basename` (default) keep reads concise; `full` reads everything;
`skip` silences them. `emoji: skip` removes emoji silently; `name` speaks
their Unicode names.

#### `mute`

Suppress noisy TUI chrome. Rules apply to both selection and window reads and
are case-sensitive. See the rule grammar in the annotated example above.

Proven Claude Code config example:

```yaml
mute:
  global:
    - "drop-line:ctrl+o to expand"   # excise a specific UI label globally
    - "re:^\\s*✻"                    # drop spinner/status glyphs

  blocks:
    - "re:^⏺ \\w+\\("               # Claude Code tool-call + its ⎿ result body
    - "re:^\\s*⎿"                    # orphaned result continuation lines

  by_app:
    "Claude Code":
      - "re:^\\s*⏺\\s*"             # strip leading ⏺ bullet from tool lines
      - "drop-line:ctrl+o to expand"
    Arc:
      - "Skip to content"
```

To find an app's name, check the `app=` field in
`~/.local/state/readaloud/hammerspoon.log`.

#### `replace`

Map literal strings to how they should be read aloud. Useful for symbols and
abbreviations that a TTS engine would mispronounce:

```yaml
replace:
  "→": to
  "≈": approximately
  "w/": with
  "e.g.": for example
```

Substitution is **literal** (not regex) and **case-sensitive**. When two keys
overlap (e.g. `"->"` and `"-"`), the longer key wins because keys are applied
longest-first. Each replacement is padded with spaces automatically, so `A→B`
becomes `A to B` rather than `AtoB`; excess spaces are collapsed. Newlines are
never affected. The empty default (`{}`) means the feature is off.

#### `playback`

`resume_rewind_ms: 600` replays the last 600 ms of audio when you resume
after a pause, giving re-entry context. Set to `0` to disable.

#### `window_read` and `limits`

`window_read.max_chars` caps how many characters the accessibility walk
captures for a window read. `limits.max_selection_chars` guards against
accidental ⌘A → read-all by silently truncating selections beyond the limit.

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
