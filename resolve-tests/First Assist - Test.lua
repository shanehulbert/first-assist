-- First Assist - Test.lua
-- Checks whether this copy of DaVinci Resolve lets a script rename Media Pool clips
-- and set their in/out marks. Run it from Workspace > Scripts.
--
-- First run:  renames the FIRST clip in the current bin (adds " [TEST]") and marks in/out.
-- Second run: puts the name back and clears the marks.
-- Results are printed in Workspace > Console (Lua tab).

local function say(s) print("[First Assist test] " .. s) end

local r = resolve or (Resolve and Resolve()) or (app and app:GetResolve())
if not r then say("FAIL - couldn't reach Resolve from this script.") return end
say("Resolve " .. tostring(r:GetVersionString()))

local project = r:GetProjectManager():GetCurrentProject()
if not project then say("FAIL - no project open.") return end
local pool = project:GetMediaPool()
local folder = pool:GetCurrentFolder()
local clips = folder and folder:GetClipList() or {}
local clip = clips[1]
if not clip then say("FAIL - the current bin is empty. Click a bin with clips in it and run again.") return end

local name = clip:GetClipProperty("Clip Name") or clip:GetName()
local file = clip:GetClipProperty("File Name") or "?"
say("Testing on: " .. tostring(name) .. "  (file " .. tostring(file) .. ") in bin '" .. folder:GetName() .. "'")

local restoring = name:sub(-7) == " [TEST]"
local newName = restoring and name:sub(1, -8) or (name .. " [TEST]")

-- 1. Rename
local ok, res = pcall(function() return clip:SetClipProperty("Clip Name", newName) end)
local after = clip:GetClipProperty("Clip Name")
if ok and after == newName then say("PASS rename - clip is now '" .. after .. "'")
else say("FAIL rename - " .. (ok and ("returned " .. tostring(res) .. ", name still '" .. tostring(after) .. "'") or tostring(res))) end

-- 2. In/out marks (frames counted from the start of the clip)
local okm, resm
if restoring then
  okm, resm = pcall(function() return clip:ClearMarkInOut("all") end)
  say((okm and resm) and "PASS clear marks" or ("FAIL clear marks - " .. tostring(resm)))
else
  okm, resm = pcall(function() return clip:SetMarkInOut(24, 72, "all") end)
  if okm and resm then
    local m = clip:GetMarkInOut() or {}
    local v = m.video or {}
    say("PASS marks - in " .. tostring(v["in"]) .. ", out " .. tostring(v["out"]) .. " (expected 24 and 72)")
  else
    say("FAIL marks - " .. tostring(resm))
  end
end

say(restoring and "Done - clip restored." or "Done. Check the clip in the Media Pool, then run this script again to undo.")
