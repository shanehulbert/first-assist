#!/usr/bin/env python3
"""First Assist - (re)install the Resolve script for a project's last Send.

    python3 install_script.py <project-id>

Writes "First Assist - Apply Last Send.lua" into Resolve's Scripts folder
(Workspace > Scripts) for the clips in that project's most recent Send.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import library as L  # noqa: E402
import export as X   # noqa: E402

pid = sys.argv[1] if len(sys.argv) > 1 else None
lib = L.load_lib()
projects = lib["projects"]
p = next((x for x in projects if x["id"] == pid), None)
if not p:
    sys.exit("Project not found. Choose one of: " + ", ".join(x["id"] for x in projects))
sends = p.get("sends") or []
if not sends:
    sys.exit("That project hasn't been sent to Resolve yet.")
batch = sends[-1]["batch"]
cat = L.load_cat(pid)
clips = sorted(cat["clips"].values(), key=lambda c: (c["meta"].get("recorded", ""), c["file"]))
ctx = X.context(p, clips, lib.get("people"), lib.get("settings"))
approved = [c for c in clips if c["review"]["status"] in ("approved", "ingested")]
path = X.install_resolve_script(X.resolve_script(approved, ctx, X.timeline_name(p), p["name"]))
print(f"Installed for {len(approved)} clips (Send {batch}):\n{path}" if path else "Couldn't write to Resolve's Scripts folder.")
