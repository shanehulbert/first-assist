#!/usr/bin/env python3
"""Ingest Sorter - merge tagger output into a project.

A tagger (local model, cloud model, or a person) produces JSON like:

    {"source": "...", "clips": {"<clip id>": {"description": "...", "scene": "...", "title": "...",
      "keywords": [...], "shot_type": "MS", "angle": "Eye level", "suggested_in": 1.5, "suggested_out": 6.0}}}

Only the fields present are updated. Keywords are mapped through the
Controlled Vocabulary (synonyms become their main term). Your own review
edits are stored separately and never overwritten.

    python3 apply_tags.py <project-id> tags.json [--motion motion.json]
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import library as L  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("project")
ap.add_argument("tags", nargs="?")
ap.add_argument("--motion")
ap.add_argument("--library")
a = ap.parse_args()
if a.library:
    L.LIB = os.path.abspath(a.library)

tags = json.load(open(a.tags)) if a.tags else {"clips": {}}
motion = json.load(open(a.motion)) if a.motion else {}
drop = {k.lower() for k in tags.get("drop_keywords", [])} | L.blocked_terms()
cmap = L.canon_map()
n = 0
with L.LOCK:
    cat = L.load_cat(a.project)
    for cid, c in cat["clips"].items():
        t = tags["clips"].get(cid, {})
        ai = c["ai"]
        for k in ("description", "scene", "title", "shot_type", "angle", "suggested_in", "suggested_out"):
            if k in t:
                ai[k] = t[k]
        if "keywords" in t:
            ai["keywords"] = t["keywords"]
        if drop:
            ai["keywords"] = [k for k in ai.get("keywords", []) if k.lower() not in drop]
            if c["review"].get("keywords"):
                c["review"]["keywords"] = [k for k in c["review"]["keywords"] if k.lower() not in drop]
        ai["keywords"] = L.normalize(ai.get("keywords", []), cmap)
        if t and tags.get("source"):
            ai["source"] = tags["source"]
        if cid in motion:
            c["motion"] = motion[cid]
        n += bool(t) or cid in motion
    L.save_cat(a.project, cat)
print(f"Updated {n} clips")
