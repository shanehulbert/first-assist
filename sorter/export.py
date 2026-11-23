"""First Assist - the files DaVinci Resolve imports.

  <Project> - metadata.csv          File > Import Metadata To > Media Pool
                                     (match by file name; untick the timecode match)
  <Project> - draft timeline.fcpxml File > Import > Timeline
                                     (A-roll on V1, B-roll on V2, trimmed to your in/out points)
  <Project> - catalogue report.csv  every clip and its status, for your records

Clip names are built from the project's token list (default Title, Day, Shot,
Clip joined with "_", e.g. "Couple-dance_D2_MS_C076"). Empty tokens drop out.
"""
import csv, os, re
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

TOKENS = {  # token -> label shown in the app
    "project": "Project", "person": "Person", "title": "Title", "scene": "Scene", "shot": "Shot", "day": "Day",
    "date": "Date", "clip": "Clip", "camera": "Camera", "reel": "Reel", "role": "Role", "location": "Location",
}
DEFAULT_TOKENS = ["title", "day", "shot", "clip"]
DEFAULT_SEP = "_"
# Resolve metadata fields you can fill in the Metadata panel, in the order they're written to the CSV.
# The names must match Resolve's own field names exactly (taken from a Resolve metadata export).
META_FIELDS = ["Take", "Day / Night", "Environment", "Camera #", "Reel Number", "Location",
               "Production Name", "Program Name", "Episode #", "Episode Name", "Series #", "Genre", "Production Company",
               "Producer", "Assistant Producer", "Line Producer", "Unit Manager", "Post Producer", "Production Asst",
               "Director", "Assistant Director", "DOP", "Camera Operator", "Camera Assistant", "Focus Puller", "Key Grip",
               "Sound Mixer", "Digital Technician", "Script Supervisor", "Continuity", "Editor", "Editing Assistant",
               "Data Wrangler", "Colorist", "Colorist Assistant", "Dailies Colorist", "Crew Comments",
               "2nd Dir", "2nd Dir Asst", "2nd Continuity", "2nd DOP", "2nd Asst", "2nd DIT", "Camera Notes"]

DEFAULT_SETTINGS = {"conf_green": 70, "conf_red": 40,
                    "tl_bin": "00_Timelines/00.01_Working", "tl_template": "",
                    "ai_engine": "off", "ai_auto": False, "claude_model": "claude-haiku-5-5",
                    "ollama_model": "", "ollama_url": "http://localhost:11434", "faces_on": False}


def _clean(s):
    return re.sub(r'[/\\:*?"<>|%#]', "", str(s or "")).strip()


def _part(s, sep):
    s = _clean(s)
    if sep == "-":  # hyphen separates parts, so join words inside a part CamelCase-style
        return "".join(w[:1].upper() + w[1:] for w in re.split(r"[\s_-]+", s) if w)
    if sep == " ":
        return re.sub(r"\s+", " ", s)
    return re.sub(r"_+", "-", re.sub(r"\s+", "-", s))


def shoot_days(clips):
    """Shoot Day per recording date: D1 = the earliest date in the project."""
    dates = sorted({(c["meta"].get("recorded") or "")[:10] for c in clips if c["meta"].get("recorded")})
    return {d: f"D{i}" for i, d in enumerate(dates, 1)}


def context(project, clips, people=None, settings=None):
    return {"project": project or {}, "days": shoot_days(clips), "people": {p["id"]: p["name"] for p in people or []},
            "tokens": (project or {}).get("name_tokens") or DEFAULT_TOKENS,
            "sep": (project or {}).get("name_sep", DEFAULT_SEP), "settings": {**DEFAULT_SETTINGS, **(settings or {})}}


