# Keep-the-best recording — play back your fastest dictation

*Status: designed, NOT started. Deferred by choice 2026-06-22 — capture the
design, build later. All decisions below are locked unless noted.*

## Goal

Let the user listen back to the audio of their **best words-per-minute** session.
Not an archive of everything — just always hold onto the one clip that matters,
so "play my fastest take" is one command away.

## Locked decisions

- **Keep-best-only retention.** Retain audio for the current champion session
  only. When a new session's WPM beats it, delete the old champion's file. This
  bounds storage to ~one recording AND is exactly the feature ("the best WPM
  recording"). Optional later: keep top-3 for a podium.
- **AAC `.m4a`** container/codec. ~1 MB/min vs ~11.5 MB/min for raw 48 kHz mono
  float WAV. Worth the small extra setup given how much the user dictates.
- **Default OFF.** This flips SpeakWrite from "transcribe and forget" to "keeps
  recordings of your voice on disk." Must be an explicit opt-in knob, not silent.

## Architecture

Everything hangs off the EXISTING mic tap in `Dictation.run` (`mac/main.swift`,
the `input.installTap(onBus:…)` block). That callback already has each input
buffer and currently does two things with it: (1) feeds the SpeechAnalyzer
stream, (2) computes the RMS level for the orb. We add a **third tee**: write the
buffer to an `AVAudioFile`. The audio we want is already in hand — we just stop
throwing it away.

1. **On start** (when `CONFIG.audioRecord` is true): open an `AVAudioFile` for
   writing at a temp path, e.g. `~/.config/speakwrite/recordings/session-<ts>.m4a`.
   Use AAC output settings; the file's processing format can match the input
   node's format so buffers write without conversion.
2. **In the tap**: append each input `AVAudioPCMBuffer` to the file. **Real-time
   caveat:** the tap runs on a real-time audio thread and `AVAudioFile.write` is
   not strictly RT-safe. Hand buffers to a dedicated serial `DispatchQueue`
   ("speakwrite.recorder") and write there — don't block the audio thread. For
   dictation an inline write usually survives, but the queue is the correct build.
3. **On stop**: finalize/close the file (let the queue drain first). We now have a
   complete clip + the session's computed WPM (from the existing `WPMMeter`).

## Linking audio → metric → "best"

`metrics.jsonl` already stores `wpm` per session. Add one field:

```json
{"date":"…","words":87,"speakingSeconds":36.8,"totalSeconds":52.1,"wpm":142,"audioFile":"…/session-….m4a"}
```

"Best WPM recording" = the row with `max(wpm)` whose `audioFile` still exists.
Because of keep-best-only, normally just one row has a live `audioFile`.

## Retention logic (keep-best-only)

On stop, after computing the new metric:

1. Read the current best WPM from existing metrics (or cache it).
2. If `new.wpm > bestSoFar`: this is the new champion. Delete the previous
   champion's `audioFile`, keep the new one, record its path in the metric.
3. Else: this session didn't win — **delete its just-recorded file immediately**
   and write the metric with `audioFile: null`.

Net: at most one audio file on disk (or N for top-N). Self-pruning, no sweep job.

Edge cases: champion file manually deleted → next session with any WPM can claim
the throne, or `make mac-play-best` reports "no recording for the current best."
Ties → keep the existing champion (first wins), avoids churn.

## Config knobs (`audio` block; remember: must also land in config.json — see CLAUDE.md)

```json
"audio": {
  "warnBluetoothInput": true,
  "record": false,           // master opt-in
  "keepTop": 1               // 1 = best only; bump for a podium
}
```

## Playback

Cheapest first: a `make mac-play-best` target — parse `metrics.jsonl`, find the
live champion, `afplay` (or `open`) its `audioFile`. `sw_stats.py` can also print
the champion's path. An in-app hotkey to play the champion is a nice-to-have, not
required for v1.

## Storage math (why keep-best-only matters)

AAC mono ~1 MB/min. Keep-best-only ⇒ ~1–3 MB total, forever. Keeping everything at
the user's cadence would be GBs/year — the reason we don't.

## Privacy

Recordings are sensitive. Default off, stored locally only, never uploaded. The
keep-best-only policy also minimizes what's retained. Consider a one-time notice
when `record` is first enabled.

## Effort

~an afternoon: capture (~30 min) · metric field + keep-best retention (~30 min) ·
`make mac-play-best` (~10 min) · config knobs + default-off + privacy notice ·
test by ear. The keep-best-only choice is what keeps it cheap — we build a
champion-holder, not a recording archive.
