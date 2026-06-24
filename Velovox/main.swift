// Velovox — one resident macOS app, two voice tools:
//   • Read Aloud  (⌃⌥⌘R default) — speak the current text selection aloud.
//   • Dictate     (⌃⌥S  default) — live on-device dictation pasted at the cursor.
//
// Everything runs on Apple's on-device speech stacks — no cloud, no network. The
// app lives in the menu bar (a waveform icon); both hotkeys are global. Config is
// one file: ~/.config/velovox/config.json (sections `readAloud` and `speakWrite`).
//
// This is the only file with top-level code (Swift requires the executable's
// entry statements to live in main.swift); every other file is declarations only.
import Cocoa
import Carbon.HIToolbox

// ---------------------------------------------------------------------------
// Hidden CLI mode for fidelity testing: `Velovox --script < text` prints the
// ReadAloud pipeline chunks as JSON and exits without launching the UI.
// ---------------------------------------------------------------------------
if CommandLine.arguments.contains("--script") {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    let raw = String(data: data, encoding: .utf8) ?? ""
    let chunks = Pipeline.chunks(from: raw, cfg: VELOVOX.readAloud.pipeline(), app: nil)
    let enc = JSONEncoder()
    enc.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    if let out = try? enc.encode(chunks), let s = String(data: out, encoding: .utf8) { print(s) }
    exit(0)
}

// ---------------------------------------------------------------------------
// `Velovox --stats` — dictation WPM readout (replaces the old sw_stats.py). Reads
// the JSONL metrics log and prints 7-day / last-50 / all-time pace plus totals.
// We measure SPEAKING time, not recording time, so wpm reflects how fast you
// actually talk; "thinking %" is the share of mic time excluded as silence.
// ---------------------------------------------------------------------------
if CommandLine.arguments.contains("--stats") {
    let url = Metrics.fileURL
    let text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
    let dec = JSONDecoder()
    let sessions: [Metric] = text.split(separator: "\n").compactMap { line in
        let t = line.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty, let d = t.data(using: .utf8) else { return nil }
        return try? dec.decode(Metric.self, from: d)
    }
    guard !sessions.isEmpty else {
        print("No sessions recorded yet — dictate something first.")
        exit(0)
    }
    let iso = ISO8601DateFormatter()
    func when(_ m: Metric) -> Date { iso.date(from: m.date) ?? Date.distantPast }
    let cutoff = Date().addingTimeInterval(-7 * 86400)
    let byRecent = sessions.sorted { when($0) > when($1) }
    let last7  = sessions.filter { when($0) >= cutoff }
    let last50 = Array(byRecent.prefix(50))
    func avg(_ xs: [Metric]) -> String {
        guard !xs.isEmpty else { return "—" }
        let mean = xs.map(\.wpm).reduce(0, +) / Double(xs.count)
        return "\(Int(mean.rounded())) wpm"
    }
    let totalSpeaking = sessions.map(\.speakingSeconds).reduce(0, +)
    let totalMic      = sessions.map(\.totalSeconds).reduce(0, +)
    let thinking      = totalMic > 0 ? (1 - totalSpeaking / totalMic) : 0
    let totalWords    = sessions.map(\.words).reduce(0, +)
    let wpms          = sessions.map(\.wpm)
    print("Velovox dictation stats")
    print("=======================")
    print("  7-day avg     : \(avg(last7))  (\(last7.count) sessions)")
    print("  last-50 avg   : \(avg(last50))  (\(last50.count) sessions)")
    print("  all-time avg  : \(avg(sessions))  (\(sessions.count) sessions)")
    print("")
    print("  total sessions: \(sessions.count)")
    print("  best wpm      : \(Int(wpms.max() ?? 0)) wpm")
    print("  worst wpm     : \(Int(wpms.min() ?? 0)) wpm")
    print("  total words   : \(totalWords)")
    print(String(format: "  thinking %%    : %.0f%%  (share of mic time excluded as silence/thinking)", thinking * 100))
    exit(0)
}

