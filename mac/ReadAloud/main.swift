// ReadAloud — native macOS hotkey text-to-speech reader (Phase 1 walking skeleton).
// Replaces the Python daemon + Hammerspoon + Lua stack with one resident Swift app.
//
// Flow: global hotkey → capture the frontmost app's selection → minimal cleanup →
// speak it with AVSpeechSynthesizer (Zoe Premium by default) → show the transport
// pill. Same hotkey toggles pause/resume; the pill's right zone stops.
//
// Build/run via the Makefile read-mac-* targets. Config at
// ~/.config/readaloud/config.json (written on first run).
import Cocoa
import Carbon.HIToolbox

// ---------------------------------------------------------------------------
// Controller — owns the speaker, the pill, and the idle→reading→paused state.
// ---------------------------------------------------------------------------
final class Controller {
    private let speaker = Speaker()
    private let pill = TransportPill()
    private var hotKeyRef: EventHotKeyRef?

    init() {
        speaker.onFinish = { [weak self] in self?.onFinished() }
        pill.onToggle = { [weak self] in self?.togglePause() }
        pill.onStop = { [weak self] in self?.stop() }
    }

    /// Hotkey: start a read when idle, otherwise toggle pause (never stops — stop is
    /// the pill's right zone, matching the Lua's behavior).
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
        if raw.count > CONFIG.maxSelectionChars { raw = String(raw.prefix(CONFIG.maxSelectionChars)) }
        let chunks = Pipeline.chunks(from: raw, cfg: CONFIG.pipeline(), app: app)
        guard !chunks.isEmpty else {
            NSLog("readaloud: nothing to read after cleanup")
            return
        }
        speaker.speak(chunks, voiceSpec: CONFIG.voiceSpec, rate: CONFIG.speechRate)
        pill.show(yPct: CONFIG.alertYPct)
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

    // MARK: Hotkey registration (Carbon) — mirrors SpeakWrite's pattern.

    func registerHotKey() {
        var ref: EventHotKeyRef?
        let id = EventHotKeyID(signature: OSType(0x52444B59), id: 1) // 'RDKY'
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                 eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
            DispatchQueue.main.async { gController?.toggle() }
            return noErr
        }, 1, &spec, nil, nil)
        let parsed = Self.parseHotKey(CONFIG.hotkeySpec)
        let (key, mods) = parsed ?? (UInt32(kVK_ANSI_R), UInt32(controlKey | optionKey | cmdKey))
        let note = parsed == nil ? " — UNPARSEABLE, using default ctrl+alt+cmd+r" : ""
        let status = RegisterEventHotKey(key, mods, id, GetApplicationEventTarget(), 0, &ref)
        hotKeyRef = ref
        NSLog("readaloud: hotkey '\(CONFIG.hotkeySpec)'\(note) status=\(status) (0=ok; nonzero=already taken)")
    }

    static func parseHotKey(_ spec: String) -> (UInt32, UInt32)? {
        var mods: UInt32 = 0
        var keyCode: UInt32? = nil
        for raw in spec.lowercased().split(separator: "+") {
            let t = raw.trimmingCharacters(in: .whitespaces)
            switch t {
            case "cmd", "command", "⌘":       mods |= UInt32(cmdKey)
            case "ctrl", "control", "⌃":      mods |= UInt32(controlKey)
            case "alt", "opt", "option", "⌥": mods |= UInt32(optionKey)
            case "shift", "⇧":                mods |= UInt32(shiftKey)
            default:                          keyCode = keyCodeMap[t]
            }
        }
        guard let k = keyCode else { return nil }
        return (k, mods)
    }

    private static let keyCodeMap: [String: UInt32] = [
        "a":0,"s":1,"d":2,"f":3,"h":4,"g":5,"z":6,"x":7,"c":8,"v":9,"b":11,"q":12,
        "w":13,"e":14,"r":15,"y":16,"t":17,"o":31,"u":32,"i":34,"p":35,"l":37,
        "j":38,"k":40,"n":45,"m":46,
        "1":18,"2":19,"3":20,"4":21,"5":23,"6":22,"7":26,"8":28,"9":25,"0":29,
        "space":49,"return":36,"enter":36,"tab":48,"escape":53,"esc":53,
        "`":50,"grave":50,"backtick":50,"-":27,"minus":27,"=":24,"equal":24,
        "[":33,"]":30,";":41,"'":39,",":43,".":47,"period":47,"/":44,"slash":44,"\\":42,
    ]
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
        // Accessibility is required to post the synthetic ⌘C and read AXSelectedText.
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        if !AXIsProcessTrustedWithOptions(opts) {
            NSLog("readaloud: grant Accessibility (needed to capture selection) in System Settings → Privacy")
        }
        NSLog("readaloud: Phase 1 ready — \(CONFIG.hotkeySpec) to read the selection; again to pause")
    }
}

// Hidden CLI mode for fidelity testing: `ReadAloud --script < text` prints the
// pipeline chunks as JSON, so the Swift port can be diffed against the Python
// `readaloud script`. Runs the pipeline and exits without launching the UI.
if CommandLine.arguments.contains("--script") {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    let raw = String(data: data, encoding: .utf8) ?? ""
    let chunks = Pipeline.chunks(from: raw, cfg: CONFIG.pipeline(), app: nil)
    let enc = JSONEncoder()
    enc.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    if let out = try? enc.encode(chunks), let s = String(data: out, encoding: .utf8) { print(s) }
    exit(0)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // no dock icon
app.run()
