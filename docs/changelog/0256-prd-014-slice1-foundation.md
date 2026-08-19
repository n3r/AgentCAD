# 0256 — 2026-08-19 — PRD-014 slice 1: display-list foundation, sheet formats, data-driven title block

## Summary

Slice 1 of PRD-014 (Drawings v2) — the foundation the remaining slices build on.
Refactors the drawing handler onto a **display-list / backend split**, adds
sheet-format templates, a uniform auto-scale, a data-driven title block, the
`drawing` manifest section + its tools, and the deterministic version seam
(FR1, FR2, and the FR13 result skeleton). No product behavior regresses; the
only intended output change is the new sheet frame + populated title block
(existing SVG parity otherwise).

## Changes

- **`agentcad/kernel/handlers/_draw_primitives.py`** (new, pure Python, no OCP):
  the primitive vocabulary — `Line`, `Polyline`, `Circle`, `Arc`, `Text`,
  `Hatch`, `Rect`, and a documented `Raw` escape hatch — a `Style` enum
  (`VIS/HID/THIN/CHAIN/DIM/HATCH/FRAME/TEXT/NOTE`), the central **`fmt()`**
  formatter (round-half-even to 3 dp, strip trailing zeros/dot, never `-0` —
  the determinism keystone every backend and coordinate goes through), and
  `SvgBackend` mapping each style to the exact stroke/dash the old inline
  constants used.
- **`agentcad/kernel/handlers/_sheets.py`** (new, pure data + geometry): the
  `SHEETS` table (`iso_a4..iso_a0`, `ansi_a..ansi_d`, all landscape, default
  `iso_a3` = 420×297 to preserve today's size), `SheetTemplate`/`Zone`
  (title/revision/table/view zones), the preferred `SCALE_LADDER`, and
  `scale_label()`.
- **`agentcad/kernel/handlers/drawing.py`**: `_build_svg` now builds a display
  list rendered by `SvgBackend` (`_edge_svg`→`_edge_prim`; `_linear_dim`,
  `_datum_flag`, `_fcf_frame`, `_arrow` emit primitives); frame + zones drawn
  from the selected `SheetTemplate` instead of hard-coded coordinates; uniform
  auto-scale (`_choose_scale`/`_largest_fit`) replaces the per-view scale; a
  data-driven title block (`_title_block`) renders all FR2 fields; new
  `sheet`/`scale`/`title` handler args; FR13 result skeleton
  `{path, size_bytes, sheet, scale, views, sections, detected, warnings}`
  (`sections: []` until slice 3). `_build_dxf` is untouched (DXF unchanged).
- **`agentcad/kernel/handlers/sheetmetal.py`**: decoupled from the drawing
  handler's removed string helpers — it now imports only the two geometry
  helpers (`_VIEW_DIRS`, `_view_bounds`) and keeps byte-identical local copies of
  its own `_edge_svg`/`_line`/`_text`/style constants. This was the worker-
  startup fix; the flat-pattern SVG bytes are unchanged.
- **`agentcad/core/tools_drawing.py`**: `generate_drawing` grows `sheet`/`scale`
  args + the title-block data wiring; new `set_drawing_fields`/
  `get_drawing_fields` (validated whitelist: company/author/project_code/
  approved_by/notes, length-capped, control-chars and unknown keys refused,
  stored at top-level `manifest["drawing"]` like PMI); the `_drawing_version`
  seam (tag-or-`head[:7]` + committer date via `history`, or `"wt-"+content
  hash` / `"-"` with no repo — never the wall-clock); mass/field helpers.
- **`tests/test_drawings_v2.py`** (new): 19 tests — `fmt()` canonical form,
  per-format viewBox, unknown-sheet validation, auto-scale ratio + overflow
  warning, title-block fields, `set_drawing_fields` validation + round-trip, and
  `_drawing_version` (no-repo content hash determinism + a real repo/tag path).

## Determinism fix caught by the full suite (the version-cell divergence)

The new title block renders a version line (`rev <ref>   <date>`), which broke the
geometry-CI **determinism stage** on `construction`/`prototyping` (5 failures the
drawing-only subset missed). Root cause, pinned from the diverging bytes: that
stage regenerates the drawing in the project **and** in a **git-stripped mirror**
copy, then compares bytes — the project resolves a git ref + commit date
(`rev 7499014   2026-08-19`) while the mirror has no git and falls back to the
content hash (`rev wt-594505e   -`). Same geometry, different *version identity* —
exactly the case the codebase already foresaw for DXF's `$TDCREATE`/GUIDs
(`_DXF_HINT`, "a fixed-date / CONST_GUID path in the drawing handlers").

Fix (the fixed-date path): `generate_drawing` grows an optional `version`
override `{ref, date}` that pins the title-block identity instead of deriving it
from git; the determinism stage (`core/checks.py`) passes ONE constant to **both**
sides, so it certifies the geometry, not the version cell. A new test
(`test_version_override_pins_the_title_block_and_is_byte_stable`) locks it. This
stage stays fail-closed for real geometry drift.

## Notes

Verified independently: drawing + sheetmetal + configs suites **68 passed**; the
broad `-k "drawing or draw or sheet"` selection **203 passed**; server-side
`import agentcad.core.tools_drawing` pulls in **no** OCP/build123d (the kernel
boundary holds); `fmt()` locked by test (`fmt(-0.0)=="0"`, `fmt(1.0)=="1"`,
`fmt(1.2345)=="1.234"`).

`make test` — **4464 passed, 30 skipped** (clean run on the committed tree; the
determinism fix above cleared the 5 `test_checks_ref.py` failures the first full
run surfaced, and the suite grew 4458→4464 with slice 1's tests).
