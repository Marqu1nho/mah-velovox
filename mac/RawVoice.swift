//
//  RawVoice.swift
//
//  A minimal, "raw" voice-capture indicator. A breathing center blob, one faint
//  dashed ring, and an outer reactive waveform — all in thin white line on a dark
//  stage. While the user is actually speaking, short dashes continuously break
//  past the outer waveform ring and fade out just beyond it.
//
//  This is the "anchor-off" mode: when a user opts out of the live visual
//  transcript, this orb is the entire confidence signal that their voice is
//  being captured. It must read as unmistakably ALIVE the instant they talk and
//  clearly QUIET-but-awake when they pause.
//
//  Pure SwiftUI drawing (Canvas + TimelineView). No third-party dependencies.
//  The dash field is computed deterministically from time, so there is no mutable
//  particle state to manage — it plays nicely with SwiftUI value semantics.
//
//  ─────────────────────────────────────────────────────────────────────────────
//  SIZING (see RawVoice-Handoff.md for the full rationale):
//   • One fixed PHYSICAL size on every screen. Do NOT scale to the display.
//   • Work in points, not pixels — Canvas maps points→pixels and handles Retina.
//   • Default footprint: 220 pt. Full-screen listening mode: ~300 pt.
//     Small inline indicator next to a field: ~80 pt.
//   • Everything (ring offsets, waveform amplitude, dash reach, stroke widths)
//     derives from the single `diameter` value, so proportions hold at any size.
//   • Self-protecting guardrail: the view clamps to its container, so it shrinks
//     gracefully in a cramped window instead of overflowing.
//  ─────────────────────────────────────────────────────────────────────────────
//
//  Usage — live mic:
//      @StateObject private var mic = VoiceLevelMonitor()
//      RawVoiceView(level: mic.level)                 // 220 pt default
//          .frame(width: 220, height: 220)
//          .onAppear { mic.start() }
//          .onDisappear { mic.stop() }
//
//  Usage — preview the look with no mic:
//      RawVoiceDemoView()
//
//  Live mic requires "Privacy - Microphone Usage Description"
//  (NSMicrophoneUsageDescription) in Info.plist.
//

import SwiftUI
import AVFoundation

// MARK: - Live audio level

/// Taps the mic input and publishes a smoothed 0...1 loudness level on the main
/// actor. Fast attack / slow release: the orb snaps up on a syllable, eases back.
///
/// If you already receive audio buffers from your ASR pipeline (e.g. Deepgram),
/// you do NOT need this — compute the same RMS there and feed `RawVoiceView`
/// directly. Running a second input tap is wasteful.
@MainActor
final class VoiceLevelMonitor: ObservableObject {

    @Published private(set) var level: CGFloat = 0

    private let engine = AVAudioEngine()
    private var smoothed: CGFloat = 0
    private var running = false

    func requestPermission(_ completion: @escaping (Bool) -> Void) {
        #if os(iOS)
        if #available(iOS 17.0, *) {
            AVAudioApplication.requestRecordPermission { ok in
                DispatchQueue.main.async { completion(ok) }
            }
        } else {
            AVAudioSession.sharedInstance().requestRecordPermission { ok in
                DispatchQueue.main.async { completion(ok) }
            }
        }
        #else
        completion(true)
        #endif
    }

    func start() {
        guard !running else { return }

        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playAndRecord, mode: .measurement,
                                 options: [.defaultToSpeaker, .allowBluetooth])
        try? session.setActive(true, options: .notifyOthersOnDeactivation)
        #endif

        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)

        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            let rms = Self.rms(of: buffer)
            let db = 20 * log10(max(rms, 1e-7))          // ~ -50 dB floor
            let norm = max(0, min(1, (db + 50) / 50))
            Task { @MainActor in self?.apply(CGFloat(norm)) }
        }

        engine.prepare()
        do { try engine.start(); running = true } catch { running = false }
    }

    func stop() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        running = false
        smoothed = 0
        level = 0
    }

    /// Feed an externally-computed 0...1 level (e.g. from your ASR audio frames)
    /// through the same smoothing, if you prefer not to run the built-in tap.
    func ingest(externalLevel target: CGFloat) { apply(max(0, min(1, target))) }

    private func apply(_ target: CGFloat) {
        let k: CGFloat = target > smoothed ? 0.5 : 0.15      // attack vs release
        smoothed += (target - smoothed) * k
        level = smoothed
    }

    private static func rms(of buffer: AVAudioPCMBuffer) -> Float {
        guard let ch = buffer.floatChannelData?[0] else { return 0 }
        let n = Int(buffer.frameLength)
        guard n > 0 else { return 0 }
        var sum: Float = 0
        for i in 0..<n { let s = ch[i]; sum += s * s }
        return (sum / Float(n)).squareRoot()
    }
}

