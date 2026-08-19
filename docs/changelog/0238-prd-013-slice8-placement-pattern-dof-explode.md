# 0238 — 2026-08-19 — PRD-013 slice 8: pattern editor + DOF fields + explode stub

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary

Eighth slice of Assembly v2: the placement card grows the human path for
structure. A **pattern editor** (linear/polar, count, spacing/angle) commits via
`set_pattern`; per-joint **DOF fields** for slider (offset) and planar (u/v/spin)
commit via `set_mate {dof}` and surface a clamp warning honestly; a derived
pattern/sub-assembly member shows what drives it instead of a lying gizmo. An
**Explode** slider is added to the viewport toolbar as a DISABLED Phase-2 stub.

## Changes

- `frontend/js/placement_model.js` (**new**, pure — no DOM/imports): `dofEditor`
  (choose the DOF fields from a mate's resolved `params` vocabulary —
  `{position}`→slider, `{u,v,spin}`→planar, `{angle}`→revolute — because the
  instance carries no connector-type field), `patternSpec` (the exact
  `set_pattern` payload: linear carries `step_mm`, polar `angle_step_deg`, count
  coerced to int≥1), `patternDraft` (editor values from a raw instance).
- `frontend/js/placement.js`: `render`/`build` now resolve the RAW base instance
  behind a selected member (`baseIdOf` strips `[i]` / `/…`); a slider/planar mate
  shows editable DOF fields (`dofFields` → `commitDof`, which toasts a
  `dof_clamped` warning), a revolute keeps the angle sweep; a derived member
  shows a "member of …" note (+ the pattern editor for a pattern member); a plain
  part base gains a **Pattern** section (`patternEditor` → `commitPattern`, with
  Add/Update/Clear). The panel signature includes the pattern + derived state so
  adding/clearing a pattern rebuilds the card.
- `frontend/index.html`: the disabled Explode slider (`#explode-range`) beside
  the rep-mode toggle, with a "coming in a later phase" tooltip.
- `frontend/css/app.css`: `.placement-pattern` / `.placement-dof` / `.explode-stub`.

## Files

- `frontend/js/placement_model.js` (new), `frontend/js/placement.js`,
  `frontend/index.html`, `frontend/css/app.css`
- `tests/test_frontend_placement.py` (new)

## Notes

- **DOF type from params, not a type field.** A stored mate is only
  `{connector, to_instance, to_connector, params}`; the connector type lives in
  the part script. The resolved `params` vocabulary is type-specific, so the
  editor keys off it — the one decision worth a node test (`dofEditor`).
- **Browser pass is EVIDENCE-GRADED / extension-gated.** No Chrome browser is
  connected (`list_connected_browsers` returned `[]`, as for many prior
  sessions), so the visual pass — a `×8` bolt circle that expands, a sub-assembly
  that expands read-only, a slider DOF field that drives the joint, the HUD
  counts — is NOT claimed here. The machine-checkable core is the pure model
  (`test_frontend_placement`) and the route/tree tests; `node --check` is clean
  on the edited JS.
- The explode slider is a wired-but-disabled seam only — `explode_assembly` is
  Phase 2 (asserted absent in the acceptance module).
- Measured: `tests/test_frontend_placement.py` 5 passed; `node --check` clean on
  `placement.js` and `placement_model.js`.