def effective(clip, ctx=None):
    ctx = ctx or context({}, [clip])
    ai, rv = clip.get("ai", {}), clip.get("review", {})
    pick = lambda k: rv.get(k) if rv.get(k) not in (None, "") else ai.get(k)
    m = clip["meta"]
    dur = m["duration"]
    i = rv.get("in") if rv.get("in") is not None else ai.get("suggested_in")
    o = rv.get("out") if rv.get("out") is not None else ai.get("suggested_out")
    i = max(0.0, float(i or 0))
    o = min(dur, float(o if o is not None else dur))
    if o <= i:
        i, o = 0.0, dur
    status = rv.get("status", "unreviewed")
    role = rv.get("role") or ("A-roll" if status in ("approved", "ingested") else "")
    mo = clip.get("motion") or {}
    confident = (mo.get("confidence") or 0) * 100 >= ctx["settings"]["conf_green"]
    movement = rv.get("movement") or (mo.get("movement") if confident else "")
    date = (m.get("recorded") or "")[:10]
    removed = set(rv.get("people_removed") or [])
    sure = [x["id"] for x in ai.get("people") or [] if (x.get("conf") or 0) >= ctx["settings"]["conf_green"]]
    people = [ctx["people"].get(pid, "") for pid in dict.fromkeys((rv.get("people") or []) + sure) if pid not in removed]
    people = [p for p in people if p]
    loc = ctx["project"].get("location") or ((clip.get("location") or {}).get("name") or "").split(",")[0]
    e = {"description": pick("description") or "", "scene": pick("scene") or "", "title": pick("title") or "",
         "keywords": pick("keywords") or [], "shot_type": pick("shot_type") or "", "angle": pick("angle") or "",
         "movement": movement or "", "day": ctx["days"].get(date, ""), "date": date, "people": people,
         "in": i, "out": o, "role": role, "rating": rv.get("rating", 0), "status": status, "notes": rv.get("notes", "")}
    cnum = re.search(r"(C\d{3,4})$", clip["id"])  # camera clip number (Blackmagic/ARRI style), else the file name
    tok = {"project": ctx["project"].get("name", ""), "person": people[0] if people else "",
           "title": e["title"] or e["scene"], "scene": e["scene"],
           "shot": e["shot_type"], "day": e["day"], "date": date, "clip": cnum.group(1) if cnum else clip["id"],
           "camera": m.get("camera_id", ""), "reel": m.get("reel", ""), "role": role, "location": loc}
    sep = ctx["sep"]
    auto = sep.join(p for p in (_part(tok.get(t, ""), sep) for t in ctx["tokens"]) if p)
    e["clip_name"] = _clean(rv.get("clip_name")) or auto
    return e


def path_of(clip):
    return clip.get("moved_to") or os.path.join(clip.get("src_dir", ""), clip["file"])


def frame_rate(fps):
    """(num, den) of one frame's duration, FCPXML style."""
    for nominal, ntsc in ((24, 23.976), (30, 29.97), (60, 59.94)):
        if abs(fps - ntsc) < 0.01:
            return 1001, nominal * 1000
    return 1, int(round(fps))


def tc_to_frames(tc, fps):
    if not tc:
        return 0
    h, mi, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return ((h * 60 + mi) * 60 + s) * int(round(fps)) + f


def fcpxml(clips, name, ctx):
    """Every approved clip in recorded order on a single video track."""
    first = clips[0]["meta"]
    num, den = frame_rate(first["fps"])
    seq_fps = int(round(first["fps"]))
    t = lambda frames: f"{frames * num}/{den}s" if frames else "0s"
    fmts, res, items = {}, [], []
    for idx, c in enumerate(clips, 1):
        m, e = c["meta"], effective(c, ctx)
        key = (m["width"], m["height"], m["fps"])
        if key not in fmts:
            fn, fd = frame_rate(m["fps"])
            fmts[key] = f"r{len(fmts) + 1}"
            res.append(f'<format id="{fmts[key]}" frameDuration="{fn}/{fd}s" width="{m["width"]}" height="{m["height"]}"/>')
        base = int(round(m["fps"]))
        start = tc_to_frames(m.get("timecode"), m["fps"])
        total = int(round(m["duration"] * base))
        fin, fout = int(round(e["in"] * base)), int(round(e["out"] * base))
        aid = f"a{idx}"
        audio = ' hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000"' if m.get("audio") else ""
        res.append(f'<asset id="{aid}" name={quoteattr(e["clip_name"])} start="{t(start)}" duration="{t(total)}" '
                   f'hasVideo="1" format="{fmts[key]}"{audio}><media-rep kind="original-media" '
                   f'src={quoteattr("file://" + quote(path_of(c)))}/></asset>')
        note = " · ".join(x for x in [", ".join(e["people"]), e["role"], e["scene"], e["shot_type"], e["description"]] if x)
        items.append({"aid": aid, "fid": fmts[key], "name": e["clip_name"], "src_in": start + fin,
                      "len": max(1, int(round((fout - fin) * seq_fps / base))), "note": note, "b": e["role"] == "B-roll"})

    # One track: every approved clip in order on V1. A-roll / B-roll is shown as clip colour by the Resolve script.
    body, pos = [], 0
    for x in items:
        body.append(f'<asset-clip ref="{x["aid"]}" name={quoteattr(x["name"])} offset="{t(pos)}" '
                    f'start="{t(x["src_in"])}" duration="{t(x["len"])}" format="{x["fid"]}" tcFormat="NDF">'
                    f'<note>{quoteattr(x["note"])[1:-1]}</note></asset-clip>')
        pos += x["len"]
    total_a = total_b = pos
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">\n<resources>\n'
            + "\n".join(res) + "\n</resources>\n<library>\n<event name=\"First Assist\">\n"
            + f'<project name={quoteattr(name)}>\n<sequence format="r1" duration="{t(max(total_a, total_b))}" tcStart="0s" tcFormat="NDF">\n<spine>\n'
            + "\n".join(body) + "\n</spine>\n</sequence>\n</project>\n</event>\n</library>\n</fcpxml>\n")


