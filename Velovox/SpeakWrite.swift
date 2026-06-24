// SpeakWrite — native macOS dictation anchor.
//
// One app: global hotkey -> floating HUD shows live SpeechTranscriber output
// (committed bright, volatile dim) -> hotkey again pastes the transcript at the
// cursor and restores the clipboard. No Python, no Hammerspoon, no parakeet.
//
// v1 edit-as-you-go: the HUD is an editable NSTextView. Finalized speech appends
// at the END of the document (before a dim volatile tail); you may freely edit
// anything above. On stop, the WHOLE edited document is pasted — not the raw
// transcript. The volatile tail is located by its dim attribute each update, so
// it's robust to your edits in the bright region above it.
import Cocoa
import SwiftUI
import Combine
import Speech
import AVFoundation
import CoreAudio
import Carbon.HIToolbox

// ---------------------------------------------------------------------------
// A borderless panel that CAN become key (so the text view accepts edits), but
// only when something inside actually needs it (becomesKeyOnlyIfNeeded) — so it
// stays hands-off and the synthesized ⌘V lands in the target app, v0-style.
// ---------------------------------------------------------------------------
final class KeyablePanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    // Catch Cmd+C at the window level — a menu-less LSUIElement app has no Edit
    // menu wiring ⌘C to copy:, so we can't rely on responder routing. Returns
    // true (consumed) only if the handler took the whole-buffer copy.
    var onCmdC: (() -> Bool)?
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let mods = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if mods == .command, event.charactersIgnoringModifiers == "c" {
            NSLog("speakwrite: Cmd+C seen (panelKey=\(isKeyWindow))")
            if onCmdC?() == true { return true }
        }
        return super.performKeyEquivalent(with: event)
    }
}

// ---------------------------------------------------------------------------
// Transparent overlay that sits on top of the HUD and turns the border bands +
// a top strip into drag-to-resize / drag-to-move handles. Over the text area its
// hitTest returns nil, so clicks fall through to the editable text view below.
// Zones are computed from the live bounds, so they stay correct after a resize.
// ---------------------------------------------------------------------------
final class HUDFrameView: NSView {
    var marginSide: CGFloat = 16     // non-text band on left/right/bottom
    var marginTop: CGFloat = 28      // taller band up top for the move strip
    private let grab: CGFloat = 8    // how close to an edge counts as "resize"
    private let minW: CGFloat = 240, minH: CGFloat = 90

    var onMoveCommit: (() -> Void)?
    var onResizeCommit: (() -> Void)?

    private enum Mode { case none, move, left, right, bottom, bottomLeft, bottomRight }
    private var mode: Mode = .none
    private var startFrame: NSRect = .zero
    private var startMouse: NSPoint = .zero   // screen coords

    // The text passes through; everything else (border bands, strip) is ours.
    private var textRect: NSRect {
        NSRect(x: marginSide, y: marginSide,
               width: bounds.width - 2 * marginSide,
               height: bounds.height - marginSide - marginTop)
    }
    override func hitTest(_ point: NSPoint) -> NSView? {
        textRect.contains(point) ? nil : self
    }

    private func detectMode(_ p: NSPoint) -> Mode {
        let nearL = p.x <= grab, nearR = p.x >= bounds.maxX - grab, nearB = p.y <= grab
        if nearB && nearL { return .bottomLeft }
        if nearB && nearR { return .bottomRight }
        if nearL { return .left }
        if nearR { return .right }
        if nearB { return .bottom }
        return .move   // strip + inner margins
    }

    override func mouseDown(with e: NSEvent) {
        mode = detectMode(convert(e.locationInWindow, from: nil))
        startFrame = window?.frame ?? .zero
        startMouse = NSEvent.mouseLocation
    }

    override func mouseDragged(with e: NSEvent) {
        guard let win = window, mode != .none else { return }
        let m = NSEvent.mouseLocation
        let dx = m.x - startMouse.x, dy = m.y - startMouse.y
        var f = startFrame
        // NSWindow coords are y-up: dragging the bottom edge down (dy<0) grows height.
        func resizeLeft()   { let w = max(minW, startFrame.width  - dx); f.origin.x = startFrame.maxX - w; f.size.width = w }
        func resizeRight()  { f.size.width = max(minW, startFrame.width + dx) }
        func resizeBottom() { let h = max(minH, startFrame.height - dy); f.origin.y = startFrame.maxY - h; f.size.height = h }
        switch mode {
        case .move:        f.origin.x = startFrame.origin.x + dx; f.origin.y = startFrame.origin.y + dy
        case .left:        resizeLeft()
        case .right:       resizeRight()
        case .bottom:      resizeBottom()
        case .bottomLeft:  resizeLeft();  resizeBottom()
        case .bottomRight: resizeRight(); resizeBottom()
        case .none:        break
        }
        win.setFrame(f, display: true)
    }

    override func mouseUp(with e: NSEvent) {
        if mode == .move { onMoveCommit?() }
        else if mode != .none { onResizeCommit?() }
        mode = .none
    }

    override func resetCursorRects() {
        let b = bounds
        addCursorRect(NSRect(x: 0, y: 0, width: b.width, height: grab), cursor: .resizeUpDown)             // bottom
        addCursorRect(NSRect(x: 0, y: 0, width: grab, height: b.height), cursor: .resizeLeftRight)         // left
        addCursorRect(NSRect(x: b.maxX - grab, y: 0, width: grab, height: b.height), cursor: .resizeLeftRight) // right
        addCursorRect(NSRect(x: 0, y: b.maxY - marginTop, width: b.width, height: marginTop), cursor: .openHand) // move strip
    }

    // Hover cue: draw a small resize grip in the bottom-right corner when the
    // mouse is over it, so it's discoverable that the corner is a grab handle.
    private var hoverBR = false
    private var brTracking: NSTrackingArea?
    private var brCornerRect: NSRect { NSRect(x: bounds.maxX - 22, y: 0, width: 22, height: 22) }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let t = brTracking { removeTrackingArea(t) }
        let t = NSTrackingArea(rect: brCornerRect, options: [.mouseEnteredAndExited, .activeAlways], owner: self)
        addTrackingArea(t); brTracking = t
    }
    override func mouseEntered(with e: NSEvent) { hoverBR = true; needsDisplay = true }
    override func mouseExited(with e: NSEvent)  { hoverBR = false; needsDisplay = true }

    override func draw(_ dirtyRect: NSRect) {
        guard hoverBR else { return }
        NSColor.white.withAlphaComponent(0.6).setStroke()
        let p = NSBezierPath(); p.lineWidth = 1.5; p.lineCapStyle = .round
        let m = bounds.maxX
        for k: CGFloat in [7, 12, 17] {   // three diagonal grip lines
            p.move(to: NSPoint(x: m - 3, y: k))
            p.line(to: NSPoint(x: m - k, y: 3))
        }
        p.stroke()
    }
}

