"""Ingest Sorter - numbers for the Dashboard tab (one project, or the whole library)."""
import os
from collections import Counter

import export
import library as L


def _du(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _top(counter, n):
    return [{"name": k, "n": v} for k, v in counter.most_common(n)]


def build(pids=None):
    lib = L.load_lib()
    settings = {**export.DEFAULT_SETTINGS, **lib.get("settings", {})}
    projects = [p for p in lib["projects"] if pids is None or p["id"] in pids]
    out = {"scope": "all" if pids is None else "project", "clips": 0, "runtime": 0.0, "footage_mb": 0.0,
           "status": Counter(), "todo": Counter(), "pipeline": Counter(), "days": {"shot": Counter(), "added": Counter(), "reviewed": Counter()},
           "usage": Counter(), "ai": Counter(), "ai_cost": 0.0, "ai_cost_untracked": 0, "keywords": Counter(), "scenes": Counter(),
           "people": Counter(), "cameras": Counter(), "shots": Counter(), "projects": [], "faces_on": bool(settings.get("faces_on")),
           "last_sent": ""}
    for p in projects:
        pid = p["id"]
        cat = L.load_cat(pid)
        clips = list(cat["clips"].values())
        ctx = export.context(p, clips, lib.get("people"), lib.get("settings"))
        archive = p.get("mode") == "archive"
        st = Counter()
        ptodo = Counter()
        runtime = 0.0
        for c in clips:
            rv, ai, m = c.get("review", {}), c.get("ai", {}), c.get("meta", {})
            e = export.effective(c, ctx)
            status = e["status"]
            sel = status in L.SELECTED
            # in an Archive project nothing moves, so "sent to Resolve" is what "ingested" is for a Shoot project
            st["ingested" if archive and status == "approved" and c.get("sent") else status] += 1
            runtime += m.get("duration") or 0
            out["footage_mb"] += m.get("size_mb") or 0
            # still to do
            if status == "unreviewed":
                ptodo["review"] += 1
            mo = c.get("motion") or {}
            if status == "approved" and not rv.get("movement") and mo.get("movement") \
                    and (mo.get("confidence") or 0) * 100 < settings["conf_green"]:
                ptodo["movement"] += 1
            if not e["description"]:
                ptodo["nodesc"] += 1
            if (status == "approved" and not archive) or (archive and sel and not c.get("sent")):
                ptodo["unsent"] += 1
            # pipeline
            out["pipeline"]["found"] += 1
            out["pipeline"]["previews"] += 1 if c.get("proxy") else 0
            out["pipeline"]["described"] += 1 if ai.get("description") or ai.get("described") else 0
            out["pipeline"]["people"] += 1 if "faces" in c else 0
            out["pipeline"]["reviewed"] += 1 if status != "unreviewed" else 0
            # day by day
            for key, val in (("shot", m.get("recorded")), ("added", c.get("added")), ("reviewed", rv.get("reviewed_at"))):
                if val:
                    out["days"][key][str(val)[:10]] += 1
            # AI describer
            if ai.get("description") or ai.get("described"):
                src = (ai.get("source") or "").lower()
                kind = "claude" if "claude" in src else "local"
                out["ai"][kind] += 1
                if kind == "claude":
                    if "cost" in ai:
                        out["ai_cost"] += ai["cost"] or 0
                    else:
                        out["ai_cost_untracked"] += 1
            # composition
            out["cameras"][m.get("camera") or "Unknown camera"] += (m.get("duration") or 0)
            for name in e["people"]:
                out["people"][name] += 1
            if sel:
                for k in e["keywords"]:
                    out["keywords"][k] += 1
                if e["scene"]:
                    out["scenes"][e["scene"]] += 1
                out["shots"][e["shot_type"] or "Not set"] += 1
        try:
            import faces
            ptodo["faces"] += len([g for g in (faces.load_pfaces(pid).get("groups") or {}).values() if g.get("faces")])
        except Exception:  # noqa: BLE001
            pass
        out["todo"].update(ptodo)
        d = L.pdir(pid)
        use = {"Previews": _du(os.path.join(d, "proxies")), "Thumbnails": _du(os.path.join(d, "thumbs")),
               "Face data": _du(os.path.join(d, "faces")) + _du(os.path.join(d, "faces.json")),
               "Catalogue": _du(os.path.join(d, "catalogue.json")) + _du(os.path.join(d, "exports"))}
        for k, v in use.items():
            out["usage"][k] += v
        sends = [s for s in p.get("sends") or [] if not s.get("undone")]
        last = sends[-1]["batch"][:10] if sends else ""
        out["last_sent"] = max(out["last_sent"], last)
        out["clips"] += len(clips)
        out["runtime"] += runtime
        out["status"].update(st)
        out["projects"].append({"id": pid, "name": p["name"], "mode": p.get("mode") or "", "clips": len(clips), "runtime": runtime,
                                "status": dict(st), "todo": dict(ptodo), "app_bytes": sum(use.values()), "last_sent": last})
    out["shared_bytes"] = _du(os.path.join(L.LIB, "models")) + _du(L.people_dir())
    cams = out.pop("cameras")
    out["cameras"] = [{"name": k, "n": round(v)} for k, v in cams.most_common(6)]
    for k, n in (("keywords", 14), ("scenes", 10), ("people", 8), ("shots", 8)):
        out[k] = _top(out[k], n)
    for k in ("status", "todo", "pipeline", "usage", "ai"):
        out[k] = dict(out[k])
    out["days"] = {k: dict(v) for k, v in out["days"].items()}
    out["ai_cost"] = round(out["ai_cost"], 4)
    return out
