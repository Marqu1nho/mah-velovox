# speakwrite — anchor-first dictation

*Plan doc v0.1 — a living document, iterate freely. Working name `speakwrite`; naming open.*

## 1. The problem

Dictation tools transcribe speech; none of them help you **compose** speech. When we
write, the text on screen is the anchor of thought — working memory only has to hold
the next clause, because everything already said is parked in front of our eyes. In
conversation, the listener's face plays that role. Dictating into a void has neither,
so the speaker's working memory does double duty: composing forward while rehearsing
backward. The result is the familiar mid-sentence stall — *"what the hell was I just
saying?"* — and time lost to restarts and abandoned clauses.

Marco's framing (verbatim intent, from the session that spawned this doc):

> When we write, our anchor point of our thought is what we're seeing when we are
> writing. The anchor in conversation is something like je ne sais quoi in another
> human's face — and that's just not there [when dictating]. Having this anchor for
> the things that I have said, in combination with what I plan to say, I can
> formulate this run-time plan for my next things that I'm going to say.

A friend's review of the idea adds the cognitive-science framing and sharpens the
diagnosis: writing works as **external memory** — the words on screen hold your
thought-so-far so working memory is free to plan the next clause. Conversation
replaces that with the listener's live feedback channel (face, nods, "mm-hm"s).
Dictating into a blank input box is the **worst of both worlds** — "you're producing
speech (which evolved expecting a listener) while staring at a void (which gives you
nothing back)." That's why dictation feels weirdly harder than talking to a person,
even though it's the same motor act.

So: the deliverable is not transcription. It is **an external memory surface for
speech composition** that happens to also paste the transcript when you're done.
If it works, this isn't Wispr minus the 40% we don't want — it's a different tool
that happens to share a microphone.

## 2. Design thesis

1. **The display is the product.** Commercial tools (Wispr Flow et al.) treat the
   live transcript as incidental chrome — they optimize the transcript as a
   *deliverable*. Here it is a *thinking surface*: the always-on-top HUD is the
   core feature; the paste-at-cursor is the byproduct. This makes the anchor a
   **latency-and-UI problem, not a model-quality problem** — exactly the kind of
   thing a custom build does better than a commercial product.
2. **Verbatim is load-bearing.** Post-processing that rewrites your words breaks the
   anchor — what's on screen stops being a faithful trace of what you thought. LLM
   "polish" also changes meaning and formalizes tone (observed friction with Wispr).
   Default to (near-)verbatim; make cleanup a knob, not a default.
3. **Streaming partials are a hard requirement.** An anchor with 2-second lag is a
   broken anchor. Engine choice is driven by partial-result latency, not by batch
   accuracy benchmarks.
4. **Same philosophy as readaloud:** one hotkey gesture, one YAML config, Hammerspoon
   glue, local-first engines behind a thin interface, GUI bolt-on possible later.

## 3. The Anchor HUD

The centerpiece. An always-on-top, non-focusable, click-through panel
(`hs.canvas`) that appears when dictation starts.

**Content model — three visual states of text:**

| state | meaning | treatment |
|---|---|---|
| volatile | engine's provisional partial (may still change) | dim amber, italic |
| committed | engine finalized this span | bright ink |
| aging | committed text older than the last ~2 sentences | progressively fades toward background |

The fade is the point: the eye lands on the bright tail — *exactly where your thought
is* — while older context stays legible but recedes, mirroring how the anchor works
when writing. Fade by recency (sentence count), not by wall-clock, so a slow speaker
isn't punished.

**Behavior:**

- Appears within ~100 ms of the dictation hotkey (before the engine is even warm —
  show a listening indicator immediately; perceived responsiveness is half the anchor).
- Rolling window: last N lines (config), newest at the bottom; older text scrolls
  away rather than shrinking the font.
- **Re-anchor cue:** if the speaker goes silent for more than ~3 s mid-dictation,
  gently pulse/highlight the last committed sentence — the system's answer to
  "what was I saying," offered exactly when the stall happens.
