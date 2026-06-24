// Speaker — Read Aloud's TTS backend, switchable between two engines via config:
//
//   readAloud.engine = "avspeech"  (default) → AVSpeaker, the AVSpeechSynthesizer
//                      "say"                  → SaySpeaker, shells out to /usr/bin/say
//
// AVSpeechSynthesizer CANNOT use the restricted "Siri" voices (they don't appear in
// AVSpeechSynthesisVoice.speechVoices()). The `say` CLI engine CAN reach them, so the
// say-shim exists to speak with e.g. "Voice 2" (set via readAloud.sayVoice). Both
// engines share the `Speaking` interface so ReadAloud.swift doesn't care which is live;
// SpeakerFactory.make() picks one from config.
import AVFoundation
import Foundation
import Darwin
import AppKit   // NSApplication.willTerminateNotification — self-contained quit cleanup for the warm spare

// ---------------------------------------------------------------------------
// Speaking — the shared interface ReadAloud.swift talks to. Both backends conform.
// ---------------------------------------------------------------------------
protocol Speaking: AnyObject {
    var onFinish: (() -> Void)? { get set }
    var isPaused: Bool { get }
    func speak(_ chunks: [Chunk], voiceSpec: String, rate: Float)
    func togglePause()
    func stop()
    var isActive: Bool { get }
}

// ---------------------------------------------------------------------------
// SpeakerFactory — returns the configured engine. "say" → SaySpeaker, else AVSpeaker.
// ---------------------------------------------------------------------------
enum SpeakerFactory {
    static func make() -> Speaking {
        VELOVOX.readAloud.ttsEngine == "say" ? SaySpeaker() : AVSpeaker()
    }
}

// ---------------------------------------------------------------------------
// AVSpeaker — wraps AVSpeechSynthesizer. Speaks a pipeline chunk list as a QUEUE of
// utterances: each chunk's pause becomes pre/postUtteranceDelay and headers slow
// down via rate_factor. Pause/resume/stop come from the framework and span the
// whole queue:
//   pauseSpeaking(at: .word) / continueSpeaking()  — resumes where it left off
//   stopSpeaking(at: .immediate)                   — instant
// ---------------------------------------------------------------------------
final class AVSpeaker: NSObject, Speaking, AVSpeechSynthesizerDelegate {
    // Non-Sendable per Apple; we only touch it on the main thread (hotkey → main;
    // delegate callbacks hop to main before any state change).
    nonisolated(unsafe) private let synth = AVSpeechSynthesizer()
    var onFinish: (() -> Void)?
    private(set) var isPaused = false
    private var remaining = 0
    private var ended = false

    override init() {
        super.init()
        synth.delegate = self
    }

    /// Resolve a config voice spec: a full identifier, or a friendly name like
    /// "Serena" / "Zoe". A name can have compact/enhanced/premium variants all
    /// matching (e.g. "Zoe" and "Zoe (Premium)"), so match every variant and pick
    /// the HIGHEST quality — otherwise the bare-named compact voice wins by accident.
    static func resolveVoice(_ spec: String) -> AVSpeechSynthesisVoice? {
        if let v = AVSpeechSynthesisVoice(identifier: spec) { return v }
        let lower = spec.lowercased()
        let matches = AVSpeechSynthesisVoice.speechVoices().filter {
            let n = $0.name.lowercased()
            return n == lower || n.hasPrefix(lower + " (") || $0.identifier.lowercased().contains(lower)
        }
        return matches.max(by: { $0.quality.rawValue < $1.quality.rawValue })
    }

    func speak(_ chunks: [Chunk], voiceSpec: String, rate: Float) {
        let voice = Self.resolveVoice(voiceSpec)
        if voice == nil { NSLog("readaloud: voice '\(voiceSpec)' unavailable; using system default") }

        let utterances: [AVSpeechUtterance] = chunks.map { c in
            // Empty text (hr) still needs to realize its pause, so speak a space.
            let u = AVSpeechUtterance(string: c.text.isEmpty ? " " : c.text)
            u.voice = voice
            let r = rate * Float(c.rate_factor)
            u.rate = max(min(r, AVSpeechUtteranceMaximumSpeechRate), AVSpeechUtteranceMinimumSpeechRate)
            u.preUtteranceDelay = Double(c.pause_before_ms) / 1000.0
            u.postUtteranceDelay = Double(c.pause_after_ms) / 1000.0
            return u
        }

        ended = false
        isPaused = false
        remaining = utterances.count
        guard remaining > 0 else { return }
        for u in utterances { synth.speak(u) }
    }

    func togglePause() {
        if synth.isSpeaking, !isPaused {
            synth.pauseSpeaking(at: .word)
            isPaused = true
        } else if isPaused {
            synth.continueSpeaking()
            isPaused = false
        }
    }

