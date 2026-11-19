"""Ingest Sorter - the library: projects, master term list, ingest, location
lookup, and the verified move to a destination folder.

Layout on disk (next to the footage-sorter code folder):
    library/library.json            projects + master term list
    library/geocache.json           GPS -> place name lookups (one per location)
    library/projects/<id>/catalogue.json, thumbs/, proxies/, moves.json
"""
import hashlib, json, os, re, shutil, threading, time, urllib.parse, urllib.request
from datetime import datetime

from ingest import VIDEO_EXT, probe, tech_flags, ffmpeg, proxy_encoder, tools, find_tool

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.abspath(os.path.join(HERE, "..", "..", "library"))
LOCK = threading.RLock()
STATUS = {}  # project id -> {"state", "stage", "done", "total", "message"}
ROLES = ["A-roll", "B-roll"]
SELECTED = ("approved", "ingested")  # clips that go to Resolve; "ingested" = already moved to the destination
SHOT_TYPES = ["EST", "EWS", "WS", "MWS", "MS", "MCU", "CU", "ECU", "OTS", "Insert"]
ANGLES = ["Eye level", "High", "Low", "Overhead", "Dutch"]
MOVEMENTS = ["Static", "Pan left", "Pan right", "Tilt up", "Tilt down", "Push in", "Pull out", "Tracking",
             "Handheld", "Mixed"]
REVIEW_DEFAULTS = {"status": "unreviewed", "rating": 0, "in": None, "out": None, "notes": "", "keywords": None,
                   "description": None, "scene": None, "title": None, "role": "", "shot_type": None, "angle": None,
                   "movement": None, "clip_name": None, "people": []}
AI_DEFAULTS = {"description": "", "scene": "", "title": "", "keywords": [], "shot_type": "", "angle": "", "source": ""}


# ---------- storage ----------
def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def lib_file():
    return os.path.join(LIB, "library.json")


def load_lib():
    lib = _read(lib_file(), {"projects": [], "vocab": []})
    lib.setdefault("people", [])
    lib.setdefault("settings", {})
    for p in lib["projects"]:  # v1.3 single pattern string -> token list
        if "name_tokens" not in p:
            p["name_tokens"] = (p.pop("name_pattern", None) or "title_day_shot_clip").split("_")
            p.setdefault("name_sep", "_")
        if not p.get("mode"):  # before v1.9 every project moved its selects, like a Shoot project
            p["mode"] = "shoot"
    if "vocab" not in lib:  # v2 "terms" become pinned vocabulary entries
        lib["vocab"] = [{"term": t, "synonyms": [], "pinned": True} for t in lib.get("terms", [])]
    lib.pop("terms", None)
    return _people_parts(lib)


def _people_parts(lib):
    for p in lib.get("people", []):
        _ensure_parts(p)
    return lib


def save_lib(lib):
    _write(lib_file(), lib)


def project(pid):
    return next((p for p in load_lib()["projects"] if p["id"] == pid), None)


def pdir(pid):
    return os.path.join(LIB, "projects", pid)


def upgrade_clip(c):
    """Bring a clip record up to the v3 fields. Safe to run repeatedly."""
    ai, rv = c.setdefault("ai", {}), c.setdefault("review", {})
    for d in (ai, rv):
        if "category" in d:
            if d.get("scene") in (None, ""):
                d["scene"] = d["category"]
            del d["category"]
        if "tags" in d:
            if d.get("keywords") in (None, []):
                d["keywords"] = d["tags"]
            del d["tags"]
    if rv.get("role") in ("Establishing", "Cutaway"):
        if rv.get("role") == "Establishing" and not rv.get("shot_type"):
            rv["shot_type"] = "EST"
        rv["role"] = "B-roll"
    for k, v in REVIEW_DEFAULTS.items():
        rv.setdefault(k, v)
    for k, v in AI_DEFAULTS.items():
        ai.setdefault(k, v if not isinstance(v, list) else [])
    if c.get("moved_to") and rv["status"] == "approved":
        rv["status"] = "ingested"
    if rv["status"] in SELECTED and not rv.get("role"):
        rv["role"] = "A-roll"
    return c


def load_cat(pid):
    path = os.path.join(pdir(pid), "catalogue.json")
    cat = _read(path, {"clips": {}})
    if cat.get("version", 0) < 3 or any("category" in c.get("review", {}) for c in cat["clips"].values()):
        with LOCK:
            cat = _read(path, {"clips": {}})
            for c in cat["clips"].values():
                upgrade_clip(c)
            cat["version"] = 3
            if os.path.exists(path):
                _write(path, cat)
    return cat


def save_cat(pid, cat):
    cat["version"] = 3
    _write(os.path.join(pdir(pid), "catalogue.json"), cat)


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"
    existing = {p["id"] for p in load_lib()["projects"]}
    out, n = s, 2
    while out in existing:
        out, n = f"{s}-{n}", n + 1
    return out


def clip_path(clip):
    return clip.get("moved_to") or os.path.join(clip["src_dir"], clip["file"])


# ---------- projects and terms ----------
RUSHES, SELECTS = "00_Rushes", "01_Selects"
SKIP_DIRS = {"imovie cache", "imovie thumbnails", "_resolve import", "02_project", "03_deliverables"}


