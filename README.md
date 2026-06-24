# Velovox

**Two on-device voice tools for macOS, in one menu-bar app.** Everything runs on
Apple's built-in speech stacks — no cloud, no network, no account.

- 🔊 **Read Aloud** — `⌃⌥⌘R` — select any text, hit the hotkey, and it's read
  aloud in a natural voice. *("speak" — you hear it.)*
- ⌨️ **Dictate** — `⌃⌥S` — hit the hotkey, talk, and your words are pasted at the
  cursor with a live floating HUD. *("write" — you write it.)*

Velovox lives in your menu bar (the waveform icon). Both hotkeys are always live;
you can toggle either one from the menu.

> Built and tested on macOS 26 (Apple Silicon). Dictation uses Apple's newer
> on-device `SpeechTranscriber` / `DictationTranscriber`, so it needs macOS 26+.

---

## Install

There's no notarized download yet, so you build it from source (one command) and
run it once past Gatekeeper.

```sh
git clone git@github.com:Marqu1nho/mah-velovox.git
cd mah-velovox
make build         # compiles Velovox.app with Xcode's Swift toolchain
make launch        # launches it (menu-bar icon appears)
```

### First launch (Gatekeeper)

Velovox is **ad-hoc signed** (no paid Apple Developer account), so macOS may warn
that it's from an unidentified developer. Since you built it yourself, that's fine:

- Right-click `Velovox.app` → **Open** → **Open** in the dialog, **or**
- `xattr -dr com.apple.quarantine Velovox.app`

### Permissions

macOS will prompt for two privacy permissions the first time you use a feature —
grant both in **System Settings → Privacy & Security**:

| Permission        | Why                                                        |
| ----------------- | ---------------------------------------------------------- |
| **Accessibility** | Read the current selection (Read Aloud) and paste (Dictate)|
| **Microphone**    | Capture your voice for dictation                           |

> If paste or selection-capture silently stops working after a rebuild, run
> `make reset` — re-signing can invalidate the grants, and `reset` clears them so
> you can re-grant cleanly.

---

## Commands

