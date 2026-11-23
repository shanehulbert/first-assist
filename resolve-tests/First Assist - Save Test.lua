-- First Assist - Save Test.lua
-- On the FIRST clip in the current bin: removes the test tags from its name,
-- sets in/out marks (frames 24 to 72), then saves the project.
local r = resolve or (Resolve and Resolve()) or (app and app:GetResolve())
local pm = r:GetProjectManager()
local project = pm:GetCurrentProject()
local clip = (project:GetMediaPool():GetCurrentFolder():GetClipList() or {})[1]
if not clip then return end
local name = tostring(clip:GetClipProperty("Clip Name"))
name = name:gsub(" %[RT%d%]", ""):gsub(" %[TEST%]", "")
if not clip:SetName(name) then clip:SetClipProperty("Clip Name", name) end
clip:SetMarkInOut(24, 72, "all")
pm:SaveProject()
