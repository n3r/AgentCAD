#!/bin/bash
# Smoke-test the built onedir bundle end-to-end:
#   1. dist/agentcad/agentcad serve  (background, throwaway projects dir)
#   2. /api/health answers
#   3. create_project + create_part via /api/tools  (create_part exercises the
#      FROZEN kernel worker: the bundle re-execs itself with `worker`)
#   4. part metrics report a positive volume
#   5. GET / serves the frontend HTML
#
# Usage: scripts/smoke_binary.sh [path-to-executable]   (default dist/agentcad/agentcad)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${1:-$REPO_ROOT/dist/agentcad/agentcad}"
PORT="${SMOKE_PORT:-8637}"
BASE="http://127.0.0.1:$PORT"
TMP="$(mktemp -d)"
SERVER_PID=""

cleanup() {
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        # Kill the whole process group: serve + its kernel worker children.
        kill -- -"$SERVER_PID" 2>/dev/null || kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP"
}
trap cleanup EXIT

fail() {
    echo "SMOKE FAIL: $1" >&2
    echo "--- server log tail ---" >&2
    tail -50 "$TMP/server.log" >&2 || true
    exit 1
}

# jq-free JSON assertions via the system python3 (dev machine tool, not a
# runtime dependency of the bundle).
json_ok() {  # json_ok <json> <python-expr over parsed `d`>
    python3 -c '
import json, sys
d = json.loads(sys.argv[1])
assert "error" not in d, "API error: %r" % (d["error"],)
assert eval(sys.argv[2]), "assertion %r failed on: %s" % (sys.argv[2], json.dumps(d)[:400])
' "$1" "$2"
}

[[ -x "$BIN" ]] || { echo "SMOKE FAIL: no executable at $BIN (run scripts/build_binary.sh first)" >&2; exit 1; }

# A leftover listener would make us smoke-test the wrong server instance.
if curl -sf --max-time 2 "$BASE/api/health" > /dev/null 2>&1; then
    echo "SMOKE FAIL: something is already listening on $BASE — kill it or set SMOKE_PORT" >&2
    exit 1
fi

echo "== starting: $BIN serve --no-open --port $PORT --projects-dir $TMP/projects"
set -m  # own process group per job, so cleanup can kill serve + worker together
"$BIN" serve --no-open --port "$PORT" --projects-dir "$TMP/projects" \
    > "$TMP/server.log" 2>&1 &
SERVER_PID=$!
set +m

# The FIRST launch of a freshly built bundle can take several minutes on
# macOS: Gatekeeper verifies the code signatures of ~400 MB of just-written
# dylibs once (subsequent launches come up in ~15-20 s). Allow 300 s.
echo "== waiting for $BASE/api/health (up to 300s; first launch after a build is slow)"
health=""
for _ in $(seq 1 600); do
    if health="$(curl -sf --max-time 2 "$BASE/api/health" 2>/dev/null)"; then
        break
    fi
    kill -0 "$SERVER_PID" 2>/dev/null || fail "server process exited during startup"
    sleep 0.5
done
[[ -n "$health" ]] || fail "/api/health never answered"
echo "health: $health"
json_ok "$health" 'd["status"] == "ok"'

echo "== tool packs discovered (pkgutil.iter_modules over the frozen package)"
resp="$(curl -sf --max-time 30 "$BASE/api/tools")" || fail "list tools failed"
json_ok "$resp" '{"create_part", "solve_sketch", "list_materials", "generate_drawing"} <= {t["name"] for t in d["tools"]}'
echo "tools: $(python3 -c 'import json,sys; print(len(json.loads(sys.argv[1])["tools"]), "tools listed")' "$resp")"

echo "== bundled examples registered"
resp="$(curl -sf --max-time 30 "$BASE/api/projects")" || fail "list projects failed"
echo "projects: ${resp:0:300}..."
json_ok "$resp" '"construction" in [p["name"] for p in d["projects"]]'

echo "== create_project"
resp="$(curl -sf --max-time 30 -X POST "$BASE/api/tools/create_project" \
    -H 'Content-Type: application/json' -d '{"name": "smoke"}')" \
    || fail "create_project request failed"
echo "create_project: $resp"
json_ok "$resp" 'd.get("name") == "smoke"'

echo "== create_part (template part — first build spawns the frozen kernel worker; may take a while)"
resp="$(curl -sf --max-time 300 -X POST "$BASE/api/tools/create_part" \
    -H 'Content-Type: application/json' \
    -d '{"project": "smoke", "part_id": "bracket"}')" \
    || fail "create_part request failed"
echo "create_part: ${resp:0:300}..."
json_ok "$resp" 'd.get("id") == "bracket"'

echo "== part metrics (built by the frozen worker)"
resp="$(curl -sf --max-time 60 "$BASE/api/projects/smoke/parts/bracket/metrics")" \
    || fail "metrics request failed"
echo "metrics: $resp"
json_ok "$resp" 'd["volume_mm3"] > 0 and d["is_valid"]'

echo "== frontend"
index="$(curl -sf --max-time 10 "$BASE/")" || fail "GET / failed"
case "$index" in
    *"<title>AgentCAD</title>"*) echo "frontend: index.html served ($(printf %s "$index" | wc -c | tr -d ' ') bytes)" ;;
    *) fail "GET / did not return the AgentCAD index.html" ;;
esac

echo
echo "SMOKE OK: server + frontend + frozen kernel worker all healthy"
