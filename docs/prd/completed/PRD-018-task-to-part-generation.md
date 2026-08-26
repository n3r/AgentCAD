# PRD-018 — Task-to-part generation (kernel-grounded)

- **Status:** completed — merged to main in PR #37 (MVP + most of Phase 2; the frozen intent contract is re-measured server-side against built geometry via the `frozen_measure` kernel op — un-forgeable and un-observable to `build()`; NEMA hole-pattern feature checks, background jobs, and model-tiering deferred)
- **Phase:** v6 — generative engineering & the manufacturing bridge
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-003 (hard — specs are the termination criterion) ·
  PRD-001/PRD-002 (soft — candidates on branches, accepted results as proposals)
- **Related:** PRD-024 (bench measures the loop), PRD-029 (skills/knowledge
  packs are the quality lever), PRD-020 (background generation jobs), PRD-007
  (customizer is the zero-install funnel into generation), PRD-022 (scan/mesh
  assist is the mesh-input sibling)

## Problem & motivation

AgentCAD has world-class *raw material* for generation — an agent with the
73-tool surface can already draft a script, build it, render it, and read
the metrics — but no generation **front door**: no packaged intake for a
prompt, sketch photo, PDF drawing, or datasheet; no termination contract; no
candidate comparison; no provenance trail. A newcomer's first question
("just make me a mount for this motor") has no first-class answer.

The competitive evidence (market_research.md, "AI-native CAD"): text-to-CAD
is every rival's front door — Zoo's Zookeeper is a Plan→Act→Observe agent
debugging KCL with engine feedback; AdamCAD rode 1M+ generated models to a
YC round. The same section settles the method: Embodied CAD shows
solver-grounded iterative agents beat single-pass generation, and
Text2CAD-Bench and MUSE document cascading one-shot failures on real
engineering criteria. The gap matrix verdict is build-differentiated:
kernel-grounded loop on an open stack. The startups lack engineering depth;
the incumbents lack the loop (Neural CAD is "soon", Onshape Labs is a
promise). Nobody has generation that terminates on *spec-green* — geometry
the kernel validates **and** that meets stated, machine-checkable intent
(PRD-003). That is the moat this PRD builds.

## Users & jobs

- **Non-CAD maker or founder (human):** describe a part in plain language
  (or photograph a napkin sketch) and get a real parametric part — with
  sliders to tune — without writing build123d.
- **Design engineer (human):** skip boilerplate — "flanged NEMA 17 mount,
  4× M3, 5 mm standoff" — then refine the script by hand; ground a part in
  a vendor PDF drawing or datasheet instead of re-typing dimensions.
- **Generation agent:** draft, build, look, measure, iterate until green,
  then package the result honestly.
- **Reviewing human or agent:** judge candidates from renders + metrics +
  spec results; accept one as an ordinary proposal (PRD-002), never a
  magic blob.

## Goals

- G1. One intake for prompt, image (sketch photo), PDF drawing, and
  datasheet, producing a stated design intent the loop iterates against.
- G2. A budgeted iterate-until-green loop: draft build123d → build → look
  (`render_view`) → read metrics vs intent → revise — terminating only when
  the kernel **and** the specs (PRD-003) are green, or the budget is spent
  (returning best-so-far, honestly flagged).
- G3. Output is a first-class parametric part: typed PARAMS with sane
  bounds, `connectors(p, part)` where interfaces were stated, a generated
  `SPECS` block encoding the stated constraints.
- G4. Multi-candidate generation with a side-by-side gallery (render, mass,
  bbox, spec chips) and one-click accept.
- G5. Datasheet grounding: "mount for NEMA 17" places the 31 mm hole square
  and 22 mm pilot bore from the standard's numbers, not from vibes.
- G6. Generated parts enter through the normal trust machinery: provenance
  in the manifest, arrival as a proposal (PRD-002) when branching exists,
  full audit attribution.
