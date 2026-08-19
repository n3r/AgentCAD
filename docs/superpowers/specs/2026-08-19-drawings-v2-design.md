# PRD-014 Drawings v2 — design spec (the standards wrapper)

Grounded in a full read of the drawing subsystem (handlers/drawing.py 1143 LOC,
handlers/analysis.py, tools_drawing.py, server/routes_drawing.py, drawings.js,
pmi.py, manifest_merge.py, history.py). This spec records the decisions and the
rejected alternatives; the slice plan is the sibling file
`docs/superpowers/plans/2026-08-19-drawings-v2.md`.

## Scope (what this PRD builds now)

Dependencies satisfied on `main` today: PRD-010 (holes), PRD-012 (configs),
PRD-013 (assembly), PRD-001 (versioning). **PRD-015 (BOM) is NOT built.** So the
buildable scope is the PRD's stated MVP **plus** the Phase-3 items whose deps are
met, deferring only the PRD-015-gated half:

- **Build now:** FR1 sheet formats + frame + auto-scale, FR2 title block, FR6
  section views, FR7 detail views, FR8 centerlines/center marks, FR9 hole tables
  (PRD-010), FR10 config tabulation (PRD-012), FR11 PDF, FR12 determinism, FR13
  machine-readable results.
- **Defer (needs PRD-015):** FR3 revision block (reads release records), FR4/FR5
  assembly views + balloons + BOM table. A `part_id`-omitted (assembly) request
  returns a `validation_error`-style warning naming PRD-015, never a blank sheet.

Non-goals unchanged from the PRD: no interactive drawing editor, no full Y14.5
auto-dimensioning, **no DXF byte-parity** (DXF stays geometry-exchange, ignores
PMI/tables; `ezdxf` stamps fresh timestamps/GUIDs so DXF is excluded from the
byte-stability guarantee — SVG and PDF only).

## 1. The primitive/backend split (Decision 1 — the foundation)

Today `_build_svg` (drawing.py:729-1090) writes SVG strings inline with ad-hoc
float precision (`:.3f`, `:.2f`, `_fmt_tol`), and `_build_dxf` is a separate
top-view-only path. There is **no shared primitive layer**, and the PDF backend
(FR11) plus the byte-stability guarantee (FR12) both require one.

**Decision:** refactor the sheet composition to build an intermediate
**display list** of primitives, then render it through pluggable backends:

- Primitives (a small frozen vocabulary): `Line(x1,y1,x2,y2,style)`,
  `Polyline(pts,style,closed?)`, `Circle(cx,cy,r,style)`, `Arc(...)`,
  `Text(x,y,s,style,anchor,size)`, `Hatch(loops,angle,pitch,style)`. Styles are
  a small enum (`VIS`, `HID`, `THIN`, `CHAIN`, `DIM`, `HATCH`, `FRAME`, `TEXT`).
- `SvgBackend.render(display_list) -> str` reproduces today's SVG (golden updated
  once, then stable) and adds the sheet frame/title block/tables.
- `PdfBackend.render(display_list, meta) -> bytes` (Decision 7).
- One **central float formatter** `fmt(x)` — round-half-even to 3 dp, strip to a
  canonical form, never locale-dependent, never `-0`. Every backend and every
  primitive coordinate goes through it. This is the determinism keystone.

Rejected: keep writing SVG inline and parse SVG→PDF. The PRD explicitly rejects
an SVG→PDF dependency for nondeterminism; and a display list is what makes the
hatching, section, and detail composition tractable (they are just more
primitives) rather than string-splicing into a 1143-line function.

Rejected: a full retained-mode scene graph. Overkill — a flat ordered list with
styles is enough; z-order is insertion order.

## 2. Sheet templates as data (Decision 2 — FR1)

A versioned `SHEETS` table (a new `handlers/_sheets.py`, imported by the drawing
pack — pure data + geometry, no OCP):

```
SHEETS["iso_a3"] = SheetTemplate(
    w=420, h=297, margin=10, frame_inset=6,
    title_block=Zone(x, y, w, h),      # bottom-right
    revision_block=Zone(...),          # top-right (empty until PRD-015)
    table_zone=Zone(...),              # right column, for hole/config tables
    view_area=Zone(...),               # remaining area for projected views
)
```

