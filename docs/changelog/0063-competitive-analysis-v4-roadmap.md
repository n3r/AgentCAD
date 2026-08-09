# 0063 — Competitive analysis + the v4–v6 human+agent cloud CAD roadmap

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Docs-only strategy wave: a full competitive analysis of the August-2026 CAD
landscape (new `docs/competitive-analysis.md`) and a rewritten
`docs/roadmap.md` that turns its conclusions into the v4–v6 feature plan for
a world-class cloud + open-source CAD where humans and agents work as peers.
The analysis was compiled from primary-source web research across five
clusters — Onshape/cloud-native, the desktop incumbents (Fusion, SolidWorks,
Creo, NX), AI-native CAD (Zoo, AdamCAD, Backflip, benchmarks), open-source/
code-CAD (FreeCAD, Ondsel autopsy, build123d ecosystem), and the workflow
ring (sim, review, DFM/quoting, print, CAM) — with sources linked per
section.

## Changes

- **New: `docs/competitive-analysis.md`** — landscape snapshots per cluster;
  the 2025–26 convergence argument (incumbents bolting on agents, AI-natives
  lacking depth, OSS cloud vacuum, research settling on kernel-grounded
  iteration); a ~30-row gap matrix with verdicts
  (build / build-differentiated / integrate / skip); AgentCAD's six
  structural advantages; business-model guardrails from the Ondsel autopsy;
  an explicit will-not-build list.
- **Rewritten: `docs/roadmap.md`** — keeps the shipped v2/v3 record, then
  replaces the residuals section with: the v4+ thesis ("the unit of
  collaboration is the change"; the validation loop is the moat; the window
  argument), and 24 detailed features in three phases, each with
  What / Why now / Agent-native angle / MVP / Done when:
  - **v4 collaborative core** — branching history, change proposals with
    geometric diff (CAD pull requests), executable design specs, geometry
    CI, multi-tenant cloud with local-first sync, cross-platform sandboxing
    + quotas, share links + customizer publishing, anchored review threads
    + presence.
  - **v5 daily-driver depth + ecosystem** — sketcher v2 (arcs/splines),
    feature toolkit II (patterns/hole wizard/sheet-metal v2), parts package
    registry, configurations, assembly v2 (sub-assemblies/scale/joints/
    URDF), drawings v2 (standards wrapper/BOM/balloons), BOM + release
    management, workbench UX depth (measure/sections/selection-aware chat),
    interop pack (AP242 PMI/3MF/glTF/assembly-STEP).
  - **v6 generative + manufacturing** — kernel-grounded task-to-part
    generation, design studies/optimization, jobs + fleet orchestration,
    open DFM rule packs + costing, manufacturing connectors (quotes/slicer/
    scan-assist/sim-burst), auto-documentation, AgentCAD-Bench public evals.
  - Deliberate non-goals (absorbing the old residuals list with reasons)
    and a sequencing/dependency table.

## Files

- `docs/competitive-analysis.md` — new
- `docs/roadmap.md` — rewritten below the "Shipped since v0.1" section

## Notes

- Docs-only diff; `make test` was not run in this checkout (no venv synced —
  verifying a markdown change doesn't justify the ~2 GB toolchain pull) and
  no code or tests are touched. The three-OS CI matrix runs on push.
- The old roadmap's "Remaining non-goals" are all accounted for: sketcher
  depth → 5.1; bend relief/partial flanges → 5.2; Windows/Linux sandbox →
  4.6 (promoted to cloud prerequisite); real-time collaborative editing →
  4.8 + non-goals (same-file CRDT stays out); heavier FEM → 6.5 sim-burst +
  non-goals; kinematics/Class-A UX/notarization → non-goals & 4.5 note.
- Research reports (five verbatim agent deliverables with full source lists)
  informed both docs; per-section source links in the analysis are the
  citable subset.
