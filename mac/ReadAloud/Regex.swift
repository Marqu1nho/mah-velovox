// Small regex + String helpers so the clean/parse/script ports read close to the
// Python originals (which lean on Python's `re` and str methods). NSRegularExpression
// works in UTF-16 NSRanges; these wrappers keep callers in String.Index land.
import Foundation

struct RE {
    let re: NSRegularExpression
    init(_ pattern: String, _ opts: NSRegularExpression.Options = []) {
        // Patterns are authored as raw strings (#"…"#) with ICU syntax; a bad one
        // is a programming error, so trap loudly at startup.
        re = try! NSRegularExpression(pattern: pattern, options: opts)
    }

    private func ns(_ s: String) -> NSRange { NSRange(s.startIndex..., in: s) }

    /// Python `re.search(...) is not None` (match anywhere).
    func search(_ s: String) -> Bool { re.firstMatch(in: s, range: ns(s)) != nil }

    /// Python `re.match(...)` — anchored at the start of the string.
    func match(_ s: String) -> Bool {
        guard let m = re.firstMatch(in: s, range: ns(s)) else { return false }
        return m.range.location == 0
    }

    func firstMatch(_ s: String) -> NSTextCheckingResult? { re.firstMatch(in: s, range: ns(s)) }
    func allMatches(_ s: String) -> [NSTextCheckingResult] { re.matches(in: s, range: ns(s)) }

    /// Python `re.sub(repl, text)` with a constant template ("" to delete).
    func sub(_ s: String, _ template: String = "") -> String {
        re.stringByReplacingMatches(in: s, range: ns(s), withTemplate: template)
    }

    /// Python `re.sub(func, text)` — replace each match via a closure that gets the
    /// match and the original string (for capture-group extraction).
    func sub(_ s: String, _ f: (NSTextCheckingResult, String) -> String) -> String {
        let results = re.matches(in: s, range: ns(s))
        guard !results.isEmpty else { return s }
        var out = s
        for m in results.reversed() {
            guard let r = Range(m.range, in: out) else { continue }
            out.replaceSubrange(r, with: f(m, s))
        }
        return out
    }
}

extension NSTextCheckingResult {
    /// Capture group `i` from the original string, or "" if it didn't participate.
    func group(_ i: Int, in s: String) -> String {
        guard i < numberOfRanges, range(at: i).location != NSNotFound,
              let r = Range(range(at: i), in: s) else { return "" }
        return String(s[r])
    }
}

extension String {
    /// Python str.strip() — trims leading/trailing whitespace AND newlines.
    func pyStrip() -> String { trimmingCharacters(in: .whitespacesAndNewlines) }

    /// Python str.strip(chars) — trims the given characters from both ends.
    func pyStrip(_ chars: Set<Character>) -> String {
        var s = Substring(self)
        while let f = s.first, chars.contains(f) { s = s.dropFirst() }
        while let l = s.last, chars.contains(l) { s = s.dropLast() }
        return String(s)
    }

    /// Python str.rstrip() — trailing whitespace/newlines only.
    func pyRStrip() -> String {
        var s = Substring(self)
        while let last = s.last, last.isWhitespace { s = s.dropLast() }
        return String(s)
    }

    /// Python str.rstrip(chars).
    func pyRStrip(_ chars: Set<Character>) -> String {
        var s = Substring(self)
        while let last = s.last, chars.contains(last) { s = s.dropLast() }
        return String(s)
    }

    /// True if the string has no alphanumeric content (Python: not any(isalnum)).
    var hasNoAlnum: Bool { !contains { $0.isLetter || $0.isNumber } }

    func splitLines() -> [String] { components(separatedBy: "\n") }
}
