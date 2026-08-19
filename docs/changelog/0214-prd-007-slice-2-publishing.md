# 0214 — PRD-007 slice 2: the pin, the muzzled build service, and the management API

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
Publishing for share links: an authenticated member turns a part at a version
into a token, the pin **copies** the script bytes out of the project into the
state dir (so a live link never drifts), and the default variant is pre-warmed
in a muzzled build service that never touches the owner's project.

## Changes
- **New `agentcad/core/share_build.py`** — `ShareBuilder`:
  - `pin(service, project, part_id, ref)` resolves the ref with the PRD-001
    discipline (a tag pins as a tag; a branch, or an omitted ref, auto-tags the
    current head into an immutable version), reads the script + material +
    stored params at the commit via `history._run_bytes` (`cat-file blob`, no
    worktree), content-addresses the raw bytes to `script_sha`, writes
    `scripts/<sha>.py`, and warms the default variant. Returns
    `{script_sha, ref, material, part_id, default_variant_key}`.
  - The build service is a single lazily-constructed muzzled `AgentCADService`
    rooted at `<state-dir>/publications/build/` (the `_ephemeral_service`
    recipe: `bus.on_publish=None`, then after `build_registry`,
    `store.branch_resolver=None` and `store.write_guard=None`). Builds run a
    `dataclasses.replace` record (visitor params + pinned material) through
    `_build_with(..., status_key=None)` — no badge, no state written.
  - Content-addressed build project (`s<38 hex>`, one part `part`), so two
    identical parts share one build and cache.
  - Kernel-free read helpers for the viewer: `mesh_path` / `metrics_for`
    (404-if-absent, computed from the store layout — restart-safe, never
    builds), `params_spec` (a sidecar cached at pin), `script_text`.
  - `ensure_share(service)` installs `service.publications` + `service.share_builder`
    from `appmode.state_dir()`, called only from a route pack — never from
    `AgentCADService.__init__`.
- **New `agentcad/server/routes_share.py`** (`/api`, authenticated, inert in
  local mode) — `POST /api/share` (mints the token, returns `{url, pub_id}` 201,
  secret once), `GET /api/share?project=` (owner's links, coarse counters, never
  the token), `DELETE /api/share/{pub_id}` (immediate revoke; a link that is not
  the caller's is a 404, no oracle). Publishes `share_changed`.
- **New `agentcad/core/tools_share.py`** — `share_create`/`share_list`/`share_revoke`,
  registered **only** when `security.current_config()` is hosted (the `whoami`
  precedent). Thin wrappers over the same store + builder.
- **`agentcad/core/publications.py`** — `create` gained an optional `material`
  field on the record (slice-4 readiness; the customizer rebuilds with it).

## Files
- `agentcad/core/share_build.py` — new: pin + muzzled build + kernel-free reads
- `agentcad/server/routes_share.py` — new: management API pack
- `agentcad/core/tools_share.py` — new: hosted-only tools
- `agentcad/core/publications.py` — record carries `material`
- `tests/test_share_publish.py` — new: URL/pub_id, no-token listing, immediate
  revoke, auto-tag, unknown part 404, bad export 422, anonymous 401, edit-immune
  pin, tool registration in/out of hosted mode
- `tests/test_share_isolation.py` — new: publish leaves the owner tree
  byte-unchanged, the variant cache is in the state dir, the build project is
  invisible to `list_projects`

## Notes
Verification: `pytest tests/test_share_publish.py tests/test_share_isolation.py`
→ **13 passed**; `pytest tests/test_hosted_surface.py tests/test_versioning_api.py
tests/test_auth_routes.py tests/test_security_guard.py` → **130 passed**
(2026-08-18). The anonymous surface is untouched this slice (management routes
are `/api`, private). The capped/rate-limited public `/variant` + `/download`
and the in-flight semaphore are slice 4; the object model is shaped for them.
Phase-2 residual: a project-custom material is not copied into the
content-addressed build project, so the pin falls back to the default material's
density for such parts.
