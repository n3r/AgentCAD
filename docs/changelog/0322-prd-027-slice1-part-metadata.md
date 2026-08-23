# 0322 — 2026-08-23 — PRD-027 slice 1: part folder/tags, instance folder, `set_part_meta`, the single-RMW bulk write

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated; Claude)

## Summary

The manifest metadata every other PRD-027 slice builds on: `folder` and
`tags` on parts, `folder` on assembly instances, store write methods
(including the one-read-one-write bulk method that later makes a bulk
operation a single undo step), exposure through `get_project`/`get_part`, the
new `tools_navigation` pack with `set_part_meta`, and the `parts_meta_changed`
event. Design: `docs/superpowers/specs/2026-08-23-project-navigation-scale-design.md`
§1/§5.

## Changes

- **`agentcad/core/navigation.py` (new)** — the grammar module:
  `FOLDER_SEGMENT_RE` (1–8 `/`-joined segments of
  `[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}`, no leading/trailing space per segment,
  `fullmatch`, stored verbatim), `TAG_RE` (`[a-z0-9][a-z0-9_.-]{0,31}`),
  `normalize_folder` (`None`/`""` → root), `normalize_tags` (strip, lowercase,
  de-duplicate first-seen, ≤ 32, names the offending raw tag; `None` is
  refused — every caller means "unchanged" by it), `folder_matches`
  (case-insensitive **segment** prefix: `a/b` is under `a`, not under `a/bc`;
  an empty query matches everything — ruling; a non-string query raises).
- **`agentcad/core/model.py`** — `PartRecord.folder`/`tags`,
  `InstanceSpec.folder`; every `to_manifest` writes them **only when set**, so
  an untouched project serializes byte-identically (no `folder: null`, no
  `tags: []` — a test compares file bytes after a real no-op write).
- **`agentcad/core/project.py`** — `get_part` reads them through a new
  single-sited `_part_record(entry)`; `instances()` reads `folder` and
  `set_instances` validates it (the store is the one place all four instance
  writers reach); `update_part_meta(proj, part_id, *, folder=_UNSET, tags=None)`
  under `locks.write_scope(part_id)`; **`update_parts_meta(proj, edits)`** —
  validate every id and every edit key (`{folder, tags, material}`, unknown
  keys refused) before any write, `write_guard(proj)` once per part inside its
  own `write_scope`, then one mutation pass and **one** `save_manifest`. Its
  docstring carries two contracts the store cannot enforce: the caller holds
  `manifest_scope(store, proj)` then `service._lock` (outer → inner, the
  `tools_configs.set_instance_config` order — taking `manifest_scope` inside
  the store would invert it), and a `material` edit obliges the caller to
  publish `project_changed` and `rebuild_after_write` per part (material feeds
  `_cache_key`).
- **`agentcad/core/service.py`** — `get_project` parts and the `get_part`
  detail carry `folder`/`tags`; `set_assembly` passes `folder` through.
  `tools_structure._set_assembly` and `mates._member` (pattern members inherit
  their base's folder) do the same — the five `InstanceSpec(` sites are all
  accounted for, because `set_instances` is a full replace from `to_manifest`.
- **`agentcad/server/routes_assembly2.py`** — the gizmo PATCH accepts `folder`
  (validated; `null` = root) and reads its body through the strict
  `routes_configs._json` (a non-object body is a 422 before any subscript).
  The mate-driven refusal now fires only when `position`/`rotation_deg` is in
  the body: organizing (or recolouring) a mated instance is not a transform.
- **`agentcad/core/tools_navigation.py` (new)** — loads at `nav` (after
  `materials`, before `packages`/`proposals`), registers **no gate provider**
  (asserted with `ast`), one tool so far: `set_part_meta {project, part_id,
  folder?, tags?}` (`folder` omitted = unchanged, `""`/`null` = root; `tags`
  omitted = unchanged, `[]` = clear; a call with neither is a read and publishes
  nothing). Publishes `project_changed` (`reason: "meta"`) **then**
  `parts_meta_changed {project, part_ids, fields}` — one undo step. The schema
  declares `folder` as `"string"` because `ToolRegistry`'s validator cannot
  hash a JSON-schema type list (verified); an explicit `null` still reaches the
  handler (the validator skips `None` on an optional argument).

## Files

- `agentcad/core/navigation.py`, `agentcad/core/tools_navigation.py` — new
- `agentcad/core/model.py`, `agentcad/core/project.py`, `agentcad/core/service.py`, `agentcad/core/tools_structure.py`, `agentcad/core/mates.py`, `agentcad/server/routes_assembly2.py` — as above
- `tests/test_navigation_meta.py` — new, 106 tests (grammar tables, byte-identity, guard scope `["cube", "pin", None]`, the five instance writers, PATCH body/mate rules, event order, one undo step through a real `AgentCADService` + git history)

## Notes

Review (Opus) found the two docstring contracts above missing and three
minors (unknown edit keys, PATCH body shape, `folder_matches` non-string);
all fixed and re-reviewed clean. Deferred minors for the final review: the
guard→save TOCTOU inside the bulk method is inherent (one `write_part_var`
contextvar cannot hold N scopes) and bounded by the project-level guard at
`save_manifest`; `_apply_meta` appends keys in mutation order (cosmetic
diff churn vs `to_manifest` order). The colour-only PATCH on a mated
instance answering 200 (was 409) is a deliberate, pinned widening.

`make test` — **5491 passed, 50 skipped** (13m00s; a run on the same tree with this entry still carrying a count placeholder measured 5481 passed + the 10 self-referential count-guard tests red on the placeholder alone — they pass once the number is in, which is what 5491 counts).