// Editable text view that treats Cmd+C with NO selection as "copy the whole
// buffer" (a safety grab while building), but keeps normal selection-copy.
final class AnchorTextView: NSTextView {
    var onCopyAll: (() -> Void)?
    var onKeyDown: ((NSEvent) -> Bool)?   // return true if consumed (e.g. first arrow)
    var onUserClick: (() -> Void)?
    override func copy(_ sender: Any?) {
        if selectedRange().length == 0, let h = onCopyAll { h() } else { super.copy(sender) }
    }
    override func keyDown(with event: NSEvent) {
        if onKeyDown?(event) == true { return }
        super.keyDown(with: event)
    }
    override func mouseDown(with event: NSEvent) {
        onUserClick?(); super.mouseDown(with: event)
    }
}

// Purely-visual label that never intercepts the mouse (passes clicks through to
// the move/resize overlay below it). Used for the transient "✓ Copied" cue.
final class PassthroughLabel: NSTextField {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}

// Hosts the friend's SwiftUI RawVoiceView (minimal mode), observing a smoothed
// level fed from OUR mic tap (no second AVAudioEngine tap — see RawVoice-Handoff).
final class OrbLevel: ObservableObject { @Published var level: CGFloat = 0 }
struct RawVoiceHost: View {
    @ObservedObject var model: OrbLevel
    var diameter: CGFloat
    var body: some View { RawVoiceView(level: model.level, diameter: diameter, stageColor: .clear) }
}

// ---------------------------------------------------------------------------
// HUD — non-activating floating panel with a scrollable, editable text view.
// ---------------------------------------------------------------------------
final class HUD {
    private let panel: KeyablePanel
    private let textView: AnchorTextView
    private let bg: NSView
    private let scroll: NSScrollView
    private let overlay: HUDFrameView
    private let orbModel = OrbLevel()
    private let orbHost: NSHostingView<RawVoiceHost>
    private var orbLevel: CGFloat = 0    // smoothed (fast attack / slow release)
    private var bloomLevel: CGFloat = 0  // one-shot start "breath", max'd with mic level
    private var bloomTimer: Timer?
    private let cueLabel = PassthroughLabel(labelWithString: "✓ Copied")
    private let fontSize = CGFloat(VELOVOX.speakWrite.hud.fontSize)
    private var editing = false   // false until you click/arrow in; caret hidden
    private let commAttrs: [NSAttributedString.Key: Any]
    private let volAttrs: [NSAttributedString.Key: Any]

    init() {
        commAttrs = [
            .font: NSFont.systemFont(ofSize: fontSize),
            .foregroundColor: NSColor.white,
        ]
        volAttrs = [
            .font: NSFont.systemFont(ofSize: fontSize),
            .foregroundColor: NSColor(white: 1.0, alpha: 0.45),
        ]
        // Assign before any [weak self] closure below so self is fully initialized.
        orbHost = NSHostingView(rootView: RawVoiceHost(model: orbModel, diameter: VELOVOX.speakWrite.orbSize))

        let w = CGFloat(VELOVOX.speakWrite.hud.width), h = CGFloat(VELOVOX.speakWrite.hud.height)
        panel = KeyablePanel(
            contentRect: NSRect(x: 0, y: 0, width: w, height: h),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false)
        panel.level = .screenSaver                 // float above fullscreen
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = false       // HUD is the focused editor while up
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.alphaValue = 1.0   // keep text crisp; transparency lives in the bg film
        panel.appearance = NSAppearance(named: .darkAqua)   // dark scroller etc.

        // Dark translucent "film" background — flat, not frosted vibrancy (the
        // frost is what read as gray). `alpha` = film opacity: lower = more
        // see-through. It's a sibling of the text, so the film can be very
        // transparent without dimming the words.
        bg = NSView(frame: panel.contentView!.bounds)
        bg.autoresizingMask = [.width, .height]
        bg.wantsLayer = true
        bg.layer?.backgroundColor = NSColor.black.withAlphaComponent(CGFloat(VELOVOX.speakWrite.hud.alpha)).cgColor
        bg.layer?.cornerRadius = 16
        bg.layer?.masksToBounds = true

        // Text inset to leave a non-text band on every side: the overlay turns
        // those bands into resize handles + a top move-strip. Margins MUST match
        // HUDFrameView's (16 sides/bottom, 28 top) so visual text == pass-through.
        let mSide: CGFloat = 16, mTop: CGFloat = 28
        scroll = NSScrollView(frame: NSRect(x: mSide, y: mSide,
                                            width: bg.bounds.width - 2 * mSide,
                                            height: bg.bounds.height - mSide - mTop))
        scroll.autoresizingMask = [.width, .height]
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true

        textView = AnchorTextView(frame: scroll.bounds)
        textView.autoresizingMask = [.width]
        textView.isEditable = true               // v1: edit-as-you-go
        textView.isSelectable = true
        textView.isRichText = false              // plain text; we control attrs
        textView.drawsBackground = false
        textView.textColor = .white
        textView.insertionPointColor = .white
        textView.typingAttributes = commAttrs    // user edits render bright/white
        textView.textContainerInset = NSSize(width: 4, height: 4)
        scroll.documentView = textView

        // z-order: film at back, text in front of it, move/resize overlay on top.
        panel.contentView!.addSubview(bg)
        panel.contentView!.addSubview(scroll)

        // Move/resize overlay on top — passes text-area clicks through to editing.
        overlay = HUDFrameView(frame: panel.contentView!.bounds)
        overlay.autoresizingMask = [.width, .height]
        overlay.marginSide = mSide; overlay.marginTop = mTop
        panel.contentView!.addSubview(overlay)
        // self is fully initialized here (all stored properties assigned above).
        overlay.onMoveCommit = { [weak self] in self?.snapToNearestZone(); self?.persistGeometry() }
        overlay.onResizeCommit = { [weak self] in self?.persistGeometry() }
        textView.onCopyAll = { [weak self] in self?.copyBuffer() }
        // Window-level Cmd+C: copy the whole buffer unless there's a selection
        // (then let the normal selection-copy proceed).
        panel.onCmdC = { [weak self] in
            guard let self else { return false }
            if self.textView.selectedRange().length > 0 { return false }
            self.copyBuffer(); return true
        }
        // First arrow press (when not yet editing) drops the caret at the end of
        // the committed (non-gray) text; a click puts it where you click.
        textView.onKeyDown = { [weak self] event in
            guard let self else { return false }
            let arrows: Set<UInt16> = [123, 124, 125, 126]   // ← → ↓ ↑
            if !self.editing, arrows.contains(event.keyCode) { self.enterEditAtCommittedEnd(); return true }
            return false
        }
        textView.onUserClick = { [weak self] in self?.beginEditing() }

        // Transient "✓ Copied" cue, frontmost + non-interactive.
        cueLabel.font = .boldSystemFont(ofSize: 13)
        cueLabel.textColor = .white
        cueLabel.alignment = .center
        cueLabel.wantsLayer = true
        cueLabel.drawsBackground = true
        cueLabel.backgroundColor = NSColor.black.withAlphaComponent(0.65)
        cueLabel.layer?.cornerRadius = 8
        cueLabel.layer?.masksToBounds = true
        cueLabel.isHidden = true
        panel.contentView!.addSubview(cueLabel)

        // RawVoice orb for minimal mode — floats on the transparent panel; hidden in text.
        orbHost.frame = panel.contentView!.bounds
        orbHost.autoresizingMask = [.width, .height]
        orbHost.isHidden = true
        panel.contentView!.addSubview(orbHost)

        reset()
        positionFromConfig()
    }

