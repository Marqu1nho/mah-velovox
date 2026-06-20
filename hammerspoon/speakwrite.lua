-- speakwrite.lua — Hammerspoon module for hotkey-triggered mic→STT→paste.
--
-- Loaded from ~/.hammerspoon/init.lua via `require("speakwrite")`.
--
-- Inverse of readaloud: you speak, the transcript appears in a live HUD anchor,
-- then on stop the final text is pasted at the cursor.
--
-- Paths to the repo and the CLI are resolved at install time and written to
-- ~/.hammerspoon/speakwrite_paths.lua by install.sh. We require that file here
-- so the committed module never hardcodes a machine-specific path.

local M = {}

-- ---------------------------------------------------------------------------
-- Resolve CLI path (install-time generated; fall back to a clear error).
-- ---------------------------------------------------------------------------
local paths_ok, paths = pcall(require, "speakwrite_paths")
local CLI = nil
if paths_ok and type(paths) == "table" and paths.cli then
  CLI = paths.cli
else
  hs.alert.show("speakwrite: speakwrite_paths.lua missing — re-run install.sh")
end

-- ---------------------------------------------------------------------------
-- Streaming subcommand.
-- Uses the warm daemon: `send dictate` relays live partials over the unix
-- socket to stdout (model stays loaded + pre-warmed, so no per-press lag).
-- Same newline-JSON contract as standalone `stream` — swap back to {"stream"}
-- to run daemonless.
-- ---------------------------------------------------------------------------
local STREAM_CMD = { "send", "dictate" }

