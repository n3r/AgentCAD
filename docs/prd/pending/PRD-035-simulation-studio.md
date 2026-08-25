# PRD-035 — Simulation studio

- **Status:** pending
- **Phase:** v5 frame → v6 depth (the Test mode's core surface)
- **Created:** 2026-08-25
- **Origin:** founder direction from the Aug-2026 UX study (round 4: a
  study manager "to test parts or the whole item — static loads, fluid
  flows, whether joints hold") + competitive analysis
- **Depends on:** PRD-003 (specs — completed) · the `[fem]` solver tier
  (v3: `fem_static`/`fem_modal`/`fem_thermal`) · PRD-028 (materials —
  completed) — soft: PRD-025 (mode frame), PRD-030 (joint loads),
  PRD-019 (studies as optimization objectives), PRD-022 (cloud burst)
- **Related:** PRD-004 (CI), PRD-034 (selection), PRD-008 (face anchors)

## Problem & motivation

The solvers exist (linear-static, modal, thermal FEM since v3, resolved
against PRD-028's temperature-aware materials), but simulation is a
one-shot tool call: no persisted setup, no re-run, no staleness truth, no
place in the UI where "is this part strong enough?" lives. The UX study's
Testing mode — a SolidWorks-Simulation-style study tree (Fixtures · Loads
· Mesh · Results) with results rendered on the geometry — was validated by
CAD-literate review as the familiar container. The adversarial review also
fixed the bar for honesty, finding two classes of defect the real product
must never ship: **stale results presented as current** (geometry changed,
verdict didn't) and **decorative result displays** (three toggles, one
image). Both become invariants here. Finally, a study's verdict must feed
the same checks surface everything else reads (PRD-003 specs, the status
bar, CI) — evidence is only useful where decisions are made.

## Users & jobs

- **Design engineer:** define a load case once ("2 kN axial, bore
  fixed"), re-run it forever; see margins on the part, not in a log.
- **Team lead / reviewer:** open Test and know what has been proven,
  what is stale, and what failed — without asking.
- **Agent:** set up and run studies through tools, read structured
  results, and cite them ("SF 3.2 against yield") in proposals.
- **CI:** a study is a check — a proposal that degrades SF goes red
  (PRD-004).

## Goals

- G1. Studies are persisted, named objects on a part or assembly:
  definition (fixtures, loads, material source, mesh intent) + last
  result + the input hash it was solved against.
- G2. **Staleness is structural:** results are keyed to a hash of
  geometry + material + loads + scope; any drift renders them "out of
  date" everywhere they appear, and withdraws their check row until
  re-solved. No path may show a stale number as current.
- G3. Results render on the model (colormap + legend + deformed shape),
  with genuinely distinct displays per result kind.
- G4. A study can declare a criterion (e.g. SF ≥ 1.5) and thereby become
  a spec row (PRD-003) — runnable by `agentcad check`, gating CI.
- G5. Assembly scope: solve over the assembly's parts and report
  per-joint reactions against joint capacity (the founder's "will the
  joints hold" — deepened by PRD-030's load extraction when it lands).

## Non-goals

- In-house CFD, nonlinear/contact FEM, fatigue — roadmap non-goals; flow
  studies appear as a study *type* whose execution bursts to cloud
  connectors (PRD-022) and is absent until that lands (only-if-runnable).
- Meshing UX beyond intent presets (fine/standard/coarse) — no manual
  mesh editing.
- Load-case libraries/standards (wind/seismic) — later, possibly packs.

## Experience

Test mode: left rail lists studies (`Static 2 kN · solved · SF 3.2`,
`Thermal soak · stale`); selecting one shows its tree — Fixtures, Loads,
Mesh, Results. Fixtures and loads are created by picking faces in the
viewport (the PRD-008 face-anchor picking already proves the gesture) and
typing magnitudes with units. Run shows solver progress; results paint
the model — von Mises, displacement (deformed overlay with an explicit
exaggeration factor), FoS bands — with a legend and honest captions.
Changing thickness in Design flips the study to *stale* everywhere: rail,
tree, status bar, and its check row leaves the passing set. A failing or
stale check row is a link back to the study; a failing study names the
governing input where the solver knows it. Assembly studies add a joints
table (joint · reaction · capacity · margin). The agent panel can do all
of it conversationally ("will it survive 2 kN?" → creates/updates the
study, runs, reports), through the same tools.

## Functional requirements

- FR1. Study CRUD persisted in the manifest (additive `studies` key):
  `{id, label, scope: part|assembly, type: static|modal|thermal|flow,
  fixtures: [face refs], loads: [{face refs, vector|pressure|temp}],
  mesh: preset, criterion?: {metric, op, value}}`. Face refs use the
  PRD-008 anchor model (orphan honestly on geometry change).
- FR2. Run executes via the existing `[fem]` handlers with PRD-028
  material resolution; results persist beside the study with the **input
  hash** (geometry cache key + material id + loads + scope + mesh).
- FR3. Staleness: every surface that shows a result compares hashes;
  mismatch → "out of date" state, dimmed values labeled as the old run,
  check row withdrawn. Re-run affordance everywhere the state shows.
- FR4. Result displays are real: von Mises field, displacement with
  deformed geometry at a labeled exaggeration, FoS with its own scale;
  a result kind without real field data is not offered as a toggle.
- FR5. Criterion studies register as spec rows (`check_study` in the
  PRD-003 registry): `checks`/`agentcad check`/CI run them by re-solving
  or — under `--budget` — reporting `skip`, never a silent pass.
- FR6. Assembly static: per-joint reaction table with capacity margins
  (capacities from joint definitions; refined by PRD-030); a joint below
  margin fails the study's criterion.
- FR7. Flow/thermal-transient study types are visible only when a
  backing connector (PRD-022) is installed; otherwise absent, not
  stubbed (the FEM-extra precedent).
- FR8. Solver failures are structured and displayed as the study's
  state (mesh failed, unconstrained, singular) — never a silent red.
- FR9. Tools: `create_study` / `update_study` / `run_study` /
  `get_study` / `list_studies`; event `study_changed {id, state}`.
- FR10. Runs respect kernel affinity/queue rules and are budget-honest
  under CI (`skip`, exit 2 — the PRD-004 contract).

## Agent surface

The five tools above, `[fem]`-gated like the solver tools they wrap;
structured errors (`study_stale`, `study_unconstrained`,
`solver_unavailable {type}`); results return `{metrics: {sigma_max,
displacement_max, fos, per_joint: […]}, hash, solved_at_version}`.
`run_study` returns post-state including the check-row effect.

## Technical approach

Service: `core/studies.py` (definitions, hashing, staleness) +
`tools_studies.py` + a routes pack for the Test-mode UI; spec registry
gains `check_study` (PRD-003 extension point). Kernel: existing FEM
handlers, extended to return per-face/per-node fields for the frontend
colormap (mesh-with-scalars payload rides the existing mesh streaming).
Frontend: Test mode composition per PRD-025; study tree + pickers reuse
the anchor-picking pipeline; colormap rendering extends the viewport's
existing mesh path. Joints table reads PRD-013 joint definitions.

## MVP & phasing

- **MVP:** part-scope static + thermal studies with fixtures/loads
  pickers, run/persist/staleness, von Mises + displacement displays,
  criterion → check row, `create/run/get/list`.
- **Phase 2:** assembly scope with the joints table; modal display;
  FoS bands; failing-check → study → governing-input links.
- **Phase 3 (with 022/030):** flow via cloud burst; joint capacities
  from dynamics; study results in proposal review packets (PRD-002).

## Acceptance criteria

- AC1. Browser: define a static study on an example part by picking
  faces, run it, see the colormap + legend; edit a parameter in Design
  and watch the study go stale and its check row leave — live, no
  refresh.
- AC2. Re-run clears staleness; the check row returns with the new
  verdict; `agentcad check` runs the same study headlessly and its
  verdict matches the UI.
- AC3. Displacement view renders deformed geometry with the stated
  exaggeration; von Mises and displacement are visibly distinct fields
  (pixel-diff test on server renders).
- AC4. Assembly study reports per-joint margins; degrading a joint's
  capacity flips exactly that row.
- AC5. Without `[fem]`, the tools are absent from the registry and the
  Test mode shows the designed empty state (both directions tested).
- AC6. A mid-run budget stop in CI yields `skip`/exit 2, never a red
  (PRD-004 parity). Full suite green.

## Risks & open questions

- **Anchor drift on faces:** load/fixture faces orphan when topology
  changes — surface it as part of staleness ("fixture orphaned"), never
  re-guess; measure orphan rates like PRD-008 did.
- **Field payload size:** nodal scalars on large meshes; mitigate with
  the existing LOD streaming and per-display decimation.
- **Criterion double-bookkeeping** (study criterion vs spec row) must be
  one record — design review decides which side owns it.
- **Naming:** "studio" vs "studies" vs Test-mode-implicit — cosmetic,
  decide in design.

## Competitive references

SolidWorks Simulation's study tree is the borrowed container (UX study
familiarity map); Fusion ships simulation as a workspace the same way.
We differ in three ways: results are hash-keyed and structurally honest
about staleness (incumbents let stale plots linger), studies are spec
rows a CI gate can run (nobody wires simulation into review gates), and
agents are first-class study authors through the same tools.