    // Toggle subviews for the active mode (text shows editor; minimal shows the
    // orb on a transparent background so it floats).
    private func applyMode() {
        let orb = VELOVOX.speakWrite.orbMode
        orbHost.isHidden = !orb
        scroll.isHidden = orb
        overlay.isHidden = orb
        bg.isHidden = orb                // transparent film in orb mode → it floats
        if orb { cueLabel.isHidden = true }
    }

    // Feed the live mic level (0…1) to the orb. Fast attack; a middle-ground
    // release (~400ms) — snappier than the original ~800ms, not as abrupt as 200ms.
    func setLevel(_ v: Float) {
        let target = CGFloat(max(0, min(1, v)))
        orbLevel += (target - orbLevel) * (target > orbLevel ? 0.6 : 0.3)
        orbModel.level = max(orbLevel, bloomLevel)   // bloom wins while it's still up
    }

    // One gentle "breath" the instant recording starts: ramp the orb out and back
    // over ~0.9s (sin peaks at the midpoint) so blob mode confirms "I'm awake, go
    // ahead" even before you speak. Maxed with the live mic level so an immediate
    // start still reads. Orb-mode only; no-op otherwise.
    func bloom() {
        guard VELOVOX.speakWrite.orbMode, VELOVOX.speakWrite.cueBloom else { return }
        bloomTimer?.invalidate()
        let t0 = Date(); let dur = 0.9
        bloomTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60, repeats: true) { [weak self] tm in
            guard let self else { tm.invalidate(); return }
            let p = min(1, Date().timeIntervalSince(t0) / dur)
            self.bloomLevel = CGFloat(sin(.pi * p)) * 0.6
            self.orbModel.level = max(self.orbLevel, self.bloomLevel)
            if p >= 1 { tm.invalidate(); self.bloomLevel = 0 }
        }
    }

    // Cmd+C with no selection: copy the whole edited buffer to the clipboard as a
    // safety grab, and flash a confirmation so you know it landed.
    private func copyBuffer() {
        let text = editableText().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
        NSLog("speakwrite: copied buffer to clipboard (\(text.count) chars)")
        flashCopiedCue()
    }

    private func flashCopiedCue() {
        guard let cv = panel.contentView else { return }
        let w: CGFloat = 96, h: CGFloat = 24
        cueLabel.frame = NSRect(x: (cv.bounds.width - w) / 2, y: cv.bounds.height - h - 8, width: w, height: h)
        cueLabel.alphaValue = 1
        cueLabel.isHidden = false
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.9
            cueLabel.animator().alphaValue = 0
        }, completionHandler: { [weak self] in self?.cueLabel.isHidden = true })
    }

    // Restore the saved spot if it's still on a screen; otherwise bottom-center.
    private func positionFromConfig() {
        if let x = VELOVOX.speakWrite.hud.x, let y = VELOVOX.speakWrite.hud.y {
            let origin = NSPoint(x: x, y: y)
            let frame = NSRect(origin: origin, size: panel.frame.size)
            if NSScreen.screens.contains(where: { $0.visibleFrame.intersects(frame) }) {
                panel.setFrameOrigin(origin); return
            }
        }
        positionBottomCenter()
    }

    private func positionBottomCenter() {
        guard let screen = NSScreen.main else { return }
        let f = screen.visibleFrame
        let size = panel.frame.size
        panel.setFrameOrigin(NSPoint(x: f.minX + (f.width - size.width) / 2, y: f.minY + 64))
    }

    private func positionCentered() {
        guard let screen = NSScreen.main else { return }
        let f = screen.visibleFrame
        let size = panel.frame.size
        let x = f.minX + (f.width - size.width) / 2
        let y = f.minY + (f.height - size.height) / 2   // dead center
        panel.setFrameOrigin(NSPoint(x: x, y: y))
    }

    // Place the orb at one of the 9-grid anchors from orb.position
    // (e.g. "top-center", "bottom-right", "center-left", "center").
    private func positionOrb() {
        guard let screen = NSScreen.main else { return }
        let vf = screen.visibleFrame, sz = panel.frame.size, m: CGFloat = 24
        let a = VELOVOX.speakWrite.orbPosition.lowercased()
        let x: CGFloat = a.contains("left")  ? vf.minX + m
                       : a.contains("right") ? vf.maxX - sz.width - m
                       :                        vf.midX - sz.width / 2
        let y: CGFloat = a.contains("top")    ? vf.maxY - sz.height - m
                       : a.contains("bottom") ? vf.minY + m
                       :                         vf.midY - sz.height / 2
        panel.setFrameOrigin(NSPoint(x: x, y: y))
    }

    // Snap the dropped panel to the nearest of a 9-grid of screen anchors, but
    // only if it landed close to one — otherwise leave it where you dropped it.
    private func snapToNearestZone() {
        guard let screen = panel.screen ?? NSScreen.main else { return }
        let vf = screen.visibleFrame
        let f = panel.frame, m: CGFloat = 12
        let xs = [vf.minX + m, vf.midX - f.width / 2, vf.maxX - f.width - m]
        let ys = [vf.minY + m, vf.midY - f.height / 2, vf.maxY - f.height - m]
        var best = f.origin, bestD = CGFloat.greatestFiniteMagnitude
        for x in xs { for y in ys {
            let d = hypot(x - f.origin.x, y - f.origin.y)
            if d < bestD { bestD = d; best = NSPoint(x: x, y: y) }
        }}
        if bestD < 80 { panel.setFrameOrigin(best) }
    }

    // Write the live position + size back to config (preserving everything else).
    private func persistGeometry() {
        let f = panel.frame
        VELOVOX.speakWrite.hud.x = Double(f.origin.x); VELOVOX.speakWrite.hud.y = Double(f.origin.y)
        VELOVOX.speakWrite.hud.width = Double(f.width); VELOVOX.speakWrite.hud.height = Double(f.height)
        VelovoxConfig.save()
    }

    // The dim volatile tail is always the trailing run with reduced alpha. We
    // RECOMPUTE it each update by scanning back over dim-colored characters, so
    // edits the user makes in the bright region above never desync our bookkeeping.
    private func volatileRange() -> NSRange {
        guard let ts = textView.textStorage else { return NSRange(location: 0, length: 0) }
        let len = ts.length
        guard len > 0 else { return NSRange(location: 0, length: 0) }
        var loc = len
        while loc > 0 {
            var eff = NSRange()
            let color = ts.attribute(.foregroundColor, at: loc - 1, effectiveRange: &eff) as? NSColor
            if (color?.alphaComponent ?? 1.0) < 0.9 { loc = eff.location } else { break }
        }
        return NSRange(location: loc, length: len - loc)
    }

    // Everything before the volatile tail = the (possibly user-edited) document.
    func editableText() -> String {
        let full = textView.string as NSString
        return full.substring(to: min(volatileRange().location, full.length))
    }

    var panelIsKey: Bool { panel.isKeyWindow }

    func reset() {
        editing = false
        textView.insertionPointColor = .clear   // hide caret until you engage
        textView.string = ""
        // commit-only: no dim prompt either — the box starts empty and fills with
        // committed words only (the start cue already signals "listening").
        setVolatile(VELOVOX.speakWrite.hudCommitOnly ? "" : "listening…")
    }

    // Reveal the caret + mark that the user has taken edit control.
    private func beginEditing() {
        editing = true
        textView.insertionPointColor = .white
    }

    private func enterEditAtCommittedEnd() {
        beginEditing()
        let pos = volatileRange().location          // end of committed (non-gray) text
        textView.setSelectedRange(NSRange(location: pos, length: 0))
        textView.scrollRangeToVisible(NSRange(location: pos, length: 0))
        NSLog("speakwrite: arrow-edit entered at committed end (pos=\(pos))")
    }

    // Append finalized text at the end of the editable region (before the dim
    // tail). Preserves the user's cursor/selection if it sits above the insert.
    func appendFinal(_ s: String) {
        guard !s.isEmpty, let ts = textView.textStorage else { return }
        var insertAt = volatileRange().location
        // If this chunk opens a new line, trim trailing spaces/punctuation off the
        // previous line so it doesn't end on a dangling comma or period (handles
        // the case where the punctuation was committed in an earlier segment).
        if s.hasPrefix("\n") {
            let seam = CharacterSet(charactersIn: " \t,.;:!?")
            let str = ts.string as NSString
            var end = insertAt
            while end > 0,
                  str.substring(with: NSRange(location: end - 1, length: 1)).rangeOfCharacter(from: seam) != nil {
                end -= 1
            }
            if end < insertAt {
                ts.deleteCharacters(in: NSRange(location: end, length: insertAt - end))
                insertAt = end
            }
        }
        let nsStr = ts.string as NSString
        var piece = s
        if insertAt > 0 {
            let prev = nsStr.substring(with: NSRange(location: insertAt - 1, length: 1))
            let prevIsWS = prev.rangeOfCharacter(from: .whitespacesAndNewlines) != nil
            if !prevIsWS && !s.hasPrefix("\n") { piece = " " + s }
        }
        if VELOVOX.speakWrite.dictationCasual {
            piece = WriteMode.casual(piece, sentenceStart: sentenceStartBefore(insertAt, in: nsStr))
        }
        let sel = textView.selectedRange()
        ts.insert(NSAttributedString(string: piece, attributes: commAttrs), at: insertAt)
        if sel.location + sel.length <= insertAt { textView.setSelectedRange(sel) }
    }

    // Look back from `loc` over whitespace for the last committed character: a
    // sentence opens at the document start (nothing before) or right after [.!?].
    private func sentenceStartBefore(_ loc: Int, in str: NSString) -> Bool {
        var k = loc
        while k > 0 {
            let ch = str.substring(with: NSRange(location: k - 1, length: 1))
            if ch.rangeOfCharacter(from: .whitespacesAndNewlines) != nil { k -= 1; continue }
            return ch == "." || ch == "!" || ch == "?"
        }
        return true
    }

    // Replace the dim tail in place with the live volatile guess.
    func setVolatile(_ s: String) {
        guard let ts = textView.textStorage else { return }
        let range = volatileRange()
        let wasAtBottom = atBottom()
        let sel = textView.selectedRange()
        var s = s
        if VELOVOX.speakWrite.dictationCasual, !s.isEmpty {
            s = WriteMode.casual(s, sentenceStart: sentenceStartBefore(range.location, in: ts.string as NSString))
        }
        ts.replaceCharacters(in: range, with: NSAttributedString(string: s, attributes: volAttrs))
        if sel.location + sel.length <= range.location { textView.setSelectedRange(sel) }
        if wasAtBottom { textView.scrollToEndOfDocument(nil) }
    }

    // Pin-to-bottom only if already at the bottom — leave the user's scroll
    // position alone if they scrolled up to re-read.
    private func atBottom() -> Bool {
        guard let scroll = textView.enclosingScrollView else { return true }
        let visible = scroll.documentVisibleRect
        return visible.maxY >= textView.bounds.height - 24
    }

    // Replace the whole document with a single bright string (used to show the
    // final transcript briefly when not in edit mode).
    func showFinal(_ s: String) {
        textView.textStorage?.setAttributedString(NSAttributedString(string: s, attributes: commAttrs))
        textView.scrollToEndOfDocument(nil)
    }

    func show() {
        switch VELOVOX.speakWrite.mode {
        case "off":
            return                                     // show nothing; dictate → paste only
        case "orb":
            applyMode()
            let s = VELOVOX.speakWrite.orbSize                      // fixed ambient indicator (own config)
            orbLevel = 0; orbModel.level = 0
            orbHost.rootView = RawVoiceHost(model: orbModel, diameter: s)
            panel.setContentSize(NSSize(width: s, height: s))
            positionOrb()                               // anchored per orb.position
            panel.isMovableByWindowBackground = false   // fixed indicator (per RawVoice handoff)
            panel.orderFrontRegardless()                // don't steal focus in orb mode
        default:                                       // "hud"
            applyMode()
            panel.isMovableByWindowBackground = false
            panel.setContentSize(NSSize(width: CGFloat(VELOVOX.speakWrite.hud.width), height: CGFloat(VELOVOX.speakWrite.hud.height)))
            positionFromConfig()
            panel.makeKeyAndOrderFront(nil)            // become the key window (for arrows/typing)
            panel.makeFirstResponder(textView)         // textView gets keys; caret stays hidden
        }
    }
    func hide() { panel.orderOut(nil) }
}

