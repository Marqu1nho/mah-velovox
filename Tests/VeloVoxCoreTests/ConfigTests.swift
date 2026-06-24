// Tests for the Codable config layer. The critical guard here is the repo's
// config-contract rule: decoding JSON that is MISSING newer fields must still
// succeed and fall back to code defaults — never throw.
import XCTest
import Foundation
@testable import VeloVoxCore

final class ConfigTests: XCTestCase {
    private let dec = JSONDecoder()

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try dec.decode(T.self, from: Data(json.utf8))
    }

    // CleanConfig with only one field present decodes; absent fields are nil and
    // the accessor-side defaults fill in later (see ReadAloudConfig.pipeline()).
    func testCleanConfigPartialDecode() throws {
        let clean = try decode(CleanConfig.self, #"{"rejoin":"never"}"#)
        XCTAssertEqual(clean.rejoin, "never")
        XCTAssertNil(clean.urls)
        XCTAssertNil(clean.split_identifiers)
    }

    // An EMPTY ReadAloud JSON object must decode and resolve a full PipelineConfig
    // from code defaults — this is the config-contract safety net.
    func testReadAloudEmptyDecodesToDefaults() throws {
        let ra = try decode(ReadAloudConfig.self, "{}")
        let p = ra.pipeline()
        XCTAssertEqual(p.cleanRejoin, "smart")
        XCTAssertEqual(p.cleanURLs, "domain")
        XCTAssertEqual(p.cleanPaths, "basename")
        XCTAssertEqual(p.cleanEmoji, "skip")
        XCTAssertTrue(p.splitIdentifiers)
        XCTAssertEqual(p.hRate, 0.85, accuracy: 0.0001)
        XCTAssertEqual(p.commaMs, 150)
        XCTAssertEqual(p.codeMode, "skip")
        XCTAssertEqual(p.announceTemplate, "code block, {lines} lines")
    }

    // ReadAloud accessor defaults (voice/rate/limits).
    func testReadAloudAccessorDefaults() throws {
        let ra = try decode(ReadAloudConfig.self, "{}")
        XCTAssertEqual(ra.voiceSpec, "com.apple.voice.premium.en-GB.Serena")
        XCTAssertEqual(ra.speechRate, 0.5, accuracy: 0.0001)
        XCTAssertEqual(ra.hotkeySpec, "ctrl+alt+cmd+r")
        XCTAssertEqual(ra.maxSelectionChars, 60000)
    }

    // Explicit values override the defaults in the resolved pipeline.
    func testReadAloudExplicitValuesFlowThrough() throws {
        let json = #"""
        {
          "headers": { "rate_factor": 0.5, "pause_before_ms": 100 },
          "pauses": { "comma_ms": 0 },
          "code_blocks": { "mode": "read" },
          "clean": { "urls": "skip", "split_identifiers": false }
        }
        """#
        let p = try decode(ReadAloudConfig.self, json).pipeline()
        XCTAssertEqual(p.hRate, 0.5, accuracy: 0.0001)
        XCTAssertEqual(p.hPauseBefore, 100)
        XCTAssertEqual(p.commaMs, 0)
        XCTAssertEqual(p.codeMode, "read")
        XCTAssertEqual(p.cleanURLs, "skip")
        XCTAssertFalse(p.splitIdentifiers)
        // Untouched fields still fall back.
        XCTAssertEqual(p.cleanPaths, "basename")
        XCTAssertEqual(p.hPauseAfter, 400)
    }

    // SpeakWrite: minimal required fields decode; every newer optional resolves to
    // its accessor default.
    func testSpeakWriteMinimalDecode() throws {
        let json = #"""
        {"locale":"en-GB","hud":{"alpha":0.4,"fontSize":20,"width":500,"height":140},"replacements":[]}
        """#
        let sw = try decode(SpeakWriteConfig.self, json)
        XCTAssertEqual(sw.locale, "en-GB")
        XCTAssertEqual(sw.mode, "hud")
        XCTAssertEqual(sw.engineKind, "speech")
        XCTAssertFalse(sw.dictationPunctuation)
        XCTAssertFalse(sw.dictationEmoji)
        XCTAssertEqual(sw.dictationMode, "formal")
        XCTAssertEqual(sw.dictationCapitalExceptions, ["I"])
        XCTAssertFalse(sw.hudCommitOnly)
        XCTAssertEqual(sw.hotkeySpec, "ctrl+alt+s")
        XCTAssertTrue(sw.cueSound)
        XCTAssertEqual(sw.cueStart, "Tink")
        XCTAssertTrue(sw.metricsEnabled)
    }

    // displayMode aliases canonicalize: orb/minimal→orb, off→off, anything else→hud.
    func testSpeakWriteDisplayModeAliases() throws {
        func mode(_ m: String) throws -> String {
            let json = #"{"locale":"x","displayMode":"\#(m)","hud":{"alpha":0.4,"fontSize":20,"width":500,"height":140},"replacements":[]}"#
            return try decode(SpeakWriteConfig.self, json).mode
        }
        try XCTAssertEqual(mode("orb"), "orb")
        try XCTAssertEqual(mode("minimal"), "orb")
        try XCTAssertEqual(mode("off"), "off")
        try XCTAssertEqual(mode("hud"), "hud")
        try XCTAssertEqual(mode("weird"), "hud")
    }

    // dictation engine + flags flow through.
    func testSpeakWriteDictationEngine() throws {
        let json = #"""
        {"locale":"en-US","engine":"dictation",
         "dictation":{"punctuation":true,"emoji":true,"mode":"casual","capitalExceptions":["I","API"]},
         "hud":{"alpha":0.4,"fontSize":20,"width":500,"height":140},"replacements":[]}
        """#
        let sw = try decode(SpeakWriteConfig.self, json)
        XCTAssertEqual(sw.engineKind, "dictation")
        XCTAssertTrue(sw.dictationPunctuation)
        XCTAssertTrue(sw.dictationEmoji)
        XCTAssertEqual(sw.dictationMode, "casual")
        XCTAssertTrue(sw.dictationCasual)
        XCTAssertEqual(sw.dictationCapitalExceptions, ["I", "API"])
    }

    // replacements is an ORDERED array and preserves insertion order on decode.
    func testSpeakWriteReplacementsArrayOrder() throws {
        let json = #"""
        {"locale":"x","hud":{"alpha":0.4,"fontSize":20,"width":500,"height":140},
         "replacements":[{"say":"new paragraph","insert":"\n\n"},{"say":"cool beans","insert":"🆒"}]}
        """#
        let sw = try decode(SpeakWriteConfig.self, json)
        XCTAssertEqual(sw.replacements.map { $0.say }, ["new paragraph", "cool beans"])
        XCTAssertEqual(sw.replacements[0].insert, "\n\n")
    }

    // Top-level: a malformed/partial speakWrite section falls back to its own
    // defaults WITHOUT nuking a valid readAloud section (per-section decode).
    func testTopLevelPerSectionFallback() throws {
        let cfg = try decode(VeloVoxConfig.self,
                             #"{"readAloud":{"rate":0.7},"speakWrite":{"bogus":1}}"#)
        XCTAssertEqual(cfg.readAloud.speechRate, 0.7, accuracy: 0.0001)
        XCTAssertEqual(cfg.speakWrite.locale, "en-US")      // fell back to default
        XCTAssertEqual(cfg.speakWrite.mode, "hud")
    }

    // An entirely empty top-level object yields both fallback sections.
    func testTopLevelEmptyObjectFallsBack() throws {
        let cfg = try decode(VeloVoxConfig.self, "{}")
        XCTAssertEqual(cfg.readAloud.voiceSpec, ReadAloudConfig.fallback.voiceSpec)
        XCTAssertEqual(cfg.speakWrite.locale, "en-US")
    }

    // Round-trip: encoding the fallback and decoding it back is stable.
    func testFallbackRoundTrips() throws {
        let enc = JSONEncoder()
        let data = try enc.encode(VeloVoxConfig.fallback)
        let back = try dec.decode(VeloVoxConfig.self, from: data)
        XCTAssertEqual(back.speakWrite.locale, VeloVoxConfig.fallback.speakWrite.locale)
        XCTAssertEqual(back.speakWrite.replacements.count,
                       VeloVoxConfig.fallback.speakWrite.replacements.count)
        XCTAssertEqual(back.readAloud.voiceSpec, VeloVoxConfig.fallback.readAloud.voiceSpec)
    }
}