// MARK: - The indicator

struct RawVoiceView: View {

    /// Live loudness, 0...1. Wire to `VoiceLevelMonitor.level` (or your own).
    var level: CGFloat

    /// Overall footprint in points. The view clamps to its container if smaller.
    var diameter: CGFloat = 220

    /// How far a dash travels past the outer waveform ring before it vanishes,
    /// as a fraction of the radius. 0.18 ≈ "reined in" (the look we landed on).
    /// Larger values start to streak.
    var dashReachRatio: CGFloat = 0.18

    /// How many dashes are in flight at once, 0...1. ~0.5 is a steady shimmer.
    var density: CGFloat = 0.5

    var lineColor: Color = .white

    /// Dark stage behind the white line work. Pass `.clear` if your surrounding
    /// UI is already dark enough.
    var stageColor: Color = Color(red: 0.051, green: 0.047, blue: 0.067)

    var body: some View {
        TimelineView(.animation) { tl in
            let t = tl.date.timeIntervalSinceReferenceDate
            Canvas { ctx, size in draw(ctx, size, t) }
        }
        .background(stageColor)
    }

    // MARK: Drawing

    private func draw(_ ctx: GraphicsContext, _ size: CGSize, _ t: Double) {
        let lvl = max(0, min(1, level))
        let D = min(diameter, min(size.width, size.height))
        let R = D / 2
        let c = CGPoint(x: size.width / 2, y: size.height / 2)
        let sw = max(0.6, D / 220)                          // stroke scale

        // Radii — all derived from R so proportions hold at any diameter.
        let coreR = (0.28 + 0.10 * lvl) * R
        let ringR = (0.40 + 0.05 * lvl) * R
        let waveR = (0.60 + 0.08 * lvl) * R
        let reach = dashReachRatio * R

        // 1. Single faint dashed ring (slow drift).
        let ringPath = Path(ellipseIn: CGRect(x: c.x - ringR, y: c.y - ringR,
                                              width: ringR * 2, height: ringR * 2))
        ctx.stroke(ringPath,
                   with: .color(lineColor.opacity(0.18)),
                   style: StrokeStyle(lineWidth: 1 * sw, dash: [2, 7], dashPhase: t * 8))

        // 2. Breathing core blob.
        let breath = 0.5 + 0.5 * sin(t * 1.6)
        var blob = Path()
        let steps = 90
        for i in 0...steps {
            let ang = Double(i) / Double(steps) * .pi * 2
            let wob = 1
                + 0.045 * sin(ang * 3 + t * 1.2)
                + 0.030 * sin(ang * 5 - t * 0.9)
                + Double(lvl) * 0.06 * sin(ang * 2 + t * 4.0)
            let r = coreR * CGFloat(wob) * CGFloat(1 + breath * 0.02)
            let p = point(c, ang, r)
            if i == 0 { blob.move(to: p) } else { blob.addLine(to: p) }
        }
        blob.closeSubpath()
        ctx.fill(blob, with: .color(lineColor.opacity(0.05 + Double(lvl) * 0.10)))
        ctx.stroke(blob, with: .color(lineColor.opacity(0.92)), lineWidth: 1.6 * sw)

        // 3. Outer reactive waveform.
        var wave = Path()
        let wsteps = 200
        for i in 0...wsteps {
            let ang = Double(i) / Double(wsteps) * .pi * 2
            var wob = 0.014 * sin(ang * 3 + t * 1.4)
            wob += Double(lvl) * (0.061 * sin(ang * 6 + t * 5.0)
                                + 0.033 * sin(ang * 11 - t * 3.0))
            let r = waveR + CGFloat(wob) * R
            let p = point(c, ang, r)
            if i == 0 { wave.move(to: p) } else { wave.addLine(to: p) }
        }
        wave.closeSubpath()
        ctx.stroke(wave, with: .color(lineColor.opacity(0.32 + Double(lvl) * 0.4)),
                   lineWidth: 1.1 * sw)

        // 4. Continuous flash-out dashes while speaking.
        //    Each emitter always has one dash in flight; it spawns just inside the
        //    waveform ring, crosses it, and fades out within `reach`. The whole
        //    field's brightness scales with how loudly the user is speaking, so it
        //    fades in/out smoothly at speech boundaries instead of popping.
        let gate = smoothstep(0.08, 0.18, lvl)              // voice presence 0...1
        if gate > 0.001 {
            let emitters = Int((0.18 + 0.64 * density) * 100) // density 0.5 → 50
            let inset = 0.02 * R
            for i in 0..<emitters {
                let ang   = rnd(i, 1) * .pi * 2
                let cps   = 1.8 + rnd(i, 3) * 1.6            // travel cycles / sec
                let frac  = ((t * cps) + rnd(i, 4)).truncatingRemainder(dividingBy: 1)
                let len   = (0.02 + rnd(i, 5) * 0.02) * R
                let r0    = waveR - inset + CGFloat(frac) * (reach + inset)
                let alpha = pow(1 - frac, 1.1) * Double(gate) * 0.9
                if alpha <= 0.01 { continue }
                var d = Path()
                d.move(to: point(c, ang, r0))
                d.addLine(to: point(c, ang, r0 + len))
                ctx.stroke(d, with: .color(lineColor.opacity(alpha)),
                           lineWidth: 1.1 * sw)
            }
        }
    }