// ---------------------------------------------------------------------------
// Replacement dictionary — spoken phrase -> inserted text. Applied to each
// segment as it streams, so the HUD shows the substitution live and the pasted
// document already contains it. "new line" is just a dictionary entry that maps
// to a newline char (Apple never emits one itself). Case-insensitive, whole-
// phrase. Ordered most-specific-first.
//
// A `say` value may be either:
//   • a literal phrase (default) — matched whole-word, metacharacters escaped.
//   • a regex, by prefixing it with `re:` — the part after `re:` is used as a
//     raw NSRegularExpression (you control your own anchors; no implicit \b).
//     e.g.  {"say": "re:essay (config|configuration)", "insert": "essay config"}
// In both cases `insert` is a literal string — regex backreferences ($1) are not
// substituted, so a rule normalizes many spoken variants to one fixed string.
// ---------------------------------------------------------------------------
enum Replacements {
    private static let compiled: [(NSRegularExpression, String)] = VELOVOX.speakWrite.replacements.compactMap { rule in
        let pattern: String
        if rule.say.hasPrefix("re:") {
            // Raw regex: everything after the "re:" prefix, used verbatim.
            pattern = String(rule.say.dropFirst(3))
        } else {
            let p = NSRegularExpression.escapedPattern(for: rule.say)
            // Newline commands also swallow any whitespace/punctuation hugging the
            // phrase, so a spoken "new line" never leaves a stray comma or period
            // on the seam. Text replacements (e.g. emoji) keep their surroundings.
            pattern = rule.insert.contains("\n")
                ? "[\\s,.;:!?]*\\b\(p)\\b[\\s,.;:!?]*"
                : "\\b\(p)\\b"
        }
        guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else {
            NSLog("speakwrite: bad replacement pattern '\(rule.say)' — skipped"); return nil
        }
        return (re, NSRegularExpression.escapedTemplate(for: rule.insert))
    }
    static func apply(_ s: String) -> String {
        var out = s
        for (re, template) in compiled {
            out = re.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: template)
        }
        return out
    }
}

