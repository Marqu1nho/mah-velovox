// Transport pill — the persistent, CLICKABLE top-screen cue, ported from
// readaloud.lua. Two zones on a dark rounded pill:
//   LEFT  = play/pause toggle (⏸ while playing, ▶ while paused)
//   RIGHT = stop (⏹)
// Centered horizontally; vertical center at alerts.y_pct% of screen height from the
// top. Stays until the read stops/finishes, then fades out (0.2s). Non-activating so
// clicking it never steals focus from the app you're reading from.
import Cocoa

private let kFont = NSFont.systemFont(ofSize: 16)
private let kPadX: CGFloat = 18
private let kPadY: CGFloat = 8
private let kSepW: CGFloat = 1
private let kBgAlpha: CGFloat = 0.72

final class TransportPill {
    var onToggle: (() -> Void)?
    var onStop: (() -> Void)?

    private var panel: NSPanel?
    private var view: PillView?
    private var paused = false
    private var yPct: Double = 3.5

    func show(yPct: Double) {
        self.yPct = yPct
        self.paused = false
        if panel == nil { build() }
        relayout()
        panel?.alphaValue = 1
        panel?.orderFrontRegardless()
    }

    func setPaused(_ p: Bool) {
        paused = p
        view?.paused = p
        relayout()
        view?.needsDisplay = true
    }

    func hide() {
        guard let panel = panel else { return }
        self.panel = nil
        self.view = nil
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.2
            panel.animator().alphaValue = 0
        }, completionHandler: {
            panel.orderOut(nil)
        })
    }

    private func build() {
        let p = NSPanel(contentRect: .zero,
                        styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: false)
        p.level = .screenSaver
        p.isFloatingPanel = true
        p.hidesOnDeactivate = false
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = false
        p.ignoresMouseEvents = false
        p.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        let v = PillView(frame: .zero)
        v.onLeft = { [weak self] in self?.onToggle?() }
        v.onRight = { [weak self] in self?.onStop?() }
        p.contentView = v
        panel = p
        view = v
    }

    private func relayout() {
        guard let panel = panel, let view = view else { return }
        let leftIcon = paused ? "▶" : "⏸"
        let rightIcon = "⏹"
        let lSize = measure(leftIcon)
        let rSize = measure(rightIcon)
        let zoneH = max(lSize.height, rSize.height) + kPadY * 2
        let leftW = lSize.width + kPadX * 2
        let rightW = rSize.width + kPadX * 2
        let totalW = leftW + kSepW + rightW

        view.leftIcon = leftIcon
        view.rightIcon = rightIcon
        view.leftW = leftW
        view.zoneH = zoneH

        guard let screen = NSScreen.main else { return }
        let f = screen.frame
        let centerY = f.maxY - (f.height * CGFloat(yPct) / 100.0)
        let originX = f.midX - totalW / 2
        let originY = centerY - zoneH / 2
        panel.setFrame(NSRect(x: originX, y: originY, width: totalW, height: zoneH), display: true)
        view.frame = NSRect(x: 0, y: 0, width: totalW, height: zoneH)
        view.needsDisplay = true
    }
}

private func measure(_ s: String) -> NSSize {
    (s as NSString).size(withAttributes: [.font: kFont])
}

final class PillView: NSView {
    var leftIcon = "⏸"
    var rightIcon = "⏹"
    var paused = false
    var leftW: CGFloat = 0
    var zoneH: CGFloat = 0
    var onLeft: (() -> Void)?
    var onRight: (() -> Void)?

    override var isFlipped: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        let b = bounds
        // Background pill.
        let radius = b.height / 2
        let bg = NSBezierPath(roundedRect: b, xRadius: radius, yRadius: radius)
        NSColor(white: 0, alpha: kBgAlpha).setFill()
        bg.fill()

        let attrs: [NSAttributedString.Key: Any] = [
            .font: kFont,
            .foregroundColor: NSColor.white,
        ]
        // Left zone (centered in [0, leftW]).
        drawCentered(leftIcon, in: NSRect(x: 0, y: 0, width: leftW, height: b.height), attrs: attrs)
        // Separator.
        NSColor(white: 1, alpha: 0.35).setFill()
        NSRect(x: leftW, y: kPadY, width: kSepW, height: b.height - kPadY * 2).fill()
        // Right zone (centered in the remainder).
        let rightX = leftW + kSepW
        drawCentered(rightIcon, in: NSRect(x: rightX, y: 0, width: b.width - rightX, height: b.height), attrs: attrs)
    }

    private func drawCentered(_ s: String, in rect: NSRect, attrs: [NSAttributedString.Key: Any]) {
        let size = (s as NSString).size(withAttributes: attrs)
        let p = NSPoint(x: rect.midX - size.width / 2, y: rect.midY - size.height / 2)
        (s as NSString).draw(at: p, withAttributes: attrs)
    }

    override func mouseUp(with event: NSEvent) {
        let p = convert(event.locationInWindow, from: nil)
        if p.x < leftW { onLeft?() } else { onRight?() }
    }
}
