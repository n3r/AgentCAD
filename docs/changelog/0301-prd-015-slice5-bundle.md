# 0301 — 2026-08-20 — PRD-015 slice 5: the reproducible release bundle

## Summary

Slice 5 of BOM & release management — the release **bundle** (FR10-11): finalize
assembles a directory + zip of STEP, drawings, BOM, flat patterns, README, and a
sha256 `artifacts.json`, all built at the tag and byte-stable on a rebuild.

## Changes

- **`agentcad/core/releases.py`**: `build_bundle(service, project, rev)` opens
  `materialized_service(service, project, "release/<rev-lower>")` (tree = the tag,
  **cold cache so exports build for real** — this is where real per-tag mass
  comes from) and produces into the worktree's `exports/`: STEP per script part
  (`export_part`) + the assembly (`export_assembly`, skipped when no instances);
  PDF+SVG drawings per part via `generate_drawing(version={ref, date: <tag commit
  date>})` — the PRD-014 override that pins the title block; `flat_pattern` SVG
  per sheet-metal part (a solid errors → caught, skipped, noted); `bom.csv` +
  `bom.json` (run **after** the geometry builds, so masses are warm/real);
  `README.md`; `artifacts.json`. Everything is **copied out** to the real
  project's `exports/releases/<rev>/` inside the `with` block (before teardown),
  then zipped. `release_finalize` calls `build_bundle` inline (best-effort — a
  bundle failure records an error but does not un-release); `_persist_bundle`
  publishes `project_changed` so the bundle stamp doesn't dirty the next
  release's `working_tree_clean` gate (a regression caught + fixed).
- **`agentcad/core/tools_releases.py`**: the idempotent `release_bundle
  {project, rev}` tool (refuses a non-released rev; overwrites the dir).
- **`tests/test_release_bundle.py`** (new, 5): every expected artifact present;
  `artifacts.json` sha256s match the files; flat pattern only for sheet-metal
  (solid skipped + noted); README carries the name + gate report; **AC6** — a
  rebuild at the tag is reproducible.
- **`tests/test_release_finalize.py`**: an autouse fixture stubs `build_bundle`
  so the finalize suite stays fast (its bundle coverage lives in the new file).

## Reproducibility (FR11) — honest about the two normalized fields

Determinism: the pinned drawing `version` override, sorted/`fmt`-free BOM, a
clock-free README (tag commit date only), sorted `artifacts.json`. Every
`deterministic`-class artifact (SVG+PDF drawings, bom.csv/json, README, flat
patterns) is byte-identical on a rebuild. **STEP is the one normalized class**:
`_normalize_step_bytes` neutralizes (1) the ISO-10303-21 `FILE_NAME` write
timestamp and (2) — found by diffing two rebuilds — OCCT's
`NEXT_ASSEMBLY_USAGE_OCCURRENCE` first field, a **process-global session counter**
the shared kernel increments between runs. Neither is geometry; both are named in
the README and `artifacts.json.classes`. `artifacts.json` = `{rev, tag, generated
(tag date), files: [{path, sha256, bytes, class}], classes}`, sorted, never lists
itself.

## Notes

Verified: 29 bundle+finalize+release tests (incl. AC6 reproducibility); OCP
boundary clean (`releases.py` stays pure Python — geometry is the ephemeral
service's kernel, never an OCP import here). This resolves the slice-2 limitation:
`get_bom {ref}` reads cold-cache mass as `unbuilt`, but the bundle warm-builds at
the tag for real mass.

Also fixes a slice-6 **presence tripwire** the full suite caught (and the route-
only run missed): `test_presence` counts the literal `X-Agent-Id` string in
`api.js`, and slice 6's new `bomCsvUrl` comment contained the token ("no
X-Agent-Id needed") — reworded to "no identity header needed" so the count is 6
again (the actual header usages were always correct).

`make test` — **4619 passed, 39 skipped** (green total; the contended run
measured 4609 with the 9 self-referential count guards + the one presence
tripwire above, all now resolved — the presence fix is comment-only and the rest
of the tree is untouched; suite grew across slices 5+6).
