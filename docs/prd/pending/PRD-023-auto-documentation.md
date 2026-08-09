# PRD-023 — Auto-documentation

- **Status:** pending
- **Phase:** v6 — generative engineering & the manufacturing bridge
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-013 (hard — exploded-view offsets from the mate
  graph) · PRD-001 (hard — named versions to write release notes between) ·
  PRD-015 (soft — per-release regeneration + BOM sourcing) · PRD-002 (soft —
  agent-drafted docs approved as proposals)
- **Related:** PRD-014 (drawings and the PDF backend are sibling
  artifacts), PRD-011 (package metadata supplies sourcing links), PRD-020
  (doc regeneration as jobs), PRD-007 (share links publish the docs),
  PRD-008 (review threads on doc proposals)

## Problem & motivation

Documentation is the most-hated, most-deferred engineering chore: assembly
instructions are hand-built in slides from stale screenshots, READMEs rot
the day they're written, release notes are reconstructed from memory, and
none of it regenerates when the design changes. The market evidence
(market_research.md, "The workflow ring"): communication artifacts live
*outside* CAD today — CoLab raised $72M on pin-feedback because incumbent
models can't carry their own review artifacts — and the incumbents' 2025–26
auto-generation push (SW2026 one-click drawings, Solid Edge ~80% auto-views)
stops at drawings, the only artifact their models can ground.