    func stop() {
        synth.stopSpeaking(at: .immediate)
        isPaused = false
    }

    var isActive: Bool { synth.isSpeaking || synth.isPaused }

    // MARK: delegate

    func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish u: AVSpeechUtterance) {
        remaining -= 1
        if remaining <= 0 { end() }
    }
    func speechSynthesizer(_ s: AVSpeechSynthesizer, didCancel u: AVSpeechUtterance) {
        end()
    }

    private func end() {
        if ended { return }
        ended = true
        remaining = 0
        isPaused = false
        DispatchQueue.main.async { [weak self] in self?.onFinish?() }
    }
}

// ---------------------------------------------------------------------------
// SaySpeaker — shells out to `/usr/bin/say -v <sayVoice>` so Read Aloud can use the
// restricted Siri voices that AVSpeechSynthesizer can't see. The voice name comes
// from readAloud.sayVoice (default "Voice 2").
//
// Rate is set via `say -r <wpm>` (the system Spoken-Content rate does NOT apply to
// CLI say). The words-per-minute is mapped from the config rate (readAloud.rate),
// so you can tune speed by editing config.json + `make launch` (no recompile).
// Pause/resume = SIGSTOP/SIGCONT on the say process; stop = terminate.
//
// CAVEAT: -r speeds up the SYNTHESIS, which can sound choppy at high wpm. If it does,
// the clean path is to render to a file at 1x and pitch-preserving time-stretch
// (what made the 2.5x POC samples sound good) — a bigger change than this shim.
//
// PRE-WARM (the latency win) ------------------------------------------------
// A cold `say` pays a FIXED ~1s tax per invocation before the first phoneme: it has
// to spawn, hand off to speechsynthesisd, and prime the neural voice. Measured: 3
// chars and 66 chars both cost ~1.1s to first audio — it's O(1) startup, not synth
// time. That dead-air gap is what you hear before the voice starts.
//
// So we keep ONE pre-spawned "warm spare": a `say` already past that ~1s setup,
// blocked on an open stdin pipe, waiting. When a read fires we just write the text
// and close stdin — the priming is already paid, so first audio comes ~0.5s sooner
// (measured 1.14s → 0.61s to first audio). The moment we consume the spare we spawn
// the next one, so back-to-back reads stay fast.
//
// Invariant — at most TWO `say` processes, both tracked by us:
//   active — the one currently speaking (nil when idle)
//   warm   — the idle, pre-primed spare (nil only briefly between consume & respawn)
// ensureWarm() is the single door to making a spare and NO-OPS if a live one exists,
// reaping any stale one first — so spares can't accumulate (the "if we ever have more
// than one, kill it" guard is structural: every reassignment is reaped/guarded, and
// we only ever signal PIDs we spawned, never a stray user `say`). If the spare is
// ever missing or stale (e.g. the rate changed since it was primed), speak() falls
// back to a cold spawn — identical to the pre-warm-less version, so we're never
// slower than before. Cost of the warm path: one idle ~29 MB `say` resident while the
// app runs (only when this engine is selected; it holds no audio device while idle).
// ---------------------------------------------------------------------------
final class SaySpeaker: Speaking {
    var onFinish: (() -> Void)?
    private(set) var isPaused = false

    private var active: Process?        // currently speaking (nil = idle)
    private var warm: Process?          // pre-primed spare, blocked on stdin
    private var warmStdin: FileHandle?  // write end of the spare's stdin
    private var warmWpm: Int?           // the -r the spare was primed with (rate can change live)

    // The Siri voice — only reachable via the `say` CLI, not AVSpeechSynthesizer.
    private let sayVoice = VELOVOX.readAloud.sayVoiceName

    init() {
        // Kill both processes when the app quits so no `say` is ever orphaned. Kept
        // inside this class (vs a hook in main.swift) to hold the blast radius to one
        // file. (A child say blocked on our stdin also gets EOF and exits when we die;
        // this is the belt to that suspenders.)
        NotificationCenter.default.addObserver(
            self, selector: #selector(appWillTerminate),
            name: NSApplication.willTerminateNotification, object: nil)
        // Prime a spare now so even the FIRST read of the session is instant. Choosing
        // the say engine signals intent to use Read Aloud, so the ~29 MB is earned.
        ensureWarm()
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
        reap(&active); reap(&warm); warmStdin = nil; warmWpm = nil
    }

    // say's rate is words-per-minute. Map config rate (~0–1) → wpm: 0→150, 0.5→325,
    // 1.0→500 (clamped 120–600). Single source of truth: used to prime the spare AND
    // to check whether the spare's rate still matches the requested read.
    private func wpm(for rate: Float) -> Int { max(120, min(600, Int(150 + rate * 350))) }

