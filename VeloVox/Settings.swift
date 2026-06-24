// Settings.swift — VeloVox's native Settings/Preferences window.
//
// VeloVox is a pure-AppKit `LSUIElement`/`.accessory` menu-bar agent (no SwiftUI
// app lifecycle — see main.swift's manual `app.run()` + `.accessory`). This file
// adds a Settings window WITHOUT touching that lifecycle: an AppKit
// `NSWindowController` hosts a SwiftUI `Form` via `NSHostingController`. SwiftUI is
// already linked and used (NSHostingView in SpeakWrite.swift), so this is a proven
// pattern, not new framework risk.
//
// CONFIG ROUND-TRIP: the controls bind straight through to the live global
// `VELOVOX` via explicit get/set Bindings (mirroring SpeakWrite.persistGeometry()).
// A Save button — and save-on-close — calls the existing `VeloVoxConfig.save()`,
// which re-encodes the WHOLE struct, so fields the GUI doesn't expose (replacements,
// mute lists, geometry, …) round-trip untouched.
//
// LAYOUT: Option C — a single scrolling `Form { Section } ` with `.formStyle(.grouped)`.
// Two sections, "Read Aloud" and "Dictate", mirroring the two config sections.
import SwiftUI
import AppKit
import AVFoundation

// Note on `CleanConfig()` / `HeadersConfig()` etc. below: Swift's synthesized
// memberwise initializer supplies an implicit `= nil` default for every optional
// stored property, so the zero-arg form works for all these all-optional structs.
// We use it to MATERIALIZE a sub-struct the first time the GUI writes a leaf, so
// the key lands in the re-encoded JSON (CLAUDE.md "knob visible on disk" rule).

// ===========================================================================
// MARK: - Window controller
// ===========================================================================

/// Hosts `SettingsView` in a standard titled NSWindow. Single shared instance
/// (main.swift lazily creates it once and reuses it). The window hides on close
/// (`isReleasedWhenClosed = false`) and saves the config on the way out.
final class SettingsWindowController: NSWindowController, NSWindowDelegate {
    convenience init() {
        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 600),
            // No `.resizable` — the grouped Form sizes itself and scrolls.
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false)
        win.title = "\(Brand.name) Settings"
        win.isReleasedWhenClosed = false   // closing hides; controller survives for reopen
        win.contentViewController = NSHostingController(rootView: SettingsView())
        win.center()
        self.init(window: win)
        win.delegate = self
    }

    // Persist on close so users don't have to hit Save explicitly. The bindings
    // already mutated VELOVOX live; this just flushes it to disk.
    func windowWillClose(_ notification: Notification) {
        VeloVoxConfig.save()
    }
}

// ===========================================================================
// MARK: - Binding helpers
//
// VELOVOX's sub-structs (clean, headers, dictation, orb, cue, metrics, audio) are
// OPTIONAL. Writing a leaf through the GUI must MATERIALIZE the sub-struct so the
// key is present in the re-encoded JSON (satisfies the CLAUDE.md "knob visible on
// disk" rule). These helpers read via the safety-net accessor and write into a
// freshly-materialized sub-struct when needed.
// ===========================================================================

/// Bind an optional `String?` field to a non-optional control, using `fallback`
/// when the stored value is nil/absent.
private func strBinding(_ get: @escaping () -> String?,
                        _ set: @escaping (String) -> Void,
                        fallback: String) -> Binding<String> {
    Binding(get: { get() ?? fallback }, set: { set($0) })
}

// ===========================================================================
// MARK: - SettingsView
// ===========================================================================

struct SettingsView: View {
    // A monotonically-bumped tick to force SwiftUI to re-read the global VELOVOX
    // after each mutation (the bindings write into a plain global `var`, not an
    // @Published, so we nudge the view ourselves).
    @State private var tick = 0

