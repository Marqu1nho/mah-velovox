-- readaloud.lua — Hammerspoon module for hotkey-triggered TTS reading.
--
-- Loaded from ~/.hammerspoon/init.lua via `require("readaloud")`.
--
-- Paths to the repo and the CLI are resolved at install time and written to
-- ~/.hammerspoon/readaloud_paths.lua by install.sh. We require that file here
-- so the committed module never hardcodes a machine-specific path and always
-- invokes the CLI by absolute path (never relying on PATH).

local M = {}

-- ---------------------------------------------------------------------------
-- Resolve CLI path (install-time generated; fall back to a clear error).
-- ---------------------------------------------------------------------------
local paths_ok, paths = pcall(require, "readaloud_paths")
local CLI = nil
if paths_ok and type(paths) == "table" and paths.cli then
  CLI = paths.cli
else
  hs.alert.show("readaloud: readaloud_paths.lua missing — re-run install.sh")
end

-- ---------------------------------------------------------------------------
-- Config (read once at load by shelling out to the CLI's --print-config-json).
-- ---------------------------------------------------------------------------
local config = nil

local function loadConfig()
  if not CLI then return nil end
  local out, ok = hs.execute(string.format("%q --print-config-json", CLI), true)
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
-- Reader process management + single-instance.
-- ---------------------------------------------------------------------------
local readerTask = nil

-- Diagnostics log, readable from a shell while debugging:
--   tail -f ~/.local/state/readaloud/hammerspoon.log
local LOG_PATH = os.getenv("HOME") .. "/.local/state/readaloud/hammerspoon.log"

local function dlog(fmt, ...)
  local msg = string.format(fmt, ...)
  hs.printf("readaloud: %s", msg)
  local fh = io.open(LOG_PATH, "a")
  if fh then
    fh:write(os.date("%H:%M:%S "), msg, "\n")
    fh:close()
  end
end

-- Status pill: a small always-on-top canvas, horizontally centered with its
-- center at alerts.y_pct% of the screen's usable height (0 = top, 100 =
-- bottom). Replaces hs.alert, whose center-screen position is not adjustable.
local pillCanvas = nil
local pillTimer = nil

local function alert(msg)
  if not cfgGet("hotkeys.show_alerts", true) then return end
  if pillTimer then pillTimer:stop(); pillTimer = nil end
  if pillCanvas then pillCanvas:delete(); pillCanvas = nil end

  local screen = hs.screen.mainScreen():frame()
  local yPct = tonumber(cfgGet("alerts.y_pct", 3.5)) or 3.5
  local duration = tonumber(cfgGet("alerts.duration_s", 1.2)) or 1.2

  local styled = hs.styledtext.new(msg, {
    font = { name = ".AppleSystemUIFont", size = 16 },
    color = { white = 1.0, alpha = 1.0 },
  })
  local c = hs.canvas.new({ x = 0, y = 0, w = 10, h = 10 })
  local size = c:minimumTextSize(styled)
  local padX, padY = 18, 8
  local w, h = size.w + padX * 2, size.h + padY * 2
  c:frame({
    x = screen.x + (screen.w - w) / 2,
    y = screen.y + (screen.h * yPct / 100) - h / 2,
    w = w,
    h = h,
  })
  c[1] = {
    type = "rectangle",
    action = "fill",
    roundedRectRadii = { xRadius = h / 2, yRadius = h / 2 },
    fillColor = { red = 0, green = 0, blue = 0, alpha = 0.72 },
  }
  c[2] = {
    type = "text",
    text = styled,
    frame = { x = padX, y = padY, w = size.w, h = size.h },
  }
  c:level(hs.canvas.windowLevels.overlay)
  c:behaviorAsLabels({ "canJoinAllSpaces", "transient" })
  c:show()
  pillCanvas = c
  pillTimer = hs.timer.doAfter(duration, function()
    pillTimer = nil
    if pillCanvas then
      pillCanvas:delete(0.4)
      pillCanvas = nil
    end
  end)
