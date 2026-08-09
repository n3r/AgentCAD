# PRD-013 — Assembly v2: structure, scale, richer joints, URDF

- **Status:** pending
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026) · founder idea #6 in part (robotics/URDF)
- **Depends on:** PRD-011 (soft — packaged sub-assemblies) · PRD-012 (soft — config pinning on instances)
- **Related:** PRD-014 (assembly drawings consume structure), PRD-015 (BOM quantity roll-ups), PRD-016 (drag-to-place instances), PRD-023 (exploded views feed docs), PRD-027 (navigation at scale), PRD-030 (URDF is its dynamics handoff)

## Problem & motivation

Assemblies today are one flat list: `set_assembly` replaces
`{id, part, position, rotation_deg, color?, mate?}` instances, mates are
rigid/revolute/cylindrical connectors resolved by `agentcad/core/mates.py`
through the worker's `resolve_mates` handler, and every instance is placed by
hand or by one mate. That models a thrust chamber; it does not model a test
stand *holding* a thrust chamber. There is no way to reuse an assembly inside
another, no way to say "eight of these on this bolt circle" without eight
manifest entries, no joint vocabulary for a drawer slide or a gimbal, and
nothing between "full mesh" and "not loaded" when instance counts grow — the
only scale machinery is the per-part LOD tier (`<key>.lod1.acm`) for >150k-
triangle parts.

The competitive evidence (market_research.md, "The desktop incumbents"):
Fusion's degradation above ~500 components is a documented churn driver —
teams outgrow it and leave. FreeCAD 1.x ships a richer joint set than our
three mates ("Open-source CAD"). Onshape exports URDF and has made robotics
teams a core constituency ("Cloud-native CAD: Onshape"); the gap matrix rows
"Large-assembly semantics (1k+ instances)" and "Richer joints, exploded
views, URDF" are both verdict **build**. For our target users — rocketry
stacks on test stands, robot arms, construction nodes repeated across a truss
— assembly structure is the difference between a part modeler and a CAD
system.

## Users & jobs

- **Systems engineer (human):** compose a machine from sub-machines (engine →
  test stand → cell) without flattening everything into one project; navigate
  and edit 1k+ instances interactively.
- **Robotics engineer (human):** model real joints (slider, planar, ball,
  gear) with limits, then land the mechanism in a simulator via URDF without
  hand-writing XML.
- **Design agent:** treat a sub-assembly like software treats a module — an
  interface of exported connectors, composed by tools; pattern fasteners in
  one call instead of computing N transforms.
- **Docs/BOM tooling (agent, PRD-015/023):** read true structure — quantity
  roll-ups from patterns and sub-assemblies, exploded offsets for
  instructions.

## Goals

- G1. A project can instance another project's assembly as a unit, with its
  internal mate graph resolved and its declared interface connectors matable
  from outside.
- G2. Repetition is one entry: linear/polar instance patterns expand
  deterministically and count correctly everywhere (mass, interference, BOM).
- G3. 1,000+ instances stay interactive: kernel-side simplified
  representations plus instanced rendering on top of the existing LOD tiers.
- G4. The joint vocabulary grows to slider, planar, ball, and gear/rack
  couplings, all with limits, all declared in `connectors(p, part)` and
  driven through `set_mate`/`sweep_motion` like today's revolute DOF.
- G5. Exploded views derive from the mate graph — no hand-posed "exploded
  copy" that drifts from the model.
- G6. `export_urdf` emits links/joints/inertia from parts + mates that load
  in standard robotics toolchains, giving PRD-030 its dynamics substrate.

## Non-goals

- Closed-chain/simultaneous linkage solving — the mate graph stays a forest
  resolved in topological order; couplings are explicit ratios, not a
  constraint solver (roadmap non-goal, carried).
- Flexible sub-assemblies (posing a sub-assembly's internal DOF per parent
  instance) — v1 sub-assemblies place as rigid resolved units; per-instance
  DOF overrides are a later slice.
- Dynamics, gravity, contact — PRD-030.
- Registry hosting/versioning of shared sub-assemblies — PRD-011 (this PRD
  consumes a source reference, local path first).
- Drag-to-place UX and mate snapping — PRD-016 (this PRD provides the data).

## Experience

