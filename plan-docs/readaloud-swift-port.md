# ReadAloud → native Swift port — handoff & plan

The plan doc that didn't exist. This is the spec for porting `readaloud` (Python
daemon + Hammerspoon + Lua) to a single native macOS Swift app, mirroring the
SpeakWrite build (`mac/main.swift`).

## Why / the locked decisions

- **Goal:** one resident Swift app replaces the Python daemon + Hammerspoon + Lua.
  The app *is* the warm process → instant start. Bundle + sell.
- **Engine:** Apple `AVSpeechSynthesizer`, voice = **Zoe (Premium)**
  (`com.apple.voice.premium.en-US.Zoe`, 513 MB, quality tier 3).
  - Verified: cold-process time-to-first-word **0.022s**; ~11s for the test
    paragraph at rate 0.62. Warm inside a resident app → effectively instant.
  - **Hard finding:** the Siri voice (`com.apple.siri.natural.Simone`) the Python
    version used via the `say` loophole is **NOT reachable** from
    `AVSpeechSynthesizer` (returns `nil` by identifier). Marco A/B'd Zoe Premium by
    ear and accepted it. Premium/enhanced voices must be downloaded in System
    Settings → Accessibility → Spoken Content → Manage Voices before the Swift API
    can see them.
- **Transport:** simple `pauseSpeaking(at:)` / `continueSpeaking()` — resumes
  exactly where it left off. **No 1-second rewind** (dropped for v1; it was the
  only piece that wasn't a one-liner). Same hotkey toggles pause; click-to-stop.
- **Config:** `~/.config/readaloud/config.json` (JSON, sits beside the old
  `config.yaml`). Per repo CLAUDE.md: every knob is WRITTEN to the live file on
  first run, not just defaulted in code. `voice` + `rate` are the ear-tuning knobs.
- **Visual cue:** the top-screen transport pill (clickable ⏸/▶ + ⏹), ported from
  the Lua. The RawVoice orb is **out of scope** for v1 (AVSpeechSynthesizer gives
  no easy real-time level).

## Architecture

```
hotkey (Carbon) ──► capture selection (synthetic ⌘C → clipboard → restore → AX fallback)
                         │
                         ▼
              text pipeline (clean → parse → script)  [Swift port of the Python]
                         │  list of Chunks
                         ▼
        chunks → [AVSpeechUtterance] (per-chunk rate + pre/postUtteranceDelay)
                         │  enqueued on one AVSpeechSynthesizer
                         ▼
              audio out  +  transport pill (pause/stop)
```

**Key mapping — pauses without custom audio:** each `Chunk` becomes one
`AVSpeechUtterance`:
- `utterance.rate` = configured `rate` × chunk `rate_factor` (headers slower)
- `utterance.preUtteranceDelay` = `pause_before_ms / 1000`
- `utterance.postUtteranceDelay` = `pause_after_ms / 1000`
- `utterance.voice` = configured voice

Enqueue all on one synthesizer; it plays sequentially with the gaps. `pauseSpeaking`
/`continueSpeaking` span the queue; `stopSpeaking(.immediate)` is instant because
utterances are small (≤500 chars, enforced by the splitter). Pause/resume naturally
lands on chunk boundaries.

## File layout (in `mac/ReadAloud/`, separate binary from SpeakWrite)

| File | Contents |
| --- | --- |
| `main.swift` | AppDelegate, Carbon hotkey (reuse SpeakWrite's parseHotKey), Controller state machine |
| `Config.swift` | Codable `Config` → `~/.config/readaloud/config.json`, fallback-on-first-run |
| `Capture.swift` | selection capture: synthetic ⌘C via CGEvent → poll clipboard → restore → AX fallback |
| `Clean.swift` | port of `clean.py` (ANSI strip, mute global/by_app/blocks, replace, urls/paths/emoji, rejoin) |
| `Parse.swift` | port of `parse.py` (paragraphs/headers/lists/code/tables/hr, inline-md strip, all-caps headers) |
| `Script.swift` | port of `script.py` (sentence/clause split, pauses, code-block modes, table rows) → `[Chunk]` |
| `Speaker.swift` | `AVSpeechSynthesizer` wrapper: chunks→utterances, play/pause/stop, delegate |
| `Transport.swift` | top-screen clickable pill (⏸/▶ + ⏹), positioned at `y_pct`, 0.2s fade |
| `build.sh` | `swiftc -O *.swift`, Info.plist (`LSUIElement`, bundle id `com.marco.readaloud`), ad-hoc sign |

Makefile targets — verb says whether it recompiles: `read` (launch existing build,
NO rebuild — daily driver), `read-rebuild` (recompile + launch), `read-debug`
(recompile + foreground logs), `read-build` (compile only), `read-stop`, `read-reset`.
SpeakWrite mirrors this as `speak` / `speak-rebuild` / … (renamed from the old
`mac-*` / `read-mac-*` start/run names, which were confusingly synonymous).

## Phasing (audio-first, test by ear at each gate)

- **Phase 1 — walking skeleton — ✅ DONE (2026-06-22).** App shell + Carbon hotkey +
  selection capture (⌘C/clipboard/AX) + speak selection via AVSpeechSynthesizer +
  transport pill (pause/stop) + config.json. Voice switched Zoe → **Serena (Premium)**
  by ear. `nonisolated(unsafe)` on the synth silences the Sendable lint.
- **Phase 2 — text-pipeline fidelity — ✅ DONE (2026-06-22).** Faithful Swift port of
  clean → parse → script (Clean/Parse/Script/Pipeline/Regex.swift). Every knob wired
  AND written to the live config.json (headers/pauses/code_blocks/clean/mute/replace).
  **Verified byte-identical to `python -m readaloud script`** via the hidden
  `ReadAloud --script` mode (diff on a feature sample AND a Claude-Code TUI sample —
  mute + replace parity confirmed). Voice spec now also accepts a friendly name
  ("Serena") not just the full identifier.
- **Phase 2.5 — identifier splitting — ✅ DONE (2026-06-22).** camelCase/snake_case
  split into spoken words (`kAudioDevicePropertyTransportType` → "k Audio Device
  Property Transport Type"), applied to all readable text, behind `clean.split_identifiers`
  (default on). Simple split-everything by choice — common words (iPhone, JavaScript)
  still sound right when split. (Was TODO (a).)
- **Phase 3 — polish (optional / next).** read-window (AX tree walk) mode, start/stop
  cue sounds, then packaging (Developer ID + notarization).
  - Hotkey config knob (was TODO (b)): already shipped — the `hotkey` key.

## Config schema (v1)

```json
{
  "voice": "com.apple.voice.premium.en-US.Zoe",
  "rate": 0.62,
  "hotkey": "ctrl+alt+cmd+s",
  "alerts": { "y_pct": 3.5, "duration_s": 1.2 },
  "headers": { "rate_factor": 0.85, "pause_before_ms": 500, "pause_after_ms": 400,
               "treat_all_caps_lines_as_headers": true },
  "pauses": { "paragraph_ms": 350, "list_item_ms": 200, "horizontal_rule_ms": 600, "comma_ms": 150 },
  "code_blocks": { "mode": "skip", "announce_template": "code block, {lines} lines" },
  "clean": { "rejoin": "smart", "urls": "domain", "paths": "basename", "emoji": "skip" },
  "mute": { "global": [], "by_app": {}, "blocks": [] },
  "replace": {},
  "limits": { "max_selection_chars": 60000 }
}
```
Defaults seeded from the live `~/.config/readaloud/config.yaml` where they differ
from the Python code defaults (e.g. `comma_ms`, paragraph pauses).
```
```
