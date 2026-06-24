// Tests for Clean.clean — the noise/terminal scrubbing pass. Exercised through the
// public entry point Clean.clean(_:_:app:) since the rule/matcher helpers are
// private. The plain config disables URL/path/emoji/identifier passes so each test
// isolates the behavior it targets.
import XCTest
import Foundation
@testable import VeloVoxCore

final class CleanTests: XCTestCase {
    private func clean(_ raw: String, _ mutate: (inout PipelineConfig) -> Void = { _ in },
                       app: String? = nil) -> String {
        var cfg = Fixtures.plainPipeline()
        mutate(&cfg)
        return Clean.clean(raw, cfg, app: app)
    }

    // ANSI/VT escape sequences are stripped, leaving the visible text.
    func testStripsAnsiEscapes() {
        XCTAssertEqual(clean("\u{1B}[31mred\u{1B}[0m text"), "red text")
    }

    // A literal global mute rule removes the substring (and the resulting double
    // space collapses).
    func testLiteralMuteRemovesSubstring() {
        XCTAssertEqual(clean("keep SECRET this") { $0.muteGlobal = ["SECRET"] }, "keep this")
    }

    // A "drop-line:" rule removes the whole matching line.
    func testDropLineMuteRemovesLine() {
        let out = clean("line one\nDEBUG noise here\nline two") { $0.muteGlobal = ["drop-line:DEBUG"] }
        XCTAssertFalse(out.contains("DEBUG"))
        XCTAssertTrue(out.contains("line one"))
        XCTAssertTrue(out.contains("line two"))
    }

    // A "re:" rule applies a regex substitution.
    func testRegexMute() {
        XCTAssertEqual(clean("abc 123 def 456") { $0.muteGlobal = ["re:\\d+"] }, "abc def")
    }

    // by_app mute rules apply ONLY for the matching frontmost app.
    func testByAppMuteScoping() {
        let mutate: (inout PipelineConfig) -> Void = { $0.muteByApp = ["Xcode": ["drop-line:warning"]] }
        let matched = clean("ok\nwarning: bad\nfine", mutate, app: "Xcode")
        XCTAssertFalse(matched.contains("warning"))
        // For a different app the rule is inert (the line survives).
        let other = clean("ok\nwarning: bad\nfine", mutate, app: "Safari")
        XCTAssertTrue(other.contains("warning: bad"))
    }

    // A "blocks" rule drops everything from a matching line until the next blank.
    func testMuteBlockDropsUntilBlank() {
        let out = clean("good\n\nTraceback bad\nmore bad\n\nafter") { $0.muteBlocks = ["Traceback"] }
        XCTAssertTrue(out.contains("good"))
        XCTAssertTrue(out.contains("after"))
        XCTAssertFalse(out.contains("Traceback"))
        XCTAssertFalse(out.contains("more bad"))
    }

    // The replace map substitutes the say→insert text.
    func testReplaceMap() {
        XCTAssertEqual(clean("hello btw world") { $0.replace = ["btw": "by the way"] },
                       "hello by the way world")
    }

    // Longer replace keys are applied before shorter ones (most-specific-first).
    func testReplaceLongestKeyFirst() {
        let out = clean("ping the API now") {
            $0.replace = ["API": "A P I", "the API": "the interface"]
        }
        XCTAssertTrue(out.contains("the interface"))
        XCTAssertFalse(out.contains("A P I"))
    }

    // Fenced code is passed through verbatim (not scrubbed) by the clean pass.
    func testFencedCodePassesThrough() {
        let out = clean("intro\n\n```\nlet x = 1\n```\n\nouttro")
        XCTAssertTrue(out.contains("let x = 1"))
    }

    // Rejoin "always" merges a hard-wrapped continuation line into one paragraph.
    func testSmartRejoinMergesWraps() {
        let out = clean("This is a long line that wraps\nonto a second physical line.") {
            $0.cleanRejoin = "always"
        }
        XCTAssertEqual(out, "This is a long line that wraps onto a second physical line.")
    }

    // Empty / whitespace-only input cleans to empty.
    func testEmptyCleansToEmpty() {
        XCTAssertEqual(clean("   \n\n   "), "")
    }
}
