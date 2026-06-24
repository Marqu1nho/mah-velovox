# VeloVox — Settings/Preferences Window: Implementation Plan

A native Settings window GUI for VeloVox, a pure-AppKit `LSUIElement`/`.accessory`
menu-bar app. This plan is grounded in the actual code as of this writing; every
struct/field/function named below was verified against the source.

> **Scope guardrail:** this is a *plan only*. No `.swift` files are modified by
> writing it. The implementation it describes touches exactly two source files
> (`main.swift` + a new `Settings.swift`) plus `Package.swift`'s exclude list.

---

## 0. Ground truth (what the code actually does today)

Verified facts the design depends on:

- **No SwiftUI app lifecycle.** `VeloVox/main.swift` ends with a manual
  `NSApplication.shared` → `app.delegate = …` → `app.setActivationPolicy(.accessory)`
  → `app.run()`. There is **no** `@main`, no `App`/`Scene`, no `Settings` scene.
  The app is `LSUIElement` (`build.sh` Info.plist sets `<key>LSUIElement</key><true/>`)
  and `.accessory` — menu-bar only, no Dock icon.
- **SwiftUI is already linked.** `VeloVox/SpeakWrite.swift` uses `NSHostingView`
  (line 215: `orbHost = NSHostingView(rootView: RawVoiceHost(...))`) and
  `VeloVox/RawVoice.swift` is SwiftUI. So `import SwiftUI` + `NSHostingController`
  is already a proven, compiled-in pattern — no new framework/linker risk.
- **Config is a single global `var`.** `VeloVox/Config.swift` line 315:
  `var VELOVOX = VeloVoxConfig.load()`. It is read **live** at most use-sites
  (e.g. `VELOVOX.speakWrite.cueStart`, `…engineKind`, `…dictationEmoji` are read
  at session start; `Speaker` reads `…voiceSpec`/`…speechRate` per read-aloud).
- **The save round-trip already exists and is safe.** `SpeakWrite.swift`
  `persistGeometry()` (lines 439–443) mutates `VELOVOX.speakWrite.hud.*` then calls
  `VeloVoxConfig.save()`. `save()` (Config.swift 294–296) calls
  `write(VELOVOX, to: fileURL)`, which **re-encodes the WHOLE `VeloVoxConfig`
  struct** (`JSONEncoder`, `.prettyPrinted/.withoutEscapingSlashes/.sortedKeys`).
  This is the exact model the Settings window must reuse.
- **Load behavior.** `VeloVoxConfig.load()` (267–290): if the file is missing it
  writes full defaults (or migrates old split configs); if present it decodes;
  on decode error it logs and returns `.fallback` **without** overwriting the bad
  file. Each section decodes independently (`init(from:)`, 245–249) so a malformed
  section falls back on its own.
- **Build picks up new files automatically.** `build.sh` line 19:
  `swiftc -O VeloVox/*.swift -o …`. A new `VeloVox/Settings.swift` is compiled with
  zero build-script changes.
- **The test module is curated, not glob.** `Package.swift` uses an explicit
  `sources:` allow-list (Regex/Clean/Parse/Script/Pipeline/Config) **and** an
  `exclude:` list of AppKit-bound files. A new AppKit/SwiftUI `Settings.swift`
  **must** be added to `exclude:` (see §4) or SwiftPM will warn about an unhandled
  file in the dir.

---

## 1. Windowing approach

### Recommendation: **(a) AppKit `NSWindowController` hosting a SwiftUI view via `NSHostingController`.**

Rationale, weighed against the alternatives:

| Option | Verdict | Why |
|---|---|---|
| **(a) `NSWindowController` + `NSHostingController`/`NSHostingView`** | **CHOSEN** | SwiftUI is already linked and used (`NSHostingView` in SpeakWrite.swift). SwiftUI gives forms/sliders/toggles/pickers almost for free via `@State`/`Form`, while the *window + activation* stays in AppKit where we already control `.accessory`/TCC. Smallest code, no lifecycle surgery. |
| (b) Pure AppKit `NSViewController` + manual `NSSlider`/`NSButton`/`NSPopUpButton` + target/action | Rejected | Works and is dependency-free, but every control needs manual wiring + Auto Layout. 3–5× the code of a SwiftUI `Form` for the same v0 surface, with no benefit since SwiftUI is already in the binary. |
| (c) SwiftUI `Settings` scene / `@main App` | **Rejected — risky** | Requires converting `main.swift` to the SwiftUI `App` lifecycle. That would replace the manual `app.run()` + `setActivationPolicy(.accessory)` and the hand-built `NSStatusItem` menu, putting the `LSUIElement`/`.accessory`/menu-bar/TCC setup at risk for zero gain. **Do not do this.** |

