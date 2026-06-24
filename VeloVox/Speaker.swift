// Speaker — *POC say-shim* (worktree throwaway).
//
// VeloVox normally speaks via AVSpeechSynthesizer, which CANNOT use the restricted
// "Siri" voices (they don't appear in AVSpeechSynthesisVoice.speechVoices()). This
// proof-of-concept instead shells out to `/usr/bin/say -v "Voice 2"` — the Siri
// voice IS reachable through the CLI — so Read Aloud speaks with it.
//
// Rate is set via `say -r <wpm>` (the system Spoken-Content rate does NOT apply to
// CLI say). The words-per-minute is mapped from the config rate (readAloud.rate),
// so you can tune speed by editing config.json + `make launch` (no recompile). We
// keep the same public interface (speak / togglePause / stop / isActive / onFinish)
// so ReadAloud.swift is unchanged. Pause/resume = SIGSTOP/SIGCONT on the say process.
//
// CAVEAT: -r speeds up the SYNTHESIS, which can sound choppy at high wpm. If it does,
// the clean path is to render to a file at 1x and pitch-preserving time-stretch
// (what made the 2.5x POC samples sound good) — a bigger change than this shim.
import Foundation
import Darwin

final class Speaker {
    var onFinish: (() -> Void)?
    private(set) var isPaused = false
    private var proc: Process?

    // The Siri voice — only reachable via the `say` CLI, not AVSpeechSynthesizer.
    private static let sayVoice = "Voice 2"

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
        p.arguments = ["-v", Self.sayVoice, "-r", String(wpm)]
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
        NSLog("readaloud(say-shim): launched 'say -v \(Self.sayVoice) -r \(wpm)' in \(Int(Date().timeIntervalSince(t0) * 1000))ms (\(text.count) chars)")
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
