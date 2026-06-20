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
local hudTextFrame = nil -- stable text-element frame (set in buildHud; do NOT read back from the canvas)
local hudLinger  = nil   -- hs.timer for the post-done linger

-- Build (or rebuild) the HUD canvas. Called once on the first dictation start.
-- We keep the canvas alive across sessions (just hide/show it) to avoid
-- create/delete flicker. Returns the canvas or nil on failure.
local function buildHud()
  -- Pick the screen under the mouse; fall back to mainScreen.
  local mousePos    = hs.mouse.absolutePosition()
  local mouseScreen = hs.screen.find(mousePos)
  local screen      = (mouseScreen or hs.screen.mainScreen()):frame()

  local widthPct  = tonumber(cfgGet("hud.width_pct", 30)) or 30
  local fontSize  = tonumber(cfgGet("hud.font_size", 20)) or 20
  local opacity   = tonumber(cfgGet("hud.opacity",  0.92)) or 0.92
  local lines     = tonumber(cfgGet("hud.lines", 6)) or 6
  local position  = cfgGet("hud.position", "center")

  local padding = tonumber(cfgGet("hud.padding", 22)) or 22
  local hudW    = math.floor(screen.w * widthPct / 100)
  local padX    = padding
  local padY    = padding
  local lineH   = fontSize * 1.4          -- approximate line height
  local textH   = math.ceil(lineH * lines)
  local hudH    = textH + padY * 2

  -- Resolve X/Y based on position setting.
  -- "center"        → both horizontally and vertically centered.
  -- "bottom-center" → horizontal center, ~75% down (old behavior).
  -- "top-center"    → horizontal center, ~12% down.
  -- "mouse"         → centered at the mouse cursor position.
  -- {x=…, y=…}     → explicit top-left coordinates.
  local hudX, hudY
  local centerX = screen.x + math.floor((screen.w - hudW) / 2)

  if type(position) == "table" and position.x ~= nil and position.y ~= nil then
    hudX = math.floor(position.x)
    hudY = math.floor(position.y)
  elseif position == "bottom-center" then
    hudX = centerX
    hudY = screen.y + math.floor(screen.h * 0.75) - math.floor(hudH / 2)
  elseif position == "top-center" then
    hudX = centerX
    hudY = screen.y + math.floor(screen.h * 0.12) - math.floor(hudH / 2)
  elseif position == "mouse" then
    hudX = math.floor(mousePos.x - hudW / 2)
    hudY = math.floor(mousePos.y - hudH / 2)
  else
    -- Default: "center" — horizontally AND vertically centered.
    hudX = centerX
    hudY = screen.y + math.floor((screen.h - hudH) / 2)
  end

  local c = hs.canvas.new({ x = hudX, y = hudY, w = hudW, h = hudH })

  -- Background rounded rectangle (radius 14, not a full pill).
  -- A full pill (radius = hudH/2) eats into the text area; 14px keeps corners
  -- modest so the inner text area equals hudW - 2*padX wide.
  c[1] = {
    type             = "rectangle",
    action           = "fill",
    roundedRectRadii = { xRadius = 14, yRadius = 14 },
    fillColor        = { red = 0, green = 0, blue = 0, alpha = 0.80 },
  }

  -- Text element (index 2 = hudTextIdx). Seeded with "listening…".
  -- Frame is inset by padX/padY on all sides so text never renders outside
  -- the rounded rectangle.
  local initStyled = hs.styledtext.new("listening…", {
    font       = { name = ".AppleSystemUIFont", size = fontSize },
    color      = { white = 1.0, alpha = opacity },
    paragraphStyle = { lineBreak = "wordWrap" },
  })
  hudTextFrame = { x = padX, y = padY, w = hudW - padX * 2, h = textH }
  c[2] = {
    type  = "text",
    text  = initStyled,
    frame = hudTextFrame,
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
-- Implements tail-trim: only the LAST K lines of the full rolling transcript
-- are shown, so the newest text is always visible at the bottom.  Older text
-- is never lost — it lives in finalText and gets pasted in full on stop.
--
-- Approach: greedy word-wrap the full text at an estimated chars-per-line, then
-- keep only the last K lines where K = lines config value.
local function hudSetText(fullText)
  if not hud then return end
  local fontSize = tonumber(cfgGet("hud.font_size", 20)) or 20
  local opacity  = tonumber(cfgGet("hud.opacity", 0.92)) or 0.92
  local lines    = tonumber(cfgGet("hud.lines", 6)) or 6

  -- Use the STABLE stored frame (never read it back from the canvas element —
  -- that readback was fragile across repeated reassignment and could error,
  -- aborting the stream drain and wedging the HUD).
  local innerW = (hudTextFrame and hudTextFrame.w) or 300

  -- Estimate average char width at this font size (roughly 0.5× em for the
  -- system UI font at normal weight).
  local charsPerLine = math.max(10, math.floor(innerW / (fontSize * 0.55)))

  -- Greedy word-wrap: split into words, build lines greedily.
  local wrapped = {}
  local currentLine = ""
  for word in fullText:gmatch("%S+") do
    if #currentLine == 0 then
      currentLine = word
    elseif #currentLine + 1 + #word <= charsPerLine then
      currentLine = currentLine .. " " .. word
    else
      table.insert(wrapped, currentLine)
      currentLine = word
    end
  end
  if #currentLine > 0 then
    table.insert(wrapped, currentLine)
  end

  -- Keep only the tail (last `lines` lines) so newest text stays visible.
  local tail = {}
  local start = math.max(1, #wrapped - lines + 1)
  for i = start, #wrapped do
    table.insert(tail, wrapped[i])
  end
  local displayText = table.concat(tail, "\n")

  local styled = hs.styledtext.new(displayText, {
    font       = { name = ".AppleSystemUIFont", size = fontSize },
    color      = { white = 1.0, alpha = opacity },
    paragraphStyle = { lineBreak = "wordWrap" },
  })
  hud[hudTextIdx] = {
    type  = "text",
    text  = styled,
    frame = hudTextFrame,
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
local dictTask    = nil   -- hs.task handle for the stream process
local isDictating = false
local stopping    = false  -- true while send-stop in flight (between 2nd press and {done})
local stopTimer   = nil    -- fallback watchdog: fires if daemon doesn't emit {done} in time
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

  -- Cancel the stop-watchdog timer (if it fired, we're here anyway — safe to cancel).
  if stopTimer then stopTimer:stop(); stopTimer = nil end

  local textToPaste = finalText
  finalText   = nil
  isDictating = false
  stopping    = false
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
    dlog("partial: %d chars -> hudSetText", #obj.text)
    hudSetText(obj.text)

  elseif obj.text and not obj.event then
    -- Plain {text:...} without volatile flag — treat as partial.
    hudSetText(obj.text)
  end
end

-- Safe wrapper: a single malformed line or render error must NEVER abort the
-- stream drain — doing so would skip {done} → onDictationDone and wedge the HUD
-- (exactly the bug that required a Hammerspoon reload to clear).
local function safeHandleJsonLine(line)
  local ok, err = pcall(handleJsonLine, line)
  if not ok then dlog("handleJsonLine error: %s", tostring(err)) end
end

-- Streaming callback: returns true to keep stdout flowing.
-- A single chunk may contain zero, one, or multiple newlines.
local function onStreamChunk(_task, stdOut, _stdErr)
  if stdOut and #stdOut > 0 then
    dlog("onStreamChunk: %d bytes received", #stdOut)
    lineBuf = lineBuf .. stdOut
    -- Drain complete lines.
    while true do
      local nl = string.find(lineBuf, "\n", 1, true)
      if not nl then break end
      local line = string.sub(lineBuf, 1, nl - 1)
      lineBuf = string.sub(lineBuf, nl + 1)
      -- Trim Windows \r if present.
      line = line:gsub("\r$", "")
      safeHandleJsonLine(line)
    end
  end
  return true  -- MUST return true to keep streaming
end

-- Exit callback: fires when the process exits (either naturally or via terminate).
local function onStreamExit(exitCode, stdOut, stdErr)
  dlog("stream exited code=%s stdOutBytes=%d stderr=%s",
    tostring(exitCode), stdOut and #stdOut or 0, (stdErr or ""):sub(1, 200))

  -- CRITICAL: hs.task delivers the FINAL batch of output to this termination
  -- callback's stdOut argument rather than the streaming callback when the
  -- process exits (esp. a fast-exiting one). Append it and drain ALL complete
  -- lines — otherwise the tail (later partials + {final} + {done}) is lost,
  -- which is exactly the "no text in HUD / finalText=nil" bug.
  if stdOut and #stdOut > 0 then
    lineBuf = lineBuf .. stdOut
  end
  while true do
    local nl = string.find(lineBuf, "\n", 1, true)
    if not nl then break end
    local line = string.sub(lineBuf, 1, nl - 1)
    lineBuf = string.sub(lineBuf, nl + 1)
    line = line:gsub("\r$", "")
    safeHandleJsonLine(line)
  end
  if #lineBuf > 0 then
    safeHandleJsonLine(lineBuf)
    lineBuf = ""
  end

  -- handleJsonLine({done}) already called onDictationDone (clearing the flags).
  -- Only finalize here if we somehow still think we're mid-session.
  if isDictating or stopping then
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
-- We use the warm-daemon path: spawn `send stop` as a fire-and-forget command.
-- The daemon receives "stop", finalizes the mic session, and emits
-- {event:"final"} then {event:"done"} on the still-open dictate connection.
-- Those events flow through onStreamChunk → handleJsonLine → onDictationDone,
-- which does the paste + linger + hide and clears all state.
-- We do NOT terminate dictTask here — it exits naturally when {done} arrives.
-- A 3 s watchdog timer handles the case where the daemon wedges and never
-- emits {done} (it force-terminates and calls onDictationDone directly).
local function stopDictation()
  if not isDictating or stopping then return end
  dlog("stopping dictation (send stop)")
  stopping = true

  -- Tell the daemon to stop the current session.
  if CLI then
    hs.task.new(CLI, nil, { "send", "stop" }):start()
  end

  -- Watchdog: if onDictationDone hasn't fired within 3 s, force-terminate.
  stopTimer = hs.timer.doAfter(3, function()
    stopTimer = nil
    dlog("stop watchdog fired — forcing terminate")
    if dictTask and dictTask:isRunning() then
      dictTask:terminate()
      -- onStreamExit will call onDictationDone via the stopping branch.
    else
      onDictationDone()
    end
  end)
end

-- ---------------------------------------------------------------------------
-- Hotkey handler.
-- ---------------------------------------------------------------------------
local function onDictateHotkey()
  if stopping then
    -- Already in the stop→done window; ignore or give feedback.
    dlog("onDictateHotkey: ignoring press while stopping in progress")
    alert("finishing…")
    return
  end
  if isDictating then
    stopDictation()
  else
    startDictation()
  end
end

-- Debug hook: trigger the toggle from the hs CLI (hs -c "require('speakwrite').testToggle()").
M.testToggle = onDictateHotkey

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
