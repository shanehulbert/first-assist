#!/usr/bin/env python3
"""First Assist - ingest.

Reads every video in a source folder, records technical metadata, pulls
thumbnails and (optionally) small H.264 preview proxies, and writes/updates a
catalogue. Re-running is safe: AI tags and your review decisions are kept.

    python3 ingest.py "/path/to/footage" [--catalogue DIR] [--proxies]

Originals are only ever read. Nothing in the source folder is moved or changed.
"""
import re, argparse, json, os, shutil, subprocess, sys
from datetime import datetime, timezone

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mts", ".braw"}
BM = "com.blackmagic-design."


def find_tool(name):
    """Find ffmpeg/ffprobe: a copy in the app's own bin folder first, then the usual Mac install locations."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "bin", name), shutil.which(name), f"/opt/homebrew/bin/{name}",
                 f"/usr/local/bin/{name}", os.path.expanduser(f"~/bin/{name}")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.abspath(cand)
    return None


def tools():
    return {"ffmpeg": find_tool("ffmpeg"), "ffprobe": find_tool("ffprobe")}


EXIF_TAGS = ["LensModel", "LensID", "LensType", "FocalLength", "Aperture", "FNumber", "ISO",
             "ExposureTime", "ShutterSpeed", "ColorTemperature", "Make", "Model", "DeviceModelName"]


def exif_extra(path):
    """Optional: if ExifTool is installed (brew install exiftool), read camera settings that ffprobe can't see,
    including Sony's frame-by-frame camera data. Returns {} if ExifTool isn't there or can't read the file."""
    et = find_tool("exiftool")
    if not et:
        return {}
    try:
        r = subprocess.run([et, "-j", "-G3", "-ee3", "-api", "LargeFileSupport=1", *("-" + t for t in EXIF_TAGS), path],
                           capture_output=True, text=True, timeout=90)
        data = (json.loads(r.stdout or "[]") or [{}])[0]
    except Exception:  # noqa: BLE001 - extra details only
        return {}

    def ok(v):
        v = str(v).strip()
        return v and not v.lower().startswith(("unknown", "n/a", "----", "none")) and v not in ("0", "0.0", "inf")

    def get(*names):
        for grp in ("Main", "Doc1"):
            for n in names:
                v = data.get(f"{grp}:{n}")
                if v is not None and ok(v):
                    return str(v).strip()
        for n in names:
            for k, v in data.items():
                if k.split(":", 1)[-1] == n and ok(v):
                    return str(v).strip()
        return ""

    ap = get("Aperture", "FNumber")
    try:
        ap = f"f/{float(ap):g}" if ap else ""
    except ValueError:
        pass
    focal = get("FocalLength").replace(".0 mm", " mm")
    make, model = get("Make"), get("Model", "DeviceModelName")
    return {"lens": get("LensModel", "LensID", "LensType"), "focal": focal, "aperture": ap, "iso": get("ISO"),
            "shutter": get("ExposureTime", "ShutterSpeed"), "wb_kelvin": get("ColorTemperature").replace(" K", ""),
            "camera": (f"{make} {model}".strip() if model and make and not model.lower().startswith(make.lower()) else model)}


def merge_exif(m, path):
    """Fill only the fields that are still empty, then work out the shutter angle."""
    x = exif_extra(path)
    for k, v in x.items():
        if v and not m.get(k):
            m[k] = v
    sh = m.get("shutter") or ""
    if m.get("shutter_angle") is None and sh.startswith("1/") and m.get("fps"):
        try:
            m["shutter_angle"] = round(360 * m["fps"] / float(sh[2:]), 1)
        except ValueError:
            pass
    m["et_checked"] = bool(find_tool("exiftool"))
    return m


_ROMAN = {"2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI"}


def sony_name(code):
    """ILCE-7RM2 -> Sony A7R II, ILCE-6400 -> Sony A6400, FX3 stays FX3."""
    m = re.match(r"^ILCE-(\d+)([A-Z]*?)(?:M(\d))?$", code or "")
    if not m:
        return f"Sony {code}" if code else ""
    num, letters, gen = m.groups()
    return f"Sony A{num}{letters}" + (f" {_ROMAN.get(gen, gen)}" if gen else "")