Formats: `iso_a4|iso_a3|iso_a2|iso_a1|iso_a0`, `ansi_a|ansi_b|ansi_c|ansi_d`, all
**landscape**, default `iso_a3` (preserves today's 420×297). The frame + zones
are drawn from the template — no hard-coded coordinates in `_build_svg` anymore.

**Auto-scale (FR1):** views are scaled **uniformly** (not today's per-view
independent scale). Pick the largest scale from the preferred ladder
`[100:1,50:1,20:1,10:1,5:1,2:1,1:1,1:2,1:5,1:10,1:20,1:50,1:100,1:200]` such that
all requested views' combined bounding boxes fit `view_area`. The chosen scale
prints in the title block (`"1:2"`); a `scale` override is honored and, if it
overflows, a `warnings` entry says so (risk item: crowding). The result reports
`scale`.

Rejected: user-editable template files in v1 (custom title blocks) — deferred per
the PRD open question; the data table + fields whitelist leaves the door open.

## 3. Title block, data-driven (Decision 3 — FR2)

The title block renders from three sources, none of them wall-clock:

- **PartRecord / metrics:** part id + `label`, `material`, `mass` (from
  `service.get_metrics` → `mass_g`), units (mm).
- **Manifest `drawing` section** (Decision 4): company, author, project_code,
  approved_by, notes.
- **Version identity (Decision 5):** version ref + date, computed service-side.
- Sheet size + scale from Decision 2.

The kernel handler receives all of these as **plain strings in the request** and
renders them; it never reads git or the clock. Determinism is owned service-side.

## 4. The `drawing` manifest section + its tools (Decision 4 — agent surface)

Title-block fields live at a new **top-level** `manifest["drawing"]` (parallel to
`name`/`units`), following the PMI precedent (a raw manifest dict, not a
`PartRecord` field). It merges through `manifest_merge`'s default `_merge_atomic`
fallback with **zero new merge code** — a whole-object atomic merge. (We
deliberately do NOT special-case it for field-wise merge in v1; title-block edits
are rare and single-author. Noted as a follow-up if concurrent field edits become
real.)

New tools (in `tools_drawing.py`, the existing pack):

- `set_drawing_fields {project, fields}` — **validated whitelist**: `company`,
  `author`, `project_code`, `approved_by`, `notes` (all strings, length-capped,
  control-chars refused). Unknown keys → `validation_error` naming them. Empty
  string clears a field; writing `{}` leaves the section absent.
- `get_drawing_fields {project}` — returns the section with defaults filled.

Rejected: per-part drawing fields. Title block identity is project-level
(company/project code) plus part data already on the record; a per-part override
is not in the PRD and doubles the surface.

## 5. Deterministic version ref + date (Decision 5 — FR2/FR12)

A service-side helper `_drawing_version(project) -> {"ref": str, "date": str}`:

- `commit = history.head(path)`; `rows = history.log(path, limit=1)` gives the
  HEAD committer date via `%cI` (already the log format). `tags = history.tags`
  → if a tag's `commit == commit`, `ref = tag.name`, else `ref = commit[:7]`.
  `date = <committer-date, date portion>`.
- No repo / unborn: `ref = "wt-" + sha256(manifest + scripts)[:7]` (a stable
  content hash of the working tree — same content ⇒ same ref), `date = "-"`.

This is passed into the kernel request as `version_ref`/`version_date`. Because
the date is the **commit** date (or "-"), never `datetime.now()`, two runs at the
same project state render identical bytes (FR12), and `project_restore` to a
snapshot reproduces the original bytes (AC2).

Rejected: rendering the wall-clock date "for humans". It breaks FR12 and AC2
outright; the version date is the honest, reproducible field.

## 6. Section & detail views (Decision 6 — FR6/FR7)

**`section_outline` is greenfield** — today's `analysis._section` computes only
`area_mm2`/`n_faces` and discards the geometry. A new kernel handler
`section_outline` (added in `handlers/drawing.py`, which already owns the SVG edge
primitives; it needs no `service`):

- Input: `script, params, plane ∈ {xy,xz,yz}, offset_mm`.
- `sec = b3d.section(shape, section_by=Plane(...offset...))`; for each solid body
  separately, take the section faces, extract outer + inner wires, project to the
  plane's 2D coordinates, return `{bodies: [{loops: [[[x,y],...], ...]}],
  bbox, warnings}`. A plane that misses the solid → `warnings:["section plane
  misses the solid"]` and empty bodies (FR-error contract: warning + empty view,
  never a silent blank sheet).

