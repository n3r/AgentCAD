#!/bin/bash
# Build dist/AgentCAD.app — a minimal macOS app bundle that launches the
# AgentCAD server and opens the browser UI. Ad-hoc signed for local use.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$REPO_ROOT/dist/AgentCAD.app"
UV_BIN="$(command -v uv)"

if [[ -z "$UV_BIN" ]]; then
    echo "error: uv not found on PATH" >&2
    exit 1
fi

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>AgentCAD</string>
    <key>CFBundleDisplayName</key><string>AgentCAD</string>
    <key>CFBundleIdentifier</key><string>dev.agentcad.app</string>
    <key>CFBundleExecutable</key><string>AgentCAD</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>LSUIElement</key><false/>
</dict>
</plist>
PLIST

cat > "$APP_DIR/Contents/MacOS/AgentCAD" <<LAUNCHER
#!/bin/bash
# Launches the AgentCAD server (installing deps on first run) and opens the UI.
LOG="\$HOME/Library/Logs/AgentCAD.log"
cd "$REPO_ROOT"
exec "$UV_BIN" run agentcad open >> "\$LOG" 2>&1
LAUNCHER
chmod +x "$APP_DIR/Contents/MacOS/AgentCAD"

codesign --force --deep -s - "$APP_DIR"
echo "built $APP_DIR"
