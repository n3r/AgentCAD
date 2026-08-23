# 0335 — 2026-08-23 — PRD-027 slice 4: `bulk_part_op` as one undo step, `remove_parts`, the kernel-free dashboard

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated; Claude)

## Summary

Bulk operations over a multi-selection (FR5) that land as **one** manifest
write and **one** `project_changed` publish — so one git snapshot and one undo
entry (AC4) — plus the project dashboard payload (FR6) that reads manifests
and in-memory build state only. Design §4/§6.

## Changes

- **`agentcad/core/navigation.py`** — `OPS = (material, tag, untag, folder,
  export, delete)`, `MAX_BULK = 500`, `MAX_BULK_EXPORT = 50`, `BulkExecutor`:
  - `run(proj, part_ids, op, args)` → `{op, ok, applied, results: [{id, ok,
    error?, …}], undo_label}`. Ids are de-duplicated in order and bounded
    1..500; an unknown op, a bad material, a bad export format or malformed
    args refuse **before any write**; a missing part is a per-item
    `notfound_error` row (the house wire spelling, not the spec prose's class
    name — one payload, one spelling).
  - Metadata ops (`material`/`tag`/`untag`/`folder`) plan inside
    `manifest_scope(store, proj)` → `service._lock` (outer → inner, the
    `update_parts_meta` precondition; planning moved inside the locks after a
    self-review found the TOCTOU), write through **one**
    `store.update_parts_meta`, publish `project_changed {reason:
    "bulk material ×6"}` with `part` omitted (the snapshot label becomes the
    undo label), then `parts_meta_changed {part_ids, fields}`. A `material`
    change then runs `service.rebuild_after_write` per part **outside** the
    locks; rebuilds publish only `rebuild_*`, so they add no undo entries —
    mutation-checked (a per-part publish flips the test's `1` to `7`).
  - `delete` → `store.remove_parts(part_ids, force)`: per-item
    `conflict_error` with `details.instances` when instances use the part
    unless `force`, which drops those instances **in the same write** and then
    runs a dangling-mate fixpoint (an instance mated to a removed instance is
    dropped too — `set_instances` refuses dangling mates, so a raw write would
    have left the assembly unreadable); `_status`/`_config_status` evicted
    like `service.delete_part`; scripts unlinked after the save; one publish.
    Claim semantics match single `delete_part` (turn-lock only).
  - `export` → per-item `service.export_part` (each a kernel round trip,
    hence ≤ 50 ids), no publish, `undo_label: null`.
- **`agentcad/core/project.py`** — `ProjectStore.remove_parts(proj,
  part_ids, *, force=False) -> {"removed", "errors"}`; validates everything
  first, one `save_manifest`.
- **`agentcad/core/tools_navigation.py`** (bulk block) — `bulk_part_op
  {project, part_ids, op, args?}`.
- **`navigation.dashboard(service)`** + **`GET /api/dashboard`**
  (`routes_navigation.py`, member-only): `{projects: [{name, path, n_parts,
  n_instances, mass_g|null, failing, last_modified|null, thumb|null}]}` —
  `mass_g` only when **every** part has ok metrics carrying a number (a
  reference part without one → `null`, never a partial sum), `failing` =
  error states, `last_modified` = ISO-8601 UTC of `project.json`'s mtime
  (`null` when absent), `thumb` only when `thumbnails.has_thumb` finds an
  existing file/mesh (no render). Kernel spy and a `render_acm` that raises
  both prove zero calls.

## Files

- `agentcad/core/navigation.py`, `agentcad/core/project.py`, `agentcad/core/tools_navigation.py`, `agentcad/server/routes_navigation.py` — as above
- `tests/test_tools_navigation.py` (56), `tests/test_routes_navigation.py` (21) — new

## Notes

Measured: 20 projects × 25 parts dashboard in **3.1–3.3 ms** (AC bar 500 ms).
Review (Opus) approved with minors deferred to the final review: the
route-registration test uses the naive `app.routes` walk rescued by a
tautological `or` (should use `flatten_routes`); the dashboard re-parses each
manifest `list_projects` already read; `_bulk_ids` validates elements before
bounding the count; a metadata bulk parses the manifest twice (plan + write,
both under the lock); an export-level `DiskBudgetError` becomes N identical
rows. Tool count is now 88 (91 with `[fem]`) — the docs strings move in
slice 7 (eight files: the seven in the plan plus
`tests/test_prd012_acceptance.py`'s asserted string).

`make test` — see 0336 (slices 4 and 5 landed in one commit; the count is cited there).
