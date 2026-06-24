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
// ---------------------------------------------------------------------------
final class SaySpeaker: Speaking {
    var onFinish: (() -> Void)?
    private(set) var isPaused = false
    private var proc: Process?

    // The Siri voice — only reachable via the `say` CLI, not AVSpeechSynthesizer.
    private let sayVoice = VELOVOX.readAloud.sayVoiceName

    func speak(_ chunks: [Chunk], voiceSpec: String, rate: Float) {
        // Join the already-cleaned chunk text (drop empty hr chunks). rate_factor /
        // pauses are AVSpeech-specific and ignored here — `say` paces itself.
        let text = chunks.map(\.text).filter { !$0.isEmpty }.joined(separator: " ")
        guard !text.isEmpty else { return }

        stop()            // cancel anything already in flight
        isPaused = false

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/say")
        // say's rate is words-per-minute. Map config rate (readAloud.rate, ~0–1)
        // onto a wpm range: 0→120, 0.5→325, 1.0→500 (clamped 120–600). Tune via
        // config.json `readAloud.rate` + `make launch`.
        let wpm = max(120, min(600, Int(150 + rate * 350)))
        p.arguments = ["-v", sayVoice, "-r", String(wpm)]
        let stdin = Pipe()                    // feed text on stdin: any length / chars
        p.standardInput = stdin
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self else { return }
                self.proc = nil
                self.isPaused = false
                self.onFinish?()
            }
        }

        let t0 = Date()
        do {
            try p.run()
        } catch {
            NSLog("readaloud(say-shim): failed to launch say: \(error)")
            return
        }
        let h = stdin.fileHandleForWriting
        try? h.write(contentsOf: Data(text.utf8))
        try? h.close()
        proc = p
        NSLog("readaloud(say-shim): launched 'say -v \(sayVoice) -r \(wpm)' in \(Int(Date().timeIntervalSince(t0) * 1000))ms (\(text.count) chars)")
    }

    func togglePause() {
        guard let p = proc, p.isRunning else { return }
        if !isPaused {
            kill(p.processIdentifier, SIGSTOP)   // pause audio
            isPaused = true
        } else {
            kill(p.processIdentifier, SIGCONT)   // resume
            isPaused = false
        }
    }

    func stop() {
        if let p = proc, p.isRunning {
            if isPaused { kill(p.processIdentifier, SIGCONT) }  // can't TERM a stopped proc
            p.terminate()
        }
        proc = nil
        isPaused = false
    }

    var isActive: Bool { proc?.isRunning == true }
}
