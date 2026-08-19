# 0230 — 2026-08-19 — PRD-013 (Assembly v2) design: structure, patterns, joints, URDF

## Summary

The design round for Assembly v2 — the biggest daily-driver PRD: sub-assembly
instancing, instance patterns, richer joints (slider/planar in MVP), simplified
representations for 1k+ instances, and URDF export. 15 numbered decisions,
9 TDD slices, every claim grounded in the existing `mates.py`/`resolve_mates`
seam. The MVP holds to the PRD's own split; ball joints, gear couplings,
exploded views, and the interference broad-phase are Phase 2 seams designed but
not built.

## The decisions that shaped it

- **Pattern expansion is one point, replace-not-add.** `mates.resolve` calls a
  new `mates.expand` that replaces a patterned base id with `<id>[0..N-1]`
  (never alongside it), so every one of the ten consumers already routing
  through `_resolved_instances` counts N automatically — mass, interference,
  BOM, `get_assembly`. `sweep_motion` (the one raw-instance consumer) also
  calls `expand`. Rotation composition happens in the kernel via build123d
  `Location` (one Euler convention); an off-axis polar-mated base falls back to
  a rigid image with a `pattern_polar_offaxis` warning.
- **Cross-project sub-assembly reads cannot write a source — structurally.**
  `write_guard` fires only in `write_script`/`save_manifest`/`imports_dir(
  write=True)`, and depth-first resolution calls none of them on a source; a
  store-spy test pins it. Cross-project cycles are a `validation_error` naming
  the cycle; a non-exported interface connector is a `validation_error`.
- **The manifest bump is nearly free.** Per-id instance entries already merge
  whole-value, so `pattern`/`assembly` on an instance need zero merge-code
  change; only project-level `assembly.interface`/`couplings` need the key-wise
  `_ENTRY_DICTS` treatment plus a referential check. Old files load via existing
  `.get`/`setdefault` tolerance — no migration, `SCHEMA_VERSION` unchanged
  (AC8). A pattern edit renders as a one-line `count: 6→8` proposal diff.
- **A correctness requirement the PRD omitted:** `inertia_tensor_g_mm2` is
  about the global origin, so URDF export must **parallel-axis-shift the tensor
  to each link's COM** or every link's inertia is wrong. Caught in design.
- **`simplified_rep`** is a distinct `handlers/simplify.py` (scipy `ConvexHull`)
  — a real proxy mesh, not a coarser LOD tolerance — lazily produced on a
  `?lod=simplified` sidecar miss and served through the unchanged tier
  mechanism. `core/urdf.py` and the interference broad-phase are OCP-free.

## Decisions taken (not escalated)

- **Unpinned sub-assembly refs warn, they do not refuse** — the PRD's own risk
  section already sets this direction (warn for MVP; require pins in releases
  via PRD-015 and CI via PRD-004). The schema reserves `assembly.{version,
  config}` for the Phase 3 pinning.
- **One PR, not the design's proposed A/B split** — PRD-011 shipped 14 slices
  in one PR and the review gate handled it; every PRD this session has been one
  PR. The A/B seam (slices 1–4 server core · 5–9 UI/URDF/scale) is held in
  reserve if the diff proves too large for a quality review at PR time.

## Divergences from the PRD (spec §14)

Clamp-not-raise for out-of-limit DOFs (a behaviour change from today's raise,
matching PARAMS clamping); a small merge-code change for key-wise interface/
couplings (the PRD assumed the driver already did it); an added `set_pattern`
verb; couplings schema present in MVP but resolution in Phase 2; the URDF
parallel-axis shift; AC3 (1k-instance fps) graded as evidence (kernel-side
`len(flat)==1000` + node-tested InstancedMesh id-mapping machine-checked, the
fps number honest) on the 005a/007/031a precedent.

## Files

- `docs/superpowers/specs/2026-08-19-assembly-v2-design.md` — 15 decisions
- `docs/superpowers/plans/2026-08-19-assembly-v2.md` — 9 TDD slices
- `docs/prd/in-progress/PRD-013-assembly-v2.md` — moved from `pending/`
- `docs/changelog/0230-prd-013-design.md` — this entry

## Notes

Docs only; no production code. Slices order: manifest schema + pattern
expansion → sub-assemblies → joints → simplified_rep/instancing → URDF → UI →
docs/acceptance. Extension points only (a new `handlers/simplify.py`, the
connectors resolver, `tools_structure`/`tools_urdf`, `routes_structure`); no
worker/tools/app/service core touched. Coordination: PRD-006 (sandboxing) is
in flight on another branch touching kernel confinement — 013 stays in packs,
low conflict; main will be merged into 013 when 006 lands. Last full-suite
measurement: 4068 passed, 1 skipped (0229).