    // Available TTS voices for the Read-Aloud picker (id + display name).
    private let voices: [(id: String, name: String)] =
        AVSpeechSynthesisVoice.speechVoices()
            .sorted { $0.name < $1.name }
            .map { ($0.identifier, "\($0.name) (\($0.language))") }

    // Known string-enum option sets for the clean pickers.
    private let rejoinOptions = ["smart", "always", "never"]
    private let urlOptions    = ["domain", "full", "skip"]
    private let pathOptions   = ["basename", "full", "skip"]
    private let emojiOptions  = ["skip", "name", "keep"]
    private let engineOptions = ["speech", "dictation"]
    private let displayOptions = ["hud", "orb", "off"]
    private let dictModeOptions = ["formal", "casual"]
    private let orbPositions = ["top-left", "top-center", "top-right",
                                "center-left", "center", "center-right",
                                "bottom-left", "bottom-center", "bottom-right"]
    // A small palette of macOS system-sound names for the cue pickers. "" = silent.
    private let cueSounds = ["", "Tink", "Pop", "Glass", "Purr", "Ping",
                            "Morse", "Bottle", "Frog", "Funk", "Hero", "Submarine"]

    var body: some View {
        Form {
            readAloudSection
            dictateSection
            footerSection
        }
        .formStyle(.grouped)
        .frame(width: 460, height: 600)
        // Re-evaluate body whenever a binding mutates VELOVOX (via tick).
        .id(tick)
    }

    // Force a re-read of the global after a mutation (propagates cross-control
    // dependencies like the engine-gated `.disabled()` states). NOTE: do NOT call
    // this from a TextField's setter — `.id(tick)` rebuilds the view and drops
    // keyboard focus, so a text field that bumps loses focus after each keystroke.
    // Text fields here gate nothing, so they intentionally skip bump().
    private func bump() { tick &+= 1 }

    // ----------------------------------------------------------------------
    // MARK: Read Aloud
    // ----------------------------------------------------------------------
    private var readAloudSection: some View {
        Section("Read Aloud") {
            // Speech rate 0.3–0.7 (AVSpeech rate-factor used by the app).
            VStack(alignment: .leading) {
                Text("Speech rate: \(String(format: "%.2f", VELOVOX.readAloud.rate ?? 0.5))")
                Slider(value: Binding(
                    get: { VELOVOX.readAloud.rate ?? 0.5 },
                    set: { VELOVOX.readAloud.rate = $0; bump() }), in: 0.3...0.7)
            }

            Picker("Voice", selection: Binding(
                get: { VELOVOX.readAloud.voiceSpec },
                set: { VELOVOX.readAloud.voice = $0; bump() })) {
                ForEach(voices, id: \.id) { v in
                    Text(v.name).tag(v.id)
                }
                // Keep the current value selectable even if not in the list.
                if !voices.contains(where: { $0.id == VELOVOX.readAloud.voiceSpec }) {
                    Text(VELOVOX.readAloud.voiceSpec).tag(VELOVOX.readAloud.voiceSpec)
                }
            }

            HStack {
                TextField("Hotkey", text: strBinding(
                    { VELOVOX.readAloud.hotkey },
                    { VELOVOX.readAloud.hotkey = $0 },   // no bump: see bump() note (preserves TextField focus)
                    fallback: VELOVOX.readAloud.hotkeySpec))
                Text(prettyHotkey(VELOVOX.readAloud.hotkeySpec))
                    .foregroundStyle(.secondary)
            }
            Text("Relaunch to apply the hotkey change.")
                .font(.caption).foregroundStyle(.secondary)

            Picker("Rejoin wrapped lines", selection: cleanBinding(
                { $0.rejoin }, { $0.rejoin = $1 }, fallback: "smart")) {
                ForEach(rejoinOptions, id: \.self) { Text($0).tag($0) }
            }
            Picker("URLs", selection: cleanBinding(
                { $0.urls }, { $0.urls = $1 }, fallback: "domain")) {
                ForEach(urlOptions, id: \.self) { Text($0).tag($0) }
            }
            Picker("File paths", selection: cleanBinding(
                { $0.paths }, { $0.paths = $1 }, fallback: "basename")) {
                ForEach(pathOptions, id: \.self) { Text($0).tag($0) }
            }
            Picker("Emoji", selection: cleanBinding(
                { $0.emoji }, { $0.emoji = $1 }, fallback: "skip")) {
                ForEach(emojiOptions, id: \.self) { Text($0).tag($0) }
            }
            Toggle("Split identifiers (camelCase / snake_case)", isOn: Binding(
                get: { VELOVOX.readAloud.clean?.split_identifiers ?? true },
                set: { v in
                    var c = VELOVOX.readAloud.clean ?? CleanConfig()
                    c.split_identifiers = v
                    VELOVOX.readAloud.clean = c; bump()
                }))
            Toggle("Treat ALL-CAPS lines as headers", isOn: Binding(
                get: { VELOVOX.readAloud.headers?.treat_all_caps_lines_as_headers ?? true },
                set: { v in
                    var h = VELOVOX.readAloud.headers ?? HeadersConfig()
                    h.treat_all_caps_lines_as_headers = v
                    VELOVOX.readAloud.headers = h; bump()
                }))
        }
    }

