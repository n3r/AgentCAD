# 0190 — PRD-012 slice 2: the configuration build path

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude

## Summary
Slice 2 of PRD-012 (Configurations) turns slice 1's resolution into geometry:
one build path with two entry points (`_build_with` / `_ensure_config_built`),
a per-configuration mesh, export and cache identity, and the configuration
state exposed on `get_part` / `get_project`. A part without configurations is
unchanged — same cache keys, same `_status` slots, byte-identical `rebuild_*`
payloads.

## Changes
- **`service._build_with(proj, record, *, affinity, status_key, config=None)`**
  is the extracted body of `_rebuild`: same cache key, same
  `<key>.metrics.json` sidecar read/write, same `rebuild_started` /
  `rebuild_finished` / `rebuild_failed` events, same return shape. Two things
  are now parameters instead of derived from `part_id`: the `_status` slot to
  write (**`None` writes none**) and the pool `affinity`. `config` only tags
  the event payloads (`**({"config": config} if config else {})`), so a base
  rebuild's dicts stay byte-identical — the frontend keys on `ev.part`.
- **`service._rebuild(proj, part_id)`** keeps its signature byte-for-byte
  (`tools_specs` / `tools_holes` rebind it with two-positional wrappers, and
  `tools_specs` / `tools_holes` / `tools_packages` do the same to `get_part`)
  and becomes a thin shell: the stored record, `affinity=part_id`, the existing
  2-tuple `_status_key`.
- **`service._record_for(proj, part_id, config=None)`** — the stored record
  (the working state) or a DERIVED record
  (`dataclasses.replace(record, params=record.config_params(config),
  active_config=None)`) whose params are the pure configuration map. Every
  record-driven helper (`_cache_key_for`, `_content_signature`,
  `_solid_densities`, `_shape_item`) then works unchanged on a configuration
  build, which is why no `config=` argument is threaded through them. It
  refuses a reference part (no PARAMS) and an undeclared name
  (`details.declared`), so the refusal happens before any build.
- **`service._ensure_config_built(proj, part_id, config)`** — `_ensure_built`
  for a pure configuration, memoized in a **separate**
  `_config_status[(lock_key, part_id, config)]` dict (checked exactly like
  `_status`: recorded key equals the freshly computed pure key *and* the
  `.acm` exists) and **never** in `_status`. So `get_project.parts[].state`,
  `get_part.status` and the tree badge keep meaning *the working state*, the
  three tests and one pack that index `_status` as a literal 2-tuple are
  untouched, and a memo hit **publishes nothing** — the livelock guard from
  design Decision 4 (two instances bound to different configurations would
  otherwise republish `rebuild_finished` on alternate `get_assembly` calls and
  drive the browser's refresh loop forever).
- **Sweeps:** `_forget_status(lock_key)` and `delete_part` clear
  `_config_status` alongside `_status` (prefix `(lock_key, part_id)`), so a
  merge's staged worktree entries and a deleted part's variants do not outlive
  them.
- **`mesh_info(..., *, config=None)` / `ensure_mesh(..., *, config=None)`** —
  the same `{path, key, lod}` for one configuration's mesh. An undeclared name
  raises (from `_record_for`); it never falls back to the working state,
  because a caller asking for a size that does not exist must not be handed a
  different one.
- **`export_part(..., *, config=None)`** → `exports/<part>_<config>.<fmt>`
  (base naming unchanged), pure resolution, and the configuration echoed in
  the result only when one was asked for. Names are dot- and slash-free by
  grammar, so no extra sanitizing. Wired through the core `export_part` tool
  schema (new `config` string property), the `POST
  /api/projects/{p}/parts/{id}/export` route (`config=body.get("config")`) and
  `agentcad export --config`.
- **Exposed state:** `get_part` gains top-level `configs` (always present,
  `{}` when the part has no family) and `active_config` (always present,
  `None` at base), and `status.diverged` / `status.diverged_params` from the
  new module-level `_divergence(record)`; `get_project`'s part rows gain
  `configs` / `active_config` read straight off the manifest entry.
  Divergence is **semantic**: an override whose value equals the active
  configuration's is not divergence (the geometry, and the cache key, are the
  pure configuration's), a parameter the configuration does not set at all
  counts, and a dangling `active_config` resolves as base and never diverges.
- **Tests** (`tests/test_configs.py`, additions only): two kernel-free
  `_record_for` tests, plus `TestFlangeFamily` — a `@pytest.mark.timeout(600)`
  class over a class-scoped template project (`FLANGE_SCRIPT` +
  `THREE_SIZE_CONFIGS`, cloned per test with `clone_test_service`) covering
  three configurations → three distinct masses and keys, the silent memo hit,
  `_status` staying 2-tuple keyed (and both dicts swept), `delete_part`
  forgetting the variants, `config` on a configuration build's events and
  absent from a base rebuild's (pinned as a whole dict), two configurations
  sharing one cache key with **no kernel `build`** (AC5), per-configuration
  mesh keys, the refusal to fall back, the base export unchanged, the
  `flange_l.step` naming plus the echoed config, the tool schema and the HTTP
  route forwarding it, and the four divergence cases.

## Files
- `agentcad/core/service.py` — `_UNSET`, `_config_status`, `_record_for`,
  `_config_status_key`, `_ensure_config_built`, `_build_with` (extracted),
  `_rebuild` (thin shell), `mesh_info` / `ensure_mesh` / `export_part`
  `config=`, `get_part` / `get_project` fields, `_divergence`, the two sweeps
- `agentcad/core/tools.py` — `export_part` schema gains `config`; the lambda
  forwards it
- `agentcad/server/app.py` — the export route forwards `body.get("config")`
- `agentcad/cli.py` — `agentcad export --config` (usage line + argument)
- `docs/agent-api.md` — the `export_part` tool row documents `config`
- `tests/test_configs.py` — 18 new tests (2 module-level + `TestFlangeFamily`)
- `docs/changelog/0190-config-build-path.md` — this entry

## Notes
- No new tool, route or UI yet (slices 3+). `set_active_config` does not exist
  here, so the divergence tests write `active_config` through
  `store.update_part_entry` and layer overrides with `set_params`.
- Nothing new entered `_cache_key`'s payload: configuration awareness is
  `record.effective_params` and nothing else, so
  `tests/test_solids.py`'s pinned key bytes and every pre-PRD-012 on-disk cache
  entry stay valid.
- Accepted, per design Decision 4: the `tools_specs` / `tools_holes` wrappers
  around `_rebuild` do **not** decorate pure-configuration builds. Per-config
  spec results are produced deliberately in `build_configs` (Decision 8), and
  per-config hole metadata is not a PRD-012 deliverable.
- Verification: `uv run pytest tests/test_configs.py -q` — 42 passed (24
  slice-1 + 18 new); `uv run pytest tests/test_service.py tests/test_specs.py
  tests/test_holes.py -q` — 168 passed, 2 skipped (the frozen wrapper
  signatures); `make test` — 3382 passed, 7 skipped (10:53 on 8 workers); the one red row, `tests/test_sketch_diagnostics.py::test_the_full_budget_completes_the_same_analysis`, is PRD-009's wall-clock analysis-budget assertion, untouched here, and passes alone (37/37) — the run overlapped a second full suite on the same machine.
