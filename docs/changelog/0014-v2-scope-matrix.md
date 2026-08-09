# 0014 — Add v2 scope matrix (user gaps + kernel limitations, triaged)

- **Commit:** d72e986
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Adds a planning document that triages every known AgentCAD limitation — the four
user-reported experience gaps and the sixteen build123d/kernel limitations
enumerated on 2026-08-08 — into FIX (this wave), MITIGATE (partial), or ROADMAP
(deferred), with a named validation spike gating each committed item.

## Changes
- New spec `docs/superpowers/specs/2026-08-08-agentcad-v2-scope.md` with three
  sections: user-reported gaps (U1–U4), kernel/library limitations (K1–K16), and
  restated non-goals. Each row carries a triage verdict, an approach, and the
  spike whose verdict gates the final plan.
- User gaps: U1 mouse-driven modeling (MITIGATE now via gizmos/sketch-solver, full
  GUI sketcher ROADMAP), U2 CAD file import as a new `reference` part kind + an
  `import_cad_file` tool (FIX), U3 move/rotate gizmos (FIX; scale stays parametric
  by design), U4 engineering materials with a property schema + picker (FIX).
- Kernel items include: fillet/chamfer and shelling robustness via
  `agentcad.toolkit.safe_fillet`/`safe_shell` (MITIGATE), an "Error Doctor"
  attaching `details.hint` to kernel errors (FIX, K4), a constraint-solved sketch
  API (FIX, K5), named connectors + declarative mates (FIX, K6), bd_warehouse
  threads/fasteners (FIX, K7a), section/inertia analysis with spike-gated FEM
  (K11), projected multi-view drawings via a `generate_drawing` tool (FIX, K12),
  and a kernel worker pool for parallel rebuilds (MITIGATE, K14). Sheet metal,
  class-A surfacing, direct B-rep push/pull, PMI/GD&T, and tolerance stacks are
  explicitly ROADMAP.

## Files
- `docs/superpowers/specs/2026-08-08-agentcad-v2-scope.md` — new scope matrix

## Notes
Documentation/planning only — no code change. The matrix feeds the companion v2
design spec (`docs/superpowers/specs/2026-08-08-agentcad-v2-design.md`) and names
the spikes whose verdicts gate what actually ships.
