#!/usr/bin/env python3
"""First Assist - check that everything it needs is installed, and say how to fix what isn't."""
import json, os, shutil, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
OK, NO, OPT = "  OK  ", " MISSING ", " optional "
needed_missing = []


def line(state, name, detail="", fix=""):
    print(f"[{state.strip():^8}] {name}" + (f": {detail}" if detail else ""))
    if fix:
        for f in fix.splitlines():
            print(f"             {f}")


print("\nFirst Assist - setup check\n" + "-" * 28)

# Needed
v = sys.version_info
if v >= (3, 9):
    line(OK, "Python", f"{v.major}.{v.minor}.{v.micro}")
else:
    needed_missing.append("Python")
    line(NO, "Python", f"{v.major}.{v.minor} is too old (3.9 or newer needed)", "Install a newer Python from https://www.python.org/downloads/")

try:
    from ingest import find_tool
except Exception:  # noqa: BLE001
    find_tool = shutil.which
for tool in ("ffmpeg", "ffprobe"):
    path = find_tool(tool)
    if path:
        line(OK, tool, path)
    else:
        needed_missing.append(tool)
        line(NO, tool, "needed to read footage and make thumbnails and previews",
             "In Terminal:  brew install ffmpeg\n(Install Homebrew first from https://brew.sh, or see bin/README.txt for a no-admin option.)")

resolve_dir = os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve")
if sys.platform != "darwin":
    line(NO, "macOS", "First Assist has only been built and tested on a Mac")
elif os.path.isdir(resolve_dir):
    line(OK, "DaVinci Resolve", "found (free or Studio both work)")
else:
    line(NO, "DaVinci Resolve", "not found",
         "Install it from https://www.blackmagicdesign.com/products/davinciresolve and open it once.\nYou can still sort and tag footage without it.")

# Optional
print()
try:
    import cv2  # noqa: F401
    line(OK, "OpenCV", f"{cv2.__version__} (camera-movement measuring and people recognition)")
except Exception:  # noqa: BLE001
    line(OPT, "OpenCV", "adds camera-movement measuring and people recognition",
         "In Terminal:  pip3 install --user opencv-python-headless")

if shutil.which("exiftool") or os.path.exists("/opt/homebrew/bin/exiftool") or os.path.exists("/usr/local/bin/exiftool"):
    line(OK, "ExifTool", "fuller camera details")
else:
    line(OPT, "ExifTool", "adds fuller camera details (aperture, ISO, lens on some cameras)", "In Terminal:  brew install exiftool")

try:
    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
        models = [m["name"] for m in json.load(r).get("models", [])]
    if models:
        line(OK, "Ollama", "running, models: " + ", ".join(models[:6]))
    else:
        line(OPT, "Ollama", "running, but no models yet", "In Terminal:  ollama pull qwen2.5vl:7b   (about 6 GB)")
except Exception:  # noqa: BLE001
    line(OPT, "Ollama", "not running; it powers the free, local AI describer",
         "Install from https://ollama.com, open it, then in Terminal:  ollama pull qwen2.5vl:7b")

print("\n" + "-" * 28)
if needed_missing:
    print("Not ready yet. Install: " + ", ".join(needed_missing) + ". Then run this check again.")
else:
    print('Ready. Double-click "Open First Assist.command" to start.')
    print("Anything marked optional can be added later.")
print()
