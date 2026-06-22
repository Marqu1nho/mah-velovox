// Selection capture — ported from the Hammerspoon readaloud.lua pattern (NOT from
// SpeakWrite, which only ever pastes). Synthetic ⌘C → poll the pasteboard for a
// change → read it → restore the original pasteboard. If ⌘C never lands a change
// (some apps), fall back to the Accessibility AXSelectedText of the focused element.
//
// Needs Accessibility (TCC): both posting CGEvents into other apps and reading AX
// require it. AppDelegate nudges the prompt on launch.
import Cocoa
import ApplicationServices

enum Capture {
    /// Frontmost app's display name, for per-app mute rules (mute.by_app).
    static func frontmostAppName() -> String? {
        NSWorkspace.shared.frontmostApplication?.localizedName
    }

    /// Best-effort grab of the frontmost app's current text selection. Returns nil
    /// if nothing could be captured.
    static func selection() -> String? {
        let pb = NSPasteboard.general
        let savedString = pb.string(forType: .string)
        let baseline = pb.changeCount

        // The hotkey itself (⌃⌥⌘S) holds modifiers down. If ⌃/⌥ are still pressed
        // when we fire the synthetic ⌘C, the target app sees ⌃⌥⌘C — NOT copy — and
        // nothing lands on the clipboard. Wait for them to lift first. This is the
        // race behind the intermittent "no selection captured" in fast apps.
        waitForModifiersToClear()

        // Copy + poll; retry once, since the first ⌘C can still race the release.
        var captured = copyAndPoll(pb, since: baseline)
        if captured == nil { captured = copyAndPoll(pb, since: pb.changeCount) }

        // Restore the user's clipboard. If it was empty before but ⌘C populated it,
        // clear it so we don't leak the selection into an empty clipboard.
        if let saved = savedString {
            pb.clearContents()
            pb.setString(saved, forType: .string)
        } else if pb.changeCount != baseline {
            pb.clearContents()
        }

        if let c = captured, !c.isEmpty { return c }
        return axSelectedText()
    }

    /// Fire ⌘C and poll the pasteboard up to ~500ms for a change.
    private static func copyAndPoll(_ pb: NSPasteboard, since base: Int) -> String? {
        postCmdC()
        for _ in 0..<25 {
            usleep(20_000)
            if pb.changeCount != base { return pb.string(forType: .string) }
        }
        return nil
    }

    /// Block (cap ~300ms) until the control/option modifiers are released, so the
    /// synthetic ⌘C isn't polluted into ⌃⌥⌘C by the still-held hotkey.
    private static func waitForModifiersToClear() {
        for _ in 0..<30 {
            let f = CGEventSource.flagsState(.combinedSessionState)
            if !f.contains(.maskControl) && !f.contains(.maskAlternate) { return }
            usleep(10_000)
        }
    }

    private static func postCmdC() {
        let src = CGEventSource(stateID: .combinedSessionState)
        let cKey = CGKeyCode(8) // kVK_ANSI_C
        let down = CGEvent(keyboardEventSource: src, virtualKey: cKey, keyDown: true)
        down?.flags = .maskCommand
        down?.post(tap: .cghidEventTap)
        let up = CGEvent(keyboardEventSource: src, virtualKey: cKey, keyDown: false)
        up?.flags = .maskCommand
        up?.post(tap: .cghidEventTap)
    }

    private static func axSelectedText() -> String? {
        let sys = AXUIElementCreateSystemWide()
        var focused: CFTypeRef?
        guard AXUIElementCopyAttributeValue(sys, kAXFocusedUIElementAttribute as CFString, &focused) == .success,
              let element = focused else { return nil }
        // swiftlint:disable:next force_cast
        let el = element as! AXUIElement
        var sel: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXSelectedTextAttribute as CFString, &sel) == .success,
              let s = sel as? String, !s.isEmpty else { return nil }
        return s
    }
}
