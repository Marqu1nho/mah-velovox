// SpeakWrite — native macOS dictation anchor.
//
// One app: global hotkey -> floating HUD shows live SpeechTranscriber output
// (committed bright, volatile dim) -> hotkey again pastes the transcript at the
// cursor and restores the clipboard. No Python, no Hammerspoon, no parakeet.
//
// v1 edit-as-you-go: the HUD is an editable NSTextView. Finalized speech appends
// at the END of the document (before a dim volatile tail); you may freely edit
// anything above. On stop, the WHOLE edited document is pasted — not the raw
// transcript. The volatile tail is located by its dim attribute each update, so
// it's robust to your edits in the bright region above it.
import Cocoa
import Speech
import AVFoundation
import Carbon.HIToolbox

// ---------------------------------------------------------------------------
// Config — JSON at ~/.config/speakwrite/config.json, decoded via Codable (zero
// dependencies). Written with defaults on first run so there's always a file to
// edit; a malformed file logs and falls back to defaults rather than crashing.
//
// `replacements` is an ARRAY (not an object) on purpose: order matters — the
// dictionary is applied most-specific-first — and JSON objects don't preserve
// key order when decoded. `\n` is a native JSON escape, so newlines just work.
// ---------------------------------------------------------------------------
struct Replacement: Codable { let say: String; let insert: String }
struct HUDConfig: Codable { var alpha: Double; var fontSize: Double; var width: Double; var height: Double }

struct Config: Codable {
    var locale: String
    var hud: HUDConfig
    var replacements: [Replacement]

    static let fallback = Config(
        locale: "en-US",
        hud: HUDConfig(alpha: 0.82, fontSize: 22, width: 720, height: 200),
        replacements: [
            Replacement(say: "new paragraph", insert: "\n\n"),
            Replacement(say: "new line", insert: "\n"),
            Replacement(say: "cool beans", insert: "🆒🫘"),
        ])

    static func load() -> Config {
        let fm = FileManager.default
        let dir = fm.homeDirectoryForCurrentUser.appendingPathComponent(".config/speakwrite", isDirectory: true)
        let url = dir.appendingPathComponent("config.json")

        if !fm.fileExists(atPath: url.path) {
            try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
            let enc = JSONEncoder()
            enc.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes]
            if let data = try? enc.encode(fallback) { try? data.write(to: url) }
            NSLog("speakwrite: wrote default config -> \(url.path)")
            return fallback
        }
        do {
            let cfg = try JSONDecoder().decode(Config.self, from: Data(contentsOf: url))
            NSLog("speakwrite: loaded config <- \(url.path) (\(cfg.replacements.count) replacements)")
            return cfg
        } catch {
            NSLog("speakwrite: BAD config (\(error)); using defaults. Fix \(url.path)")
            return fallback
        }
    }
}

let CONFIG = Config.load()

// ---------------------------------------------------------------------------
// A borderless panel that CAN become key (so the text view accepts edits), but
// only when something inside actually needs it (becomesKeyOnlyIfNeeded) — so it
// stays hands-off and the synthesized ⌘V lands in the target app, v0-style.
// ---------------------------------------------------------------------------
final class KeyablePanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

// ---------------------------------------------------------------------------
// HUD — non-activating floating panel with a scrollable, editable text view.
// ---------------------------------------------------------------------------
final class HUD {
    private let panel: KeyablePanel
    private let textView: NSTextView
    private let fontSize = CGFloat(CONFIG.hud.fontSize)
    private let commAttrs: [NSAttributedString.Key: Any]
    private let volAttrs: [NSAttributedString.Key: Any]