// ---------------------------------------------------------------------------
// Write mode — "casual" lowercases the first word of every sentence so prose
// reads like a quick chat message rather than formal copy. Runs on finalized
// text AFTER replacements, in the HUD, where the accumulated document gives the
// sentence-boundary context that a single streamed chunk lacks.
//
// A sentence starts at the document start or after [.!?] + whitespace. The first
// alphabetic word there is lowercased UNLESS it's on the exceptions list — match
// is whole-word and case-insensitive, and compares the part before any
// apostrophe, so a single "I" entry also spares I'm / I'll / I've / I'd.
// "formal" mode never calls this; the engine's own casing is left as-is.
// ---------------------------------------------------------------------------
enum WriteMode {
    private static let exceptions: Set<String> =
        Set(VELOVOX.speakWrite.dictationCapitalExceptions.map { $0.lowercased() })

    private static func isException(_ word: [Character]) -> Bool {
        let stem = word.prefix { $0 != "'" && $0 != "\u{2019}" }   // up to first apostrophe
        return exceptions.contains(String(stem).lowercased())
    }

    // Lowercase sentence-initial words in `s`. `sentenceStart` says whether the
    // chunk itself opens a sentence (derived from the preceding committed char).
    static func casual(_ s: String, sentenceStart: Bool) -> String {
        guard !s.isEmpty else { return s }
        let chars = Array(s)
        var out: [Character] = []
        out.reserveCapacity(chars.count)
        var atStart = sentenceStart
        var i = 0
        while i < chars.count {
            let c = chars[i]
            if c.isLetter {
                if atStart {
                    // Grab the whole word (letters + apostrophes) to test/exempt.
                    var j = i
                    while j < chars.count,
                          chars[j].isLetter || chars[j] == "'" || chars[j] == "\u{2019}" { j += 1 }
                    let word = Array(chars[i..<j])
                    if isException(word) {
                        out.append(contentsOf: word)
                    } else {
                        out.append(contentsOf: String(word[0]).lowercased())
                        out.append(contentsOf: word[1...])
                    }
                    i = j
                    atStart = false
                    continue
                }
            } else if c == "." || c == "!" || c == "?" {
                atStart = true               // next word opens a new sentence
            }
            // Whitespace and opening punctuation (quotes/brackets) keep `atStart`
            // so the first real word after them is still treated as the start.
            out.append(c)
            i += 1
        }
        return String(out)
    }
}

// ---------------------------------------------------------------------------
// Cues — a soft chime on start ("you can talk now") and optionally on stop. The
// confirmation paid dictation apps give; here it works in EVERY mode, including
// "off", which otherwise has no feedback at all. System sounds are cached and
// resolved by name from /System/Library/Sounds; a missing name logs and no-ops.
// ---------------------------------------------------------------------------
enum Cue {
    private static var cache: [String: NSSound] = [:]
    private static func play(_ name: String?) {
        guard VELOVOX.speakWrite.cueSound, let name, !name.isEmpty else { return }
        guard let s = cache[name] ?? NSSound(named: NSSound.Name(name)) else {
            NSLog("speakwrite: no system sound named '\(name)' (see /System/Library/Sounds)"); return
        }
        cache[name] = s
        s.volume = VELOVOX.speakWrite.cueVolume
        if s.isPlaying { s.stop() }
        s.play()
    }
    static func start() { play(VELOVOX.speakWrite.cueStart) }
    static func stop()  { play(VELOVOX.speakWrite.cueStop) }
}

