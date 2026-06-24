// swift-tools-version:5.9
//
// SwiftPM manifest for the *pure-logic* slice of VeloVox, so we can unit-test it
// without dragging in AppKit/Cocoa/Carbon. This sits ALONGSIDE the existing flat
// build (build.sh compiles VeloVox/*.swift straight into VeloVox.app); it does not
// replace or interfere with it.
//
// The library target points at the same VeloVox/ directory but uses an explicit
// `sources:` list naming ONLY the Foundation-only files. SwiftPM compiles exactly
// those and ignores every other .swift in the dir (main/HotKeys/SpeakWrite/etc.),
// so the AppKit-bound code never enters the test module.
//
// ─── Tests run on real XCTest ──────────────────────────────────────────────────
// The `VeloVoxCoreTests` `.testTarget` is driven by `swift test` against the real
// XCTest framework (Xcode is installed). The suite files use only `import XCTest`
// + `XCTestCase` + the common `XCTAssert*` surface. (Historically, before full
// Xcode was installed, these were driven through a bespoke XCTest-compatible shim
// + a plain executable runner because the Command Line Tools lack a working
// `swift test`; that shim has been retired.)
import PackageDescription

let package = Package(
    name: "VeloVoxCore",
    platforms: [
        // The app ships for macOS 26 (LSMinimumSystemVersion 26.0), but the pure
        // logic only needs Foundation; v13 is the floor that compiles cleanly here.
        .macOS(.v13)
    ],
    targets: [
        .target(
            name: "VeloVoxCore",
            path: "VeloVox",
            // The AppKit/Cocoa/Carbon-bound files live in the same dir but are NOT
            // part of this module; exclude them so SwiftPM doesn't warn about
            // "unhandled files" and intent stays explicit.
            exclude: [
                "main.swift",
                "HotKeys.swift",
                "SpeakWrite.swift",
                "RawVoice.swift",
                "Speaker.swift",
                "Capture.swift",
                "Transport.swift",
                "ReadAloud.swift",
                "Settings.swift",
            ],
            sources: [
                "Regex.swift",
                "Clean.swift",
                "Parse.swift",
                "Script.swift",
                "Pipeline.swift",
                "Config.swift",
            ],
            // -enable-testing lets the `@testable import` in the suites reach the
            // module's `internal` types (PipelineConfig, Chunk, Clean, …).
            swiftSettings: [.unsafeFlags(["-enable-testing"])]
        ),
        .testTarget(
            name: "VeloVoxCoreTests",
            dependencies: ["VeloVoxCore"],
            path: "Tests/VeloVoxCoreTests"
        ),
    ]
)
