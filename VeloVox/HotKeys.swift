// HotKeys — one shared Carbon global-hotkey manager for the whole app.
//
// Why this exists: each tool used to install its OWN Carbon event handler that
// ignored the event and unconditionally fired its controller. That's fine for a
// single-hotkey process, but Velovox runs TWO hotkeys in ONE process — and Carbon
// calls EVERY installed kEventHotKeyPressed handler on EVERY hotkey. So we install
// ONE handler that reads the EventHotKeyID out of the event and routes to the
// action registered under that id. Register/unregister are keyed by id so the
// menu bar can enable/disable each tool's hotkey independently.
import Cocoa
import Carbon.HIToolbox

// Stable ids for each tool's hotkey — the routing handler dispatches on these.
enum HotKeyID {
    static let readAloud: UInt32 = 1
    static let speakWrite: UInt32 = 2
}

enum HotKeys {
    // All Velovox hotkeys share this 4-char signature; the `id` disambiguates.
    private static let signature = OSType(0x564C5658) // 'VLVX'

    struct Registration {
        let key: UInt32
        let mods: UInt32
        let action: () -> Void
        var ref: EventHotKeyRef?
    }

    private static var regs: [UInt32: Registration] = [:]   // id → registration
    private static var handlerInstalled = false

    /// Register (or re-register) the hotkey for `id`. `spec` is parsed; if it's
    /// unparseable we fall back to (defaultKey, defaultMods). Returns the parsed
    /// human spec actually used, for logging.
    @discardableResult
    static func register(id: UInt32, spec: String,
                         defaultKey: UInt32, defaultMods: UInt32,
                         action: @escaping () -> Void) -> Bool {
        installHandlerOnce()
        unregister(id: id)   // idempotent: drop any prior binding for this id

        let parsed = parse(spec)
        let (key, mods) = parsed ?? (defaultKey, defaultMods)
        var ref: EventHotKeyRef?
        let hkid = EventHotKeyID(signature: signature, id: id)
        let status = RegisterEventHotKey(key, mods, hkid, GetApplicationEventTarget(), 0, &ref)
        regs[id] = Registration(key: key, mods: mods, action: action, ref: ref)
        let note = parsed == nil ? " — UNPARSEABLE, using default" : ""
        NSLog("velovox: hotkey id=\(id) '\(spec)'\(note) status=\(status) (0=ok; nonzero=already taken)")
        return status == noErr
    }

    static func unregister(id: UInt32) {
        if let r = regs[id]?.ref { UnregisterEventHotKey(r) }
        regs[id] = nil
    }

    /// Toggle a previously-registered hotkey on/off without losing its binding.
    /// `enabled == false` unregisters it (the key stops being captured); `true`
    /// re-registers from the stored key/mods/action.
    static func setEnabled(_ enabled: Bool, id: UInt32) {
        guard let r = regs[id] else { return }
        if enabled {
            if r.ref == nil {
                var ref: EventHotKeyRef?
                let hkid = EventHotKeyID(signature: signature, id: id)
                RegisterEventHotKey(r.key, r.mods, hkid, GetApplicationEventTarget(), 0, &ref)
                regs[id]?.ref = ref
            }
        } else {
            if let ref = r.ref { UnregisterEventHotKey(ref); regs[id]?.ref = nil }
        }
    }

    static func isEnabled(id: UInt32) -> Bool { regs[id]?.ref != nil }

    // MARK: - The single routing handler

    private static func installHandlerOnce() {
        guard !handlerInstalled else { return }
        handlerInstalled = true
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                 eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
            var hkid = EventHotKeyID()
            let err = GetEventParameter(event, EventParamName(kEventParamDirectObject),
                                        EventParamType(typeEventHotKeyID), nil,
                                        MemoryLayout<EventHotKeyID>.size, nil, &hkid)
            if err == noErr {
                let id = hkid.id
                DispatchQueue.main.async { HotKeys.regs[id]?.action() }
            }
            return noErr
        }, 1, &spec, nil, nil)
    }

    // MARK: - Spec parsing (shared by both tools)

    /// Parse e.g. "ctrl+alt+s" / "cmd+shift+space" into (keycode, Carbon modifiers).
    /// Returns nil if no recognizable key token is present.
    static func parse(_ spec: String) -> (UInt32, UInt32)? {
        var mods: UInt32 = 0
        var keyCode: UInt32? = nil
        for raw in spec.lowercased().split(separator: "+") {
            let t = raw.trimmingCharacters(in: .whitespaces)
            switch t {
            case "cmd", "command", "⌘":          mods |= UInt32(cmdKey)
            case "ctrl", "control", "⌃":         mods |= UInt32(controlKey)
            case "alt", "opt", "option", "⌥":    mods |= UInt32(optionKey)
            case "shift", "⇧":                   mods |= UInt32(shiftKey)
            default:                             keyCode = keyCodeMap[t]
            }
        }
        guard let k = keyCode else { return nil }
        return (k, mods)
    }

    static let keyCodeMap: [String: UInt32] = [
        "a":0,"s":1,"d":2,"f":3,"h":4,"g":5,"z":6,"x":7,"c":8,"v":9,"b":11,"q":12,
        "w":13,"e":14,"r":15,"y":16,"t":17,"o":31,"u":32,"i":34,"p":35,"l":37,
        "j":38,"k":40,"n":45,"m":46,
        "1":18,"2":19,"3":20,"4":21,"5":23,"6":22,"7":26,"8":28,"9":25,"0":29,
        "space":49,"return":36,"enter":36,"tab":48,"escape":53,"esc":53,
        // punctuation / symbols
        "`":50,"grave":50,"backtick":50,"-":27,"minus":27,"=":24,"equal":24,
        "[":33,"]":30,";":41,"'":39,",":43,".":47,"period":47,"/":44,"slash":44,"\\":42,
    ]
}
