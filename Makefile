# Native macOS apps:
#   ReadAloud  (mac/ReadAloud/) — hotkey TTS reader. Targets: read / read-*
#   SpeakWrite (mac/)           — hotkey dictation.   Targets: speak / speak-*
#
# Naming rule: the VERB says whether it recompiles.
#   bare name (read / speak)  = launch the existing build, NO rebuild (daily driver)
#   *-rebuild                 = recompile + launch (after you change code)
#   *-debug                   = recompile + run in the foreground with live logs
#   *-build                   = compile + bundle + sign only, don't launch
#   *-stop                    = quit it
#   *-reset                   = reset TCC permission grants + rebuild
#
# All launches exec the inner binary DIRECTLY (never `open`): TCC keys the
# Accessibility/Mic grant to the direct-exec identity of these ad-hoc-signed apps,
# and an `open` (LaunchServices) launch presents a different identity the grant
# misses — silently breaking paste/capture. *-reset re-signs (new ad-hoc identity),
# which can invalidate grants, so it clears them for a clean re-grant.

PY     := .venv/bin/python
MACDIR := mac
APPID  := com.marco.speakwrite
APP    := $(MACDIR)/SpeakWrite.app
RADIR  := mac/ReadAloud
RAID   := com.marco.readaloud
RAAPP  := $(RADIR)/ReadAloud.app

.DEFAULT_GOAL := help
.PHONY: help status stop-read tts \
        read read-rebuild read-debug read-build read-stop read-reset \
        speak speak-rebuild speak-debug speak-build speak-stop speak-reset speak-stats

help:
	@echo "ReadAloud (TTS reader) — config at ~/.config/readaloud/config.json"
	@echo "  make read           launch the current build, NO rebuild — daily driver"
	@echo "  make read-rebuild   recompile + launch (use after changing code)"
	@echo "  make read-debug     recompile + run in foreground with live logs"
	@echo "  make read-build     compile + bundle + sign only (don't launch)"
	@echo "  make read-stop      quit it"
	@echo "  make read-reset     reset Accessibility grant + rebuild"
	@echo ""
	@echo "SpeakWrite (dictation)"
	@echo "  make speak          launch the current build, NO rebuild — daily driver"
	@echo "  make speak-rebuild  recompile + launch (use after changing code)"
	@echo "  make speak-debug    recompile + run in foreground with live logs"
	@echo "  make speak-build    compile + bundle + sign only (don't launch)"
	@echo "  make speak-stop     quit it"
	@echo "  make speak-reset    reset Mic+Accessibility grants + rebuild"
	@echo "  make speak-stats    dictation WPM stats (7-day / last-50 / all-time)"
	@echo ""
	@echo "Misc"
	@echo "  make tts            TTS probe: hear/list AVSpeechSynthesizer voices"
	@echo "  make stop-read      stop the legacy Python readaloud daemon (being retired)"
	@echo "  make status         show whether the legacy daemon is running"

# --- ReadAloud ---
read-build:
	@$(RADIR)/build.sh

read-stop:
	@pkill -x ReadAloud 2>/dev/null || true

read: read-stop
	@nohup $(RAAPP)/Contents/MacOS/ReadAloud >/dev/null 2>&1 &
	@echo "ReadAloud launched from the existing build (no rebuild). Select text + hotkey to read."

read-rebuild: read-stop read-build
	@nohup $(RAAPP)/Contents/MacOS/ReadAloud >/dev/null 2>&1 &
	@echo "ReadAloud rebuilt + launched. Select text + hotkey to read."

read-debug: read-stop read-build
	@echo "running ReadAloud in foreground — ctrl+C to stop. logs below:"
	@$(RAAPP)/Contents/MacOS/ReadAloud

read-reset: read-stop
	@tccutil reset Accessibility $(RAID) 2>/dev/null || true
	@$(RADIR)/build.sh
	@echo "Accessibility grant reset + rebuilt. Run 'make read' and re-grant Accessibility."

# --- SpeakWrite ---
speak-build:
	@$(MACDIR)/build.sh

speak-stop:
	@pkill -x SpeakWrite 2>/dev/null || true

speak: speak-stop
	@nohup $(APP)/Contents/MacOS/SpeakWrite >/dev/null 2>&1 &
	@echo "SpeakWrite launched from the existing build (no rebuild). ctrl+alt+S to dictate."

speak-rebuild: speak-stop speak-build
	@nohup $(APP)/Contents/MacOS/SpeakWrite >/dev/null 2>&1 &
	@echo "SpeakWrite rebuilt + launched. ctrl+alt+S to dictate."

speak-debug: speak-stop speak-build
	@echo "running SpeakWrite in foreground — ctrl+C to stop. logs below:"
	@$(APP)/Contents/MacOS/SpeakWrite

speak-reset: speak-stop
	@tccutil reset Microphone $(APPID) 2>/dev/null || true
	@tccutil reset Accessibility $(APPID) 2>/dev/null || true
	@$(MACDIR)/build.sh
	@echo "TCC grants reset + rebuilt. Run 'make speak' and re-grant Mic + Accessibility."

speak-stats:
	@$(PY) sw_stats.py

# --- misc ---
tts:
	@xcrun -sdk macosx swiftc $(MACDIR)/tts_probe.swift -o $(MACDIR)/tts_probe
	@./$(MACDIR)/tts_probe

stop-read:
	@pkill -f readaloud.daemon 2>/dev/null || true
	@echo "legacy readaloud daemon stopped"

status:
	@pgrep -fl readaloud.daemon || echo "legacy readaloud daemon: not running"
