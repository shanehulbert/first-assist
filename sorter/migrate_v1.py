#!/usr/bin/env python3
"""One-off: bring a v1 catalogue (catalogues/<name>) into the v2 library as a project.

    python3 migrate_v1.py "/path/to/catalogues/<name>" "Project name" [--terms a,b,c]
"""
import argparse, json, os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import library as L  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("old")
ap.add_argument("name")
ap.add_argument("--terms", default="")
ap.add_argument("--library")
a = ap.parse_args()
if a.library:
    L.LIB = os.path.abspath(a.library)

old = json.load(open(os.path.join(a.old, "catalogue.json")))
src = old["source_path"]
pid, _ = L.create_project(a.name, src, "")
d = L.pdir(pid)
clips = {}
for cid, c in old["clips"].items():
    c["src_dir"] = src
    c["review"].setdefault("role", "")
    c.pop("eff", None)
    for t in c.get("thumbs", []):
        shutil.copy2(os.path.join(a.old, t["src"]), os.path.join(d, t["src"]))
    if c.get("proxy") and os.path.exists(os.path.join(a.old, c["proxy"])):
        shutil.copy2(os.path.join(a.old, c["proxy"]), os.path.join(d, c["proxy"]))
    clips[cid] = c
L.save_cat(pid, {"clips": clips})
if a.terms:
    L.set_terms(L.load_lib()["terms"] + a.terms.split(","))
print("Migrated", len(clips), "clips into project", pid)