ROLE_COLOURS = {"A-roll": "Green", "B-roll": "Apricot"}
SCRIPT_NAME = "First Assist - Apply Last Send.lua"
OLD_SCRIPT_NAME = "Ingest Sorter - Apply Last Send.lua"   # the app's old name; replaced at the next Send


BASE_FIELDS = ["Scene", "Shot", "Shot Type", "Shoot Day", "People", "Description", "Keywords", "Comments",
               "Rating", "Angle", "Move"]


def project_meta(project):
    """Project-wide Resolve fields every clip inherits (a clip's own value wins). Production Name defaults to the project name."""
    project = project or {}
    out = {"Production Name": project.get("name", "")} if project.get("name") else {}
    out.update({k: v for k, v in (project.get("meta") or {}).items() if str(v).strip()})
    return out


def timeline_name(project):
    return project.get("timeline_name") or f'{project.get("name", "First Assist")} - draft'


def used_meta_fields(approved, ctx=None):
    pm = project_meta((ctx or {}).get("project"))
    return [f for f in META_FIELDS if f in pm or any((c["review"].get("meta") or {}).get(f) for c in approved)]


def metadata_values(c, e, used, ctx=None):
    """Field -> value for one clip, as sent to Resolve (by the script and the CSV)."""
    kw = list(dict.fromkeys(k for k in [e["role"], e["scene"], e["movement"]] + e["keywords"] if k))
    comment = " - ".join(x for x in [f'Rating {e["rating"]}/5' if e["rating"] else "",
                                     e["angle"] and f'{e["angle"]} angle', e["notes"]] if x)
    meta = {**project_meta((ctx or {}).get("project")), **{k: v for k, v in (c["review"].get("meta") or {}).items() if str(v).strip()}}
    vals = [e["scene"], e["shot_type"], e["shot_type"], e["day"], ", ".join(e["people"]), e["description"],
            ", ".join(kw), comment, e["rating"] or "", e["angle"], e["movement"]]
    out = dict(zip(BASE_FIELDS, vals))
    out.update({f: meta.get(f, "") for f in used})
    return out


