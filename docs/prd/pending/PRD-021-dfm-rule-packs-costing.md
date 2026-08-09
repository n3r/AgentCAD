# PRD-021 — DFM rule packs and cost models

- **Status:** pending
- **Phase:** v6 — generative engineering & the manufacturing bridge
- **Created:** 2026-08-09
- **Origin:** both — competitive analysis (Aug 2026) + founder idea #1b
  (Production tab with swappable process modules, Aug 2026)
- **Depends on:** — (runs on the existing analysis architecture) · PRD-003
  (soft — `check_dfm` as a spec) · PRD-004 (soft — CI gate)
- **Related:** PRD-025 (the Production tab hosts process modules — the rule
  packs *are* the swappable modules), PRD-022 (quotes consume DFM state),
  PRD-015 (release gates), PRD-010 (hole metadata feeds hole rules), PRD-028
  (materials expansion feeds cost models), PRD-011 (community pack
  distribution)

## Problem & motivation

Manufacturability feedback today arrives at the worst possible moment: after
upload, at a vendor's portal — or after a failed print. AgentCAD has exactly
one manufacturability-shaped check (`analyze_part kind=wall`, which returns
the minimum thickness *and its location*), and it proves the pattern works:
the agent reads a located violation, edits the script, and re-checks. Every
other process rule — CNC corner radii, tool access, overhangs, bend margins,
draft — lives in vendors' closed checkers and engineers' heads.

The competitive evidence (market_research.md, "The workflow ring"): quote +
DFM inside CAD just became table stakes — Siemens invested ~$50M in Xometry
to embed instant quoting/DFM in NX; Xometry add-ins live in SolidWorks/
Fusion/Onshape; Protolabs ships free DFM with every quote. But all of it is
post-upload, at the vendor's portal, on closed rules. The same section's key
fact: per-process DFM rules are *stable, published, and automatable by a
geometry kernel before upload*. The gap matrix verdict: build-differentiated
— open rule packs, pre-quote. Nobody owns an open ruleset; "ESLint for
parts" is unclaimed. Founder idea #1b supplies the product frame: a
Production tab (PRD-025) with swappable process modules — the rule packs are
exactly those modules.

The agent-native payoff is structural: violations are structured errors with
locations, so the loop that fixes a fillet today fixes an unmachinable
pocket tomorrow — and `check_dfm` becomes a spec (PRD-003) and CI gate
(PRD-004): "this project stays 3-axis machinable" as an enforceable
invariant no vendor portal can offer.

## Users & jobs

- **Design engineer (human):** catch violations while designing, with the
  offending faces highlighted in the viewport — not three days later in a
  quote rejection.
- **Design agent:** treat DFM violations exactly like build errors — read,
  locate, fix, re-check until green.
- **Manufacturing engineer (human):** encode the shop's real limits (tool
  set, materials, machine envelope) as a project- or org-level pack override.
- **Community contributor:** publish and version process packs like
  materials — open data, reviewable, testable.
- **Buyer / founder (human):** a cost estimate that moves sensibly with
  design decisions, before any vendor sees the file.

## Goals

- G1. Per-process DFM rule packs as versioned, open data (YAML metadata +
  Python rule implementations), community-extensible with the same layering
  as materials (builtin < user < project).
- G2. A kernel rule engine executing packs against built geometry, returning
  *located* violations (face/edge indices, points) the UI can highlight and
  agents can act on — the `analyze_part` wall check generalized.
- G3. `check_dfm {process}` as a tool, a PRD-003 spec predicate, and a
  PRD-004 CI gate.
- G4. Parametric cost models v1 per process (material volume + process time
  proxies) with honest uncertainty bands.
- G5. Lint ergonomics: rules have stable ids, severities, rationale docs,
  and per-project configuration (tighten, relax, disable-with-reason).
- G6. Determinism: same part + pack version + params ⇒ byte-identical
  report, so DFM state is cacheable, diffable, and CI-stable.

