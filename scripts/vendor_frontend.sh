#!/usr/bin/env bash
# Vendor the frontend's third-party libraries into frontend/vendor/.
#
# The frontend is offline-only: no CDN references. This script npm-installs
# three (latest) and codemirror@5 into a throwaway directory and copies the
# exact files the UI needs. The vendored files are committed.
#
# Files produced under frontend/vendor/:
#   three.module.min.js     ES module build of three.js (re-exports core)
#   three.core.min.js       core chunk imported by three.module.min.js
#   OrbitControls.js        examples/jsm controls; its `from 'three'` import
#                           is resolved by the import map in index.html
#                           ({"three": "/vendor/three.module.min.js"})
#   codemirror.js           CodeMirror 5 (UMD -> window.CodeMirror)
#   codemirror.css
#   python.js               CodeMirror 5 python mode
#   VERSIONS.txt            record of vendored package versions

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/frontend/vendor"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agentcad-vendor.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "vendoring into $VENDOR_DIR (workdir $WORK_DIR)"
cd "$WORK_DIR"
npm init -y >/dev/null 2>&1
npm install --no-audit --no-fund three@latest codemirror@5 >/dev/null

THREE_DIR="$WORK_DIR/node_modules/three"
CM_DIR="$WORK_DIR/node_modules/codemirror"

# Sanity: the module build must exist, and if it re-exports from a core
# chunk that chunk must be copied alongside so the import graph resolves.
test -f "$THREE_DIR/build/three.module.min.js"
test -f "$CM_DIR/lib/codemirror.js"

mkdir -p "$VENDOR_DIR"
cp "$THREE_DIR/build/three.module.min.js" "$VENDOR_DIR/three.module.min.js"
if grep -q "three.core.min.js" "$VENDOR_DIR/three.module.min.js"; then
  cp "$THREE_DIR/build/three.core.min.js" "$VENDOR_DIR/three.core.min.js"
fi
cp "$THREE_DIR/examples/jsm/controls/OrbitControls.js" "$VENDOR_DIR/OrbitControls.js"
cp "$CM_DIR/lib/codemirror.js" "$VENDOR_DIR/codemirror.js"
cp "$CM_DIR/lib/codemirror.css" "$VENDOR_DIR/codemirror.css"
cp "$CM_DIR/mode/python/python.js" "$VENDOR_DIR/python.js"

three_version="$(node -p "JSON.parse(require('fs').readFileSync('$THREE_DIR/package.json')).version")"
cm_version="$(node -p "JSON.parse(require('fs').readFileSync('$CM_DIR/package.json')).version")"
cat > "$VENDOR_DIR/VERSIONS.txt" <<EOF
three $three_version
codemirror $cm_version
vendored $(date -u +%Y-%m-%dT%H:%M:%SZ) by scripts/vendor_frontend.sh
EOF

echo "vendored:"
ls -la "$VENDOR_DIR"
