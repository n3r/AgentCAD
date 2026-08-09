# Roadmap

v0.1 delivered the vertical slice: script-as-model parts on a real OCCT B-rep
kernel, projects/assemblies, a browser UI, and the dual agent surface (MCP +
built-in chat). **v2** built out the engineering depth on top of that spine.
**v3** closed out the remainder of this roadmap — every section below
"Shipped" that used to list a gap now lists what shipped for it, with the
honest residuals kept at the bottom.

## Shipped since v0.1

v2:

- **CAD import** — STEP/BREP as boolean-capable reference parts, STL as
  mesh-only (measure/display).
- **Assembly mates** — declarative rigid/revolute/cylindrical connectors,
  resolved to concrete transforms; `check_interference` already validated the
  other half.
- **2D drawings** — projected views with dimensions and hole callouts
  detected from geometry (SVG/DXF).
- **Geometric feedback tools** — cross-section, min wall thickness, projected
  area, full inertia tensor; optional linear-static FEM behind `agentcad[fem]`.
- **Materials with engineering properties** — 30 curated materials plus a
  layered user-override resolver.
- **Robustness** — the `safe_fillet`/`safe_shell`/`safe_bool` toolkit, a
  constraint sketch solver, `bd_warehouse` threads, and an Error Doctor.
- **Performance** — a parallel kernel-worker pool for multi-part rebuilds.

v3 (this wave):

- **Typed parameters** — PARAMS `type: number|int|bool|enum|string` with
  choices/max_len, enforced in both layers, typed inspector controls.
- **Per-solid part semantics** — `SOLID_LABELS`, per-solid metrics, per-solid
  materials with correct mass roll-ups.
- **Sheet metal** — the `SheetPart` toolkit (fold/unfold from one spec, bend
  allowance) and the `flat_pattern` export with bend lines (SVG/DXF).
- **PMI / GD&T** — a validated tolerance model on parts (dims/datums/FCFs)
  rendered as drawing callouts, plus **tolerance stack-ups** (worst-case +
  RSS) along the assembly mate graph.
- **Motion from mates** — driven revolute/cylindrical DOF sweeps with
  moving-body interference checking and viewport animation.
- **Class-A surfacing** — `smooth_loft` + G0/G1/G2 `blend_surface` (plate
  filling) and a `curvature` analysis kind to verify continuity numerically.
- **FEM tiers** — modal (natural frequencies) and steady-state thermal
  behind the same `[fem]` extra.
- **GUI sketching & push/pull** — an interactive 2D sketcher over the
  constraint solver that emits build123d code, and face push/pull recorded
  as script edits (script stays the source of truth).
- **Vision feedback** — `render_view`: server-side shaded renders returned
  as real image content over MCP and chat.
- **Turn-locking & multi-agent sessions** — per-project editing turns
  enforced at the store choke point; concurrent chat sessions with scoped
  identities.
- **Undo / history** — every mutation snapshots into a per-project git repo;
  Cmd+Z and `project_restore` time travel.
- **Mesh streaming** — coarse LOD tiers for >150k-triangle parts with
  progressive viewport loading (ACM1 unchanged).
- **Sandboxed script execution** — macOS seatbelt confinement of kernel
  workers (writes only in project roots, no network).
- **Windows/Linux CI** — a three-OS GitHub Actions matrix over the full
  suite (the architecture was already portable).
- **Single-binary distribution** — a self-contained PyInstaller bundle
  (`make dist`, ~390 MB) with the executable re-launching itself as the
  kernel worker.

## Remaining non-goals (deferred deliberately)

- **Heavier FEM** — multi-body contact and a CalculiX tier stay documented
  but unshipped; modal/thermal cover the common single-part questions, and
  contact needs a solver investment out of proportion to current demand.
- **Full kinematic/DOF solver** — mates place parts and sweep single driven
  DOFs; simultaneous multi-joint kinematics (linkages, closed chains) remains
  out of scope until there's a concrete need.
- **Class-A modeling *UX*** — the surfacing toolkit and curvature analysis
  shipped; interactive surface sculpting (control-point editing, live zebra
  overlays in the viewport) is a product milestone of its own.
- **Bend-relief & partial-width flanges** — the sheet-metal v1 does
  full-edge flanges; relief cuts and flange segments are the natural next
  iteration.
- **Windows/Linux sandbox confinement** — the seatbelt profile is
  macOS-only; Linux (Landlock/seccomp) and Windows (AppContainer)
  equivalents are future work, and `/api/health` reports "unsupported"
  there honestly.
- **Signed/notarized distribution** — the bundle is ad-hoc signed and
  arm64-only; notarization and multi-arch builds belong to a release
  pipeline, not the repo.
- **Real-time collaborative editing** — turn locks serialize writers; the
  localhost-only bind remains a security stance. CRDT-style concurrent
  editing is out of scope.
- **Sketcher depth** — arcs/splines/ellipses in the sketch solver, drag-to-
  solve warm starting (the `initial` hook is reserved), and rank-based DOF
  reporting.
