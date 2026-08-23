# 0316 — PRD-017 review fixes (4-way review: 2 Opus lenses, adversarial verifier, Codex)

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus + Sonnet fixers) / Nikita Fedorov

## Summary
One consolidated fix wave for the findings of four independent reviews.
The security posture held everywhere (no path escape, no XSS, no XML
injection, no anonymous reach, determinism across processes); what needed
fixing was error-contract honesty, three data-integrity hazards, and a set
of factually wrong doc statements.

## Changes (code — Fixer A)
- `GltfError`/`UsdError` are now `ValidationError`-family (re-raised in
  `_render` so the wire type is literally `validation_error` — the registry
  derives type from the class name and `core/tools.py` is off-limits); an
  empty-mesh assembly member becomes an `instances_skipped` row instead of
  failing the export; an empty part is a clean refusal; NaN/Inf are guarded
  at every writer boundary (`gltf`, `usd`, and `_pmi_map`, where a
  non-finite value used to be WRITTEN into the STEP and reported attached —
  now a `non_finite_value` skip row).
- All five staging-file sites (tools_xchange + three interop handlers +
  interop_import) use random tmp suffixes, each cleaning only its own tmp —
  concurrent same-destination exports interleaved bytes with the fixed name
  (the changelog-0181 bug, reproduced with a real pool before fixing).
- Materialized `.brep` names embed a digest of the ORIGINAL uploaded
  filename — `widget-1.step` vs `widget_1.step` used to silently rewrite
  the first import's geometry (proven by volume, now pinned by test).
- `BRepTools.Write_s`'s boolean return is checked (a `False` used to
  promote a stale tmp as product geometry); tmp cleanup on every path;
  `os.replace` errors translated so the server's absolute path never leaks.
- The AP242 schema static's try/finally opens before any setter (both
  writers) — a failed second setter could leak `AP242DIS` process-wide;
  the fix is mutation-verified by test.
- The preview payload carries `structured_suggested` (the server's own
  name-aware verdict, now a public `looks_structured(payload)`).
- `fidelity.pmi` derives from the attached counts ("none" when everything
  skipped); malformed manifest PMI is refused at the tool layer
  (`validate_pmi`) and skipped defensively in `map_pmi` (`malformed_entry`
  rows) — it used to take the whole export down with a raw `KeyError`.
- Kernel `_sanitize` caps fragments at 40 chars + digest (a 10k-char
  product name ENAMETOOLONG'd and leaked the absolute path); import
  `prefix` capped at 16; instance renames now warn like part renames;
  an unreadable STEP in auto mode names the file problem, not "needs a
  part_id"; import refusals carry `details.stage` ("parse" vs "map") —
  the PRD's promised error contract.
- **Structured import is one transaction**: validate everything, then ONE
  `save_manifest` (parts + provenance + instances) under the service lock
  and ONE `project_changed` — so it is genuinely one undo step and a
  mid-landing failure lands nothing (it used to leave half the parts with
  no provenance and orphan .breps). Undo leaves the materialized `.brep`
  files behind (restore overlays, never deletes) — orphaned-but-harmless
  and reused byte-for-byte on re-import, pinned by test.
- glTF accessor min/max floor/ceil at the 6th decimal (rounded bounds
  could exclude actual buffer values); the acceptance GLB determinism test
  hashes the first export before the second overwrites the path (it was
  vacuous), and the acceptance validator now decodes index buffers; a
  14-product/41-occurrence generated fixture grades AC2 at its stated
  scale (0.33 s); 3MF metadata refuses C0 control chars (NUL silently
  truncated via lib3mf's C strings); the preview route maps the worker's
  contract_error to 422 (caller's bytes), keeping 502 for kernel faults.

## Changes (frontend + docs — Fixer B)
- Import-preview dialog gates on the preview payload's new
  `structured_suggested` (the server's own name-aware verdict; count-rule
  fallback only when the field is absent) — a re-imported multi-solid
  AgentCAD STEP no longer lands in a dialog whose primary button would
  explode it into anonymous `SOLID` parts.
- Export menu: "STEP (structured)" assembly entry (`callTool` with
  `structured: true`; the menu's dataset/label plumbing learned to carry a
  structured flag so two STEP assembly rows coexist) and a static
  `assembly 3mf` entry (no longer dependent on the un-awaited boot fetch).
- Docs corrected against code: `instances_skipped` scoped to
  gltf/glb/usd; translation-matrix metadata row scoped to **3MF only**
  (fixed in the PRD too — the docs had faithfully copied its overclaim);
  the AGENTS.md self-contradiction ("No core/gltf.py") and the CLAUDE.md
  angular-dims mislabeling fixed; `.brep` clarified as import-side;
  3MF metadata defaults corrected; `details.stage` documented; the
  REST-forwards-only-format gap and the CLI's unwrapped-service export
  surface recorded as known gaps; PRD AC4/AC5 reworded to the validation
  actually performed (no XSD validator, no usdchecker in usd-core); PRD
  header → in-progress; roadmap link/status fixed (was a broken
  `pending/` link).

## Files
- `agentcad/core/gltf.py`, `usd_export.py`, `tools_xchange.py`,
  `tools_import.py`, `server/routes_import.py` — error contract, staging,
  transactional landing, preview verdict, caps
- `agentcad/kernel/handlers/_pmi_map.py`, `interop.py`,
  `interop_import.py` — non-finite guards, static ordering, digested
  filenames, Write_s checking, sanitize cap
- `frontend/js/main.js` — dialog gated on `structured_suggested`,
  "STEP (structured)" + static 3MF assembly entries
- `tests/test_interop_*.py`, `tests/test_xchange_pack.py`,
  `tests/test_prd017_acceptance.py` — regression pins for every fix, the
  14×41 fixture, de-vacuumed AC3
- `docs/agent-api.md`, `docs/user-guide.md`, `docs/architecture.md`,
  `AGENTS.md`, `CLAUDE.md`, `docs/prd/in-progress/PRD-017-interop-pack.md`,
  `docs/roadmap.md` — the corrected statements (see above)

## Notes
Review reports and rulings live in the orchestration record; the four
verdicts were unanimously FIX-FIRST and every gating finding is addressed
here. `make test` — 5608 passed, 40 skipped (28:07, shared box); non-passing were the pre-existing prd028 AC6 local solver timeout (skips on CI), the supervisor ballooning-kill flake, and a routes_structure worker-restart timeout cascade — 17/17 pass in 60 s in isolation.