end

-- ---------------------------------------------------------------------------
-- Transport pill: a persistent, CLICKABLE two-zone canvas. Unlike alert() it
-- does NOT auto-dismiss — it stays until the read stops or finishes.
-- LEFT zone  = play/pause toggle (icon reflects current state).
-- RIGHT zone = stop.
-- Same visual style/position as the transient pill.
-- (forward declaration; stopReader is defined later in the file.)
-- ---------------------------------------------------------------------------
local transportCanvas = nil
local paused = false

local function hideTransport()
  if transportCanvas then
    transportCanvas:delete(0.2)
    transportCanvas = nil
  end
end

-- togglePauseLive: shared function used by BOTH the hotkey path and the
-- left-zone click.  Guards that a live readerTask exists, sends SIGUSR1,
-- flips the module `paused` bool, and redraws the transport pill.
-- Forward-declared here; body assigned after stopReader so it can't
-- accidentally reference it.
local togglePauseLive
-- stopReader is defined later (needs readerTask helpers), but showTransport's
-- click callback references it — forward-declare so the closure binds the local.
local stopReader

local function showTransport()
  -- Rebuild from scratch each call so sizing stays clean.
  if transportCanvas then transportCanvas:delete(); transportCanvas = nil end

  local screen = hs.screen.mainScreen():frame()
  local yPct = tonumber(cfgGet("alerts.y_pct", 3.5)) or 3.5

  -- Left icon: show the action that a click will *perform* (opposite of state).
  local leftIcon  = paused and "▶" or "⏸"
  local rightIcon = "⏹"
  local sepW = 1  -- thin separator width in points

  local font = { name = ".AppleSystemUIFont", size = 16 }
  local white = { white = 1.0, alpha = 1.0 }

  -- Measure each zone's text independently using a scratch canvas.
  local scratch = hs.canvas.new({ x = 0, y = 0, w = 10, h = 10 })
  local styledLeft  = hs.styledtext.new(leftIcon,  { font = font, color = white })
  local styledRight = hs.styledtext.new(rightIcon, { font = font, color = white })
  local szLeft  = scratch:minimumTextSize(styledLeft)
  local szRight = scratch:minimumTextSize(styledRight)
  scratch:delete()

  local padX, padY = 18, 8
  local zoneH = math.max(szLeft.h, szRight.h) + padY * 2
  local leftW  = szLeft.w  + padX * 2
  local rightW = szRight.w + padX * 2
  local totalW = leftW + sepW + rightW

  local c = hs.canvas.new({
    x = screen.x + (screen.w - totalW) / 2,
    y = screen.y + (screen.h * yPct / 100) - zoneH / 2,
    w = totalW,
    h = zoneH,
  })

  -- Background pill (no click tracking — just visual).
  c[1] = {
    type = "rectangle",
    action = "fill",
    roundedRectRadii = { xRadius = zoneH / 2, yRadius = zoneH / 2 },
    fillColor = { red = 0, green = 0, blue = 0, alpha = 0.72 },
  }

  -- LEFT zone: play/pause.  Full-height rectangle that catches clicks.
  c[2] = {
    id = "playpause",
    type = "rectangle",
    action = "fill",
    frame = { x = 0, y = 0, w = leftW, h = zoneH },
    fillColor = { red = 0, green = 0, blue = 0, alpha = 0.0 },  -- transparent
    trackMouseUp = true,
  }

  -- Left icon text.
  c[3] = {
    type = "text",
    text = styledLeft,
    frame = {
      x = (leftW - szLeft.w) / 2,
      y = (zoneH - szLeft.h) / 2,
      w = szLeft.w,
      h = szLeft.h,
    },
  }

  -- Separator.
  c[4] = {
    type = "rectangle",
    action = "fill",
    frame = { x = leftW, y = padY, w = sepW, h = zoneH - padY * 2 },
    fillColor = { white = 1.0, alpha = 0.35 },
  }

  -- RIGHT zone: stop.  Full-height rectangle that catches clicks.
  c[5] = {
    id = "stop",
    type = "rectangle",
    action = "fill",
    frame = { x = leftW + sepW, y = 0, w = rightW, h = zoneH },
    fillColor = { red = 0, green = 0, blue = 0, alpha = 0.0 },  -- transparent
    trackMouseUp = true,
  }

  -- Right icon text.
  c[6] = {
    type = "text",
    text = styledRight,
    frame = {
      x = leftW + sepW + (rightW - szRight.w) / 2,
      y = (zoneH - szRight.h) / 2,
      w = szRight.w,
      h = szRight.h,
    },
  }

  c:level(hs.canvas.windowLevels.overlay)
  c:behaviorAsLabels({ "canJoinAllSpaces", "transient" })
  -- Canvas-level mouse events are required for clicks to be delivered at all;
  -- per-element trackMouseUp on transparent fills is not reliable on its own.
  -- Route by the click x-coordinate (canvas-relative) to the correct zone.
  c:canvasMouseEvents(true, true)
  c:mouseCallback(function(_canvas, msg, _id, x, _y)
    if msg ~= "mouseUp" then return end
    if x ~= nil and x < leftW then
      togglePauseLive()
    else
      stopReader()
      paused = false
      hideTransport()
      dlog("transport stop clicked")
    end
  end)
  c:show()
  transportCanvas = c
