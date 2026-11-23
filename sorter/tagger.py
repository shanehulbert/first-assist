"""First Assist - AI describer.

Looks at a few frames from each clip and fills in its Scene, Title, Description,
Keywords, Shot type, Angle and a suggested in/out. Two engines:

  local   a vision model running on this Mac through Ollama (free, private, offline)
  claude  Claude through the Anthropic API (better, small cost, needs an API key and internet)

Results go into each clip's "ai" values. Anything you have typed yourself (the
"review" values) is never overwritten.
"""
import base64, json, os, subprocess, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import library as L
from ingest import find_tool

CLAUDE_MODELS = {  # US$ per million tokens (prompts under 100k tokens)
    "claude-haiku-5-5": {"label": "Claude Haiku 5.5 (fast, cheapest)", "in": 0.10, "out": 0.50},
    "claude-sonnet-5-5": {"label": "Claude Sonnet 5.5 (best descriptions)", "in": 2.00, "out": 10.00},
}
CLAUDE_URL = "https://api.anthropic.com/v1"
FRAMES = {"claude": 6, "local": 4}
FRAME_SIZE = 768
EST_TOKENS = {"in": 4200, "out": 450}  # per clip, for the cost estimate before a run
VISION_HINTS = ("vl", "vision", "llava", "gemma3", "gemma4", "minicpm-v", "moondream", "qwen3.5", "qwen3.6", "mistral-small3")


# ---------- settings and key ----------
def settings():
    import export
    return {**export.DEFAULT_SETTINGS, **L.load_lib().get("settings", {})}


def _secrets_file():
    return os.path.join(L.LIB, "secrets.json")


def get_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"].strip()
    try:
        with open(_secrets_file()) as f:
            return (json.load(f).get("anthropic_key") or "").strip()
    except (OSError, ValueError):
        return ""


def set_key(key):
    key = (key or "").strip()
    path = _secrets_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"anthropic_key": key}, f)
    os.chmod(path, 0o600)
    return bool(key)


def check_key(key=None):
    """Ask the API to list models: proves the key works and costs nothing."""
    key = key or get_key()
    if not key:
        return {"ok": False, "message": "No API key saved."}
    try:
        _http("GET", f"{CLAUDE_URL}/models?limit=1", headers=_claude_headers(key), timeout=20)
        return {"ok": True, "message": "Key works."}
    except ApiError as e:
        return {"ok": False, "message": e.friendly}


# ---------- http ----------
class ApiError(Exception):
    def __init__(self, status, body, friendly):
        super().__init__(friendly)
        self.status, self.body, self.friendly = status, body, friendly


