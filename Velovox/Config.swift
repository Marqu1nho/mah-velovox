// Velovox config — one JSON file at ~/.config/velovox/config.json, decoded via
// Codable (zero dependencies). Two sections, one per tool:
//
//   { "readAloud": { … }, "speakWrite": { … } }
//
// Written with full defaults on first run so there's ALWAYS a file to edit, and a
// malformed section logs + falls back to defaults rather than crashing. Repo rule
// (CLAUDE.md): EVERY knob the code reads must appear in the on-disk file — the code
// default is only the safety net, the file is the contract. So each `fallback`
// below carries every key, and a fresh config.json gets all of them.
//
// Migration: if no velovox config exists yet but the old split SpeakWrite /
// ReadAloud configs do (~/.config/speakwrite, ~/.config/readaloud), their tuned
// values are read and folded into the new merged file on first launch — nobody
// loses their settings to the rename.
import Foundation

// ===========================================================================
// MARK: - ReadAloud section (TTS reader)
// ===========================================================================

struct AlertsConfig: Codable { var y_pct: Double? }
struct LimitsConfig: Codable { var max_selection_chars: Int? }
struct HeadersConfig: Codable {
    var rate_factor: Double?
    var pause_before_ms: Int?
    var pause_after_ms: Int?
    var treat_all_caps_lines_as_headers: Bool?
}
struct PausesConfig: Codable {
    var paragraph_ms: Int?
    var list_item_ms: Int?
    var horizontal_rule_ms: Int?
    var comma_ms: Int?
}
struct CodeBlocksConfig: Codable { var mode: String?; var announce_template: String? }
struct CleanConfig: Codable {
    var rejoin: String?
    var urls: String?
    var paths: String?
    var emoji: String?
    var split_identifiers: Bool?
}
struct MuteConfig: Codable {
    var global: [String]?
    var by_app: [String: [String]]?
    var blocks: [String]?
}

struct ReadAloudConfig: Codable {
    var voice: String?
    var rate: Double?
    var hotkey: String?
    var alerts: AlertsConfig?
    var limits: LimitsConfig?
    var headers: HeadersConfig?
    var pauses: PausesConfig?
    var code_blocks: CodeBlocksConfig?
    var clean: CleanConfig?
    var mute: MuteConfig?
    var replace: [String: String]?

    // --- accessors with safety-net defaults ---
    var voiceSpec: String { voice ?? "com.apple.voice.premium.en-GB.Serena" }
    var speechRate: Float { Float(rate ?? 0.5) }
    var hotkeySpec: String { hotkey ?? "ctrl+alt+cmd+r" }
    var alertYPct: Double { alerts?.y_pct ?? 3.5 }
    var maxSelectionChars: Int { limits?.max_selection_chars ?? 60000 }

    func pipeline() -> PipelineConfig {
        PipelineConfig(
            cleanRejoin: clean?.rejoin ?? "smart",
            cleanURLs: clean?.urls ?? "domain",
            cleanPaths: clean?.paths ?? "basename",
            cleanEmoji: clean?.emoji ?? "skip",
            splitIdentifiers: clean?.split_identifiers ?? true,
            muteGlobal: mute?.global ?? [],
            muteByApp: mute?.by_app ?? [:],
            muteBlocks: mute?.blocks ?? [],
            replace: replace ?? [:],
            treatAllCaps: headers?.treat_all_caps_lines_as_headers ?? true,
            hRate: headers?.rate_factor ?? 0.85,
            hPauseBefore: headers?.pause_before_ms ?? 500,
            hPauseAfter: headers?.pause_after_ms ?? 400,
            pPara: pauses?.paragraph_ms ?? 350,
            pList: pauses?.list_item_ms ?? 200,
            pHr: pauses?.horizontal_rule_ms ?? 600,
            commaMs: pauses?.comma_ms ?? 150,
            codeMode: code_blocks?.mode ?? "skip",
            announceTemplate: code_blocks?.announce_template ?? "code block, {lines} lines"
        )
    }

    static let fallback = ReadAloudConfig(
        voice: "com.apple.voice.premium.en-GB.Serena",
        rate: 0.5,
        hotkey: "ctrl+alt+cmd+r",
        alerts: AlertsConfig(y_pct: 3.5),
        limits: LimitsConfig(max_selection_chars: 60000),
        headers: HeadersConfig(rate_factor: 0.85, pause_before_ms: 500,
                               pause_after_ms: 400, treat_all_caps_lines_as_headers: true),
        pauses: PausesConfig(paragraph_ms: 350, list_item_ms: 200,
                             horizontal_rule_ms: 600, comma_ms: 150),
        code_blocks: CodeBlocksConfig(mode: "skip", announce_template: "code block, {lines} lines"),
        clean: CleanConfig(rejoin: "smart", urls: "domain", paths: "basename", emoji: "skip", split_identifiers: true),
        mute: MuteConfig(global: [], by_app: [:], blocks: []),
        replace: [:]
    )
}

