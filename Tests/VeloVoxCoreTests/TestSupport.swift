// Shared fixtures for the VeloVoxCore test suites.
import Foundation
@testable import VeloVoxCore

enum Fixtures {
    /// A baseline PipelineConfig mirroring the app's ReadAloud defaults, so tests
    /// exercise the same knob values the user actually runs with. Individual tests
    /// copy this and tweak just the field under test.
    static func defaultPipeline() -> PipelineConfig {
        PipelineConfig(
            cleanRejoin: "smart",
            cleanURLs: "domain",
            cleanPaths: "basename",
            cleanEmoji: "skip",
            splitIdentifiers: true,
            muteGlobal: [],
            muteByApp: [:],
            muteBlocks: [],
            replace: [:],
            treatAllCaps: true,
            hRate: 0.85,
            hPauseBefore: 500,
            hPauseAfter: 400,
            pPara: 350,
            pList: 200,
            pHr: 600,
            commaMs: 150,
            codeMode: "skip",
            announceTemplate: "code block, {lines} lines"
        )
    }

    /// Same baseline but with the "noise" passes disabled, so a test that only
    /// cares about block structure isn't surprised by URL/path/emoji rewriting,
    /// comma-pausing, or identifier splitting.
    static func plainPipeline() -> PipelineConfig {
        var cfg = defaultPipeline()
        cfg.cleanURLs = "full"
        cfg.cleanPaths = "full"
        cfg.cleanEmoji = "skip"
        cfg.splitIdentifiers = false
        cfg.commaMs = 0
        return cfg
    }
}
