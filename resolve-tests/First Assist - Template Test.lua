-- First Assist - Template Test.lua
-- 1. Finds the first timeline in Master > 00_Timelines > 00.01_Working (your template).
-- 2. Duplicates it as "First Assist Test - from <template name>" in that same bin.
-- 3. Appends the first clip of the bin you had selected onto V1, trimmed to 1s - 3s, coloured Apricot.
-- Results show on screen (no Console needed):
--   no new timeline        -> duplicating failed
--   new timeline, empty    -> appending failed
--   new timeline with clip -> both work
local r = resolve or (Resolve and Resolve()) or (app and app:GetResolve())
local pm = r:GetProjectManager()
local project = pm:GetCurrentProject()
local pool = project:GetMediaPool()

local startFolder = pool:GetCurrentFolder()
local clip = (startFolder:GetClipList() or {})[1]

local function sub(folder, name)
  for _, f in ipairs(folder:GetSubFolderList() or {}) do if f:GetName() == name then return f end end
end
local tlFolder = sub(pool:GetRootFolder(), "00_Timelines")
tlFolder = tlFolder and sub(tlFolder, "00.01_Working")
if not tlFolder then return end

-- the template: first timeline item in that bin
local templateName
for _, item in ipairs(tlFolder:GetClipList() or {}) do
  if item:GetClipProperty("Type") == "Timeline" then templateName = item:GetName() break end
end
if not templateName then return end

local template
for i = 1, project:GetTimelineCount() do
  local t = project:GetTimelineByIndex(i)
  if t:GetName() == templateName then template = t end
end
if not template then return end

pool:SetCurrentFolder(tlFolder)
local copy = template:DuplicateTimeline("First Assist Test - from " .. templateName)
if not copy then return end
project:SetCurrentTimeline(copy)

if clip then
  local fps = tonumber(clip:GetClipProperty("FPS")) or 24
  local s, e = math.floor(fps), math.floor(fps * 3)
  local added = pool:AppendToTimeline({ { mediaPoolItem = clip, startFrame = s, endFrame = e, trackIndex = 1 } })
  if added and added[1] then added[1]:SetClipColor("Apricot") end
end
pool:SetCurrentFolder(startFolder)
pm:SaveProject()