| Command        | What it does                                             |
| -------------- | -------------------------------------------------------- |
| `make launch`  | Launch the current build, no rebuild *(daily driver)*    |
| `make rebuild` | Recompile + relaunch (after changing code)               |
| `make debug`   | Recompile + run in the foreground with live logs         |
| `make build`   | Compile + bundle + sign only (don't launch)              |
| `make stop`    | Quit it                                                  |
| `make reset`   | Reset Mic + Accessibility grants + rebuild               |
| `make stats`   | Dictation WPM stats (7-day / last-50 / all-time)         |

---

## Configuration

One file: **`~/.config/velovox/config.json`**, written with full defaults on first
run. It has two sections — `readAloud` and `speakWrite`. A committed
[`config.example.json`](config.example.json) mirrors every knob.

> Config is read **at launch**, so changes need a relaunch (`make launch`).
> The menu-bar **Edit Config…** item opens the file for you.

### Dictation (`speakWrite`)

#### Speech engine — `"engine"`

Apple has two on-device transcribers; pick one:

| `engine`      | Behaviour                                                                 |
| ------------- | ------------------------------------------------------------------------- |
| `"speech"`    | `SpeechTranscriber` — always auto-punctuated and auto-capitalized.        |
| `"dictation"` | `DictationTranscriber` — punctuation & emoji are **opt-in** (see below).  |

With `"speech"` a thinking pause often finalizes the segment with a period (it has
no toggle for that). With `"dictation"` and `dictation.punctuation: false`, the
engine **never** auto-inserts periods/commas and stays mostly lowercase, so a
pause never ends your sentence — you add punctuation yourself (speak it, or via
`replacements`). `dictation.emoji: true` turns spoken emoji names into emoji
("fire" → 🔥). `dictation.mode` is `"formal"` (engine casing untouched) or
`"casual"` (lowercases the first word of each sentence, except `capitalExceptions`).

#### Replacements — `"replacements"`

A spoken-phrase → inserted-text map, applied most-specific-first. An **array**
(order matters, and `\n` works as a normal JSON escape):

```json
"replacements": [
  { "say": "new paragraph", "insert": "\n\n" },
  { "say": "new line",      "insert": "\n" },
  { "say": "cool beans",    "insert": "🆒🫘" }
]
```

#### Display & cues

- `displayMode` — `"hud"` (editable floating transcript), `"orb"` (minimal ambient
  blob), or `"off"`.
- `hud.commitOnly` — `false` shows live gray "volatile" text as you speak (feels
  fast); `true` shows only finalized words.
- `cue` — start/stop chimes (`start`/`stop` are macOS system-sound names like
  `Tink`, `Pop`, `Glass`; empty = silent) and `bloom` (orb breathes once on start).
- `metrics` — smart WPM tracking. Measures *speaking* time (silence beyond
  `silenceGraceSeconds` is excluded as thinking), logs each session to
  `~/.config/velovox/metrics.jsonl`, and `flash`es a "142 wpm" toast on stop.
- `audio.warnBluetoothInput` — one-time nudge when your mic is a Bluetooth headset
  (forced into low-quality call mode), suggesting a wired/built-in mic.

### Read Aloud (`readAloud`)

- `voice` — an `AVSpeechSynthesis` voice. A bare name like `"Ava"` or a full id
  like `"com.apple.voice.premium.en-GB.Serena"`. Premium/Enhanced voices download
  in **System Settings → Accessibility → Spoken Content → System Voice → Manage**.
- `rate` — speech rate, `0`–`1` (default `0.5`).
- `pauses` / `headers` — pacing in milliseconds around paragraphs, list items,
  commas, and headers (headers can read slower via `rate_factor`).
- `clean` — how URLs/paths/emoji are spoken (e.g. `urls: "domain"` reads only the
  domain; `paths: "basename"` reads only the filename) and `split_identifiers`
  speaks `camelCase`/`snake_case` as words.
- `code_blocks` — `"skip"` (announce "code block, N lines") or read them.
- `replace` — literal substring → spoken form (e.g. `"→": "to"`), longest-match
  first, auto-padded with spaces.
- `mute` — suppress clutter (TUI chrome, boilerplate). Per-line rules under
  `global`, per-app rules under `by_app`, and multi-line `blocks`:
  - `"plain string"` — excise wherever it appears in a line
  - `"re:<regex>"` — regex, per line (`^`/`$` anchor to a line)
  - `"drop-line:<str>"` / `"drop-line:re:<rx>"` — drop the whole matching line
  - `blocks` entries match a group's **start** line and drop it plus following
    lines until the next blank line.

### Hotkeys

Both `readAloud.hotkey` and `speakWrite.hotkey` accept specs like `"ctrl+alt+s"`,
`"cmd+shift+space"`, or `` "ctrl+opt+`" ``. Defaults: `ctrl+alt+cmd+r` (read),
`ctrl+alt+s` (dictate).

---

## How it works

Velovox is a single resident `LSUIElement` app (menu bar only, no Dock icon). One
shared Carbon hotkey handler routes each global hotkey to the right tool by id, so
the two coexist in one process. Read Aloud captures the selection via the
Accessibility API and speaks it with `AVSpeechSynthesizer`; Dictate streams mic
audio through Apple's on-device transcriber into a floating HUD, then pastes the
(optionally hand-edited) result at your cursor and restores your clipboard.

## Project layout

```
Velovox/          Swift sources (compiled as one module)
  main.swift      entry point, menu bar, --script / --stats CLI modes
  Config.swift    VelovoxConfig + readAloud/speakWrite sections (+ migration)
  HotKeys.swift   shared global-hotkey manager (routes by id)
  ReadAloud.swift / SpeakWrite.swift / RawVoice.swift   the two tools
  Capture, Clean, Parse, Pipeline, Regex, Script, Speaker, Transport  (Read Aloud)
build.sh          compile → bundle → ad-hoc sign Velovox.app
config.example.json
```

## Non-goals

No cloud transcription, no telemetry, no background network. On-device only.
