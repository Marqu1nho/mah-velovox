// Pipeline — resolves the configured knobs into a flat value type the three
// stages read, then chains clean → parse → script. Mirrors readaloud's
// clean(text) → parse(text) → build_script(blocks).
import Foundation

struct PipelineConfig {
    // clean
    var cleanRejoin: String
    var cleanURLs: String
    var cleanPaths: String
    var cleanEmoji: String
    var splitIdentifiers: Bool
    var muteGlobal: [String]
    var muteByApp: [String: [String]]
    var muteBlocks: [String]
    var replace: [String: String]
    // parse
    var treatAllCaps: Bool
    // script — headers
    var hRate: Double
    var hPauseBefore: Int
    var hPauseAfter: Int
    // script — pauses
    var pPara: Int
    var pList: Int
    var pHr: Int
    var commaMs: Int
    // script — code
    var codeMode: String
    var announceTemplate: String
}

enum Pipeline {
    static func chunks(from raw: String, cfg: PipelineConfig, app: String?) -> [Chunk] {
        let cleaned = Clean.clean(raw, cfg, app: app)
        let blocks = Parse.parse(cleaned, cfg)
        return Script.build(blocks, cfg)
    }
}
