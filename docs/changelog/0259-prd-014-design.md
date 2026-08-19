# 0259 — 2026-08-19 — PRD-014 Drawings v2: design spec + implementation plan

## Summary

Opens PRD-014 (Drawings v2 — the standards wrapper). The PRD moves to
`docs/prd/in-progress/` and this commit lands the design spec + slice plan under
`docs/superpowers/`, grounded in a full read of the drawing subsystem
(`handlers/drawing.py` 1143 LOC, `handlers/analysis.py`, `tools_drawing.py`,
`server/routes_drawing.py`, `drawings.js`, `pmi.py`, `manifest_merge.py`,
`history.py`).

## Scope decision

PRD-014 lists **PRD-015 (BOM) as a hard dependency**, and 015 is not built. But
the PRD's own MVP is scoped to not need it, and PRD-010/012/013/001 (holes,
configs, assembly, versioning) are all completed. So the buildable scope is:

- **Build now:** sheet formats + frame + auto-scale (FR1), data-driven title
  block (FR2), section views (FR6), detail views (FR7), centerlines/center marks
  (FR8), hole tables (FR9, PRD-010), config tabulation (FR10, PRD-012),
  deterministic PDF (FR11), byte-stability guarantee + test (FR12), machine-
  readable results (FR13).
- **Defer (needs PRD-015):** revision block (FR3), assembly views + balloons +
  BOM (FR4/FR5) — a `part_id`-omitted request returns a warning naming PRD-015,
  never a blank sheet.

## Key design decisions (see the spec for the rejected alternatives)

- **A display-list / backend split** is the foundation: `_build_svg` is
  refactored to build an intermediate list of primitives (Line/Polyline/Circle/
  Arc/Text/Hatch + a style enum) that both an `SvgBackend` and a new `PdfBackend`
  render, with **one central `fmt()` float formatter** — the determinism keystone
  (FR12). Rejected: writing SVG inline and parsing SVG→PDF (nondeterministic, a
  heavy dep).
- **Sheet templates as data** (`handlers/_sheets.py`): iso_a4..a0, ansi_a..d,
  landscape, default iso_a3; frame + zones (title/revision/table/view) from the
  template, not hard-coded coordinates. Uniform auto-scale from a preferred ladder,
  chosen scale printed + reported.
- **`section_outline` is greenfield** — today's `analysis._section` returns only
  `area_mm2`/`n_faces` and discards the geometry; the new handler keeps the
  section faces, projects per-body loops to 2D, and the drawing composes a
  section view with 45°/alternating hatching + cutting-plane arrows.
- **A minimal pure-Python deterministic PDF writer** (`handlers/_pdf.py`) over
  the display list — no new dependency (none exists for PDF), fixed object order,
  `fmt()` coordinates, no wall-clock/random id.
- **Determinism owned service-side:** `_drawing_version(service, project)`
  resolves `{ref, date}` from `history.head/log/tags` (tag-or-short-hash +
  committer date via `%cI`, content-hash + "-" with no repo) and passes strings
  into the kernel request; the kernel never reads git or the clock.
- **Title-block fields** live at a new top-level `manifest["drawing"]` (PMI
  precedent — raw dict, atomic merge, zero new merge code) via
  `set_drawing_fields`/`get_drawing_fields` (validated whitelist).
- **FR10 letter variables** are a render-time layer (PMI dims are lowercase ids
  today; only datums are letters), reusing the PRD-012 dim-table machinery.

## Slices (serial — they all touch `handlers/drawing.py`)

1. Foundation: display list + `SvgBackend` + `fmt()` + sheets + title block +
   drawing fields + `_drawing_version` + result skeleton (FR1/2/13).
2. Deterministic PDF + determinism test (FR11/12).
3. Sections + details (FR6/7).
4. Centerlines/center marks + hole tables (FR8/9).
5. Config tabulation (FR10).
6. Frontend controls (drawings.js).
7. Acceptance tests (AC1/2/4/6/7) + docs; AC3/AC5 deferred with PRD-015.

## Notes

Docs-only commit (design spec, plan, PRD move) — no product code changed, so the
suite is unchanged from `main`. `make test` — **4444 passed, 30 skipped** (the
committed `main` tree this branch forked from; verified on the PRD-013 close-out).
CI on the three-OS matrix is authoritative as slices land.
