# 0194 — PRD-005a slice 6: Dockerfile, compose, the PREFIX seam, deployment docs

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (with Nikita Fedorov)

## Summary

`docker compose up` serves a real hosted AgentCAD with persistent state on one
volume. Adds the multi-stage `Dockerfile`, `compose.yaml` (one service, one
volume, a `proxy` profile for one-command TLS), `.dockerignore`, `.env.example`
and `docs/deployment.md`, plus the two-line `PREFIX` seam that lets a route
pack mount somewhere other than `/api` — PRD-007's share links need `/s/<token>`
at the root and the extension point could not express it.

## Changes

- **`Dockerfile` (new).** Multi-stage on `python:3.12-slim`. Builder installs
  `uv` and runs `uv sync --locked --no-dev`; runtime installs exactly the six
  OCCT system libraries the Linux CI job already proves are needed **plus
  `git`**, which `core/history.py` shells out to and which no CI step ever had
  to install because runners ship it. Non-root `agentcad` (uid/gid 10001 —
  a number the docs can tell an operator to `chown` a bind mount to),
  `HOME=/data/home`, `AGENTCAD_PROJECTS_DIR=/data/projects`,
  `AGENTCAD_STATE_DIR=/data/state`, `EXPOSE 8630`,
  `CMD ["agentcad", "serve", "--no-open"]`.