- G7. Quality is measured, not asserted: the loop is scored by
  AgentCAD-Bench (PRD-024) against one-shot baselines; knowledge packs
  (PRD-029) are the tunable quality lever.

## Non-goals

- A B-rep foundation model or any trained geometry model — documented
  capital sink (market_research.md, "What we deliberately will not build");
  we orchestrate frontier LLMs over the existing tool surface.
- Mesh text-to-3D — not CAD; wrong artifact.
- Scan/mesh→parametric — that intake is PRD-022's assist.
- One-shot generation as the primary UX — the loop *is* the product; a
  single-pass mode exists only as the bench baseline.
- Whole-machine assembly generation — v1 generates parts (and their
  connectors); multi-part composition is a later phase on PRD-013.

## Experience

**Human path.** A "Generate" entry point in the part-creation flow and the
chat dock: a prompt box plus attachment well (PNG/JPG sketch photo, PDF
drawing, datasheet). Submitting streams the loop live over the existing chat
event channel — draft written, build result, render thumbnail, metric
readout, revision note — so the user watches the part converge. Finished
candidates land in a gallery: iso render, mass, bbox, PARAMS table, spec
chips. Picking one lands it as a proposal on a generation branch (with
PRD-001/002) or directly into the project (single-user fallback), opening in
the normal editor — indistinguishable from a hand-written part except for
its provenance badge.

**Agent path.** `generate_part {project, prompt, images?, files?,
candidates?, budget?}` runs the loop server-side and returns per-candidate
results (script, metrics, spec report, render path, iteration log). An MCP
client like Claude Code can instead *be* the loop itself — the same registry tools
are the loop's only moves — following the same spec + provenance
conventions. Either way the accepted candidate flows through
`accept_candidate` → proposal.

**Handoff.** Review is the handoff: the human reads the proposal packet
(PRD-002) — script diff, metrics, renders, spec results — and merges or
requests changes; requested changes are ordinary review comments the
generation agent can address (PRD-008).

## Functional requirements

**Intake**
- FR1. Prompt-only, prompt+images, and prompt+PDF intake; PDFs are rasterized
  server-side for vision and their extracted text/tables offered to the loop
  as reference data. Malformed/oversized uploads are `validation_error`s.
- FR2. Intent normalization: the orchestrator derives an explicit intent
  record (target envelope, interfaces, material, quantities, constraints)
  and a draft `SPECS` block from the request *before* geometry work; both are
  returned with the result so the user can see what the loop aimed at.

**Loop & termination**
- FR3. Each iteration must: write/revise the script (`create_part`/
  `update_part_script`), read the structured build result, render at least
  one `render_view` image, and read `get_metrics` (plus `analyze_part` /
  `run_specs` when applicable) before the next revision — the look-and-measure
  steps are mechanical, not left to model discretion.
- FR4. Termination: success = kernel-green (builds, `is_valid`) AND
  spec-green (`run_specs` passes the generated + user-stated specs). Budget
  exhaustion (max iterations, wall-clock, and spend caps, all configurable
  with safe defaults) returns the best candidate so far with
  `spec_green: false` and the failing checks named — never an exception,
  never a half-written project state.
- FR5. A candidate that repeatedly crashes the kernel or times out is
  abandoned with the structured error preserved in its iteration log;
  other candidates continue.

**Output contract**
- FR6. Every returned candidate is a valid part script: typed PARAMS
  (`type`, `default`, `min`/`max` or `choices`, units) covering the
  dimensions a user would plausibly tune; hardcoded magic numbers for
  stated-interface dimensions are a spec violation.
- FR7. When the intent names an interface (motor face, PCB, mating part),
  the script declares `connectors(p, part)` for it.
- FR8. The generated `SPECS` block encodes stated constraints (mass budget,
  wall minimum, envelope, clearance) via PRD-003's check vocabulary; the
  loop may not weaken or delete a spec derived from the user's stated intent
  (specs are frozen after intent normalization; only additions are allowed).

