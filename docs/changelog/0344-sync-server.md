# 0344 — PRD-005 slice 2: git sync, server half

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
Smart-HTTP git serving for project `.history` repos: a streamed
`git http-backend` CGI proxy under `/git/{org}/{ws}/{proj}.git/`, the FR9
pre-receive hook, and write-scoped worktree materialization. FR8's server
half.

## Changes
- `core/sync_server.py`: `prepare_repo` (idempotent, memoized on
  config/hook mtimes; `receive.denyCurrentBranch=ignore` — the spike
  proved `updateInstead` structurally unusable against the `.history`
  layout; `denyNonFastForwards`/`denyDeletes` as belt) + the versioned
  `/bin/sh` pre-receive hook: any ref delete refused, branch non-FF
  refused ("pull and merge, never force"), tag rewrite refused ("tags
  are immutable"), anything outside heads/tags refused; null-oid checked
  sha256-correctly; FF via `merge-base --is-ancestor`.
  `materialize` runs `checkout -f` under the project write scope
  (turnlock honored — a held turn means the push lands (200) with
  `materialized: false`, never a clobber; a pre-push-dirty tree skips
  checkout unless forced). `probe()` reports git/http-backend presence
  (portability-tested so a missing backend is a red leg, not a 500).
- `server/routes_sync.py` (`PREFIX=/git`): the three smart endpoints
  only — config/HEAD/hooks/comments and the dumb protocol are 404.
  CGI env per the spike (GIT_PROJECT_ROOT=project dir,
  PATH_INFO=/.history/…, GIT_PROTOCOL forwarded — protocol v2 asserted;
  hermetic HOME inside the GIT_DIR so an operator's `core.hooksPath`
  cannot disable the hook; REMOTE_USER=principal). **Request bodies
  spool to an unlinked temp file** (64 KB chunks, capped via
  `AGENTCAD_SYNC_MAX_PUSH_MB`, default 512): the app's
  `BaseHTTPMiddleware` makes a full-duplex proxy impossible —
  http-backend emits CGI headers before reading the body, and
  `StreamingResponse` + the middleware share one receive channel
  (measured: 3 MB pushes stall at 288 KB full-duplex). Responses still
  stream; child stderr drained in a loop (a single read deadlocks large
  pushes on the 64 KB pipe).
- Auth: `install_git_auth()` wraps `security.guard`
  (capture-and-reinstall, `_WRAPPED`) for `/git/` paths only — git
  speaks Basic (password = bearer token → rewritten to Bearer in the
  ASGI scope) and needs `WWW-Authenticate: Basic` on 401 or clients
  never offer credentials. Integration slice decides whether to fold
  into security.py.
- Seams for later slices (module attrs, fake-tested, fixture-reset):
  `require_role` (view for upload-pack, edit for receive-pack incl.
  advertisement; None → authenticated-principal floor),
  `resolve_project` (None → flat store, segments validated),
  `on_materialize` (slice 5's audit tap).

## Files
- `agentcad/core/sync_server.py`, `agentcad/server/routes_sync.py` — new
- `tests/test_sync_server.py` — new (25 integration tests, real uvicorn +
  real git; clone/push round trips, all hook refusals, 3 MB streamed
  push, hosted 401/Basic-token auth, materialization vs held turn)

## Notes
Verified on macOS/git 2.50.1; the Linux leg runs the module (portability
marker). A never-committed project answers a name-free 404 (slice 6's
clone should know). `make test` — 6798 passed, 51 skipped (13:10); non-passing: the pre-existing prd028 AC6 local solver timeout (skips on CI; unchanged since PRD-017's proof it predates these branches) and supervisor/share_frontend load flakes (green in isolation).
