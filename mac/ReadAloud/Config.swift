// Config — JSON at ~/.config/readaloud/config.json, decoded via Codable (zero
// dependencies). Written with defaults on first run so there's ALWAYS a file to
// edit. Repo rule (CLAUDE.md): EVERY knob the code reads must appear in the on-disk
// file — the code default is only the safety net, the file is the contract. So the
// `fallback` below carries every key, and a fresh config.json gets all of them.
import Foundation

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

struct Config: Codable {
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

    static let fallback = Config(
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

    private static var fileURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/readaloud/config.json")
    }

    static func load() -> Config {
        let fm = FileManager.default
        let url = fileURL
        if !fm.fileExists(atPath: url.path) {
            try? fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            if let data = try? encoder().encode(fallback) { try? data.write(to: url) }
            NSLog("readaloud: wrote default config -> \(url.path)")
            return fallback
        }
        do {
            let cfg = try JSONDecoder().decode(Config.self, from: Data(contentsOf: url))
            NSLog("readaloud: loaded config <- \(url.path)")
            return cfg
        } catch {
            NSLog("readaloud: BAD config (\(error)); using defaults. Fix \(url.path)")
            return fallback
        }
    }

    private static func encoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes, .sortedKeys]
        return e
    }
}

var CONFIG = Config.load()
