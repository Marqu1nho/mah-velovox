// SpeakWrite — native macOS dictation anchor (v0: anchor + paste).
//
// One app: global hotkey -> floating HUD shows live SpeechTranscriber output
// (committed bright, volatile dim) -> hotkey again pastes the transcript at the
// cursor and restores the clipboard. No Python, no Hammerspoon, no parakeet.
//
// The HUD is an NSTextView (non-editable in v0) so v1 "edit-as-you-go" is a flip
// of isEditable + append-at-end handling, not a rewrite.
import Cocoa
import Speech
import AVFoundation
import Carbon.HIToolbox

// ---------------------------------------------------------------------------
// HUD — non-activating floating panel with a scrollable text view.
// ---------------------------------------------------------------------------
final class HUD {
    private let panel: NSPanel
    private let textView: NSTextView
    private let fontSize: CGFloat = 22

    init() {
        let w: CGFloat = 720, h: CGFloat = 200
        panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: w, height: h),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false)
        panel.level = .screenSaver                 // float above fullscreen
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.alphaValue = 0.82   // more see-through; lower = more transparent

        // Rounded translucent background.
        let bg = NSVisualEffectView(frame: panel.contentView!.bounds)
        bg.autoresizingMask = [.width, .height]
        bg.material = .hudWindow
        bg.state = .active
        bg.wantsLayer = true
        bg.layer?.cornerRadius = 16
        bg.layer?.masksToBounds = true

        let inset: CGFloat = 18
        let scroll = NSScrollView(frame: bg.bounds.insetBy(dx: inset, dy: inset))
        scroll.autoresizingMask = [.width, .height]
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true

        textView = NSTextView(frame: scroll.bounds)
        textView.autoresizingMask = [.width]
        textView.isEditable = false              // v0: read-only anchor; v1 flips this
        textView.isSelectable = true
        textView.drawsBackground = false
        textView.textContainerInset = NSSize(width: 4, height: 4)
        scroll.documentView = textView

        bg.addSubview(scroll)
        panel.contentView!.addSubview(bg)
        setText(committed: "", volatile: "listening…")
        positionCentered()
    }

    private func positionCentered() {
        guard let screen = NSScreen.main else { return }
        let f = screen.visibleFrame
        let size = panel.frame.size
        let x = f.minX + (f.width - size.width) / 2
        let y = f.minY + (f.height - size.height) / 2   // dead center
        panel.setFrameOrigin(NSPoint(x: x, y: y))
    }

    func setText(committed: String, volatile: String) {
        let out = NSMutableAttributedString()
        let commAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: fontSize),
            .foregroundColor: NSColor.white,
        ]
        let volAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: fontSize),
            .foregroundColor: NSColor(white: 1.0, alpha: 0.45),
        ]
        out.append(NSAttributedString(string: committed, attributes: commAttrs))
        if !committed.isEmpty && !volatile.isEmpty {
            out.append(NSAttributedString(string: " ", attributes: commAttrs))
        }
        out.append(NSAttributedString(string: volatile, attributes: volAttrs))

        // Pin-to-bottom only if already at the bottom. If the user scrolled up to
        // re-read a previous anchor point, leave their position — don't yank them
        // down on every new word.
        var wasAtBottom = true
        if let scroll = textView.enclosingScrollView {
            let visible = scroll.documentVisibleRect
            wasAtBottom = visible.maxY >= textView.bounds.height - 24
        }
        textView.textStorage?.setAttributedString(out)
        if wasAtBottom { textView.scrollToEndOfDocument(nil) }
    }

    func show() { positionCentered(); panel.orderFrontRegardless() }
    func hide() { panel.orderOut(nil) }
}

// One-shot box for the converter input block (avoids a mutable-var capture).
private final class FeedBox { var buf: AVAudioPCMBuffer?; init(_ b: AVAudioPCMBuffer) { buf = b } }

// ---------------------------------------------------------------------------
// Dictation — AVAudioEngine mic -> Apple SpeechTranscriber PROGRESSIVE streaming.
//
// Live anchor: finalized text accumulates (committed, bright), the volatile tail
// updates in place (dim) and self-corrects to truth — proven flawless in the
// apple_live probe. (The earlier "streaming = garbage" was the retired parakeet
// stack shadowing the hotkey, never Apple.)
// ---------------------------------------------------------------------------
final class Dictation {
    private let engine = AVAudioEngine()
    private var analyzer: SpeechAnalyzer?
    private var continuation: AsyncStream<AnalyzerInput>.Continuation?
    private var resultsTask: Task<Void, Never>?
    private var committed = ""
    private var volatileText = ""

    var onUpdate: ((String, String) -> Void)?   // (committed, volatile)

    func start() {
        committed = ""; volatileText = ""
        Task { await run() }
    }

    func stop(_ done: @escaping (String) -> Void) {
        Task {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
            continuation?.finish()
            try? await analyzer?.finalizeAndFinishThroughEndOfInput()
            await resultsTask?.value
            let final = committed.trimmingCharacters(in: .whitespacesAndNewlines)
            NSLog("speakwrite: final (\(final.count) chars): \(final)")
            await MainActor.run { done(final) }
        }
    }

