# speakwrite v1 — plan & roadmap

*Living doc. Written 2026-06-20 night, end of the session that rebuilt speakwrite
as a native Swift app. This is the handoff for future-Marco. Companion to
`anchor-dictation.md` (the original premise/vision — still the north star).*

---

## 0. TL;DR — where we landed

We nuked the original stack and rebuilt speakwrite as a **native macOS Swift app**
(`mac/SpeakWrite.app`). It works and it's good:

> hold/press **ctrl+alt+S** → floating HUD shows your words **live** as you speak
> (Apple `SpeechTranscriber`, committed text bright + volatile tail dim, self-correcting)
> → press **ctrl+alt+S** again → it pastes the transcript at your cursor and
> restores your clipboard.

Quality is Wispr-class (verified on real dictation, ~word-perfect). v0 = **anchor +
paste** is DONE.

**Update 2026-06-21 — v1 batch 1 shipped & verified:**
- **Edit-as-you-go (§3.1)** — HUD is now an editable `NSTextView`; finalized speech
  appends at the end, you edit anything above freely, and **stop pastes the edited
  document** (not the raw transcript). Panel becomes key only on click
  (`becomesKeyOnlyIfNeeded`), then restores the prior app before the ⌘V.
- **Voice commands as a replacement dictionary (§3.3/§3.4)** — "new line"→`\n`,
  "new paragraph"→`\n\n`, "cool beans"→🆒🫘. Newline commands swallow any
  whitespace/punctuation hugging them, so no stray comma/period on the seam.
- **JSON config (§3.6)** — `~/.config/speakwrite/config.json`, decoded via `Codable`
  (zero deps). Holds `locale`, `hud` (alpha/fontSize/width/height), and
  `replacements` (an ORDERED array — order matters, most-specific first). Written
  with defaults on first run; malformed file logs the parse error and falls back
  to defaults instead of crashing.
- **Launch/paste bug root-caused** — see §1.6. `make mac-start` added as the
  daily driver.

Still open in v1: **§3.2 HUD move + snap + resize** (next), §3.5 display mode.

---

## 1. The journey (so you don't relitigate the dead ends)

The whole night was spent discovering, in order:

1. **parakeet streaming = garbage.** `transcribe_stream` produces word-salad at any
   `context_size`. Also: it only exposed `draft_tokens` (the volatile tail), which
   *collapses* as tokens finalize — that's the "pasted just a period" bug. The full
   transcript is `tr.result.text` (finalized + draft). Don't go back to parakeet
   streaming.
2. **parakeet batch = flawless but heavy** (~0.04× realtime, 2.3GB MLX model).
3. **Apple `SpeechTranscriber` (macOS 26) wins outright**: flawless, ~0.02× realtime,
   AND natively streaming (constant low latency, self-correcting volatile→final). No
   model to keep warm (OS-resident), no Python. This is the engine.
4. **The night's real villain:** every time "the app produced garbage," it was the
   **old Hammerspoon `speakwrite.lua` + parakeet daemon shadowing the ctrl+alt+S
   hotkey** — firing parakeet and pasting *that* while the native app sat deaf.
   The streaming-vs-batch flip-flopping was a phantom caused by this shadow. Apple
   streaming was always fine (`apple_live` probe proved it). **Lesson: if the app
   ever misbehaves, first check nothing else owns the hotkey.**
5. **Wispr's "secret"** is just: stream under the hood, only *display* finalized/
   committed text (hide the volatile churn). Validates our committed-display instinct.

6. **The OTHER villain (found 2026-06-21): `open` vs direct-binary launch.** Paste
   (synthetic ⌘V, needs Accessibility) works ONLY when the app is launched via the
   **inner binary directly** (`mac/SpeakWrite.app/Contents/MacOS/SpeakWrite`), NOT
   via `open SpeakWrite.app`. `open` (LaunchServices) presents a different identity
   to TCC that the Accessibility grant misses → paste silently fails AND a bogus
   re-grant prompt fires even though the toggle already shows ON. This — not a
   rebuild/re-sign as first assumed — was the "paste broke between yesterday and
   today" bug. Fix: `make mac-run` + `mac-start` now launch the direct binary
   detached. Permanent cure: the Xcode + Developer ID migration (stable signing
   identity → grant survives everything). **Lesson: if paste dies, check HOW it was
   launched before touching System Settings.**

Retired and deleted: the `speakwrite/` Python package, `hammerspoon/speakwrite.lua`,
the parakeet daemon, the 2.3GB model, `install.sh` §6. (`readaloud` untouched — it
still uses Hammerspoon + kokoro + sounddevice/soundfile; those deps were KEPT because
readaloud needs them.)