    func speak(_ chunks: [Chunk], voiceSpec: String, rate: Float) {
        // Join the already-cleaned chunk text (drop empty hr chunks). rate_factor /
        // pauses are AVSpeech-specific and ignored here — `say` paces itself.
        let text = chunks.map(\.text).filter { !$0.isEmpty }.joined(separator: " ")
        guard !text.isEmpty else { return }

        stopActive()      // cancel anything already speaking (NOT the warm spare)
        isPaused = false

        let want = wpm(for: rate)
        let t0 = Date()
        let usedWarm: Bool

        if let w = warm, w.isRunning, warmWpm == want, let h = warmStdin {
            // Fast path: a primed spare at the right rate is waiting → just feed it.
            active = w
            warm = nil; warmStdin = nil; warmWpm = nil
            try? h.write(contentsOf: Data(text.utf8))
            try? h.close()
            usedWarm = true
        } else {
            // Slow path: no spare, or rate changed since it was primed. Drop the stale
            // spare and spawn cold — exactly the old behavior, so never slower.
            reap(&warm); warmStdin = nil; warmWpm = nil
            guard let (p, h) = spawnPrimedSay(wpm: want) else { return }
            active = p
            try? h.write(contentsOf: Data(text.utf8))
            try? h.close()
            usedWarm = false
        }

        NSLog("readaloud(say-shim): speak \(usedWarm ? "via WARM spare" : "COLD spawn") in \(Int(Date().timeIntervalSince(t0) * 1000))ms (\(text.count) chars, -v \(sayVoice) -r \(want))")
        ensureWarm()      // immediately prime the spare for the NEXT read
    }

    func togglePause() {
        guard let p = active, p.isRunning else { return }
        if !isPaused {
            kill(p.processIdentifier, SIGSTOP)   // pause audio
            isPaused = true
        } else {
            kill(p.processIdentifier, SIGCONT)   // resume
            isPaused = false
        }
    }

    func stop() {
        stopActive()
        ensureWarm()      // re-prime so the read after a manual stop is still fast
    }

    // The idle spare is intentionally invisible here: a primed-but-silent `say` must
    // NOT count as "active", or the hotkey would try to pause a process that isn't
    // speaking (toggle() gates speak() on !isActive).
    var isActive: Bool { active?.isRunning == true }

    // MARK: - warm-spare machinery

    // Spawn a `say` primed and BLOCKED on stdin: it connects to speechsynthesisd and
    // loads the voice now, then waits for text. One terminationHandler serves both
    // roles by identity — so a process spawned as a spare and later promoted to active
    // is handled correctly without swapping handlers.
    private func spawnPrimedSay(wpm: Int) -> (Process, FileHandle)? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/say")
        p.arguments = ["-v", sayVoice, "-r", String(wpm)]
        let stdin = Pipe()                    // feed text on stdin: any length / chars
        p.standardInput = stdin
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self else { return }
                if self.active === proc {
                    // natural completion of a read
                    self.active = nil
                    self.isPaused = false
                    self.onFinish?()
                    self.ensureWarm()
                } else if self.warm === proc {
                    // a spare died on its own while idle → replace it
                    self.warm = nil; self.warmStdin = nil; self.warmWpm = nil
                    self.ensureWarm()
                }
                // else: a process we already reaped/replaced — ignore.
            }
        }
        do {
            try p.run()
        } catch {
            NSLog("readaloud(say-shim): failed to spawn say: \(error)")
            return nil
        }
        return (p, stdin.fileHandleForWriting)
    }

    // The single door to creating a spare. NO-OPS if a live one already exists, so
    // spares can never stack; reaps any stale spare before spawning a fresh one.
    private func ensureWarm() {
        if let w = warm, w.isRunning { return }
        reap(&warm); warmStdin = nil; warmWpm = nil
        let w = wpm(for: VELOVOX.readAloud.speechRate)
        if let (p, h) = spawnPrimedSay(wpm: w) {
            warm = p; warmStdin = h; warmWpm = w
        }
    }

    // Terminate ONLY the speaking process; leave the warm spare primed for next time.
    private func stopActive() {
        if let p = active, p.isRunning {
            if isPaused { kill(p.processIdentifier, SIGCONT) }  // can't TERM a stopped proc
            p.terminate()
        }
        active = nil
        isPaused = false
    }

    @objc private func appWillTerminate() {
        reap(&active); reap(&warm); warmStdin = nil; warmWpm = nil
    }

    // Terminate a tracked process if still running, then clear the slot. Only ever
    // targets a process WE spawned (we hold its reference) — never a stray user `say`.
    private func reap(_ slot: inout Process?) {
        if let p = slot, p.isRunning {
            if isPaused { kill(p.processIdentifier, SIGCONT) }
            p.terminate()
        }
        slot = nil
    }
}