// ---------------------------------------------------------------------------
// AppDelegate — owns both controllers, registers both hotkeys, and builds the
// menu-bar item so the (otherwise invisible) agent has a face you can click.
// ---------------------------------------------------------------------------
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let readAloud = ReadAloudController()
    private let speakWrite = SpeakWriteController()
    private var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ note: Notification) {
        gReadAloud = readAloud
        gSpeakWrite = speakWrite
        Metrics.migrateIfNeeded()   // carry old SpeakWrite dictation history forward
        readAloud.registerHotKey()
        speakWrite.registerHotKey()
        buildMenuBar()

        // Both tools need Accessibility (synthetic ⌘C to read the selection, ⌘V to
        // paste). Nudge the prompt once if it isn't granted yet.
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        if !AXIsProcessTrustedWithOptions(opts) {
            NSLog("velovox: grant Accessibility (needed to read selection + paste) in System Settings → Privacy")
        }
        NSLog("velovox: ready — \(prettyHotkey(VELOVOX.readAloud.hotkeySpec)) reads the selection, \(prettyHotkey(VELOVOX.speakWrite.hotkeySpec)) dictates")
    }

    // MARK: Menu bar

    private func buildMenuBar() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            button.image = Self.menuBarIcon()
        }
        item.menu = makeMenu()
        statusItem = item
    }

    // A small, light "VX" monogram centered INSIDE sound-wave arcs that radiate
    // out both sides (sound coming off the VX). Drawn as a template image (alpha
    // only) so macOS tints it for light/dark menu bars and selection highlight.
    static func menuBarIcon() -> NSImage {
        let size = NSSize(width: 36, height: 16)
        let img = NSImage(size: size)
        img.lockFocus()
        guard let ctx = NSGraphicsContext.current?.cgContext else { img.unlockFocus(); return img }
        ctx.setShouldAntialias(true)
        ctx.setLineCap(.round)
        ctx.setLineJoin(.round)

        let cx = size.width / 2, cy = size.height / 2

        // Sound-wave arcs fanning out on BOTH sides, centered on the VX. Two arcs
        // per side, larger = fainter/thinner, so the VX looks like the source.
        for (i, r) in [7.0, 10.0].enumerated() {
            ctx.setStrokeColor(NSColor.black.withAlphaComponent(0.55 - CGFloat(i) * 0.18).cgColor)
            ctx.setLineWidth(1.3 - CGFloat(i) * 0.35)
            // right side
            ctx.addArc(center: CGPoint(x: cx, y: cy), radius: r,
                       startAngle: -.pi / 4.2, endAngle: .pi / 4.2, clockwise: false)
            ctx.strokePath()
            // left side (mirror)
            ctx.addArc(center: CGPoint(x: cx, y: cy), radius: r,
                       startAngle: .pi - .pi / 4.2, endAngle: .pi + .pi / 4.2, clockwise: false)
            ctx.strokePath()
        }

        // "VX" centered — thinner (medium weight) and shorter (smaller) than before.
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 8.5, weight: .medium),
            .foregroundColor: NSColor.black,
        ]
        let s = NSAttributedString(string: "VX", attributes: attrs)
        let ts = s.size()
        s.draw(at: NSPoint(x: cx - ts.width / 2, y: cy - ts.height / 2))

        img.unlockFocus()
        img.isTemplate = true
        return img
    }

    private func makeMenu() -> NSMenu {
        let menu = NSMenu()

        let header = NSMenuItem(title: "Velovox", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        menu.addItem(.separator())

        // The two tools double as enable/disable checkboxes; the title carries the
        // current hotkey so there's a built-in cheat sheet.
        let raItem = NSMenuItem(title: "Read Aloud  ·  \(prettyHotkey(VELOVOX.readAloud.hotkeySpec))",
                                action: #selector(toggleReadAloud), keyEquivalent: "")
        raItem.target = self
        raItem.state = HotKeys.isEnabled(id: HotKeyID.readAloud) ? .on : .off
        raItem.tag = Int(HotKeyID.readAloud)
        menu.addItem(raItem)

        let swItem = NSMenuItem(title: "Dictate  ·  \(prettyHotkey(VELOVOX.speakWrite.hotkeySpec))",
                                action: #selector(toggleSpeakWrite), keyEquivalent: "")
        swItem.target = self
        swItem.state = HotKeys.isEnabled(id: HotKeyID.speakWrite) ? .on : .off
        swItem.tag = Int(HotKeyID.speakWrite)
        menu.addItem(swItem)

        menu.addItem(.separator())

        let edit = NSMenuItem(title: "Edit Config…", action: #selector(editConfig), keyEquivalent: ",")
        edit.target = self
        menu.addItem(edit)

        let reveal = NSMenuItem(title: "Reveal Config in Finder", action: #selector(revealConfig), keyEquivalent: "")
        reveal.target = self
        menu.addItem(reveal)

        menu.addItem(.separator())

        let about = NSMenuItem(title: "About Velovox", action: #selector(showAbout), keyEquivalent: "")
        about.target = self
        menu.addItem(about)

        let quit = NSMenuItem(title: "Quit Velovox", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)

        return menu
    }

    @objc private func toggleReadAloud(_ sender: NSMenuItem) {
        let now = !HotKeys.isEnabled(id: HotKeyID.readAloud)
        HotKeys.setEnabled(now, id: HotKeyID.readAloud)
        sender.state = now ? .on : .off
        NSLog("velovox: Read Aloud \(now ? "enabled" : "disabled")")
    }

    @objc private func toggleSpeakWrite(_ sender: NSMenuItem) {
        let now = !HotKeys.isEnabled(id: HotKeyID.speakWrite)
        HotKeys.setEnabled(now, id: HotKeyID.speakWrite)
        sender.state = now ? .on : .off
        NSLog("velovox: Dictate \(now ? "enabled" : "disabled")")
    }

    @objc private func editConfig() {
        let url = VelovoxConfig.fileURL
        // Make sure it exists (load() writes it on first run, but be safe).
        if !FileManager.default.fileExists(atPath: url.path) { _ = VelovoxConfig.load() }
        NSWorkspace.shared.open(url)
    }

    @objc private func revealConfig() {
        NSWorkspace.shared.activateFileViewerSelecting([VelovoxConfig.fileURL])
    }

    @objc private func showAbout() {
        let alert = NSAlert()
        alert.messageText = "Velovox"
        alert.informativeText = """
        Two on-device voice tools in one menu-bar app.

        • Read Aloud  (\(prettyHotkey(VELOVOX.readAloud.hotkeySpec)))  — speak the selected text.
        • Dictate  (\(prettyHotkey(VELOVOX.speakWrite.hotkeySpec)))  — dictate at the cursor.

        Config: ~/.config/velovox/config.json
        Everything runs locally — no cloud, no network.
        """
        alert.runModal()
    }

    @objc private func quit() { NSApp.terminate(nil) }
}

// Render "ctrl+alt+cmd+r" as "⌃⌥⌘R" for menu titles.
func prettyHotkey(_ spec: String) -> String {
    var out = ""
    var key = ""
    for raw in spec.lowercased().split(separator: "+") {
        switch raw.trimmingCharacters(in: .whitespaces) {
        case "ctrl", "control", "⌃":      out += "⌃"
        case "alt", "opt", "option", "⌥": out += "⌥"
        case "shift", "⇧":                out += "⇧"
        case "cmd", "command", "⌘":       out += "⌘"
        case let other:                   key = other.uppercased()
        }
    }
    return out + key
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // menu-bar only, no Dock icon
app.run()