// ---------------------------------------------------------------------------
// Audio route inspection. A Bluetooth headset can't be a hi-fi speaker AND a mic
// at once (A2DP is output-only; using the mic forces mono ~16 kHz HFP "call
// mode"), so "input transport is Bluetooth" reliably means degraded capture.
// We read the default input device's transport type via Core Audio.
// ---------------------------------------------------------------------------
enum AudioRoute {
    static func inputIsBluetooth() -> Bool {
        guard let dev = defaultInputDevice() else { return false }
        var transport: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyTransportType,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        guard AudioObjectGetPropertyData(dev, &addr, 0, nil, &size, &transport) == noErr else { return false }
        return transport == kAudioDeviceTransportTypeBluetooth
            || transport == kAudioDeviceTransportTypeBluetoothLE
    }

    private static func defaultInputDevice() -> AudioDeviceID? {
        var dev = AudioDeviceID(0)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),
                                         &addr, 0, nil, &size, &dev) == noErr, dev != 0 else { return nil }
        return dev
    }
}

// ---------------------------------------------------------------------------
// Smart WPM — counts SPEAKING time, not wall-clock. Fed the same 0…1 mic level
// that drives the orb: each sample's elapsed slice counts toward speaking time
// when voiced; during silence it still counts up to `grace` (so natural between-
// word pauses don't inflate WPM), but silence BEYOND grace is dropped as thinking.
// On stop, words / (speakingSeconds/60) → the rate at which you actually talk.
// ---------------------------------------------------------------------------
struct Metric: Codable {
    let date: String; let words: Int
    let speakingSeconds: Double; let totalSeconds: Double; let wpm: Double
}

final class WPMMeter {
    private var speaking: TimeInterval = 0
    private var total: TimeInterval = 0
    private var silenceRun: TimeInterval = 0
    private var last: Date?

    func reset() { speaking = 0; total = 0; silenceRun = 0; last = nil }

    // Call on every mic-level sample (raw normalized 0…1, on the main thread).
    func sample(level: Float) {
        let now = Date()
        defer { last = now }
        guard let prev = last else { return }
        let dt = now.timeIntervalSince(prev)
        guard dt > 0, dt < 1 else { return }   // ignore stalls / huge gaps
        total += dt
        if Double(level) >= VELOVOX.speakWrite.metricVoiceThreshold {
            speaking += dt; silenceRun = 0
        } else {
            silenceRun += dt
            if silenceRun <= VELOVOX.speakWrite.metricSilenceGrace { speaking += dt }  // bridge short pauses
        }
    }

    // Build a metric for `words` spoken; nil if disabled or too little to be real.
    func finish(words: Int) -> Metric? {
        guard VELOVOX.speakWrite.metricsEnabled, words > 0, speaking >= 0.5 else { return nil }
        let wpm = Double(words) / (speaking / 60.0)
        let iso = ISO8601DateFormatter().string(from: Date())
        return Metric(date: iso, words: words,
                      speakingSeconds: (speaking * 10).rounded() / 10,
                      totalSeconds: (total * 10).rounded() / 10,
                      wpm: wpm.rounded())
    }
}

// Append-only per-session metrics log (JSONL), next to config.json.
enum Metrics {
    static var fileURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/velovox/metrics.jsonl")
    }
    // Pre-Velovox the log lived next to the old speakwrite config. Carry the user's
    // dictation history forward once, on first launch, so no stats are lost.
    private static var legacyURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/speakwrite/metrics.jsonl")
    }
    static func migrateIfNeeded() {
        let fm = FileManager.default
        guard !fm.fileExists(atPath: fileURL.path),
              fm.fileExists(atPath: legacyURL.path) else { return }
        try? fm.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        do {
            try fm.copyItem(at: legacyURL, to: fileURL)
            NSLog("velovox: migrated dictation metrics \(legacyURL.path) -> \(fileURL.path)")
        } catch { NSLog("velovox: metrics migration failed \(error)") }
    }
    static func append(_ m: Metric) {
        guard let line = try? JSONEncoder().encode(m) else { return }
        var data = line; data.append(0x0A)   // newline
        let url = fileURL
        if let fh = try? FileHandle(forWritingTo: url) {
            defer { try? fh.close() }
            fh.seekToEndOfFile(); fh.write(data)
        } else {
            try? data.write(to: url)          // first session creates the file
        }
        NSLog("speakwrite: \(Int(m.wpm)) wpm (\(m.words) words / \(m.speakingSeconds)s speaking of \(m.totalSeconds)s total) -> metrics.jsonl")
    }
}

// ---------------------------------------------------------------------------
// A tiny non-interactive toast that flashes the just-finished session's WPM,
// bottom-center. Standalone (its own panel) so it shows in EVERY mode — the HUD
// is already hidden by the time we paste, and orb/off have no text surface.
// Non-activating + ignoresMouseEvents so it never disturbs the paste target.
// ---------------------------------------------------------------------------
final class Toast {
    private let panel: NSPanel
    private let bg: NSView
    private let label = NSTextField(labelWithString: "")
    private var dismissAt: Date?   // for overlapping flashes: latest one owns dismissal

