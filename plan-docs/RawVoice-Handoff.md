# RawVoice — voice-capture indicator handoff

This document is the spec for `RawVoice.swift`. It describes what the component is, how it should behave, and — most importantly — how to size it across screens. Read it alongside the source; the source is the source of truth for exact numbers, this is the *why*.

## What it is

`RawVoice` is an ambient "your voice is being captured" indicator for a speech-to-text app. It is the **anchor-off mode**: the app's primary experience shows a live visual transcript that anchors what the user is saying, but mass-market users can turn that off. When they do, this orb becomes the *entire* confidence signal that the mic is live and listening. So the single most important quality is contrast between two states:

- **Listening (idle).** Quiet but awake. A slow breath, a barely-there ring. Never frozen.
- **Capturing (speaking).** Unmistakably alive the instant the user talks — the blob swells, the outer waveform ripples, and short white dashes continuously break past the outer ring.

The aesthetic is deliberately raw and minimal: thin white line work on a dark stage, nothing ornamental. (An earlier exploration layered Polynesian *tatau* motifs — shark-teeth, spearheads, chevrons — over this. Those were cut. Do not reintroduce them; the minimal version is the design.)

## Visual layers

Drawn back-to-front, all in white line on a dark stage:

1. **Faint dashed ring** — a single thin dashed circle that drifts slowly. Quiet structure.
2. **Breathing core blob** — a softly wobbling circle whose radius tracks voice amplitude with a fast-attack / slow-release envelope (snaps up on a syllable, eases back down). This is the heart of the "you're being heard" read.
3. **Outer waveform** — a circle whose perimeter ripples; nearly still when idle, agitated on peaks. This is the Siri-style "voice" wrapped into a ring.
4. **Flash-out dashes** — short radial dashes that spawn just inside the waveform ring, cross it, and fade out within a short reach. They emit **continuously the whole time the user is speaking** (not just on loud peaks), and their overall brightness scales with how loudly the user is talking, so they fade in and out smoothly at speech boundaries rather than popping. They must **die shortly after escaping the waveform ring** — reined in, not streaking off into the distance.

The dash field is computed deterministically from the current time, so there is no mutable particle state. Keep it that way; it's what lets the whole thing be a pure SwiftUI value-type view.

## Behavior / signal flow

`RawVoiceView(level:)` takes a `0...1` loudness value and draws. Everything animates off `TimelineView(.animation)` time plus that level. Two ways to supply the level:

- **Built-in mic tap** via `VoiceLevelMonitor` — installs an `AVAudioEngine` input tap, computes RMS, maps it through a dB floor to `0...1`, and smooths with attack/release. Call `start()` / `stop()` with the view's lifecycle.
- **Your existing audio pipeline** — if the ASR layer (e.g. Deepgram) already hands you audio frames, compute the same RMS there and either pass it straight to `RawVoiceView(level:)` or push it through `VoiceLevelMonitor.ingest(externalLevel:)` to reuse the smoothing. Prefer this over running a second input tap.

`SimulatedVoiceSource` produces a realistic burst-and-pause speech signal for previews and on-device tuning with no mic.

## Sizing — the important part

The question this answers: should the indicator be a different size on different screens, or one size everywhere?

**Decision: one fixed physical size on every screen. Do not scale to the display.**

Rationale:

- This is a recognizable ambient affordance, like the macOS dictation orb or the old Siri blob. Those stay a consistent size regardless of monitor, because the point is instant recognition and muscle memory. A user who moves between a laptop and an external display should see the same thing.
- Scaling with the screen makes the orb balloon and dominate on a large external monitor, and feels inconsistent across displays.

**Work in points, not pixels.** SwiftUI's `Canvas` maps points to physical pixels and handles Retina for you. A fixed point size renders at essentially the same physical size on the MacBook Air's Retina panel and on a 1× 1080p external display, and stays crisp on both. You never touch DPI yourself.

**Default footprint: ~220 pt** overall diameter (outer waveform ring + dash reach), with the breathing core blob roughly 0.28–0.38× the radius. For reference, 220 pt is about 14–16% of a MacBook Air's logical width (~1440×900 pt) and ~11% of a 1080p display (1920×1080 pt) — present and confident, but clearly not the main event.

Size by role:

| Role | Diameter |
| --- | --- |
| Centered "capturing your voice" moment (default) | ~220 pt |
| Dedicated full-screen dictation / listening mode | ~280–320 pt |
| Small persistent indicator next to a text field or in a corner | ~64–96 pt |

Two implementation rules that make this hold together:

- **Drive everything off the single `diameter` value.** Ring offsets, waveform amplitude, dash reach, and stroke widths are all fractions of the radius in `RawVoice.swift`. That's what keeps the dash reach looking right at 80 pt and at 320 pt instead of correct at one size and wrong at another. Never hardcode an absolute offset that doesn't scale with `diameter`.
- **One responsive guardrail.** The view clamps its draw size to `min(diameter, min(width, height))`, so in a cramped or resized window it shrinks gracefully instead of overflowing. On any normal screen it's a flat fixed size; the clamp only ever kicks in when the container is genuinely too small. If you want a stricter guard for inline placements, clamp the caller's frame to something like `min(220, min(w, h) * 0.45)`.

## Tunable parameters

| Parameter | Default | Notes |
| --- | --- | --- |
| `diameter` | 220 | Overall footprint in points. See sizing table. |
| `dashReachRatio` | 0.18 | How far a dash travels past the waveform ring before vanishing, as a fraction of radius. ~0.18 is the "reined in" look. Higher → streaking. |
| `density` | 0.5 | How many dashes are in flight at once. Low = sparks; high = a continuous corona. |
| `lineColor` | white | The brief is white line. A faint accent only on loud peaks was floated as a future option — not in this build. |
| `stageColor` | dark ink | Pass `.clear` if the surrounding UI is already dark. |

The smoothing envelope (attack/release) lives in `VoiceLevelMonitor.apply` and `SimulatedVoiceSource.tick`. Faster attack = snappier reaction to syllables; slower release = a more lingering settle.

## Platform notes

- Targets both macOS and iOS. The `AVAudioSession` configuration is iOS-only and guarded with `#if os(iOS)`.
- Mic permission: add **NSMicrophoneUsageDescription** ("Privacy - Microphone Usage Description") to Info.plist. `VoiceLevelMonitor.requestPermission` routes through `AVAudioApplication.requestRecordPermission` on iOS 17+, falling back to `AVAudioSession` on older iOS.
- Performance: the dash field is stateless and the whole view is a single `Canvas` redraw per frame at ~50 strokes plus two closed paths — comfortably 60 fps. There are no timers driving layout (the only `Timer` is in the optional simulated source).

## Open follow-ups (not in this build)

- A distinct **"processing / thinking"** state for the gap between the user stopping and the transcript resolving (e.g. rings collapse inward and spin faster). Currently there are only two states: listening and capturing.
- A single **accent color that bleeds in only on loud peaks**, kept mostly monochrome, as a more premium variant of the pure-white look.

If you implement either, keep them gated and parametrized so the default stays the clean minimal version described here.
