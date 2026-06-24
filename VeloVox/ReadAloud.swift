// ReadAloud — native macOS hotkey text-to-speech reader (the "read aloud" half of
// Velovox). Global hotkey → capture the frontmost app's selection → minimal
// cleanup → speak it with AVSpeechSynthesizer → show the transport pill. Same
// hotkey toggles pause/resume; the pill's right zone stops.
//
// Config lives in the `readAloud` section of ~/.config/velovox/config.json.
import Cocoa
import Carbon.HIToolbox

// ---------------------------------------------------------------------------
// ReadAloudController — owns the speaker, the pill, and the idle→reading→paused
// state.
// ---------------------------------------------------------------------------
final class ReadAloudController {
    private let speaker = Speaker()
    private let pill = TransportPill()

    init() {
        speaker.onFinish = { [weak self] in self?.onFinished() }
        pill.onToggle = { [weak self] in self?.togglePause() }
        pill.onStop = { [weak self] in self?.stop() }
    }

    /// Hotkey: start a read when idle, otherwise toggle pause (never stops — stop is
    /// the pill's right zone).
    func toggle() {
        if speaker.isActive {
            togglePause()
        } else {
            startRead()
        }
    }

    private func startRead() {
        let app = Capture.frontmostAppName()
        guard var raw = Capture.selection(), !raw.isEmpty else {
            NSLog("readaloud: no selection captured")
            return
        }
        if raw.count > VELOVOX.readAloud.maxSelectionChars { raw = String(raw.prefix(VELOVOX.readAloud.maxSelectionChars)) }
        let chunks = Pipeline.chunks(from: raw, cfg: VELOVOX.readAloud.pipeline(), app: app)
        guard !chunks.isEmpty else {
            NSLog("readaloud: nothing to read after cleanup")
            return
        }
        speaker.speak(chunks, voiceSpec: VELOVOX.readAloud.voiceSpec, rate: VELOVOX.readAloud.speechRate)
        pill.show(yPct: VELOVOX.readAloud.alertYPct)
    }

    private func togglePause() {
        speaker.togglePause()
        pill.setPaused(speaker.isPaused)
    }

    private func stop() {
        speaker.stop()   // → didCancel → onFinished (also hides the pill)
        pill.hide()
    }

    private func onFinished() {
        pill.hide()
    }

    // Register the read-aloud hotkey through the shared manager (routes by
    // EventHotKeyID so it coexists with SpeakWrite's hotkey in one process).
    func registerHotKey() {
        HotKeys.register(id: HotKeyID.readAloud,
                         spec: VELOVOX.readAloud.hotkeySpec,
                         defaultKey: UInt32(kVK_ANSI_R),
                         defaultMods: UInt32(controlKey | optionKey | cmdKey)) {
            gReadAloud?.toggle()
        }
    }
}

var gReadAloud: ReadAloudController?