    init() {
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 168, height: 56),
                        styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: false)
        panel.level = .screenSaver
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.ignoresMouseEvents = true

        bg = NSView(frame: panel.contentView!.bounds)
        bg.autoresizingMask = [.width, .height]
        bg.wantsLayer = true
        bg.layer?.backgroundColor = NSColor.black.withAlphaComponent(0.7).cgColor
        bg.layer?.cornerRadius = 16
        panel.contentView!.addSubview(bg)

        label.autoresizingMask = [.width, .height]
        label.alignment = .center
        label.textColor = .white
        label.drawsBackground = false
        label.isBezeled = false
        label.isEditable = false
        label.maximumNumberOfLines = 0
        label.lineBreakMode = .byWordWrapping
        label.cell?.wraps = true
        bg.addSubview(label)
    }

    // Big, brief — the per-session WPM glance.
    func flashWPM(_ text: String) { show(text, size: NSSize(width: 168, height: 56), fontSize: 24, weight: .semibold, hold: 1.3) }
    // Wider, longer — a wrapped advisory line (e.g. the Bluetooth-mic nudge).
    func note(_ text: String)     { show(text, size: NSSize(width: 440, height: 92), fontSize: 14, weight: .medium, hold: 3.5) }

    private func show(_ text: String, size: NSSize, fontSize: CGFloat, weight: NSFont.Weight, hold: TimeInterval) {
        label.stringValue = text
        label.font = .systemFont(ofSize: fontSize, weight: weight)
        panel.setContentSize(size)
        bg.frame = NSRect(origin: .zero, size: size)
        label.frame = bg.bounds.insetBy(dx: 18, dy: 12)
        if let screen = NSScreen.main {
            let vf = screen.visibleFrame
            panel.setFrameOrigin(NSPoint(x: vf.midX - size.width / 2, y: vf.minY + 120))
        }
        panel.alphaValue = 0
        panel.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { c in c.duration = 0.15; panel.animator().alphaValue = 1 }
        let deadline = Date().addingTimeInterval(hold)
        dismissAt = deadline
        DispatchQueue.main.asyncAfter(deadline: .now() + hold) { [weak self] in
            guard let self, self.dismissAt == deadline else { return }   // a newer flash superseded us
            NSAnimationContext.runAnimationGroup({ c in c.duration = 0.5; self.panel.animator().alphaValue = 0 },
                completionHandler: { if self.dismissAt == deadline { self.panel.orderOut(nil) } })
        }
    }
}

// One-shot box for the converter input block (avoids a mutable-var capture).
private final class FeedBox { var buf: AVAudioPCMBuffer?; init(_ b: AVAudioPCMBuffer) { buf = b } }

// ---------------------------------------------------------------------------
// Dictation — AVAudioEngine mic -> Apple SpeechTranscriber PROGRESSIVE streaming.
//
// Emits per-segment deltas: onSegment(isFinal, text). Finalized chunks append at
// the end of the HUD doc; volatile updates replace the dim tail in place.
// ---------------------------------------------------------------------------
final class Dictation {
    private let engine = AVAudioEngine()
    private var analyzer: SpeechAnalyzer?
    private var continuation: AsyncStream<AnalyzerInput>.Continuation?
    private var resultsTask: Task<Void, Never>?
    private var committedCount = 0   // chars finalized this session (for the stop log)

    var onSegment: ((Bool, String) -> Void)?   // (isFinal, text)
    var onLevel: ((Float) -> Void)?            // live mic level 0…1 (for the pulse orb)

    func start() {
        committedCount = 0
        Task { await run() }
    }

    func stop(_ done: @escaping () -> Void) {
        Task {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
            continuation?.finish()
            try? await analyzer?.finalizeAndFinishThroughEndOfInput()
            await resultsTask?.value
            NSLog("speakwrite: stop (\(committedCount) finalized chars this session)")
            await MainActor.run { done() }
        }
    }

    private func run() async {
        // Pick the engine per config. Both transcribers expose a `.results`
        // sequence of (.text, .isFinal), so only the module type and how we
        // start its loop differ — everything below (assets, analyzer, audio
        // tap) treats it as `any SpeechModule`.
        let module: any SpeechModule
        switch VELOVOX.speakWrite.engineKind {
        case "dictation":
            var opts: Set<DictationTranscriber.TranscriptionOption> = []
            if VELOVOX.speakWrite.dictationPunctuation { opts.insert(.punctuation) }
            if VELOVOX.speakWrite.dictationEmoji { opts.insert(.emoji) }
            let t = DictationTranscriber(locale: Locale(identifier: VELOVOX.speakWrite.locale),
                                         contentHints: [],
                                         transcriptionOptions: opts,
                                         reportingOptions: [.volatileResults],
                                         attributeOptions: [])
            module = t
            resultsTask = Task { [weak self] in
                guard let self else { return }
                do {
                    for try await r in t.results {
                        let txt = Replacements.apply(String(r.text.characters))
                        let isFinal = r.isFinal
                        if isFinal { self.committedCount += txt.count }
                        await MainActor.run { self.onSegment?(isFinal, txt) }
                    }
                } catch { NSLog("speakwrite: results error \(error)") }
            }
            NSLog("speakwrite: engine=dictation punctuation=\(VELOVOX.speakWrite.dictationPunctuation) emoji=\(VELOVOX.speakWrite.dictationEmoji)")
        default:
            let t = SpeechTranscriber(locale: Locale(identifier: VELOVOX.speakWrite.locale),
                                      preset: .progressiveTranscription)
            module = t
            resultsTask = Task { [weak self] in
                guard let self else { return }
                do {
                    for try await r in t.results {
                        let txt = Replacements.apply(String(r.text.characters))
                        let isFinal = r.isFinal
                        if isFinal { self.committedCount += txt.count }
                        await MainActor.run { self.onSegment?(isFinal, txt) }
                    }
                } catch { NSLog("speakwrite: results error \(error)") }
            }
            NSLog("speakwrite: engine=speech")
        }
        do {
            if let req = try await AssetInventory.assetInstallationRequest(supporting: [module]) {
                try await req.downloadAndInstall()
            }
            let analyzer = SpeechAnalyzer(modules: [module]); self.analyzer = analyzer
            guard let fmt = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [module]) else { return }

            let (stream, cont) = AsyncStream<AnalyzerInput>.makeStream(); self.continuation = cont

            try await analyzer.start(inputSequence: stream)

            let input = engine.inputNode
            let inFmt = input.outputFormat(forBus: 0)
            guard let converter = AVAudioConverter(from: inFmt, to: fmt) else { return }
            input.installTap(onBus: 0, bufferSize: 4096, format: inFmt) { [weak self] buf, _ in
                // Mic level for the pulse orb: RMS -> dB -> normalized (-50…-10 dB).
                if let ch = buf.floatChannelData?[0] {
                    let n = Int(buf.frameLength)
                    var sumSq: Float = 0
                    for i in 0..<n { let s = ch[i]; sumSq += s * s }
                    let rms = (n > 0) ? sqrtf(sumSq / Float(n)) : 0
                    let db = 20 * log10f(max(rms, 1e-7))
                    let norm = max(0, min(1, (db + 50) / 40))
                    DispatchQueue.main.async { self?.onLevel?(norm) }
                }
                let ratio = fmt.sampleRate / inFmt.sampleRate
                let cap = AVAudioFrameCount(Double(buf.frameLength) * ratio + 1024)
                guard let out = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: cap) else { return }
                let box = FeedBox(buf)
                let block: AVAudioConverterInputBlock = { _, status in
                    if let b = box.buf { box.buf = nil; status.pointee = .haveData; return b }
                    status.pointee = .noDataNow; return nil
                }
                var err: NSError?
                converter.convert(to: out, error: &err, withInputFrom: block)
                if err == nil && out.frameLength > 0 { cont.yield(AnalyzerInput(buffer: out)) }
            }
            engine.prepare()
            try engine.start()
            NSLog("speakwrite: streaming started fmt=\(inFmt.sampleRate)Hz ch=\(inFmt.channelCount)")
        } catch {
            NSLog("speakwrite: dictation error \(error)")
        }
    }
}