def loose_videos(root):
    """Video files sitting directly in a project folder or its 01_Footage (not yet in 00_Rushes)."""
    out = []
    for d in (root, os.path.join(root, "01_Footage")):
        if os.path.isdir(d):
            out += [os.path.join(d, f) for f in sorted(os.listdir(d))
                    if not f.startswith(".") and os.path.splitext(f)[1].lower() in VIDEO_EXT and os.path.isfile(os.path.join(d, f))]
    return out


def check_folder(root):
    root = (root or "").rstrip("/")
    if not os.path.isdir(root):
        return {"error": "Can't find that folder."}
    foot = os.path.join(root, "01_Footage")
    return {"name": os.path.basename(root), "loose": len(loose_videos(root)),
            "has_rushes": os.path.isdir(os.path.join(foot, RUSHES)), "has_selects": os.path.isdir(os.path.join(foot, SELECTS)),
            "has_template": os.path.isdir(foot)}


def resolve_dir_of(p):
    """Where the Resolve files (script, CSV, backup timeline) go for this project."""
    if p.get("resolve_dir"):
        return p["resolve_dir"]
    if p.get("destination"):  # projects made before v1.9
        return os.path.join(p["destination"], "_Resolve import")
    return os.path.join(pdir(p["id"]), "_Resolve Import")


def create_project(name, source=None, destination=None, mode=None, root=None, sources=None, edit_dir=None, move_loose=False, meta=None):
    """mode "shoot": root is the shoot's folder; footage lives in 01_Footage/00_Rushes, Send moves selects to 01_Selects.
    mode "archive": footage stays where it is (any number of folders); Send moves nothing."""
    entry = {"created": datetime.now().isoformat(timespec="seconds"), "sends": [],
             "meta": {k: str(v).strip() for k, v in (meta or {}).items() if str(v).strip()}}
    moved = 0
    if mode == "shoot":
        root = root.rstrip("/")
        rushes, selects = os.path.join(root, "01_Footage", RUSHES), os.path.join(root, "01_Footage", SELECTS)
        rdir = os.path.join(root, "02_Project", "_Resolve Import")
        for d in (rushes, selects, rdir, os.path.join(root, "03_Deliverables")):
            os.makedirs(d, exist_ok=True)
        if move_loose:
            for f in loose_videos(root):
                dst = os.path.join(rushes, os.path.basename(f))
                if not os.path.exists(dst):
                    safe_move(f, dst)
                    moved += 1
        entry.update(mode="shoot", root=root, sources=[rushes], destination=selects, resolve_dir=rdir)
    elif mode == "archive":
        rdir = ""
        if edit_dir:
            edit_dir = edit_dir.rstrip("/")
            rdir = os.path.join(edit_dir, "02_Project", "_Resolve Import")
            for d in (rdir, os.path.join(edit_dir, "03_Deliverables")):
                os.makedirs(d, exist_ok=True)
        entry.update(mode="archive", root=edit_dir or "", sources=[s.rstrip("/") for s in sources or [] if s], destination="", resolve_dir=rdir)
    else:
        entry.update(sources=[source] if source else [], destination=destination or "")
    with LOCK:
        lib = load_lib()
        pid, n = slug(name), 2
        while any(x["id"] == pid for x in lib["projects"]):
            pid, n = f"{slug(name)}-{n}", n + 1
        lib["projects"].append({"id": pid, "name": name, **entry})
        save_lib(lib)
        os.makedirs(os.path.join(pdir(pid), "thumbs"), exist_ok=True)
        os.makedirs(os.path.join(pdir(pid), "proxies"), exist_ok=True)
        save_cat(pid, {"clips": {}})
    return pid, moved


def remove_project(pid):
    """Take a project out of the app. Its catalogue, thumbnails and proxies are moved to
    library/_removed projects/ (not deleted). Footage in the source and destination is never touched."""
    if STATUS.get(pid, {}).get("state") == "running":
        return {"error": "This project is still processing. Try again when it has finished."}
    with LOCK:
        lib = load_lib()
        p = next((x for x in lib["projects"] if x["id"] == pid), None)
        if not p:
            return {"error": "No such project."}
        lib["projects"].remove(p)
        save_lib(lib)
        src = pdir(pid)
        if os.path.isdir(src):
            bin_dir = os.path.join(LIB, "_removed projects")
            os.makedirs(bin_dir, exist_ok=True)
            dst = os.path.join(bin_dir, f"{pid} (removed {datetime.now().strftime('%Y-%m-%d %H%M')})")
            with open(os.path.join(src, "project.json"), "w") as f:
                json.dump(p, f, indent=1)  # keep the settings so the project could be restored
            shutil.move(src, dst)
        STATUS.pop(pid, None)
    return {"ok": True, "name": p["name"]}


def update_project(pid, **kw):
    with LOCK:
        lib = load_lib()
        p = next(p for p in lib["projects"] if p["id"] == pid)
        for k, v in kw.items():
            if k == "add_source":
                if v and v not in p["sources"]:
                    p["sources"].append(v)
            elif k == "meta":
                p["meta"] = {mk: str(mv).strip() for mk, mv in (v or {}).items() if str(mv).strip()}
            elif k in ("name", "destination", "client", "location", "name_tokens", "name_sep", "mode", "resolve_dir"):
                p[k] = v
        save_lib(lib)
        return p