    private func run() async {
        let transcriber = SpeechTranscriber(locale: Locale(identifier: "en-US"),
                                            preset: .progressiveTranscription)
        do {
            if let req = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
                try await req.downloadAndInstall()
            }
            let analyzer = SpeechAnalyzer(modules: [transcriber]); self.analyzer = analyzer
            guard let fmt = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else { return }

            let (stream, cont) = AsyncStream<AnalyzerInput>.makeStream(); self.continuation = cont

            resultsTask = Task { [weak self] in
                guard let self else { return }
                do {
                    for try await r in transcriber.results {
                        let txt = String(r.text.characters)
                        if r.isFinal {
                            self.committed += (self.committed.isEmpty ? "" : " ") + txt
                            self.volatileText = ""
                        } else {
                            self.volatileText = txt
                        }
                        let c = self.committed, v = self.volatileText
                        await MainActor.run { self.onUpdate?(c, v) }
                    }
                } catch { NSLog("speakwrite: results error \(error)") }
            }

            try await analyzer.start(inputSequence: stream)

            let input = engine.inputNode
            let inFmt = input.outputFormat(forBus: 0)
            guard let converter = AVAudioConverter(from: inFmt, to: fmt) else { return }
            input.installTap(onBus: 0, bufferSize: 4096, format: inFmt) { buf, _ in
                let ratio = fmt.sampleRate / inFmt.sampleRate
                let cap = AVAudioFrameCount(Double(buf.frameLength) * ratio + 1024)
                guard let out = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: cap) else { return }
                let box = FeedBox(buf)
                let block: AVAudioConverterInputBlock = { _, status in
                    if let b = box.buf { box.buf = nil; status.pointee = .haveData; return b }
                    status.pointee = .noDataNow; return nil
                }
                var err: NSError?
                converter.convert(to: out, error: &err, withInputFrom: block)
                if err == nil && out.frameLength > 0 { cont.yield(AnalyzerInput(buffer: out)) }
            }
            engine.prepare()
            try engine.start()
            NSLog("speakwrite: streaming started fmt=\(inFmt.sampleRate)Hz ch=\(inFmt.channelCount)")
        } catch {
            NSLog("speakwrite: dictation error \(error)")
        }
    }
}

// ---------------------------------------------------------------------------
// Paste at cursor with full-fidelity clipboard save/restore.
// ---------------------------------------------------------------------------
enum Paster {
    static func pasteAtCursor(_ text: String) {
        guard !text.isEmpty else { return }
        let pb = NSPasteboard.general

        // Snapshot every item/type so an image (e.g. a screenshot) survives.
        var saved: [[NSPasteboard.PasteboardType: Data]] = []
        for item in pb.pasteboardItems ?? [] {
            var entry: [NSPasteboard.PasteboardType: Data] = [:]
            for type in item.types { if let d = item.data(forType: type) { entry[type] = d } }
            saved.append(entry)
        }

        pb.clearContents()
        pb.setString(text + " ", forType: .string)

        // Synthesize Cmd+V (requires Accessibility permission).
        let src = CGEventSource(stateID: .combinedSessionState)
        let v = CGKeyCode(kVK_ANSI_V)
        let down = CGEvent(keyboardEventSource: src, virtualKey: v, keyDown: true)
        down?.flags = .maskCommand
        let up = CGEvent(keyboardEventSource: src, virtualKey: v, keyDown: false)
        up?.flags = .maskCommand
        down?.post(tap: .cghidEventTap)
        up?.post(tap: .cghidEventTap)

        // Restore after the paste is consumed.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            pb.clearContents()
            guard !saved.isEmpty else { return }
            var items: [NSPasteboardItem] = []
            for entry in saved {
                let it = NSPasteboardItem()
                for (type, data) in entry { it.setData(data, forType: type) }
                items.append(it)
            }
            pb.writeObjects(items)
        }
    }
}

// ---------------------------------------------------------------------------
// App controller — global hotkey, orchestration.
// ---------------------------------------------------------------------------
final class Controller {
    private let hud = HUD()
    private let dictation = Dictation()
    private var dictating = false
    private var hotKeyRef: EventHotKeyRef?

    init() {
        // Live streaming updates drive the HUD: committed bright, volatile dim.
        dictation.onUpdate = { [weak self] committed, volatile in
            self?.hud.setText(committed: committed, volatile: volatile)
        }
    }

    func toggle() {
        NSLog("speakwrite: hotkey fired (was dictating=\(dictating))")
        if dictating {
            dictating = false
            dictation.stop { [weak self] final in
                guard let self else { return }
                if !final.isEmpty {
                    self.hud.setText(committed: final, volatile: "")
                    Paster.pasteAtCursor(final)
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { self.hud.hide() }
            }
        } else {
            dictating = true
            hud.setText(committed: "", volatile: "listening…")
            hud.show()
            dictation.start()
        }
    }

    func registerHotKey() {
        // ctrl+alt+S  (kVK_ANSI_S = 1)
        var ref: EventHotKeyRef?
        let id = EventHotKeyID(signature: OSType(0x53574B59), id: 1)  // 'SWKY'
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                 eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
            DispatchQueue.main.async { gController?.toggle() }
            return noErr
        }, 1, &spec, nil, nil)
        let mods = UInt32(controlKey | optionKey)
        let status = RegisterEventHotKey(UInt32(kVK_ANSI_S), mods, id,
                            GetApplicationEventTarget(), 0, &ref)
        hotKeyRef = ref
        NSLog("speakwrite: RegisterEventHotKey status=\(status) (0=ok; nonzero=already taken)")
    }
}

var gController: Controller?

// ---------------------------------------------------------------------------
// Entry.
// ---------------------------------------------------------------------------
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ note: Notification) {
        let c = Controller()
        gController = c
        c.registerHotKey()
        // Nudge the Accessibility prompt if we don't have it (needed for paste).
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        if !AXIsProcessTrustedWithOptions(opts) {
            NSLog("speakwrite: grant Accessibility (needed to paste) in System Settings → Privacy")
        }
        NSLog("speakwrite: STREAMING build ready — ctrl+alt+S to dictate (live), again to paste")
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // no dock icon
app.run()