**Candidates, grounding, provenance**
- FR9. `candidates: N` (default 1, max bounded by quota) runs N loop
  instances with distinct strategies/seeds in parallel across the kernel
  pool; the gallery presents all terminal candidates, failed ones collapsed
  (a near-miss is often the right starting point).
- FR10. Standards grounding: bundled knowledge packs (PRD-029) carry
  machine-readable tables for common standards (NEMA frames, ISO 273
  clearance holes, metric threads); when intent matches a pack the loop
  must use the pack's numbers and the intent record cites the pack/table.
  User-supplied datasheets are extracted into the same structured form.
- FR11. Provenance in the manifest: the accepted part's entry gains
  `generated: {prompt_sha256, sources, model, iterations, spec_green,
  created, by}` — additive schema change, surviving `project_restore` and
  visible in `get_part`.
- FR12. With PRD-001/002 present, candidate work happens on `gen/<id>/<n>`
  branches and acceptance opens a proposal; without them, acceptance writes
  directly (normal history snapshot, undoable). All actions attributed to
  the calling identity.

**Quality & availability**
- FR13. Generation tools register only when the built-in agent is available
  (`ANTHROPIC_API_KEY` set) — same only-if-runnable philosophy as the FEM
  pack; the routes answer with an install/config hint otherwise.
- FR14. The orchestrator is testable without network: a scripted fake model
  client (the `ChatEngine` `client_factory` seam) drives deterministic loop
  tests; live end-to-end quality rides PRD-024's bench, whose generation
  tasks gate releases.

## Agent surface

New tools: `generate_part {project, prompt, images?, files?, part_id?,
candidates?, budget?}` · `generation_status {project, generation_id}` (when
run as a background job via PRD-020) · `accept_candidate {project,
generation_id, candidate}` · `list_generations {project}`.
New events: `generation_progress {project, generation_id, candidate,
iteration, phase}` · `generation_done {project, generation_id, candidates}`
(loop internals also stream as the existing `chat_tool_call`/`chat_tool_result`
events for the UI transcript).
Errors: `generation_unavailable` (no API key — mirrors `ChatUnavailable`),
`validation_error` (malformed intake). Budget exhaustion is a *result*
(`spec_green: false`), not an error.

## Technical approach

- **Orchestrator** — `agentcad/agent/generate.py` beside `chat.py`, reusing
  the `ChatEngine` machinery (Anthropic tool-use loop, `client_factory`
  injection, event publishing) with a generation system prompt, a restricted
  tool list, mechanical verify steps (FR3), and the budget/termination state
  machine. It runs under its own client identity (`gen:<id>`), taking the
  project turn like any agent — or its own branch once PRD-001 lands.
- **Tool pack** `agentcad/core/tools_generate.py` + **route pack**
  `agentcad/server/routes_generate.py` (uploads reuse
  `POST /api/projects/{proj}/imports`); cores untouched.
- **Vision** — `render_view` already returns real image content into the
  chat loop; PDF rasterization needs a server-process renderer dependency.
- **Knowledge packs** — standards tables as data under PRD-029's pack
  format; injected at intent normalization, not free-prompted.
- **Provenance** — additive manifest key in the `core/project.py`
  round-trip; no schema version bump.
- **Parallel candidates** — one asyncio task per candidate; kernel work
  spreads across the pool (distinct `part_id`/branch ⇒ distinct affinity
  keys). Long runs execute as PRD-020 jobs when `background: true`.
- **Frontend** — generation panel + candidate gallery as a new ES module;
  the transcript view reuses the chat dock's event rendering.

Kernel untouched. The loop uses only the public tool surface — which is the
point: generated parts are ordinary parts.

## MVP & phasing