# ---------- project icon ----------


def icon_file(pid):
    return os.path.join(pdir(pid), "icon.jpg")


def set_icon(pid, cid, jpeg, t=None):
    """Save a square frame chosen in the player as the project icon. Only one clip per project."""
    if not jpeg or not jpeg.startswith(b"\xff\xd8"):
        return {"error": "That image couldn't be read."}
    with LOCK:
        if cid not in load_cat(pid)["clips"]:
            return {"error": "no such clip"}
        os.makedirs(pdir(pid), exist_ok=True)
        tmp = icon_file(pid) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(jpeg)
        os.replace(tmp, icon_file(pid))
        lib = load_lib()
        p = next(x for x in lib["projects"] if x["id"] == pid)
        p["icon"] = {"clip": cid, "t": t}
        save_lib(lib)
    return {"ok": True, "icon": p["icon"]}


def clear_icon(pid):
    with LOCK:
        lib = load_lib()
        p = next(x for x in lib["projects"] if x["id"] == pid)
        p.pop("icon", None)
        save_lib(lib)
        if os.path.exists(icon_file(pid)):
            os.remove(icon_file(pid))  # our own small image in the library, never footage
    return {"ok": True}


def icon_url(p, clips):
    """The picture shown for a project: the chosen frame, else the first approved 5-star clip,
    else the first approved clip (middle thumbnail), else "" for the coloured initial."""
    pid = p["id"]
    f = icon_file(pid)
    if p.get("icon") and os.path.exists(f):
        return f"/p/{pid}/icon.jpg?v={os.stat(f).st_mtime_ns}"
    sel = sorted((c for c in clips if c.get("review", {}).get("status") in SELECTED and c.get("thumbs")),
                 key=lambda c: (c.get("meta", {}).get("recorded") or "") + c.get("file", ""))
    for pick in ([c for c in sel if c["review"].get("rating") == 5], sel):
        if pick:
            th = pick[0]["thumbs"]
            return f"/p/{pid}/{urllib.parse.quote(th[len(th) // 2]['src'])}"
    return ""


def set_mode(pid, mode, root=None):
    """Switch a project between New shoot and From existing footage. Nothing on disk is moved by switching."""
    p = project(pid)
    if mode == "archive":
        upd = {"mode": "archive", "resolve_dir": resolve_dir_of(p)}
    elif mode == "shoot":
        root = (root or p.get("root") or "").rstrip("/")
        if not root or not os.path.isdir(root):
            return {"error": "Choose the project folder."}
        rushes, selects = os.path.join(root, "01_Footage", RUSHES), os.path.join(root, "01_Footage", SELECTS)
        rdir = os.path.join(root, "02_Project", "_Resolve Import")
        for d in (rushes, selects, rdir, os.path.join(root, "03_Deliverables")):
            os.makedirs(d, exist_ok=True)
        upd = {"mode": "shoot", "root": root, "destination": selects, "resolve_dir": rdir}
    else:
        return {"error": "Unknown mode."}
    with LOCK:
        lib = load_lib()
        pr = next(x for x in lib["projects"] if x["id"] == pid)
        pr.update(upd)
        if mode == "shoot" and upd["destination"] and os.path.join(root, "01_Footage", RUSHES) not in pr["sources"]:
            pr["sources"].append(os.path.join(root, "01_Footage", RUSHES))
        save_lib(lib)
        return {"ok": True, "project": pr}


# ---------- Controlled Vocabulary ----------
def canon_map(lib=None):
    """lower-case term or synonym -> the vocabulary term it stands for"""
    lib = lib or load_lib()
    m = {}
    for v in lib["vocab"]:
        m[v["term"].lower()] = v["term"]
        for s in v.get("synonyms", []):
            m[s.lower()] = v["term"]
    return m


def normalize(keywords, cmap=None):
    cmap = cmap if cmap is not None else canon_map()
    out, seen = [], set()
    for k in keywords or []:
        k = str(k).strip()
        if not k:
            continue
        k = cmap.get(k.lower(), k)
        if k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


def _replace_everywhere(old, new):
    """Swap keyword `old` for `new` (or remove it if new is None) on every clip in every project."""
    n = 0
    for p in load_lib()["projects"]:
        cat = load_cat(p["id"])
        changed = False
        for c in cat["clips"].values():
            for d in (c["ai"], c["review"]):
                kws = d.get("keywords")
                if kws and any(k.lower() == old.lower() for k in kws):
                    rep = [new if k.lower() == old.lower() else k for k in kws] if new else \
                          [k for k in kws if k.lower() != old.lower()]
                    d["keywords"] = normalize(rep, {})
                    changed = True
                    n += 1
        if changed:
            save_cat(p["id"], cat)
    return n


def blocked_terms(lib=None):
    return {b["term"].lower() for b in (lib or load_lib()).get("blocked", [])}


