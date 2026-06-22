// Port of readaloud/parse.py — lightweight, line-based markdown structure pass.
// Emits a flat list of Block for Script to turn into a speech script.
import Foundation

struct Block {
    var kind: String          // header | paragraph | list_item | code | blockquote | table | hr
    var text: String = ""
    var level: Int = 0
    var lines: [String] = []
    var rows: [[String]] = []
}

enum Parse {
    private static let fence = RE(#"^\s*(`{3,}|~{3,})"#)
    private static let header = RE(#"^\s*(#{1,6})\s+(.*)$"#)
    private static let bullet = RE(#"^(\s*)[-*+]\s+(.*)$"#)
    private static let ordered = RE(#"^(\s*)\d+[.)]\s+(.*)$"#)
    private static let blockquote = RE(#"^\s*>\s?(.*)$"#)
    private static let hr = RE(#"^\s*(?:[-*_])\s*(?:[-*_]\s*){2,}$"#)
    private static let tableRow = RE(#"^\s*\|.*\|\s*$"#)
    private static let tableSep = RE(#"^\s*\|?[\s:|-]+\|[\s:|-]+\|?\s*$"#)
    private static let allcaps = RE(#"^[A-Z0-9][A-Z0-9 \-_/&.,()']*$"#)

    private static func looksLikeAllcapsHeader(_ line: String) -> Bool {
        let s = line.pyStrip()
        if s.count < 2 || s.count > 60 { return false }
        if !allcaps.match(s) { return false }
        return s.filter { $0.isLetter }.count >= 2
    }

    private static func splitTableRow(_ line: String) -> [String] {
        line.pyStrip().pyStrip(["|"]).components(separatedBy: "|").map { $0.pyStrip() }
    }

    static func parse(_ text: String, _ cfg: PipelineConfig) -> [Block] {
        let allcapsHeaders = cfg.treatAllCaps
        let lines = text.splitLines()
        let n = lines.count
        var blocks: [Block] = []
        var i = 0
        var paraBuf: [String] = []

        func flushPara() {
            if !paraBuf.isEmpty {
                let joined = stripInline(paraBuf.map { $0.pyStrip() }.filter { !$0.isEmpty }.joined(separator: " "))
                if !joined.isEmpty { blocks.append(Block(kind: "paragraph", text: joined)) }
                paraBuf = []
            }
        }

        while i < n {
            let line = lines[i]

            // Fenced code block.
            if let f = fence.firstMatch(line) {
                flushPara()
                let marker = String(f.group(1, in: line).first!)
                var body: [String] = []
                i += 1
                let close = RE("^\\s*\(NSRegularExpression.escapedPattern(for: marker)){3,}\\s*$")
                while i < n {
                    if close.match(lines[i]) { i += 1; break }
                    body.append(lines[i])
                    i += 1
                }
                blocks.append(Block(kind: "code", lines: body))
                continue
            }

            // Blank line -> paragraph boundary.
            if line.pyStrip().isEmpty { flushPara(); i += 1; continue }

            // Horizontal rule.
            if hr.match(line) { flushPara(); blocks.append(Block(kind: "hr")); i += 1; continue }

            // ATX header.
            if let h = header.firstMatch(line) {
                flushPara()
                let level = h.group(1, in: line).count
                blocks.append(Block(kind: "header", text: stripInline(h.group(2, in: line)), level: level))
                i += 1
                continue
            }

            // Table: a pipe row followed by more pipe rows.
            if tableRow.match(line) {
                var tableLines = [line]
                var j = i + 1
                while j < n && tableRow.match(lines[j]) { tableLines.append(lines[j]); j += 1 }
                if tableLines.count >= 2 {
                    flushPara()
                    let rows = tableLines.filter { !tableSep.match($0) }.map { splitTableRow($0) }
                    blocks.append(Block(kind: "table", rows: rows))
                    i = j
                    continue
                }
                // else fall through, treat as paragraph text
            }

            // List item (bullet or ordered).
            if let m = bullet.firstMatch(line) ?? ordered.firstMatch(line) {
                flushPara()
                var content = m.group(2, in: line)
                i += 1
                while i < n && !lines[i].pyStrip().isEmpty {
                    let nxt = lines[i]
                    if bullet.match(nxt) || ordered.match(nxt) || header.match(nxt) || fence.match(nxt) || hr.match(nxt) {
                        break
                    }
                    content += " " + nxt.pyStrip()
                    i += 1
                }
                blocks.append(Block(kind: "list_item", text: stripInline(content)))
                continue
            }

            // Blockquote.
            if let bq = blockquote.firstMatch(line) {
                flushPara()
                var content = bq.group(1, in: line)
                i += 1
                while i < n, let m = blockquote.firstMatch(lines[i]), blockquote.match(lines[i]) {
                    content += " " + m.group(1, in: lines[i]).pyStrip()
                    i += 1
                }
                blocks.append(Block(kind: "blockquote", text: stripInline(content)))
                continue
            }

            // ALL-CAPS pseudo-header (single line, surrounded by blanks).
            if allcapsHeaders && looksLikeAllcapsHeader(line) && paraBuf.isEmpty
                && (i + 1 >= n || lines[i + 1].pyStrip().isEmpty) {
                flushPara()
                blocks.append(Block(kind: "header", text: stripInline(line.pyStrip()), level: 2))
                i += 1
                continue
            }

            // Otherwise accumulate into the current paragraph.
            paraBuf.append(line)
            i += 1
        }
        flushPara()
        return blocks
    }

    // MARK: inline markdown strip

    private static let inlineCode = RE(#"`([^`]+)`"#)
    private static let bold = RE(#"(\*\*|__)(.+?)\1"#)
    private static let italic = RE(#"(?<![\w*_])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*_])"#)
    private static let strike = RE(#"~~(.+?)~~"#)
    private static let mdLink = RE(#"\[([^\]]+)\]\(([^)]*)\)"#)
    private static let mdImage = RE(#"!\[([^\]]*)\]\(([^)]*)\)"#)

    static func stripInline(_ text0: String) -> String {
        var text = text0
        text = mdImage.sub(text) { m, s in m.group(1, in: s) }
        text = mdLink.sub(text) { m, s in m.group(1, in: s) }
        text = inlineCode.sub(text) { m, s in m.group(1, in: s) }
        text = bold.sub(text) { m, s in m.group(2, in: s) }
        text = strike.sub(text) { m, s in m.group(1, in: s) }
        text = italic.sub(text) { m, s in m.group(2, in: s) }
        return text.pyStrip()
    }
}
