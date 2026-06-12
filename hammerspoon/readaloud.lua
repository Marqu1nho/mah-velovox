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

local function alert(msg)
  if cfgGet("hotkeys.show_alerts", true) then
    hs.alert.show(msg, 0.8)
  end
end

local function isRunning()
  return readerTask ~= nil and readerTask:isRunning()
end

local function stopReader()
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

local function startReader(text, mode)
  if not CLI then return end
  if not text or #text == 0 then
    alert("readaloud: nothing to read")
    return
  end
  local arg = (mode == "window") and "--window" or "--stdin"
  readerTask = hs.task.new(CLI, function(exitCode, _, _)
    readerTask = nil
  end, { arg })
  readerTask:start()
  readerTask:setInput(text)
  readerTask:closeInput()
  alert(mode == "window" and "▶ reading window…" or "▶ reading…")
end

-- ---------------------------------------------------------------------------
-- Selection capture: ⌘C with clipboard save/restore, AXSelectedText fallback.
-- ---------------------------------------------------------------------------
local function captureSelection()
  local saved = hs.pasteboard.getContents()
  local savedCount = hs.pasteboard.changeCount()

  local text = nil
  local restored = false

  -- Ensure clipboard is always restored, even on error.
  local function restore()
    if not restored then
      restored = true
      if saved ~= nil then
        hs.pasteboard.setContents(saved)
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

  if (not text or #text == 0) then
    -- AX fallback: focused element's AXSelectedText.
    local el = hs.uielement.focusedElement and hs.uielement.focusedElement()
    if not el then
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
  end

  if not ok then
    hs.printf("readaloud: capture error: %s", tostring(err))
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
local function onToggle()
  if isRunning() then
    stopReader()
    alert("■ stopped")
    return
  end
  local text = captureSelection()
  if not text or #text == 0 then
    alert("readaloud: no selection")
    return
  end
  startReader(text, "selection")
end

local function onReadWindow()
  if isRunning() then
    stopReader()
    alert("■ stopped")
    return
  end
  local text = captureWindow()
  if not text or #text == 0 then
    alert("readaloud: no window text")
    return
  end
  startReader(text, "window")
end

-- ---------------------------------------------------------------------------
-- Setup.
-- ---------------------------------------------------------------------------
function M.start()
  config = loadConfig() or {}

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