def vocab_action(data):
    act, term = data.get("action"), (data.get("term") or "").strip()
    with LOCK:
        lib = load_lib()
        voc = lib["vocab"]
        find = lambda t: next((v for v in voc if v["term"].lower() == t.lower()), None)
        entry = find(term) if term else None
        if act == "add":
            cm = canon_map(lib)
            if term.lower() in cm:  # already a term or a synonym
                entry = find(cm[term.lower()])
                if data.get("pinned"):
                    entry["pinned"] = True
            elif term:
                voc.append({"term": term, "synonyms": [], "pinned": bool(data.get("pinned"))})
        elif act == "pin" and entry:
            entry["pinned"] = bool(data.get("pinned"))
        elif act == "synonyms" and entry:
            entry["synonyms"] = [s.strip() for s in data.get("synonyms", []) if s.strip() and s.strip().lower() != term.lower()]
        elif act == "delete" and entry:
            voc.remove(entry)
        elif act == "block" and term:
            # take the keyword off every clip and keep it on the Removed list so the AI doesn't add it again
            if entry:
                voc.remove(entry)
            blocked = lib.setdefault("blocked", [])
            if not any(b["term"].lower() == term.lower() for b in blocked):
                blocked.append({"term": term, "removed": datetime.now().isoformat(timespec="seconds"), "clips": 0})
            save_lib(lib)
            n = _replace_everywhere(term, None)
            lib = load_lib()
            for b in lib["blocked"]:
                if b["term"].lower() == term.lower():
                    b["clips"] = n
            save_lib(lib)
            return lib["vocab"]
        elif act == "unblock" and term:
            lib["blocked"] = [b for b in lib.get("blocked", []) if b["term"].lower() != term.lower()]
            if data.get("add") and not find(term):
                voc.append({"term": term, "synonyms": [], "pinned": False})
        elif act == "rename" and entry:
            to = (data.get("to") or "").strip()
            if to and not find(to):
                entry["term"] = to
                save_lib(lib)
                _replace_everywhere(term, to)
                return load_lib()["vocab"]
        elif act == "merge":
            into = find((data.get("into") or "").strip())
            if into and term.lower() != into["term"].lower():
                if entry:
                    into["synonyms"] = list(dict.fromkeys(into.get("synonyms", []) + [entry["term"]] + entry.get("synonyms", [])))
                    voc.remove(entry)
                elif term.lower() not in [s.lower() for s in into.get("synonyms", [])]:
                    into.setdefault("synonyms", []).append(term)
                save_lib(lib)
                _replace_everywhere(term, into["term"])
                return load_lib()["vocab"]
        save_lib(lib)
        return lib["vocab"]


# ---------- location ----------
def parse_gps(s):
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", s or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def place_name(lat, lon):
    """Reverse-geocode via OpenStreetMap, cached per ~100 m so a whole shoot costs one lookup."""
    key = f"{lat:.3f},{lon:.3f}"
    cache_file = os.path.join(LIB, "geocache.json")
    with LOCK:
        cache = _read(cache_file, {})
    if key in cache:
        return cache[key]
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 16, "accept-language": "en"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FootageSorter/0.2 (personal footage catalogue)"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        a = d.get("address", {})
        parts = [d.get("name") or a.get("tourism") or a.get("amenity") or a.get("building"),
                 a.get("suburb") or a.get("neighbourhood"),
                 a.get("city") or a.get("town") or a.get("village") or a.get("county"),
                 a.get("state"), a.get("country")]
        seen, name = set(), []
        for x in parts:
            if x and x not in seen:
                seen.add(x)
                name.append(x)
        result = ", ".join(name) or d.get("display_name", "")
        time.sleep(1)  # OpenStreetMap asks for at most one request per second
    except Exception:
        return None  # offline etc. - try again next time
    with LOCK:
        cache = _read(cache_file, {})
        cache[key] = result
        _write(cache_file, cache)
    return result


# ---------- ingest ----------
def _set(pid, **kw):
    STATUS.setdefault(pid, {}).update(kw)


