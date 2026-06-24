// End-to-end tests of the pure clean→parse→script pipeline via Pipeline.chunks.
// Each test asserts on the resulting [Chunk] (text / kind / pauses / rate) for a
// representative kind of input.
import XCTest
import Foundation
@testable import VeloVoxCore

final class PipelineTests: XCTestCase {
    // A header carries the configured rate + before/after pauses; following prose
    // becomes a paragraph chunk with the paragraph pause.
    func testHeaderThenParagraph() {
        let chunks = Pipeline.chunks(from: "# Hello World\n\nSome text.",
                                     cfg: Fixtures.defaultPipeline(), app: nil)
        XCTAssertEqual(chunks.count, 2)
        XCTAssertEqual(chunks[0].kind, "header")
        XCTAssertEqual(chunks[0].text, "Hello World")
        XCTAssertEqual(chunks[0].rate_factor, 0.85, accuracy: 0.0001)
        XCTAssertEqual(chunks[0].pause_before_ms, 500)
        XCTAssertEqual(chunks[0].pause_after_ms, 400)
        XCTAssertEqual(chunks[1].kind, "paragraph")
        XCTAssertEqual(chunks[1].text, "Some text.")
        XCTAssertEqual(chunks[1].pause_after_ms, 350)
    }

    // Each bullet becomes its own list_item chunk with the list pause.
    func testBulletList() {
        let chunks = Pipeline.chunks(from: "- first item\n- second item\n- third",
                                     cfg: Fixtures.defaultPipeline(), app: nil)
        XCTAssertEqual(chunks.map { $0.kind }, ["list_item", "list_item", "list_item"])
        XCTAssertEqual(chunks.map { $0.text }, ["first item", "second item", "third"])
        XCTAssertTrue(chunks.allSatisfy { $0.pause_after_ms == 200 })
    }

    // With the ReadAloud default code mode ("skip"), a fenced block is NOT read or
    // dropped — it is announced ("code block, N lines") with the body line count
    // (fence lines excluded).
    func testCodeBlockAnnounceIsDefaultForSkipMode() {
        let chunks = Pipeline.chunks(from: "intro\n\n```swift\nlet x = 1\nlet y = 2\n```\n\nafter",
                                     cfg: Fixtures.defaultPipeline(), app: nil)
        XCTAssertEqual(chunks.map { $0.kind }, ["paragraph", "code_announce", "paragraph"])
        XCTAssertEqual(chunks[1].text, "code block, 2 lines")
    }

    // codeMode "silent-skip" drops the block entirely; "read" emits its body verbatim.
    func testCodeBlockSilentSkipAndRead() {
        var skipCfg = Fixtures.defaultPipeline(); skipCfg.codeMode = "silent-skip"
        let skipped = Pipeline.chunks(from: "before\n\n```\na\nb\n```\n\nafter", cfg: skipCfg, app: nil)
        XCTAssertEqual(skipped.map { $0.kind }, ["paragraph", "paragraph"])

        var readCfg = Fixtures.defaultPipeline(); readCfg.codeMode = "read"
        let read = Pipeline.chunks(from: "```\na\nb\n```", cfg: readCfg, app: nil)
        XCTAssertEqual(read.count, 1)
        XCTAssertEqual(read[0].kind, "code")
        XCTAssertEqual(read[0].text, "a\nb")
    }

    // announce template line-count substitution.
    func testCodeAnnounceTemplate() {
        var cfg = Fixtures.defaultPipeline(); cfg.codeMode = "announce"
        let chunks = Pipeline.chunks(from: "```\na\nb\nc\n```", cfg: cfg, app: nil)
        XCTAssertEqual(chunks.count, 1)
        XCTAssertEqual(chunks[0].kind, "code_announce")
        XCTAssertEqual(chunks[0].text, "code block, 3 lines")
    }

    // URLs collapse to "link to <domain>" (www stripped) and paths to their
    // basename under the default domain/basename modes.
    func testURLAndPathRewriting() {
        let chunks = Pipeline.chunks(from: "See https://www.example.com/foo and /Users/bob/file.txt here.",
                                     cfg: Fixtures.defaultPipeline(), app: nil)
        XCTAssertEqual(chunks.count, 1)
        XCTAssertEqual(chunks[0].text, "See link to example.com and file.txt here.")
    }

    // emoji "skip" deletes the glyph; "name" replaces it with its Unicode name.
    func testEmojiModes() {
        var skip = Fixtures.defaultPipeline(); skip.cleanEmoji = "skip"
        XCTAssertEqual(Pipeline.chunks(from: "hello 🎉 world", cfg: skip, app: nil)[0].text,
                       "hello world")

        var name = Fixtures.defaultPipeline(); name.cleanEmoji = "name"
        XCTAssertEqual(Pipeline.chunks(from: "hi 🎉 yo", cfg: name, app: nil)[0].text,
                       "hi party popper yo")
    }