// ===========================================================================
// MARK: - SpeakWrite section (dictation)
// ===========================================================================

// `replacements` is an ARRAY (not an object) on purpose: order matters — the
// dictionary is applied most-specific-first — and JSON objects don't preserve
// key order when decoded. `\n` is a native JSON escape, so newlines just work.
struct Replacement: Codable { let say: String; let insert: String }

// TEXT-mode geometry. x/y optional: absent → bottom-center; written when you drag/resize.
struct HUDConfig: Codable {
    var alpha: Double; var fontSize: Double; var width: Double; var height: Double
    var x: Double? = nil; var y: Double? = nil
    // commitOnly: when true the HUD NEVER shows the dim volatile/interim text —
    // only finalized (committed) words appear, one color.
    var commitOnly: Bool? = nil
}
// MINIMAL-mode (orb) config — kept SEPARATE from HUDConfig so the two modes never
// borrow each other's geometry.
struct OrbConfig: Codable {
    var size: Double
    var position: String? = nil   // 9-grid anchor: top-left … center … bottom-right
}
// Start/stop CUES. `sound` is the master switch; `start`/`stop` are macOS
// system-sound NAMES (Tink, Pop, Glass, Purr, Ping, …) — empty/absent = silent.
// `bloom` makes the orb breathe once on start.
struct CueConfig: Codable {
    var sound: Bool? = nil
    var start: String? = nil
    var stop: String? = nil
    var volume: Double? = nil   // 0…1
    var bloom: Bool? = nil
}
// Smart WPM metrics. We measure SPEAKING time, not recording time.
struct MetricsConfig: Codable {
    var enabled: Bool? = nil
    var silenceGraceSeconds: Double? = nil
    var voiceThreshold: Double? = nil
    var flash: Bool? = nil           // brief "142 wpm" toast after each session
}
// Audio-route nudges. `warnBluetoothInput`: flash a one-time nudge when the mic is
// a Bluetooth device (forced into mono ~16 kHz "call mode").
struct AudioConfig: Codable {
    var warnBluetoothInput: Bool? = nil
}
// Tuning for the "dictation" engine (DictationTranscriber). Both flags are opt-in
// on Apple's side.
struct DictationConfig: Codable {
    var punctuation: Bool? = nil
    var emoji: Bool? = nil
    // Write mode: "formal" (default, engine casing untouched) or "casual".
    var mode: String? = nil
    var capitalExceptions: [String]? = nil
}

struct SpeakWriteConfig: Codable {
    var locale: String
    var engine: String? = nil        // "speech" (default, auto-punctuated) or "dictation"
    var dictation: DictationConfig? = nil
    var displayMode: String? = nil   // "hud" (default), "orb", or "off"
    var hotkey: String? = nil        // e.g. "ctrl+alt+s"; default if absent
    var hud: HUDConfig
    var orb: OrbConfig? = nil
    var cue: CueConfig? = nil
    var metrics: MetricsConfig? = nil
    var audio: AudioConfig? = nil
    var replacements: [Replacement]

    // Canonical mode. Accepts new names (hud/orb) and old ones (text/minimal).
    var mode: String {
        switch displayMode {
        case "orb", "minimal": return "orb"
        case "off":            return "off"
        default:               return "hud"
        }
    }
    var orbMode: Bool { mode == "orb" }
    var orbSize: CGFloat { CGFloat(orb?.size ?? 150) }
    var orbPosition: String { orb?.position ?? "center" }
    var hotkeySpec: String { hotkey ?? "ctrl+alt+s" }
    var cueSound: Bool { cue?.sound ?? true }
    var cueStart: String { cue?.start ?? "Tink" }   // subtle "you can talk now"
    var cueStop: String? { cue?.stop }              // nil/empty → silent on paste
    var cueVolume: Float { Float(cue?.volume ?? 0.5) }
    var cueBloom: Bool { cue?.bloom ?? true }
    var hudCommitOnly: Bool { hud.commitOnly ?? false }
    // "speech" → SpeechTranscriber (auto-punctuation); "dictation" → DictationTranscriber.
    var engineKind: String { engine == "dictation" ? "dictation" : "speech" }
    var dictationPunctuation: Bool { dictation?.punctuation ?? false }
    var dictationEmoji: Bool { dictation?.emoji ?? false }
    var dictationMode: String { dictation?.mode ?? "formal" }
    var dictationCasual: Bool { dictationMode == "casual" }
    var dictationCapitalExceptions: [String] { dictation?.capitalExceptions ?? ["I"] }
    var metricsEnabled: Bool { metrics?.enabled ?? true }
    var metricSilenceGrace: Double { metrics?.silenceGraceSeconds ?? 1.0 }
    var metricVoiceThreshold: Double { metrics?.voiceThreshold ?? 0.15 }
    var metricsFlash: Bool { metrics?.flash ?? true }
    var warnBluetoothInput: Bool { audio?.warnBluetoothInput ?? true }

