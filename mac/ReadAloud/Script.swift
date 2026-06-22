// Port of readaloud/script.py — build a flat speech script (list of Chunk) from
// parsed Blocks. Long paragraphs are sentence-split so stop feels instant.
import Foundation

struct Chunk: Codable {
    var text: String
    var kind: String
    var rate_factor: Double = 1.0
    var pause_before_ms: Int = 0
    var pause_after_ms: Int = 0
}

enum Script {
    private static let sentenceBoundary = RE(#"[.!?]["')\]”’]*\s+(?=["'(\[“‘]?[A-Z0-9])"#)
    private static let clauseBoundary = RE(#"([,;:])(\s+)"#)
    private static let abbreviations: Set<String> = [
        "e.g", "i.e", "etc", "vs", "cf", "mr", "mrs", "ms", "dr",
        "prof", "st", "no", "fig", "approx", "ca", "al",
    ]
    private static let maxChunkChars = 500

    private static func endsWithAbbreviation(_ text: String, _ dotIdx: String.Index) -> Bool {
        var wordStart = dotIdx
        while wordStart > text.startIndex {
            let prev = text.index(before: wordStart)
            let c = text[prev]
            if c.isLetter || c.isNumber || c == "." { wordStart = prev } else { break }
        }
        let word = String(text[wordStart..<dotIdx]).lowercased().drop(while: { $0 == "." })
        return abbreviations.contains(String(word))
    }

    static func splitSentences(_ text0: String) -> [String] {
        let text = text0.pyStrip()
        if text.isEmpty { return [] }
        var parts: [String] = []
        var start = text.startIndex
        for m in sentenceBoundary.allMatches(text) {
            guard let r = Range(m.range, in: text) else { continue }
            let punct = text[r.lowerBound]
            if punct == "." && endsWithAbbreviation(text, r.lowerBound) { continue }
            parts.append(String(text[start..<r.upperBound]).pyStrip())
            start = r.upperBound
        }
        if start < text.endIndex { parts.append(String(text[start...]).pyStrip()) }
        parts = parts.filter { !$0.isEmpty }

        var out: [String] = []
        for p0 in parts {
            var part = p0
            while part.count > maxChunkChars {
                let limit = part.index(part.startIndex, offsetBy: maxChunkChars)
                let head = part[..<limit]
                let cutIdx = [", ", "; ", " "].compactMap { sep in
                    head.range(of: sep, options: .backwards)?.lowerBound
                }.max()
                let cut = cutIdx ?? limit
                // include the char at `cut` (Python part[:cut+1]); for a found sep
                // that's the punctuation/space, then strip trailing , ;
                let after = part.index(after: cut)
                out.append(String(part[..<after]).pyStrip().pyRStrip([",", ";"]))
                part = String(part[after...]).pyStrip()
            }
            if !part.isEmpty { out.append(part) }
        }
        return out
    }

    private static func splitClauses(_ sentence: String, _ commaMs: Int) -> [(String, Int)] {
        if commaMs <= 0 { return [(sentence, 0)] }
        var parts: [String] = []
        var last = sentence.startIndex
        for m in clauseBoundary.allMatches(sentence) {
            // group(2) is the whitespace run; split before it so the punctuation
            // mark stays with the preceding clause.
            guard m.numberOfRanges >= 3,
                  let wsRange = Range(m.range(at: 2), in: sentence) else { continue }
            parts.append(String(sentence[last..<wsRange.lowerBound]))
            last = wsRange.upperBound
        }
        if last < sentence.endIndex { parts.append(String(sentence[last...])) }
        parts = parts.filter { !$0.isEmpty }
        if parts.count <= 1 { return [(sentence, 0)] }
        return parts.dropLast().map { ($0, commaMs) } + [(parts.last!, 0)]
    }

    static func build(_ blocks: [Block], _ cfg: PipelineConfig) -> [Chunk] {
        var chunks: [Chunk] = []
        for block in blocks {
            switch block.kind {
            case "header":
                if block.text.isEmpty { continue }
                chunks.append(Chunk(text: block.text, kind: "header",
                                    rate_factor: cfg.hRate,
                                    pause_before_ms: cfg.hPauseBefore,
                                    pause_after_ms: cfg.hPauseAfter))

            case "paragraph":
                appendProse(&chunks, block.text, kind: "paragraph", terminal: cfg.pPara, cfg: cfg, fallbackSelf: false)

            case "list_item":
                if block.text.isEmpty { continue }
                appendProse(&chunks, block.text, kind: "list_item", terminal: cfg.pList, cfg: cfg, fallbackSelf: true)

            case "blockquote":
                if block.text.isEmpty { continue }
                appendProse(&chunks, block.text, kind: "blockquote", terminal: cfg.pPara, cfg: cfg, fallbackSelf: true)

            case "hr":
                chunks.append(Chunk(text: "", kind: "hr", pause_after_ms: cfg.pHr))

            case "table":
                chunks.append(contentsOf: tableChunks(block, cfg.pList))

            case "code":
                let nLines = block.lines.count
                if cfg.codeMode == "silent-skip" { continue }
                if cfg.codeMode == "read" {
                    let body = block.lines.joined(separator: "\n").pyStrip()
                    if !body.isEmpty { chunks.append(Chunk(text: body, kind: "code", pause_after_ms: cfg.pPara)) }
                    continue
                }
                let announce = cfg.announceTemplate.replacingOccurrences(of: "{lines}", with: "\(nLines)")
                chunks.append(Chunk(text: announce, kind: "code_announce", pause_after_ms: cfg.pPara))

            default:
                continue
            }
        }
        return chunks
    }

    // Shared paragraph/list/blockquote expansion: sentences → clauses → chunks.
    private static func appendProse(_ chunks: inout [Chunk], _ text: String, kind: String,
                                    terminal: Int, cfg: PipelineConfig, fallbackSelf: Bool) {
        var sentences = splitSentences(text)
        if sentences.isEmpty && fallbackSelf { sentences = [text] }
        for (idx, sent) in sentences.enumerated() {
            let lastSent = idx == sentences.count - 1
            let terminalPause = lastSent ? terminal : 0
            let clauses = splitClauses(sent, cfg.commaMs)
            for (cIdx, clause) in clauses.enumerated() {
                let lastClause = cIdx == clauses.count - 1
                chunks.append(Chunk(text: clause.0, kind: kind, rate_factor: 1.0,
                                    pause_after_ms: lastClause ? terminalPause : clause.1))
            }
        }
    }

    private static func tableChunks(_ block: Block, _ pauseMs: Int) -> [Chunk] {
        let rows = block.rows
        if rows.isEmpty { return [] }
        let header = rows[0]
        let body = Array(rows.dropFirst())
        if body.isEmpty {
            return [Chunk(text: header.filter { !$0.isEmpty }.joined(separator: ", "), kind: "table", pause_after_ms: pauseMs)]
        }
        var out: [Chunk] = []
        for row in body {
            var pairs: [String] = []
            for (ci, cell) in row.enumerated() {
                if cell.isEmpty { continue }
                let col = ci < header.count ? header[ci] : ""
                pairs.append(col.isEmpty ? cell : "\(col): \(cell)")
            }
            if !pairs.isEmpty {
                out.append(Chunk(text: pairs.joined(separator: ", "), kind: "table", pause_after_ms: pauseMs))
            }
        }
        return out
    }
}