### Activation nuance (critical for a menu-bar agent)

An `.accessory` app is **not** a regular foreground app, so a freshly shown window
will not automatically come to the front or take key focus. The window-open code
**must**:

1. Lazily create the window controller (single shared instance — reuse on reopen,
   don't leak a new window each time the menu item is clicked).
2. `NSApp.activate(ignoringOtherApps: true)` to pull VeloVox forward.
3. `window.makeKeyAndOrderFront(nil)` and `window.center()` on first show.

**Do NOT** call `app.setActivationPolicy(.regular)` to show the window — that would
add a Dock icon and break the `LSUIElement` contract. `.accessory` + `NSApp.activate`
is sufficient to show and focus a normal `NSWindow`. (Confirmed approach for
accessory apps; the window is a standard titled `NSWindow`, not a panel.)

Use a plain `NSWindow` with style mask `[.titled, .closable, .miniaturizable]`
(no `.resizable` needed for a fixed Form; add `.resizable` only if we adopt a
scrollable form later). `isReleasedWhenClosed = false` so closing just hides it and
the shared controller survives for the next open.

### Menu wiring change (`main.swift`, `makeMenu()`)

Today (lines 207–213):

```swift
let edit = NSMenuItem(title: "Edit Config…", action: #selector(editConfig), keyEquivalent: ",")
…
let reveal = NSMenuItem(title: "Reveal Config in Finder", action: #selector(revealConfig), keyEquivalent: "")
```

**Change:**

- Add a new **"Settings…"** item bound to a new `@objc func openSettings()`,
  and give it the `⌘,` key equivalent (the conventional Preferences shortcut).
- **Move** `keyEquivalent: ","` off "Edit Config…" (a menu can't have two `⌘,`).
  Keep "Edit Config…" as a raw-JSON escape hatch (it opens the file in the default
  editor — still useful for the deferred-v1 list/map knobs), just without a shortcut.
- **Keep "Reveal Config in Finder" unchanged.**

Resulting block (illustrative):

```swift
let settings = NSMenuItem(title: "Settings…", action: #selector(openSettings), keyEquivalent: ",")
settings.target = self
menu.addItem(settings)

let edit = NSMenuItem(title: "Edit Config (JSON)…", action: #selector(editConfig), keyEquivalent: "")
edit.target = self
menu.addItem(edit)

let reveal = NSMenuItem(title: "Reveal Config in Finder", action: #selector(revealConfig), keyEquivalent: "")
reveal.target = self
menu.addItem(reveal)
```

`AppDelegate` gains one stored property (the lazy window controller) and one
selector:

```swift
private var settingsWC: SettingsWindowController?

@objc private func openSettings() {
    if settingsWC == nil { settingsWC = SettingsWindowController() }
    NSApp.activate(ignoringOtherApps: true)
    settingsWC?.showWindow(nil)
    settingsWC?.window?.makeKeyAndOrderFront(nil)
}
```

---

## 2. Config round-trip — the hard part

### The flow

```
VELOVOX (global, already loaded)  →  SwiftUI @State draft  →  edit in UI  →
mutate VELOVOX in place  →  VeloVoxConfig.save()  →  ~/.config/velovox/config.json
```

**Read.** Do **not** re-read the file. The live in-memory `VELOVOX` is the current
truth (it's what every feature reads). Seed the SwiftUI view's `@State`/bindings
from `VELOVOX.readAloud.*` and `VELOVOX.speakWrite.*` accessors.

**Bind.** Two viable binding styles; recommend **explicit get/set `Binding`s** that
write straight through to `VELOVOX`, mirroring `persistGeometry()`:

```swift
Slider(value: Binding(
    get: { VELOVOX.readAloud.rate ?? 0.5 },
    set: { VELOVOX.readAloud.rate = $0 }), in: 0.3...0.7)
```

This keeps `VELOVOX` as the single source of truth and avoids a parallel draft
struct that could drift. A "Save" button (or save-on-close) then just calls
`VeloVoxConfig.save()`. (Alternative: a local draft `@State` copy committed to
`VELOVOX` on Save — fine too, but more code and a second source of truth.)

> Note: writing the optional fields directly (`VELOVOX.readAloud.rate = $0`)
> ensures the **key is materialized** in the struct, so it will be present in the
> re-encoded JSON — satisfying the CLAUDE.md "knob must be visible on disk" rule
> automatically, because `save()` re-encodes the full struct and `sortedKeys`
> writes every non-nil field.

**Save.** Call `VeloVoxConfig.save()` — the **existing** method. Because it
re-encodes the entire `VeloVoxConfig` (both sections, all fields), **fields the UI
doesn't expose are preserved** as long as they're still present in the in-memory
`VELOVOX`. They are: `VELOVOX` was decoded from the same file at launch, so
`mute`, `replace`, `replacements`, `by_app`, geometry `x/y`, etc. all round-trip
untouched through the struct. The UI only mutates the specific fields it binds.

### The two real risks — called out explicitly

1. **Comments / hand-formatting are LOST on save.** `config.json` is decoded by
   `JSONDecoder` into Codable structs and re-encoded by `JSONEncoder`. JSON has no
   comments, and `JSONEncoder` emits its own canonical formatting (sorted keys,
   2-space). **Any comments or custom whitespace a user added by hand are
   destroyed the first time they hit Save in the GUI.** This is *already true* of
   the existing `persistGeometry()` path (moving the HUD rewrites the file), so the
   GUI doesn't introduce a new class of risk — but it widens the blast radius
   (more frequent saves). **Recommendation:** accept it, and document it. The
   committed `config.example.json` has no comments, and the on-disk default written
   by `load()` has none either, so in practice there's nothing to lose. Optionally
   surface a one-line note in the Settings window footer: *"Saving rewrites
   config.json in canonical form (comments are not preserved)."* Do **not** attempt
   a comment-preserving JSON5/merge layer for v0 — it's disproportionate effort.

2. **Truly-unknown keys (not in the structs) are LOST.** Any JSON key with no
   corresponding Codable field is dropped on decode and therefore absent on
   re-encode. This is **already** the behavior of the app today (load→any save),
   not new. The structs are a superset of `config.example.json`, so there are no
   such keys in the shipped shape. We accept this; it's the existing contract.

### Fields/structs the round-trip touches

All from `Config.swift`: top-level `VeloVoxConfig{readAloud, speakWrite}`,
`ReadAloudConfig`, `SpeakWriteConfig`, and their sub-structs `CleanConfig`,
`HUDConfig`, `OrbConfig`, `CueConfig`, `MetricsConfig`, `AudioConfig`,
`DictationConfig`, `HeadersConfig`, `PausesConfig`, `MuteConfig`,
`CodeBlocksConfig`, `AlertsConfig`, `LimitsConfig`. v0 mutates only a subset
(§3); the rest pass through `save()` unchanged.

### CLAUDE.md contract — also update the example file

Per CLAUDE.md, every config-accessible feature must be visible in `config.json`.
Because v0 only exposes knobs that **already exist** in both `config.example.json`
and the user's real `~/.config/velovox/config.json` (verified — all v0 fields below
are present in both), **no example-file change is required for v0.** If a future GUI
knob introduces a *new* field, that PR must (a) add it to the struct + accessor +
`fallback`, AND (b) add it to `config.example.json` and the user's real file — same
rule, unchanged.

---

## 3. Scope — v0 vs deferred v1

### v0 — simple controls (clean 1:1 map to a control)

| Section | UI control | Config field (Codable) | Accessor / default | Notes |
|---|---|---|---|---|
| Read Aloud | Slider 0.3–0.7 | `ReadAloudConfig.rate: Double?` | `speechRate` / 0.5 | Speech rate. |
| Read Aloud | Voice picker (dropdown) | `ReadAloudConfig.voice: String?` | `voiceSpec` / `com.apple.voice.premium.en-GB.Serena` | Populate from `NSSpeechSynthesizer.availableVoices` (or `AVSpeechSynthesisVoice.speechVoices()`); store the voice identifier string. |
| Read Aloud | Text field (hotkey) | `ReadAloudConfig.hotkey: String?` | `hotkeySpec` / `ctrl+alt+cmd+r` | Free text like `ctrl+alt+cmd+r`. **Live-apply caveat — see §5.** Show parsed form via existing `prettyHotkey(_:)`. |
| Read Aloud | 4× dropdowns | `CleanConfig.rejoin/urls/paths/emoji: String?` + `split_identifiers: Bool?` | pipeline() defaults: `smart`/`domain`/`basename`/`skip`/`true` | Enum-ish string knobs; offer the known options as a `Picker`. `split_identifiers` is a toggle. |
| Read Aloud | Toggle | `HeadersConfig.treat_all_caps_lines_as_headers: Bool?` | `true` | Simple bool. |
| Dictate | Segmented/Picker | `SpeakWriteConfig.engine: String?` | `engineKind` → `speech`/`dictation` | Two-value toggle. |
| Dictate | Picker | `SpeakWriteConfig.displayMode: String?` | `mode` → `hud`/`orb`/`off` | Three values. |
| Dictate | Text field (hotkey) | `SpeakWriteConfig.hotkey: String?` | `hotkeySpec` / `ctrl+alt+s` | Same live-apply caveat as RA hotkey. |
| Dictate | Picker | `DictationConfig.mode: String?` | `dictationMode` → `formal`/`casual` | Only meaningful when engine = dictation; can disable when engine=speech. |
| Dictate | Toggle | `DictationConfig.punctuation: Bool?` | `false` | dictation engine only. |
| Dictate | Toggle | `DictationConfig.emoji: Bool?` | `false` | dictation engine only. |
| Dictate | Toggle | `HUDConfig.commitOnly: Bool?` | `hudCommitOnly` / false | HUD-mode only. |
| Dictate | Slider | `HUDConfig.alpha: Double` (non-optional) | direct, 0–1 | HUD opacity. |
| Dictate | Slider/stepper | `HUDConfig.fontSize: Double` (non-optional) | direct | HUD font size. **Read at HUD construction — relaunch to apply (§5).** |
| Dictate | Slider/stepper | `OrbConfig.size: Double` | `orbSize` / 150 | Orb mode. |
| Dictate | 9-grid Picker | `OrbConfig.position: String?` | `orbPosition` / center | top-left…center…bottom-right. |
| Dictate | Toggle | `CueConfig.sound: Bool?` | `cueSound` / true | Master cue switch. |
| Dictate | Pickers | `CueConfig.start/stop: String?` | `cueStart` Tink / `cueStop` nil | System sound names (Tink/Pop/Glass/Purr/Ping…). |
| Dictate | Slider | `CueConfig.volume: Double?` | `cueVolume` / 0.5 | 0–1. |
| Dictate | Toggle | `CueConfig.bloom: Bool?` | `cueBloom` / true | Orb breathe-on-start. |
| Dictate | Toggle | `MetricsConfig.enabled: Bool?` | `metricsEnabled` / true | WPM logging. |
| Dictate | Toggle | `MetricsConfig.flash: Bool?` | `metricsFlash` / true | WPM toast. |
| Dictate | Toggle | `AudioConfig.warnBluetoothInput: Bool?` | `warnBluetoothInput` / true | One-time BT nudge. |
| Dictate | Text field | `SpeakWriteConfig.locale: String` (non-optional) | direct, `en-US` | Could be a dropdown later; text field is fine for v0. |

**v0 LAYOUT — DECIDED (Option C): a single scrolling pane, NOT tabs or a sidebar.**
Use one SwiftUI `Form { Section("…") { … } }` with `.formStyle(.grouped)`; the
`Section` headers are the horizontal dividers between groups. Sections: **"Read
Aloud"** and **"Dictate"** (mirroring the two config sections), optionally a third
**"General"** later. Marco chose this over tabs (option A) and the icon sidebar
(option B) for v0. The icon-forward `NavigationSplitView` **sidebar is the v1
north star** — graduate to it once the v1 list/map editors land and each section
earns a roomy pane. The data model + Save round-trip are identical across all
three layouts, so Option C costs nothing later. Disable engine-specific controls
(dictation punctuation/emoji/mode) when `engine != "dictation"`, and HUD vs orb
controls based on `displayMode`, for clarity.

### Deferred to v1 — complex list/map editors

These stay **raw-JSON only** (via the kept "Edit Config (JSON)…" menu item) for now,
because each needs dynamic add/remove-row UI (variable-length lists / key-value maps)
that is disproportionate for v0:

| Field | Type | Why deferred |
|---|---|---|
| `SpeakWriteConfig.replacements: [Replacement]` | ordered array of `{say, insert}` | Add/remove/reorder rows; `insert` can contain `\n`. Needs a table editor; order is semantically significant. |
| `ReadAloudConfig.replace: [String: String]` | dictionary | Add/remove key-value rows. |
| `MuteConfig.global: [String]?` | string list | Add/remove rows. |
| `MuteConfig.blocks: [String]?` | string list | Add/remove rows. |
| `MuteConfig.by_app: [String:[String]]?` | map of app → list | Nested editor (per-app lists). Hardest of all. |
| `DictationConfig.capitalExceptions: [String]?` | string list | Add/remove rows. |
| `HeadersConfig` numeric ms knobs, `PausesConfig` ms knobs, `CodeBlocksConfig`, `AlertsConfig.y_pct`, `LimitsConfig.max_selection_chars` | scalars | *Could* be v0 sliders/steppers, but they're niche tuning knobs; leave for a "v0.5 Advanced tab" to keep v0 focused. Not hard — just out of the initial cut. |

All deferred fields **survive Save untouched** (§2) because `save()` re-encodes the
full struct from the in-memory `VELOVOX` that was decoded from disk.

---

## 4. File-by-file change list (v0)

### `VeloVox/main.swift` — edit (menu wiring + open handler)

- In `makeMenu()`: add **"Settings…"** (`⌘,`); remove `⌘,` from "Edit Config…"
  (retitle to "Edit Config (JSON)…", no shortcut); leave "Reveal Config in Finder".
- Add stored property `private var settingsWC: SettingsWindowController?`.
- Add `@objc private func openSettings()` (lazy-create + `NSApp.activate` +
  `makeKeyAndOrderFront`, per §1).
- No change to the `app.run()` / `.accessory` tail. No change to hotkey registration.

### `VeloVox/Settings.swift` — **new file**

Contains:
- `final class SettingsWindowController: NSWindowController` — builds an `NSWindow`
  (`[.titled, .closable, .miniaturizable]`, `isReleasedWhenClosed = false`,
  `center()`), sets `contentViewController = NSHostingController(rootView: SettingsView())`.
- `struct SettingsView: View` — the SwiftUI form/tabs from §3, with explicit
  get/set `Binding`s into `VELOVOX`, a **Save** button calling `VeloVoxConfig.save()`
  (and/or save-on-close via the window controller's `windowWillClose`), and an
  optional footer note about canonical-rewrite (§2 risk 1).
- `import SwiftUI` + `import AppKit`/`Cocoa`. Proven pattern (`NSHostingView` already
  used in `SpeakWrite.swift`).

**Build pickup:** `build.sh` compiles `VeloVox/*.swift` (line 19), so `Settings.swift`
is included automatically — **no `build.sh` change**.

### `Package.swift` — edit (exclude the new AppKit file from the test module)

`Settings.swift` imports SwiftUI/AppKit, so it must **not** enter the pure
`VeloVoxCore` test library (CLT has no GUI test runner anyway). Add it to the
`exclude:` array (alongside `main.swift`, `SpeakWrite.swift`, `RawVoice.swift`, …):

```swift
exclude: [
    "main.swift",
    "HotKeys.swift",
    "SpeakWrite.swift",
    "RawVoice.swift",
    "Speaker.swift",
    "Capture.swift",
    "Transport.swift",
    "ReadAloud.swift",
    "Settings.swift",   // ← NEW: AppKit/SwiftUI-bound, stays out of the test module
],
```

The `sources:` allow-list (Regex/Clean/Parse/Script/Pipeline/Config) is unchanged —
`Settings.swift` is **not** added there. If omitted from `exclude`, SwiftPM emits an
"unhandled files" warning (not a hard error, but the repo keeps this list explicit).

### No other files change

`config.example.json` is **not** touched for v0 (all exposed knobs already present —
§2). No new Info.plist keys (no new TCC-gated capability). `build.sh` unchanged.

---

## 5. Risks & validation

### Live-apply vs relaunch (be precise — this is the subtle part)

`VELOVOX` is a global `var` read **live** at most use-sites, so many knobs take
effect on the **next use** without relaunch — but several are latched and need a
relaunch. Verified per field:

- **Take effect on next use (no relaunch):** Read-Aloud `rate`/`voice` (read in
  `ReadAloud.swift` per invocation, lines 46), `clean`/headers pipeline knobs
  (`pipeline()` rebuilt each read), all Dictate `engine`/`dictation.*`/`cue.*`/
  `metrics.*`/`audio.*`/`displayMode`/`hud.commitOnly`/`hud.alpha`/`orb.*` —
  these are read at session start or HUD show. (`displayMode`/`orb.size` are read
  in `SpeakWrite.swift` when the panel is (re)built per session — verified lines
  322/572/577/587.)
- **Need a RELAUNCH:** the **two hotkeys**. `registerHotKey()` runs **once** in
  `applicationDidFinishLaunching` (main.swift 100–101) reading `…hotkeySpec`; there
  is **no re-register path**. Changing a hotkey in the GUI writes the file but the
  old hotkey stays bound until relaunch. → **The Settings window must show a
  "relaunch to apply" note next to the hotkey fields.** (A future enhancement could
  add a `HotKeys.unregister`+re-register, but that's out of v0 scope.)
- **`HUDConfig.fontSize`** is captured into a `let` at controller construction
  (`SpeakWrite.swift` line 200: `private let fontSize = CGFloat(VELOVOX.speakWrite.hud.fontSize)`).
  Whether it re-reads depends on controller lifetime; treat fontSize as
  **relaunch-to-be-safe** and note it. *(Stated as a known uncertainty rather than
  guessed.)*

> **Honest uncertainty:** I did not exhaustively trace whether the SpeakWrite
> controller is rebuilt per session or persists for the app's lifetime. The geometry
> read-back (`hud.x/y/width/height`) is written by the app itself; if the user also
> edits those in a future GUI it could race with `persistGeometry()`. v0 does **not**
> expose `hud.x/y`, so no race in this cut. Validate fontSize/displayMode live
> behavior empirically (below) before claiming "live" in user-facing copy.

### TCC / signing

- **No new TCC surface.** A settings `NSWindow` requests no Mic/Accessibility/Speech
  capability; it only reads/writes a file under `~/.config`. Mic
  (`NSMicrophoneUsageDescription`) and Accessibility (the `AXIsProcessTrustedWithOptions`
  prompt in `applicationDidFinishLaunching`) are untouched.
- **Signing/grant caveat (pre-existing, from MEMORY):** ad-hoc rebuilds can reset
  Mic/Accessibility grants. `build.sh` already mitigates by signing with a **stable
  bundle id** (`--identifier com.marco.velovox`, line 51) so TCC keys on identity,
  not hash. Adding `Settings.swift` does **not** change the bundle id or entitlements,
  so it does **not** affect grants. (No entitlements file in play; nothing to edit.)
- Activating via `NSApp.activate(ignoringOtherApps:)` keeps `.accessory` — **no Dock
  icon appears**, `LSUIElement` contract intact.

### Manual verification checklist

1. `make build` / `build.sh` compiles cleanly with the new `Settings.swift` (confirms
   glob pickup) and `swift run vvtests` still builds (confirms `Package.swift` exclude
   is correct — no "unhandled file" warning, no AppKit leaking into the test module).
2. Launch the app, click the menu-bar icon → **Settings…** (or press `⌘,` while a
   VeloVox window context is key). Window appears, comes to front, no Dock icon
   appears (LSUIElement intact).
3. Change a **live** knob (e.g. Read-Aloud `rate`), Save, close, then trigger
   Read-Aloud (`⌃⌥⌘R`) on a selection → audibly faster/slower **without relaunch**.
4. `cat ~/.config/velovox/config.json` → the changed field is present and updated;
   spot-check that an **unexposed** field (e.g. a `replacements` entry, or
   `mute.global`) is **still present and unchanged** (round-trip preservation).
5. Change a **hotkey**, Save → confirm the file updated but the old hotkey still
   fires until relaunch (matches the documented "relaunch to apply" note); relaunch →
   new hotkey fires.
6. Confirm Mic/Accessibility grants survive the rebuild (or are re-prompted only due
   to the pre-existing ad-hoc-signing behavior, not the new window).

---

## 6. Effort estimate

| Step | Work | Est. |
|---|---|---|
| 1 | `main.swift` menu wiring + `openSettings()` + lazy WC property | ~0.5 h |
| 2 | `Settings.swift`: `SettingsWindowController` + window setup + activation | ~1 h |
| 3 | `SettingsView` SwiftUI form — Read Aloud tab (rate/voice/hotkey/clean/headers) | ~1.5 h |
| 4 | `SettingsView` — Dictate tab (engine/displayMode/dictation/hud/orb/cue/metrics/audio) | ~2.5 h |
| 5 | get/set bindings into `VELOVOX` + Save (`VeloVoxConfig.save()`) + save-on-close | ~1 h |
| 6 | Enable/disable logic (engine-gated, hud/orb-gated) + "relaunch to apply" notes | ~0.5 h |
| 7 | `Package.swift` exclude entry | ~5 min |
| 8 | Manual validation pass (§5 checklist), incl. live-vs-relaunch empirical check | ~1 h |
| **Total (v0)** | | **~8 h** (one focused day) |

Deferred v1 (list/map editors for `replacements`, `replace`, `mute.*`,
`capitalExceptions`, plus an "Advanced" numeric-tuning tab) is a separate effort,
roughly another **1–1.5 days**, gated on real usage demand.

---

## 7. Dev environment — VS Code ↔ Xcode (prep + division of labor)

Adding Xcode is **purely additive** — VS Code keeps working exactly as today.
The build machinery is all command-line / IDE-agnostic, so nothing is removed:

- **VS Code stays the daily driver.** `make rebuild` / `make launch` / `make stop`
  (→ `build.sh`), `make test`, git, and the non-Swift files (`Makefile`,
  `build.sh`, `config.json`, markdown) all live here. The Swift extension provides
  SourceKit-LSP squiggles + LLDB stepping. None of this changes when Xcode arrives.
- **`build.sh` stays the SINGLE SOURCE OF TRUTH for the real app build.** It does
  the ad-hoc signing with the stable bundle id (`com.marco.velovox`) that keeps
  Mic/Accessibility (TCC) grants alive. Xcode must **not** become a competing
  app-build path — it's for previews / Test Navigator / Instruments only.

### Prep once Xcode is installed

1. **Tests → real `swift test`.** Open `Package.swift` directly in Xcode to get the
   Test Navigator (gutter diamonds, re-run-failed). Then flip `vvtests` from
   `.executableTarget` to a `.testTarget` with `import XCTest` and delete the
   `TestHarness.swift` shim + `Tests/.../main.swift` (the CLT-only workaround — see
   the `velovox-test-suite` memory). The suite files compile unchanged. `make test`
   already auto-switches to `swift test` when Xcode is present (Makefile `test:`).

2. **SwiftUI previews for `Settings.swift` — the wrinkle.** The app builds via
   `build.sh` (no `.xcodeproj`), and `Settings.swift` is `exclude:`d from the
   SwiftPM package (§4), so **Xcode has no buildable target containing it → previews
   won't "just work."** Give the preview a buildable context (resolve at
   implementation time, ~15–30 min):
   - **(a) Recommended:** add a small **preview-only SwiftPM target** in
     `Package.swift` that includes `Settings.swift` and depends on `VeloVoxCore`
     (which holds `VELOVOX`/`Config`). A SwiftPM macOS target may `import SwiftUI`/
     `AppKit`, so the view compiles and previews. `Settings.swift` is then compiled
     in two contexts (build.sh's app + this preview target) — the **same dual-compile
     pattern the pure files already use** (build.sh + `VeloVoxCore`), so it's
     consistent, not novel. Keep it out of build.sh's real app output.
   - (b) Generate/maintain a thin `.xcodeproj` for the app target — more setup +
     drift risk; only if (a) proves insufficient.
   - (c) Prototype `SettingsView` against a mock/in-memory config in a previewable
     spot first, then move it into `Settings.swift`.

**Net division of labor:** VS Code for code / tests / git / `build.sh` launches;
Xcode opened on `Package.swift` for SwiftUI previews + Test Navigator + Instruments.
The VS Code flow is untouched.

---

## Summary of guarantees this plan upholds

- **`LSUIElement`/`.accessory` intact** — no SwiftUI lifecycle, no Dock icon; window
  shown via `NSApp.activate` + `makeKeyAndOrderFront`.
- **Config contract intact** — Save re-encodes the whole `VeloVoxConfig` from the live
  `VELOVOX`; unexposed/known fields survive; v0 exposes only fields already in
  `config.example.json`, so no example-file change needed.
- **Build + test isolation intact** — `Settings.swift` auto-compiled by `build.sh`'s
  glob, and excluded from the pure `VeloVoxCore` test module in `Package.swift`.
- **No TCC/grant impact** — no new capabilities, bundle id, or entitlements.
- **Comment-loss + hotkey-relaunch risks documented**, not hidden.
