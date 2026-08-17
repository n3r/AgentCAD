# 0203 — PRD-012 slice 5: the merge reaches a configuration's parameter, and a dangling selection is reported

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude

## Summary
The merge half of PRD-012 (FR12, Decision 9). Before this change
`parts.<id>.configs` merged as **one atomic value**, so two branches adding
*different* configurations conflicted and two branches editing *different*
parameters of one configuration conflicted — FR12 violated in both directions
at once. The structure-aware driver now merges the map per **name** and, inside
a configuration, per **parameter**; `apply_choices` writes a six-segment
resolution into the map instead of beside it; and the new
`manifest_merge.config_problems` reports the damage a clean key-wise merge can
still do — a *selection* (an instance's `config`, a part's `active_config`) that
one branch kept while the other removed the configuration it names. Pure Python
plus one call site in the merge orchestrator: no kernel, no server, no tool.

## Changes
- **`_PART_ENTRY_DICTS = {"configs": ("params",)}`** beside `_PART_SUBDICTS` —
  one descriptor, not a fork of `_merge_entry`. A part field named there whose
  value is keyed on every present side goes to the new
  **`_merge_keyed_entries`**, which walks the map per name and, for a name
  present on all three sides, hands the entry to the existing `_merge_entry`
  with `subdicts=("params",)`. So `label`/`description` merge as whole values,
  `params` merges per parameter, and add/add of one name or delete/modify still
  conflicts on the whole configuration (the entry is the conflict unit, exactly
  as for a part or an instance).
- **The per-name `_keyed` guard is load-bearing** — the map-level analogue of
  `_entry_list`'s guard. A map entry that is not an object (a hand-edited
  `"m": 5`, an authored `null`) merges *whole*; without the guard
  `_merge_entry`'s `_as_dict` rewrites it to `{}` — a **clean** merge that
  silently destroys data.
- **`_write_keyed_entry`** teaches `_write_entry` the same shape, so a
  resolution at `parts.<id>.configs.<name>.params.<param>` writes into the map.
  Measured before the fix: `apply_choices` wrote the literal flat key
  `"configs.m.params.w"` beside the real map. It goes through the **recorded
  path segments**, never a re-split of the dotted key (X13's rule; a config
  name is `CONFIG_RE`-clean today, but the driver does not depend on that), and
  it refuses with a `ValidationError` rather than conjuring a configuration
  that is not in the document.
- **`parts.<id>.active_config` and `assembly.instances.<id>.config`** need no
  driver work: a selection is a single value and merges whole. That is also
  what makes them the failure mode below.
- **`config_problems(manifest)`** beside `package_problems`, same problem shape,
  same reason for existing: the selection lives in a *different key* from the
  map it names, so one branch removing a configuration while the other selects
  it touches no common key and merges clean. Two kinds, deliberately
  asymmetric — `dangling_instance_config` (an instance bound to a configuration
  the merged part no longer declares) is **blocking**, because the binding is
  that instance's whole parameter set and it now resolves to nothing;
  `dangling_active_config` is a **warning**, because an unknown active
  configuration resolves as base (Decision 3) and someone only has to re-pick.
  Silent on a config-free project, on `{}`, and on a healthy family; an
  instance whose *part* is missing is skipped, because `_integrity`'s
  `dangling_instance` already says so and saying it twice in two vocabularies
  is worse than saying it once.
- **`merge._validate`** calls it once and splits the result: the blocking rows
  join `report["integrity"]` next to `_manifest_shape`, `_integrity` and
  `package_problems`; the warnings append to the report's existing
  `report["warnings"]` list (already there for the interference cap, already
  rendered by `frontend/js/merge.js`'s `reportBlock`, so no consumer needed a
  change). `_summarize` already reads `problem.get("instance") or
  problem.get("message")`, so a new row summarizes as
  `dangling_instance_config box_1` in an `allow_invalid` commit message.
- **Docs:** the module docstring's key-space table gains the five rows plus the
  paragraph that says *why* a configuration is not atomic while a
  `packages_lock` entry is (a lock entry is content-determined, so half of one
  verifies against nothing; a configuration is a set of independent parameter
  values — the argument that already makes `params` per-key, one level deeper).
  The same rows land in the three docs that assert the key space.