**Human path.** The Assembly section of the sidebar becomes a tree: instances,
pattern groups (one row, `×8` badge, expandable), and sub-assembly nodes that
expand to their contents read-only. Clicking through selects in the viewport
as today. A rep-mode toggle in the toolbar switches Full / Simplified for
heavy scenes; the HUD shows instance and triangle counts. An "Explode"
slider (0–100%) animates instances outward along their mate axes; the pose is
view-state, never written to the manifest. Patterns are created from the
placement card ("Pattern… linear/polar, count, spacing/angle"); sub-assemblies
via "Add sub-assembly…" (pick a known project + its version tag once PRD-001
tags exist). Editing inside a sub-assembly means opening its source project —
one click from the tree node.

**Agent path.** `set_assembly` accepts the richer instance schema (patterns,
sub-assembly refs); `set_mate` drives the new joint DOFs; `set_coupling` ties
two revolute connectors at a ratio; `explode_assembly` returns per-instance
offsets (and can write a render via `render_view` for docs); `export_urdf`
writes the robot description plus meshes. `sweep_motion` sweeps a driven DOF
with couplings honored, so "open the gripper through its range and check
clearance" is still one call.

**Handoff.** An agent patterns the bolt circle; the human drags the explode
slider to check it; PRD-015 reads quantities from the same manifest entry.

## Functional requirements

**Sub-assemblies**
- FR1. An assembly instance may reference an assembly instead of a part:
  `{id, assembly: {project, version?, config?}, position, rotation_deg,
  mate?}`. `project` is a known project name or absolute path; `version` is a
  PRD-001 tag (default: current state); `config` a PRD-012 configuration.
- FR2. The source's mate graph resolves internally first; the sub-assembly
  then places as one rigid unit. Cycles across projects (A instances B
  instances A) are a `validation_error` naming the cycle.
- FR3. A project may declare an interface — `assembly.interface:
  {name: {instance, connector}}` in the manifest — exporting internal
  connectors; `set_mate` on a sub-assembly instance addresses interface
  names. Mating to a non-exported connector is a `validation_error`.
- FR4. Flattened semantics everywhere downstream: `get_assembly` mass
  roll-ups recurse; `check_interference` and `sweep_motion` operate on the
  flattened instance set; `tolerance_stackup` may path through a
  sub-assembly boundary via its interface connectors. Nesting ≥2 levels deep
  works (engine → stand → cell).

**Instance patterns**
- FR5. An instance may carry `pattern: {kind: linear|polar, count,
  step_mm?|angle_step_deg?, axis?, center?}`; expansion is deterministic with
  ids `<id>[0]`…`<id>[count-1]`; expanded members inherit part, color, and
  mate template (polar patterns re-aim the mate per member).
- FR6. Patterns count as N everywhere: mass roll-up, interference pairs,
  BOM quantities (PRD-015), balloons (PRD-014). Editing `count` is one
  manifest change; per-member overrides are out of v1 (delete the pattern to
  diverge).

**Scale**
- FR7. A `simplified_rep` build kind produces a proxy mesh per part —
  `convex` (hull of the tessellation's vertices) or `decimated` (coarse
  tessellation) — cached as an ACM sidecar tier next to `<key>.acm`, served
  through the existing `mesh_info(lod=…)` tier mechanism.
- FR8. The viewport renders repeated parts (patterns, repeated instances)
  through instanced meshes: one geometry upload per (part, rep-tier), N
  transforms. A synthetic 1,000-instance assembly orbits interactively
  (≥30 fps on the dev-reference laptop) in simplified mode.
- FR9. `check_interference` gains an AABB broad-phase prefilter so 1k
  instances don't attempt ~500k boolean pairs; only bbox-overlapping pairs
  are boolean-checked, and the result reports `prefiltered` counts.

**Joints**
- FR10. `connectors(p, part)` accepts new types: `slider` (axis +
  `linear_range`), `planar` (plane origin + normal; `u_range`/`v_range`,
  optional `spin`), `ball` (center + `cone_deg` limit), `gear` (a revolute
  axis + `ratio` intent, couplable). Existing scripts are untouched.