- On stop: text pastes at cursor; HUD lingers for a configurable beat (default ~1.5 s)
  then fades — long enough to confirm what landed, short enough to stay out of the way.
- Position/size/font/opacity all in YAML. Default: lower third of the focused screen,
  centered — near the eye line, away from where most text cursors live.
- Never steals focus, never captures clicks. The HUD is glass, not a window.

## 4. The plan lane (v2 concept — the "runtime plan")

The second half of Marco's insight: the anchor is "what I've said **in combination
with what I plan to say**." A thin pinned lane above the transcript holding an
outline — bullets you pre-type (or paste) before a long dictation, staying visible
while you speak. What's said anchors backward; the lane anchors forward.

Possible later: advance the outline highlight as bullets get covered (fuzzy match),
a "scratch that" voice command that strikes through the last sentence rather than
deleting it (keep the trace honest), per-app lanes. **None of this in v1** — but the
HUD layout should reserve the top strip so the lane can be added without redesign.

## 5. Architecture sketch

```
hold/toggle hotkey (mouse-mappable, same philosophy as readaloud)
        │
 ┌──────▼───────┐  shows/feeds the Anchor HUD (hs.canvas),
 │  Hammerspoon │  starts/stops capture, receives streaming
 │  speakwrite  │  partials, pastes final text at cursor
 │     .lua     │  (clipboard set + ⌘V + restore — readaloud's
 └──────┬───────┘  capture machinery, reversed)
        │ unix socket / stdout stream (JSON lines: {text, volatile})
 ┌──────▼───────┐
 │  speakwrite  │  daemon: mic capture + streaming ASR
 │    daemon    │  engines: parakeet | apple | whisper
 └──────────────┘
```

Key differences from readaloud:

- **A daemon, not a per-invocation CLI.** ASR models take seconds to load; the
  anchor needs first-partial latency in the hundreds of milliseconds. The model
  stays resident (launchd agent or socket-activated), Hammerspoon talks to it over
  a unix socket. This is the architectural fork readaloud didn't need.
- **Engines** (same three-option hedge readaloud uses for voices):
  - `parakeet` — parakeet-mlx, chunked ~0.5 s windows, pseudo-streaming. Likely v1
    default: pure Python/MLX, no shim, excellent speed/accuracy on Apple Silicon.
  - `apple` — macOS 26 SpeechAnalyzer/SpeechTranscriber via a small Swift shim
    emitting JSON lines. True volatile partials (purpose-built for live captioning),
    zero model download; the shim is the cost. Strong v1.5 candidate.
  - `whisper` — faster-whisper batch fallback. No real streaming; exists for
    accuracy comparisons and as a safety net.
- **Polish dial** (`polish: none | punctuation | light | full`): none =
  court-reporter raw; **punctuation (default)** = restore sentence boundaries and
  commas only — readable without changing a single word choice (likely Marco's
  setting); light = also strip disfluencies ("um", "uh", stutter repeats),
  deterministic rules only, no LLM; full = optional LLM pass for Wispr-style
  smoothing. The HUD always shows the pre-polish trace — the anchor stays
  faithful; polish applies only to the paste.
- **Push-to-talk vs toggle** both supported; push-to-talk (hold the mouse key,
  release to paste) is the likely winner for the one-gesture flow.

## 6. Config sketch (the contract, readaloud-style)

```yaml
engine: parakeet            # parakeet | apple | whisper

hotkeys:
  dictate: ["ctrl", "alt", "cmd", "D"]
  mode: push_to_talk        # push_to_talk | toggle

hud:
  show: true
  position: bottom-center   # bottom-center | top-center | mouse | {x,y}
  width_pct: 50
  lines: 4
  font_size: 20
  opacity: 0.92
  fade_after_sentences: 2   # committed text older than this starts to fade
  reanchor_pulse_after_s: 3 # silence gap that triggers the last-sentence pulse
  linger_ms: 1500           # HUD visible after paste

polish: punctuation         # none | punctuation | light | full

inject:
  method: paste             # paste (clipboard+cmd-v, restore) | type (keystrokes)
  trailing_space: true

plan_lane:                  # v2 — reserved
  enabled: false
```

