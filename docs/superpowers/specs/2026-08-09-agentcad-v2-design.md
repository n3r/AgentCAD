# AgentCAD v2 — Design Specification

**Date:** 2026-08-09 · **Status:** Approved (autonomous session under `/goal`;
all decisions gated by the 10-spike validation fleet — every candidate below
was proven by running code on this machine before adoption; spike evidence
lives in the workflow transcript and scratch artifacts).
**Scope input:** `2026-08-08-agentcad-v2-scope.md`.

## Decisions (one per limitation, spike-validated)

| Area | Decision | Rejected alternatives (why) |
|---|---|---|
| Import (U2) | New `reference` part kind: STEP (primary, lossless incl. labels/colors/assembly tree), BREP (geometry-only), STL (display/measure ONLY — booleans on mesh Faces segfault OCCT and are blocked at the loader with `contract_error`). Worker-side loader with LRU keyed by (realpath, mtime_ns, size). | Re-import per request (0.3 s/MB, too slow); converting STL→solid (fails: triangulation-only Face). |
| Sketch constraints (K5) | First-party scipy least-squares solver (`agentcad.toolkit.sketch`, ~330 lines, ms-scale, machine-precision; validated on fillet-rectangle and two-circle tangent). Exposed as script helper AND `solve_sketch` tool. | python-solvespace / py-slvs: install and solve fine but GPLv3 — license-incompatible with a dependency we redistribute against. |
| Threads (K7a) | Depend on `bd_warehouse` 0.3.0 (Apache-2.0, pure Python, requires build123d≥0.11.1 — exact match). Ship `agentcad.toolkit.threads.tapped_hole` wrapper to bypass the ThreadedHole(simple=False) 15 s no-op trap; cheat-sheet documents real-vs-simple guidance. | Hand-rolled helix sweeps (slow, agents reinvent badly). |
| Drawings (K12) | `project_to_viewport` (OCCT HLR) + first-party SVG annotation layer (dimensions DETECTED from projected geometry; validated: complete annotated A3 of the flange in ~0.5 s, DXF via ezdxf). Tool `generate_drawing`; Export menu entry. | Library ExportSVG path (30 ms/BSpline edge → 2.5 s+ on hidden-line-rich views). |
| Robustness (K1–K4) | `agentcad.toolkit`: `safe_fillet` (max_fillet hint + bisection; found 4.905 where 12 failed, 0.22 s), `safe_shell` (offset→boolean-inner-copy fallback, APPROXIMATE — warning must state up-to-~20% thinning on curved mid-sections), `safe_bool` (OCCT fuzzy tolerance). Error Doctor: signature→diagnosis→hint table enriching every worker error's `details.hint`. | Patching OCCT (out of reach); silent fallbacks (violates kernel-as-referee). |
| Mates (K6) | Optional `connectors(p, part)` function in part scripts declaring named build123d Joints; instances gain optional `mate` spec; service resolves mate chains to concrete position/rotation_deg at assembly read (topological order, cycles → 422). Frontend unchanged by construction. Proven: Location↔(position, intrinsic-XYZ°) decomposes losslessly incl. gimbal poses. | Full kinematic solver (not needed for placement; roadmap). |
| Performance (K14) | KernelPool: N `KernelClient`s (default `max(1, min(3, cores//3))`, config `kernel_pool_size`), part_id-hash affinity for warm shape LRUs, lazy spawn. Measured 2.4× (3 workers) to 3.6× (4) on 8-part batches; byte-identical outputs. Memory is the constraint: ~0.5–0.66 GB/worker. | Async single worker (kernel is CPU-bound); process-per-request (3 s import each). |
| Gizmos (U3) | Vendor `TransformControls.js` from three@0.185.1 (matches vendored builds byte-for-byte lineage). Translate/rotate modes, snap 1 mm/5° with Shift, `scene.add(tc.getHelper())` (r169+ API). Writes via new single-instance PATCH. Scale intentionally absent — UI explains "resize via parameters". | Custom gizmo (reinventing MIT-licensed wheel). |
| Materials (U4) | Materials v2: frozen-dataclass schema (`density_g_cm3` required; E_gpa, yield_mpa, ultimate_mpa, elongation_pct, cte_um_m_k, k_w_m_k, max_service_temp_c, cost_usd_kg, category, notes optional); 30 curated engineering materials (v1's 10 ids preserved with identical densities — cache keys stay stable); 3-layer resolver builtin < `~/.agentcad/materials.json` < project `materials` section (whole-entry replacement, provenance tag). Tool caveat: typical datasheet values, NOT design allowables. UI picker + Metrics properties. | Fixed table (defeats "alloys etc."); per-property inheritance (surprising merges). |
| Analysis (K11) | Tier 1 ships: `analyze_section`, `measure_wall_thickness` (ray-cast, validated on known 2.5 mm wall), projected area, full inertia tensor (GProp matrix vs analytic — exact). Tier 2 ships as optional extra `agentcad[fem]`: STEP → gmsh (SUBPROCESS ONLY — GPL isolation across the process boundary) → meshio (MIT) → scikit-fem P2 linear elasticity; validated 0.03% vs Euler-Bernoulli, ~1.6 s. Tier 3 (CalculiX) documented, not shipped. | In-process gmsh import (GPL contamination risk); shipping FEM as core dep (heavy). |

Two upstream bugs discovered by spikes, both fixed in our layer: nested-Compound
`.volume` undercounts (worker metrics now sum `shape.solids()`); re-exporting a
shape after Compound adoption fails (loader hands out moved copies).

## Architecture: how v2 lands without a rewrite

The v1 spine is untouched. v2 features are **vertical modules** behind three
pre-wired extension points (built in the scaffolding pass before parallel work):

1. **Worker handlers** — `agentcad/kernel/handlers/<feature>.py`, each exporting
   `HANDLERS: dict[str, callable]`; `worker.py` merges them at startup.
   Error Doctor enriches every error response via one hook in `_dispatch`.
2. **Tool packs** — `agentcad/core/tools_<feature>.py`, each exporting
   `register(registry, service)`; `build_registry` calls all packs. v1's 17
   tools are unchanged; v2 adds ~10 (below).
3. **Route packs** — `agentcad/server/routes_<feature>.py`, each exporting an
   `APIRouter` mounted by `app.py` under `/api`.

`AgentCADService` gains exactly three pre-slotted call sites (scaffolding):
material density via `MaterialResolver` (project-aware), mate resolution in
`get_assembly`, and the kernel pool replacing the single `KernelClient`
(same `request()` signature; pool size 1 ≡ v1 behavior).

## Binding contracts

### Manifest (schema_version 2; readers accept 1)
- Part entries: `{"id", "label", "material", "params", "kind": "script"|"reference" (default script), "source": "imports/<file>" (reference only)}`.
- Instances: optional `"mate": {"connector": str, "to_instance": str, "to_connector": str, "params": {"angle_deg"?: num, "offset_mm"?: num}}` — when present, position/rotation_deg are derived (and rewritten) at read time.
- Project may carry `"materials": {id: {schema-v2 fields}}`.

### New/changed HTTP surface
- `POST /api/projects/{proj}/imports` — raw body upload, `?filename=` required, 100 MB cap, extensions .step/.stp/.brep/.stl → saves under project `imports/`, returns `{source, size_bytes}`.
- `POST /api/projects/{proj}/parts` accepts `{kind: "reference", source}` .
- `PATCH /api/projects/{proj}/assembly/instances/{id}` — `{position?, rotation_deg?, color?}` → updated assembly (gizmo write-back; 409 if instance is mate-driven).
- `GET /api/materials?project=` — resolved material table with provenance.
- `PUT /api/projects/{proj}/materials` — replace project materials section.
- `POST /api/projects/{proj}/parts/{id}/drawing` — `{views?, format: svg|dxf, sheet?}` → file in exports/, plus `GET` of the produced SVG for UI preview.
- `POST /api/projects/{proj}/parts/{id}/analyze` — `{kind: section|wall|inertia|projected_area, ...}`.
- `POST /api/projects/{proj}/parts/{id}/fem` — 501 with install hint unless `[fem]` present.
- `POST /api/sketch/solve` — entities+constraints JSON → solved coordinates.

### New agent tools (registry grows 17 → 27)
`import_cad_file` (path or uploaded source → reference part; returns metrics +
what survived: labels/colors/n_solids/mesh-only flag), `set_instance_transform`,
`set_mate`, `clear_mate`, `list_materials`, `set_project_materials`,
`solve_sketch`, `generate_drawing`, `analyze_part` (section/wall/inertia/area),
`fem_static` (registered only when extra installed; otherwise absent from the
list — agents must not see tools that cannot run).

### Script contract additions (both optional, backward compatible)
- `CONNECTORS` via `def connectors(p, part) -> dict[str, Joint-spec]` — the
  worker's `inspect` returns declared connector names/types.
- Scripts may `from agentcad.toolkit import safe_fillet, safe_shell, safe_bool,
  sketch, threads` — documented as the blessed way to write robust parts;
  plain-build123d portability note updated honestly (toolkit imports require
  the agentcad package).

### Frontend v2 (single-owner wave after backend)
Gizmo on selected instance (translate/rotate, snap, disabled+tooltip when
mate-driven), numeric transform panel, material picker (resolved list, grouped
by category, properties shown in Metrics), Import button (file picker → upload
→ reference part appears with provenance badge), Drawing generation from the
Export menu with SVG preview, Analysis actions (section/wall/inertia) surfaced
in Metrics tab, scale-explainer copy. All events flow through the existing WS.

## Testing bar (per feature, no exceptions)
Worker handler tests against the real kernel; service/tool/route tests; the
spike's own validation case reproduced as a regression test (e.g. the 2.5 mm
wall probe, the mirrored-solution sketch guard, the STL boolean block, the
mate chain + cycle rejection, pool determinism byte-check, FEM-vs-analytic
tolerance gated behind the extra). Suite must stay green without `[fem]`.

## Rollout order
Wave 0 scaffolding (extension points, deps, schema v2 read/write, metrics
volume fix) → Wave 1 eight parallel backend verticals → Wave 2 frontend +
docs + examples-upgrade in parallel → Wave 3 adversarial review → final
verification. Each wave ends with the full suite green and a commit.
