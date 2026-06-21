// tts_probe — hear Apple's AVSpeechSynthesizer (Siri/Premium voices) speak a
// paragraph, and measure time-to-first-word. The point: decide whether a NATIVE
// readaloud (AVSpeechSynthesizer, no daemon, no chunking) sounds as good and starts
// as fast as the kokoro warm-daemon version — without readaloud's say-CLI +
// sounddevice + manual-chunking plumbing that likely caused the "buggy stops".
//
// Build & run (or just `make tts`):
//   xcrun -sdk macosx swiftc mac/tts_probe.swift -o mac/tts_probe
//   ./mac/tts_probe --list                 # list installed English voices + identifiers
//   ./mac/tts_probe                         # speak with the system default voice
//   ./mac/tts_probe Zoe                     # speak with the first voice matching "Zoe"
//   ./mac/tts_probe com.apple.voice.premium.en-US.Zoe   # or an exact identifier
//   ./mac/tts_probe Zoe 0.5                 # optional 2nd arg = rate (0..1, default ~0.5)
//
// Listen for: does it START fast, and does it FLOW naturally through the sentence
// with no choppy gaps? If yes -> native readaloud is a clean win (you keep the Siri
// voice you liked, lose the daemon/Hammerspoon/sounddevice). See plan-docs/speakwritev1.md §4.
import Foundation
import AVFoundation

let TEXT = """
Notice that one of the things I really do is have a passage or two in a piece that \
really captivate me. And no matter how many times I listen to it, the passage is like \
this little shard of musical perfection — it's hinting at the potential of a reality \
beyond our own, where all music is as perfect and inexhaustible as such a passage.
"""

func err(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

func qualityName(_ q: AVSpeechSynthesisVoiceQuality) -> String {
    switch q {
    case .premium: return "premium"
    case .enhanced: return "enhanced"
    default: return "default"
    }
}

func listVoices() {
    let voices = AVSpeechSynthesisVoice.speechVoices()
        .filter { $0.language.hasPrefix("en") }
        .sorted { $0.quality.rawValue > $1.quality.rawValue }
    print("=== installed English voices (name | quality | language | identifier) ===")
    for v in voices {
        print("  \(v.name) | \(qualityName(v.quality)) | \(v.language) | \(v.identifier)")
    }
    print("(premium/enhanced need downloading in System Settings > Accessibility > Spoken Content > Voices)")
}

final class Speaker: NSObject, AVSpeechSynthesizerDelegate {
    let synth = AVSpeechSynthesizer()
    var start = Date()
    override init() { super.init(); synth.delegate = self }

    func speak(_ text: String, voice: AVSpeechSynthesisVoice?, rate: Float?) {
        let u = AVSpeechUtterance(string: text)
        if let v = voice { u.voice = v }
        if let r = rate { u.rate = r }
        start = Date()
        synth.speak(u)
    }

    func speechSynthesizer(_ s: AVSpeechSynthesizer, didStart u: AVSpeechUtterance) {
        err(String(format: "time-to-first-word: %.3fs", Date().timeIntervalSince(start)))
    }
    func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish u: AVSpeechUtterance) {
        err(String(format: "done — %.1fs total", Date().timeIntervalSince(start)))
        exit(0)
    }
    func speechSynthesizer(_ s: AVSpeechSynthesizer, didCancel u: AVSpeechUtterance) { exit(0) }
}

// ---- main ----
let args = CommandLine.arguments
listVoices()
if args.count > 1 && args[1] == "--list" { exit(0) }

var chosen: AVSpeechSynthesisVoice? = nil
if args.count > 1 {
    let q = args[1].lowercased()
    chosen = AVSpeechSynthesisVoice.speechVoices().first {
        $0.identifier.lowercased().contains(q) || $0.name.lowercased().contains(q)
    }
    if chosen == nil { err("voice '\(args[1])' not found — using system default") }
}
let rate: Float? = (args.count > 2) ? Float(args[2]) : nil

err("\nspeaking with: \(chosen?.name ?? "system default")\(rate.map { ", rate \($0)" } ?? "")")
let speaker = Speaker()
speaker.speak(TEXT, voice: chosen, rate: rate)
RunLoop.main.run()   // keep alive for delegate callbacks; exits on didFinish