---

## 2. Architecture as it stands

Single Swift file, no Xcode project: `mac/main.swift` (~270 lines), built by
`mac/build.sh` into an ad-hoc-signed `SpeakWrite.app` (LSUIElement, no dock icon).

- **Hotkey:** Carbon `RegisterEventHotKey`, ctrl+alt+S (`kVK_ANSI_S` + control|option).
- **Mic:** `AVAudioEngine` input tap → `AVAudioConverter` to the analyzer's format.
- **Engine:** `SpeechTranscriber(locale: en-US, preset: .progressiveTranscription)`
  feeding `SpeechAnalyzer` via an `AsyncStream<AnalyzerInput>`; results loop appends
  finalized to `committed`, sets `volatile` to the live tail.
- **HUD:** non-activating borderless `NSPanel`, `.screenSaver` level, centered,
  `alphaValue = 0.82`, `NSVisualEffectView(.hudWindow)` bg, `NSScrollView` +
  `NSTextView` (read-only). Committed = white, volatile = white@0.45. **Pin-to-bottom
  only if already at bottom** (scroll up to re-read without being yanked down).
- **Paste:** `NSPasteboard` snapshot of ALL items/UTIs → set string → synthesized
  ⌘V via `CGEvent` → restore the snapshot after 0.15s (screenshot survives).

**Update 2026-06-21:** the HUD `NSTextView` is now `isEditable = true` (edit-as-you-go),
the panel is a `KeyablePanel` (`becomesKeyOnlyIfNeeded`), HUD knobs + locale + the
replacement dictionary come from `~/.config/speakwrite/config.json`, and all make
targets launch the **inner binary directly** (never `open` — §1.6).

**Build/run (all via make):**
```
make mac          # compile + bundle + ad-hoc sign
make mac-run      # rebuild + launch (no dock icon)
make mac-debug    # rebuild + run in foreground with logs
make mac-kill     # quit it
make mac-reset    # tccutil reset Mic+Accessibility + rebuild  (re-signing voids TCC)
```
**Permissions:** Microphone (prompt) + **Accessibility** (required for the ⌘V paste;
System Settings → Privacy → Accessibility). `SpeechAnalyzer` needs no speech-auth.
Re-signing on rebuild can invalidate grants → that's what `make mac-reset` is for.

**Reference probes** (gitignored, in repo root / scratchpad): `apple_probe` (batch),
`apple_live` (streaming) — handy for engine experiments without touching the app.

---

## 3. v1 roadmap (rough priority order)

### 3.1 Edit-as-you-go — *the headline feature* — ✅ DONE (2026-06-21)
*Shipped as described below. New finalized text appends at the end; the volatile tail
is found by its dim attribute each update (robust to your edits above it); stop pastes
the edited document. The panel takes key focus only when you click in.*

The "wildly satisfying" original dream. Now feasible because Apple's **finalized text
is stable** (parakeet revising under your cursor was what made this hostile before).
The HUD is already an `NSTextView` — v0 just keeps it `isEditable = false`.

Plan:
- Flip `isEditable = true`. The committed region is freely editable.
- New incoming speech must NOT clobber your edits or your cursor. Cleanest model:
  new finalized text appends at the **end** of the document; you edit anywhere above
  freely; the volatile tail renders after the last committed point.
- On stop, paste the **whole edited document** (not the raw transcript) at the cursor.
- Decide: while editing, does the volatile tail keep updating (could be distracting)?
  Maybe pause the live tail while the text view has an active text cursor/selection.

### 3.2 Move + snap + resize the HUD — ⏭️ NEXT
Drag the panel; snap it to screen regions (corners / thirds / center). Persist the
chosen spot. (NSPanel is already movable-by-background-drag-able with a little work;
add snap zones.) Pure UX win.

### 3.3 Voice commands — ✅ DONE as a dictionary (2026-06-21)
Marco's insight: "new line" is just a replacement-dictionary entry, not a special
case. Implemented exactly that — "new line"→`\n`, "new paragraph"→`\n\n`. Newline
entries also swallow whitespace/punctuation hugging the phrase (no stray comma/period
on the seam). Future: richer commands ("scratch that") may need more than a dictionary.

### 3.4 Replacement dictionary + emoji — ✅ DONE (2026-06-21)
Shipped: ordered `replacements` array in `config.json`, applied to each segment as it
streams (so the HUD shows the substitution live and the paste already contains it).
Newline-producing entries use a punctuation-eating pattern; text/emoji entries
(e.g. "cool beans"→🆒🫘) keep their surroundings intact. Order = file order.

