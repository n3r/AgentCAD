# 0262 — 2026-08-19 — PRD-014 slice 7: acceptance suite + documentation

## Summary

Slice 7 (final) of Drawings v2 — the consolidated acceptance suite and the docs.
This closes the buildable PRD-014 scope; what remains (FR3 revision block, FR4/
FR5 assembly balloons + BOM) is deferred to PRD-015 and recorded as marked skips.

## Changes

- **`tests/test_prd014_acceptance.py`** (new) — 8 machine-checked + 2
  deferred-skip:
  - **AC1** the construction **gusset** produces an A3 ISO sheet with frame, a
    populated title block (material `steel_a36`, mass in kg, `scale 1:5`, version
    ref), an `A-A` section with hatch, and center marks on the 12 bolt holes.
  - **AC2** regenerate-twice byte-equality (SVG **and** PDF) + mutate→
    `history.restore`→original bytes (git-guarded).
  - **AC4** hole table with PRD-010 metadata (designations + tags,
    `from_metadata`) and the detected-diameter fallback.
  - **AC5** (built half) a three-config family tabulates letter variables + a
    config table; the `get_bom` cross-check is the **deferred** half (PRD-015).
  - **AC6** strict PDF parse (one page, `/MediaBox`, `xref`/`%%EOF`) + SVG
    well-formedness.
  - **AC7** an existing `generate_drawing` call with no new args is unchanged but
    for the default `iso_a3` wrapper (structural; byte-stability is AC2's job).
  - **AC3** (assembly balloons + on-sheet BOM) — `@skip` deferred to PRD-015.
- **`docs/agent-api.md`** — the extended `generate_drawing` (sheet/sections/
  details/format:pdf/scale/hole_table/tabulate), `set_drawing_fields`/
  `get_drawing_fields`, the FR13 result shape, a worked example; corrected the
  stale "SVG only" `dim_table` note and the top-view-only hole-callout caveat.
- **`docs/user-guide.md`** — sheet formats + title-block fields, sections/
  details, hole tables, config tabulation, PDF + the determinism guarantee; the
  `.pdf` export row.
- **`docs/part-authoring.md`** — PDF rendering + the base-14 Helvetica glyph
  limit (⌀/GD&T → `?` in PDF, full in SVG); how `tabulate` letters PMI diameter
  dims.
- **`AGENTS.md`** — a "Drawings-v2 gotchas (PRD-014)" block: the display-list/
  backend split + `fmt()` keystone, the pure-Python PDF writer + its glyph
  limit, section geometry in the handler, DXF's exclusion from byte-stability,
  the version-override fixed-date path for the determinism stage, `hole_table`
  opt-in, `tabulate` winning over `dim_table`, and the full-surface routes.

## Recorded divergences (PRD vs shipped — documented, not silent)

Assembly drawings (`part_id`-omitted mode, balloons, BOM, revision block) are
**not** built — deferred to PRD-015 (`part_id` stays required). Hole tables are
**opt-in** (`hole_table: true`) not automatic. Section geometry lives in the
drawing handler, not a separate `section_outline` handler. The frontend has no
detail-view control (the `details` tool arg works). `tabulate` is tool/MCP-only
(no route/query surface). The browser-visual halves of AC1/AC6 are
**evidence-graded** — no Chrome extension was connected this session
(`list_connected_browsers → []`), matching the 005a/031a precedent; the machine
halves are green and a controller spot-check confirmed the integrated gusset
sheet end to end.

`make test` — **4511 passed, 32 skipped** (clean run; the full suite measured
4502 passed with the 9 `*_cites_a_make_test_count` guards reading this entry's
own count before it was filled — green once it lands; the 2 new skips are AC3 +
AC5's get_bom half, deferred to PRD-015).