The drawing composition then renders a **section view** as a new view type: the
outline polylines (VIS style) + `Hatch(loops, angle=45°, pitch)` per body with
**alternating angles** across bodies (45°, 135°, …), labeled `A-A`, with
cutting-plane arrows + label drawn on the parent view (FR6). Multiple sections get
`A-A`, `B-B`, … in order.

**Detail views (FR7):** `details:[{view, center_mm:[x,y], radius_mm, scale}]` —
draw a labeled circle (`A`, `B`…) on the parent view at the center/radius, and a
magnified detail view (the parent view's edges clipped to the circle, scaled up)
placed in the view area. Reuses the projection already computed for the parent
view (no extra kernel build) — pure 2D clip + scale of the display list.

Kernel affinity: any extra section builds are issued from `tools_drawing.py`
pinned to the same `part_id` worker (the CLAUDE.md drawing trap — never re-add the
fan-out); the section handler runs in the same worker as the main projection.

## 7. Deterministic PDF writer (Decision 7 — FR11/FR12)

A minimal, pure-Python PDF writer `handlers/_pdf.py` (no new dependency —
`pyproject.toml` has none for PDF, and adding reportlab/cairosvg risks
nondeterministic output). It consumes the **same display list** as the SVG
backend and emits PDF content-stream operators directly:

- One page per sheet, `MediaBox` from the sheet size (mm→pt), a single content
  stream of path/line/text operators, one embedded base-14 font (Helvetica) for
  text (no font embedding needed — Helvetica is a standard PDF font).
- **Determinism:** fixed object numbering order, `fmt()` for every coordinate, no
  `/CreationDate` OR a `/CreationDate` derived from `version_date` (fixed string),
  no `/ID` randomness (a fixed or content-derived `/ID`), no compression (or fixed
  zlib level with a deterministic input). Two renders of the same display list ⇒
  identical bytes.

`format: pdf` flows through `generate_drawing` (tool) and a new
`GET /api/projects/{proj}/parts/{part_id}/drawing.pdf` route (in
`routes_drawing.py`, through `_drawing_result`). SVG and DXF keep working; PMI
renders in SVG and PDF, DXF ignores it (unchanged).

Risk & tripwire: PDF determinism is easy to lose. The FR12 test regenerates twice
and asserts equal sha256 for **both** SVG and PDF; it is the CI tripwire (also run
by PRD-004's geometry-CI determinism check).

## 8. Centerlines, center marks, hole tables (Decision 8 — FR8/FR9)

**Center marks (FR8):** today circles are detected in the **top view only**
(drawing.py:80-89 names PRD-014 as the fix). Extend `_detect_circles` to run per
view (each view's visible edges), draw a center mark (small cross) at each
detected circle center and each PRD-010 hole center in **every** view; coaxial
hole runs seen edge-on in side views get a **centerline** (thin CHAIN linetype)
spanning the run.

**Hole tables (FR9):** inside the drawing handler, reuse `_records_on(shape)`
(the PRD-010 `holes.records(shape)` seam — there is no manifest/service hole API;
records live only on the rebuilt shape). Build a table in `table_zone`: tag
(`A1,A2,…`), X/Y from the view datum corner, and the standard designation
(`M5x0.8 - 6H ⌴ Ø9.5×5.4`) from `hole_standards.designation_for_record`; print
the tag at each hole. Without metadata, fall back to detected diameter groups,
each row marked `detected`. The result carries `hole_table: rows`.

Layout note: today the dim-table owns the one clear rectangle (264,18)-(414,60).
The format-parametric `table_zone` (Decision 2) replaces that single hard-coded
rectangle; hole/config tables and the dim table share the zone by stacking, and
larger sheets have more room. A4 with a large table overflows → `warnings` + a
row cap (as today's `_MAX_TABLE_ROWS`), never a silent truncation.

## 9. Config tabulation (Decision 9 — FR10)

`tabulate: true` reuses the existing PRD-012 dim-table machinery (`_measure_table`
already measures per-config X/Y/Z extents, timeout scaled per row in
`tools_drawing.py`). Extension: assign **letter variables** (A, B, …) to
PMI-declared dims at **render time** (PMI dims are lowercase ids today — only
datums are letters; the letter mapping is a render-time layer, not stored), and
render a config table (config × variable value, plus per-config mass). The drawn
views use the **active** config. Result carries `config_table: rows`. Degrades
cleanly (no configs ⇒ no table, a `warnings` note if `tabulate` was requested).

## 10. Result contract (Decision 10 — FR13)

`generate_drawing` returns:
```
{path, size_bytes, sheet, scale, views, sections, detected,
 pmi_rendered?, balloons?, hole_table?, config_table?, warnings}
```
`balloons` stays absent until PRD-015 (assembly drawings). Everything an agent
needs to verify coverage without reading pixels ("is every hole tabled?",
"what scale did it pick?").

## 11. Frontend (Decision 11 — drawings.js)

The `#drawing-modal` header grows: a sheet-format `<select>`, view checkboxes,
`Section…`/`Detail…` controls (plane+offset / center+radius), and a
`Download PDF` link beside SVG/DXF. All reuse the existing `previewSeq` stale-
response guard and the zero-extra-request `configOf` pattern. No new JS modules
(the PRD says so). PDF download mirrors the DXF POST-then-toast shape plus a
stream, or a direct `GET …/drawing.pdf`.

## 12. Errors, determinism test, acceptance (Decision 12)

House contract via `_drawing_result`: `validation_error` for bad sheet/section
specs (details name the offending entry); a section plane missing the solid →
warning + empty view. Kernel-class errors → 502 with the type intact.

Determinism test (FR12/AC2): regenerate the construction-gusset A3 sheet twice →
equal sha256 for SVG and PDF; mutate a param then `project_restore` the snapshot →
original bytes reproduced. AC1 asserts the SVG contains frame + populated title
block + a sectioned A-A view with hatching + center marks on the bolt holes.
AC4 (hole table with/without PRD-010 metadata) — two tests. AC6 (PDF strict parse
+ page count; SVG preview zero console errors — browser half evidence-graded if
the extension is unavailable). AC7 (existing calls unchanged but for the default
sheet wrapper; golden updated once). AC3/AC5 deferred with PRD-015/full config
tabulation.

## 13. Pack boundaries (Decision 13)

Extend in place, never the cores: `handlers/drawing.py` (+ `handlers/_sheets.py`,
`handlers/_pdf.py`, and the `section_outline` handler), `tools_drawing.py` (new
`set/get_drawing_fields`, `sheet`/`sections`/`details`/`tabulate`/`format:pdf`
args, `_drawing_version` service seam call), `routes_drawing.py` (`drawing.pdf`
route), `drawings.js`. No edits to worker.py/tools.py/app.py/service.py — the
`_drawing_version` helper is a new **method added by the tool pack via the
bound-method-wrapping idiom** (tools_structure/tools_holes precedent) if it must
live on the service, or simply a free function in `tools_drawing.py` taking
`service` (preferred — no service mutation).

## 14. Approaches considered and rejected (summary)

- **SVG→PDF via a library** — rejected for nondeterminism + a new heavy dep; a
  shared display list + direct PDF operators keeps bytes in our hands.
- **Storing hole records in the manifest** — rejected; they live on the rebuilt
  shape by design (PRD-010), re-derived per kernel call. FR9 rebuilds, cheaply,
  in the same pinned worker.
- **Assembly views now (FR4)** — rejected/deferred; kernel handlers can't reach
  `service`/mate resolution, and balloons/BOM need PRD-015. It needs a new multi-
  instance request payload; out of this PRD's scope.
- **Wall-clock date in the title block** — rejected; breaks FR12/AC2. Version
  commit date (or "-") is the reproducible truth.
- **DXF byte-stability** — rejected as a non-goal; `ezdxf` is nondeterministic and
  DXF ignores the sheet wrapper anyway.
