-- First Assist - Rename Test.lua
-- Tries each way of renaming a Media Pool clip and reports which one works.
-- Acts on the FIRST clip in the current bin. Results in Workspace > Console (Lua tab).
-- Run again to put the original name back.

local LOG = (os.getenv("HOME") or "") .. "/Movies/First Assist/first-assist/resolve/rename-test-log.txt"
local lines = {}
local function say(s) print("[Rename test] " .. s); lines[#lines + 1] = s end
local function finish()
  local f = io.open(LOG, "w")
  if f then f:write(os.date("%Y-%m-%d %H:%M:%S") .. "\n" .. table.concat(lines, "\n") .. "\n"); f:close()
    os.execute('open -a TextEdit "' .. LOG .. '"') end
end
local r = resolve or (Resolve and Resolve()) or (app and app:GetResolve())
if not r then say("FAIL - couldn't reach Resolve.") finish() return end
say("Resolve " .. tostring(r:GetVersionString()))

local project = r:GetProjectManager():GetCurrentProject()
local folder = project and project:GetMediaPool():GetCurrentFolder()
local clip = folder and (folder:GetClipList() or {})[1]
if not clip then say("FAIL - click a bin with clips in it first.") finish() return end

local function cur() return tostring(clip:GetClipProperty("Clip Name")) end
local start = cur()
say("Clip Name now: '" .. start .. "'   GetName(): '" .. tostring(clip:GetName()) .. "'   File: '" .. tostring(clip:GetClipProperty("File Name")) .. "'")

local restoring = start:find(" %[RT%d%]$") ~= nil
local base = restoring and start:gsub(" %[RT%d%]$", "") or start

local methods = {
  {"SetName(name)",                       function(n) return clip:SetName(n) end},
  {"SetClipProperty('Clip Name', name)",  function(n) return clip:SetClipProperty("Clip Name", n) end},
  {"SetClipProperty({['Clip Name']=name})", function(n) return clip:SetClipProperty({["Clip Name"] = n}) end},
  {"SetMetadata('Clip Name', name)",      function(n) return clip:SetMetadata("Clip Name", n) end},
}

local worked = nil
for i, m in ipairs(methods) do
  local target = restoring and base or (base .. " [RT" .. i .. "]")
  local ok, res = pcall(m[2], target)
  local now = cur()
  local pass = ok and now == target
  say((pass and "PASS  " or "fail  ") .. m[1] .. "  -> returned " .. tostring(ok and res or ("error: " .. tostring(res))) .. ", Clip Name is now '" .. now .. "'")
  if pass then worked = m[1] break end
end

if worked then
  say(restoring and ("Restored with " .. worked) or ("Renamed with " .. worked .. ". Run again to restore."))
else
  say("No method renamed the clip.")
end
finish()
