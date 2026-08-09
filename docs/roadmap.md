# Roadmap

v0.1 delivered the vertical slice: script-as-model parts on a real OCCT B-rep
kernel, projects/assemblies, a browser UI, and the dual agent surface (MCP +
built-in chat). **v2** built out the engineering depth on top of that spine —
without a rewrite, behind auto-discovered extension points
([architecture.md](architecture.md#v2-extension-points)).

## Shipped since v0.1

These were roadmap items and are now in the product:

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

The items below are the remaining non-goals, roughly ordered by expected value.

## Modeling and CAD depth

- **GUI sketching & push/pull.** The kernel has a first-party constraint
  solver (`solve_sketch`) and full parametric parts, but authoring is still
  code-first. An interactive sketcher (draw, dimension, constrain) and
  direct push/pull on faces would open the tool to users who don't write
  build123d — the solver and rebuild loop it needs already exist.
- **Sheet metal.** Flanges, bends, bend-relief, and a flat-pattern unfold are
  their own feature family; build123d has primitives but no sheet-metal
  semantics, and the flat pattern is what makes it worth doing.
- **Class-A surfacing.** Continuity-controlled (G2/G3) freeform surfaces for
  aesthetic/aero bodies. OCCT can represent them; the work is the modeling UX
  and curvature analysis, well beyond the current solid-modeling scope.
- **Non-numeric parameters.** `PARAMS` is numeric-only; boolean/enum/string
  parameters (feature toggles, named configurations) need schema, UI controls,
  and cache-key handling.
- **Per-solid part semantics.** A part returning a `Compound` renders and
  exports, but per-solid materials, metrics, and mass roll-ups are not tracked.

## Documentation of intent (PMI)

- **PMI / GD&T on drawings.** Datums, feature control frames, and tolerance
  callouts on the generated drawings. Drawings today carry driven dimensions
  only; GD&T needs a tolerance model on the geometry, not just annotation.
- **Tolerance stack-ups.** Worst-case and statistical (RSS) stacks across an
  assembly. This depends on both a tolerance model and the mate graph — mates
  now exist, so the assembly-chain half is in place.

## Kinematics

- **Motion from mates.** Mates place parts today; the natural next step is
  driving revolute/cylindrical DOFs through ranges to animate and to
  sweep-check for collisions over motion (a moving-body extension of
  `check_interference`). A full kinematic/DOF solver was explicitly deferred
  when mates shipped.

## Analysis

- **Higher-fidelity FEM.** The optional `[fem]` tier is single-part
  linear-static. Multi-body contact, modal, thermal, and a heavier solver
  (CalculiX is documented but not shipped) are future tiers — kept optional so
  the core install stays light.

## Platform

- **Windows / Linux.** The architecture is already portable (pure Python, OCP
  wheels on all three OSes, browser UI). The work is packaging and CI to prove
  it; the only mac-specific artifact is the `.app` wrapper.
- **Single-binary distribution.** Today the install story is the repo + `uv`.
  A bundled distribution (briefcase/PyInstaller or a Tauri shell) would remove
  the toolchain prerequisite.
- **OS-level script sandboxing.** Part scripts run with user privileges
  (documented trust model). A macOS `sandbox-exec`/seatbelt profile around the
  kernel workers would harden the boundary without changing the architecture,
  since all script execution is already confined to those subprocesses.

## Application

- **Direct-manipulation UI for v2.** The v2 capabilities (import, materials,
  mates, drawings, analysis) are driven through the agent and the REST API
  today; on-canvas controls — a transform gizmo, a material picker, an Import
  button, an in-app drawing preview, analysis actions in the Metrics tab —
  are the planned browser-UI follow-up.
- **Durable history.** Undo/redo shipped (service-layer snapshot two-stack,
  Ctrl+Z/Cmd+Z in the UI plus `undo`/`redo` tools, shared across all
  clients) but is in-memory and bounded. A git-backed project history
  behind the same `service.history` seam would add persistence across
  restarts and a browsable timeline.
- **Mesh streaming for huge parts.** ACM1 is a single buffer today; chunking +
  LOD would help >1M-triangle models and large imports.
- **Multi-user / collaboration.** The service layer is already shared-state; a
  turn-locking scheme would let several agents (or agent + human) work one
  project concurrently. Out of scope until there's a concrete need — the
  localhost-only bind is a security stance, not a service-layer limitation.

## Agents

- **Vision feedback.** Screenshot-of-viewport as a tool result, so agents can
  *see* what they built, not only measure it.
- **Multi-agent sessions.** Concurrent agents on one project, gated by the
  turn-locking above.
