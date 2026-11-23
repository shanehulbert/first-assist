"""First Assist - people recognition (runs entirely on this Mac).

Finds faces in a few frames of each clip (OpenCV YuNet), turns each face into a
"faceprint" (OpenCV SFace, 128 numbers), then:
  - matches faceprints against your People (from their face crops and from faces you've named)
    and tags clips with a confidence, like camera movement;
  - groups faces that match nobody, so you can name a group once or reject it;
  - remembers rejected faces and ignores them from then on.

Nothing is sent anywhere. Faceprints are stored in the library folder:
  library/people/faceprints.json          references for each person + rejected faces
  library/projects/<id>/faces.json        faceprints found in that project's clips
  library/projects/<id>/faces/*.jpg       face crops shown in the app
"""
import json, os, shutil, subprocess, threading, urllib.request

import library as L
from ingest import find_tool

MODELS = {
    "detector": ("face_detection_yunet_2023mar.onnx",
                 "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
                 200_000),
    "recogniser": ("face_recognition_sface_2021dec.onnx",
                   "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
                   30_000_000),
}
FRAME_MAX = 1280         # longest side of frames we look at
MIN_FACE = 36            # px at FRAME_MAX; smaller faces are too unreliable
DET_SCORE = 0.85
SAME_IN_CLIP = 0.50      # cosine: two detections in one clip are the same person
MATCH_MIN = 0.33         # cosine: at or above this a face is tagged as a known person
GROUP_SIM = 0.42         # cosine: unknown faces this similar are grouped together
IGNORE_SIM = 0.45        # cosine: this close to a rejected face -> ignored
MAX_REFS = 24
DL = {"state": "idle", "message": ""}
_cv = {}


# ---------- models ----------
def models_dir():
    return os.path.join(L.LIB, "models")


def model_path(kind):
    return os.path.join(models_dir(), MODELS[kind][0])


def models_ready():
    return all(os.path.exists(model_path(k)) and os.path.getsize(model_path(k)) >= MODELS[k][2] for k in MODELS)


def cv_ok():
    try:
        import cv2  # noqa: F401
        return hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF")
    except Exception:  # noqa: BLE001
        return False


def download_models():
    if DL["state"] == "running":
        return {"ok": True}

    def run():
        DL.update(state="running", message="Downloading face models…")
        try:
            os.makedirs(models_dir(), exist_ok=True)
            for kind, (name, url, minimum) in MODELS.items():
                dst = model_path(kind)
                if os.path.exists(dst) and os.path.getsize(dst) >= minimum:
                    continue
                tmp = dst + ".part"
                with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f)
                if os.path.getsize(tmp) < minimum:
                    raise RuntimeError(f"{name} looks incomplete ({os.path.getsize(tmp)} bytes)")
                os.replace(tmp, dst)
            _cv.clear()
            _models()  # prove they load
            DL.update(state="done", message="Face models ready.")
        except Exception as e:  # noqa: BLE001
            DL.update(state="error", message=f"Couldn't download the face models: {e}")
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def _models():
    if "det" not in _cv:
        import cv2
        _cv["det"] = cv2.FaceDetectorYN.create(model_path("detector"), "", (320, 320), DET_SCORE, 0.3, 5000)
        _cv["rec"] = cv2.FaceRecognizerSF.create(model_path("recogniser"), "")
        _cv["lock"] = threading.Lock()
    return _cv["det"], _cv["rec"], _cv["lock"]


# ---------- maths ----------
def _np():
    import numpy as np
    return np


def _unit(v):
    np = _np()
    v = np.asarray(v, dtype="float32").reshape(-1)
    n = float(np.linalg.norm(v)) or 1.0
    return v / n


def _sim(a, b):
    return float(_np().dot(a, b))


def conf_of(sim):
    """Map cosine similarity to the 0-100 confidence the app shows (green/orange/red thresholds in Settings)."""
    return int(round(max(0.0, min(1.0, (sim - 0.25) / 0.35)) * 100))


# ---------- storage ----------
def _refs_file():
    return os.path.join(L.people_dir(), "faceprints.json")


def load_refs():
    try:
        with open(_refs_file()) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("people", {})
    d.setdefault("ignored", [])
    d.setdefault("from_photo", {})  # person id -> photo version the reference was made from
    return d


def save_refs(d):
    L._write(_refs_file(), d)


def _pfile(pid):
    return os.path.join(L.pdir(pid), "faces.json")


def load_pfaces(pid):
    try:
        with open(_pfile(pid)) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("emb", {})
    d.setdefault("groups", {})
    return d


def save_pfaces(pid, d):
    L._write(_pfile(pid), d)


