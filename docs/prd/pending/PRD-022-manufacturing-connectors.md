# PRD-022 — Manufacturing connectors: quotes, print pipeline, scan import, sim burst

- **Status:** pending
- **Phase:** v6 — generative engineering & the manufacturing bridge
- **Created:** 2026-08-09
- **Origin:** both — competitive analysis (Aug 2026) + founder idea #1b
  (Production tab with swappable process modules, Aug 2026)
- **Depends on:** — (connectors run local-first with user-level keys) ·
  PRD-005 (soft — per-org key vault + principals) · PRD-021 (soft — DFM
  before quoting) · PRD-020 (soft — long calls as jobs) · PRD-015 (soft —
  quoting a release)
- **Related:** PRD-025 (the Produce tab is the human surface), PRD-017
  (3MF/STEP fidelity feeds every connector), PRD-018 (scan-assist drafts
  refine like generated parts), PRD-032 (native-format import is the
  different problem)

## Problem & motivation

The moment a design goes green, the workflow leaves AgentCAD: export STEP,
upload it to a vendor portal per vendor for quotes, open a slicer GUI to
print, set up a cloud solver by hand when built-in FEM isn't enough — and a
scanned mesh has no path to a parametric model at all. Hours of portal
round-trips sit between "spec-green" and "ordered."

The competitive evidence (market_research.md, "The workflow ring") is a
unanimous integrate-don't-build verdict: every neighbor is already
API-shaped and agentizing. Siemens paid ~$50M to put Xometry quoting/DFM
inside NX; Xometry add-ins live in SolidWorks/Fusion/Onshape —
single-vendor, inside closed hosts. The Prusa/Bambu/Orca slicer lineage
ships headless CLIs and 3MF became an ISO standard. SimScale markets
"Engineering AI via API". Backflip GA'd scan→parametric — impressive, but
"none were without error" per hands-on reviews: a draft generator, not an
authoring system. The gap matrix files all four under **integrate**: quotes
(APIs), print (orchestrate CLIs), high-fidelity sim (burst to APIs),
scan→parametric (import assist). Founder idea #1b frames the product: these
are swappable modules in the Production tab (PRD-025), behind one connector
extension point.

The agent-native angle is the compressed loop: design → `check_dfm`
(PRD-021) → fix → `get_quotes` → pick vendor → release bundle (PRD-015) —
hours of portals become one agent errand with a human decision at the end.

## Users & jobs

- **Buyer / founder (human):** three comparable real quotes in-app from a
  spec-green release; click through to order.
- **Maker / print operator (human):** a settings-embedded sliced 3MF from
  the part in one action — no slicer GUI safari.
- **Reverse-engineer (human + agent):** turn a scanned bracket mesh into an
  editable draft script the agent then refines against measurements.
- **Analyst (human + agent):** burst a question past built-in FEM to a
  cloud solver, with the agent routing which fidelity the question needs.
- **Org admin (human):** hold the vendor API keys once, per org (PRD-005),
  with egress consent and audit.

## Goals

- G1. One connector extension point: packs declare capabilities and config
  schemas; their tools register only when configured — the FEM
  only-if-runnable philosophy applied to external services.
- G2. `get_quotes` normalizes multiple vendors behind one tool (price,
  lead time, process/material, vendor DFM feedback, order link).
- G3. A print pipeline driving slicer CLIs to produce settings-embedded
  sliced 3MF artifacts plus a parsed slice report (time, filament mass).
- G4. Scan/mesh→parametric assist behind a flag: a draft build123d script
  from an imported mesh (local prismatic fitting and/or external service),
  packaged for agent refinement with a fit report.
- G5. Sim burst: cases beyond built-in FEM submitted to cloud solvers with
  normalized results, and agent-facing fidelity-routing guidance.
- G6. Trust mechanics: geometry never leaves the machine without explicit
  per-project consent; keys never touch project files, transcripts, or
  logs; every egress is audited with artifact hashes.

## Non-goals