- FR11. `set_mate` drives the new DOFs via a `dof` object (`{offset_mm}`,
  `{u_mm, v_mm, spin_deg}`, `{rx_deg, ry_deg, rz_deg}`); today's
  `angle_deg`/`offset_mm` args stay as shorthand. Out-of-limit values clamp
  with a warning, matching PARAMS clamping semantics.
- FR12. `set_coupling` ties two revolute/gear connectors at `ratio` (rack
  couplings tie revolute↔slider at mm-per-degree); `sweep_motion` re-resolves
  coupled joints at each sample; driving both sides of a coupling directly
  is a `validation_error`.

**Exploded views & URDF**
- FR13. `explode_assembly` computes per-instance offsets by walking the mate
  forest outward from its roots along each mate's axis (rigid mates: along
  the connector normal), scaled by `factor`; returns
  `{offsets: {id: [dx,dy,dz]}, order}` where `order` is the disassembly
  sequence (leaves first) — the input PRD-023 consumes.
- FR14. `export_urdf` writes `exports/urdf/<name>/` — `robot.urdf` + one
  mesh per link — mapping: instance → link (mass + inertia tensor from the
  inertia analysis handler, the same `inertia_tensor_g_mm2` `analyze_part`
  returns, converted to kg·m²); rigid mate → `fixed`; revolute →
  `revolute` with limits from `range` (`continuous` when unbounded); slider
  → `prismatic`; cylindrical → prismatic + revolute through a massless
  intermediate link; planar → `planar`; couplings → `<mimic>` with
  `multiplier=ratio`. Ball joints have no URDF equivalent — exported as
  `fixed` with a named warning in the result.
- FR15. Unmated instances export as fixed children of a world root with a
  warning; the tool returns `{path, links, joints, warnings}`.

## Agent surface

Changed: `set_assembly` (instance schema per FR1/FR5) · `set_mate {project,
instance, connector, to_instance, to_connector, angle_deg?, offset_mm?,
dof?}` · `sweep_motion` (sweeps any single driven DOF; couplings follow) ·
`check_interference` (broad-phase, `prefiltered` in result) ·
`get_assembly` (tree + flattened views, recursive `total_mass_g`).
New: `set_assembly_interface {project, exports}` ·
`set_coupling {project, a_instance, a_connector, b_instance, b_connector,
ratio}` · `clear_coupling {project, a_instance}` ·
`explode_assembly {project, factor?}` ·
`export_urdf {project, name?, mesh_format?}`.
New error details: `validation_error` with `details.cycle` (FR2),
`details.interface` (FR3); URDF/ball warnings in post-state, never silent.
Events: structure changes ride the existing `project_changed`;
`rebuild_started/finished` unchanged.

## Technical approach

- **Manifest** (schema bump, old files load): instance entries gain
  `assembly`, `pattern`, `config`; project gains `assembly.interface` and
  `assembly.couplings`. All merged key-wise by PRD-001's manifest driver.
- **Service seams:** `mates.resolve` (the seam `_resolved_instances` already
  calls) grows pattern expansion and sub-assembly resolution — depth-first
  resolve of the source project (via `ProjectStore` cross-project reads,
  sources opened read-only; `write_guard` never fires against a source),
  then rigid placement of the resolved unit. Kernel `affinity=` keys include
  the source project so sub-assembly builds reuse warm workers.
- **Worker handler packs:** `handlers/connectors.py` (already a pack) grows
  the joint types in `resolve_mates`; new `handlers/simplify.py` for the
  `simplified_rep` build kind (hull via scipy — already a dependency — or
  coarse re-tessellation) writing the ACM sidecar tier; the interference
  broad-phase lives server-side (bbox math needs no OCP).
- **Tool pack** `tools_structure.py` + `tools_urdf.py`; **route pack**
  `routes_structure.py` (the `routes_assembly2.py` name is taken by v2).
  URDF generation is pure server-side XML over metrics + the resolved graph
  (`agentcad/core/urdf.py`); no OCP import outside the kernel.
- **Frontend:** `tree.js` (grouped/tree rows), `viewport.js`
  (THREE.InstancedMesh path, rep-tier fetch via the existing
  `mesh/faces`-style API, explode animation), `placement.js` (pattern
  editor, DOF fields per joint type).
