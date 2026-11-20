"""Ingest Sorter - link to a Notion video production system (optional).

Notion plans, the Sorter logs the footage, Resolve cuts. Nothing here runs unless a
Notion token has been saved in Settings, so people without Notion never see it.

The token lives in library/secrets.json (like the Claude key). The database IDs live in
library.json settings, never in the code.
"""
import json, os, re, urllib.error, urllib.request

import library as L

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"  # the data sources version of the API
# key -> label shown in Settings. The IDs themselves come from the library, not from here.
DATABASES = {"productions": "Video Productions", "episodes": "Video Episodes", "scenes": "Scenes",
             "characters": "Characters", "assets": "Production Assets"}


class NotionError(Exception):
    def __init__(self, status, code, friendly):
        super().__init__(friendly)
        self.status, self.code, self.friendly = status, code, friendly


# ---------- token ----------
def _secrets():
    try:
        with open(os.path.join(L.LIB, "secrets.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get_token():
    return (_secrets().get("notion_token") or "").strip()


def set_token(token):
    s = _secrets()
    s["notion_token"] = (token or "").strip()
    if not s["notion_token"]:
        s.pop("notion_token")
    path = os.path.join(L.LIB, "secrets.json")
    os.makedirs(L.LIB, exist_ok=True)
    with open(path, "w") as f:
        json.dump(s, f)
    os.chmod(path, 0o600)


# ---------- http ----------
def _http(method, path, body=None, token=None, timeout=60):
    token = token or get_token()
    if not token:
        raise NotionError(0, "no_token", "Notion isn't connected. Add the Notion token in Settings first.")
    req = urllib.request.Request(f"{API}/{path.lstrip('/')}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(raw)
        except ValueError:
            j = {}
        code, msg = j.get("code", ""), j.get("message", raw[:200])
        if e.code == 401:
            friendly = "Notion didn't accept the token. Check it in Settings › Notion."
        elif e.code == 404 or code == "object_not_found":
            friendly = "Notion can't find that, or it isn't shared with the Ingest Sorter connection."
        elif e.code == 403:
            friendly = "The Ingest Sorter connection isn't allowed to do that in Notion (" + msg + ")."
        elif e.code == 429:
            friendly = "Notion is limiting requests. Try again in a minute."
        elif e.code >= 500:
            friendly = f"Notion's servers had a problem ({e.code}). Try again shortly."
        else:
            friendly = msg
        raise NotionError(e.code, code, friendly)
    except urllib.error.URLError as e:
        raise NotionError(0, "network", f"Couldn't reach Notion ({e.reason}). Check the internet connection.")


def check_token(token=None):
    """Ask Notion who the token belongs to: proves it works, changes nothing."""
    try:
        me = _http("GET", "users/me", token=token, timeout=20)
        name = (me.get("bot") or {}).get("workspace_name") or me.get("name") or "Notion"
        return {"ok": True, "message": f"Connected to {name}."}
    except NotionError as e:
        return {"ok": False, "message": e.friendly}


# ---------- database IDs ----------
def clean_id(s):
    """Accept an ID with or without dashes, or a Notion link that contains one."""
    s = str(s or "").split("?")[0].split("#")[0]  # a link's ?v= part is a view ID, not the database
    m = re.findall(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", s, re.I)
    if not m:
        return ""
    h = m[-1].replace("-", "").lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def ids():
    return L.load_lib().get("settings", {}).get("notion_ids") or {}


def save_ids(new):
    with L.LOCK:
        lib = L.load_lib()
        cur = lib["settings"].get("notion_ids") or {}
        for k in DATABASES:
            if k in (new or {}):
                v = clean_id(new[k])
                if v:
                    cur[k] = v
                else:
                    cur.pop(k, None)
        lib["settings"]["notion_ids"] = cur
        lib["settings"].pop("notion_ds", None)  # IDs changed: test again
        L.save_lib(lib)
    return cur


def _title(obj):
    return "".join(t.get("plain_text", "") for t in obj.get("title") or []) or obj.get("name") or ""


def check_ids():
    """Look up each saved ID. The current API wants data source IDs; if a database ID was
    given instead, use its first data source. Read-only."""
    out, ds = {}, {}
    for k, label in DATABASES.items():
        i = ids().get(k)
        if not i:
            out[k] = {"ok": False, "missing": True, "message": "No ID yet."}
            continue
        try:
            d = _http("GET", f"data_sources/{i}")
            ds[k] = d["id"]
            out[k] = {"ok": True, "form": "data source", "title": _title(d), "properties": len(d.get("properties") or {})}
            continue
        except NotionError as e:
            if e.status in (0, 401, 429) or e.status >= 500:
                out[k] = {"ok": False, "message": e.friendly}
                continue
            first = e
        try:
            db = _http("GET", f"databases/{i}")
            srcs = db.get("data_sources") or []
            if not srcs:
                raise NotionError(404, "", "That database has no data source.")
            d = _http("GET", f"data_sources/{srcs[0]['id']}")
            ds[k] = d["id"]
            out[k] = {"ok": True, "form": "database", "title": _title(d), "properties": len(d.get("properties") or {}),
                      "note": "This is a database ID; using its data source." + (" It has more than one data source." if len(srcs) > 1 else "")}
        except NotionError:
            out[k] = {"ok": False, "message": first.friendly}
    with L.LOCK:
        lib = L.load_lib()
        lib["settings"]["notion_ds"] = ds
        L.save_lib(lib)
    return out


def status():
    s = L.load_lib().get("settings", {})
    return {"has_token": bool(get_token()), "ids": s.get("notion_ids") or {}, "ds": s.get("notion_ds") or {},
            "databases": DATABASES}