def _lua(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ") + '"'


def resolve_script(clips, ctx, timeline_name, project_name):
    """A Lua script for Workspace > Scripts. Free Resolve won't let scripts read files, so the data is built in."""
    st = (ctx or {}).get("settings") or DEFAULT_SETTINGS
    rows = []
    used = used_meta_fields(clips, ctx)
    for c in clips:
        e = effective(c, ctx)
        md = ", ".join(f"[{_lua(k)}]={_lua(v)}" for k, v in metadata_values(c, e, used, ctx).items() if str(v).strip())
        base = int(round(c["meta"]["fps"]))
        fin = int(round(e["in"] * base))
        fend = max(fin + 1, int(round(e["out"] * base)))
        colour = ROLE_COLOURS.get(e["role"], "Green")
        rows.append(f'  {{file={_lua(c["file"])}, path={_lua(path_of(c))}, name={_lua(e["clip_name"])}, i={fin}, o={fend - 1}, e={fend}, colour={_lua(colour)}, m={{{md}}}}},')
    return f"""-- First Assist - Apply Last Send
-- Project: {project_name}   Timeline: {timeline_name}
-- Written by First Assist. Each Send replaces this file.
-- In Resolve: select your media bin and run Workspace > Scripts > First Assist - Apply Last Send.
-- It also writes the clip metadata (scene, shot type, people, description, keywords, rating...); the CSV is a backup.
--  1. Finds each clip below in the Media Pool by file name (importing it into the selected bin if it isn't there yet),
--     renames it, sets its in/out marks, colours it (A-roll Green, B-roll Apricot) and writes its metadata.
--  2. The draft timeline: the first Send duplicates your template timeline and puts the clips on V1, trimmed to their in/out.
--     Later Sends add only the newly approved clips to the end of that same timeline.
--  3. Saves the project.

local CLIPS = {{
{chr(10).join(rows)}
}}
local TIMELINE = {_lua(timeline_name)}
local TEMPLATE_BIN = {_lua(st.get("tl_bin") or "")}
local TEMPLATE_NAME = {_lua(st.get("tl_template") or "")}

local r = resolve or (Resolve and Resolve()) or (app and app:GetResolve())
if not r then return end
local pm = r:GetProjectManager()
local project = pm:GetCurrentProject()
if not project then return end
local pool = project:GetMediaPool()
local root = pool:GetRootFolder()
local startFolder = pool:GetCurrentFolder()

-- clips are matched by their full file path; the file name is only used when it's unique in this list
-- (iPhones reuse names like IMG_0101.MOV, so two different clips can share a name)
local byPath, byName, nameCount, items = {{}}, {{}}, {{}}, {{}}
for _, c in ipairs(CLIPS) do
  byPath[c.path] = c
  nameCount[c.file] = (nameCount[c.file] or 0) + 1
  byName[c.file] = c
end
local function find(item)
  if not item then return nil end
  local c = byPath[item:GetClipProperty("File Path") or ""]
  if c then return c end
  local n = item:GetClipProperty("File Name") or ""
  if nameCount[n] == 1 then return byName[n] end
end

-- 1. Media Pool clips
local function apply(item)
  local c = find(item)
  if not c then return end
  if not item:SetName(c.name) then item:SetClipProperty("Clip Name", c.name) end
  item:SetMarkInOut(c.i, c.o, "all")
  item:SetClipColor(c.colour)
  for field, value in pairs(c.m or {{}}) do
    pcall(function() item:SetMetadata(field, value) end)
  end
  items[c.path] = items[c.path] or item
end
local function visit(folder)
  for _, item in ipairs(folder:GetClipList() or {{}}) do apply(item) end
  for _, sub in ipairs(folder:GetSubFolderList() or {{}}) do visit(sub) end
end
visit(root)
-- clips not in the Media Pool yet are imported into the bin you had selected
local toImport = {{}}
for _, c in ipairs(CLIPS) do if not items[c.path] then toImport[#toImport + 1] = c.path end end
if #toImport > 0 then
  pool:SetCurrentFolder(startFolder)
  for _, item in ipairs(pool:ImportMedia(toImport) or {{}}) do apply(item) end
end

-- 2. Draft timeline
local function findTimeline(name)
  for n = 1, project:GetTimelineCount() do
    local t = project:GetTimelineByIndex(n)
    if t and t:GetName() == name then return t end
  end
end
local function folderAt(path)
  local f = root
  for part in string.gmatch(path or "", "[^/]+") do
    local nextF
    for _, s in ipairs(f:GetSubFolderList() or {{}}) do if s:GetName() == part then nextF = s end end
    if not nextF then return nil end
    f = nextF
  end
  return f
end
-- the template: by name anywhere, otherwise the first timeline in the template bin that isn't a draft
local function findTemplate(folder, wanted)
  for _, item in ipairs(folder:GetClipList() or {{}}) do
    if item:GetClipProperty("Type") == "Timeline" then
      local n = item:GetName()
      if (wanted ~= "" and n == wanted) or (wanted == "" and not n:find(" %- draft ") and not n:find("^First Assist") and not n:find("^Ingest Sorter")) then
        return n, folder
      end
    end
  end
  if wanted ~= "" then
    for _, sub in ipairs(folder:GetSubFolderList() or {{}}) do
      local n, f = findTemplate(sub, wanted)
      if n then return n, f end
    end
  end
end

local function clipInfo(c)
  return {{mediaPoolItem = items[c.path], startFrame = c.i, endFrame = c.e, trackIndex = 1}}
end
local tl = findTimeline(TIMELINE)
if tl then
  -- already built by an earlier Send: add only the clips that aren't on it yet, at the end
  project:SetCurrentTimeline(tl)
  local onTl = {{}}
  for track = 1, tl:GetTrackCount("video") do
    for _, ti in ipairs(tl:GetItemListInTrack("video", track) or {{}}) do
      local c = find(ti:GetMediaPoolItem())
      if c then onTl[c.path] = true end
    end
  end
  local list = {{}}
  for _, c in ipairs(CLIPS) do
    if items[c.path] and not onTl[c.path] then list[#list + 1] = clipInfo(c) end
  end
  if #list > 0 then pool:AppendToTimeline(list) end
else
  local binF = folderAt(TEMPLATE_BIN)
  local tName, tFolder
  if TEMPLATE_NAME ~= "" then tName, tFolder = findTemplate(root, TEMPLATE_NAME)
  elseif binF then tName, tFolder = findTemplate(binF, "") end
  pool:SetCurrentFolder(tFolder or binF or startFolder)
  local template = tName and findTimeline(tName)
  tl = template and template:DuplicateTimeline(TIMELINE) or pool:CreateEmptyTimeline(TIMELINE)
  if tl then
    project:SetCurrentTimeline(tl)
    local list = {{}}
    for _, c in ipairs(CLIPS) do
      if items[c.path] then list[#list + 1] = clipInfo(c) end
    end
    if #list > 0 then pool:AppendToTimeline(list) end
  end
end

-- colour the clips on the draft timeline
if tl then
  project:SetCurrentTimeline(tl)
  for track = 1, tl:GetTrackCount("video") do
    for _, ti in ipairs(tl:GetItemListInTrack("video", track) or {{}}) do
      local c = find(ti:GetMediaPoolItem())
      if c then ti:SetClipColor(c.colour) end
    end
  end
end

pool:SetCurrentFolder(startFolder)
pm:SaveProject()
"""


def install_resolve_script(text):
    """Put the script where Resolve lists it under Workspace > Scripts (macOS)."""
    folder = os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility")
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, SCRIPT_NAME)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        old = os.path.join(folder, OLD_SCRIPT_NAME)   # from before the rename (First Assist)
        if os.path.isfile(old):
            os.remove(old)
        return path
    except OSError:
        return ""