    // A sentence with commas is split into clause chunks, each carrying the comma
    // pause; the final clause carries the terminal paragraph pause instead.
    func testCommaClauseSplitting() {
        let chunks = Pipeline.chunks(from: "First, second, and third.",
                                     cfg: Fixtures.defaultPipeline(), app: nil)
        XCTAssertEqual(chunks.map { $0.text }, ["First,", "second,", "and third."])
        XCTAssertEqual(chunks[0].pause_after_ms, 150)
        XCTAssertEqual(chunks[1].pause_after_ms, 150)
        XCTAssertEqual(chunks[2].pause_after_ms, 350)
    }

    // commaMs == 0 disables clause splitting: the whole sentence stays one chunk.
    func testCommaSplittingDisabled() {
        var cfg = Fixtures.defaultPipeline(); cfg.commaMs = 0
        let chunks = Pipeline.chunks(from: "First, second, and third.", cfg: cfg, app: nil)
        XCTAssertEqual(chunks.count, 1)
        XCTAssertEqual(chunks[0].text, "First, second, and third.")
    }

    // splitIdentifiers breaks camelCase and snake_case so the TTS reads words.
    func testIdentifierSplitting() {
        var on = Fixtures.defaultPipeline(); on.splitIdentifiers = true
        XCTAssertEqual(Pipeline.chunks(from: "The kAudioDeviceProperty snake_case here.", cfg: on, app: nil)[0].text,
                       "The k Audio Device Property snake case here.")

        var off = Fixtures.defaultPipeline(); off.splitIdentifiers = false
        XCTAssertEqual(Pipeline.chunks(from: "The kAudioDeviceProperty snake_case here.", cfg: off, app: nil)[0].text,
                       "The kAudioDeviceProperty snake_case here.")
    }

    // A horizontal rule becomes an empty hr chunk carrying the configured pause.
    func testHorizontalRule() {
        let chunks = Pipeline.chunks(from: "above\n\n---\n\nbelow",
                                     cfg: Fixtures.defaultPipeline(), app: nil)
        XCTAssertEqual(chunks.map { $0.kind }, ["paragraph", "hr", "paragraph"])
        XCTAssertEqual(chunks[1].text, "")
        XCTAssertEqual(chunks[1].pause_after_ms, 600)
    }

    // An ALL-CAPS line surrounded by blanks is promoted to a header when
    // treatAllCaps is on, and stays prose when it's off.
    func testAllCapsHeaderPromotion() {
        var on = Fixtures.defaultPipeline(); on.treatAllCaps = true
        let promoted = Pipeline.chunks(from: "INTRODUCTION\n\nbody text here.", cfg: on, app: nil)
        XCTAssertEqual(promoted[0].kind, "header")
        XCTAssertEqual(promoted[0].text, "INTRODUCTION")

        var off = Fixtures.defaultPipeline(); off.treatAllCaps = false
        let plain = Pipeline.chunks(from: "INTRODUCTION\n\nbody text here.", cfg: off, app: nil)
        XCTAssertEqual(plain[0].kind, "paragraph")
    }

    // A markdown table parses into a `table` block (first row = header), then
    // Script emits one chunk per body row as "col: value" pairs. Tested via
    // Parse+Script directly: the Clean pass is intentionally destructive to the
    // `---` separator row, so a table is exercised at the structural layer.
    func testTableParseAndScript() {
        let md = "| Name | Age |\n| --- | --- |\n| Bob | 30 |\n| Sue | 41 |"
        let blocks = Parse.parse(md, Fixtures.defaultPipeline())
        XCTAssertEqual(blocks.count, 1)
        XCTAssertEqual(blocks[0].kind, "table")
        XCTAssertEqual(blocks[0].rows, [["Name", "Age"], ["Bob", "30"], ["Sue", "41"]])

        let chunks = Script.build(blocks, Fixtures.defaultPipeline())
        XCTAssertEqual(chunks.map { $0.kind }, ["table", "table"])
        XCTAssertEqual(chunks[0].text, "Name: Bob, Age: 30")
        XCTAssertEqual(chunks[1].text, "Name: Sue, Age: 41")
        XCTAssertTrue(chunks.allSatisfy { $0.pause_after_ms == 200 })
    }

    // Empty / whitespace-only input yields no chunks.
    func testEmptyInput() {
        XCTAssertTrue(Pipeline.chunks(from: "   \n\n  ", cfg: Fixtures.defaultPipeline(), app: nil).isEmpty)
    }
}