def sony_xml(path):
    """Sony cameras embed their clip XML (camera model, lens) inside the MP4, near the start or the end."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            chunks = [f.read(1_000_000)]
            if size > 1_000_000:
                f.seek(max(0, size - 1_000_000))
                chunks.append(f.read())
    except OSError:
        return {}
    blob = b"".join(chunks)
    i = blob.find(b"<NonRealTimeMeta")
    if i < 0:
        return {}
    xml = blob[i:i + 20000].decode("utf-8", "ignore")
    dev = re.search(r'<Device[^>]*modelName="([^"]+)"', xml)
    lens = re.search(r'<Lens[^>]*modelName="([^"]+)"', xml)
    return {"camera": sony_name(dev.group(1)) if dev else "", "lens": lens.group(1) if lens else ""}


def probe(path):
    out = subprocess.run([find_tool("ffprobe") or "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    t = d["format"].get("tags", {})
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    num, den = v.get("r_frame_rate", "0/1").split("/")
    fps = round(int(num) / int(den), 3) if int(den) else 0
    w, h = v.get("width", 0), v.get("height", 0)
    # rotation (phones often store portrait as rotated landscape)
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = abs(int(sd["rotation"]))
    if rot in (90, 270):
        w, h = h, w
    tc = v.get("tags", {}).get("timecode") or next(
        (s.get("tags", {}).get("timecode") for s in d["streams"] if s.get("tags", {}).get("timecode")), "")
    shutter = t.get(BM + "shutterSpeed", "")
    angle = None
    if shutter.startswith("1/") and fps:
        try:
            angle = round(360 * fps / float(shutter[2:]), 1)
        except ValueError:
            pass
    m = {
        "duration": round(float(d["format"].get("duration", 0)), 3),
        "size_mb": round(int(d["format"].get("size", 0)) / 1e6),
        "codec": f'{v.get("codec_name", "")} {v.get("profile", "")}'.strip(),
        "width": w, "height": h,
        "orientation": "portrait" if h > w else "landscape",
        "fps": fps,
        "transfer": v.get("color_transfer", ""),
        "audio": any(s["codec_type"] == "audio" for s in d["streams"]),
        "timecode": tc,
        "recorded": t.get("com.apple.quicktime.creationdate") or t.get("creation_time", ""),
        "camera": t.get("com.apple.quicktime.model", ""),
        "app": t.get("com.apple.quicktime.software", ""),
        "lens": t.get(BM + "camera.lensType", ""),
        "iso": t.get(BM + "camera.iso", ""),
        "shutter": shutter,
        "shutter_angle": angle,
        "aperture": t.get(BM + "camera.aperture", ""),
        "wb_kelvin": t.get(BM + "camera.whiteBalanceKelvin", ""),
        "project": t.get(BM + "camera.projectName", "") or t.get("com.apple.quicktime.title", ""),
        "environment": t.get(BM + "camera.environment", ""),
        "gps": t.get("com.apple.quicktime.location.ISO6709", ""),
        "camera_id": t.get("com.apple.proapps.cameraName", "") or t.get(BM + "camera.id", ""),
        "reel": t.get("com.apple.proapps.reel", "") or t.get("reel_name", ""),
    }
    if not m["camera"] and t.get("major_brand", "").strip().upper() in ("XAVC", "XAVC_S"):
        sx = sony_xml(path)
        m["camera"] = m["camera"] or sx.get("camera", "")
        m["lens"] = m["lens"] or sx.get("lens", "")
    m["nrt_checked"] = True
    return merge_exif(m, path)


def tech_flags(m):
    flags = []
    a = m.get("shutter_angle")
    if a is not None and a < 45:
        flags.append(f"Very narrow shutter ({a}°) - staccato motion")
    if a is not None and a > 270:
        flags.append(f"Very wide shutter ({a}°) - heavy motion blur")
    if m["duration"] < 2:
        flags.append("Very short clip")
    return flags


def save_clip(cat_file, cat, clip):
    """Re-read the catalogue before writing so tags or review edits made while
    ingest was running (e.g. in the review app) are never overwritten."""
    if os.path.exists(cat_file):
        disk = json.load(open(cat_file))
        old = disk["clips"].get(clip["id"], {})
        for k in ("ai", "review"):
            if k in old:
                clip[k] = old[k]
        disk["clips"][clip["id"]] = clip
        for k in ("name", "source_path", "updated"):
            disk[k] = cat[k]
        cat = disk
    tmp = cat_file + ".tmp"
    json.dump(cat, open(tmp, "w"), indent=1)
    os.replace(tmp, cat_file)


def proxy_encoder():
    """Use the Mac's hardware H.264 encoder when ffmpeg has it - far faster."""
    out = subprocess.run([find_tool("ffmpeg") or "ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "h264_videotoolbox" in out:
        return ["-c:v", "h264_videotoolbox", "-b:v", "2500k"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"]


def ffmpeg(*args):
    subprocess.run([find_tool("ffmpeg") or "ffmpeg", "-v", "error", "-y", *args], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--catalogue", help="catalogue folder (default: ../catalogues/<source name> next to this tool)")
    ap.add_argument("--source-path", help="path to record for the source folder, if different from where it is read now")
    ap.add_argument("--frames", type=int, default=5, help="thumbnails per clip")
    ap.add_argument("--proxies", action="store_true", help="also make small H.264 preview proxies")
    a = ap.parse_args()

    src = os.path.abspath(a.source)
    name = os.path.basename(src.rstrip("/"))
    here = os.path.dirname(os.path.abspath(__file__))
    cat_dir = a.catalogue or os.path.join(here, "..", "..", "catalogues", name)
    cat_dir = os.path.abspath(cat_dir)
    os.makedirs(os.path.join(cat_dir, "thumbs"), exist_ok=True)
    os.makedirs(os.path.join(cat_dir, "proxies"), exist_ok=True)
    cat_file = os.path.join(cat_dir, "catalogue.json")

    cat = json.load(open(cat_file)) if os.path.exists(cat_file) else {"clips": {}}
    cat["name"] = name
    cat["source_path"] = a.source_path or cat.get("source_path") or src
    cat["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    global ENCODER
    ENCODER = proxy_encoder() if a.proxies else []
    files = sorted(f for f in os.listdir(src)
                   if os.path.splitext(f)[1].lower() in VIDEO_EXT and not f.startswith("."))
    for i, f in enumerate(files, 1):
        path = os.path.join(src, f)
        stem = os.path.splitext(f)[0]
        clip = cat["clips"].setdefault(stem, {})
        print(f"[{i}/{len(files)}] {f}", flush=True)
        m = probe(path)
        clip.update({"id": stem, "file": f, "meta": m, "flags": tech_flags(m)})
        clip.setdefault("ai", {"description": "", "category": "", "tags": [], "source": ""})
        clip.setdefault("review", {"status": "unreviewed", "rating": 0, "in": None, "out": None,
                                   "notes": "", "tags": None, "description": None, "category": None})

        thumbs = []
        for k in range(a.frames):
            t = m["duration"] * (k + 0.5) / a.frames
            rel = f"thumbs/{stem}_{k}.jpg"
            if not os.path.exists(os.path.join(cat_dir, rel)):
                ffmpeg("-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4",
                       os.path.join(cat_dir, rel))
            thumbs.append({"t": round(t, 2), "src": rel})
        clip["thumbs"] = thumbs

        if a.proxies:
            rel = f"proxies/{stem}.mp4"
            dst = os.path.join(cat_dir, rel)
            if not os.path.exists(dst):
                ffmpeg("-i", path, "-vf", "scale=-2:540", *ENCODER, "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-b:a", "96k", "-movflags", "+faststart",
                       dst + ".part.mp4")
                os.replace(dst + ".part.mp4", dst)
            clip["proxy"] = rel
        save_clip(cat_file, cat, clip)

    print(f"Catalogue: {cat_file} ({len(files)} clips)")


if __name__ == "__main__":
    main()