// ---------------------------------------------------------------------------
// Paste at cursor with full-fidelity clipboard save/restore.
// ---------------------------------------------------------------------------
enum Paster {
    static func pasteAtCursor(_ text: String) {
        guard !text.isEmpty else { return }
        let pb = NSPasteboard.general

        // Snapshot every item/type so an image (e.g. a screenshot) survives.
        var saved: [[NSPasteboard.PasteboardType: Data]] = []
        for item in pb.pasteboardItems ?? [] {
            var entry: [NSPasteboard.PasteboardType: Data] = [:]
            for type in item.types { if let d = item.data(forType: type) { entry[type] = d } }
            saved.append(entry)
        }

        pb.clearContents()
        pb.setString(text + " ", forType: .string)

        // Synthesize Cmd+V (requires Accessibility permission).
        let src = CGEventSource(stateID: .combinedSessionState)
        let v = CGKeyCode(kVK_ANSI_V)
        let down = CGEvent(keyboardEventSource: src, virtualKey: v, keyDown: true)
        down?.flags = .maskCommand
        let up = CGEvent(keyboardEventSource: src, virtualKey: v, keyDown: false)
        up?.flags = .maskCommand
        down?.post(tap: .cghidEventTap)
        up?.post(tap: .cghidEventTap)

        // Restore after the paste is consumed.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            pb.clearContents()
            guard !saved.isEmpty else { return }
            var items: [NSPasteboardItem] = []
            for entry in saved {
                let it = NSPasteboardItem()
                for (type, data) in entry { it.setData(data, forType: type) }
                items.append(it)
            }
            pb.writeObjects(items)
        }
    }
}

// ---------------------------------------------------------------------------
// App controller — global hotkey, orchestration.
// ---------------------------------------------------------------------------
final class SpeakWriteController {
    private let hud = HUD()
    private let dictation = Dictation()
    private let meter = WPMMeter()
    private let toast = Toast()
    private var dictating = false
    private var warnedBluetooth = false   // nudge once per contiguous BT-input state
    private var previousApp: NSRunningApplication?

    init() {
        dictation.onSegment = { [weak self] isFinal, text in
            guard let self else { return }
            if isFinal {
                self.hud.appendFinal(text)
                self.hud.setVolatile("")
            } else if !VELOVOX.speakWrite.hudCommitOnly {
                self.hud.setVolatile(text)   // skip the live gray guess in commit-only
            }
        }
        dictation.onLevel = { [weak self] level in
            self?.hud.setLevel(level)
            self?.meter.sample(level: level)   // smart-WPM voice-activity sampling
        }
    }

    func toggle() {
        NSLog("speakwrite: hotkey fired (was dictating=\(dictating))")
        if dictating {
            dictating = false
            Cue.stop()                 // soft "captured" confirmation (silent unless configured)
            dictation.stop { [weak self] in
                guard let self else { return }
                let edited = self.hud.editableText().trimmingCharacters(in: .whitespacesAndNewlines)
                let wasKey = self.hud.panelIsKey
                NSLog("speakwrite: pasting \(edited.count) chars (edited; panelWasKey=\(wasKey))")
                // Smart WPM: count words in the final transcript over speaking time.
                let words = edited.split(whereSeparator: { $0.isWhitespace }).count
                if let m = self.meter.finish(words: words) {
                    Metrics.append(m)
                    if VELOVOX.speakWrite.metricsFlash { self.toast.flashWPM("\(Int(m.wpm)) wpm") }
                }
                // If the user clicked in to edit, the HUD took key focus. Hide it
                // and restore the original app before pasting so ⌘V lands there.
                self.hud.hide()
                if wasKey { self.previousApp?.activate() }
                if !edited.isEmpty {
                    let delay = wasKey ? 0.18 : 0.0
                    DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
                        Paster.pasteAtCursor(edited)
                    }
                }
            }
        } else {
            dictating = true
            previousApp = NSWorkspace.shared.frontmostApplication
            meter.reset()
            hud.reset()
            hud.show()
            Cue.start()      // "you can talk now" chime — works in every mode, even off
            hud.bloom()      // + a one-time orb breath so blob mode has a visual cue too
            warnIfBluetoothMic()
            dictation.start()
        }
    }

    // One-time nudge when the mic is a Bluetooth headset (call-mode capture). We
    // warn once per contiguous BT state: switch away and back to re-arm, so it
    // never nags on every dictation while you're stuck on the bad mic.
    private func warnIfBluetoothMic() {
        guard VELOVOX.speakWrite.warnBluetoothInput else { return }
        if AudioRoute.inputIsBluetooth() {
            if !warnedBluetooth {
                warnedBluetooth = true
                NSLog("speakwrite: input is Bluetooth (call-mode ~16kHz) — nudging to switch mic")
                toast.note("🎧 Your mic is a Bluetooth headset (call quality). Switch input to a built-in or wired mic for clearer transcription.")
            }
        } else {
            warnedBluetooth = false
        }
    }

    // Register the dictation hotkey through the shared manager (which routes by
    // EventHotKeyID so it coexists with ReadAloud's hotkey in one process).
    func registerHotKey() {
        HotKeys.register(id: HotKeyID.speakWrite,
                         spec: VELOVOX.speakWrite.hotkeySpec,
                         defaultKey: UInt32(kVK_ANSI_S),
                         defaultMods: UInt32(controlKey | optionKey)) {
            gSpeakWrite?.toggle()
        }
    }
}

var gSpeakWrite: SpeakWriteController?