def _http(method, url, body=None, headers=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(raw).get("error", {})
            msg = msg.get("message", raw) if isinstance(msg, dict) else str(msg)
        except ValueError:
            msg = raw
        friendly = msg
        if e.code == 401:
            friendly = "Claude didn't accept the API key. Check it in Settings › AI describer."
        elif e.code == 403:
            friendly = "This API key isn't allowed to do that (" + msg + ")."
        elif "credit balance" in msg.lower():
            friendly = "Your Claude API credit has run out. Add credit at platform.claude.com › Billing."
        elif e.code == 429:
            friendly = "Claude is rate-limiting requests. Try again in a minute."
        elif e.code >= 500:
            friendly = "Claude's servers had a problem (" + str(e.code) + "). Try again shortly."
        raise ApiError(e.code, raw, friendly)
    except urllib.error.URLError as e:
        raise ApiError(0, str(e.reason), f"Couldn't connect ({e.reason}).")


def _claude_headers(key):
    return {"x-api-key": key, "anthropic-version": "2023-06-01"}


# ---------- local models (Ollama) ----------
def ollama_status(url=None):
    url = (url or settings().get("ollama_url") or "http://localhost:11434").rstrip("/")
    try:
        tags = _http("GET", f"{url}/api/tags", timeout=4)
    except ApiError:
        return {"running": False, "models": [], "url": url}
    models = []
    for m in tags.get("models", []):
        name = m.get("name") or m.get("model")
        vision = None
        try:
            info = _http("POST", f"{url}/api/show", {"model": name}, timeout=8)
            if "capabilities" in info:
                vision = "vision" in info["capabilities"]
        except ApiError:
            pass
        if vision is None:
            vision = any(h in name.lower() for h in VISION_HINTS)
        size = m.get("size") or 0
        models.append({"name": name, "vision": vision, "gb": round(size / 1e9, 1)})
    return {"running": True, "models": models, "url": url}


# ---------- frames ----------
def frames(pid, clip, n):
    """n evenly spaced JPEG frames (bytes) plus their times, from the proxy if there is one."""
    src = os.path.join(L.pdir(pid), clip["proxy"]) if clip.get("proxy") else L.clip_path(clip)
    if not os.path.exists(src):
        src = L.clip_path(clip)
    dur = float(clip["meta"].get("duration") or 0) or 1.0
    out = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        cmd = [find_tool("ffmpeg") or "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
               "-vf", f"scale=w={FRAME_SIZE}:h={FRAME_SIZE}:force_original_aspect_ratio=decrease",
               "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and r.stdout:
            out.append((round(t, 2), r.stdout))
    if not out:
        raise RuntimeError("couldn't read frames from the clip")
    return out


# ---------- prompt ----------
SHOT_HELP = ("EST establishing, EWS extreme wide, WS wide (whole subject with surroundings), MWS medium wide (knees up), "
             "MS medium (waist up), MCU medium close-up (chest up), CU close-up (face or object fills frame), "
             "ECU extreme close-up (detail), OTS over the shoulder, Insert (cutaway of an object or detail)")


def schema():
    return {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "scene": {"type": "string"},
            "title": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "shot_type": {"type": "string", "enum": L.SHOT_TYPES},
            "angle": {"type": "string", "enum": L.ANGLES},
            "suggested_in": {"type": "number"},
            "suggested_out": {"type": "number"},
        },
        "required": ["description", "scene", "title", "keywords", "shot_type", "angle", "suggested_in", "suggested_out"],
        "additionalProperties": False,
    }


def prompt(project, clip, times, vocab, scenes):
    m = clip["meta"]
    loc = (clip.get("location") or {}).get("name")
    lines = [
        "You are an assistant editor logging footage for a documentary video editor.",
        f"The images are {len(times)} frames taken evenly through ONE video clip, at "
        + ", ".join(f"{t}s" for t in times) + f" of {round(float(m.get('duration') or 0), 1)}s.",
        f"Project: {project.get('name', '')}." + (f" Filmed near {loc}." if loc else "")
        + (f" Recorded {m.get('recorded', '')[:16].replace('T', ' ')}." if m.get("recorded") else ""),
        "",
        "Describe the clip as a whole and fill in every field:",
        "- description: one or two plain, factual sentences about what is seen and happening. Don't guess anyone's name or identity.",
        "- title: 2 to 5 words naming what this clip is, specific enough to tell it apart from similar clips (e.g. \"Lute and handpan duo\").",
        "- scene: a broad group label that many clips from the same event, activity or place will share (2 to 4 words, "
        "e.g. \"Stage music\", \"Exhibition opening\"). It is NOT a description of this clip; the title does that. "
        + ("Use one of these existing scenes unless the clip clearly belongs to a different event or place: "
           + "; ".join(scenes[:40]) + "." if scenes else "Keep it broad so other clips can share it."),
        "- keywords: 4 to 10 lowercase keywords for searching (subjects, actions, setting, mood). "
        + ("Prefer these existing terms whenever they fit, spelled exactly as shown, and only add new ones when nothing fits: "
           + ", ".join(vocab[:300]) + "." if vocab else ""),
        f"- shot_type: the dominant framing. {SHOT_HELP}.",
        "- angle: camera height relative to the subject.",
        "- suggested_in / suggested_out: the best usable section in seconds, skipping shaky starts, "
        "camera bumps or dead time at either end. Use 0 and the full duration if all of it is usable.",
    ]
    return "\n".join(lines)


# ---------- engines ----------
def ask_claude(images, text, model, key, fmt=None):
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(b).decode()}} for b in images]
    content.append({"type": "text", "text": text})
    body = {"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": content}],
            "output_config": {"format": {"type": "json_schema", "schema": fmt or schema()}}}
    for attempt in range(4):
        try:
            r = _http("POST", f"{CLAUDE_URL}/messages", body, _claude_headers(key))
            break
        except ApiError as e:
            if e.status in (429, 500, 502, 503, 529) and attempt < 3:
                time.sleep(4 * (attempt + 1))
                continue
            raise
    txt = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
    u = r.get("usage", {})
    price = CLAUDE_MODELS.get(model, CLAUDE_MODELS["claude-haiku-5-5"])
    cost = (u.get("input_tokens", 0) * price["in"] + u.get("output_tokens", 0) * price["out"]) / 1e6
    return _parse(txt), cost