    init() {
        commAttrs = [
            .font: NSFont.systemFont(ofSize: fontSize),
            .foregroundColor: NSColor.white,
        ]
        volAttrs = [
            .font: NSFont.systemFont(ofSize: fontSize),
            .foregroundColor: NSColor(white: 1.0, alpha: 0.45),
        ]

        let w = CGFloat(CONFIG.hud.width), h = CGFloat(CONFIG.hud.height)
        panel = KeyablePanel(
            contentRect: NSRect(x: 0, y: 0, width: w, height: h),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false)
        panel.level = .screenSaver                 // float above fullscreen
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true        // only grab focus to edit text
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.alphaValue = CGFloat(CONFIG.hud.alpha)   // lower = more transparent

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
        textView.isEditable = true               // v1: edit-as-you-go
        textView.isSelectable = true
        textView.isRichText = false              // plain text; we control attrs
        textView.drawsBackground = false
        textView.textColor = .white
        textView.insertionPointColor = .white
        textView.typingAttributes = commAttrs    // user edits render bright/white
        textView.textContainerInset = NSSize(width: 4, height: 4)
        scroll.documentView = textView

        bg.addSubview(scroll)
        panel.contentView!.addSubview(bg)
        reset()
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

    // The dim volatile tail is always the trailing run with reduced alpha. We
    // RECOMPUTE it each update by scanning back over dim-colored characters, so
    // edits the user makes in the bright region above never desync our bookkeeping.
    private func volatileRange() -> NSRange {
        guard let ts = textView.textStorage else { return NSRange(location: 0, length: 0) }
        let len = ts.length
        guard len > 0 else { return NSRange(location: 0, length: 0) }
        var loc = len
        while loc > 0 {
            var eff = NSRange()
            let color = ts.attribute(.foregroundColor, at: loc - 1, effectiveRange: &eff) as? NSColor
            if (color?.alphaComponent ?? 1.0) < 0.9 { loc = eff.location } else { break }
        }
        return NSRange(location: loc, length: len - loc)
    }

    // Everything before the volatile tail = the (possibly user-edited) document.
    func editableText() -> String {
        let full = textView.string as NSString
        return full.substring(to: min(volatileRange().location, full.length))
    }

    var panelIsKey: Bool { panel.isKeyWindow }

    func reset() {
        textView.string = ""
        setVolatile("listening…")
    }

    // Append finalized text at the end of the editable region (before the dim
    // tail). Preserves the user's cursor/selection if it sits above the insert.
    func appendFinal(_ s: String) {
        guard !s.isEmpty, let ts = textView.textStorage else { return }
        var insertAt = volatileRange().location
        // If this chunk opens a new line, trim trailing spaces/punctuation off the
        // previous line so it doesn't end on a dangling comma or period (handles
        // the case where the punctuation was committed in an earlier segment).
        if s.hasPrefix("\n") {
            let seam = CharacterSet(charactersIn: " \t,.;:!?")
            let str = ts.string as NSString
            var end = insertAt
            while end > 0,
                  str.substring(with: NSRange(location: end - 1, length: 1)).rangeOfCharacter(from: seam) != nil {
                end -= 1
            }
            if end < insertAt {
                ts.deleteCharacters(in: NSRange(location: end, length: insertAt - end))
                insertAt = end
            }
        }
        let nsStr = ts.string as NSString
        var piece = s
        if insertAt > 0 {
            let prev = nsStr.substring(with: NSRange(location: insertAt - 1, length: 1))
            let prevIsWS = prev.rangeOfCharacter(from: .whitespacesAndNewlines) != nil
            if !prevIsWS && !s.hasPrefix("\n") { piece = " " + s }
        }
        let sel = textView.selectedRange()
        ts.insert(NSAttributedString(string: piece, attributes: commAttrs), at: insertAt)
        if sel.location + sel.length <= insertAt { textView.setSelectedRange(sel) }
    }

    // Replace the dim tail in place with the live volatile guess.
    func setVolatile(_ s: String) {
        guard let ts = textView.textStorage else { return }
        let range = volatileRange()
        let wasAtBottom = atBottom()
        let sel = textView.selectedRange()
        ts.replaceCharacters(in: range, with: NSAttributedString(string: s, attributes: volAttrs))
        if sel.location + sel.length <= range.location { textView.setSelectedRange(sel) }
        if wasAtBottom { textView.scrollToEndOfDocument(nil) }
    }

    // Pin-to-bottom only if already at the bottom — leave the user's scroll
    // position alone if they scrolled up to re-read.
    private func atBottom() -> Bool {
        guard let scroll = textView.enclosingScrollView else { return true }
        let visible = scroll.documentVisibleRect
        return visible.maxY >= textView.bounds.height - 24
    }

    // Replace the whole document with a single bright string (used to show the
    // final transcript briefly when not in edit mode).
    func showFinal(_ s: String) {
        textView.textStorage?.setAttributedString(NSAttributedString(string: s, attributes: commAttrs))
        textView.scrollToEndOfDocument(nil)
    }

    func show() { positionCentered(); panel.orderFrontRegardless() }
    func hide() { panel.orderOut(nil) }
}

// ---------------------------------------------------------------------------
// Replacement dictionary — spoken phrase -> inserted text. Applied to each
// segment as it streams, so the HUD shows the substitution live and the pasted
// document already contains it. "new line" is just a dictionary entry that maps
// to a newline char (Apple never emits one itself). Case-insensitive, whole-
// phrase. Ordered most-specific-first. Trivial to externalize to config (§3.6).
// ---------------------------------------------------------------------------
enum Replacements {
    private static let compiled: [(NSRegularExpression, String)] = CONFIG.replacements.compactMap { rule in
        let p = NSRegularExpression.escapedPattern(for: rule.say)
        // Newline commands also swallow any whitespace/punctuation hugging the
        // phrase, so a spoken "new line" never leaves a stray comma or period on
        // the seam. Text replacements (e.g. emoji) keep their surroundings intact.
        let pattern = rule.insert.contains("\n")
            ? "[\\s,.;:!?]*\\b\(p)\\b[\\s,.;:!?]*"
            : "\\b\(p)\\b"
        guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { return nil }
        return (re, NSRegularExpression.escapedTemplate(for: rule.insert))
    }
    static func apply(_ s: String) -> String {
        var out = s
        for (re, template) in compiled {
            out = re.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: template)
        }
        return out
    }
}