-- ---------------------------------------------------------------------------
-- Config (read once at load by shelling out to the CLI's `config` subcommand).
-- ---------------------------------------------------------------------------
local config = nil

local function loadConfig()
  if not CLI then return nil end
  local out, ok = hs.execute(string.format("%q config", CLI), true)
  if ok and out and #out > 0 then
    local decoded = hs.json.decode(out)
    if decoded then return decoded end
  end
  return nil
end

local function cfgGet(path, default)
  local node = config
  for part in string.gmatch(path, "[^%.]+") do
    if type(node) ~= "table" then return default end
    node = node[part]
  end
  if node == nil then return default end
  return node
end

-- Map config hotkey tokens to Hammerspoon mods + key.
-- Handles the backtick/grave key ("`") as a literal key string.
local function splitHotkey(spec)
  local mods = {}
  local key = nil
  for _, token in ipairs(spec) do
    local low = string.lower(token)
    if low == "ctrl" or low == "alt" or low == "cmd" or low == "shift" then
      table.insert(mods, low)
    else
      key = token
    end
  end
  return mods, key
end

-- ---------------------------------------------------------------------------
-- Diagnostics log: tail -f ~/.local/state/speakwrite/hammerspoon.log
-- ---------------------------------------------------------------------------
local LOG_PATH = os.getenv("HOME") .. "/.local/state/speakwrite/hammerspoon.log"

local function dlog(fmt, ...)
  local msg = string.format(fmt, ...)
  hs.printf("speakwrite: %s", msg)
  local fh = io.open(LOG_PATH, "a")
  if fh then
    fh:write(os.date("%H:%M:%S "), msg, "\n")
    fh:close()
  end
end

-- ---------------------------------------------------------------------------
-- Transient alert pill (same scaffolding as readaloud).
-- Short-lived status messages; NOT the persistent live-transcript HUD.
-- ---------------------------------------------------------------------------
local pillCanvas = nil
local pillTimer  = nil

local function alert(msg)
  if pillTimer then pillTimer:stop(); pillTimer = nil end
  if pillCanvas then pillCanvas:delete(); pillCanvas = nil end

  local screen   = hs.screen.mainScreen():frame()
  local duration = 1.4

  local styled = hs.styledtext.new(msg, {
    font  = { name = ".AppleSystemUIFont", size = 16 },
    color = { white = 1.0, alpha = 1.0 },
  })
  local c    = hs.canvas.new({ x = 0, y = 0, w = 10, h = 10 })
  local size = c:minimumTextSize(styled)
  local padX, padY = 18, 8
  local w, h = size.w + padX * 2, size.h + padY * 2
  c:frame({
    x = screen.x + (screen.w - w) / 2,
    y = screen.y + (screen.h * 3.5 / 100) - h / 2,
    w = w,
    h = h,
  })
  c[1] = {
    type             = "rectangle",
    action           = "fill",
    roundedRectRadii = { xRadius = h / 2, yRadius = h / 2 },
    fillColor        = { red = 0, green = 0, blue = 0, alpha = 0.72 },
  }
  c[2] = {
    type  = "text",
    text  = styled,
    frame = { x = padX, y = padY, w = size.w, h = size.h },
  }
  c:level(hs.canvas.windowLevels.overlay)
  c:behaviorAsLabels({ "canJoinAllSpaces", "transient" })
  c:show()
  pillCanvas = c
  pillTimer  = hs.timer.doAfter(duration, function()
    pillTimer = nil
    if pillCanvas then
      pillCanvas:delete(0.4)
      pillCanvas = nil
    end
  end)
end

-- ---------------------------------------------------------------------------
-- Live-transcript HUD anchor.
--
-- Design goals (from spec):
--   • Glass / click-through / non-focusable:
--       - ZERO mouseCallback and ZERO canvasMouseEvents/trackMouseDown.
--       - canvas:clickActivating(false).
--   • Above fullscreen: level(screenSaver) + behaviorAsLabels(canJoinAllSpaces,
--       stationary). hs.dockicon.hide() called once at start.
--   • Flicker-free updates: one persistent canvas; mutate text element by index.
--   • Position: lower-third (~75% down), width from hud.width_pct of the
--       screen under the mouse (fallback mainScreen).
--   • Monochrome for now: single color, single rolling text area.
-- ---------------------------------------------------------------------------
local hud        = nil   -- the persistent hs.canvas
local hudTextIdx = 2     -- index of the text element inside hud (see buildHud)
local hudLinger  = nil   -- hs.timer for the post-done linger

-- Build (or rebuild) the HUD canvas. Called once on the first dictation start.
-- We keep the canvas alive across sessions (just hide/show it) to avoid
-- create/delete flicker. Returns the canvas or nil on failure.
local function buildHud()
  -- Pick the screen under the mouse; fall back to mainScreen.
  local mouseScreen = hs.screen.find(hs.mouse.absolutePosition())
  local screen      = (mouseScreen or hs.screen.mainScreen()):frame()

  local widthPct  = tonumber(cfgGet("hud.width_pct", 50)) or 50
  local fontSize  = tonumber(cfgGet("hud.font_size", 20)) or 20
  local opacity   = tonumber(cfgGet("hud.opacity",  0.92)) or 0.92
  local lines     = tonumber(cfgGet("hud.lines", 4)) or 4

  local hudW    = math.floor(screen.w * widthPct / 100)
  local padX    = 18
  local padY    = 10
  local lineH   = fontSize * 1.4          -- approximate line height
  local textH   = math.ceil(lineH * lines)
  local hudH    = textH + padY * 2

  -- Center horizontally; place at ~75% down the screen.
  local hudX = screen.x + math.floor((screen.w - hudW) / 2)
  local hudY = screen.y + math.floor(screen.h * 0.75) - math.floor(hudH / 2)

  local c = hs.canvas.new({ x = hudX, y = hudY, w = hudW, h = hudH })

  -- Background pill — no mouse tracking.
  c[1] = {
    type             = "rectangle",
    action           = "fill",
    roundedRectRadii = { xRadius = hudH / 2, yRadius = hudH / 2 },
    fillColor        = { red = 0, green = 0, blue = 0, alpha = 0.80 },
  }

  -- Text element (index 2 = hudTextIdx). Seeded with "listening…".
  local initStyled = hs.styledtext.new("listening…", {
    font       = { name = ".AppleSystemUIFont", size = fontSize },
    color      = { white = 1.0, alpha = opacity },
    paragraphStyle = { lineBreak = "wordWrap" },
  })
  c[2] = {
    type  = "text",
    text  = initStyled,
    frame = { x = padX, y = padY, w = hudW - padX * 2, h = textH },
  }

  -- Glass / non-focusable / click-through:
  --   • No mouseCallback registered (not called at all).
  --   • No canvasMouseEvents(true, …) — default is false.
  --   • clickActivating(false) so no focus steal.
  c:clickActivating(false)

  -- Float above fullscreen Spaces.
  c:level(hs.canvas.windowLevels.screenSaver)
  c:behaviorAsLabels({ "canJoinAllSpaces", "stationary" })

  return c
end

-- Update the HUD text in-place (no canvas delete/recreate).
local function hudSetText(text)
  if not hud then return end
  local fontSize = tonumber(cfgGet("hud.font_size", 20)) or 20
  local opacity  = tonumber(cfgGet("hud.opacity", 0.92)) or 0.92
  local styled   = hs.styledtext.new(text, {
    font       = { name = ".AppleSystemUIFont", size = fontSize },
    color      = { white = 1.0, alpha = opacity },
    paragraphStyle = { lineBreak = "wordWrap" },
  })
  hud[hudTextIdx] = {
    type  = "text",
    text  = styled,
    frame = hud[hudTextIdx].frame,
  }
end

local function hudShow(initialText)
  if not hud then
    hud = buildHud()
    if not hud then
      dlog("hudShow: buildHud returned nil")
      return
    end
  end
  if initialText then
    hudSetText(initialText)
  end
  hud:show()
end

local function hudHide(fadeS)
  if not hud then return end
  hud:hide(fadeS or 0.3)
end

-- ---------------------------------------------------------------------------
-- Streaming task + state.
-- ---------------------------------------------------------------------------
local dictTask   = nil   -- hs.task handle for the stream process
local isDictating = false
local finalText   = nil  -- stashed from {event="final"} JSON
local lineBuf     = ""   -- partial-line accumulator for streaming callback

-- Paste finalText at cursor, then restore the pasteboard.
local function pasteResult(text)
  if not text or #text == 0 then
    dlog("pasteResult: nothing to paste")
    return
  end

  local trailingSpace = cfgGet("inject.trailing_space", true)
  local toInject      = text .. (trailingSpace and " " or "")

  if cfgGet("inject.method", "paste") == "type" then
    dlog("pasteResult: typing %d chars", #toInject)
    hs.eventtap.keyStrokes(toInject)
    return
  end

  -- Paste path: save pasteboard, inject, restore.
  local saved      = hs.pasteboard.getContents()
  local savedCount = hs.pasteboard.changeCount()

  hs.pasteboard.setContents(toInject)
  hs.eventtap.keyStroke({ "cmd" }, "v", 0)

  -- Poll up to ~120ms for the target app to consume the paste,
  -- then restore the original pasteboard contents.
  local deadline = hs.timer.secondsSinceEpoch() + 0.12
  while hs.timer.secondsSinceEpoch() < deadline do
    if hs.pasteboard.changeCount() ~= savedCount then break end
    hs.timer.usleep(20000) -- 20ms
  end

  if saved ~= nil then
    hs.pasteboard.setContents(saved)
  elseif hs.pasteboard.changeCount() ~= savedCount then
    -- Clipboard was empty before; clear it again so we don't leak the text.
    hs.pasteboard.clearContents()
  end

  dlog("pasteResult: pasted %d chars", #toInject)
end

-- Called when dictation is fully done (after linger).
local function onDictationDone()
  dlog("onDictationDone: finalText=%s", finalText and tostring(#finalText).." chars" or "nil")
  local textToPaste = finalText
  finalText   = nil
  isDictating = false
  dictTask    = nil
  lineBuf     = ""

  if textToPaste then
    pasteResult(textToPaste)
  end

  local lingerMs = tonumber(cfgGet("hud.linger_ms", 1500)) or 1500
  hudLinger = hs.timer.doAfter(lingerMs / 1000, function()
    hudLinger = nil
    hudHide(0.3)
  end)
end

-- Process a complete JSON line from the stream.
local function handleJsonLine(line)
  if not line or line == "" then return end
  local ok, obj = pcall(hs.json.decode, line)
  if not ok or not obj then
    dlog("json decode error for line: %s", line:sub(1, 120))
    return
  end

  if obj.event == "done" then
    dlog("stream: done event received")
    -- Cancel any pending linger timer before starting a new one.
    if hudLinger then hudLinger:stop(); hudLinger = nil end
    -- Kick off done sequence (linger then paste).
    onDictationDone()

  elseif obj.event == "final" and obj.text then
    -- Consolidated final transcript — stash it.
    dlog("stream: final event, %d chars", #obj.text)
    finalText = obj.text
    -- Update HUD with the finalized text.
    hudSetText(obj.text)

  elseif obj.volatile == true and obj.text then
    -- Rolling partial transcript — update HUD but don't stash as final.
    hudSetText(obj.text)

  elseif obj.text and not obj.event then
    -- Plain {text:...} without volatile flag — treat as partial.
    hudSetText(obj.text)
  end
end

-- Streaming callback: returns true to keep stdout flowing.
-- A single chunk may contain zero, one, or multiple newlines.
local function onStreamChunk(_task, stdOut, _stdErr)
  if stdOut and #stdOut > 0 then
    lineBuf = lineBuf .. stdOut
    -- Drain complete lines.
    while true do
      local nl = string.find(lineBuf, "\n", 1, true)
      if not nl then break end
      local line = string.sub(lineBuf, 1, nl - 1)
      lineBuf = string.sub(lineBuf, nl + 1)
      -- Trim Windows \r if present.
      line = line:gsub("\r$", "")
      handleJsonLine(line)
    end
  end
  return true  -- MUST return true to keep streaming
end

-- Exit callback: fires when the process exits (either naturally or via terminate).
local function onStreamExit(exitCode, _stdOut, stdErr)
  dlog("stream exited code=%s stderr=%s", tostring(exitCode), (stdErr or ""):sub(1, 400))

  -- Drain any remaining partial line in the buffer (SIGTERM race).
  if lineBuf and #lineBuf > 0 then
    handleJsonLine(lineBuf)
    lineBuf = ""
  end

  -- If {event="done"} already fired, onDictationDone has been called and
  -- isDictating is already false. Only finalize here if we're still mid-session
  -- (terminate race: SIGTERM handler may have emitted final but not done).
  if isDictating then
    dlog("stream exit without done event — finalizing with latest text")
    onDictationDone()
  end
end

-- Start a new dictation session.
local function startDictation()
  if not CLI then
    alert("speakwrite: speakwrite_paths.lua missing — re-run install.sh")
    return
  end

  -- Cancel any pending linger from a previous session.
  if hudLinger then hudLinger:stop(); hudLinger = nil end

  isDictating = true
  finalText   = nil
  lineBuf     = ""

  -- Show the HUD immediately with "listening…" for perceived responsiveness
  -- BEFORE the engine warms up.
  hudShow("listening…")
  dlog("dictation started")

  dictTask = hs.task.new(CLI, onStreamExit, onStreamChunk, STREAM_CMD)
  -- NO setInput — mic is the input, not stdin.
  if not dictTask:start() then
    dlog("failed to start stream task (cli=%s)", tostring(CLI))
    isDictating = false
    dictTask    = nil
    hudHide()
    alert("speakwrite: failed to start stream")
    return
  end
  dlog("stream task started (pid=%s)", tostring(dictTask:pid()))
end

-- Stop an active dictation session.
-- For the standalone `stream` path: SIGTERM causes the CLI's handler to
-- finalize and emit final+done before exit, so onStreamExit picks it up.
-- FUTURE: once on the daemon, stop = spawn CLI {"send", "stop"} instead.
local function stopDictation()
  if not isDictating then return end
  dlog("stopping dictation (terminate)")
  if dictTask and dictTask:isRunning() then
    dictTask:terminate()
    -- onStreamExit fires asynchronously and calls onDictationDone.
  else
    -- Task already gone — finalize directly.
    onDictationDone()
  end
  -- NOTE: isDictating is cleared inside onDictationDone (async path).
  -- We clear it here too so a rapid double-press can't start a second session
  -- before the exit callback fires.
  isDictating = false
end

-- ---------------------------------------------------------------------------
-- Hotkey handler.
-- ---------------------------------------------------------------------------
local function onDictateHotkey()
  if isDictating then
    stopDictation()
  else
    startDictation()
  end
end

-- ---------------------------------------------------------------------------
-- Setup.
-- ---------------------------------------------------------------------------
-- Debug helpers, callable from the hs IPC command line:
--   hs -c "require('speakwrite').diag()"
function M.diag()
  return string.format(
    "cli=%s isDictating=%s taskRunning=%s accessibility=%s",
    tostring(CLI),
    tostring(isDictating),
    tostring(dictTask ~= nil and dictTask:isRunning()),
    tostring(hs.accessibilityState())
  )
end

function M.start()
  config = loadConfig() or {}

  -- Ensure the log directory exists.
  local logDir = os.getenv("HOME") .. "/.local/state/speakwrite"
  os.execute(string.format("mkdir -p %q", logDir))

  -- Expose the hs IPC command-line tool for interactive debugging.
  pcall(function()
    require("hs.ipc")
    if not hs.ipc.cliStatus("/opt/homebrew") then
      hs.ipc.cliInstall("/opt/homebrew")
    end
  end)

  -- Hide the dock icon so the HUD can float above fullscreen Spaces.
  hs.dockicon.hide()

  if not hs.accessibilityState() then
    hs.alert.show("speakwrite: grant Hammerspoon Accessibility permission", 4)
  end

  -- Bind the dictate hotkey. Default: ctrl+alt+` (backtick/grave).
  local dictateSpec = cfgGet("hotkeys.dictate", { "ctrl", "alt", "`" })
  local mode        = cfgGet("hotkeys.mode", "toggle")

  local dMods, dKey = splitHotkey(dictateSpec)

  if dKey then
    if mode == "push_to_talk" then
      -- Push-to-talk: hold for continuous dictation, release to stop + paste.
      hs.hotkey.bind(dMods, dKey,
        function() startDictation() end,    -- key down
        function() stopDictation()  end     -- key up
      )
      dlog("bound push_to_talk hotkey: mods=%s key=%s", table.concat(dMods, "+"), dKey)
    else
      -- Toggle (default): first press starts, second press stops + pastes.
      hs.hotkey.bind(dMods, dKey, onDictateHotkey)
      dlog("bound toggle hotkey: mods=%s key=%s", table.concat(dMods, "+"), dKey)
    end
  else
    dlog("no dictate hotkey bound (spec=%s)", hs.inspect and hs.inspect(dictateSpec) or "?")
  end

  hs.printf("speakwrite: loaded (cli=%s)", tostring(CLI))
end

M.start()

return M