def ask_local(images, text, model, url, fmt=None):
    msg = {"role": "user", "content": text}
    if images:
        msg["images"] = [base64.b64encode(b).decode() for b in images]
    body = {"model": model, "stream": False, "format": fmt or schema(), "options": {"temperature": 0.2}, "messages": [msg]}
    try:
        r = _http("POST", f"{url.rstrip('/')}/api/chat", body, timeout=600)
    except ApiError as e:
        if e.status == 0:
            raise ApiError(0, e.body, "The local model isn't reachable. Is Ollama running?")
        if e.status == 404:
            raise ApiError(404, e.body, f"Ollama doesn't have the model “{model}”. Pick one in Settings › AI describer.")
        raise
    return _parse(r.get("message", {}).get("content", "")), 0.0


def _parse(txt):
    txt = (txt or "").strip()
    try:
        return json.loads(txt)
    except ValueError:
        a, b = txt.find("{"), txt.rfind("}")
        if a >= 0 and b > a:
            return json.loads(txt[a:b + 1])
        raise RuntimeError("the model didn't return usable JSON")


def _pick(value, allowed):
    v = str(value or "").strip().lower()
    return next((a for a in allowed if a.lower() == v), "")


def clean(res, clip):
    dur = float(clip["meta"].get("duration") or 0)
    i = max(0.0, min(dur, float(res.get("suggested_in") or 0)))
    o = max(0.0, min(dur, float(res.get("suggested_out") or dur)))
    if o - i < 0.5:
        i, o = 0.0, dur
    kws = [str(k).strip().lower() for k in res.get("keywords") or [] if str(k).strip()]
    return {"description": str(res.get("description", "")).strip(),
            "scene": str(res.get("scene", "")).strip()[:60],
            "title": str(res.get("title", "")).strip()[:60],
            "keywords": [k for k in L.normalize(list(dict.fromkeys(kws))) if k.lower() not in L.blocked_terms()][:12],
            "shot_type": _pick(res.get("shot_type"), L.SHOT_TYPES),
            "angle": _pick(res.get("angle"), L.ANGLES),
            "suggested_in": round(i, 2), "suggested_out": round(o, 2)}


# ---------- running ----------
def engine_label(engine, st=None):
    st = st or settings()
    if engine == "claude":
        m = st.get("claude_model") or "claude-haiku-5-5"
        return CLAUDE_MODELS.get(m, {"label": m})["label"].split(" (")[0]
    return "Local: " + (st.get("ollama_model") or "?")


def estimate(n, model=None):
    price = CLAUDE_MODELS.get(model or settings().get("claude_model") or "claude-haiku-5-5")
    return round(n * (EST_TOKENS["in"] * price["in"] + EST_TOKENS["out"] * price["out"]) / 1e6, 4)


def pick_clips(pid, scope, ids=None):
    cat = L.load_cat(pid)
    cs = cat["clips"]
    if scope == "ids":
        return [cid for cid in (ids or []) if cid in cs]
    if scope == "all":
        return list(cs)
    return [cid for cid, c in cs.items() if not (c.get("ai") or {}).get("description")]  # untagged