def ingest(pid, proxies=True):
    """Find new clips in every source folder, then metadata + thumbnails for all of them
    (so the grid fills quickly), then locations, then preview proxies."""
    if STATUS.get(pid, {}).get("state") == "running":
        return
    _set(pid, state="running", stage="Scanning", done=0, total=0, message="", failed=0)
    try:
        missing = [k for k, v in tools().items() if not v]
        if missing:
            _set(pid, state="error", stage="Error", message="ffmpeg isn't installed on this Mac, so footage can't be "
                 "read. See the red banner for how to install it.")
            return
        p = project(pid)
        cat = load_cat(pid)
        known = {(c["src_dir"], c["file"]) for c in cat["clips"].values()}
        dest = os.path.abspath(p.get("destination") or "/nonexistent") + os.sep
        new, offline = [], []
        for src in p["sources"]:
            if not os.path.isdir(src):
                offline.append(src)
                continue
            for root, dirs, files in os.walk(src):  # include subfolders (camera cards, day folders...)
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS
                                 and not (os.path.abspath(os.path.join(root, d)) + os.sep).startswith(dest))
                for f in sorted(files):
                    if f.startswith(".") or os.path.splitext(f)[1].lower() not in VIDEO_EXT:
                        continue
                    if (root, f) not in known:
                        new.append((root, f))
        if offline and not new:
            _set(pid, message="Can't reach: " + "; ".join(offline) + ". Is the drive connected?")
        has_et = bool(find_tool("exiftool"))
        stale = [c for c in cat["clips"].values() if ("camera_id" not in c["meta"] or "nrt_checked" not in c["meta"]
                                                      or (has_et and not c["meta"].get("et_checked")))
                 and os.path.exists(clip_path(c))]
        if stale:
            _set(pid, stage="Updating clip details", total=len(stale), done=0)
            for i, c in enumerate(stale, 1):
                try:
                    m = probe(clip_path(c))
                except Exception:
                    continue
                with LOCK:
                    cat = load_cat(pid)
                    cm = cat["clips"][c["id"]]["meta"]
                    cm.update({"camera_id": m.get("camera_id", ""), "reel": m.get("reel", ""), "nrt_checked": True})
                    for k in ("camera", "lens", "focal", "aperture", "iso", "shutter", "wb_kelvin", "shutter_angle"):
                        if m.get(k) not in (None, "") and cm.get(k) in (None, ""):
                            cm[k] = m[k]
                    cm["et_checked"] = m.get("et_checked", False)
                    save_cat(pid, cat)
                _set(pid, done=i)
        _set(pid, stage="Reading clips", total=len(new), done=0)
        d = pdir(pid)
        for i, (src, f) in enumerate(new, 1):
            path = os.path.join(src, f)
            cid = os.path.splitext(f)[0]
            with LOCK:
                cat = load_cat(pid)
                base, n = cid, 2
                while cid in cat["clips"]:
                    cid, n = f"{base}_{n}", n + 1
            try:
                m = probe(path)
            except Exception as e:
                failed = STATUS[pid].get("failed", 0) + 1
                _set(pid, failed=failed, message=f"Couldn't read {failed} file(s), most recently {f}")
                continue
            thumbs = []
            for k in range(5):
                t = m["duration"] * (k + 0.5) / 5
                rel = f"thumbs/{cid}_{k}.jpg"
                if not os.path.exists(os.path.join(d, rel)):
                    try:
                        ffmpeg("-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4",
                               os.path.join(d, rel))
                    except Exception:
                        continue
                thumbs.append({"t": round(t, 2), "src": rel})
            clip = {"id": cid, "file": f, "src_dir": src, "meta": m, "flags": tech_flags(m), "thumbs": thumbs,
                    "added": datetime.now().isoformat(timespec="seconds"),
                    "ai": json.loads(json.dumps(AI_DEFAULTS)), "review": dict(REVIEW_DEFAULTS)}
            with LOCK:
                cat = load_cat(pid)
                cat["clips"][cid] = clip
                save_cat(pid, cat)
            _set(pid, done=i)
        if new and STATUS[pid].get("failed", 0) == len(new):
            _set(pid, state="error", stage="Error", message=f"None of the {len(new)} files could be read. "
                 + STATUS[pid].get("message", ""))
            return
        locate(pid)
        if proxies:
            make_proxies(pid)
        analyse_motion(pid)
        auto_faces(pid)
        auto_describe(pid)
        _set(pid, state="idle", stage="Done")
    except Exception as e:
        _set(pid, state="error", stage="Error", message=str(e))


def locate(pid):
    _set(pid, stage="Looking up locations")
    with LOCK:
        cat = load_cat(pid)
    todo = {cid: parse_gps(c["meta"].get("gps")) for cid, c in cat["clips"].items()
            if c["meta"].get("gps") and not (c.get("location") or {}).get("name")}
    for cid, ll in todo.items():
        if not ll:
            continue
        name = place_name(*ll)
        with LOCK:
            cat = load_cat(pid)
            cat["clips"][cid]["location"] = {"lat": ll[0], "lon": ll[1], "name": name}
            save_cat(pid, cat)


def make_proxies(pid):
    with LOCK:
        cat = load_cat(pid)
    todo = [c for c in cat["clips"].values() if not c.get("proxy") and os.path.exists(clip_path(c))]
    if not todo:
        return
    enc = proxy_encoder()
    _set(pid, stage="Making preview proxies", done=0, total=len(todo))
    d = pdir(pid)
    for i, c in enumerate(todo, 1):
        rel = f"proxies/{c['id']}.mp4"
        dst = os.path.join(d, rel)
        try:
            if not os.path.exists(dst):
                ffmpeg("-i", clip_path(c), "-vf", "scale=-2:540", *enc, "-pix_fmt", "yuv420p", "-c:a", "aac",
                       "-b:a", "96k", "-movflags", "+faststart", dst + ".part.mp4")
                os.replace(dst + ".part.mp4", dst)
            with LOCK:
                cat = load_cat(pid)
                cat["clips"][c["id"]]["proxy"] = rel
                save_cat(pid, cat)
        except Exception:
            pass
        _set(pid, done=i)


def analyse_motion(pid):
    """Measure camera movement for clips that don't have it yet (needs OpenCV; skipped if absent)."""
    try:
        import motion
        motion.cv2  # noqa: B018 - raises if OpenCV isn't installed
    except Exception:
        return
    with LOCK:
        cat = load_cat(pid)
    todo = [c for c in cat["clips"].values() if not c.get("motion")]
    if not todo:
        return
    _set(pid, stage="Measuring camera movement", done=0, total=len(todo))
    for i, c in enumerate(todo, 1):
        src = os.path.join(pdir(pid), c["proxy"]) if c.get("proxy") else clip_path(c)
        try:
            res = motion.analyse(src)
        except Exception as e:
            res = {"movement": "", "detail": f"couldn't analyse: {e}", "confidence": 0}
        with LOCK:
            cat = load_cat(pid)
            cat["clips"][c["id"]]["motion"] = res
            save_cat(pid, cat)
        _set(pid, done=i)


