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
paste** is DONE. Everything below is v1.

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

### 3.1 Edit-as-you-go — *the headline feature*
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

### 3.2 Move + snap the HUD
Drag the panel; snap it to screen regions (corners / thirds / center). Persist the
chosen spot. (NSPanel is already movable-by-background-drag-able with a little work;
add snap zones.) Pure UX win.

### 3.3 Voice commands
- **newline-by-voice:** say "new line" → insert a line break. (Apple results don't
  emit newlines; intercept the literal token and convert.)
- Likely a small command vocabulary later ("scratch that", "new paragraph", etc.).

### 3.4 Replacement dictionary + emoji
Mirror readaloud's `replace` map / Wispr's dictionary. Example: "cool beans" → 🆒🫘.
General emoji support. A simple user-editable map applied to the committed text before
paste (and shown in the HUD). Pairs naturally with a config file (3.6).

### 3.5 Display mode — revisit committed-only vs two-tone
Current: committed bright + volatile dim (two-tone, like `apple_live`). Marco earlier
wanted committed-only (that was under the parakeet shadow, so re-judge it fresh). Try
both by feel. Apple's volatile self-corrects nicely, so two-tone is probably fine —
but confirm it doesn't distract while editing (3.1).

### 3.6 Config externalization
Everything is hardcoded in `main.swift` right now: hotkey, locale, HUD size/position/
alpha/font, linger. Decide whether to externalize to a YAML/JSON the app reads (the
original readaloud philosophy: "every key a future GUI control"). Low urgency; nice for
tuning without recompiling.

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

---

## 6. Quick start for tomorrow-you

```
cd ~/lit/playground/cust-stt
make mac-run        # build + launch; ctrl+alt+S to dictate, again to paste
make mac-debug      # same but foreground logs if something's off
```
If it misbehaves: check `RegisterEventHotKey status=0`, confirm no other app owns the
hotkey, and `make mac-reset` if the paste stops working after a rebuild.