end

local function isRunning()
  return readerTask ~= nil and readerTask:isRunning()
end

-- A reader we no longer hold a task handle for (e.g. it survived a
-- Hammerspoon reload). Identified via the CLI's single-instance pidfile.
local PIDFILE = os.getenv("HOME") .. "/.local/state/readaloud/readaloud.pid"

local function orphanReaderPid()
  local fh = io.open(PIDFILE, "r")
  if not fh then return nil end
  local pid = tonumber((fh:read("a") or ""):match("%d+"))
  fh:close()
  if pid and os.execute(string.format("/bin/kill -0 %d 2>/dev/null", pid)) then
    return pid
  end
  return nil
end

local function stopOrphanReader()
  local pid = orphanReaderPid()
  if pid then
    os.execute(string.format("/bin/kill -TERM %d 2>/dev/null", pid))
    dlog("stopped orphaned reader pid=%d", pid)
    return true
  end
  return false
end

stopReader = function()
  if readerTask then
    -- terminate() sends SIGTERM to the CLI; its signal handler stops the
    -- engine, which in turn SIGTERMs any child `say` process and aborts the
    -- queue (kokoro stops its stream). As a belt-and-suspenders measure, also
    -- try to signal the process group in case a child outlives the CLI.
    local pid = readerTask:pid()
    readerTask:terminate()
    if pid and pid > 0 then
      hs.execute(string.format("/bin/kill -TERM -%d 2>/dev/null || true", pid))
    end
    readerTask = nil
  end
end

-- togglePauseLive: body assigned here, after stopReader is visible.
-- Called by BOTH the hotkey path AND the left-zone transport click.
togglePauseLive = function()
  if not isRunning() then return end
  local pid = readerTask:pid()
  if not pid or pid <= 0 then return end
  hs.execute(string.format("/bin/kill -USR1 %d 2>/dev/null || true", pid))
  paused = not paused
  showTransport()
  dlog("toggle pause -> %s (pid=%d)", paused and "paused" or "reading", pid)
end

-- Forward-declared so startOrTogglePause (above its definition) can call it.
local startReader

