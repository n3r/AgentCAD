# AgentCAD v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Binding contracts live in `docs/superpowers/specs/2026-08-09-agentcad-v2-design.md` (read it FIRST) and the scope matrix beside it. Each Wave-1/2 task is executed by one subagent with exclusive file ownership; the orchestrator owns Wave 0 and all commits. Every spike left runnable artifacts under the session scratchpad — your prompt names your spike directory; lift validated code from it rather than re-deriving.

**Goal:** Close every triaged limitation: imports, gizmos/transforms, materials v2, robustness toolkit + error doctor, constraint sketches, threads, mates, drawings, analysis (+optional FEM), kernel pool — with tests, docs, and UI.

**Tech additions:** bd_warehouse (Apache-2.0); optional extra `agentcad[fem]` = gmsh (subprocess-only), scikit-fem, meshio. Everything else is first-party or already present.

## Global constraints (inherit v1 plan's, plus)

- v1's 17 tools, routes, manifest fields, and ACM1 format are frozen — v2 is additive. Suite must stay green **without** `[fem]` installed.
- Worker handler packs: `agentcad/kernel/handlers/<f>.py` exporting `HANDLERS`; tool packs: `agentcad/core/tools_<f>.py` exporting `register(registry, service)`; route packs: `agentcad/server/routes_<f>.py` exporting `router` (mounted under `/api`). Do not edit `worker.py`/`tools.py`/`app.py` beyond your pack — extension points are pre-wired in Wave 0.
- Agents do not run git commands; orchestrator integrates and commits per wave.
- Every feature reproduces its spike's validation case as a regression test.

## Wave 0 — scaffolding (orchestrator, sequential)

Handler/tool/route pack discovery; Error Doctor hook in `_dispatch`;
nested-Compound volume fix in `_metrics` (+test); manifest schema v2
read/write with `kind`/`source`/`mate`/`materials` passthrough; `PartRecord.kind|source`,
`InstanceSpec.mate`; service pre-slots — `MaterialResolver` seam, `mates.resolve()`
no-op call in `get_assembly`, reference-part dispatch seam in part methods,
`kernel.request(..., affinity=)` kwarg; pyproject: bd_warehouse dep + `[fem]` extra.
Full suite green before Wave 1 launches.

## Wave 1 — backend verticals (9 parallel agents, exclusive ownership)

| Agent | Owns (create) | Delivers |
|---|---|---|
| reference-imports | `kernel/handlers/reference.py`, `core/imports.py`, `core/tools_import.py`, `server/routes_import.py`, `tests/test_reference.py` | STEP/BREP/STL reference parts: worker loader+LRU, upload route (100 MB cap), `import_cad_file` tool, STL boolean block, per-solid volume metrics, provenance in part detail. |
| toolkit-robustness | `agentcad/toolkit/{__init__,fillet,shell,boolean}.py`, `kernel/error_doctor.py`, `tests/test_toolkit.py` | `safe_fillet`/`safe_shell`/`safe_bool` with honest warnings; ≥10-signature Error Doctor table wired to the pre-slotted hook; spike failure cases as tests. |
| sketch-solver | `agentcad/toolkit/sketch.py`, `core/tools_sketch.py`, `server/routes_sketch.py`, `tests/test_sketch.py` | scipy constraint solver (port spike module), `solve_sketch` tool + route, mirror-solution guard docs, both spike sketches as tests. |
| threads | `agentcad/toolkit/threads.py`, `tests/test_threads.py` | bd_warehouse wrappers incl. `tapped_hole` (bypasses 15 s trap), tessellation-weight guidance constants, cheat-sheet snippet returned in report. |
| mates | `core/mates.py`, `kernel/handlers/connectors.py`, `core/tools_mates.py`, `server/routes_assembly2.py`, `tests/test_mates.py` | connectors-in-scripts extraction, mate→transform resolution (chains, cycle→422), `set_mate`/`clear_mate` tools, single-instance PATCH route (409 when mate-driven). |
| materials | `core/materials.py` (rewrite — exclusive), `core/tools_materials.py`, `server/routes_materials.py`, `tests/test_materials.py` | Schema v2 + 30-material library (v1 ids/densities preserved), 3-layer resolver behind the Wave-0 seam, `list_materials`/`set_project_materials` tools, allowables caveat. |
| drawings | `kernel/handlers/drawing.py`, `core/tools_drawing.py`, `server/routes_drawing.py`, `tests/test_drawings.py` | HLR views + first-party SVG dimension layer (port spike emitter), `generate_drawing` tool, SVG+DXF outputs, flange sample as golden-ish test (structural asserts, not byte-golden). |
| analysis-fem | `kernel/handlers/analysis.py`, `kernel/handlers/fem.py`, `core/tools_analysis.py`, `server/routes_analysis.py`, `tests/test_analysis.py` | Tier-1 tools (section/wall/inertia/projected-area; 2.5 mm-wall regression), `analyze_part` tool; FEM behind import guard + subprocess gmsh, `fem_static` registered only when available, cantilever-vs-analytic test behind skipif. |
| kernel-pool | `kernel/pool.py`, `tests/test_pool.py`, plus its EXCLUSIVE right to edit `core/service.py` concurrency internals and `config.py` knob | Pool with affinity + lazy spawn + respawn, service hazard audit (status dict, cache writes, events) under parallel rebuilds, determinism byte-test, `kernel_pool_size` config, pool=1 ≡ v1. |

Gate: orchestrator integrates, full suite green, single commit.

## Wave 2 — surface (3 parallel agents)

| Agent | Owns | Delivers |
|---|---|---|
| frontend-v2 | `frontend/**`, `scripts/vendor_frontend.sh` | TransformControls vendoring (same three version; `getHelper()`), gizmo + snap + mate-disabled state, numeric transform panel, material picker + properties in Metrics, Import flow, Drawing generation + SVG preview, analysis actions, scale-explainer. |
| docs-v2 | `README.md`, `docs/*.md` | All v2 surfaces documented against as-built code; roadmap rewritten (what shipped, what remains and why); agent-api covers all 27 tools; trust-model note for imports. |
| examples-v2 | `examples/**`, `agentcad/core/templates.py` | Rocketry gains a mated flange stack + a drawing; prototyping lid gains a mate; a fastener demo part with real threads; cheat-sheet gains toolkit/threads/sketch/connectors sections (collected from Wave-1 reports); examples tests extended. |

Gate: integrate, suite green, browser check, commit.

## Wave 3 — adversarial review → fixes → final verification

Same find→verify→fix workflow as v1 over the v2 surface + regression sweep;
then verification-before-completion evidence battery (suite incl. `[fem]` env,
fresh-checkout, browser screenshots of gizmo/materials/import/drawing, MCP
tool-count 27, `make app`) and the closing report.

## Self-review

Spec coverage: every FIX/MITIGATE row of the scope matrix maps to exactly one
Wave-1/2 agent; ROADMAP rows map to docs-v2's roadmap rewrite. Ownership is
disjoint by construction (materials rewrite is exclusive; service concurrency
edits reserved to kernel-pool; templates.py reserved to examples-v2). Type
consistency: tool names/routes here match the spec's binding-contracts section
verbatim. Placeholders: none — spike artifacts carry the validated code.