## Non-goals

- Replacing vendor DFM/quotes — vendors stay authoritative on price and
  lead time; PRD-022 fetches real quotes (and later calibrates our costs).
- CAM simulation or toolpath verification — out per the roadmap non-goals;
  we check geometry, not machining strategy.
- Exact costing — v1 estimates guide relative decisions, labeled as such.
- Auto-fixing geometry — the agent fixes through the normal edit loop;
  automated repair transforms are a later idea, not this PRD.

## Experience

**Human path.** A Manufacturing section (inspector card pre-PRD-025; the
Production tab's home once PRD-025 lands): pick a process — CNC 3-axis,
FDM, SLA, SLS, sheet, injection molding — optionally adjust process params
(tool set, build direction, parting direction), run. Violations group by
rule with severity chips; clicking one highlights the located faces/edges
in the viewport (the same face-picking machinery `face_info` uses) and
shows rationale plus limit vs. measured. A cost card shows the banded
estimate, moving live as the design changes. Fix, re-run, watch the list
drain. One click adds `check_dfm(process=…)` to the part's specs.

**Agent path.** `check_dfm {project, part_id, process: "fdm"}` → `{ok:
false, violations: [{rule: "fdm/min_wall", severity: "error", measured:
1.4, limit: 2.0, locations: [{face_index: 17}], …}]}` → edit script →
re-check until `ok`. `estimate_cost {process, quantity}` folds into
trade-off narration ("the thicker wall adds 11 g ≈ $0.40/part at 100
units"). With PRD-003/004 the agent writes the spec and CI holds the line
on every future proposal.

**Handoff.** DFM state is shared truth: the human sees the same highlighted
faces the agent's payload names; a violated spec shows the same red gate in
a proposal for both.

## Functional requirements

**Rule packs**
- FR1. Pack format: versioned YAML declaring pack id, process, semver,
  sources (the published guidelines the numbers derive from), and rules —
  each with stable id (`<process>/<rule>`), severity (`error|warning|info`),
  parameters with defaults, one-line message template, and rationale doc.
  Rule *implementations* are named geometric checks registered in the
  engine; a pack references implementations + parameterizes them.
- FR2. Builtin packs v1 ship for: `cnc_3axis` (internal corner radius vs.
  smallest tool, pocket depth-to-width ratio, tool access along setup axes,
  min wall, drilled-hole depth ratio), `fdm` (min wall/feature vs. nozzle,
  overhang angle vs. build direction, bridge span, min hole diameter),
  `sla` (min wall/feature, trapped-volume cupping, drain holes for
  hollows), `sls` (min wall, escape holes for enclosed powder volumes,
  clearance for printed-in-place gaps), `sheet` (inner bend radius vs.
  thickness, hole-to-bend distance, bend-relief presence, min flange
  width — grounded in the `SheetPart` bend data that `flat_pattern`
  already extracts), `injection_molding` (wall-uniformity ratio, draft
  angle along parting direction, undercut detection v1, rib-to-wall
  thickness ratio).
- FR3. Layered resolution mirrors materials: builtin < `~/.agentcad/dfm/` <
  project `dfm/` — overrides can re-parameterize, re-severity, or disable
  rules (disable requires a recorded reason, reported alongside results).
  Malformed packs are rejected at load with the failing field named; an
  invalid user pack degrades to builtins with a warning, like materials.
- FR4. Process params (tool diameter set, build direction, parting
  direction, nozzle diameter, sheet thickness source) pass per-call with
  pack defaults; the report echoes the effective params.

**Engine & results**
- FR5. `check_dfm` runs against built geometry of script parts and B-rep
  reference parts (STEP/BREP); mesh-only STL parts return a
  `skipped_mesh`-style result, consistent with booleans/interference.
- FR6. Violation payload: `{process, pack, pack_version, ok, violations:
  [{rule, severity, message, measured, limit, locations: [{face_index? |
  edge_index? | point}]}], checked_rules, skipped_rules: [{rule, reason}]}`
  — `ok` is false when any violation at or above the configured gating
  severity exists. Face/edge indices use the same mesh-order identity as
  `face_info` and the viewport's face-picking sidecar, so UI highlighting
  is index-for-index.
- FR7. Rules that are heuristic approximations (tool access, undercut
  detection) must say so in their rationale and default to `warning`
  severity — false certainty is worse than a stated approximation.
- FR8. Reports are deterministic (G6) and cache by (geometry content hash,
  pack id+version, effective params); the report records that identity.

**Cost models**
- FR9. `estimate_cost {process, quantity}` v1: material term (net or stock
  volume × density × `cost_usd_kg` from the materials catalog) + process
  time proxy (FDM: volume/infill + height×layer terms; CNC: stock-minus-part
  removed volume + setup count + feature count; sheet: cut length + bend
  count; SLA/SLS: height + volume terms; IM: amortized tooling proxy +
  per-part material) × configurable machine/labor rates from the pack data.
  Returns `{unit_cost, quantity, band_pct, breakdown}` — always with the
  band, never a bare number.
- FR10. Cost moves monotonically with its drivers (more volume, pricier
  material ⇒ higher; larger quantity ⇒ lower unit cost via setup/tooling
  amortization) — property-tested.

**Integration**
- FR11. With PRD-003: `check_dfm(process=…, max_severity=…)` is a spec
  predicate; with PRD-004: a CI check kind, red when the gate fails, with
  violations in the CI report.
- FR12. DFM results surface to PRD-022's quoting flow (a spec-green +
  DFM-green part is the precondition the "compressed loop" advertises).

## Agent surface

New tools: `check_dfm {project, part_id, process, params?, rules?,
min_severity?}` · `estimate_cost {project, part_id, process, quantity?,
params?}` · `list_dfm_packs {process?}` (packs with versions, rules,
sources, layer provenance).
New events: none (results return synchronously; long multi-part sweeps ride
PRD-020 as a job kind).
Errors: `validation_error` (unknown process — listing available; malformed
params). Violations are data, never errors: `ok: false` is a result the
loop acts on, exactly like the wall check today.

## Technical approach

- **Worker handler pack** — `agentcad/kernel/handlers/dfm.py` via
  `register(toolbox)`: the geometric measurement primitives (thickness
  field sampling generalizing the wall check's ray grid, face normal vs.
  direction classification for overhang/draft, concave-edge radius
  extraction for corner rules, pocket/reachability approximation along
  setup axes, enclosed-volume detection for drain/escape rules), reusing
  `build_shape`, `metrics`, and the tessellation the mesh sidecar already
  produces so face indices line up (FR6).
- **Rule engine + pack loader** — `agentcad/core/dfm.py`: pack parsing/
  validation, layered resolution (structured like `materials.py`'s
  builtin < global < project resolver), rule dispatch to the handler,
  severity gating, report assembly, cost models. Builtin packs live as
  data under `agentcad/core/dfm_packs/*.yaml`.
- **Tool pack** `agentcad/core/tools_dfm.py` + **route pack**
  `agentcad/server/routes_dfm.py`; cores untouched.
- **Frontend** — Manufacturing card: process picker, violations list wired
  to viewport face highlighting (existing picking path), cost card;
  relocates into the Production tab with PRD-025.
- **Spec/CI wiring** — a `dfm` check contributed to PRD-003's predicate
  registry and PRD-004's check kinds when those land; nothing here blocks
  on them.

Kernel changes are additive (one handler pack). Storage: builtin pack data
in-tree; optional `dfm/` dir per project; manifest untouched (per-project
rule config lives in the pack-override file, versioned with the project).

## MVP & phasing

- **MVP:** rule engine + pack loader with layering; `cnc_3axis` + `fdm`
  builtin packs; `check_dfm` with located violations; `estimate_cost` v1
  for both processes; violations list + face highlighting in the inspector
  card.
- **Phase 2:** `sheet` (on `SheetPart` bend data), `sla`/`sls`, and
  `injection_molding` packs; per-project overrides UI; spec predicate
  (PRD-003) + CI gate (PRD-004); multi-part project sweep as a PRD-020 job.
- **Phase 3:** community pack publishing through the registry (PRD-011),
  shop-profile packs (org tool sets via PRD-005), cost calibration against
  real PRD-022 quotes, Production-tab process modules (PRD-025).

## Acceptance criteria

- AC1. The prototyping enclosure reports its FDM violations (min wall,
  overhang) with face locations; an agent fixes them through script edits
  and `check_dfm` returns `ok: true` (scripted tool-loop test on an example
  copy).
- AC2. A CNC fixture part with a sharp internal pocket corner is flagged by
  `cnc_3axis/internal_corner_radius` with the concave edge located;
  raising the corner-radius param clears it (test).
- AC3. A `SheetPart` bracket with a hole 1·t from the bend line is flagged
  by `sheet/hole_to_bend`; moving the hole clears it (test, phase 2).
- AC4. Cost monotonicity properties hold: +volume ⇒ +cost, costlier
  material ⇒ +cost, +quantity ⇒ −unit cost; the breakdown sums to the
  total and the band is present (property test).
- AC5. A project override tightens `fdm/min_wall` to 3 mm and disables
  `fdm/bridge_span` with a reason: the report enforces 3 mm, lists the
  disabled rule under `skipped_rules` with the reason, and attributes both
  to the project layer (test).
- AC6. A malformed pack is rejected naming the field; an unknown process is
  a `validation_error` listing available processes; a mesh-only STL part
  returns the skipped result, not a crash (tests).
- AC7. Same part + pack version + params twice ⇒ byte-identical reports
  carrying the cache identity (determinism test).
- AC8. With PRD-003/004: a proposal introducing an FDM min-wall violation
  shows a red DFM gate naming the rule (cross-PRD integration test).
- AC9. Browser session: run FDM check on the enclosure, click a violation,
  see the faces highlight, read the rationale, watch the cost card move
  after a param change — zero console errors.

## Risks & open questions

- **Geometric detection hardness:** true tool-access/reachability and
  undercut analysis are research-grade. Mitigation: v1 ships stated
  approximations (FR7) at `warning` severity, conservative false-negative
  bias, a fixture-part test suite per rule.
- **False positives eroding trust:** the lint analogy cuts both ways.
  Mitigation: per-rule disable-with-reason (FR3), severity tuning per
  layer, rationale linking the published source guideline.
- **Cost credibility:** a bad number is worse than none. Mitigation: always
  banded (FR9), labeled estimate, calibrated later against PRD-022's real
  quotes, never surfaced as a quote.
- **Performance on large parts:** thickness/overhang sampling is
  geometry-heavy. Mitigation: per-rule sampling budgets, content-hash
  report caching (FR8), multi-part sweeps as background jobs.
- **Face-index stability:** indices are mesh-order, valid only for the
  build they came from. Mitigation: reports carry the content hash; the UI
  re-runs the check after a rebuild rather than reusing stale locations.
- **Pack IP hygiene:** rules derive from published vendor guidelines.
  Mitigation: packs restate limits as data with `sources` citations, no
  copied text.

## Competitive references

Xometry DFM embedded in NX via Siemens' ~$50M investment; Xometry add-ins
in SolidWorks/Fusion/Onshape; Protolabs free DFM with every quote — all
post-upload, single-vendor, closed rules (market_research.md, "The
workflow ring"). No incumbent runs manufacturability natively in the design
loop, and none exposes rules as data. We differ by: open versioned rule
packs anyone can extend (the materials-layering model), design-time checks
with located violations agents fix autonomously, determinism that makes DFM
a spec (PRD-003) and CI gate (PRD-004), and cost estimates that precede —
then calibrate against (PRD-022) — real vendor quotes.
