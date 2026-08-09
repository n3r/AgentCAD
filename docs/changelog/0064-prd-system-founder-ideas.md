# 0064 — PRD system: 32 PRDs, roadmap as index, founder-ideas review, deep-dive research

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Docs-only wave restructuring the roadmap into a PRD system, at founder
request. Eight founder idea clusters (workspace tabs, marketplace, materials
at scale, manual-CAD power, agent skills, scale navigation, kinetics,
universal import, UI revamp) were engineering-reviewed and folded into the
plan — some strengthening existing features, eight becoming new PRDs. Every
roadmap feature now has a detailed PRD under `docs/prd/pending/` (32 files,
~8,500 lines), `docs/roadmap.md` is now the PRD index with statuses and the
founder-idea mapping, and the competitive analysis was renamed to
`docs/market_research.md` and extended with four commissioned deep dives
(materials-data licensing, native CAD import tooling, marketplace platforms
and code-distribution safety, physics engines).

## Changes

- **New: `docs/prd/README.md`** — PRD conventions (folder = status:
  pending/in-progress/shipped), naming, and the 12-section template.
- **New: 32 PRDs in `docs/prd/pending/`** — each with Problem/Users/Goals/
  Non-goals/Experience/FRs/Agent surface/Technical approach (grounded in the
  real extension points)/MVP/Acceptance criteria/Risks/Competitive refs:
  - v4 collaborative core: 001 branching · 002 proposals+geometric diff ·
    003 executable design specs · 004 geometry CI · 005 multi-tenant cloud ·
    006 sandboxing+quotas · 007 share links+customizer · 008 review
    threads+presence.
  - v5 depth+ecosystem: 009 sketcher v2 · 010 feature toolkit II ·
    011 parts library/registry · 012 configurations · 013 assembly v2 ·
    014 drawings v2 · 015 BOM+releases · 016 direct modeling UX ·
    017 interop pack · 025 workspaces IA · 026 workbench shell ·
    027 navigation at scale · 028 materials database · 029 agent skills.
  - v6 moats: 018 task-to-part generation · 019 studies/optimization ·
    020 jobs+fleets · 021 DFM rule packs+costing · 022 manufacturing
    connectors · 023 auto-documentation · 024 AgentCAD-Bench · 030 motion &
    dynamics (MuJoCo-first; promotes the old kinematics non-goal) ·
    031 marketplace (server-side-execution-only safety model) ·
    032 universal CAD import (three honest tiers).
- **Rewritten: `docs/roadmap.md`** — now the PRD index: status model,
  founder-idea mapping table (all 8 clusters → PRDs), condensed thesis,
  three phase tables with dependencies, shipped v0.1–v3 summary, updated
  non-goals (kinematics non-goal retired into PRD-030; new evidence-backed
  exclusions: no bulk materials imports, no bundled proprietary translators,
  no points economies/client-side marketplace execution), and the working
  process (PRD → spec → plan; status moves with the file).
- **Renamed + extended: `docs/competitive-analysis.md` →
  `docs/market_research.md`** — retitled; Part II added with the four deep
  dives (each with sources): materials data (license walls vs open path,
  Granta-shaped 300–1,000 generic records, FreeCAD Supplemental-Materials
  precedent), native CAD import (OCCT's real format coverage, ODA converter
  pattern, cloud-conversion economics, "feature history translates
  nowhere"), marketplaces (MakerWorld/Printables mechanics, Thingiverse/
  Shapeways failures, npm/VS-Code supply-chain lessons → server-side-only
  execution), physics engines (MuJoCo verdict, Drake second tier,
  SolidWorks-Motion workflow, Onshape↔Isaac Sim bridge).
- **Pointer updates:** `AGENTS.md`, `CLAUDE.md`, `README.md` now reference
  the PRD index, `docs/prd/`, and `docs/market_research.md`.

## Files

- `docs/prd/README.md`, `docs/prd/pending/PRD-001…032` — new (33 files)
- `docs/roadmap.md` — rewritten as the PRD index
- `docs/competitive-analysis.md` → `docs/market_research.md` — renamed,
  Part II appended
- `AGENTS.md`, `CLAUDE.md`, `README.md` — doc-pointer updates

## Notes

- Docs-only diff; no code or tests touched; `make test` not run in this
  checkout (no venv synced — the 3-OS CI matrix runs on push).
- PRDs 001–024 were authored by four parallel subagents from the previous
  roadmap's feature blocks + template + exemplar (PRD-001), then QA'd:
  structural check over all 32 (12 sections, meta lines, no placeholders,
  no dangling PRD cross-references, all roadmap links resolve) plus content
  spot-reads. PRDs 025–032 (the founder-idea features) were authored
  directly with the deep-dive research.
- Notable engineering decisions recorded in the PRDs: proposals stored
  outside history snapshots so restore can't rewind review state (002);
  FEM-dependent spec checks return skip-as-data (003); an edge-picking
  sidecar as prerequisite for fillet-from-selection with index-fragility
  documented honestly (016); materialize-on-use package installs keeping
  projects portable git repos (011); manifest-resident configurations under
  the PRD-001 merge driver (012); marketplace code never executes
  client-side (031); connector API keys never flow through tools so secrets
  stay out of chat transcripts (022).
