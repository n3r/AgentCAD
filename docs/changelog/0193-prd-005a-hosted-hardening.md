# 0193 — PRD-005a slice 5: hosted-mode hardening and the bind interlock

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (with Nikita Fedorov)

## Summary

Closes the carry-overs design Decision 9 names — the things a loopback bind was
silently providing that authentication alone does not replace. `POST
/api/projects/open` and the absolute-path form of `import_cad_file` are refused
in hosted mode, a presence beacon may only name the caller's own namespace,
`agentcad serve` gains `--host` behind the mode interlock, and the bundled
examples can be switched off. Every refusal ships with the test that proves it
does **not** fire in local mode.

## Changes

- **`agentcad/server/app.py`** — `POST /api/projects/open` raises `AuthzError`
  (403) in hosted mode, naming the mode. It registers an arbitrary absolute
  path on the server as a project; on loopback that could only reach the
  operator's own disk, which is exactly what made it safe.
- **`agentcad/core/tools_import.py`** — `import_cad_file` refuses a `source`
  that is an absolute path (or contains `/`) in hosted mode. A pack, not a
  core, and the whole change is one guarded early return.
- **`agentcad/server/routes_presence.py`** — `_check_beacon_scope`: in hosted
  mode a `pagehide` beacon's `client_id` must be the caller's own principal or
  a `<device>` under it. The boundary is the `/`, not a string prefix —
  `user:nikita2` starts with `user:nikita` and is a different person. Local
  mode is a no-op early return: the header the body stands in for is itself
  self-asserted there, so refusing the body would break the real beacon and buy
  nothing.
- **`agentcad/cli.py`**
  - `_serve_bind(args, mode)` — `--host` / `$AGENTCAD_HOST` / `127.0.0.1` and
    `--port` / `$AGENTCAD_PORT` / the stored config, then
    `appmode.check_bind`. A `ModeError` becomes `SystemExit(2)` with the
    message on stderr. Checked **before** `_build_service`, so a refusal does
    not first spawn ~0.5 GB of kernel worker.
  - `_resolve_mode_or_exit()` split out of `_security_config()`, which now
    accepts the already-resolved mode, so `cmd_serve` resolves once.
  - `_projects_dir(args)` — one helper honouring `$AGENTCAD_PROJECTS_DIR`,
    applied at all six call sites.
  - `_register_examples` returns early on `AGENTCAD_EXAMPLES=0`.
  - `cmd_serve` binds `host` and prints the public origin when there is one,
    while still *opening* the loopback URL.
- **`tests/test_hosted_hardening.py` (new)** — 25 tests: each refusal, each
  refusal's local-mode negation, the beacon's four boundary cases, the bind
  interlock in both directions, the env-vs-flag precedence, a nonsense
  `AGENTCAD_PORT`, the mount-time-capture regression, and two
  `portability`-marked subprocess tests that import the server (and drive the
  store) with `fcntl` blocked at `sys.meta_path`.

## Files

- `agentcad/server/app.py` — the `/api/projects/open` refusal
- `agentcad/core/tools_import.py` — `_refuse_a_host_path_in_hosted_mode`
- `agentcad/server/routes_presence.py` — `_check_beacon_scope`
- `agentcad/cli.py` — `_projects_dir`, `_resolve_mode_or_exit`, `_serve_bind`,
  the `--host` flag, the examples skip, the `cmd_serve` rewrite
- `tests/test_hosted_hardening.py` — new

## Notes

- **Divergence from the plan, stated: `import_cad_file` is a 200 with an error
  payload, not a 403.** The plan's test asserts `status_code == 403`, but
  `ToolRegistry.call` converts every `AppError` into
  `{"error": {type, message, details}}` with a 200 *by design* — "expected
  failures come back as `{"error": {...}}` payloads so agents can read and
  react to them" (`core/tools.py`). Asserting 403 would have meant asserting
  against the house contract, so the test pins `authz_error` in the payload
  plus the fact that **nothing was ingested**, and a sibling test pins that a
  relative uploaded filename still reaches its real `validation_error` — the
  refusal must be about reading the server's disk, not about importing.
- **The plan's `import_cad_file` argument is `source`, not `path`.** The
  plan's sketch used `path`; the tool's schema says `source`
  (`core/tools_import.py`). Tests follow the code.
- **Residual, named rather than papered over: the `open_project` TOOL is not
  refused.** FR19 names the route and `tools_import.py`, and both are closed.
  The registry-level `open_project` tool lives in `core/tools.py`, which the
  plan's global constraints forbid this feature from editing, and there is no
  unregister seam. It is reachable only by an *authenticated* member, who can
  already run arbitrary Python by writing a part script (Decision 1), so it
  adds nothing to the threat model — but it is a real gap in FR19's letter and
  belongs in slice 8's gotcha section.
- **`AGENTCAD_PROJECTS_DIR` was a documented variable nothing read.** FR24
  enumerates it and slice 6's compose file sets it; before this it was inert,
  so the container's projects would have landed in `/data/home/AgentCAD/
  projects` while the docs said `/data/projects`. Applied in one helper so
  `agentcad new` over `docker compose exec` cannot land in a different tree
  from the one the server serves.
- **The `fcntl` portability assertion is honest about its mechanism.** The plan
  words it as "`authstore` is imported lazily inside `create_app`'s caller";
  it is not — `server/security.py` imports it at module scope. The property
  still holds because `core/authstore.py` guards the import
  (`try: import fcntl / except ImportError: fcntl = None`), and the test pins
  the property (the server imports, and the store still works in-process)
  rather than the mechanism.
- **The beacon rule is captured at MOUNT time, not read per request.**
  `security.current_config()` falls back to a process-global slot, so a router
  reading it per request would make a *local* app enforce the hosted rule the
  moment a hosted app existed in the same process — the trap changelog 0190
  records for `routes_auth`. `create_app` installs before it mounts packs, so
  the captured value is exactly this app's, and
  `test_a_local_app_built_after_a_hosted_one_does_not_inherit_the_rule` pins
  it.
- Verification: `.venv/bin/python -m pytest tests/test_hosted_hardening.py -q`
  → **25 passed**; with the neighbouring suites (`test_presence`,
  `test_server`, `test_cli_admin`, `test_hosted_surface`, `test_security_guard`,
  `test_auth_routes`, `test_tokens`, `test_mcp`, `test_locks`, `test_claims`,
  `test_tools`, `test_reference`) → **229 passed**.
- **And against a real `agentcad serve`**, hosted, `--host 0.0.0.0`, scratch
  config/state/projects on port 8643: `serve --host 0.0.0.0` in *local* mode
  exited 2 with the interlock message; anonymous health was
  `{"status":"ok","mode":"hosted"}`; `POST /api/projects/open {"path":"/etc"}`
  returned the `AuthzError`; `import_cad_file` with `/etc/hosts` returned
  `authz_error`; a beacon naming `user:anya/browser:bbbbbbbb` returned the
  `ValidationError`; and the same signed-in person's heartbeat took a claim
  reading `{"holder":"user:nikita/browser:7f3a1b2c","holder_kind":"human"}`.
  `AGENTCAD_PROJECTS_DIR` put the project in the scratch tree, and
  `state/auth/*.json` came out `0600` under a `0700` directory.