def write_resolve_files(cat, dest, project, batch, people=None, settings=None, folder_name="_Resolve import"):
    out = os.path.join(dest, folder_name) if folder_name else dest
    os.makedirs(out, exist_ok=True)
    clips = sorted(cat["clips"].values(), key=lambda c: (c["meta"].get("recorded", ""), c["file"]))
    ctx = context(project, clips, people, settings)
    approved = [c for c in clips if c["review"]["status"] in ("approved", "ingested")]
    safe = "".join(ch for ch in project["name"] if ch not in '/:\\')
    names = {"metadata": f"{safe} - metadata.csv", "timeline": f"{safe} - draft timeline {batch}.fcpxml",
             "report": f"{safe} - catalogue report.csv"}

    with open(os.path.join(out, names["metadata"]), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        # Clip names are applied by the generated Resolve script; Resolve ignores Clip Name in a CSV import.
        used = used_meta_fields(approved, ctx)
        cols = BASE_FIELDS + [f for f in used if f not in BASE_FIELDS]
        w.writerow(["File Name", "Clip Name"] + cols)
        for c in approved:
            e = effective(c, ctx)
            v = metadata_values(c, e, used, ctx)
            w.writerow([c["file"], e["clip_name"]] + [v.get(f, "") for f in cols])

    script_installed = ""
    if approved:
        tl_name = timeline_name(project)
        with open(os.path.join(out, names["timeline"]), "w", encoding="utf-8") as f:
            f.write(fcpxml(approved, tl_name, ctx))
        lua = resolve_script(approved, ctx, tl_name, project["name"])
        with open(os.path.join(out, SCRIPT_NAME), "w", encoding="utf-8") as f:
            f.write(lua)
        script_installed = install_resolve_script(lua)

    with open(os.path.join(out, names["report"]), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["File Name", "Clip Name", "Status", "Role", "Rating", "People", "Scene", "Shot type", "Shoot Day",
                    "Angle", "Movement (sent to Resolve)", "Measured movement", "Movement confidence %", "Description",
                    "Keywords", "In (s)", "Out (s)", "Duration (s)", "Camera", "Camera ID", "Reel", "Lens", "Recorded",
                    "Timecode", "Location", "Now at", "Technical flags", "Notes"])
        for c in clips:
            e = effective(c, ctx)
            mo = c.get("motion") or {}
            m = c["meta"]
            w.writerow([c["file"], e["clip_name"], e["status"], e["role"], e["rating"], ", ".join(e["people"]),
                        e["scene"], e["shot_type"], e["day"], e["angle"], e["movement"], mo.get("movement", ""),
                        round((mo.get("confidence") or 0) * 100) if mo else "", e["description"],
                        ", ".join(e["keywords"]), round(e["in"], 2), round(e["out"], 2), m["duration"],
                        m.get("camera", ""), m.get("camera_id", ""), m.get("reel", ""), m.get("lens", ""),
                        m.get("recorded", ""), m.get("timecode", ""), (c.get("location") or {}).get("name") or "",
                        path_of(c), "; ".join(c.get("flags", [])), e["notes"]])
    return {"folder": out, "script_installed": script_installed, "timeline_name": timeline_name(project),
            **{k: os.path.join(out, v) for k, v in names.items()}}
