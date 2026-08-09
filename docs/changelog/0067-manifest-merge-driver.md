# 0067 — Structure-aware three-way merge driver for project.json

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

First slice of PRD-001 (branching version control): a pure, kernel-free,
git-free three-way merge of `project.json` at CAD key granularity, plus its
resolution helper. Slice 3's merge orchestrator consumes `merge_manifests` and
`apply_choices` as finished components; nothing else in the tree calls them
yet, so this lands with zero behavior change to the running product.

## Changes

- **New module `agentcad/core/manifest_merge.py`** (stdlib only; imports only
  `ValidationError` from `core.model`). Public surface:
  - `merge_manifests(base, ours, theirs) -> (merged, conflicts)` — `ours` is
    the **target** branch, `theirs` the **source**. The merged manifest always
    carries ours' value at every conflicted key, so it stays a loadable
    document while conflicts are outstanding.
  - `apply_choices(merged, conflicts, choices) -> (merged, remaining)` —
    `{"take": "ours"|"theirs"|"base"}` or `{"value": …}` per conflict key;
    unknown keys and malformed choices raise `ValidationError`. Neither
    argument is mutated.
  - `CONFLICT_KEYS = ("kind", "key", "base", "ours", "theirs")` — the field
    order of a conflict entry; `base` is omitted for add/add, and the deleting
    side of a delete/modify conflict is `null`.
- **Key granularity** per the design spec: top-level scalars and any unknown
  top-level section merge whole-value; `parts` and `assembly.instances` merge
  entry-wise by `id` (add/remove at the entry, otherwise field-wise);
  `parts.<id>.params.<name>` and `parts.<id>.solid_materials.<key>` merge per
  key; `parts.<id>.pmi` and `materials.<id>` are atomic; instance `position` /
  `rotation_deg` merge as whole vectors, never component-wise.
- **Truth table** is the classic three-way rule over one whole value, with an
  explicit absent sentinel so delete/delete, delete/unchanged, add/add and
  delete/modify all fall out of the same four-line function.
- **Value identity is type-aware**: comparison uses
  `f"{type(v).__name__}:{json.dumps(v, sort_keys=True)}"`, so `6` and `6.0`
  (and `True` and `1`) are different values, matching how `_normalize_param`
  stores them and how a byte comparison of `project.json` would see them.
- **Deterministic output ordering**: merged keys, part entries, instance
  entries and fields come back in ours' order, then theirs-only additions in
  theirs' relative order — the property AC5's byte-identical tag round-trip
  depends on. Merged values are deep-copied, so the result shares no state
  with the inputs and the inputs are never mutated.
- **New `tests/test_manifest_merge.py`** — 62 pure-Python cases (no kernel
  fixture, no git, no filesystem): the full truth table parametrized over five
  key classes (scalar, part field, param, instance field, material); add/add,
  add-on-one-side, delete/delete, delete/unchanged and delete/modify
  parametrized over all three keyed sections; FR8's two cases; pmi and
  materials atomicity; vector atomicity; int-vs-float; ordering determinism and
  input immutability; unknown top-level sections; empty and missing sections;
  the "clean merge, broken references" case the validation pass must catch; and
  `apply_choices` take/value/partial/error paths.

## Files

- `agentcad/core/manifest_merge.py` — new pure merge driver (new file)
- `tests/test_manifest_merge.py` — new unit suite for it (new file)
- `docs/changelog/0067-manifest-merge-driver.md` — this entry
- `docs/superpowers/specs/2026-08-09-branching-version-control-design.md` — PRD-001 design spec (new)
- `docs/superpowers/plans/2026-08-09-branching-version-control.md` — PRD-001 implementation plan (new)
- `docs/prd/in-progress/PRD-001-branching-version-control.md` — moved from `pending/` (feature in progress)

## Notes

Referential integrity is deliberately **not** the driver's job: ours deleting
`parts.flange` while theirs adds an instance referencing it touches no common
key, so it merges clean. That is asserted as a test here; the Slice 3 kernel
validation pass is the backstop that blocks it.

Sections fall back to a whole-value merge when their shape is not what the
schema promises (e.g. `parts` is not a list of id-bearing objects), so a
future or corrupt manifest degrades to a coarse merge instead of raising.

Verification: `uv run pytest tests/test_manifest_merge.py -q` → 62 passed in
0.04 s; `make test-fast` → 345 passed, 1 skipped in 27.23 s (283 pre-existing
passes unchanged, no test file edited).
