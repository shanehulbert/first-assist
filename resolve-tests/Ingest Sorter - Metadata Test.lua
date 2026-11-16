-- Ingest Sorter: can a script write clip metadata in free Resolve?
-- Select ONE clip in the Media Pool, run Workspace > Scripts > Ingest Sorter - Metadata Test.
-- Results appear as a bin called "IS Metadata Test" with one sub-folder per result (Console output doesn't show in free Resolve).
local R = resolve or (Resolve and Resolve()) or (app and app:GetResolve())
local results = {}
local function note(s) table.insert(results, s) end

local ok, err = pcall(function()
  local resolve = R
  local pm = resolve:GetProjectManager()
  local proj = pm:GetCurrentProject()
  local mp = proj:GetMediaPool()
  local clip, how
  local okSel, sel = pcall(function() return mp:GetSelectedClips() end)
  if okSel and sel then for _, c in pairs(sel) do clip = c; how = "selected" break end end
  if not clip then
    local cl = mp:GetCurrentFolder():GetClipList()
    if cl then for _, c in pairs(cl) do clip = c; how = "first in bin" break end end
  end
  if not clip then note("0 No clip found - select a clip first") return end
  note("0 Clip (" .. how .. ") " .. tostring(clip:GetName()))

  local tests = {
    {"Description", "IS test description"}, {"Keywords", "is-test, beach"}, {"Comments", "IS test comment"},
    {"Scene", "IS-1"}, {"Shot", "IS-2"}, {"Take", "3"}, {"Day / Night", "Day"}, {"Day/Night", "Day"},
    {"Shot Type", "Wide"}, {"Camera #", "A"}, {"Location", "IS test location"}, {"Good Take", "1"},
    {"People", "IS Person"}, {"Rating", "4"}, {"Angle", "Eye level"}, {"Move", "Pan"}, {"Shoot Day", "Day 1"},
    {"Production Name", "IS test production"}, {"Reel Number", "IS-R1"},
  }
  local good, bad = 0, 0
  for i, t in ipairs(tests) do
    local okW, r = pcall(function() return clip:SetMetadata(t[1], t[2]) end)
    local okR, back = pcall(function() return clip:GetMetadata(t[1]) end)
    local n = string.format("%02d", i)
    if okW and r and back == t[2] then good = good + 1; note("1 YES " .. n .. " " .. t[1])
    else bad = bad + 1; note("2 NO " .. n .. " " .. t[1] .. " (set " .. tostring(okW and r) .. ", read " .. tostring(okR and back) .. ")") end
  end
  local okT, tr = pcall(function() return clip:SetMetadata({["Description"] = "IS table write"}) end)
  note("3 Table write " .. tostring(okT and tr))
  -- Also try the clip-property route, which is how names/colours are set
  local okP, pr = pcall(function() return clip:SetClipProperty("Description", "IS property write") end)
  note("3 SetClipProperty Description " .. tostring(okP and pr))
  local okS, st = pcall(function() return clip:SetClipProperty("Rating", "4") end)
  note("3 SetClipProperty Rating " .. tostring(okS and st))
  note("0 Summary " .. good .. " worked, " .. bad .. " failed")
  pm:SaveProject()
end)
if not ok then note("9 SCRIPT ERROR " .. tostring(err)) end

-- Report: a bin with one sub-folder per line
pcall(function()
  local mp = R:GetProjectManager():GetCurrentProject():GetMediaPool()
  local root = mp:GetRootFolder()
  local back = mp:GetCurrentFolder()
  local okD, stamp = pcall(function() return os.date("%H.%M.%S") end)
  local rep = mp:AddSubFolder(root, "IS Metadata Test " .. (okD and stamp or tostring(#root:GetSubFolderList() + 1)))
  for _, s in ipairs(results) do
    mp:AddSubFolder(rep, (s:gsub("[/:\\]", "-")):sub(1, 120))
  end
  mp:SetCurrentFolder(back)
end)