# ---------- detection ----------
def _frames(pid, clip):
    """Frames as BGR arrays, from the original (sharper faces) or the preview proxy."""
    import cv2
    np = _np()
    src = L.clip_path(clip)
    if not os.path.exists(src) and clip.get("proxy"):
        src = os.path.join(L.pdir(pid), clip["proxy"])
    dur = float(clip["meta"].get("duration") or 0) or 1.0
    n = max(3, min(10, round(dur / 2)))
    out = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        cmd = [find_tool("ffmpeg") or "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
               "-vf", f"scale=w={FRAME_MAX}:h={FRAME_MAX}:force_original_aspect_ratio=decrease",
               "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "-"]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and r.stdout:
            img = cv2.imdecode(np.frombuffer(r.stdout, dtype="uint8"), cv2.IMREAD_COLOR)
            if img is not None:
                out.append((round(t, 2), img))
    return out


def _detect(img):
    det, rec, lock = _models()
    h, w = img.shape[:2]
    with lock:
        det.setInputSize((w, h))
        _, faces = det.detect(img)
    found = []
    for f in faces if faces is not None else []:
        x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
        if score < DET_SCORE or min(fw, fh) < MIN_FACE:
            continue
        with lock:
            aligned = rec.alignCrop(img, f)
            feat = rec.feature(aligned)
        found.append({"box": [x, y, fw, fh], "score": score, "emb": _unit(feat)})
    return found


def _crop(img, box, dst):
    import cv2
    x, y, w, h = box
    pad = 0.35 * max(w, h)
    H, W = img.shape[:2]
    x0, y0 = int(max(0, x - pad)), int(max(0, y - pad))
    x1, y1 = int(min(W, x + w + pad)), int(min(H, y + h + pad))
    face = img[y0:y1, x0:x1]
    if face.size == 0:
        return False
    side = 160
    fh, fw = face.shape[:2]
    s = side / max(fh, fw)
    face = cv2.resize(face, (max(1, int(fw * s)), max(1, int(fh * s))), interpolation=cv2.INTER_AREA)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    return cv2.imwrite(dst, face, [cv2.IMWRITE_JPEG_QUALITY, 88])


def scan_clip(pid, clip):
    """Find the distinct faces in one clip. Returns (faces for the catalogue, {face id: faceprint})."""
    found = []
    for t, img in _frames(pid, clip):
        for f in _detect(img):
            f["t"], f["img"] = t, img
            found.append(f)
    # merge detections of the same person within this clip
    people = []
    for f in sorted(found, key=lambda f: -f["score"] * f["box"][2] * f["box"][3]):
        for p in people:
            if _sim(f["emb"], p["emb"]) >= SAME_IN_CLIP:
                p["members"].append(f)
                p["emb"] = _unit(sum(m["emb"] for m in p["members"]))
                break
        else:
            people.append({"emb": f["emb"], "best": f, "members": [f]})
    faces, embs = [], {}
    for i, p in enumerate(people):
        fid = f"{clip['id']}__{i}"
        b = p["best"]
        rel = f"faces/{fid}.jpg"
        _crop(b["img"], b["box"], os.path.join(L.pdir(pid), rel))
        faces.append({"f": fid, "t": b["t"], "crop": rel, "size": int(min(b["box"][2], b["box"][3])),
                      "seen": len(p["members"])})
        embs[fid] = [round(float(x), 5) for x in p["emb"]]
    return faces, embs


def photo_faceprint(path):
    """Faceprint from a person's cropped photo (the Crop face tool)."""
    import cv2
    img = cv2.imread(path)
    if img is None:
        return None
    for scale in (1, 2, 3):  # small crops: upscale so the detector can see the face
        im = img if scale == 1 else cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        pad = cv2.copyMakeBorder(im, 40, 40, 40, 40, cv2.BORDER_REPLICATE)
        det, rec, lock = _models()
        h, w = pad.shape[:2]
        with lock:
            det.setInputSize((w, h))
            _, faces = det.detect(pad)
        if faces is not None and len(faces):
            f = max(faces, key=lambda f: f[2] * f[3])
            with lock:
                return _unit(rec.feature(rec.alignCrop(pad, f)))
    return None


# ---------- references ----------
def refresh_photo_refs(refs=None):
    """Make sure every person with a face photo has a faceprint from it (redone when the photo changes)."""
    refs = refs or load_refs()
    changed = False
    for p in L.load_lib().get("people", []):
        face = p.get("face") or ""
        if not face or refs["from_photo"].get(p["id"]) == face:
            continue
        fp = photo_faceprint(os.path.join(L.people_dir(), face.split("?")[0]))
        refs["from_photo"][p["id"]] = face
        if fp is not None:
            lst = refs["people"].setdefault(p["id"], [])
            lst.insert(0, [round(float(x), 5) for x in fp])
            del lst[MAX_REFS:]
        changed = True
    if changed:
        save_refs(refs)
    return refs


def add_refs(person_id, prints, refs=None):
    refs = refs or load_refs()
    lst = refs["people"].setdefault(person_id, [])
    for p in prints:
        lst.append([round(float(x), 5) for x in p])
    if len(lst) > MAX_REFS:
        photo = lst[:1] if refs["from_photo"].get(person_id) else []
        lst[:] = photo + lst[-(MAX_REFS - len(photo)):]
    save_refs(refs)
    return refs


# ---------- matching and grouping ----------
def match_project(pid, refs=None):
    """Tag clips with known people, mark rejected faces, and group the rest."""
    np = _np()
    refs = refs or load_refs()
    known = [(person, _unit(e)) for person, lst in refs["people"].items() for e in lst]
    ignored = [_unit(e) for e in refs["ignored"]]
    lib_people = {p["id"] for p in L.load_lib().get("people", [])}
    known = [(p, e) for p, e in known if p in lib_people]
    pf = load_pfaces(pid)
    emb = {k: _unit(v) for k, v in pf["emb"].items()}
    with L.LOCK:
        cat = L.load_cat(pid)
        unknown = []
        for c in cat["clips"].values():
            best_by_person = {}
            for f in c.get("faces") or []:
                e = emb.get(f["f"])
                f.pop("match", None), f.pop("sim", None), f.pop("ignored", None), f.pop("group", None)
                if e is None:
                    continue
                if ignored and max(_sim(e, x) for x in ignored) >= IGNORE_SIM:
                    f["ignored"] = True
                    continue
                if known:
                    sims = {}
                    for person, r in known:
                        s = _sim(e, r)
                        if s > sims.get(person, -1):
                            sims[person] = s
                    person, s = max(sims.items(), key=lambda kv: kv[1])
                    if s >= MATCH_MIN:
                        f["match"], f["sim"] = person, round(s, 3)
                        if s > best_by_person.get(person, -1):
                            best_by_person[person] = s
                        continue
                unknown.append((c["id"], f, e))
            c["ai"]["people"] = [{"id": p, "conf": conf_of(s)} for p, s in sorted(best_by_person.items(), key=lambda kv: -kv[1])]
        # group unknown faces across the project (largest, clearest faces seed the groups)
        groups = []
        for cid, f, e in sorted(unknown, key=lambda u: -u[1].get("size", 0)):
            for g in groups:
                if _sim(e, g["centroid"]) >= GROUP_SIM:
                    g["members"].append((cid, f, e))
                    g["centroid"] = _unit(np.sum([m[2] for m in g["members"]], axis=0))
                    break
            else:
                groups.append({"centroid": e, "members": [(cid, f, e)]})
        pf["groups"] = {}
        for g in groups:
            gid = "g_" + g["members"][0][1]["f"]
            for _, f, _ in g["members"]:
                f["group"] = gid
            pf["groups"][gid] = {"faces": [m[1]["f"] for m in g["members"]],
                                 "clips": sorted({m[0] for m in g["members"]})}
        L.save_cat(pid, cat)
    save_pfaces(pid, pf)
    return pf["groups"]


def match_all():
    refs = refresh_photo_refs()
    for p in L.load_lib()["projects"]:
        try:
            match_project(p["id"], refs)
        except Exception:  # noqa: BLE001
            pass


# ---------- running ----------
def scan(pid, nested=False, redo=False):
    """Find faces in clips that haven't been looked at yet, then match and group."""
    if not (cv_ok() and models_ready()):
        if not nested:
            L._set(pid, state="error", message="People recognition needs the face models. See Settings › People recognition.")
        return
    if not nested:
        if L.STATUS.get(pid, {}).get("state") == "running":
            return
        L._set(pid, state="running", failed=0, message="")
    with L.LOCK:
        cat = L.load_cat(pid)
    todo = [c for c in cat["clips"].values() if redo or "faces" not in c]
    L._set(pid, stage="Finding people", done=0, total=len(todo))
    pf = load_pfaces(pid)
    for i, c in enumerate(todo, 1):
        try:
            faces, embs = scan_clip(pid, c)
        except Exception as e:  # noqa: BLE001
            faces, embs = [], {}
            L._set(pid, message=f"{c['file']}: couldn't look for faces ({e})")
        pf = load_pfaces(pid)
        for old in [k for k in pf["emb"] if k.startswith(c["id"] + "__")]:
            pf["emb"].pop(old)
        pf["emb"].update(embs)
        save_pfaces(pid, pf)
        with L.LOCK:
            cat = L.load_cat(pid)
            if c["id"] in cat["clips"]:
                cat["clips"][c["id"]]["faces"] = faces
                L.save_cat(pid, cat)
        L._set(pid, done=i)
    groups = match_project(pid, refresh_photo_refs())
    tagged = sum(bool(c["ai"].get("people")) for c in L.load_cat(pid)["clips"].values())
    msg = f"People: {tagged} clip{'s' if tagged != 1 else ''} with known people"
    msg += f" · {len(groups)} unnamed face{'s' if len(groups) != 1 else ''} to name in the People tab" if groups else ""
    L._set(pid, message=msg) if nested else L._set(pid, state="idle", stage="Done", message=msg)


def start_scan(pid, redo=False):
    if not cv_ok():
        return {"error": "OpenCV isn't installed (pip3 install --user opencv-python-headless)."}
    if not models_ready():
        return {"error": "Download the face models first, in Settings › People recognition."}
    if L.STATUS.get(pid, {}).get("state") == "running":
        return {"error": "This project is busy. Try again when the current job finishes."}
    threading.Thread(target=scan, args=(pid,), kwargs={"redo": redo}, daemon=True).start()
    return {"ok": True}


# ---------- your decisions ----------
def groups_for_ui(pid):
    pf = load_pfaces(pid)
    cat = L.load_cat(pid)
    byf = {f["f"]: (c["id"], f) for c in cat["clips"].values() for f in c.get("faces") or []}
    out = []
    for gid, g in pf["groups"].items():
        faces = [byf[f] for f in g["faces"] if f in byf]
        if not faces:
            continue
        faces.sort(key=lambda cf: -cf[1].get("size", 0))
        out.append({"id": gid, "clips": g["clips"], "crops": [f["crop"] for _, f in faces[:6]]})
    out.sort(key=lambda g: -len(g["clips"]))
    return out


def name_group(pid, gid, person_id):
    pf = load_pfaces(pid)
    g = pf["groups"].get(gid)
    if not g:
        return {"error": "That group has changed. Reload the People tab."}
    refs = add_refs(person_id, [pf["emb"][f] for f in g["faces"][:10] if f in pf["emb"]])
    # give the person a photo if they don't have one yet
    with L.LOCK:
        lib = L.load_lib()
        p = next((x for x in lib["people"] if x["id"] == person_id), None)
        if p and not p.get("face"):
            cat = L.load_cat(pid)
            best = max((f for c in cat["clips"].values() for f in c.get("faces") or [] if f["f"] in g["faces"]),
                       key=lambda f: f.get("size", 0), default=None)
            if best:
                os.makedirs(L.people_dir(), exist_ok=True)
                shutil.copy(os.path.join(L.pdir(pid), best["crop"]), os.path.join(L.people_dir(), f"{person_id}.jpg"))
                p["face"] = f"{person_id}.jpg?v=named"
                refs["from_photo"][person_id] = p["face"]
                save_refs(refs)
                L.save_lib(lib)
    match_all()
    return {"ok": True}


def reject_group(pid, gid):
    pf = load_pfaces(pid)
    g = pf["groups"].get(gid)
    if not g:
        return {"error": "That group has changed. Reload the People tab."}
    refs = load_refs()
    prints = [pf["emb"][f] for f in g["faces"] if f in pf["emb"]]
    if prints:
        refs["ignored"].append([round(float(x), 5) for x in _unit(sum(_unit(p) for p in prints))])
        refs["ignored"].extend(prints[:4])
        save_refs(refs)
    match_project(pid, refs)
    return {"ok": True}


def confirm(pid, cid, person_id):
    """You confirmed a suggested person on a clip: tag it for sure and learn from that face."""
    with L.LOCK:
        cat = L.load_cat(pid)
        c = cat["clips"].get(cid)
        if not c:
            return {"error": "No such clip."}
        rv = c["review"]
        rv["people"] = list(dict.fromkeys((rv.get("people") or []) + [person_id]))
        rv["people_removed"] = [x for x in rv.get("people_removed") or [] if x != person_id]
        fids = [f["f"] for f in c.get("faces") or [] if f.get("match") == person_id]
        L.save_cat(pid, cat)
    pf = load_pfaces(pid)
    if fids:
        add_refs(person_id, [pf["emb"][f] for f in fids if f in pf["emb"]])
        match_project(pid)
    return {"review": rv}


def forget_person(person_id):
    refs = load_refs()
    refs["people"].pop(person_id, None)
    refs["from_photo"].pop(person_id, None)
    save_refs(refs)


def merge_person(old, new):
    refs = load_refs()
    if old in refs["people"]:
        refs["people"].setdefault(new, []).extend(refs["people"].pop(old))
        del refs["people"][new][:-MAX_REFS]
    refs["from_photo"].pop(old, None)
    save_refs(refs)