## 7. Latency budget

| moment | target | why |
|---|---|---|
| hotkey ↓ → HUD visible + listening | < 100 ms | the gesture must feel armed |
| speech → first volatile partial on HUD | < 300 ms (apple) / < 700 ms (parakeet) | anchor credibility |
| partial → committed | < 1.5 s | fades/styling depend on commitment |
| hotkey ↑ → text pasted at cursor | < 500 ms | end of gesture = text exists |

If a budget can't be met, degrade visibly (show the listening indicator and a
"warming up" state), never silently.

## 8. The experiment — does the anchor work?

The whole premise is a hypothesis about Marco's brain. Test it cheaply before
polishing anything:

- **v0 spike first** (a day, not a week): engine → HUD text with crude styling →
  paste on release. No fades, no pulse.
- Measure across a week of real use, alternating days with Wispr Flow:
  - restarts/abandoned clauses per 1,000 words (countable in the transcripts),
  - subjective stall frequency ("lost my thread" moments, self-reported),
  - words per session and whether long-form dictation becomes more common.
- Decision gate: if the crude anchor doesn't reduce stalls, fades and pulses won't
  save it — revisit the premise (maybe the anchor needs to be the *plan lane*, not
  the trace) before investing in v1 polish.

An honest caveat to go in clear-eyed (friend's review): there's a reason
live-caption feedback isn't universal — for some people, reading while composing
speech competes for the same attention and trips them up worse than the void does.
The typing analogy suggests it will help here, but treat every HUD behavior as an
experiment knob (HUD on/off, lines of history, fade speed), not a guaranteed win.
One related design rule is already baked into §3: streaming partials that flicker
and rewrite themselves are an *anti-anchor* — they grab attention instead of
grounding it. The two-tone volatile/committed display exists precisely to prevent
that failure mode.

## 9. Milestones

1. **v0 spike** — parakeet-mlx mic loop printing JSON-line partials; lua HUD with
   monochrome text; push-to-talk; paste-with-clipboard-restore. Prove the loop.
2. **v1** — daemon + socket, volatile/committed styling, fades, re-anchor pulse,
   light cleanup, YAML contract above, install.sh, README.
3. **v1.5** — SpeechAnalyzer Swift shim as the `apple` engine; A/B accuracy + latency
   against parakeet.
4. **v2** — plan lane, "scratch that", per-app overrides, personal dictionary
   (learned proper-noun hinting), the GUI the YAML was designed for.

## 10. Open questions

- Mic permission lands on whichever process opens the mic (the daemon) — confirm the
  TCC prompt UX when launched via Hammerspoon vs launchd.
- HUD on the focused screen vs the screen with the text cursor (they can differ).
- Should the HUD trace persist anywhere after paste (session scrollback file) or is
  persistence the job of the pasted text? Leaning: optional session log, off by default.
- Does push-to-talk via a mouse-mapped key repeat-fire keydown events, and does the
  mouse software send distinct down/up? Affects `push_to_talk` feasibility per device.
- Repo: sibling package in this repo (shared Hammerspoon patterns, one install.sh)
  vs separate repo. Leaning: same repo, `speakwrite/` package next to `readaloud/`.

## Relationship to readaloud

Same gesture philosophy (one hotkey, mouse-mappable), same config philosophy (one
YAML, every key a future GUI control), same glue (Hammerspoon), shared clipboard
save/restore machinery — readaloud copies *out*, speakwrite pastes *in*. Together
they make the terminal-centric workflow bidirectional: the machine reads to you,
you speak to the machine, and in both directions the text stays the anchor.
