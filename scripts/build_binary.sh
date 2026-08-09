#!/bin/bash
# Build dist/agentcad — the PyInstaller onedir bundle (server + frontend +
# kernel worker + examples in one directory, no Python/uv needed to run it).
#
# Usage: scripts/build_binary.sh
# The build venv (.buildvenv) is separate from the project .venv on purpose:
# PyInstaller and its hooks never enter the app's runtime environment.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_VENV="$REPO_ROOT/.buildvenv"
SPEC="$REPO_ROOT/packaging/pyinstaller/agentcad.spec"

if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
    uv venv --python 3.12 "$BUILD_VENV"
fi
VIRTUAL_ENV="$BUILD_VENV" uv pip install --quiet -e "$REPO_ROOT" pyinstaller

"$BUILD_VENV/bin/pyinstaller" --noconfirm \
    --distpath "$REPO_ROOT/dist" \
    --workpath "$REPO_ROOT/build/pyinstaller" \
    "$SPEC"

BUNDLE="$REPO_ROOT/dist/agentcad"
echo
echo "built:  $BUNDLE"
echo "size:   $(du -sh "$BUNDLE" | cut -f1)"
echo "run:    $BUNDLE/agentcad serve --no-open"
echo "verify: scripts/smoke_binary.sh"