- **MVP:** prompt+image intake; single- and multi-candidate budgeted loop
  with kernel+spec termination (FR2–FR9); gallery UI; manifest provenance;
  fake-client loop tests; direct-accept (proposal integration stubbed if
  PRD-002 hasn't landed).
- **Phase 2:** PDF drawing + datasheet extraction (FR1/FR10 full), knowledge
  packs for NEMA/ISO tables, branch-per-candidate + proposals-out,
  background jobs via PRD-020.
- **Phase 3:** bench-driven tuning (PRD-024 tasks gate releases), model
  tiering (cheap drafts / strong repairs), customizer→generation funnel
  (PRD-007), skills marketplace packs (PRD-029/031).

## Acceptance criteria

- AC1. "A 2 mm wall enclosure for a 60×40 mm PCB with M3 bosses and a snap
  lid" yields a buildable, spec-green, parametric part with sane typed
  PARAMS in under 3 minutes (live-model test, skipped without an API key;
  bench task in PRD-024 harness).
- AC2. "Mount for a NEMA 17" produces the 31 mm mounting-hole square, M3
  clearance holes, and 22 mm pilot bore, with the intent record citing the
  standards pack (geometry asserted via `get_metrics`/drawing detection).
- AC3. With the budget forced to 1 iteration on a hard prompt, the result
  returns best-so-far with `spec_green: false` and named failing checks; the
  project contains no orphaned/half-written parts (fake-client test).
- AC4. Fake-client tests cover all three exits: spec-green success, budget
  exhaustion, repeated-kernel-failure abandonment — each with an accurate
  iteration log.
- AC5. An accepted part carries manifest provenance that survives a
  `project_restore` round-trip, and behaves as an ordinary script part
  (editable via `update_part_script`, diffable, undoable) — test.
- AC6. The generated `SPECS` block contains every stated constraint, and an
  attempt by the loop to weaken a frozen spec is rejected (test).
- AC7. Browser session: generate with 3 candidates, watch live progress,
  open the gallery, accept one, see it in the tree/viewport — zero console
  errors.
- AC8. PRD-024 generation tasks: the loop scores above the one-shot baseline
  on the same harness, and the score is reported in release criteria.

## Risks & open questions

- **Metric-green but wrong-shaped:** metrics and specs can pass while the
  shape misses the intent; vision helps but is not a proof. Mitigation:
  multi-candidate + human pick, bench scoring on geometry match (PRD-024),
  spec vocabulary growth (envelope/feature checks).
- **Hallucinated standards data:** the trust-destroying failure mode.
  Mitigation: knowledge packs as the only source for standard numbers
  (FR10), the NEMA acceptance test, a prompt-level "never invent dimensions
  — ask or cite" rule.
- **Self-graded homework:** the loop generating weak specs to go green.
  Mitigation: FR8 freezes intent-derived specs before iteration; spec diffs
  are part of the review packet.
- **Cost and latency:** loops are token-hungry. Mitigation: budgets with
  spend caps, streaming progress; open question: which steps tolerate a
  cheaper model (tiering).
- **Untrusted document content** (datasheets are attacker-controllable):
  extract into structured tables before prompting; document text is data,
  never instructions.
- **PDF renderer dependency** — server-process only, license-clean; pick
  pypdfium2 vs poppler binding in the design spec.

## Competitive references

Zoo Zookeeper: the nearest architecture (Plan→Act→Observe with engine
feedback) on a proprietary engine + DSL, no specs, no evals, no review
workflow (market_research.md, "AI-native CAD"). AdamCAD: 1M models of
one-shot OpenSCAD virality; founders publicly conceded build123d is the
stronger substrate. Spectral SGS-1: B-rep diffusion, thin walls fail.
Meshy/Tripo: meshes, not CAD. Incumbents: Neural CAD and Onshape Labs
"Text-to-Code-to-CAD" are announcements bolted onto unreviewable models.
We differ by: an open stack, mechanical look-and-measure iteration,
spec-green termination (PRD-003), candidates as reviewable proposals with
provenance (PRD-002), and published kernel-scored evals (PRD-024).