def describe(pid, ids, engine, nested=False):
    """Describe the given clips. Runs inside a background thread."""
    st = settings()
    if not nested:
        if L.STATUS.get(pid, {}).get("state") == "running":
            return
        L._set(pid, state="running", failed=0, message="")
    label = engine_label(engine, st)
    L._set(pid, stage=f"Describing clips ({label})", done=0, total=len(ids), cost=0.0, cancel=False)
    if engine == "claude":
        key = get_key()
        if not key:
            L._set(pid, state="error" if not nested else "running", message="No Claude API key yet. Add one in Settings › AI describer.")
            return
        model = st.get("claude_model") or "claude-haiku-5-5"
    else:
        url = st.get("ollama_url") or "http://localhost:11434"
        model = st.get("ollama_model") or ""
        if not model:
            L._set(pid, state="error" if not nested else "running", message="Choose a local model in Settings › AI describer.")
            return
    p = L.project(pid) or {}
    lib = L.load_lib()
    vocab = [v["term"] for v in lib.get("vocab", []) if v.get("pinned")] + \
            [v["term"] for v in lib.get("vocab", []) if not v.get("pinned")]
    totals = {"done": 0, "cost": 0.0, "failed": 0, "fatal": ""}
    lock = threading.Lock()

    def one(cid):
        if totals["fatal"] or L.STATUS.get(pid, {}).get("cancel"):
            return
        name = cid
        try:
            with L.LOCK:
                cat = L.load_cat(pid)
            clip = cat["clips"].get(cid)
            if not clip:
                return
            name = clip.get("file", cid)
            scenes = sorted({(c["review"].get("scene") or c["ai"].get("scene") or "") for c in cat["clips"].values()} - {""})
            fr = frames(pid, clip, FRAMES[engine])
            text = prompt(p, clip, [t for t, _ in fr], vocab, scenes)
            imgs = [b for _, b in fr]
            res, cost = ask_claude(imgs, text, model, key) if engine == "claude" else ask_local(imgs, text, model, url)
            res = clean(res, clip)
            with L.LOCK:
                cat = L.load_cat(pid)
                c = cat["clips"].get(cid)
                if c:
                    c["ai"].update(res)
                    c["ai"]["source"] = label
                    c["ai"]["cost"] = round(cost or 0, 5)
                    c["ai"]["described"] = datetime.now().isoformat(timespec="seconds")
                    L.save_cat(pid, cat)
            with lock:
                totals["cost"] += cost
        except ApiError as e:
            with lock:
                totals["failed"] += 1
                if e.status in (0, 401, 403, 404) or "credit" in e.friendly.lower():
                    totals["fatal"] = e.friendly
                else:
                    L._set(pid, message=f"{name}: {e.friendly}")
        except Exception as e:  # noqa: BLE001 - one bad clip shouldn't stop the run
            with lock:
                totals["failed"] += 1
                L._set(pid, message=f"{name}: {e}")
        finally:
            with lock:
                totals["done"] += 1
                L._set(pid, done=totals["done"], cost=round(totals["cost"], 4), failed=totals["failed"])

    workers = 3 if engine == "claude" else 1
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, ids))

    ok = totals["done"] - totals["failed"]
    tidy = ""
    if ok > 1 and not totals["fatal"] and not L.STATUS.get(pid, {}).get("cancel"):
        L._set(pid, stage="Tidying scenes", done=0, total=0)
        try:
            a, b = tidy_scenes(pid, engine, st)
            if a != b:
                tidy = f" · scenes tidied {a} → {b}"
        except Exception as e:  # noqa: BLE001 - descriptions are already saved
            tidy = f" · couldn't tidy scenes ({getattr(e, 'friendly', e)})"
    msg = f"Described {ok} clip{'s' if ok != 1 else ''} with {label}" + tidy
    if engine == "claude":
        msg += f" · cost about US${totals['cost']:.3f}"
    if totals["failed"]:
        msg += f" · {totals['failed']} failed"
    if L.STATUS.get(pid, {}).get("cancel"):
        msg += " · stopped"
    if totals["fatal"]:
        msg = totals["fatal"] + (f" ({ok} described before that)" if ok else "")
    if nested:
        L._set(pid, message=msg)
    else:
        L._set(pid, state="error" if totals["fatal"] else "idle", stage="Done", message=msg, cancel=False)


TIDY_SCHEMA = {
    "type": "object",
    "properties": {"mapping": {"type": "array", "items": {
        "type": "object", "properties": {"label": {"type": "string"}, "scene": {"type": "string"}},
        "required": ["label", "scene"], "additionalProperties": False}}},
    "required": ["mapping"], "additionalProperties": False,
}