    private func point(_ c: CGPoint, _ ang: Double, _ r: CGFloat) -> CGPoint {
        CGPoint(x: c.x + cos(ang) * r, y: c.y + sin(ang) * r)
    }

    private func smoothstep(_ a: Double, _ b: Double, _ x: CGFloat) -> CGFloat {
        let t = max(0, min(1, (Double(x) - a) / (b - a)))
        return CGFloat(t * t * (3 - 2 * t))
    }

    /// Cheap deterministic 0...1 hash so dashes scatter instead of marching in lockstep.
    private func rnd(_ i: Int, _ s: Int) -> Double {
        let v = sin(Double(i) * 12.9898 + Double(s) * 78.233) * 43758.5453
        return v - floor(v)
    }
}

// MARK: - Simulated source (for previews / on-device tuning without a mic)

@MainActor
final class SimulatedVoiceSource: ObservableObject {
    @Published private(set) var level: CGFloat = 0

    private var timer: Timer?
    private var smoothed: CGFloat = 0
    private var phaseIsSpeak = false
    private var phaseEnd: Double = 0

    func start() {
        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    func stop() {
        timer?.invalidate(); timer = nil
        smoothed = 0; level = 0
    }

    private func tick() {
        let now = Date.timeIntervalSinceReferenceDate
        if now > phaseEnd {
            phaseIsSpeak.toggle()
            phaseEnd = now + (phaseIsSpeak ? 0.45 + Double.random(in: 0...1.2)
                                           : 0.14 + Double.random(in: 0...0.5))
        }
        var target: CGFloat = 0.03
        if phaseIsSpeak {
            let syl = abs(sin(now * 11.0))
            let fast = 0.65 + 0.35 * sin(now * 27.0)
            target = CGFloat((0.28 + 0.72 * syl) * fast)
        }
        let k: CGFloat = target > smoothed ? 0.35 : 0.12
        smoothed += (target - smoothed) * k
        level = smoothed
    }
}

// MARK: - Demo (mic toggle + on-device tuning sliders)

struct RawVoiceDemoView: View {
    @StateObject private var mic = VoiceLevelMonitor()
    @StateObject private var sim = SimulatedVoiceSource()
    @State private var useMic = false
    @State private var diameter: CGFloat = 220
    @State private var reach: CGFloat = 0.18
    @State private var density: CGFloat = 0.5

    var body: some View {
        VStack(spacing: 18) {
            RawVoiceView(level: useMic ? mic.level : sim.level,
                         diameter: diameter,
                         dashReachRatio: reach,
                         density: density)
                .frame(height: 460)
                .clipShape(RoundedRectangle(cornerRadius: 16))

            VStack(spacing: 12) {
                slider("Diameter", value: $diameter, range: 120...320, suffix: "pt")
                slider("Dash reach", value: $reach, range: 0.06...0.40, suffix: "", scale: 100, suffix2: "%")
                slider("Density", value: $density, range: 0...1, suffix: "", scale: 100, suffix2: "%")
            }

            Toggle("Use microphone", isOn: $useMic)
                .onChange(of: useMic) { _, on in
                    if on {
                        mic.requestPermission { ok in if ok { mic.start() } else { useMic = false } }
                    } else {
                        mic.stop()
                    }
                }
        }
        .padding()
        .onAppear { sim.start() }
        .onDisappear { sim.stop(); mic.stop() }
    }

    private func slider(_ label: String, value: Binding<CGFloat>,
                        range: ClosedRange<CGFloat>, suffix: String,
                        scale: CGFloat = 1, suffix2: String = "") -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary).frame(width: 90, alignment: .leading)
            Slider(value: value, in: range)
            Text("\(Int((value.wrappedValue * scale).rounded()))\(scale == 1 ? suffix : suffix2)")
                .monospacedDigit().frame(width: 52, alignment: .trailing)
        }
        .font(.callout)
    }
}

// #Preview removed: the SwiftUI preview macro needs Xcode's plugin, which the
// single-file `swiftc` build doesn't load. RawVoiceDemoView above is preserved
// for when this moves into an Xcode project (the fast-follow).
