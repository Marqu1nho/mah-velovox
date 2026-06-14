#!/usr/bin/env bash
# readaloud installer — idempotent setup.
#
# Usage: ./install.sh [--no-kokoro]
#
# - Verifies uv is installed (instructs if missing).
# - Runs `uv sync` in this repo (creates .venv, installs the package + deps).
# - Resolves the ABSOLUTE path to the project CLI (<repo>/.venv/bin/readaloud)
#   and writes ~/.hammerspoon/readaloud_paths.lua so Hammerspoon invokes the
#   CLI by absolute path (never relying on PATH).
# - Downloads kokoro model files (skip if present; skipped entirely with
#   --no-kokoro or when engine: say and models absent — still offered).
# - Ensures ~/.config/readaloud/ exists (config is optional; defaults apply
#   when absent; see README.md for all options).
# - Symlinks the lua module into ~/.hammerspoon/ and wires init.lua idempotently.
# - Installs Hammerspoon via brew --cask if absent (instructs if brew missing).
# - Prints a post-install checklist.

set -euo pipefail

NO_KOKORO=0
for arg in "$@"; do
  case "$arg" in
    --no-kokoro) NO_KOKORO=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# Resolve the repo root (directory containing this script).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# 0. Architecture sanity (spec assumes Apple Silicon for some bits).
# ---------------------------------------------------------------------------
ARCH="$(uname -m)"
if [ "$ARCH" != "arm64" ]; then
  warn "Detected arch '$ARCH' (expected arm64). kokoro-onnx wheels target arm64; continuing anyway."
fi

# ---------------------------------------------------------------------------
# 1. uv check + sync.
# ---------------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  warn "uv is not installed."
  echo "    Install it with:  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "    (or: brew install uv), then re-run this script."
  exit 1
fi

say "Syncing Python environment with uv (this installs readaloud + deps)…"
( cd "$REPO" && uv sync )

CLI="$REPO/.venv/bin/readaloud"
if [ ! -x "$CLI" ]; then
  warn "Expected CLI at $CLI but it is not executable. uv sync may have failed."
  exit 1
fi
say "CLI resolved: $CLI"

# ---------------------------------------------------------------------------
# 2. Homebrew + Hammerspoon.
# ---------------------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  warn "Homebrew not found. Install it from https://brew.sh, then re-run, or"
  warn "install Hammerspoon manually from https://www.hammerspoon.org/."
else
  if [ -d "/Applications/Hammerspoon.app" ] || brew list --cask hammerspoon >/dev/null 2>&1; then
    say "Hammerspoon already installed."
  else
    say "Installing Hammerspoon via brew…"
    brew install --cask hammerspoon
  fi
fi

# ---------------------------------------------------------------------------
# 3. Kokoro model files.
# ---------------------------------------------------------------------------
MODEL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/readaloud/models"
ONNX="$MODEL_DIR/kokoro-v1.0.onnx"
VOICES="$MODEL_DIR/voices-v1.0.bin"
BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

# Download one model file. Returns non-zero (without aborting under set -e,
# because callers test it) if the download fails, cleaning up the partial.
download_model() {
  local name="$1"
  local dest="$MODEL_DIR/$name"
  if [ -f "$dest" ]; then
    say "kokoro model present: $name (skipping download)."
    return 0
  fi
  say "Downloading $name (kokoro models are large; ~340 MB total)…"
  if curl -fSL --retry 3 --progress-bar -o "$dest.part" "$BASE_URL/$name"; then
    mv "$dest.part" "$dest"
  else
    rm -f "$dest.part"
    return 1
  fi
}

if [ "$NO_KOKORO" -eq 1 ]; then
  say "--no-kokoro: skipping kokoro model download."
else
  mkdir -p "$MODEL_DIR"
  if ! download_model "kokoro-v1.0.onnx" || ! download_model "voices-v1.0.bin"; then
    # Non-fatal: the default engine is `say`, which needs no model files. A
    # network failure must not abort the whole install.
    warn "kokoro model download failed — 'engine: say' still works."
    warn "Re-run ./install.sh later to retry, or pass --no-kokoro to silence this."
  fi
fi

# ---------------------------------------------------------------------------
# 4. Config directory (config is optional — defaults apply when absent).
# ---------------------------------------------------------------------------
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/readaloud"
CONFIG="$CONFIG_DIR/config.yaml"
mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG" ]; then
  say "Config found at $CONFIG"
else
  say "No config needed to start — defaults apply."
  echo "    To customize, create $CONFIG"
  echo "    See README.md for all options."
fi

# ---------------------------------------------------------------------------
# 5. Hammerspoon wiring.
# ---------------------------------------------------------------------------
HS_DIR="$HOME/.hammerspoon"
mkdir -p "$HS_DIR"

# 5a. Generate the install-time paths file (absolute CLI + repo).
cat > "$HS_DIR/readaloud_paths.lua" <<EOF
-- Generated by readaloud install.sh — do not edit by hand.
return {
  repo = "$REPO",
  cli = "$CLI",
}
EOF
say "Wrote $HS_DIR/readaloud_paths.lua"

# 5b. Symlink the lua module into ~/.hammerspoon/.
ln -sf "$REPO/hammerspoon/readaloud.lua" "$HS_DIR/readaloud.lua"
say "Linked readaloud.lua into $HS_DIR"

# 5c. Idempotently wire require("readaloud") into init.lua.
INIT="$HS_DIR/init.lua"
touch "$INIT"
if grep -q 'require("readaloud")' "$INIT" || grep -q "require('readaloud')" "$INIT"; then
  say "init.lua already requires readaloud."
else
  printf '\nrequire("readaloud")\n' >> "$INIT"
  say "Added require(\"readaloud\") to $INIT"
fi

# ---------------------------------------------------------------------------
# 6. Post-install checklist.
# ---------------------------------------------------------------------------
cat <<EOF

$(printf '\033[1;32m')readaloud installed.$(printf '\033[0m')

Next steps:
  1. Grant Hammerspoon Accessibility permission:
       System Settings -> Privacy & Security -> Accessibility -> enable Hammerspoon.
       (Required for the Cmd-C simulation and the window-read AX walk.)
  2. Launch Hammerspoon (or reload its config) so the hotkeys bind.
  3. Map your mouse button to the toggle hotkey (default Ctrl-Alt-Cmd-S) in
     your mouse software.
  4. Optional: for the named-voice say path, download "Zoe (Premium)" in
       System Settings -> Accessibility -> Read & Speak -> Manage Voices,
     then set voice.say_voice: "Zoe (Premium)" in $CONFIG.
  5. Default engine is 'say' with say_voice: system — it inherits whatever
     voice is set in Read & Speak (currently your Siri voice). Change it there
     and readaloud follows.

Hotkey or config changes: edit $CONFIG. Engine/voice/prosody take effect on the
next read; hotkey changes require a Hammerspoon reload.
EOF
