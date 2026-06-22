// Speaker — wraps AVSpeechSynthesizer. Speaks a pipeline chunk list as a QUEUE of
// utterances: each chunk's pause becomes pre/postUtteranceDelay and headers slow
// down via rate_factor. Pause/resume/stop come from the framework and span the
// whole queue:
//   pauseSpeaking(at: .word) / continueSpeaking()  — resumes where it left off
//   stopSpeaking(at: .immediate)                   — instant
import AVFoundation

final class Speaker: NSObject, AVSpeechSynthesizerDelegate {
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