    /// Bind a `CleanConfig` string field, materializing the sub-struct on write.
    private func cleanBinding(_ get: @escaping (CleanConfig) -> String?,
                              _ set: @escaping (inout CleanConfig, String) -> Void,
                              fallback: String) -> Binding<String> {
        Binding(
            get: { get(VELOVOX.readAloud.clean ?? CleanConfig()) ?? fallback },
            set: { v in
                var c = VELOVOX.readAloud.clean ?? CleanConfig()
                set(&c, v)
                VELOVOX.readAloud.clean = c; bump()
            })
    }

    // ----------------------------------------------------------------------
    // MARK: Dictate
    // ----------------------------------------------------------------------
    private var dictateSection: some View {
        let isDictation = VELOVOX.speakWrite.engineKind == "dictation"
        let mode = VELOVOX.speakWrite.mode

        return Section("Dictate") {
            Picker("Engine", selection: Binding(
                get: { VELOVOX.speakWrite.engineKind },
                set: { VELOVOX.speakWrite.engine = $0; bump() })) {
                ForEach(engineOptions, id: \.self) { Text($0).tag($0) }
            }
            Picker("Display mode", selection: Binding(
                get: { VELOVOX.speakWrite.mode },
                set: { VELOVOX.speakWrite.displayMode = $0; bump() })) {
                ForEach(displayOptions, id: \.self) { Text($0).tag($0) }
            }

            HStack {
                TextField("Hotkey", text: strBinding(
                    { VELOVOX.speakWrite.hotkey },
                    { VELOVOX.speakWrite.hotkey = $0 },   // no bump: see bump() note (preserves TextField focus)
                    fallback: VELOVOX.speakWrite.hotkeySpec))
                Text(prettyHotkey(VELOVOX.speakWrite.hotkeySpec))
                    .foregroundStyle(.secondary)
            }
            Text("Relaunch to apply the hotkey change.")
                .font(.caption).foregroundStyle(.secondary)

            TextField("Locale", text: Binding(
                get: { VELOVOX.speakWrite.locale },
                set: { VELOVOX.speakWrite.locale = $0 }))   // no bump: see bump() note (preserves TextField focus)

            // --- dictation-engine-only knobs ---
            Picker("Dictation write mode", selection: dictBindingStr(
                { $0.mode }, { $0.mode = $1 }, fallback: "formal")) {
                ForEach(dictModeOptions, id: \.self) { Text($0).tag($0) }
            }.disabled(!isDictation)
            Toggle("Spoken punctuation", isOn: dictBindingBool(
                { $0.punctuation }, { $0.punctuation = $1 }, fallback: false))
                .disabled(!isDictation)
            Toggle("Spoken emoji", isOn: dictBindingBool(
                { $0.emoji }, { $0.emoji = $1 }, fallback: false))
                .disabled(!isDictation)

            // --- HUD-mode-only knobs ---
            Toggle("HUD: committed text only", isOn: Binding(
                get: { VELOVOX.speakWrite.hudCommitOnly },
                set: { VELOVOX.speakWrite.hud.commitOnly = $0; bump() }))
                .disabled(mode != "hud")
            VStack(alignment: .leading) {
                Text("HUD opacity: \(String(format: "%.2f", VELOVOX.speakWrite.hud.alpha))")
                Slider(value: Binding(
                    get: { VELOVOX.speakWrite.hud.alpha },
                    set: { VELOVOX.speakWrite.hud.alpha = $0; bump() }), in: 0...1)
            }.disabled(mode != "hud")
            VStack(alignment: .leading) {
                Text("HUD font size: \(Int(VELOVOX.speakWrite.hud.fontSize))")
                Slider(value: Binding(
                    get: { VELOVOX.speakWrite.hud.fontSize },
                    set: { VELOVOX.speakWrite.hud.fontSize = $0; bump() }), in: 12...48, step: 1)
                Text("Relaunch to apply font size.")
                    .font(.caption).foregroundStyle(.secondary)
            }.disabled(mode != "hud")

            // --- orb-mode-only knobs ---
            VStack(alignment: .leading) {
                Text("Orb size: \(Int(VELOVOX.speakWrite.orbSize))")
                Slider(value: Binding(
                    get: { Double(VELOVOX.speakWrite.orbSize) },
                    set: { v in
                        var o = VELOVOX.speakWrite.orb ?? OrbConfig(size: 150)
                        o.size = v
                        VELOVOX.speakWrite.orb = o; bump()
                    }), in: 60...300, step: 1)
            }.disabled(mode != "orb")
            Picker("Orb position", selection: Binding(
                get: { VELOVOX.speakWrite.orbPosition },
                set: { v in
                    var o = VELOVOX.speakWrite.orb ?? OrbConfig(size: 150)
                    o.position = v
                    VELOVOX.speakWrite.orb = o; bump()
                })) {
                ForEach(orbPositions, id: \.self) { Text($0).tag($0) }
            }.disabled(mode != "orb")

            // --- cues ---
            Toggle("Cue sounds", isOn: cueBindingBool(
                { $0.sound }, { $0.sound = $1 }, fallback: true))
            Picker("Start cue", selection: cueBindingStr(
                { $0.start }, { $0.start = $1 }, fallback: "Tink")) {
                ForEach(cueSounds, id: \.self) { Text($0.isEmpty ? "(none)" : $0).tag($0) }
            }
            Picker("Stop cue", selection: cueBindingStr(
                { $0.stop }, { $0.stop = $1 }, fallback: "")) {
                ForEach(cueSounds, id: \.self) { Text($0.isEmpty ? "(none)" : $0).tag($0) }
            }
            VStack(alignment: .leading) {
                Text("Cue volume: \(String(format: "%.2f", VELOVOX.speakWrite.cue?.volume ?? 0.5))")
                Slider(value: cueBindingDouble(
                    { $0.volume }, { $0.volume = $1 }, fallback: 0.5), in: 0...1)
            }
            Toggle("Orb bloom on start", isOn: cueBindingBool(
                { $0.bloom }, { $0.bloom = $1 }, fallback: true))

            // --- metrics + audio ---
            Toggle("Log WPM metrics", isOn: Binding(
                get: { VELOVOX.speakWrite.metricsEnabled },
                set: { v in
                    var m = VELOVOX.speakWrite.metrics ?? MetricsConfig()
                    m.enabled = v
                    VELOVOX.speakWrite.metrics = m; bump()
                }))
            Toggle("Show WPM toast", isOn: Binding(
                get: { VELOVOX.speakWrite.metricsFlash },
                set: { v in
                    var m = VELOVOX.speakWrite.metrics ?? MetricsConfig()
                    m.flash = v
                    VELOVOX.speakWrite.metrics = m; bump()
                }))
            Toggle("Warn on Bluetooth mic input", isOn: Binding(
                get: { VELOVOX.speakWrite.warnBluetoothInput },
                set: { v in
                    var a = VELOVOX.speakWrite.audio ?? AudioConfig()
                    a.warnBluetoothInput = v
                    VELOVOX.speakWrite.audio = a; bump()
                }))
        }
    }