// One-shot box for the converter input block (avoids a mutable-var capture).
private final class FeedBox { var buf: AVAudioPCMBuffer?; init(_ b: AVAudioPCMBuffer) { buf = b } }

// ---------------------------------------------------------------------------
// Dictation — AVAudioEngine mic -> Apple SpeechTranscriber PROGRESSIVE streaming.
//
// Emits per-segment deltas: onSegment(isFinal, text). Finalized chunks append at
// the end of the HUD doc; volatile updates replace the dim tail in place.
// ---------------------------------------------------------------------------
final class Dictation {
    private let engine = AVAudioEngine()
    private var analyzer: SpeechAnalyzer?
    private var continuation: AsyncStream<AnalyzerInput>.Continuation?
    private var resultsTask: Task<Void, Never>?
    private var committedCount = 0   // chars finalized this session (for the stop log)

    var onSegment: ((Bool, String) -> Void)?   // (isFinal, text)

    func start() {
        committedCount = 0
        Task { await run() }
    }

    func stop(_ done: @escaping () -> Void) {
        Task {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
            continuation?.finish()
            try? await analyzer?.finalizeAndFinishThroughEndOfInput()
            await resultsTask?.value
            NSLog("speakwrite: stop (\(committedCount) finalized chars this session)")
            await MainActor.run { done() }
        }
    }

    private func run() async {
        let transcriber = SpeechTranscriber(locale: Locale(identifier: CONFIG.locale),
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
                        let txt = Replacements.apply(String(r.text.characters))
                        let isFinal = r.isFinal
                        if isFinal { self.committedCount += txt.count }
                        await MainActor.run { self.onSegment?(isFinal, txt) }
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
    private var previousApp: NSRunningApplication?

    init() {
        dictation.onSegment = { [weak self] isFinal, text in
            guard let self else { return }
            if isFinal {
                self.hud.appendFinal(text)
                self.hud.setVolatile("")
            } else {
                self.hud.setVolatile(text)
            }
        }
    }

    func toggle() {
        NSLog("speakwrite: hotkey fired (was dictating=\(dictating))")
        if dictating {
            dictating = false
            dictation.stop { [weak self] in
                guard let self else { return }
                let edited = self.hud.editableText().trimmingCharacters(in: .whitespacesAndNewlines)
                let wasKey = self.hud.panelIsKey
                NSLog("speakwrite: pasting \(edited.count) chars (edited; panelWasKey=\(wasKey))")
                // If the user clicked in to edit, the HUD took key focus. Hide it
                // and restore the original app before pasting so ⌘V lands there.
                self.hud.hide()
                if wasKey { self.previousApp?.activate() }
                if !edited.isEmpty {
                    let delay = wasKey ? 0.18 : 0.0
                    DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
                        Paster.pasteAtCursor(edited)
                    }
                }
            }
        } else {
            dictating = true
            previousApp = NSWorkspace.shared.frontmostApplication
            hud.reset()
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
