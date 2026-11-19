#!/usr/bin/env python3
"""Ingest Sorter - local review app (v2).

    python3 server.py [--port 8765] [--no-browser]

Then open http://localhost:8765. Everything stays on this machine.
"""
import argparse, json, mimetypes, os, re, subprocess, sys, threading, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import library as L  # noqa: E402

REVIEW_KEYS = {"meta", "people_removed", "status", "rating", "in", "out", "notes", "keywords", "description", "scene", "title", "role",
               "shot_type", "angle", "movement", "clip_name", "people"}


def mac_choose_folder(prompt):
    if sys.platform != "darwin":
        return None
    # Ask Finder to show the dialog: a background process can't show one itself.
    # (The first time, macOS asks whether Terminal may control Finder - click OK.)
    script = ['-e', 'tell application "Finder" to activate',
              '-e', f'tell application "Finder" to set f to choose folder with prompt "{prompt}"',
              '-e', 'POSIX path of (f as alias)']
    r = subprocess.run(["osascript", *script], capture_output=True, text=True)
    if r.returncode != 0:
        if "-128" in r.stderr:  # user pressed Cancel
            return ""
        print("Folder picker problem:", r.stderr.strip())
        return None
    return r.stdout.strip().rstrip("/") or None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not path or not os.path.isfile(path):
            return self.send_error(404)
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                else:
                    start = size - int(m.group(2))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        length = end - start + 1
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):
        p = unquote(urlparse(self.path).path)
        if p in ("/", "/index.html"):
            return self.send_file(os.path.join(HERE, "static", "index.html"))
        if p == "/api/version":
            from ingest import tools
            return self.send_json({"app": "footage-sorter", "version": 5, "release": "1.10", "tools": tools()})
        if p == "/api/library":
            lib = L.load_lib()
            for pr in lib["projects"]:
                cat = L.load_cat(pr["id"])
                cs = list(cat["clips"].values())
                arch = pr.get("mode") == "archive"  # nothing moves in an Archive project: "sent" counts as ingested
                st = ["ingested" if arch and c["review"]["status"] == "approved" and c.get("sent") else c["review"]["status"] for c in cs]
                pr["counts"] = {"clips": len(cs), **{k: st.count(k) for k in ("approved", "ingested", "rejected", "unreviewed")}}
                pr["status"] = L.STATUS.get(pr["id"], {"state": "idle"})
                pr["icon_url"] = L.icon_url(pr, cs)
            import export
            lib["options"] = {"meta_fields": export.META_FIELDS, "roles": L.ROLES, "shot_types": L.SHOT_TYPES, "angles": L.ANGLES,
                              "movements": L.MOVEMENTS, "tokens": export.TOKENS,
                              "default_tokens": export.DEFAULT_TOKENS, "default_sep": export.DEFAULT_SEP}
            lib["settings"] = {**export.DEFAULT_SETTINGS, **lib.get("settings", {})}
            return self.send_json(lib)
        if p == "/api/dashboard":
            import dashboard
            from urllib.parse import parse_qs
            pid = (parse_qs(urlparse(self.path).query).get("pid") or [""])[0]
            return self.send_json(dashboard.build([pid] if pid else None))
        if p == "/api/vocab/usage":  # how often each keyword is used, per project and overall
            use = {}
            for pr in L.load_lib()["projects"]:
                for c in L.load_cat(pr["id"])["clips"].values():
                    kws = c["review"]["keywords"] if c["review"].get("keywords") is not None else c["ai"].get("keywords", [])
                    for k in kws or []:
                        u = use.setdefault(k.lower(), {"keyword": k, "total": 0, "projects": {}})
                        u["total"] += 1
                        u["projects"][pr["id"]] = u["projects"].get(pr["id"], 0) + 1
            return self.send_json(use)
        if p == "/api/ai":
            import tagger
            st = tagger.settings()
            return self.send_json({"has_key": bool(tagger.get_key()), "key_from_env": bool(os.environ.get("ANTHROPIC_API_KEY")),
                                   "claude_models": tagger.CLAUDE_MODELS, "ollama": tagger.ollama_status(st.get("ollama_url"))})
        if p == "/api/faces":
            import faces
            return self.send_json({"cv": faces.cv_ok(), "models": faces.models_ready(), "download": faces.DL})
        if p == "/api/people/usage":
            return self.send_json(L.people_usage())
        m = re.match(r"^/people/([^/]+)$", p)
        if m:
            return self.send_file(os.path.join(L.people_dir(), os.path.basename(m.group(1))))
        m = re.match(r"^/api/p/([^/]+)/(catalogue|status)$", p)
        if m:
            pid, what = m.groups()
            if what == "status":
                return self.send_json(L.STATUS.get(pid, {"state": "idle"}))
            cat = L.load_cat(pid)
            for c in cat["clips"].values():  # flag clips whose file can't be found right now
                c["offline"] = not os.path.exists(L.clip_path(c))
            return self.send_json(cat)
        m = re.match(r"^/p/([^/]+)/icon\.jpg$", p)
        if m:
            return self.send_file(L.icon_file(os.path.basename(m.group(1))))
        m = re.match(r"^/p/([^/]+)/(thumbs|proxies|faces)/([^/]+)$", p)
        if m:
            pid, kind, name = m.groups()
            return self.send_file(os.path.join(L.pdir(pid), kind, os.path.basename(name)))
        m = re.match(r"^/p/([^/]+)/original/([^/]+)$", p)
        if m:
            pid, cid = m.groups()
            clip = L.load_cat(pid)["clips"].get(cid)
            return self.send_file(L.clip_path(clip) if clip else None)
        self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or b"{}")

        if p == "/api/choose-folder":
            return self.send_json({"path": mac_choose_folder(data.get("prompt", "Choose a folder"))})
        if p == "/api/reveal":
            path = data.get("path", "")
            if sys.platform == "darwin" and os.path.exists(path):
                subprocess.run(["open", "-R", path])
                return self.send_json({"ok": True})
            return self.send_json({"ok": False})
        if p == "/api/people":
            return self.send_json(L.people_action(data))
        if p == "/api/people/photo-options":
            return self.send_json({"faces": L.people_face_options(data.get("id"))})
        if p == "/api/people/photo":
            return self.send_json(L.people_photo_from_face(data.get("id"), data.get("project", ""), data.get("crop", "")))
        if p == "/api/people/face":
            import base64
            img = (data.get("image") or "").split(",")[-1]
            return self.send_json(L.save_face(data.get("id"), base64.b64decode(img)))
        if p == "/api/faces/download":
            import faces
            return self.send_json(faces.download_models())
        if p == "/api/ai/key":
            import tagger
            if data.get("clear"):
                tagger.set_key("")
                return self.send_json({"ok": True, "has_key": False})
            key = (data.get("key") or "").strip()
            res = tagger.check_key(key) if key else tagger.check_key()
            if key and res["ok"]:
                tagger.set_key(key)
            return self.send_json({**res, "has_key": bool(tagger.get_key())})
        if p == "/api/settings":
            import export
            return self.send_json({"settings": {**export.DEFAULT_SETTINGS, **L.update_settings(data)}})
        if p == "/api/vocab":
            return self.send_json({"vocab": L.vocab_action(data)})
        if p == "/api/projects/check":
            return self.send_json(L.check_folder(data.get("root")))
        if p == "/api/projects" and data.get("mode") in ("shoot", "archive"):
            name, mode = (data.get("name") or "").strip(), data["mode"]
            if not name:
                return self.send_json({"error": "Give the project a name."}, 400)
            if mode == "shoot" and not os.path.isdir(data.get("root") or ""):
                return self.send_json({"error": "Choose the project folder."}, 400)
            srcs = [s for s in data.get("sources") or [] if s]
            if mode == "archive" and not srcs:
                return self.send_json({"error": "Add at least one footage folder."}, 400)
            bad = [s for s in srcs if not os.path.isdir(s)]
            if bad:
                return self.send_json({"error": "Can't find: " + "; ".join(bad)}, 400)
            try:
                pid, moved = L.create_project(name, mode=mode, root=data.get("root"), sources=srcs,
                                              edit_dir=(data.get("edit_dir") or "").strip() or None, move_loose=bool(data.get("move_loose")),
                                              meta=data.get("meta") if isinstance(data.get("meta"), dict) else None)
            except Exception as e:  # noqa: BLE001
                return self.send_json({"error": f"Couldn't set up the folders: {e}"}, 400)
            L.start_ingest(pid)
            return self.send_json({"id": pid, "moved": moved})
        if p == "/api/projects":
            name = (data.get("name") or "").strip()
            src, dst = (data.get("source") or "").strip(), (data.get("destination") or "").strip()
            if not name or not src:
                return self.send_json({"error": "A project needs a name and a source folder."}, 400)
            if not os.path.isdir(src):
                return self.send_json({"error": f"Can't find the source folder: {src}"}, 400)
            pid, _ = L.create_project(name, src, dst)
            L.start_ingest(pid)
            return self.send_json({"id": pid})

        m = re.match(r"^/api/p/([^/]+)/([\w-]+)(?:/(.+))?$", p)
        if not m:
            return self.send_error(404)
        pid, action, arg = m.groups()
        if not L.project(pid):
            return self.send_json({"error": "No such project"}, 404)
        if action == "clip":
            cid = unquote(arg or "")
            with L.LOCK:
                cat = L.load_cat(pid)
                clip = cat["clips"].get(cid)
                if not clip:
                    return self.send_json({"error": "no such clip"}, 404)
                upd = {k: v for k, v in data.items() if k in REVIEW_KEYS}
                if upd.get("status") and upd["status"] != clip["review"].get("status"):
                    from datetime import datetime
                    clip["review"]["reviewed_at"] = datetime.now().isoformat(timespec="seconds") if upd["status"] != "unreviewed" else ""
                if clip.get("moved_to") and upd.get("status", "ingested") != "ingested":
                    return self.send_json({"error": "This clip is already in the destination. Move it back first."}, 409)
                if not clip.get("moved_to") and upd.get("status") == "ingested":
                    upd.pop("status")
                if upd.get("keywords") is not None:
                    upd["keywords"] = L.normalize(upd["keywords"])
                clip["review"].update(upd)
                if clip["review"]["status"] in L.SELECTED and not clip["review"].get("role"):
                    clip["review"]["role"] = "A-roll"
                L.save_cat(pid, cat)
            return self.send_json(clip["review"])
        if action == "icon":  # use the frame in the player as the project icon, or clear it
            if data.get("clear"):
                return self.send_json(L.clear_icon(pid))
            import base64
            try:
                img = base64.b64decode((data.get("image") or "").split(",")[-1])
            except ValueError:
                img = b""
            r = L.set_icon(pid, data.get("clip", ""), img, data.get("t"))
            return self.send_json(r, 400 if r.get("error") else 200)
        if action == "mode":
            r = L.set_mode(pid, data.get("mode"), data.get("root"))
            return self.send_json(r, 400 if r.get("error") else 200)
        if action == "update":
            if data.get("add_source") and not os.path.isdir(data["add_source"]):
                return self.send_json({"error": "Can't find that folder."}, 400)
            pr = L.update_project(pid, **{k: v for k, v in data.items() if k in ("name", "destination", "add_source", "client", "location", "name_tokens", "name_sep", "mode", "resolve_dir", "meta")})
            if data.get("add_source"):
                L.start_ingest(pid)
            return self.send_json(pr)
        if action == "meta-copy":
            values = {k: str(v).strip() for k, v in (data.get("values") or {}).items() if str(v).strip()}
            targets = set(data.get("targets") or [])
            n = 0
            with L.LOCK:
                cat = L.load_cat(pid)
                for cid in targets:
                    c = cat["clips"].get(cid)
                    if c:
                        m = c["review"].get("meta") or {}
                        m.update(values)
                        c["review"]["meta"] = m
                        n += 1
                L.save_cat(pid, cat)
            return self.send_json({"ok": True, "clips": n, "fields": len(values)})
        if action == "faces":
            import faces
            if arg == "scan":
                return self.send_json(faces.start_scan(pid, bool(data.get("redo"))))
            if arg == "groups":
                return self.send_json({"groups": faces.groups_for_ui(pid)})
            if arg == "name":
                person = data.get("person")
                if not person and (data.get("name") or "").strip():
                    person = (L.people_action({"action": "add", "name": data["name"]}).get("person") or {}).get("id")
                if not person:
                    return self.send_json({"error": "Type a name first."})
                return self.send_json(faces.name_group(pid, data.get("group"), person))
            if arg == "reject":
                return self.send_json(faces.reject_group(pid, data.get("group")))
            if arg == "confirm":
                return self.send_json(faces.confirm(pid, data.get("clip"), data.get("person")))
            return self.send_json({"error": "unknown faces action"}, 400)
        if action == "describe":
            import tagger
            if arg == "cancel":
                return self.send_json(tagger.cancel(pid))
            if arg == "tidy":
                return self.send_json(tagger.start_tidy(pid, data.get("engine")))
            if arg == "estimate":
                n = len(tagger.pick_clips(pid, data.get("scope", "untagged"), data.get("ids")))
                return self.send_json({"count": n, "cost": tagger.estimate(n, data.get("model"))})
            return self.send_json(tagger.start(pid, data.get("scope", "untagged"), data.get("ids"), data.get("engine")))
        if action == "rescan":
            L.start_ingest(pid)
            return self.send_json({"ok": True})
        if action == "send":
            return self.send_json(L.send(pid))
        if action == "test-export":
            return self.send_json(L.test_export(pid))
        if action == "remove":
            return self.send_json(L.remove_project(pid))
        if action == "move-back":
            return self.send_json(L.move_back(pid, unquote(arg or ""), data.get("status", "approved")))
        if action == "undo":
            return self.send_json(L.undo_last(pid))
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--library", help="library folder (default: ../library beside the code folder)")
    a = ap.parse_args()
    if a.library:
        L.LIB = os.path.abspath(a.library)
    os.makedirs(L.LIB, exist_ok=True)
    # pick up anything new in each project's source folders, plus missing locations/proxies
    for pr in L.load_lib()["projects"]:
        L.start_ingest(pr["id"])
    server = None
    for port in range(a.port, a.port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            if port == a.port:
                print(f"Port {port} is busy - probably an older Ingest Sorter still running in another "
                      "Terminal window. You can close that window. Using the next free port instead.")
    if not server:
        sys.exit("Couldn't find a free port. Close other Ingest Sorter windows and try again.")
    url = f"http://localhost:{server.server_address[1]}"
    print(f"Ingest Sorter v1.5 running at {url}")
    print(f"Library: {L.LIB}")
    print("Leave this window open while you work. Press Ctrl-C to stop.")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
