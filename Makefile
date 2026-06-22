# readaloud (Hammerspoon + kokoro daemon) and the native SpeakWrite.app.
# speakwrite is no longer a Python daemon / Hammerspoon module — it's mac/SpeakWrite.app.

PY     := .venv/bin/python
HS     := /opt/homebrew/bin/hs
STATE  := $(HOME)/.local/state
MACDIR := mac
APPID  := com.marco.speakwrite
APP    := $(MACDIR)/SpeakWrite.app

.DEFAULT_GOAL := help
.PHONY: help restart-read reload-hs stop-read status \
        mac mac-run mac-start mac-debug mac-kill mac-reset mac-stats tts

help:
	@echo "Targets:"
	@echo "  make restart-read   - bounce readaloud daemon + reload Hammerspoon"
	@echo "  make reload-hs      - reload Hammerspoon only"
	@echo "  make stop-read      - stop the readaloud daemon"
	@echo "  make status         - show which daemons are running"
	@echo "  --- native SpeakWrite.app ---"
	@echo "  make mac            - compile + bundle + ad-hoc sign SpeakWrite.app"
	@echo "  make mac-start      - launch the EXISTING build, no rebuild (keeps permissions). Daily driver."
	@echo "  make mac-run        - (re)build, then launch it (ctrl+alt+S to dictate)"
	@echo "  make mac-debug      - (re)build, run in foreground with logs"
	@echo "  make mac-kill       - quit a running SpeakWrite"
	@echo "  make mac-reset      - reset Mic+Accessibility grants, then rebuild"
	@echo "  make mac-stats      - print dictation stats (7-day / last-50 / all-time wpm) from metrics.jsonl"
	@echo "  make tts            - TTS probe: hear AVSpeechSynthesizer voices (readaloud-native test)"

restart-read: stop-read
	@mkdir -p $(STATE)/readaloud
	@nohup $(PY) -m readaloud.daemon > $(STATE)/readaloud/daemon.out 2>&1 &
	@echo "readaloud daemon launched (log: $(STATE)/readaloud/daemon.out)"
	@$(MAKE) --no-print-directory reload-hs

reload-hs:
	@$(HS) -c "hs.reload()" >/dev/null 2>&1 || true
	@echo "Hammerspoon reloaded"

stop-read:
	@pkill -f readaloud.daemon 2>/dev/null || true
	@echo "readaloud daemon stopped"

status:
	@pgrep -fl readaloud.daemon || echo "readaloud: not running"

# --- native SpeakWrite.app: compile + bundle + sign, all here so signing never
#     has to be done by hand. mac-reset clears TCC grants (re-signing changes the
#     ad-hoc identity, which can invalidate Mic/Accessibility) so re-granting is clean.
mac:
	@$(MACDIR)/build.sh

mac-kill:
	@pkill -x SpeakWrite 2>/dev/null || true

mac-run: mac-kill mac
	@nohup $(APP)/Contents/MacOS/SpeakWrite >/dev/null 2>&1 &
	@echo "SpeakWrite launched (no dock icon). Press ctrl+alt+S to dictate."

# Launch the EXISTING build without recompiling/re-signing. Launches the inner
# binary DIRECTLY (detached) rather than via `open` — TCC keys the Accessibility
# grant to the direct-exec identity for this ad-hoc-signed app, and an `open`
# (LaunchServices) launch presents a different identity that the grant misses,
# silently breaking paste. Daily-driver command when code hasn't changed.
mac-start: mac-kill
	@nohup $(APP)/Contents/MacOS/SpeakWrite >/dev/null 2>&1 &
	@echo "SpeakWrite launched from the existing build (no rebuild). ctrl+alt+S to dictate."

mac-debug: mac-kill mac
	@echo "running in foreground — ctrl+C to stop. logs below:"
	@$(APP)/Contents/MacOS/SpeakWrite

mac-reset: mac-kill
	@tccutil reset Microphone $(APPID) 2>/dev/null || true
	@tccutil reset Accessibility $(APPID) 2>/dev/null || true
	@$(MACDIR)/build.sh
	@echo "TCC grants reset + rebuilt. Run 'make mac-run' and re-grant Mic + Accessibility."

# Dictation stats — read metrics.jsonl and print 7-day / last-50 / all-time wpm.
mac-stats:
	@$(PY) sw_stats.py

# TTS probe — hear AVSpeechSynthesizer (for the readaloud-native question). Speaks
# a paragraph + lists voices. Pick a voice: ./mac/tts_probe <name>  (see file header).
tts:
	@xcrun -sdk macosx swiftc $(MACDIR)/tts_probe.swift -o $(MACDIR)/tts_probe
	@./$(MACDIR)/tts_probe
