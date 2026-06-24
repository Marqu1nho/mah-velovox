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
.PHONY: help launch rebuild debug build stop reset stats

help:
	@echo "VeloVox — config at ~/.config/velovox/config.json"
	@echo "  make launch               launch the current build, NO rebuild — daily driver"
	@echo "  make rebuild              recompile + launch (use after changing code)"
	@echo "  make debug                recompile + run in foreground with live logs"
	@echo "  make build                compile + bundle + sign only (don't launch)"
	@echo "  make stop                 quit it"
	@echo "  make reset                reset Mic+Accessibility grants + rebuild"
	@echo "  make stats                dictation WPM stats (7-day / last-50 / all-time)"
	@echo ""
	@echo "Hotkeys: ⌃⌥⌘R reads the selection · ⌃⌥S dictates at the cursor."

build:
	@./build.sh

stop:
	@pkill -x VeloVox 2>/dev/null || true


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
