# AgentCAD v2 — Scope Matrix

**Input:** the user's four experience gaps plus every build123d/kernel limitation
enumerated in conversation on 2026-08-08. Each row is triaged **FIX** (this
wave), **MITIGATE** (meaningful partial), or **ROADMAP** (explicitly deferred
with rationale). Spike column names the validation agent whose verdict gates
the final plan (`docs/superpowers/specs/2026-08-08-agentcad-v2-design.md`).

## User-reported gaps

| # | Limitation | Triage | Approach (pending spike verdict) | Spike |
|---|---|---|---|---|
| U1 | Humans can only edit via parameters (no mouse-driven modeling) | MITIGATE now, ROADMAP full | This wave: viewport transform gizmos, a material picker, richer parameter UX, constraint-solved sketches agents/humans share. Full GUI sketcher + face push/pull is a distinct product milestone — planned, not in this wave. | gizmos, sketch-solver |
| U2 | Cannot open existing CAD files | FIX | STEP/BREP/STL import as a new `reference` part kind: no script contract, tessellated/measured/placeable/exportable like any part; upload route + `import_cad_file` tool. | import |
| U3 | Cannot move/rotate/scale objects | FIX (move/rotate), by-design (scale) | Drag gizmos (translate/rotate, snapping) on assembly instances + numeric transform panel; single-instance PATCH. Scale stays parametric — resizing is a parameter edit so geometry remains engineering-valid; the UI will say so where users look for scale. | gizmos |
| U4 | Cannot define materials (alloys etc.) | FIX | Materials v2: engineering property schema (density, E, yield, ultimate, CTE, thermal conductivity, service temp, cost, category), 25–35 curated engineering materials incl. aerospace alloys, per-project + global user-defined materials, UI picker, properties in Metrics, mass/inertia everywhere. | materials |

## Kernel/library limitations

| # | Limitation | Triage | Approach (pending spike verdict) | Spike |
|---|---|---|---|---|
| K1 | Fillet/chamfer fragility; constant radius only | MITIGATE | `agentcad.toolkit.safe_fillet` (largest-achievable search, warning surfaced), documented; variable-radius stays ROADMAP (OCCT exposes it; API design cost). | robustness |
| K2 | Shelling fragility | MITIGATE | `safe_shell` with validated fallback strategy. | robustness |
| K3 | Boolean failures on tangent/coincident faces | MITIGATE | Fuzzy-tolerance boolean option (`safe_bool`) if OCCT fuzzy value proves out. | robustness |
| K4 | Cryptic OCCT errors | FIX | Error Doctor: signature→diagnosis→hint table in the worker; every kernel error gains `details.hint`. | robustness |
| K5 | No 2D constraint solver | FIX (agent-facing) | Constraint-solved sketches: entities+constraints in, coordinates out, feeding normal build123d code. GUI sketcher on top is ROADMAP. | sketch-solver |
| K6 | Joints exist but AgentCAD has no mates | FIX | Named connectors in part scripts + declarative mates on instances, resolved to concrete transforms at assembly read (frontend unchanged); no kinematic solver claimed. | mates |
| K7a | No native threads | FIX | bd_warehouse dependency (threads/fasteners) + guidance on real-vs-cosmetic threads. | threads |
| K7b | No sheet metal | ROADMAP | Bend/unfold correctness needs dedicated design; not this wave. | — |
| K8 | Basic surfacing (no G2/G3/freeform) | ROADMAP | Out of scope: wrong tool for class-A surfacing; documented honestly. | — |
| K9 | No direct B-rep editing (push/pull) | ROADMAP | Couples to GUI modeling milestone (U1). | — |
| K10 | Import metadata limits (no PMI/GD&T; STL is mesh) | MITIGATE | Ship what survives (geometry, names where present); PMI is ROADMAP. | import |
| K11 | Analysis stops at mass | FIX tier 1, spike-gated tier 2 | Tier 1: section analysis, wall-thickness probe, full inertia tensor tools. Tier 2 (linear-static FEM via pip-only chain) only if the end-to-end spike validates against an analytic case. | analysis-fem |
| K12 | No drawings | FIX (v1) | Projected multi-view SVG/DXF drawings with basic dimensions; `generate_drawing` tool + Export menu. | drawings |
| K13 | No tolerance modeling | ROADMAP | Meaningful only with drawings maturity + fits database; deferred. | — |
| K14 | Single-threaded rebuilds; whole-script regeneration | MITIGATE | Kernel worker pool (parallel part rebuilds, part→worker affinity, warm LRU); whole-script regen is inherent to code-as-model and stays (content-hash cache already skips unchanged parts). | perf |
| K15 | Heavy install, 3 s import, wheel lag | MITIGATE | Already amortized by warm worker; lazy pool spawn; documented. No further action this wave. | — |
| K16 | 0.x API drift | MITIGATE (done) | Version pinned via lockfile; the test suite is the compat harness (it caught `intersect()`/`is_valid` drift already). | — |

## Non-goals restated for this wave

Full GUI sketcher and push/pull modeling (the "humans who never touch code"
milestone — needs its own design cycle), sheet metal, class-A surfacing,
PMI/GD&T, tolerance stacks, multi-user. Each stays on `docs/roadmap.md` with
its rationale updated by this wave's outcome.