- **`compose.yaml` (new).** One `agentcad` service, one named volume at
  `/data`, the FR24 environment block, a `healthcheck` that opens
  `/api/health`, `restart: unless-stopped`, and a `caddy` service behind
  `profiles: ["proxy"]` for automatic ACME. The header carries the trust
  sentence verbatim (FR17's third place).
- **`.env.example`, `.dockerignore` (new).** `.env` is in the ignore list: it
  holds the session key and must never land in a layer `docker history` can
  read. `.venv/` too — it holds absolute host paths and would shadow the venv
  the builder makes.
- **`docs/deployment.md` (new).** Trust statement first, before any
  instruction; quick start; the full FR24 environment table plus the mode
  interlock; TLS (nginx and Caddy snippets, then `--profile proxy`), including
  why bring-your-own is the default; sizing; accounts; tokens and MCP; backup
  (**no downtime, no quiescing** — every write is an atomic replace) and its
  "treat the archive as a secret" warning; restore; upgrade; what PRD-006 will
  change.
- **`agentcad/server/app.py`** — `_mount_route_packs` honours a module-level
  `PREFIX`, defaulting to `/api`. Commented as PRD-007's seam, and as *not* a
  way past the allowlist: `security.is_public` is consulted with the full
  request path.
- **`tests/test_deploy_config.py` (new)** — 37 tests over the artefacts with no
  Docker daemon, including a positive control for the hand-rolled YAML parser
  (a parser returning `{}` would make every other assertion vacuous), the
  16 FR24 variables parametrised against the doc, and the FR27 topics.
- **`tests/test_route_prefix.py` (new)** — 4 tests. The probe pack is injected
  through `sys.modules` plus a path-filtered `pkgutil.iter_modules`, **not** by
  writing a file into `agentcad/server/`: eight workers share one checkout and
  a real file would be visible to every app every other worker builds.

## Files

- `Dockerfile`, `compose.yaml`, `.dockerignore`, `.env.example` — new
- `docs/deployment.md` — new
- `agentcad/server/app.py` — the `PREFIX` seam
- `tests/test_deploy_config.py`, `tests/test_route_prefix.py` — new

## Notes

- **Why the image copies `/app` wholesale.** `_resources.resource_root()` is
  the *parent* of the `agentcad` package, so `frontend/`, `examples/` and
  `catalog/` must sit beside it at the path the editable install points at. A
  `pip install agentcad` into `site-packages` would serve a 404 for the UI and
  silently lose the bundled catalog. `test_the_image_keeps_the_frontend_beside_
  the_package` pins both halves — the `COPY` and the three directories staying
  out of `.dockerignore`.
- **`AGENTCAD_PUBLIC_ORIGIN` defaults rather than `:?`-fails.** A `:?` would
  make `docker compose config` (slice 8's per-PR lint) and the smoke job fail
  with no `.env`, so the file defaults to `http://localhost:8630` and
  `.env.example` plus the docs say to set it. A wrong value is loud, not quiet:
  every request becomes a `403 ForbiddenOrigin`.
- **TLS is inverted from PRD-005 on purpose** (design Decision 11): automatic
  ACME as the *default* needs a real public DNS name at first `up`, which
  breaks an air-gapped install, a staging box and the CI smoke job.
  `--profile proxy` is the one-command path for operators who want it.
- **The healthcheck was wrong, and only running it said so.** The plan's probe
  is `urlopen('http://127.0.0.1:8630/api/health')`. That sends
  `Host: 127.0.0.1:8630`, the hosted guard requires `Host` to equal the
  configured public origin's host, and the container therefore reported
  **unhealthy while serving perfectly** — `curl localhost:8630/api/health`
  answered `{"status":"ok","mode":"hosted"}` at the same moment
  `docker inspect` said `unhealthy`. The probe now dials the loopback
  interface and *says* it is `$AGENTCAD_PUBLIC_ORIGIN`, and
  `test_the_healthcheck_sends_the_configured_host_header` pins all three parts
  so the naive version cannot come back. Under `restart: unless-stopped` plus
  any orchestrator that acts on health, the original would have been a restart
  loop on a healthy instance.
- **Residual, untested-by-me:** the `proxy` profile (Caddy + ACME) is
  syntax-checked by `docker compose config` but never brought up — it needs a
  public DNS name pointed at the host, which this machine does not have. The
  nginx snippet in the docs is likewise read, not run.

## Verification

Real, on this machine (macOS, Docker daemon available):

- `docker compose build` → `Image agentcad:local Built`, exit 0.
- `docker compose up -d` → container `cad_claude-agentcad-1`, health
  `healthy`; startup line
  `AgentCAD 0.1.0 — http://localhost:8630 (listening on 0.0.0.0:8630)`.
- `curl -s localhost:8630/api/health` → `{"status":"ok","mode":"hosted"}`;
  anonymous `GET /api/projects` → `401`; `GET /` → `200`.
- `docker compose exec agentcad agentcad admin user add nikita --admin`
  printed the enrolment link and the trust sentence; `POST`ing to that link
  returned `{"principal":"user:nikita","kind":"user","role":"admin",
  "mode":"hosted"}` with a `Set-Cookie`, and `GET /api/auth/session` read it
  back. `admin token add ci` minted a bearer that answered `200` on
  `GET /api/projects`.
- **A real kernel build inside the container**: `POST
  /api/projects/demo/parts` with a `Box` script returned the built part — so
  the six OCCT system libraries are right and `git` is present
  (`git version 2.47.3`, and `/data/projects/demo/.history` exists).
- `AGENTCAD_EXAMPLES=0` holds: the project list is only what was created.
- `docker compose down && docker compose up -d` → the account, the token, the
  project and the **live session cookie** all survive on the volume.
- `docker compose -f compose.yaml config --quiet` → exit 0 (the step slice 8
  adds to the PR job).
- `.venv/bin/python -m pytest tests/test_deploy_config.py
  tests/test_route_prefix.py -q` → **42 passed**.
- `make test` → **3640 passed, 1 skipped in 660.31 s** (8 workers, this
  machine). The branch baseline is 0190's **3532 passed, 1 skipped**, so
  slices 4–6 add **108** tests (41 + 25 + 42) and no regressions.
- `make test-portability` → **726 passed in 320.53 s**, which includes the
  three new `portability` rows (the two `fcntl`-blocked subprocess tests from
  slice 5 and `tests/test_mcp_remote.py`).
  - The run taken *before* this line existed reported `3635 passed, 5 failed`.
    Four of those five are the PRD-008/009/010/011 acceptance tests that
    require the **newest** changelog entry to cite a suite count — this entry —
    which is a bootstrap, not a regression. The fifth,
    `test_sketch_drag.py::test_the_cached_block_is_measurably_cheaper`, is a
    wall-clock FR6 budget: `pytest tests/test_sketch_drag.py -q` → **17
    passed** standalone (`diagnostics cached 3.81 ms/frame`), and it misses
    under 8-way co-load. That is the same flake class `ci.yml` already splits
    `test_sketch_bench.py` into a serial tail for; `test_sketch_drag.py` is
    not in that tail, which is worth a follow-up and is not this feature's.

**Not done: the browser pass.** The Chrome extension reports no connected
browsers (`list_connected_browsers` → `[]`), so the plan's step-10 screenshot
of the signed-in workbench was **not** taken, and slice 3's outstanding visual
verification is still outstanding. What is verified here is every HTTP contract
end to end against the real container, including that `/` and `/js/*` are
served. Stated rather than claimed.
