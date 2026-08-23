# 0309 — PRD-017 slice 2: structured STEP import, kernel half

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
Kernel handler pack reading a multi-product STEP's XCAF document into a
product tree, and materializing one `.brep` per unique product for the
server half (slice 3) to register as reference parts. FR8's kernel side.

## Changes
- `inspect_cad_tree {source_path}` — read-only: deduplicated `products`
  (first-encounter order, `index == i`), flattened `occurrences` with
  **composed** transforms (position + intrinsic-XYZ Euler degrees via
  `gp_Quaternion.GetEulerAngles(gp_Intrinsic_XYZ)` — the house convention,
  proven by a two-axis `Rz(90)·Ry(90)` re-placement test), per-occurrence
  color override vs product color (`ColorSurf→ColorGen→ColorCurv` at each of
  component/referred label, static `GetColor_s`, `Values(Quantity_TOC_sRGB)`
  → `#rrggbb` — `.Red()` is linear and silently darkens), nested `tree`,
  `counts`, `warnings`.
- `import_structured {source_path, out_dir}` — same payload plus
  `products[i].file` = `<stem>__<i>_<name>.brep` (basename only, atomic
  tmp+replace, deterministic across runs; loads through `refload`).
- Occurrence identity derives from the **component-label path** (a
  product's leaf label is shared by all its occurrences); name collisions
  qualify by path then `_2, _3…`, each reported in `warnings`.
- Refusals are `contract_error`: wrong extension (`.stl`/`.brep` included —
  the server auto-detect must gate on extension), unreadable file, empty
  transfer. `xstep.cascade.unit` untouched (process-global).

## Files
- `agentcad/kernel/handlers/interop_import.py` — new handler pack
- `tests/test_interop_import_kernel.py` — new (15 tests)

## Notes
The multi-product fixture is authored in-suite by raw XCAF **inside the
kernel worker** via a script string through the existing `export` handler
(no OCP import in the test process — no test module imports OCP/build123d,
and this deliberately keeps it that way; the fixture writer also leaves
`write.step.schema` alone, since AP214 carries tree/names/colors fine).
Fixture uses a MIN-aligned box + a locally-rotated pin so Euler sign/order
errors are visible to the bbox assertions (mutation-verified both ways).
`make test` — 5406 passed, 40 skipped in the recorded run; the 23 non-passing items were: a machine-load hang that burned `test_service`'s 120 s timeouts (19/19 pass in 3.6 s in isolation), transient supervisor/count-guard items that re-run green, and the pre-existing local-only `test_prd028_acceptance` AC6 real-solver timeout (fails identically on a tree without this branch's packs; skips on CI where `[fem]` is absent).