-- Start reading if idle, else toggle pause/resume on the live reader. The
-- toggle NEVER stops — stop is reserved for clicking the transport right zone.
local function startOrTogglePause(captureFn, mode)
  if isRunning() then
    togglePauseLive()
    return
  end
  if orphanReaderPid() then
    -- Orphan reader (post-reload): no handle to sync pause state, so the safe
    -- fallback is to stop it outright.
    stopOrphanReader()
    paused = false
    hideTransport()
    alert("■ stopped")
    return
  end
  local text = captureFn()
  if not text or #text == 0 then
    alert(mode == "window" and "readaloud: no window text" or "readaloud: no selection")
    return
  end
  startReader(text, mode)
end

function startReader(text, mode)
  if not CLI then return end
  if not text or #text == 0 then
    alert("readaloud: nothing to read")
    return
  end
  local arg = (mode == "window") and "--window" or "--stdin"
  local args = { arg }
  local frontApp = hs.application.frontmostApplication()
  if frontApp and frontApp:name() then
    table.insert(args, "--app")
    table.insert(args, frontApp:name())
  end
  readerTask = hs.task.new(CLI, function(exitCode, stdOut, stdErr)
    dlog("reader exited code=%s stderr=%s", tostring(exitCode), (stdErr or ""):sub(1, 400))
    readerTask = nil
    -- Natural completion or stop: dismiss the transport pill and reset state.
    paused = false
    hideTransport()
  end, args)
  -- Queue stdin BEFORE start, and do NOT call closeInput(): hs.task closes
  -- the pipe immediately on closeInput, discarding queued input that hasn't
  -- been written yet. Non-streaming tasks auto-close stdin once the queued
  -- write completes.
  readerTask:setInput(text)
  if not readerTask:start() then
    dlog("reader failed to start (cli=%s)", tostring(CLI))
    readerTask = nil
    alert("readaloud: failed to start reader")
    return
  end
  dlog("reader started mode=%s app=%s chars=%d", mode, (frontApp and frontApp:name()) or "?", #text)
  paused = false
  showTransport()
end

-- ---------------------------------------------------------------------------
-- Selection capture: ⌘C with clipboard save/restore, AXSelectedText fallback.
-- ---------------------------------------------------------------------------
local function captureSelection()
  local saved = hs.pasteboard.getContents()
  local savedCount = hs.pasteboard.changeCount()

  local text = nil
  local restored = false

  -- Ensure clipboard is always restored, even on error. If the clipboard was
  -- empty before our ⌘C and the copy populated it, clear it again so we don't
  -- leak the captured selection into an otherwise-empty pasteboard.
  local function restore()
    if not restored then
      restored = true
      if saved ~= nil then
        hs.pasteboard.setContents(saved)
      elseif hs.pasteboard.changeCount() ~= savedCount then
        hs.pasteboard.clearContents()
      end
    end
  end

  local ok, err = pcall(function()
    -- Simulate ⌘C.
    hs.eventtap.keyStroke({ "cmd" }, "c", 0)

    -- Poll changeCount up to ~400ms.
    local deadline = hs.timer.secondsSinceEpoch() + 0.4
    while hs.timer.secondsSinceEpoch() < deadline do
      if hs.pasteboard.changeCount() ~= savedCount then
        text = hs.pasteboard.getContents()
        break
      end
      hs.timer.usleep(20000) -- 20ms
    end
  end)

  restore()

  local viaClipboard = (text ~= nil and #text > 0)
  if not viaClipboard then
    -- AX fallback: focused element's AXSelectedText.
    local app = hs.application.frontmostApplication()
    if app then
      local axapp = hs.axuielement.applicationElement(app)
      if axapp then
        local focused = axapp:attributeValue("AXFocusedUIElement")
        if focused then
          text = focused:attributeValue("AXSelectedText")
        end
      end
    end
  end

  local front = hs.application.frontmostApplication()
  dlog(
    "capture: app=%s clipboard=%s ax_fallback=%s len=%d",
    front and front:name() or "?",
    tostring(viaClipboard),
    tostring(not viaClipboard and text ~= nil and #text > 0),
    text and #text or 0
  )
  if not ok then
    dlog("capture error: %s", tostring(err))
  end
  return text
end

-- ---------------------------------------------------------------------------
-- Window read: walk the focused window AX tree collecting text.
-- ---------------------------------------------------------------------------
local function captureWindow()
  local maxChars = cfgGet("window_read.max_chars", 20000)
  local app = hs.application.frontmostApplication()
  if not app then return nil end
  local axapp = hs.axuielement.applicationElement(app)
  if not axapp then return nil end
  local win = axapp:attributeValue("AXFocusedWindow")
  if not win then return nil end

  local parts = {}
  local total = 0
  local maxDepth = 40

  local function walk(el, depth)
    if total >= maxChars or depth > maxDepth then return end
    local role = el:attributeValue("AXRole")
    local val = el:attributeValue("AXValue")
    if type(val) == "string" and #val > 0 then
      table.insert(parts, val)
      total = total + #val
    elseif role == "AXStaticText" then
      local t = el:attributeValue("AXTitle")
      if type(t) == "string" and #t > 0 then
        table.insert(parts, t)
        total = total + #t
      end
    end
    local children = el:attributeValue("AXChildren")
    if type(children) == "table" then
      for _, child in ipairs(children) do
        if total >= maxChars then break end
        walk(child, depth + 1)
      end
    end
  end

  walk(win, 0)
  local text = table.concat(parts, "\n")
  if #text > maxChars then
    text = string.sub(text, 1, maxChars)
  end
  return text
end

-- ---------------------------------------------------------------------------
-- Hotkey handlers.
-- ---------------------------------------------------------------------------
-- Toggle hotkey: start if idle, else pause/resume. Stop is click-only.
local function onToggle()
  startOrTogglePause(captureSelection, "selection")
end

local function onReadWindow()
  startOrTogglePause(captureWindow, "window")
end

-- ---------------------------------------------------------------------------
-- Setup.
-- ---------------------------------------------------------------------------
-- Debug helpers, callable from a shell once hs.ipc is installed:
--   hs -c "require('readaloud').testSpeak()"
--   hs -c "print(require('readaloud').diag())"
function M.testSpeak(text)
  startReader(text or "Hammerspoon test. If you hear this, the task path works.", "selection")
end

function M.diag()
  return string.format(
    "cli=%s running=%s accessibility=%s",
    tostring(CLI), tostring(isRunning()), tostring(hs.accessibilityState())
  )
end

-- Debug: report the transport pill frame + the left/stop zone boundary so a
-- synthesized click can target a specific zone. Returns "x y w h leftW".
function M.transportFrame()
  if not transportCanvas then return "none" end
  local f = transportCanvas:frame()
  return string.format("%d %d %d %d", f.x, f.y, f.w, f.h)
end

function M.start()
  config = loadConfig() or {}

  -- Expose the `hs` command-line tool for interactive debugging.
  pcall(function()
    require("hs.ipc")
    if not hs.ipc.cliStatus("/opt/homebrew") then
      hs.ipc.cliInstall("/opt/homebrew")
    end
  end)

  if not hs.accessibilityState() then
    hs.alert.show("readaloud: grant Hammerspoon Accessibility permission", 4)
  end

  local toggleSpec = cfgGet("hotkeys.toggle", { "ctrl", "alt", "cmd", "S" })
  local windowSpec = cfgGet("hotkeys.read_window", { "ctrl", "alt", "cmd", "W" })

  local tMods, tKey = splitHotkey(toggleSpec)
  local wMods, wKey = splitHotkey(windowSpec)

  if tKey then hs.hotkey.bind(tMods, tKey, onToggle) end
  if wKey then hs.hotkey.bind(wMods, wKey, onReadWindow) end

  hs.printf("readaloud: loaded (cli=%s)", tostring(CLI))
end

M.start()

return M
