# 0269 — 2026-08-19 — PRD-014 closed out: Drawings v2 ships, the second daily-driver-depth PRD

## Summary

Bookkeeping after PR #25 (Drawings v2) merged to main. The PRD moves to
`docs/prd/completed/` and its roadmap row flips to **completed (PR #25)** — 014
is the **second of the demoted v5 "daily-driver depth" tier (013/014/015/017) to
ship**, after 013.

## What shipped (MVP + met Phase-3)

- **Sheets & title blocks (FR1-2):** nine formats (iso_a4–a0, ansi_a–d), uniform
  auto-scale, a data-driven title block (material, mass, scale, version ref/date)
  from a new `manifest["drawing"]` section via `set_drawing_fields`/
  `get_drawing_fields`.
- **Sections & details (FR6-7):** per-body cuts, 45°/alternating hatching,
  cutting-plane arrows, `A-A`/`B-B` labels; magnified detail views.
- **Center marks, coaxial centerlines, hole tables (FR8-9):** marks in every view
  (was top-view-only); an opt-in hole table from PRD-010 metadata with a
  detected-diameter fallback.
- **Config tabulation (FR10):** letter variables (A/B/C…) + a per-config table.
- **Deterministic PDF (FR11-12):** a minimal pure-Python PDF writer (no new
  dependency) over a shared display list — byte-identical SVG **and** PDF at a
  fixed version, verified across processes and by `pdfinfo`.
- **Frontend + full HTTP surface, machine-readable results (FR13).**

## Deferred to PRD-015 (recorded)

FR3 revision block, FR4/FR5 assembly views + balloons + on-sheet BOM — they need
the BOM feature. `part_id` stays required; the acceptance suite carries AC3 +
AC5's `get_bom` half as marked skips.

## Architecture & quality

A **display-list / backend split** with a central `fmt()` determinism keystone;
only `agentcad/kernel/` imports OCP; `affinity=part_id` preserved. Two build
passes + an adversarial code review (**SHIP, no HIGH**); the review's two MED
validation gaps (unknown-view → 422, `_json_query` RecursionError → 422) and one
LOW (section-count cap) fixed with tests. A determinism regression was caught and
fixed mid-build (the title-block version cell vs the geometry-CI determinism
stage's git-stripped mirror — a `version` fixed-date override). The Ubuntu
apt-mirror CI flake was hardened twice (0253, then a hard `timeout` in 0268 once
the retry-never-fires bug was understood).

## Changes

- `docs/prd/in-progress/PRD-014-drawings-v2.md` → `docs/prd/completed/`, status
  "completed — merged in PR #25 (MVP + Phase-3; FR3/FR4/FR5 deferred to PRD-015)".
- `docs/roadmap.md`: the 014 row → **completed (PR #25)** with the shipped/
  deferred split; the "demoted behind that chain" note updated (013 + 014 done).

## Notes

Two independent build passes + one adversarial review; changelogs were renumbered
`0259-0267` at merge to avoid a collision with PRD-006b (which landed in
parallel). Browser-visual halves of AC1/AC6 are evidence-graded (no Chrome
extension connected this session); the machine halves are green and a controller
spot-check confirmed the integrated construction-gusset sheet end to end.

`make test` — **4550 passed, 38 skipped** on the committed main tree (the
PRD-014 + PRD-006b merge; this close-out is docs-only — PRD move, roadmap, this
entry — so the suite is unchanged). CI on the three-OS matrix is authoritative.
