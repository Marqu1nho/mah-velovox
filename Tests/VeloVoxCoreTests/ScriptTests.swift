// Tests for Script.splitSentences — sentence boundary detection, abbreviation
// guarding, and long-sentence (maxChunkChars) splitting.
import XCTest
import Foundation
@testable import VeloVoxCore

final class ScriptTests: XCTestCase {
    func testBasicSentenceSplit() {
        XCTAssertEqual(Script.splitSentences("One. Two! Three?"),
                       ["One.", "Two!", "Three?"])
    }

    // "e.g." / "i.e." mid-sentence must NOT trigger a split.
    func testAbbreviationEGDoesNotSplit() {
        XCTAssertEqual(Script.splitSentences("This is e.g. an example. And another one."),
                       ["This is e.g. an example.", "And another one."])
        XCTAssertEqual(Script.splitSentences("See i.e. this. Next sentence here."),
                       ["See i.e. this.", "Next sentence here."])
    }

    // Title abbreviations ("Dr.", "vs.") also don't split.
    func testTitleAbbreviations() {
        XCTAssertEqual(Script.splitSentences("Dr. Smith arrived. He was late."),
                       ["Dr. Smith arrived.", "He was late."])
        XCTAssertEqual(Script.splitSentences("Cats vs. dogs is the topic."),
                       ["Cats vs. dogs is the topic."])
    }

    func testEmptyAndWhitespace() {
        XCTAssertEqual(Script.splitSentences(""), [])
        XCTAssertEqual(Script.splitSentences("   \n  "), [])
    }

    // A single sentence with no terminal boundary stays one part.
    func testSingleSentenceNoBoundary() {
        XCTAssertEqual(Script.splitSentences("just one clause without a period"),
                       ["just one clause without a period"])
    }

    // A sentence longer than maxChunkChars (500) is hard-split into multiple
    // chunks, each at or below the limit, broken on a word/clause boundary.
    func testLongSentenceSplitsUnderMaxChunkChars() {
        let long = String(repeating: "word ", count: 150) + "end."  // ~754 chars
        let parts = Script.splitSentences(long)
        XCTAssertGreaterThan(parts.count, 1)
        XCTAssertTrue(parts.allSatisfy { $0.count <= 500 },
                      "every chunk must be <= maxChunkChars; got \(parts.map { $0.count })")
        // Recombining the words should preserve all the original tokens.
        let rejoinedWords = parts.joined(separator: " ").split(separator: " ").count
        XCTAssertEqual(rejoinedWords, 151)  // 150 "word" + "end."
    }

    // A short sentence is never split.
    func testShortSentenceNotSplit() {
        XCTAssertEqual(Script.splitSentences("Short and sweet."), ["Short and sweet."])
    }
}