def auto_faces(pid):
    """If people recognition is on, look for faces in clips that haven't been checked yet."""
    import export
    st = {**export.DEFAULT_SETTINGS, **load_lib().get("settings", {})}
    if not st.get("faces_on"):
        return
    try:
        import faces
        if faces.cv_ok() and faces.models_ready():
            faces.scan(pid, nested=True)
    except Exception as e:  # noqa: BLE001
        _set(pid, message=f"People recognition: {e}")


def _faces_rematch():
    """After people change (photo, rename, merge, delete), re-tag clips in the background."""
    def run():
        try:
            import faces
            if faces.cv_ok() and faces.models_ready():
                faces.match_all()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, daemon=True).start()


def auto_describe(pid):
    """If 'describe new clips automatically' is on, describe clips that don't have a description yet."""
    import export
    st = {**export.DEFAULT_SETTINGS, **load_lib().get("settings", {})}
    if not st.get("ai_auto") or st.get("ai_engine") not in ("local", "claude"):
        return
    try:
        import tagger
        todo = tagger.pick_clips(pid, "untagged")
        if todo:
            tagger.describe(pid, todo, st["ai_engine"], nested=True)
    except Exception as e:  # noqa: BLE001
        _set(pid, message=f"AI describer: {e}")


def start_ingest(pid):
    threading.Thread(target=ingest, args=(pid,), daemon=True).start()


