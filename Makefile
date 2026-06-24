# VeloVox — one native macOS menu-bar app, two on-device voice tools:
#   • Read Aloud (⌃⌥⌘R) — speak the selected text aloud  ("speak": you hear it)
#   • Dictate    (⌃⌥S)  — live dictation at the cursor     ("write": you write it)
#
# It's ONE app/one binary now (both hotkeys always live; toggle either from the
# menu bar), so there's a single launch verb.
#
# Config: ~/.config/velovox/config.json  (sections: readAloud, speakWrite)
#
#   launch          launch the existing build, NO rebuild (daily driver)
#   rebuild         recompile + launch (after you change code)
#   debug           recompile + run in the foreground with live logs
#   build           compile + bundle + sign only, don't launch
#   stop            quit it
#   reset           reset Mic+Accessibility grants + rebuild
#
# All launches exec the inner binary DIRECTLY (never `open`): TCC keys the
# Mic/Accessibility grant to the direct-exec identity of this ad-hoc-signed app,
# and an `open` (LaunchServices) launch presents a different identity the grant
# misses — silently breaking paste/capture. `reset` re-signs (new ad-hoc
# identity), which can invalidate grants, so it clears them for a clean re-grant.

APPID := com.marco.velovox
APP   := VeloVox.app
BIN   := $(APP)/Contents/MacOS/VeloVox

.DEFAULT_GOAL := help
.PHONY: help launch rebuild debug build stop reset stats test clean

help:
	@echo "VeloVox — config at ~/.config/velovox/config.json"
	@echo "  make launch               launch the current build, NO rebuild — daily driver"
	@echo "  make rebuild              recompile + launch (use after changing code)"
	@echo "  make debug                recompile + run in foreground with live logs"
	@echo "  make build                compile + bundle + sign only (don't launch)"
	@echo "  make stop                 quit it"
	@echo "  make reset                reset Mic+Accessibility grants + rebuild"
	@echo "  make stats                dictation WPM stats (7-day / last-50 / all-time)"
	@echo "  make test                 run the VeloVoxCore unit tests (pure logic)"
	@echo "  make clean                delete build caches (.build + VeloVox.app) — cold reset"
	@echo ""
	@echo "Hotkeys: ⌃⌥⌘R reads the selection · ⌃⌥S dictates at the cursor."

build:
	@./build.sh

stop:
	@pkill -x VeloVox 2>/dev/null; pkill -x Velovox 2>/dev/null || true


launch: stop
	@nohup $(BIN) >/dev/null 2>&1 &
	@echo "VeloVox launched (no rebuild). ⌃⌥⌘R reads the selection · ⌃⌥S dictates at the cursor."

rebuild: stop build
	@nohup $(BIN) >/dev/null 2>&1 &
	@echo "VeloVox rebuilt + launched. ⌃⌥⌘R reads · ⌃⌥S dictates."

debug: stop build
	@echo "running VeloVox in foreground — ctrl+C to stop. logs below:"
	@$(BIN)

reset: stop
	@tccutil reset Microphone $(APPID) 2>/dev/null || true
	@tccutil reset Accessibility $(APPID) 2>/dev/null || true
	@./build.sh
	@echo "TCC grants reset + rebuilt. Run 'make launch' and re-grant Mic + Accessibility."

stats:
	@test -x $(BIN) || ./build.sh
	@$(BIN) --stats

# Unit tests for the pure-logic core (clean/parse/script/config). These live in a
# SwiftPM package (Package.swift) that does NOT touch the flat build.sh build.
#
# On a machine with full Xcode this is just `swift test`. This repo's machines run
# only the Command Line Tools, where SwiftPM's XCTest/swift-testing runner is
# non-functional — so the suites are driven by the `vvtests` executable instead,
# which prints an XCTest-style summary and exits nonzero on failure. We prefer the
# real `swift test` when it's available and fall back to the executable runner.
test:
	@if xcode-select -p 2>/dev/null | grep -q "Xcode.app"; then swift test; else swift run vvtests; fi

# Cold reset — delete BOTH build systems' generated output: SwiftPM's .build/
# cache (~100MB of recompilable framework modules) and the app bundle build.sh
# produces. Nothing here is source; both regenerate on the next build/test. Run
# this deliberately when build state is stale or corrupt — never as part of a
# normal build, or you throw away the incremental-compile cache every time.
clean:
	@rm -rf .build VeloVox.app
	@echo "cleaned: removed .build/ and VeloVox.app — next build/test will be a cold (re)compile."
