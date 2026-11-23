#!/bin/bash
# Builds "First Assist.app", a small Mac launcher for this folder.
#
#   tools/make_app.sh                 builds it into /Applications
#   tools/make_app.sh ~/Desktop       builds it somewhere else
#
# Double-clicking the app:
#   1. if First Assist is already running on port 8765, just opens a browser tab;
#   2. otherwise starts it in the background (no Terminal window), logging to library/first-assist.log;
#   3. waits until it answers, then opens http://localhost:8765 in the default browser.
# It always uses port 8765, so the address (and your browser's bookmarks and saved settings) never change.
# Uses only tools built into macOS (sips, iconutil, codesign). Build it again if you move this folder.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-/Applications}"
PORT="${FA_APP_PORT:-8765}"     # only testing changes these two; the real app is always 8765
TESTLIB="${FA_APP_LIBRARY:-}"   # and uses the library folder beside the code
NAME="First Assist"
APP="$DEST/$NAME.app"
ICON_SRC="$HERE/branding/first-assist-app-icon-1024.png"
PY="/usr/bin/python3"           # the same python3 the .command finds in Terminal

[ -f "$HERE/sorter/server.py" ] || { echo "Can't find sorter/server.py beside this script."; exit 1; }
[ -f "$ICON_SRC" ] || { echo "Can't find $ICON_SRC"; exit 1; }
VERSION="$(sed -n 's/^RELEASE = "\([^"]*\)".*/\1/p' "$HERE/sorter/server.py")"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/$NAME.app/Contents/MacOS" "$WORK/$NAME.app/Contents/Resources"

# ---- icon ----
SET="$WORK/AppIcon.iconset"
mkdir -p "$SET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ICON_SRC" --out "$SET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  sips -z $d $d "$ICON_SRC" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$SET" -o "$WORK/$NAME.app/Contents/Resources/AppIcon.icns"

# ---- Info.plist ----
cat > "$WORK/$NAME.app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>local.first-assist.launcher</string>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSArchitecturePriority</key><array><string>arm64</string><string>x86_64</string></array>
  <key>LSRequiresNativeExecution</key><true/>
</dict>
</plist>
PLIST

# ---- the launcher ----
cat > "$WORK/$NAME.app/Contents/MacOS/launch" <<LAUNCH
#!/bin/bash
# First Assist launcher (built by tools/make_app.sh from $HERE)
CODE="$HERE"
PORT=$PORT
PY="$PY"
URL="http://localhost:\$PORT"
LIB="$TESTLIB"
[ -n "\$LIB" ] || LIB="\$(cd "\$CODE/.." && pwd)/library"
LOG="\$LIB/first-assist.log"

alert() { /usr/bin/osascript -e "display alert \"First Assist\" message \"\$1\" as warning" >/dev/null 2>&1; }
ours() { /usr/bin/curl -s -m 2 "http://127.0.0.1:\$PORT/api/version" 2>/dev/null | /usr/bin/grep -qE '"app": ?"(first-assist|footage-sorter)"'; }

if ours; then /usr/bin/open "\$URL"; exit 0; fi
if [ ! -f "\$CODE/sorter/server.py" ]; then
  alert "Can't find the app's code at \$CODE. If you moved the first-assist folder, build the app again with tools/make_app.sh."
  exit 1
fi
if /usr/sbin/lsof -nP -iTCP:\$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  alert "Another program is using port \$PORT, which First Assist needs. If it's an older First Assist in a Terminal window, close that window. Then try again."
  exit 1
fi
mkdir -p "\$LIB"
{ echo; echo "---- started \$(date '+%Y-%m-%d %H:%M:%S') ----"; } >> "\$LOG"
cd "\$CODE"
# On Apple silicon always run Python natively: OpenCV and NumPy are installed for arm64, and a launcher
# started under Rosetta would otherwise start Python as Intel and face recognition would fail to load.
ARCH=()
[ "\$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] && ARCH=(/usr/bin/arch -arm64)
/usr/bin/nohup "\${ARCH[@]}" "\$PY" sorter/server.py --port \$PORT --no-browser --library "\$LIB" >> "\$LOG" 2>&1 < /dev/null &
for i in \$(seq 1 60); do
  if ours; then /usr/bin/open "\$URL"; exit 0; fi
  sleep 0.25
done
alert "First Assist didn't start within 15 seconds. The details are in \$LOG"
exit 1
LAUNCH
chmod 755 "$WORK/$NAME.app/Contents/MacOS/launch"

# ad-hoc signature so macOS treats the bundle as one unit
codesign --force --deep -s - "$WORK/$NAME.app" >/dev/null 2>&1 || true

mkdir -p "$DEST"
rm -rf "$APP"
mv "$WORK/$NAME.app" "$APP"
touch "$APP"   # so Finder and the Dock pick up the icon
echo "Built $APP (port $PORT, code in $HERE)"