AgentCAD's assemblies carry *semantics in reviewable form*: named connector
mates with resolved transforms, the mate forest `tolerance_stackup` already
walks, typed PARAMS, metrics, PMI, specs (PRD-003), and a git history of
every change. Nothing in the market generates assembly docs from mate
semantics because no one else's assemblies have semantics to generate from
("Where AgentCAD wins" #1). That makes documentation a pure agent sweet
spot with a structural moat: the agent has geometry, mates, history, and
specs in-context, so docs are **grounded, not hallucinated** — every number
traces to a tool result, and approval rides the normal proposal flow
(PRD-002).

## Users & jobs

- **Assembler / technician (human):** printable step-by-step instructions
  with exploded renders in the right order and the right fasteners counted.
- **Project maintainer / OSS consumer (human):** a README with live
  renders, params tables, and metrics that is regenerated, never stale.
- **Release manager (human):** honest release notes — what changed, by how
  much, and whether the gates stayed green — written from history, not
  memory.
- **Buyer (human):** a BOM with quantities and sourcing links inside the
  release bundle.
- **Docs agent:** drafts all of the above from model data; addresses
  review comments with evidence.
- **Reviewing human:** approves docs like any other change — as a proposal
  with diffs.

## Goals

- G1. Assembly instructions derived from the mate graph: a step sequence
  from the mate forest, per-step exploded renders (PRD-013 offsets +
  `render_view`), connector-accurate text, and fastener aggregation.
- G2. Project READMEs as compiled artifacts: overview, live renders,
  per-part params/metrics tables, spec status.
- G3. Release notes generated from history between two named versions
  (PRD-001): parts added/changed/removed, PARAMS deltas, metric deltas,
  spec-status transitions — prose constrained to computed facts.
- G4. BOM with quantities, materials, masses, and sourcing links (PRD-015/
  PRD-011) included in the doc set.
- G5. Docs regenerate deterministically per release (PRD-015 hook), so
  they are versioned artifacts of the model — never stale files.
- G6. Grounding is enforced, not aspirational: every factual claim in a
  generated document carries a machine-checkable source, and generation
  fails if prose contains unsourced numbers.
- G7. Agent-drafted, human-approved: docs land as proposals (PRD-002) when
  the review machinery exists; direct-but-undoable writes otherwise.

## Non-goals

- A general publishing/wiki system — docs here are compiled artifacts of
  the model, not freeform content.
- Video/animation generation — stills first; turntable GIFs are a later
  phase, video is out.
- Localization v1 — single-language templates; structure anticipates it.
- Marketing copy — the grounding rule (G6) is the point, not a limitation.

## Experience

**Human path.** A Docs panel (and a "Generate docs" step in the PRD-015
release flow): choose artifacts — instructions, README, release notes, BOM
— and, for notes, the two versions to compare. Generation runs as a job
(PRD-020) with progress in the tray; the result opens as a preview
(rendered HTML beside its markdown source). With PRD-002, artifacts arrive
as a proposal touching `docs/generated/` — reviewable diffs like any script
change, with review threads (PRD-008) for "step 3 is out of order" feedback
the agent addresses. Approving merges the docs; the release bundle picks
them up. A sequence-override editor lets a human reorder steps; the
override persists and future regenerations respect it.

**Agent path.** `assembly_sequence {project}` returns the derived order as
data (useful alone — an agent planning a fixture uses it too).
`generate_docs {project, kinds: ["instructions", "release_notes"], ref:
"rev-b", compare_ref: "rev-a"}` drafts everything: it walks the mate
forest, computes exploded offsets (PRD-013), renders each step
(`render_view`), diffs manifests and metrics between refs, and writes
markdown/HTML plus a claims sidecar. If review exists it opens the proposal
and cites its evidence; on comments it revises and replies with
before/after renders (PRD-008).

**Handoff.** The proposal is the handoff: agents draft and revise; humans
reorder, annotate, approve. Overrides are data the next regeneration
honors.

## Functional requirements

**Assembly instructions**
- FR1. Step sequence derives from the mate forest: explicitly-posed
  anchors first, then topological order along mate edges (an instance
  follows the instance it mates to), ties broken bottom-up by world Z;
  fastener instances (identified by package metadata when PRD-011 is
  present) group onto their parent step. A manifest-level
  `assembly.sequence` override, when present, wins and is validated
  against the instance set.
- FR2. Each step renders an exploded state (PRD-013 offsets along mate
  axes) with the incoming parts visually distinguished, plus text built
  from model data: part labels, connector names, counts ("Mate `bracket1`
  seat onto `plate1` hole1"), and relevant PMI notes.
- FR3. Assemblies with unmated (explicit-transform) instances degrade
  gracefully: those instances append in a final placement step with a
  warning in the result — never a failure.
- FR4. Output: markdown + self-contained HTML under the project's
  `docs/generated/` (assets alongside); PDF via PRD-014's backend once it
  lands. Printable = paginated steps, one render per step.

**README, release notes, BOM**
- FR5. README generator: project overview, assembly iso render, per-part
  table (label, material, mass from metrics), per-part PARAMS tables from
  `params_spec`, spec status (PRD-003 when present), and regeneration
  provenance footer.
- FR6. Release notes between `compare_ref` and `ref` (tags from PRD-001)
  derive from *computed* differences — parts added/removed/changed, PARAMS
  deltas, per-part mass/volume deltas, material changes, spec transitions
  — not from snapshot commit messages (which are machine-generated and
  low-information). Agent prose may narrate only those computed facts.
- FR7. BOM section: instance roll-ups with quantity, material, mass, and
  sourcing links from package metadata (PRD-015 builder when present;
  assembly roll-up fallback otherwise).

**Grounding & determinism**
- FR8. Every generated artifact emits a `claims.json` sidecar mapping each
  factual claim (number, order assertion, count) to its source (tool +
  args + value). A grounding lint rejects the artifact if prose contains
  numeric claims absent from the sidecar; generation fails loudly rather
  than shipping unsourced text.
- FR9. Regeneration at the same ref with the same template version is
  byte-stable (renders are deterministic server-side; templates are
  versioned and recorded), matching the drawings-as-CI-artifacts standard
  set by PRD-014.
- FR10. Artifacts live in the project repo (`docs/generated/`), so they
  version, diff, and travel with clones; compiled copies land in release
  bundles (PRD-015). Direct writes (no PRD-002) snapshot history like any
  mutation — undoable.
- FR11. Doc generation registers as a PRD-020 job kind (`docs`) —
  progress, cancellation, and per-release automatic regeneration triggered
  by the PRD-015 release flow.

## Agent surface

New tools: `generate_docs {project, kinds, ref?, compare_ref?, options?}`
(kinds ⊆ `[instructions, readme, release_notes, bom]`; returns artifact
paths, warnings, and the claims summary) · `assembly_sequence {project}`
(the derived step order with per-step instances and mate edges — data, no
rendering).
New events: `job_update` via PRD-020 for long runs.
Errors: `validation_error` (unknown ref/kind); unmated instances and
missing optional inputs (specs, packages) are warnings in the result, not
errors.

## Technical approach

- **Doc engine** — `agentcad/core/docgen.py`: sequence derivation over the
  same mate forest `mates.resolve` and `tolerance_stackup` walk; diff
  computation between refs via the history layer (`core/history.py`, ref
  access from PRD-001) plus manifest/metrics comparison in the service;
  template rendering with stdlib templating (no new dependency; templates
  versioned in-tree); the claims sidecar + grounding lint.
- **Renders** — the existing `render_view` service path with per-step
  instance transforms supplied by PRD-013's exploded-offsets tool; render
  outputs cached by content hash so regenerations only re-render changed
  steps.
- **Agent drafting** — narration passes reuse the `ChatEngine` machinery
  (as PRD-018 does) with a docs system prompt whose contract is FR8: claims
  first, prose over claims; the lint runs regardless of which model wrote
  the text.
- **Tool pack** `agentcad/core/tools_docs.py` + **route pack**
  `agentcad/server/routes_docs.py`; job kind registered per PRD-020;
  cores untouched.
- **Frontend** — Docs panel with kind pickers, ref selectors, HTML
  preview, and the sequence-override editor writing the manifest override.
- **Storage** — `docs/generated/**` tracked by project history; renders at
  modest resolution to bound repo growth (PRD-001's `git gc` housekeeping
  note applies).

Kernel untouched — everything composes existing capabilities, which is
exactly the grounded-docs argument in mechanical form.

## MVP & phasing

- **MVP:** `assembly_sequence` + instruction generator (mate-forest
  heuristic, exploded renders via PRD-013, HTML/markdown output, unmated
  degradation) and the release-notes generator over history between refs
  (PRD-001), both with claims sidecars and the grounding lint.
- **Phase 2:** README generator + BOM with sourcing (PRD-015/011);
  per-release automatic regeneration as a PRD-020 job in the PRD-015
  flow; proposal-based approval (PRD-002) with review-thread revision
  loops (PRD-008); sequence-override editor.
- **Phase 3:** PDF output (PRD-014 backend), per-step turntable GIFs,
  custom template packs, share-link publishing of docs (PRD-007),
  localization scaffolding.

## Acceptance criteria

- AC1. The rocketry stack produces printable assembly instructions in the
  correct order — flange → injector → nozzle — with a per-step exploded
  render and connector-accurate text, without hand-editing (integration
  test on an example copy + fixture snapshot).
- AC2. Release notes between the project's last three tagged versions
  report every part/param/metric change, and a cross-check test verifies
  each claim in `claims.json` against independently computed diffs — zero
  unsourced numbers.
- AC3. Grounding lint negative test: an artifact doctored to contain a
  number absent from its sidecar fails generation with the offending claim
  named.
- AC4. Regenerating at the same ref twice yields byte-identical artifacts
  (determinism test), and regenerating after a param change updates
  exactly the affected renders/tables.
- AC5. An assembly with one unmated instance generates successfully with
  the instance in a final placement step and a warning in the result
  (test).
- AC6. A manifest `assembly.sequence` override reorders the steps and
  survives regeneration; an override naming a missing instance is a
  `validation_error` (test).
- AC7. With PRD-002: `generate_docs` opens a proposal touching only
  `docs/generated/**`; approving merges it; without PRD-002 the write is
  history-snapshotted and undoable (tests in both configurations).
- AC8. Browser session: generate instructions + notes from the Docs panel,
  watch the job in the tray, preview the HTML, reorder one step via the
  override editor, regenerate — zero console errors.

## Risks & open questions

- **Sequence heuristic wrong on real assemblies:** topological order along
  mates is a draft, not assembly-planning truth. Mitigation: the override
  is first-class (FR1), the artifact is reviewed before release (G7), and
  tie-breaks are documented so failures are predictable.
- **Prose quality vs. grounding tension:** claims-only text can read like a
  robot. Mitigation: template-first structure with agent narration
  constrained to claims (FR8) — dry and true beats fluent and wrong in a
  release artifact; tone is a template iteration problem.
- **Render volume:** N steps × renders per regeneration. Mitigation:
  content-hash render caching, job execution (PRD-020), modest default
  resolutions.
- **History bloat from tracked doc assets:** renders in git grow clones.
  Mitigation: bounded resolutions, regenerate-in-place paths, the PRD-001
  housekeeping note; measure before adding LFS-style complexity.
- **Dependency timing:** exploded offsets (PRD-013) and the PDF backend
  (PRD-014) land on their own schedules. Mitigation: HTML-first; MVP
  blocks only on PRD-013's offsets tool, which is in that PRD's MVP.
- **Open question:** whether `claims.json` becomes the general evidence
  format for proposal packets (PRD-002) and review replies (PRD-008) —
  decide there; the shape is designed to be shareable.

## Competitive references

Nobody generates assembly instructions from mate semantics — the models
can't support it: Onshape has exploded views and drawings but instructions
are manual authoring; SW2026 and Solid Edge 2026 auto-generate *drawings*
(~80% of views), not documentation; CoLab's $72M business exists precisely
because communication artifacts live outside the CAD model
(market_research.md, "The workflow ring", "The desktop incumbents"). We
differ by: docs compiled from assembly semantics the model actually
carries, deterministic regeneration per release so docs are never stale,
enforced claim-level grounding instead of LLM prose on trust, and an
agent-drafts/human-approves loop riding the same proposal machinery as
geometry (PRD-002) — the "grounded, not hallucinated" argument made
mechanical.