- In-house CAM/toolpathing, high-fidelity solvers, or a scan foundation
  model — the documented graveyard (market_research.md, "What we
  deliberately will not build"); we connect to them.
- Being a slicer — we orchestrate existing CLIs and their profiles.
- Order placement/payments v1 — quotes deep-link to vendor checkout; a
  human clicks "order" on the vendor's site.
- A vendor marketplace — PRD-031's territory if it ever exists.

## Experience

**Human path.** The Produce surface (PRD-025; a Produce card pre-tab): pick
a part or release → the DFM chip (PRD-021) shows green/red → "Get quotes"
(process, material, quantity) → normalized quote cards per vendor with
price, lead time, vendor DFM notes, and an order link. Print: pick a
printer/filament profile → "Slice" → download the sliced 3MF, with print
time and filament mass shown. Scan: import an STL scan → "Draft parametric"
→ side-by-side mesh vs. rebuilt draft with deviation stats → open the draft
in the editor. Sim: "Run high-fidelity" submits the case to the configured
solver as a background job; results summarize beside the built-in numbers.
Connector settings (keys, org scope, egress consent) live in a settings
surface — never in chat.

**Agent path.** `get_quotes {project, part_id, process: "cnc", quantity:
25}` → normalized offers the agent compares and narrates. `slice_part
{project, part_id, profile: "mk4_petg_04"}` → artifact + report. Flagged:
`mesh_to_script {project, part_id}` → draft part + fit report the agent
refines through the normal edit loop (provenance as in PRD-018).
`sim_submit {project, part_id, case}` (a PRD-020 job) → normalized results;
tool descriptions carry fidelity-routing guidance ("built-in `fem_static`
for sanity and trends; burst for mesh-converged magnitudes"). The
compressed loop is then one errand: check_dfm → fix → get_quotes → attach
the chosen quote to the release bundle (PRD-015).

**Handoff.** The human decision points are explicit: egress consent, vendor
choice, order click. Everything else the agent can carry.

## Functional requirements

**Connector framework**
- FR1. Connector packs declare `{id, capabilities: [quotes|slice|scan|sim],
  config_schema, key_requirements}`; a connector's tools appear in
  `GET /api/tools` only when it is configured and enabled — unconfigured
  capability calls via REST return `connector_not_configured` with a
  setup hint.
- FR2. Key storage: local mode `~/.agentcad/connectors.json` (0600, never
  inside a project dir, never in exports/bundles); org vault with PRD-005.
  No tool accepts or returns secrets — configuration happens via the
  settings route/UI only, so keys cannot enter chat transcripts or MCP
  logs (verified by test greps, AC7).
- FR3. Egress consent: a per-project `allow_external_upload` setting
  (default off). Any call that would upload geometry while it is off fails
  with `egress_disabled` naming the setting. Every actual egress writes an
  audit entry `{principal, connector, artifact, sha256, ts}`.
- FR4. Vendor/API failures are isolated per connector: one vendor's 5xx
  yields a per-vendor error entry in the result while others return;
  connector calls carry their own timeouts and never hold the kernel pool.

**Quotes**
- FR5. `get_quotes` uploads STEP/3MF (produced by the existing exporters)
  to each requested configured vendor and returns normalized offers:
  `{vendor, process, material, quantity, unit_price, currency, lead_days,
  dfm_notes?, url, raw_ref}`. v1 vendors: one major instant-quote platform
  (Xometry-class) + JLC3DP; the adapter set is a pack, not a core list.
- FR6. Quote results cache with a TTL and are attached (offer snapshot,
  not live) to release bundles via PRD-015 when requested.

**Print pipeline**
- FR7. `slice_part` drives a slicer CLI (PrusaSlicer first; Bambu/Orca
  adapters later) with a named profile (printer/filament/process profiles
  stored per user or project); output is a settings-embedded sliced 3MF
  (with G-code where the slicer's project format carries it) under
  `exports/print/`, plus a parsed report `{print_time_s, filament_g,
  layer_count, slicer, slicer_version, profile}`.
- FR8. Slicer discovery and version probing happen at configuration time;
  a missing/incompatible CLI is `connector_not_configured` with the tested
  version range in the hint.

**Scan assist (flagged)**
- FR9. `mesh_to_script` (behind an explicit config flag) takes a mesh-only
  imported part and produces a *draft* script part: local strategy — RANSAC
  plane/cylinder fitting for prismatic cases, emitting parameterized
  build123d; external strategy — a configured Backflip-class service,
  gated by FR3 consent. Returns the draft part plus a fit report
  `{max_deviation_mm, rms_mm, coverage_pct}` comparing the rebuilt B-rep
  against the source mesh.
- FR10. Drafts carry provenance (PRD-018's manifest convention: source
  mesh hash, strategy, service) and are honestly labeled drafts — the
  original mesh reference part is kept alongside for comparison.

**Sim burst**
- FR11. `sim_submit` maps the built-in FEM case schema (`fixed_face`/
  `load_face`, loads, material) to a configured cloud solver, runs as a
  PRD-020 job, and returns normalized results `{max_displacement_mm,
  max_von_mises_mpa, solver, mesh_stats, report_url}` — comparable
  field-for-field with `fem_static` output so agents can cross-check tiers.

## Agent surface

New tools (each present only when its connector is configured):
`list_connectors {}` · `get_quotes {project, part_id?, release?, process,
material?, quantity?, vendors?}` · `slice_part {project, part_id, profile,
slicer?}` · `mesh_to_script {project, part_id, strategy?}` (flagged) ·
`sim_submit {project, part_id, case}` · `sim_status {job_id}` (thin over
PRD-020).
New events: long connector calls emit PRD-020 `job_update` events.
New error types: `connector_not_configured {connector, hint}` ·
`connector_error {connector, vendor_status, message}` · `egress_disabled
{setting}`.

## Technical approach

- **A new pack family** — `agentcad/connectors/<name>.py` exporting
  `register(connectors, config)`, parallel to handler/tool/route packs.
  Rationale: external I/O belongs in the server process — the kernel
  workers stay no-network by sandbox design (PRD-006), so connectors are
  the *only* sanctioned egress path, which is what makes FR3's consent
  gate airtight.
- **Core registry** — `agentcad/core/connectors.py`: pack loading, config/
  key store, capability→tool mapping, consent + audit enforcement, quote
  normalization types, response caching.
- **Tool pack** `agentcad/core/tools_connectors.py` (conditional
  registration per FR1) + **route pack**
  `agentcad/server/routes_connectors.py` (settings endpoints never return
  secrets; artifact downloads).
- **Slicer driver** — server-side subprocess with its own timeout and
  parsed stdout; artifacts via the existing atomic-write discipline into
  `exports/print/`.
- **Local mesh fitting** — a kernel handler pack
  `agentcad/kernel/handlers/meshfit.py` (it is geometry work on the
  already-loaded mesh: RANSAC primitives, deviation sampling), while
  external scan services are connectors; the two strategies share the fit
  report shape.
- **Sim adapter** — maps the `kernel/handlers/fem.py` case schema outward;
  results normalized in core.
- **Testing** — recorded-fixture adapters (vendor HTTP fixtures, canned
  slicer outputs) run in CI; live-API tests are an optional marked lane.
- **Frontend** — Produce card/tab (PRD-025): quote cards, profile picker,
  scan side-by-side, connector settings with consent toggles.

Kernel change: one additive handler pack (mesh fitting). Manifest change:
draft provenance key shared with PRD-018; per-project consent setting.

## MVP & phasing

- **MVP:** connector framework (FR1–FR4) + quotes via one major vendor +
  JLC3DP for CNC+3DP; PrusaSlicer pipeline with profiles; `mesh_to_script`
  local prismatic strategy behind its flag; settings UI with consent.
- **Phase 2:** Bambu/Orca slicer adapters; sim-burst connector
  (SimScale-class) as a PRD-020 job kind; org key vault + roles (PRD-005);
  quote snapshots in release bundles (PRD-015); DFM↔quote correlation
  data for PRD-021 calibration.
- **Phase 3:** external scan-service strategy; more quote vendors as
  community packs; print-farm queueing; order-status webhooks.

## Acceptance criteria

- AC1. The rocketry flange gets three real quotes in-app from a spec-green
  release — normalized cards with price/lead/link (live-marked test;
  CI runs the same flow against recorded vendor fixtures).
- AC2. `slice_part` on the prototyping enclosure produces a sliced 3MF
  that embeds the profile settings and G-code and re-opens cleanly in the
  slicer (artifact schema assertions in CI; a documented manual print
  smoke for releases), with `print_time_s` and `filament_g` parsed.
- AC3. A scanned bracket fixture mesh yields an editable draft script
  whose rebuilt B-rep deviates < 0.5 mm max from the mesh per the fit
  report, with draft provenance recorded (test, local strategy).
- AC4. Unconfigured connectors: their tools are absent from
  `GET /api/tools`; a direct REST call returns `connector_not_configured`
  with a setup hint (test).
- AC5. With two vendors configured and one returning 500 (fixture), the
  other's offer returns and the failure appears as a per-vendor
  `connector_error` entry — the tool call itself succeeds (test).
- AC6. With `allow_external_upload` off, `get_quotes` and `sim_submit`
  fail with `egress_disabled`; enabling it unblocks and writes an audit
  entry carrying the artifact sha256 (test).
- AC7. Secrets hygiene: after a full quote/slice/sim session, vendor keys
  appear nowhere in the project dir, exports, chat history, job records,
  or server logs (grep test over all artifacts).
- AC8. Sim burst on a calibration part returns normalized results within
  an expected envelope of built-in `fem_static` on the same case (fixture
  test), and the job is cancellable via PRD-020.
- AC9. Browser session: DFM chip → quotes cards → slice → download, with
  consent prompt on first egress — zero console errors.

## Risks & open questions

- **Vendor API access and ToS drift:** adapters break outside our control.
  Mitigation: per-vendor packs behind feature flags, recorded-fixture CI,
  an optional nightly live lane, and a no-API fallback that prepares an
  upload bundle + deep link instead of failing.
- **JLC3DP/vendor API availability:** if a v1 vendor lacks a usable API,
  ship the prepared-bundle fallback and promote the next vendor that has
  one — the framework, not the vendor list, is the deliverable.
- **Slicer CLI drift:** flags and project formats change across versions.
  Mitigation: pinned tested ranges (FR8), version probe at config, parser
  fixtures per version.
- **IP/privacy of egress:** uploading geometry is the sensitivity peak.
  Mitigation: default-off consent, per-call audit with hashes, org policy
  with PRD-005; documented plainly.
- **Scan-assist overpromise:** Backflip-class output is "none without
  error." Mitigation: draft framing everywhere, fit-report thresholds,
  flag-gated, prismatic-local as the honest default.
- **Open question:** whether quotes send STEP or 3MF per process (vendor
  pipelines differ) — decide per adapter with PRD-017's export work.

## Competitive references

Xometry add-ins inside SolidWorks/Fusion/Onshape and the ~$50M
Siemens-Xometry NX embedding: single-vendor quoting inside closed hosts.
Protolabs: DFM with every quote, at their portal. SimScale: agentic sim
via API but disconnected from authoring. Backflip: scan→CAD GA, drafts
with errors. Slicers: excellent CLIs, no CAD integration
(market_research.md, "The workflow ring", "AI-native CAD"). We differ by:
one open connector extension point instead of N vendor add-ins,
multi-vendor normalization behind a single tool, DFM-before-quote ordering
(PRD-021), kernel-refereed drafts for scan input, agent fidelity routing
across sim tiers — and the whole ring reachable by an agent as one errand.
