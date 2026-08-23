# 0315 — PRD-017 slice 8: acceptance tests + docs (close-out)

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
The close-out slice of PRD-017: one acceptance test per criterion (AC1–AC7,
machine halves) graded against the shipped surface, and the documentation the
interop pack owes — the agent-facing export/import contracts and the fidelity
matrix, the human-facing export menu / import preview / platform matrix, the
architecture notes for the two kernel packs and the schema-mutation mechanism,
and one condensed trap list each in `AGENTS.md` and `CLAUDE.md`.

## Changes
- `tests/test_prd017_acceptance.py` — 13 tests, one (or a named pair) per AC,
  reusing the slice suites' fixture helpers by import rather than re-spelling
  them (`make_assembly_step`, `parse_glb`, the 3MF readers, `BORED_BLOCK`):
  - **AC1** — a part toleranced through `set_part_pmi` (2 dims, a datum, a
    flatness *and* a perpendicularity frame) exports AP242 and `read_step_pmi`
    gets every entity back in **millimetres**. The perpendicularity frame is
    load-bearing and the finding is new: a datum **nothing references** is
    written to the file (`#437 = DATUM('','',#4,.F.,'A')`, measured) but
    OCCT's reader only materializes datum labels reachable from a geometric
    tolerance's datum system, so `read_step_pmi` answers `datums: []` for it.
    Grading "every entity survives" therefore needs the datum referenced —
    which is what a datum is for. The FreeCAD viewer half is recorded as a
    per-release manual check in `docs/user-guide.md`, not stubbed.
  - **AC2** — the 3-product / 7-occurrence fixture lands as exactly
    `["ball", "bracket", "pin"]` with all seven occurrences as instances, the
    spike's composed-pose case (`pinpair_2_pin_2` at (0,80,10), rotation
    `[-90, 0, 90]` — intrinsic XYZ; extrinsic would answer `[0,90,90]`),
    labels from the product names and the per-occurrence colour override
    distinct from the product colour; `structured: false` is still one blob.
  - **AC3** — an assembly GLB passes structural validation written
    independently of the writer (asset version, 4-byte-aligned buffer views,
    every accessor byte range inside its view inside the buffer, every index
    in range), the poses match `get_assembly`'s own numbers, `#ff0000` reaches
    the file **linear**, and two exports are byte-identical (sha256). The
    Three.js half cites slice 6's Playwright run (changelog 0313).
  - **AC4** — the 3MF is an OPC package with the three required parts, the
    core-namespace root, `unit="millimeter"`, `Title`/`Designer`/`PartNumber`
    (from `set_bom_fields`) and per-solid colours resolved through the
    `pid`/`pindex` wiring *and* re-read through the venv's own lib3mf.
    PrusaSlicer is the manual half, documented.
  - **AC5** — `usd` is in both format enums iff `usd_available()`
    (monkeypatched both ways, asserted over the registry **and**
    `GET /api/tools`), a `usd` request without it is the ordinary
    unknown-format refusal; with `pxr` present a stage exports, re-opens,
    reports `metersPerUnit 0.001` / `upAxis Z`, and holds one prototype in an
    abstract library for two instances.
  - **AC6** — `fidelity` on every export format and both assembly formats
    (`parametric: "none"` on all of them, `pmi` present only on `step`), the
    exact blocks for glTF / 3MF / structured STEP / structured import / flat
    import, and the FR3 skip path: a diameter dim on a cube lands in
    `pmi_skipped` with `no_cylindrical_face…`, the rest of the PMI still
    attaches, and `pmi: false` reports `opted_out` rather than `none`.
  - **AC7** — the flat import's result keys are unchanged, a delegated export
    returns the pre-wrap result plus exactly one key (`fidelity`) with the STL
    bytes identical, and the 100 MB cap + extension gate still refuse (the
    size guard exercised against a lowered ceiling rather than 100 MB of RAM).
    Plus the count guard: the newest changelog entry must cite a `make test`
    count (the PRD-004 AC10 / PRD-012 AC8 / PRD-026 AC7 shape, copied).