def tidy_scenes(pid, engine, st=None):
    """Merge the AI's scene labels into a small shared set, looking at the whole project at once.
    Scenes you typed yourself are kept exactly and used as anchors. Returns (before, after) counts."""
    st = st or settings()
    with L.LOCK:
        cat = L.load_cat(pid)
    clips = sorted(cat["clips"].values(), key=lambda c: (c["meta"].get("recorded", ""), c["file"]))
    days = {d: f"D{i + 1}" for i, d in enumerate(sorted({(c["meta"].get("recorded") or "")[:10] for c in clips} - {""}))}
    fixed = sorted({c["review"].get("scene") for c in clips if c["review"].get("scene")})
    groups = {}
    for c in clips:
        if c["review"].get("scene") or not c["ai"].get("scene"):
            continue
        g = groups.setdefault(c["ai"]["scene"], {"n": 0, "times": [], "titles": []})
        g["n"] += 1
        rec = c["meta"].get("recorded") or ""
        if rec:
            g["times"].append(f"{days.get(rec[:10], '')} {rec[11:16]}")
        if len(g["titles"]) < 3 and c["ai"].get("title"):
            g["titles"].append(c["ai"]["title"])
    if len(groups) < 2:
        return len(groups), len(groups)
    n = sum(g["n"] for g in groups.values())
    target = min(len(groups) + len(fixed), max(3, -(-(n + len(fixed)) // 4)))  # about 1 per 4 clips, at least 3
    lines = [f'- "{k}": {g["n"]} clip{"s" if g["n"] != 1 else ""}'
             + (f', filmed {g["times"][0]}' + (f' to {g["times"][-1]}' if g["times"][-1] != g["times"][0] else "") if g["times"] else "")
             + (f', e.g. {"; ".join(g["titles"])}' if g["titles"] else "")
             for k, g in groups.items()]
    text = "\n".join([
        "You are an assistant editor tidying the scene labels of a video project so related clips share one scene.",
        f"Project: {(L.project(pid) or {}).get('name', '')}.",
        ("Scenes the editor set by hand (keep these names exactly, and reuse them when a label belongs with them): "
         + "; ".join(fixed) + ".") if fixed else "",
        "Current AI scene labels, with how many clips use each, when they were filmed, and example clip titles:",
        *lines,
        "",
        f"Merge labels that are the same event, activity or place into one shared scene. Aim for roughly {target} scenes in total: "
        "fewer if the clips really are one event, more if they are clearly different events or places. "
        "Clips filmed in the same place within a few minutes of each other usually belong together. Use short, broad names "
        "(2 to 4 words, e.g. \"Stage music\", \"Exhibition opening\"). Return one entry for EVERY label above, giving the scene it becomes.",
    ])
    if engine == "claude":
        res, _ = ask_claude([], text, st.get("claude_model") or "claude-haiku-5-5", get_key(), fmt=TIDY_SCHEMA)
    else:
        res, _ = ask_local([], text, st.get("ollama_model"), st.get("ollama_url") or "http://localhost:11434", fmt=TIDY_SCHEMA)
    fixed_ci = {f.lower(): f for f in fixed}
    mapping = {}
    for m in res.get("mapping") or []:
        old, new = str(m.get("label", "")).strip(), str(m.get("scene", "")).strip()[:60]
        if old in groups and new:
            mapping[old] = fixed_ci.get(new.lower(), new)
    # keep identical names identical regardless of capitalisation
    canon = {}
    for v in mapping.values():
        canon.setdefault(v.lower(), v)
    with L.LOCK:
        cat = L.load_cat(pid)
        for c in cat["clips"].values():
            s_ = c["ai"].get("scene")
            if not c["review"].get("scene") and s_ in mapping:
                c["ai"]["scene"] = canon[mapping[s_].lower()]
        L.save_cat(pid, cat)
        after = {c["ai"].get("scene") for c in cat["clips"].values() if not c["review"].get("scene") and c["ai"].get("scene")}
    return len(groups), len(after)


def start_tidy(pid, engine=None):
    engine = engine or settings().get("ai_engine")
    if engine not in ("claude", "local"):
        return {"error": "Choose Local or Claude first, in Settings › AI describer."}
    if L.STATUS.get(pid, {}).get("state") == "running":
        return {"error": "This project is busy. Try again when the current job finishes."}

    def run():
        L._set(pid, state="running", stage="Tidying scenes", done=0, total=0, message="", failed=0)
        try:
            a, b = tidy_scenes(pid, engine)
            L._set(pid, state="idle", stage="Done", message=f"Scenes tidied: {a} → {b}")
        except Exception as e:  # noqa: BLE001
            L._set(pid, state="error", stage="Done", message=f"Couldn't tidy scenes: {getattr(e, 'friendly', e)}")
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def start(pid, scope="untagged", ids=None, engine=None):
    engine = engine or settings().get("ai_engine") or "off"
    if engine not in ("claude", "local"):
        return {"error": "Choose Local or Claude first, in Settings › AI describer."}
    if L.STATUS.get(pid, {}).get("state") == "running":
        return {"error": "This project is busy. Try again when the current job finishes."}
    todo = pick_clips(pid, scope, ids)
    if not todo:
        return {"error": "No clips to describe." + (" All clips already have descriptions; choose “All clips” to redo them." if scope == "untagged" else "")}
    threading.Thread(target=describe, args=(pid, todo, engine), daemon=True).start()
    return {"ok": True, "count": len(todo), "engine": engine}


def cancel(pid):
    L._set(pid, cancel=True)
    return {"ok": True}