- **Tests:** joint-resolution unit tests per type; pattern determinism;
  two-level sub-assembly fixture; a generated 1k-instance synthetic project
  for the scale budget; URDF golden-file test parsed by a strict XML check.

## MVP & phasing

- **MVP:** patterns (FR5–FR6), sub-assembly instancing with rigid placement
  + interface mating (FR1–FR4, one nesting level tested to two), slider +
  planar joints (FR10–FR11), `simplified_rep` + instanced rendering (FR7–
  FR8), `export_urdf` for fixed/revolute/prismatic (FR14 core).
- **Phase 2:** ball joints, gear/rack couplings + coupled `sweep_motion`
  (FR12), exploded views (FR13), interference broad-phase (FR9), cylindrical
  decomposition and `<mimic>` in URDF.
- **Phase 3:** config-pinned sub-assembly instances (with PRD-012), packaged
  sub-assembly sources (with PRD-011), per-instance DOF overrides.

## Acceptance criteria

- AC1. A two-level assembly (engine sub-assembly on a test stand) resolves:
  internal mates hold, the engine mates to the stand via an interface
  connector, `get_assembly` total mass equals the hand-computed sum (test on
  a rocketry-derived fixture).
- AC2. A bolt circle declared as one polar pattern entry (`count: 8`) shows
  8 bodies, contributes 8× mass, and produces 8 interference candidates;
  changing `count` to 6 updates all three (test).
- AC3. The 1,000-instance synthetic assembly loads in simplified mode and
  orbits at ≥30 fps on the dev-reference laptop (manual browser session,
  HUD counts screenshotted); full-detail fallback still works for a selected
  instance.
- AC4. A slider joint with `linear_range: (0, 50)` driven to 80 mm clamps to
  50 with a warning; `sweep_motion` over the range reports `first_collision`
  against a deliberately obstructing fixture part (test).
- AC5. A 2:1 gear coupling: driving gear A by 90° poses gear B at 45° in the
  resolved assembly (test); URDF export carries the `<mimic>` tag.
- AC6. `export_urdf` on the mated rocketry stack produces a URDF that a
  standard checker parses cleanly (`urdfdom`/`check_urdf`-equivalent parse in
  CI) and that loads in a stock viewer (manual: urdf-viz screenshot); link
  masses match `get_metrics` within 0.1%.
- AC7. `explode_assembly` on the rocketry stack returns nonzero offsets along
  the stack axis and a leaves-first order (test); the explode slider animates
  in the browser with zero console errors.
- AC8. Full suite green; flat single-level projects and all existing
  mate/motion/stackup tests unchanged.

## Risks & open questions

- **Sub-assembly identity & staleness:** referencing another project's
  *current state* makes builds non-reproducible; version-pinned refs need
  PRD-001 tags. Direction: warn on unpinned refs, require pins in releases
  (PRD-015) and CI (PRD-004).
- **Interference cost at scale:** even broad-phased, dense assemblies may
  boolean thousands of pairs; budget per call, return partial results with
  `truncated` rather than time out. Simplified-rep interference (hull vs
  hull) as a fast pre-check is tempting but over-reports — keep it opt-in.
- **Ball-joint semantics:** three rotational DOFs make `sweep_motion`'s
  single-value contract awkward; v1 drives one axis at a time. Revisit with
  PRD-030.
- **Pattern/mate interaction:** re-aiming mates per polar member is
  well-defined only when the mate anchor is on the pattern's axis; off-axis
  cases fall back to rigid transforms with a warning — document it.
- **Instanced picking:** THREE.InstancedMesh needs instanceId-aware raycast
  mapped back to expanded ids; the existing click-select contract must not
  regress.

## Competitive references

Fusion documents degradation above ~500 components — a churn driver we turn
into a target (market_research.md, "The desktop incumbents"). FreeCAD 1.x
ships integrated assemblies with a richer joint set than our three mates
("Open-source CAD"). Onshape exports URDF and owns robotics mindshare via
FIRST ("Cloud-native CAD: Onshape"). We differ by: structure that stays
reviewable text (patterns and sub-assemblies are manifest entries a proposal
can diff), a kernel that referees scale features (interference and mass stay
exact even when display simplifies), and URDF emitted from mate *semantics*
— feeding the sim stacks agents already drive (PRD-030) rather than a manual
export wizard.