    // --- DictationConfig binding helpers (materialize the sub-struct on write) ---
    private func dictBindingStr(_ get: @escaping (DictationConfig) -> String?,
                                _ set: @escaping (inout DictationConfig, String) -> Void,
                                fallback: String) -> Binding<String> {
        Binding(
            get: { get(VELOVOX.speakWrite.dictation ?? DictationConfig()) ?? fallback },
            set: { v in
                var d = VELOVOX.speakWrite.dictation ?? DictationConfig()
                set(&d, v); VELOVOX.speakWrite.dictation = d; bump()
            })
    }
    private func dictBindingBool(_ get: @escaping (DictationConfig) -> Bool?,
                                 _ set: @escaping (inout DictationConfig, Bool) -> Void,
                                 fallback: Bool) -> Binding<Bool> {
        Binding(
            get: { get(VELOVOX.speakWrite.dictation ?? DictationConfig()) ?? fallback },
            set: { v in
                var d = VELOVOX.speakWrite.dictation ?? DictationConfig()
                set(&d, v); VELOVOX.speakWrite.dictation = d; bump()
            })
    }

    // --- CueConfig binding helpers ---
    private func cueBindingStr(_ get: @escaping (CueConfig) -> String?,
                               _ set: @escaping (inout CueConfig, String) -> Void,
                               fallback: String) -> Binding<String> {
        Binding(
            get: { get(VELOVOX.speakWrite.cue ?? CueConfig()) ?? fallback },
            set: { v in
                var c = VELOVOX.speakWrite.cue ?? CueConfig()
                set(&c, v); VELOVOX.speakWrite.cue = c; bump()
            })
    }
    private func cueBindingBool(_ get: @escaping (CueConfig) -> Bool?,
                                _ set: @escaping (inout CueConfig, Bool) -> Void,
                                fallback: Bool) -> Binding<Bool> {
        Binding(
            get: { get(VELOVOX.speakWrite.cue ?? CueConfig()) ?? fallback },
            set: { v in
                var c = VELOVOX.speakWrite.cue ?? CueConfig()
                set(&c, v); VELOVOX.speakWrite.cue = c; bump()
            })
    }
    private func cueBindingDouble(_ get: @escaping (CueConfig) -> Double?,
                                  _ set: @escaping (inout CueConfig, Double) -> Void,
                                  fallback: Double) -> Binding<Double> {
        Binding(
            get: { get(VELOVOX.speakWrite.cue ?? CueConfig()) ?? fallback },
            set: { v in
                var c = VELOVOX.speakWrite.cue ?? CueConfig()
                set(&c, v); VELOVOX.speakWrite.cue = c; bump()
            })
    }

    // ----------------------------------------------------------------------
    // MARK: Footer (Save + caveat)
    // ----------------------------------------------------------------------
    private var footerSection: some View {
        Section {
            HStack {
                Button("Save") { VeloVoxConfig.save() }
                    .keyboardShortcut(.defaultAction)
                Spacer()
            }
            Text("Settings are also saved automatically when this window closes. "
                 + "Saving rewrites config.json in canonical form — any hand-added "
                 + "comments or custom formatting are not preserved.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