### 3.5 Display mode — revisit committed-only vs two-tone
Current: committed bright + volatile dim (two-tone, like `apple_live`). Marco earlier
wanted committed-only (that was under the parakeet shadow, so re-judge it fresh). Try
both by feel. Apple's volatile self-corrects nicely, so two-tone is probably fine —
but confirm it doesn't distract while editing (3.1).

### 3.6 Config externalization — ✅ DONE as JSON (2026-06-21)
Chose **JSON over YAML**: Foundation ships a JSON parser (`Codable`), none for YAML, and
this is a single-file `swiftc` build with no package manager — so YAML would mean a
hand-rolled parser or adopting SPM. JSON gets us comment-free but native, type-safe,
zero-dependency config now; revisit if the Xcode migration makes SPM+Yams cheap.
Externalized so far: `locale`, `hud.{alpha,fontSize,width,height}`, `replacements`.
Not yet externalized: hotkey. ("Every key a future GUI control" still the north star.)

---

## 4. readaloud → native Swift? (explored, not decided)

We discussed converting readaloud to the same native modality. Verdict: **weaker case
than speakwrite, and it hinges entirely on the voice.**

- speakwrite's pivot was a slam-dunk because Apple's STT ≥ parakeet AND it killed a
  genuine bug graveyard. Neither fully applies to readaloud: it's **stable** (not
  fragile), and **kokoro is the whole reason it exists** beyond `say`.
- The one deciding question: **is Apple `AVSpeechSynthesizer` (Siri/Premium voices)
  good enough to your ear vs kokoro?**
  - **Yes** → clean full-native rewrite: drops Python *and* Hammerspoon, and pause/
    resume comes free (`pauseSpeaking`/`continueSpeaking`), killing the custom
    sounddevice frame-accurate playback. Reuses speakwrite's Swift building blocks.
  - **No (kokoro's voice is the point)** → native means keeping a Python kokoro engine
    (just swaps Hammerspoon for Swift glue) or porting kokoro to Swift via ONNX
    Runtime + porting the text pipeline. Real work for a consistency payoff.
- **Cheap way to decide:** a tiny Swift probe that speaks a paragraph in Apple's best
  voice, A/B against kokoro. Same move that settled the speakwrite engine question.
- Recommendation: don't do it for consistency alone. Only if the voice A/B says Apple
  is good enough.

---

## 5. Open questions / notes

- **Locale** hardcoded en-US. Fine for now.
- **Hotkey conflicts:** the app logs `RegisterEventHotKey status=` — nonzero means
  something else owns ctrl+alt+S (the lesson from §1.4). Keep that log.
- **Mic format:** records 48kHz/2ch; `SpeechAnalyzer` handles it, no mono conversion
  needed.
- **Single-instance guard:** consider preventing two SpeakWrite instances (both would
  fight for the hotkey). Not currently guarded.
- **Push-to-talk vs toggle:** currently toggle. Push-to-talk (hold to talk) was in the
  original vision — easy to add (keyDown start / keyUp stop) if wanted.
- **Distribution decision (2026-06-21):** target is **Developer ID + notarization
  (direct download)**, NOT the Mac App Store — MAS requires the App Sandbox, which
  forbids the synthetic-paste-into-other-apps core (Accessibility + cross-app input).
  Developer ID supports everything AND a stable signing identity makes the TCC grant
  survive rebuilds (kills §1.6 permanently). Needs the paid ($99/yr) Apple Developer
  Program, not the free tier. **Fast-follow:** migrate the single `main.swift` +
  `build.sh` into a real **Xcode project** (gives icon via asset catalog, entitlements,
  notarization, and SPM dependency integration). Decided to do this AFTER the current
  feature loop; SPM then makes pulling in deps (incl. Yams if we ever want YAML) cheap.

---

## 6. Quick start for tomorrow-you

```
cd ~/lit/playground/cust-stt
make mac-start      # DAILY DRIVER: launch the existing build (no rebuild → permissions intact)
make mac-run        # rebuild + launch (use when code changed)
make mac-debug      # rebuild + foreground logs if something's off
make mac-reset      # clear Mic+Accessibility + rebuild; then re-grant once (last resort)
```
Mental model: **`mac-start` = use it · `mac-run` = I changed code · `mac-reset` = paste
broke, start clean.** All three launch the **inner binary directly** (never `open` —
see §1.6). Config lives at `~/.config/speakwrite/config.json` (edit, restart to apply).
If paste dies: it's almost always the launch method (§1.6), not System Settings.