    static let fallback = SpeakWriteConfig(
        locale: "en-US",
        engine: "speech",
        dictation: DictationConfig(punctuation: false, emoji: false, mode: "formal", capitalExceptions: ["I"]),
        displayMode: "hud",
        hotkey: "ctrl+alt+s",
        hud: HUDConfig(alpha: 0.5, fontSize: 22, width: 560, height: 160, commitOnly: false),
        orb: OrbConfig(size: 150, position: "center"),
        cue: CueConfig(sound: true, start: "Tink", stop: "", volume: 0.5, bloom: true),
        metrics: MetricsConfig(enabled: true, silenceGraceSeconds: 1.0, voiceThreshold: 0.15, flash: true),
        audio: AudioConfig(warnBluetoothInput: true),
        replacements: [
            Replacement(say: "new paragraph", insert: "\n\n"),
            Replacement(say: "new line", insert: "\n"),
            Replacement(say: "cool beans", insert: "🆒🫘"),
        ])
}

// ===========================================================================
// MARK: - Top-level Velovox config (the on-disk file)
// ===========================================================================

struct VelovoxConfig: Codable {
    var readAloud: ReadAloudConfig
    var speakWrite: SpeakWriteConfig

    enum CodingKeys: String, CodingKey { case readAloud, speakWrite }

    init(readAloud: ReadAloudConfig, speakWrite: SpeakWriteConfig) {
        self.readAloud = readAloud
        self.speakWrite = speakWrite
    }

    // Decode each section independently so a malformed/partial section falls back
    // to its own defaults instead of nuking the whole file.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        readAloud = (try? c.decode(ReadAloudConfig.self, forKey: .readAloud)) ?? .fallback
        speakWrite = (try? c.decode(SpeakWriteConfig.self, forKey: .speakWrite)) ?? .fallback
    }

    static let fallback = VelovoxConfig(readAloud: .fallback, speakWrite: .fallback)

    // ~/.config/velovox/config.json — the one file to rule them both.
    static var fileURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/velovox/config.json")
    }
    private static var oldReadAloudURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/readaloud/config.json")
    }
    private static var oldSpeakWriteURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/speakwrite/config.json")
    }

    static func load() -> VelovoxConfig {
        let fm = FileManager.default
        let url = fileURL

        if !fm.fileExists(atPath: url.path) {
            // First run under the new name. Salvage the user's tuned settings from
            // the old split configs if they exist, else write full defaults.
            let ra = decodeOld(ReadAloudConfig.self, from: oldReadAloudURL)
            let sw = decodeOld(SpeakWriteConfig.self, from: oldSpeakWriteURL)
            let migrated = (ra != nil || sw != nil)
            let cfg = VelovoxConfig(readAloud: ra ?? .fallback, speakWrite: sw ?? .fallback)
            write(cfg, to: url)
            NSLog("velovox: \(migrated ? "MIGRATED old configs into" : "wrote default config ->") \(url.path)")
            return cfg
        }
        do {
            let cfg = try JSONDecoder().decode(VelovoxConfig.self, from: Data(contentsOf: url))
            NSLog("velovox: loaded config <- \(url.path) (\(cfg.speakWrite.replacements.count) replacements)")
            return cfg
        } catch {
            NSLog("velovox: BAD config (\(error)); using defaults. Fix \(url.path)")
            return .fallback
        }
    }

    // Persist the live config (called when the SpeakWrite HUD is moved/resized).
    // Re-encodes the WHOLE file so the readAloud section is never clobbered.
    static func save() {
        write(VELOVOX, to: fileURL)
    }

    private static func decodeOld<T: Decodable>(_ type: T.Type, from url: URL) -> T? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    private static func write(_ cfg: VelovoxConfig, to url: URL) {
        let fm = FileManager.default
        try? fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes, .sortedKeys]
        if let data = try? e.encode(cfg) {
            do { try data.write(to: url) }
            catch { NSLog("velovox: config save failed \(error)") }
        }
    }
}

var VELOVOX = VelovoxConfig.load()
