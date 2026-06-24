// Port of readaloud/clean.py — terminal & noise scrubbing. Pure functions: raw
// pasted text (Claude Code TUI chrome, ANSI, box-drawing, hard wraps) → clean prose
// with blank-line block structure preserved for the parser.
import Foundation

enum Clean {
    // --- ANSI / VT escapes (ICU \u escapes; ESC = U+001B, BEL = U+0007) ---
    private static let ansiCSI    = RE(#"\u001B\[[0-?]*[ -/]*[@-~]"#)
    private static let ansiOSC    = RE(#"\u001B\][^\u0007\u001B]*(?:\u0007|\u001B\\)"#)
    private static let ansiTwo    = RE(#"\u001B[()#][0-9A-Za-z]"#)
    private static let ansiSimple = RE(#"\u001B[=>NOM78c]"#)
    private static let ansiLone   = RE(#"\u001B"#)
    private static let c0ctrl     = RE(#"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]"#)

    // --- box-drawing / block / braille / decoration ---
    private static let boxBlockBraille = RE(#"[─-▟⠀-⣿]"#)
    private static let decoration = RE(#"[•‣●○◐◑◒◓▪▫✓✗✔✘✨✻✽❖⁙·∙∘◦]"#)
    private static let emoji = RE(#"[\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}\x{1F000}-\x{1F0FF}\x{FE00}-\x{FE0F}\x{1F1E6}-\x{1F1FF}]+"#)

    private static let promptMarker = RE(#"^[ \t]*(?:❯|%|\$)\s+"#)
    private static let urlRE  = RE(#"https?://[^\s<>()\[\]]+"#, [.caseInsensitive])
    private static let pathRE = RE(#"(?<![\w/:])(?:~|\.{1,2})?/[^\s:;,'"()\[\]]+"#)
    private static let multispace = RE(#"[ \t]+"#)
    private static let sentenceEnd = RE(#"[.!?:]["')\]]?$"#)
    private static let listMarker = RE(#"^\s*(?:[-*+]\s+|\d+[.)]\s+)"#)
    private static let headerLine = RE(#"^\s*#{1,6}\s+"#)
    private static let blockquote = RE(#"^\s*>\s+"#)
    private static let hr = RE(#"^\s*(?:[-*_])\s*(?:[-*_]\s*){2,}$"#)
    private static let fenceLine = RE(#"^\s*(?:`{3,}|~{3,})"#)
    private static let domainRE = RE(#"https?://([^/]+)"#, [.caseInsensitive])

    // Identifier splitting: break camelCase / snake_case so the TTS reads
    // "k Audio Device Property Transport Type" instead of spelling it out. Common
    // CamelCase words (iPhone, JavaScript) still SOUND right when split — just a
    // word boundary, not a different pronunciation.
    private static let camelLower = RE(#"([a-z0-9])([A-Z])"#)        // foo|Bar
    private static let camelAcro  = RE(#"([A-Z])([A-Z][a-z])"#)      // URL|Session
    private static let snakeUnderscore = RE(#"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])"#)

    // MARK: ANSI

    static func stripAnsi(_ text0: String) -> String {
        var t = text0
        t = ansiOSC.sub(t)
        t = ansiCSI.sub(t)
        t = ansiTwo.sub(t)
        t = ansiSimple.sub(t)
        t = ansiLone.sub(t)
        t = t.replacingOccurrences(of: "\t", with: " ")
        t = c0ctrl.sub(t)
        return t
    }

    // MARK: URLs / paths / emoji

    private static func domainOf(_ url: String) -> String {
        var host = domainRE.firstMatch(url).map { $0.group(1, in: url) } ?? url
        if host.lowercased().hasPrefix("www.") { host = String(host.dropFirst(4)) }
        return host
    }

    private static func applyURLs(_ text: String, _ mode: String) -> String {
        if mode == "full" { return text }
        return urlRE.sub(text) { m, s in
            mode == "skip" ? "" : "link to \(domainOf(m.group(0, in: s)))"
        }
    }

    private static func applyPaths(_ text: String, _ mode: String) -> String {
        if mode == "full" { return text }
        return pathRE.sub(text) { m, s in
            let raw = m.group(0, in: s).pyRStrip([".", ",", ";", ":"])
            if mode == "skip" { return "" }
            let base = raw.pyRStrip(["/"]).components(separatedBy: "/").last ?? raw
            return base.isEmpty ? raw : base
        }
    }

    private static func applyEmoji(_ text: String, _ mode: String) -> String {
        if mode != "name" { return emoji.sub(text) }
        return emoji.sub(text) { m, s in
            let run = m.group(0, in: s)
            var names: [String] = []
            for ch in run.unicodeScalars {
                let name = (ch.properties.name ?? "").lowercased()
                if !name.isEmpty, !name.contains("variation selector"), !name.contains("zero width") {
                    names.append(name)
                }
            }
            return names.isEmpty ? " " : " \(names.joined(separator: " ")) "
        }
    }

    static func splitIdentifiers(_ s: String) -> String {
        var t = camelLower.sub(s, "$1 $2")
        t = camelAcro.sub(t, "$1 $2")
        t = snakeUnderscore.sub(t, " ")
        return t
    }

    private static func stripLineNoise(_ line0: String) -> String {
        var line = boxBlockBraille.sub(line0, " ")
        line = decoration.sub(line)
        line = promptMarker.sub(line)
        return line
    }

    // MARK: rejoin

    private static func shouldJoin(_ cur: String, _ nxt: String, _ fullLen: Int) -> Bool {
        let curS = cur.pyRStrip()
        let nxtS = nxt.pyStrip()
        if curS.isEmpty || nxtS.isEmpty { return false }
        if sentenceEnd.search(curS) { return false }
        if listMarker.match(nxt) || headerLine.match(nxt) || hr.match(nxt) { return false }
        if blockquote.match(nxt) && !blockquote.match(cur) { return false }
        if fullLen > 0 && curS.count < Int(0.85 * Double(fullLen)) { return false }
        let first = nxtS.first!
        if first.isLowercase || first == "," || first == ";" || first == ")" { return true }
        return false
    }

    private static func rejoinBlock(_ lines: [String], _ mode: String) -> [String] {
        if mode == "never" || lines.isEmpty { return lines }
        let fullLen = lines.map { $0.pyRStrip().count }.max() ?? 0
        var out: [String] = []
        var buf = lines[0]
        for nxt in lines.dropFirst() {
            let join: Bool
            if mode == "always" {
                join = !buf.pyStrip().isEmpty
                    && !sentenceEnd.search(buf.pyRStrip())
                    && !listMarker.match(nxt)
                    && !headerLine.match(nxt)
                    && !hr.match(nxt)
            } else {
                join = shouldJoin(buf, nxt, fullLen)
            }
            if join {
                buf = buf.pyRStrip() + " " + nxt.pyStrip()
            } else {
                out.append(buf)
                buf = nxt
            }
        }
        out.append(buf)
        return out
    }

    // MARK: mute rules

    private enum Matcher { case literal(String); case regex(RE) }
    private struct MuteRule { let dropLine: Bool; let matcher: Matcher }

    private static func parseMuteRules(_ rules: [String]) -> [MuteRule] {
        var parsed: [MuteRule] = []
        for rule in rules {
            var dropLine = false
            var s = rule
            if s.hasPrefix("drop-line:") { dropLine = true; s = String(s.dropFirst("drop-line:".count)) }
            if s.hasPrefix("re:") {
                let pat = String(s.dropFirst(3))
                if let compiled = try? NSRegularExpression(pattern: pat) {
                    parsed.append(MuteRule(dropLine: dropLine, matcher: .regex(RE(wrapping: compiled))))
                } else {
                    NSLog("readaloud: mute rule '\(rule)' has invalid regex; skipping")
                }
            } else {
                parsed.append(MuteRule(dropLine: dropLine, matcher: .literal(s)))
            }
        }
        return parsed
    }

    private static func parseBlockRules(_ rules: [String]) -> [Matcher] {
        var parsed: [Matcher] = []
        for rule in rules {
            if rule.hasPrefix("re:") {
                let pat = String(rule.dropFirst(3))
                if let compiled = try? NSRegularExpression(pattern: pat) {
                    parsed.append(.regex(RE(wrapping: compiled)))
                } else {
                    NSLog("readaloud: mute block rule '\(rule)' has invalid regex; skipping")
                }
            } else {
                parsed.append(.literal(rule))
            }
        }
        return parsed
    }

    private static func blockMatches(_ line: String, _ matchers: [Matcher]) -> Bool {
        for m in matchers {
            switch m {
            case .literal(let lit): if line.contains(lit) { return true }
            case .regex(let re): if re.search(line) { return true }
            }
        }
        return false
    }

    private static func applyBlockMute(_ text: String, _ blockRules: [String]) -> String {
        if blockRules.isEmpty { return text }
        let matchers = parseBlockRules(blockRules)
        if matchers.isEmpty { return text }
        var out: [String] = []
        var dropping = false
        for line in text.splitLines() {
            if dropping {
                if line.pyStrip().isEmpty { dropping = false; out.append(line) }
                continue
            }
            if blockMatches(line, matchers) { dropping = true; continue }
            out.append(line)
        }
        return out.joined(separator: "\n")
    }

    private static func applyMute(_ text: String, _ rules: [String]) -> String {
        if rules.isEmpty { return text }
        let parsed = parseMuteRules(rules)
        if parsed.isEmpty { return text }
        var out: [String] = []
        for line0 in text.splitLines() {
            var line = line0
            for rule in parsed {
                switch rule.matcher {
                case .literal(let lit):
                    if rule.dropLine {
                        if line.contains(lit) { line = "" }
                    } else {
                        line = line.replacingOccurrences(of: lit, with: "")
                    }
                case .regex(let re):
                    if rule.dropLine {
                        if re.search(line) { line = "" }
                    } else {
                        line = re.sub(line)
                    }
                }
                if rule.dropLine && line.isEmpty { break }
            }
            out.append(line)
        }
        return out.joined(separator: "\n")
    }

    // MARK: replace map

    private static func applyReplace(_ text0: String, _ replace: [String: String]) -> String {
        if replace.isEmpty { return text0 }
        var text = text0
        for key in replace.keys.sorted(by: { $0.count > $1.count }) {
            text = text.replacingOccurrences(of: key, with: " \(replace[key]!) ")
        }
        return text.splitLines().map { multispace.sub($0, " ").pyRStrip() }.joined(separator: "\n")
    }

    // MARK: full pipeline

    static func clean(_ text0: String, _ cfg: PipelineConfig, app: String?) -> String {
        var text = stripAnsi(text0)
        text = text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")

        if !cfg.muteBlocks.isEmpty { text = applyBlockMute(text, cfg.muteBlocks) }

        var muteRules = cfg.muteGlobal
        if let app = app, let byApp = cfg.muteByApp[app] { muteRules += byApp }
        if !muteRules.isEmpty { text = applyMute(text, muteRules) }

        if !cfg.replace.isEmpty { text = applyReplace(text, cfg.replace) }

        // First pass: scrub prose; pass fenced code verbatim. CODE markers use a
        // NUL sentinel exactly like the Python.
        let rawLines = text.splitLines()
        var inFence = false
        var fenceChar = ""
        var prose: [String] = []
        var codeBuf: [String] = []
        var codeBlocks: [[String]] = []

        for line0 in rawLines {
            if inFence {
                codeBuf.append(line0)
                if fenceLine.match(line0) && line0.pyStrip().hasPrefix(fenceChar) {
                    inFence = false
                    codeBlocks.append(codeBuf)
                    prose.append("\u{0}CODE\u{0}\(codeBlocks.count - 1)")
                    codeBuf = []
                }
                continue
            }
            if fenceLine.match(line0) {
                inFence = true
                fenceChar = String(line0.pyStrip().first!)
                codeBuf = [line0]
                continue
            }
            var line = stripLineNoise(line0)
            line = applyURLs(line, cfg.cleanURLs)
            line = applyPaths(line, cfg.cleanPaths)
            line = applyEmoji(line, cfg.cleanEmoji)
            if cfg.splitIdentifiers { line = splitIdentifiers(line) }
            line = multispace.sub(line, " ").pyRStrip()
            if !line.pyStrip().isEmpty && line.hasNoAlnum && !hr.match(line) { line = "" }
            prose.append(line)
        }
        if !codeBuf.isEmpty {
            codeBlocks.append(codeBuf)
            prose.append("\u{0}CODE\u{0}\(codeBlocks.count - 1)")
        }

        // Group into blocks separated by blanks / code markers; rejoin; re-emit.
        var outBlocks: [String] = []
        var cur: [String] = []
        func flush() {
            if !cur.isEmpty {
                outBlocks.append(rejoinBlock(cur, cfg.cleanRejoin).joined(separator: "\n"))
                cur.removeAll()
            }
        }
        let marker = "\u{0}CODE\u{0}"
        for line in prose {
            if line.hasPrefix(marker) {
                flush()
                if let idx = Int(line.dropFirst(marker.count)) {
                    outBlocks.append(codeBlocks[idx].joined(separator: "\n"))
                }
            } else if !line.pyStrip().isEmpty {
                cur.append(line)
            } else {
                flush()
            }
        }
        flush()
        return outBlocks.filter { !$0.pyStrip().isEmpty }.joined(separator: "\n\n").pyStrip()
    }
}

// Lets us wrap an already-compiled NSRegularExpression (for user-supplied mute
// patterns) in the same RE helper used everywhere else.
extension RE {
    init(wrapping compiled: NSRegularExpression) { self.init(compiled.pattern) }
}