# ---------- verified move ----------
def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_move(src, dst):
    """Same drive: instant rename. Different drive: copy, verify size and checksum,
    and only then remove the original."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        raise FileExistsError(f"{os.path.basename(dst)} already exists in the destination")
    try:
        if os.stat(src).st_dev == os.stat(os.path.dirname(dst)).st_dev:
            os.rename(src, dst)
            return "renamed"
    except OSError:
        pass
    part = dst + ".part"
    shutil.copy2(src, part)
    if os.path.getsize(part) != os.path.getsize(src) or _sha(part) != _sha(src):
        os.remove(part)
        raise IOError(f"Verification failed for {os.path.basename(src)} - original left in place")
    os.replace(part, dst)
    os.remove(src)
    return "copied+verified"


def send(pid):
    """New shoot: move approved clips to 01_Selects (keeping sub-folders). Existing footage: move nothing.
    Either way, write the Resolve files (script, CSV, backup timeline) to the project's _Resolve Import folder."""
    import export
    p = project(pid)
    archive = p.get("mode") == "archive"
    dest = p.get("destination")
    if not archive and not dest:
        return {"error": "Choose a destination folder first."}
    if dest and not archive:
        os.makedirs(dest, exist_ok=True)
    batch = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    moved, errors = [], []
    with LOCK:
        cat = load_cat(pid)
    approved = [c for c in cat["clips"].values() if c["review"]["status"] == "approved"]
    selected = [c for c in cat["clips"].values() if c["review"]["status"] in SELECTED]
    if not selected:
        return {"error": "No approved clips yet."}
    for c in ([] if archive else approved):
        if c.get("moved_to") and os.path.exists(c["moved_to"]):
            with LOCK:
                cat = load_cat(pid)
                cat["clips"][c["id"]]["review"]["status"] = "ingested"
                save_cat(pid, cat)
            continue
        src = clip_path(c)
        root = next((s for s in sorted(p["sources"], key=len, reverse=True)
                     if os.path.abspath(c["src_dir"]).startswith(os.path.abspath(s))), None)
        rel = os.path.relpath(c["src_dir"], root) if root else "."
        dst = os.path.join(dest, rel, c["file"]) if rel not in (".", "") else os.path.join(dest, c["file"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            how = safe_move(src, dst)
        except Exception as e:
            errors.append(str(e))
            continue
        moved.append({"id": c["id"], "from": src, "to": dst, "how": how})
        with LOCK:
            cat = load_cat(pid)
            cl = cat["clips"][c["id"]]
            cl["moved_to"] = dst
            cl["ingested"] = batch
            cl["review"]["status"] = "ingested"
            save_cat(pid, cat)
    with LOCK:
        cat = load_cat(pid)
    lib = load_lib()
    out = resolve_dir_of(p)
    files = export.write_resolve_files(cat, out, p, batch, lib["people"], lib["settings"], folder_name="")
    with LOCK:
        lib = load_lib()
        pr = next(x for x in lib["projects"] if x["id"] == pid)
        pr.setdefault("sends", []).append({"batch": batch, "moved": moved, "files": files, "errors": errors})
        if files.get("timeline_name"):
            pr.setdefault("timeline_name", files["timeline_name"])  # keep the same draft timeline even if the project is renamed
        if archive:
            cat = load_cat(pid)
            for c in selected:
                cat["clips"][c["id"]]["sent"] = batch
            save_cat(pid, cat)
        save_lib(lib)
    return {"batch": batch, "moved": len(moved), "approved": len(selected), "errors": errors,
            "files": files, "folder": out, "archive": archive}


def test_export(pid):
    """Write the Resolve files without moving anything, pointing at the clips where they are now."""
    import export
    p = project(pid)
    with LOCK:
        cat = load_cat(pid)
    if not any(c["review"]["status"] in SELECTED for c in cat["clips"].values()):
        return {"error": "No approved clips yet."}
    batch = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = os.path.join(pdir(pid), "exports", "test " + batch)
    lib = load_lib()
    files = export.write_resolve_files(cat, out, p, batch, lib["people"], lib["settings"], folder_name="")
    return {"folder": out, "files": files}


def undo_last(pid):
    """Put the clips from the most recent send back where they came from."""
    with LOCK:
        lib = load_lib()
        pr = next(x for x in lib["projects"] if x["id"] == pid)
        sends = [s for s in pr.get("sends", []) if s["moved"] and not s.get("undone")]
    if not sends:
        return {"error": "Nothing to undo."}
    last = sends[-1]
    back, errors = 0, []
    for m in last["moved"]:
        try:
            if os.path.exists(m["to"]):
                safe_move(m["to"], m["from"])
            with LOCK:
                cat = load_cat(pid)
                cl = cat["clips"][m["id"]]
                cl.pop("moved_to", None)
                cl.pop("ingested", None)
                if cl["review"]["status"] == "ingested":
                    cl["review"]["status"] = "approved"
                save_cat(pid, cat)
            back += 1
        except Exception as e:
            errors.append(str(e))
    with LOCK:
        lib = load_lib()
        pr = next(x for x in lib["projects"] if x["id"] == pid)
        for s in pr["sends"]:
            if s["batch"] == last["batch"]:
                s["undone"] = True
        save_lib(lib)
    return {"returned": back, "errors": errors}


def move_back(pid, cid, status="approved"):
    """Return one ingested clip from the destination to its source folder, then set its status."""
    with LOCK:
        cat = load_cat(pid)
    c = cat["clips"].get(cid)
    if not c or not c.get("moved_to"):
        return {"error": "That clip hasn't been moved."}
    src = os.path.join(c["src_dir"], c["file"])
    try:
        if os.path.exists(c["moved_to"]):
            safe_move(c["moved_to"], src)
        elif not os.path.exists(src):
            return {"error": f"Can't find {c['file']} in the destination or the source folder."}
    except Exception as e:
        return {"error": str(e)}
    with LOCK:
        cat = load_cat(pid)
        cl = cat["clips"][cid]
        cl.pop("moved_to", None)
        cl.pop("ingested", None)
        cl["review"]["status"] = status if status in ("approved", "rejected", "unreviewed") else "approved"
        save_cat(pid, cat)
        lib = load_lib()
        pr = next(x for x in lib["projects"] if x["id"] == pid)
        pr.setdefault("returns", []).append({"clip": cid, "at": datetime.now().isoformat(timespec="seconds"),
                                             "to": src, "status": cl["review"]["status"]})
        save_lib(lib)
    return {"ok": True, "review": cl["review"]}


# ---------- people (shared by all projects) ----------
def people_dir():
    return os.path.join(LIB, "people")


def split_name(name):
    """Best guess from one typed name: last word is the family name. Fix name order with family_first."""
    parts = (name or "").split()
    if len(parts) < 2:
        return {"given": name.strip(), "family": "", "family_first": False}
    return {"given": " ".join(parts[:-1]), "family": parts[-1], "family_first": False}


def display_name(p):
    g, f = (p.get("given") or "").strip(), (p.get("family") or "").strip()
    if not (g or f):
        return p.get("name", "")
    return " ".join(x for x in ([f, g] if p.get("family_first") else [g, f]) if x)


def _ensure_parts(p):
    if "given" not in p and "family" not in p:
        p.update(split_name(p.get("name", "")))
    return p


def people_action(data):
    act = data.get("action")
    with LOCK:
        lib = load_lib()
        ppl = lib["people"]
        byid = {p["id"]: p for p in ppl}
        if act == "add":
            name = (data.get("name") or "").strip()
            if not name:
                return {"error": "A person needs a name."}
            hit = next((p for p in ppl if p["name"].lower() == name.lower()), None)
            if hit:
                return {"person": hit, "people": ppl}
            base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "person"
            pid, n = base, 2
            while pid in byid:
                pid, n = f"{base}-{n}", n + 1
            person = {"id": pid, "name": name, "face": "", "added": datetime.now().isoformat(timespec="seconds"), **split_name(name)}
            ppl.append(person)
            save_lib(lib)
            return {"person": person, "people": ppl}
        p = byid.get(data.get("id"))
        if not p:
            return {"error": "No such person."}
        if act == "rename":
            name = (data.get("name") or "").strip()
            if name:
                p["name"] = name
                p.update(split_name(name))
        elif act == "parts":
            _ensure_parts(p)
            ff = bool(data.get("family_first", p.get("family_first")))
            if ff != bool(p.get("family_first")) and "given" not in data and "family" not in data:
                # flipping the order keeps the name as written: swap which part is the family name
                words = display_name(p).split()
                if len(words) >= 2:
                    p["given"], p["family"] = (" ".join(words[1:]), words[0]) if ff else (" ".join(words[:-1]), words[-1])
            else:
                p["given"] = (data.get("given", p.get("given")) or "").strip()
                p["family"] = (data.get("family", p.get("family")) or "").strip()
            p["family_first"] = ff
            p["name"] = display_name(p) or p["name"]
        elif act == "delete":
            ppl.remove(p)
            _swap_person(p["id"], None)
            try:
                import faces
                faces.forget_person(p["id"])
            except Exception:  # noqa: BLE001
                pass
        elif act == "merge":
            into = byid.get(data.get("into"))
            if not into or into is p:
                return {"error": "Choose a different person to merge into."}
            if not into.get("face") and p.get("face"):
                into["face"] = p["face"]
            ppl.remove(p)
            _swap_person(p["id"], into["id"])
            try:
                import faces
                faces.merge_person(p["id"], into["id"])
            except Exception:  # noqa: BLE001
                pass
        save_lib(lib)
        if act in ("delete", "merge"):
            _faces_rematch()
        return {"people": ppl}


def _swap_person(old, new):
    for pr in load_lib()["projects"]:
        cat = load_cat(pr["id"])
        changed = False
        for c in cat["clips"].values():
            ids = c["review"].get("people") or []
            if old in ids:
                ids = [new if i == old else i for i in ids if (new if i == old else i)]
                c["review"]["people"] = list(dict.fromkeys(ids))
                changed = True
            gone = c["review"].get("people_removed") or []
            if old in gone:
                c["review"]["people_removed"] = [x for x in gone if x != old] + ([new] if new else [])
                changed = True
            aip = c.get("ai", {}).get("people") or []
            if any(x["id"] == old for x in aip):
                c["ai"]["people"] = [x for x in aip if x["id"] != old]
                changed = True
        if changed:
            save_cat(pr["id"], cat)


def save_face(pid_person, jpeg_bytes):
    os.makedirs(people_dir(), exist_ok=True)
    with LOCK:
        lib = load_lib()
        p = next((x for x in lib["people"] if x["id"] == pid_person), None)
        if not p:
            return {"error": "No such person."}
        name = f"{p['id']}.jpg"
        with open(os.path.join(people_dir(), name), "wb") as f:
            f.write(jpeg_bytes)
        p["face"] = f"{name}?v={int(time.time())}"
        save_lib(lib)
    _faces_rematch()
    return {"person": p}


def people_face_options(person_id, limit=24):
    """Faces recognition has matched to this person, across all projects, clearest first."""
    out = []
    for pr in load_lib()["projects"]:
        for c in load_cat(pr["id"])["clips"].values():
            for f in c.get("faces") or []:
                if f.get("match") == person_id and f.get("crop"):
                    out.append({"project": pr["id"], "project_name": pr["name"], "clip": c["id"], "crop": f["crop"],
                                "size": f.get("size", 0), "sim": f.get("sim", 0)})
    out.sort(key=lambda x: -(x["size"] * (0.5 + x["sim"])))
    return out[:limit]


def people_photo_from_face(person_id, project_id, crop):
    """Use a face crop found by recognition as the person's photo."""
    if not project(project_id) or not crop.startswith("faces/") or "/" in crop[6:] or ".." in crop:
        return {"error": "That face can't be found."}
    path = os.path.join(pdir(project_id), crop)
    if not os.path.isfile(path):
        return {"error": "That face can't be found."}
    with open(path, "rb") as f:
        return save_face(person_id, f.read())


def people_usage():
    """Which clips each person appears in, across every project."""
    out = {}
    for pr in load_lib()["projects"]:
        for c in load_cat(pr["id"])["clips"].values():
            removed = set(c["review"].get("people_removed") or [])
            conf = {x["id"]: x.get("conf") for x in c.get("ai", {}).get("people") or []}
            for pid in dict.fromkeys((c["review"].get("people") or []) + list(conf)):
                if pid in removed:
                    continue
                mid = c["thumbs"][len(c["thumbs"]) // 2]["src"] if c.get("thumbs") else ""
                sure = pid in (c["review"].get("people") or [])
                out.setdefault(pid, []).append({"project": pr["id"], "project_name": pr["name"], "clip": c["id"],
                                                "file": c["file"], "thumb": mid, "status": c["review"]["status"],
                                                "conf": None if sure else conf.get(pid)})
    return out


def update_settings(data):
    with LOCK:
        lib = load_lib()
        for k in ("conf_green", "conf_red"):
            if k in data:
                lib["settings"][k] = max(0, min(100, int(data[k])))
        for k in ("tl_bin", "tl_template"):
            if k in data:
                lib["settings"][k] = str(data[k]).strip().strip("/")
        if data.get("ai_engine") in ("off", "local", "claude"):
            lib["settings"]["ai_engine"] = data["ai_engine"]
        if "ai_auto" in data:
            lib["settings"]["ai_auto"] = bool(data["ai_auto"])
        if "faces_on" in data:
            lib["settings"]["faces_on"] = bool(data["faces_on"])
        for k in ("claude_model", "ollama_model", "ollama_url"):
            if k in data:
                lib["settings"][k] = str(data[k]).strip()
        save_lib(lib)
        return lib["settings"]