- **Fix round 1 (review):** `docs/agent-api.md`'s validation-pass paragraph now
  names `kind: "dangling_instance_config"` (blocking, with `instance`/`part`/
  `config`) and the non-blocking `active_config` warning, so the documented
  agent-facing surface matches what a merge can now return. `_PART_SUBDICTS`'
  companion `_PART_ENTRY_DICTS` is **threaded** through `_merge_entry_list` /
  `_merge_entry` / `_write_entry` (`{}` for instances) instead of being read as
  a module global — read globally, a forward-compatible *instance* field named
  `configs` got the keyed-map treatment and merged deep, contradicting the
  docstring's "instance fields … whole value". `_write_keyed_entry`'s final
  fallback now **raises** `ValidationError` instead of joining ≥ 2 leftover
  segments into a dotted flat key (unreachable from a recorded conflict, and
  the one line left in the new code that could write a key nothing reads back).
  `tests/test_configs_merge.py` carries `@pytest.mark.timeout(600)` (two real
  kernel builds against a 120 s global default). Two new tests, red against
  e61b7e9 and green after: the instance-field probe and the deep-path refusal.

## Files
- `agentcad/core/manifest_merge.py` — `_PART_ENTRY_DICTS`,
  `_merge_keyed_entries`, the `_merge_entry` branch, `_write_keyed_entry`, the
  `_write_entry` branch, `config_problems` + `_declared`, and the docstring's
  key-space table
- `agentcad/core/merge.py` — `_validate` calls `config_problems` and splits it
  into `integrity` (blocking) and `warnings`
- `tests/test_manifest_merge.py` — the `# ---- PRD-012: the configs map`
  section (12 tests) and the `config_problems` section (4 tests), plus two
  `KEY_CLASSES` rows (`parts.flange.configs.m.params.bolt_d`,
  `parts.flange.configs.m.label`) and a `configs` map on `sample()`'s flange so
  the four truth-table tests cover a configuration leaf as well
- `tests/test_configs_merge.py` — **new**, 2 integration tests driving two real
  branches through `merge_branch` (the
  `test_packages_index.py::test_a_real_merge_blocks_on_the_package_hybrid`
  shape): a bound instance lands in `validation.integrity` and blocks; a stale
  `active_config` is a `validation.warnings` string on an `ok: true` merge
- `docs/agent-api.md` — the validation-pass paragraph names the new `integrity`
  kind and the new warning (fix round 1)
- `docs/architecture.md` — the merge walkthrough's step 3 gains a key-space
  table and step 5 names both problem reporters and the warning
- `docs/packages.md` — the `manifest_merge` paragraph gains the configs rows
  and the atomic-vs-per-parameter contrast
- `docs/superpowers/specs/2026-08-09-branching-version-control-design.md` —
  four rows in the key-space table (`configs.<name>`, its fields, its params,
  `active_config`), `.config` on the instance-fields row, and the paragraph on
  why a selection can dangle

## Notes
- **Verification.** Focused, written test-first:
  `uv run pytest tests/test_manifest_merge.py tests/test_configs_merge.py
  tests/test_merge.py tests/test_branches.py tests/test_versioning_api.py -q`
  — **220 passed** in 233 s. Red first, in three steps: the driver tests failed
  15/91 with the old atomic `configs` (the 82 pre-existing tests green
  throughout), then 4/102 on `AttributeError: module … has no attribute
  'config_problems'`, then the two orchestrator tests failed on a merge that
  *landed clean* with a dangling binding and an empty `warnings` list — which is
  the bug this slice exists for. Also re-run for the shared report shape:
  `tests/test_packages_index.py -k merge` (2 passed) and
  `tests/test_prd001_acceptance.py` (7 passed).
  Full suite (run by the controller over slices 2 and 5 together): `make test`
  — 3382 passed, 7 skipped; the one red row,
  `tests/test_sketch_diagnostics.py::test_the_full_budget_completes_the_same_analysis`,
  is PRD-009's wall-clock analysis-budget assertion, untouched here, and passes
  alone (37/37) — the run overlapped a second full suite on the same machine.
- No existing test was edited. `sample()` — a shared builder, not a test — now
  carries a one-configuration family; every pre-existing assertion over it
  still holds because an identical map on all three sides merges clean.
- `frontend/js/merge.js` renders an integrity row as
  `${kind}: [instance, part, mate]`, so a `dangling_instance_config` shows as
  `dangling_instance_config: box_1 → box` and its `config`/`message` are not
  displayed. That degrades honestly (a `package_*` row already shows an empty
  tail) and the UI belongs to a later slice, so it is deliberately untouched
  here.
- Scope kept narrow on purpose: this slice adds no tool, no route and no
  validation of a configuration's *contents* at merge time. A merge can still
  produce a configuration whose parameter is out of range — the publish gate
  and `set_part_configs` are the boundaries that refuse that, and re-validating
  a merged family against a `PARAMS` spec would need a kernel `inspect` inside
  the pure driver.