- `docs/agent-api.md` — `export_part` / `export_assembly` / `import_cad_file`
  rows rewritten from the code (format enums incl. the conditional `usd`,
  `pmi?`/`metadata?`/`structured?`/`prefix?`, `part_id` required only for a
  flat import, both result shapes, the name-aware auto-detect and why); the
  preview route documented; and a new **"Interop: fidelity and the translation
  matrix"** section — the exact `fidelity` block per call, what `pmi_skipped`
  and `pmi_notes` mean, the four-row translation matrix (exact B-rep → STEP
  only; PMI → AP242 export only; tessellation+colours+metadata → 3MF/glTF/USD;
  parametric intent → nowhere), and the determinism rule (glTF/GLB and USD
  byte-identical, 3MF never content-hashed).
- `docs/user-guide.md` — the Export menu's new formats and its schema-driven
  behaviour, the *Include GD&T (AP242)* toggle and what the toast reports, the
  import-preview flow (the three buttons as they are actually labelled, the
  prefix field, what falls through to the old prompt), a plain-language
  fidelity table, the USD platform matrix (macOS / x86-64 Linux / Windows —
  **no linux-aarch64 wheel**), and the two per-release manual checks (FreeCAD
  AP242, PrusaSlicer 3MF).
- `docs/architecture.md` — the two kernel interop packs and the OCP-free
  server-side writers in the file table and the extension-point section,
  including **how a pack grows an existing verb** (wrap the service method +
  mutate the registered `Tool` in place, *and rebind its handler*), why
  re-registration was impossible (`ToolRegistry.register` raises on a
  duplicate and has no overwrite seam), and the load-order reason for the
  `xchange` name; the glTF Y-up root node and USD's declared-not-converted
  axis beside "Transform semantics"; and a new short section on interop
  determinism (3MF's UUIDs, the DXF precedent) and the `.brep`-materialization
  import design with the rejected `(file, product_path)` selector.
- `AGENTS.md` — a new "Interop gotchas (PRD-017)" section: the six AP242
  traps, the unreferenced-datum finding, sRGB-vs-linear in both directions,
  the `tools_xchange` load order + the two-halves schema mutation, the
  `add_shape(Part)` and single-solid-product colour traps, 3MF non-hashing,
  the usd wheel matrix, pxr's reversed `rotateXYZ`, the name-aware
  auto-detect, the fidelity rules, and the OCP-free probe.
- `CLAUDE.md` — the same, condensed to one dense bullet in the trap list.

## Files
- `tests/test_prd017_acceptance.py` — new (13 tests)
- `docs/agent-api.md` — export/import rows, preview route, fidelity +
  translation-matrix section
- `docs/user-guide.md` — export menu, PMI toggle, import preview, fidelity
  table, USD platform matrix, per-release manual checks
- `docs/architecture.md` — pack tables, extension-point mechanism, up-axis
  note, interop determinism + import design
- `AGENTS.md`, `CLAUDE.md` — interop trap lists

## Notes
The AC1 fixture deviates from the PRD's literal "dims + datum + flatness FCF"
by adding one perpendicularity frame, and the reason is in the module: without
a frame referencing it, the datum is in the file but not in the round trip, so
the stricter-looking fixture is the one that actually grades the criterion.

The three manual halves (AC1's FreeCAD viewer, AC3's Three.js loader, AC4's
PrusaSlicer) are **evidence-graded, not stubbed**: each is named in the test
module's docstring with where its evidence lives — slice 6's Playwright
session for the browser half, `docs/user-guide.md`'s per-release list for the
two viewers. A test that pretended to run FreeCAD would be worse than no test.

Slice-suite provenance for this branch: `make test` — 5564 passed, 40 skipped
(the run recorded in `0314-interop-usd.md`). Slice 8 adds no production code,
only tests and documentation.

`make test` — 5573 passed, 40 skipped (21:54); non-passing were the pre-existing prd028 AC6 local solver timeout (skips on CI), the supervisor ballooning-kill and sketch-drag timing flakes (documented load-flake families), and a routes_structure worker-restart timeout cascade — all 34 pass in 67 s in isolation.
