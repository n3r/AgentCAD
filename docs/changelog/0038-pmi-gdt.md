# 0038 — PMI / GD&T: tolerance model + drawing callouts

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Parts can now carry a validated PMI section — toleranced dimensions, datums,
and feature control frames — persisted in the manifest and rendered onto the
generated SVG drawings (roadmap "PMI / GD&T on drawings"). The tolerance
model lives on the part, not just the annotation layer, which is what the
stack-up slice builds on next.

## Changes

- **`agentcad/core/pmi.py`** (new): `validate_pmi` — explicit-field-tuple
  validation (materials-style): `dims` (linear targets width/height/depth or
  a positive nominal hole diameter; `plus`/`minus` ≥ 0, not both 0), `datums`
  (single letters A–Z, axis-aligned face names), `fcf` (five GD&T types,
  `tol_mm` > 0, datum refs required for position/perpendicularity/parallelism,
  validated against declared datums). Unknown keys rejected with
  known/unknown detail lists; `{}` clears.
- **Tools** (`tools_pmi.py` pack): `set_part_pmi` (validate → persist to the
  part entry → `project_changed` → post-state) and `get_part_pmi`. Applies to
  script AND reference parts (annotation, not geometry).
- **Drawing renderer** (`handlers/drawing.py`): optional `pmi` params render
  ± suffixes on the measured overall dims (width→front/top X, height→front Y,
  depth→top Y), newly drawn diameter callouts with leaders (`8x ⌀9.00
  +0.05/-0.10`) matched against detected top-view circles within 0.05 mm,
  boxed datum flags with leaders on the view bbox sides, and a stacked FCF
  column (Unicode GD&T symbols) above the title block. `detected` gains
  `pmi_rendered` counts and `pmi_warnings` (e.g. unmatched diameter targets).
  `tools_drawing.py` forwards the stored PMI into the kernel call.
- Docs: agent-api rows + generate_drawing amendment; part-authoring
  "Tolerances and GD&T (PMI)" section.

## Files

- `agentcad/core/pmi.py`, `agentcad/core/tools_pmi.py`
- `agentcad/kernel/handlers/drawing.py`, `agentcad/core/tools_drawing.py`
- `tests/test_pmi.py` — 11 tests (validation, persistence round-trip,
  SVG structural asserts, diameter matching + warning, reference parts)
- `docs/agent-api.md`, `docs/part-authoring.md`

## Notes

DXF output ignores PMI in this v1. Dimension values on drawings remain
measured from projected geometry — PMI only appends tolerances, so a driven
dim can never disagree with the geometry. FCF/datum placement is
deterministic with documented collision envelopes (unusually tall right
views or >6 frames could crowd; impossible with typical plate parts).
