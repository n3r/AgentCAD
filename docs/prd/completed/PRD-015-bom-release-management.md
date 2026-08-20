# PRD-015 — BOM and release management

- **Status:** completed — merged to main in PR #28 (per-config BOM part-number override and the warn/soft gate checks deferred; unblocks PRD-014's FR4/FR5)
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-001 (hard — releases pin immutable version tags) · PRD-002 (hard — approvals ride proposals) · PRD-003 (hard — "released implies spec-green") · PRD-004 (soft — CI check on release) · PRD-012 (soft — per-config identity) · PRD-013 (soft — sub-assembly quantity roll-ups) · PRD-011 (soft — sourcing links from package metadata)
- **Related:** PRD-014 (drawings in the bundle, revision block), PRD-023 (release notes/README generation), PRD-021 (cost models refine cost fields)

## Problem & motivation

AgentCAD can tell you an assembly's total mass, and nothing else a buyer,
builder, or auditor needs: no part numbers, no quantities ("three of these
screws" is three manifest entries), no costs, no revision identity, no way to
say "this exact state is what we sent to the shop" and prove it later. Teams
at our target size run BOMs in spreadsheets that silently diverge from CAD
until an ECO disaster, and "releases" are a folder of exports someone
remembered to make.

The competitive evidence is unusually direct: **"PDM built in" is Onshape's
killer sales pitch** — release workflows assigning revisions against
immutable versions, no vault, no check-in/out (market_research.md,
"Cloud-native CAD: Onshape"). The gap matrix rows "Release management /
revisions" (**build, on git substrate**) and "Structured BOM + exports"
(**build**) both point here. Our substrate makes the expensive part free:
versions are already git states (PRD-001 tags), approvals are already
proposals (PRD-002), and "does this state meet spec" is already executable
(PRD-003/004). We get Onshape's top purchase driver with zero vault
infrastructure — and a release becomes something an agent can *assemble* and
a human merely approves.

## Users & jobs

- **Design engineer (human):** stamp part numbers and costs once, in the
  model, and never reconcile a spreadsheet again.
- **Release manager / lead (human):** cut Rev B knowing exactly what changed
  since Rev A, that specs are green, and that the bundle a supplier receives
  regenerates identically from the tag.
- **Buyer / builder (human, possibly external):** open the BOM in a
  spreadsheet with quantities rolled up correctly; follow sourcing links to
  order.
- **Release agent:** "cut Rev B of the test stand" as one task — assemble
  the bundle, draft release notes from history, route approval to a human
  (the PRD-002 trust boundary: agents propose, humans release).
- **Downstream tooling (PRD-014/023, automation):** read BOM lines and
  release state programmatically for balloons, tables, and docs.

## Goals

- G1. A structured BOM is derived from the model on demand — part numbers,
  per-config identity, material, mass, rolled-up quantities across patterns
  and sub-assemblies, cost, sourcing — never hand-maintained.
- G2. BOMs export losslessly to CSV and JSON, and open correctly in
  spreadsheet tools (Sheets/Excel) with sane headers and quoting.
- G3. Revisions (Rev A, B, …) are a state machine riding proposals: draft →
  in review → released → superseded, every transition attributed.
- G4. A release pins a PRD-001 tag immutably and carries a reproducible
  bundle: STEP + drawings + BOM + flat patterns + README.
- G5. "Released implies green": a release cannot complete while specs
  (PRD-003) fail — or the failure is explicitly waived and recorded.
- G6. Editing a released state is impossible; evolving it requires a branch
  — the git substrate enforces what vault PDM polices.

## Non-goals

- ERP/MRP integration, purchase orders, inventory — export the BOM; stop.
- Change-order ceremony (ECO forms, CCB routing) — proposals + releases are
  the workflow; enterprise ceremony is a documented incumbent liability
  (market_research.md, "What we deliberately will not build").
- Per-seat approval hierarchies and e-signature compliance (21 CFR 11) —
  identity-attributed approvals via PRD-002/PRD-005 principals, nothing
  more, until demanded.
- Cost *estimation* from geometry/process — PRD-021; here cost is a field
  (manual or material-derived) that rolls up.
- Where-used queries across projects and registry-wide part numbering —
  PRD-011's territory.

## Experience

**Human path.** A "BOM" view on the Assembly node: the table (item, qty,
part number, name, config, material, unit mass, unit cost, extended cost,
source), footer totals, an export button (CSV/JSON), and inline editing for
the fields that are inputs (part number, unit cost, source link) — edits
write to the manifest like any parameter edit. A "Releases" panel lists
revisions with status chips; "Cut release…" asks for notes, shows the gate
report (specs green? CI green? uncommitted changes?), and opens a proposal
for approval. Approving (PRD-002 review UI) finalizes: the tag is created,
the bundle builds in the background, and the release row gains a
"bundle ready" link. A released row is visibly locked; "start Rev C" creates
a branch.

**Agent path.** `get_bom {project}` → structured lines; `set_bom_fields` for
part numbers/costs; `release_start {project, notes?}` → returns the gate
report and the proposal id; a human (or a permitted principal) approves via
PRD-002's `approve_proposal`; `release_finalize` (or auto-finalize on
approval) pins the tag and runs the bundle job, returning artifact paths.
`release_notes_draft` (PRD-023 seam) writes the README/notes from history
between tags.

**Handoff.** The agent assembles everything and stops at the approval; the
human's approve click is the release. Afterward either can fetch the bundle
or re-verify its reproducibility.

## Functional requirements

**BOM**
- FR1. `get_bom` computes lines from the manifest + resolved assembly:
  one line per (part, config) with `qty` summed across instances, pattern
  members (PRD-013 patterns count as N), and sub-assembly recursion
  (multiplication through levels); `structure: flat|indented` returns either
  the rolled-up flat BOM or the indented tree with level numbers.
- FR2. Line fields: `item` (stable ordinal), `part_id`, `part_number`,
  `label`, `config` (PRD-012, empty otherwise), `material`, `unit_mass_g`
  (from cached metrics; per-config metrics when configured),
  `unit_cost_usd`, `ext_cost_usd`, `qty`, `source` (supplier/url). Reference
  parts appear with their import source; package parts (PRD-011) inherit
  `part_number`/`source` from package metadata unless overridden.
- FR3. `part_number` and `unit_cost_usd`/`source` are manifest fields set
  via `set_bom_fields`; absent `unit_cost_usd` falls back to
  `unit_mass_g × material.cost_usd_kg / 1000` when the material carries
  `cost_usd_kg`, flagged `cost_source: material_estimate` so estimates are
  never mistaken for quotes.
- FR4. Exports: `export_bom {format: csv|json}` writes
  `exports/bom.<ext>` — CSV with RFC-4180 quoting and a header row that
  opens cleanly in Google Sheets/Excel; JSON mirrors FR2 exactly. Totals row
  (mass, cost) included in CSV, separate keys in JSON.
- FR5. BOM computation is read-only and deterministic at a ref: `get_bom`
  accepts `ref?` (branch/tag per PRD-001) so the BOM of a release is
  reproducible after the fact.

**Revisions & releases**
- FR6. A release record: `{name, rev, status: draft|in_review|released|
  superseded, tag, proposal, notes, approvals: [{principal, ts}], bundle}`.
  Revisions auto-sequence A, B, … per project; releasing Rev B marks Rev A
  `superseded`. Records live in the manifest's `releases` section (merged
  key-wise by PRD-001's driver).
- FR7. `release_start` runs the gate: working state clean on the release
  branch, `run_specs` green (PRD-003), CI check green when configured
  (PRD-004), all sub-assembly refs version-pinned (PRD-013 FR/AC), drawings
  regenerable. It returns the gate report and opens a PRD-002 proposal
  carrying it; a red gate leaves the release in `draft` with each failing
  check named.
- FR8. Spec-gate override: `waive: {reason}` records an explicit waiver into
  the release record and the audit trail; silent overrides are impossible.
- FR9. Approval of the release proposal (PRD-002 flow) transitions to
  `released`: a PRD-001 tag `release/<rev>` is created and registered as
  referenced (tags with referrers cannot be deleted or moved — PRD-001 FR5),
  and the bundle job starts.
- FR10. The bundle is a directory + zip under `exports/releases/<rev>/`:
  STEP per part and assembly, drawings (PRD-014 PDF+SVG, regenerated at the
  tag), `bom.csv` + `bom.json` (FR4 at the tag), flat patterns for every
  sheet-metal part (`flat_pattern`), and `README.md` (release name, notes,
  gate report, artifact manifest). An `artifacts.json` lists every file with
  sha256.
- FR11. Reproducibility: re-running the bundle at the same tag yields the
  same `artifacts.json` hashes for every deterministic artifact (drawings,
  BOM, flat patterns, README); STEP files are compared with their timestamp
  header lines normalized, and any nondeterministic artifact class is
  explicitly listed in the bundle README — honesty over hand-waving.
- FR12. Immutability: any mutating tool against a released tag's state is a
  `conflict_error` directing to branch (PRD-001 semantics); release records
  themselves are append-only (status transitions, never rewrites).

## Agent surface

New tools: `get_bom {project, ref?, config?, structure?}` ·
`export_bom {project, format, ref?}` ·
`set_bom_fields {project, part_id, part_number?, unit_cost_usd?, supplier?,
url?, config?}` · `release_start {project, notes?, waive?}` ·
`release_finalize {project, rev}` (idempotent; normally auto on approval) ·
`list_releases {project}` · `get_release {project, rev}` (record + gate
report + artifact list).
Changed: PRD-002 proposal objects gain a `release` kind; PRD-014
`generate_drawing` is invoked at-ref by the bundle job.
Events: `release_changed {project, rev, status}`; bundle progress rides the
job events (PRD-020 when it lands; synchronous with progress logs before).
Errors: `conflict_error` on released-state mutation (FR12);
`validation_error` with `details.gate` listing failing checks (FR7).

## Technical approach

- **BOM builder** — pure function over the manifest + resolved assembly in
  `agentcad/core/bom.py` (no kernel calls; masses come from cached metrics,
  so a BOM never triggers rebuilds — stale metrics are reported as a warning
  naming the unbuilt parts). Exposed as a service seam so PRD-014 balloons
  and PRD-023 docs consume the same lines.
- **Tool pack** `tools_bom.py` + **route pack** `routes_bom.py`
  (BOM view + releases panel endpoints); cores untouched per the
  extension-point contract.
- **Release engine** — `agentcad/core/releases.py`: gate orchestration
  (calls `run_specs`, the PRD-004 checker, PRD-001 tag ops through the
  store), the state machine, and the bundle job (export orchestration
  reusing existing `export_part`/`export_assembly`/`generate_drawing`/
  `flat_pattern` paths against a temp worktree checked out at the tag —
  the same staged-worktree mechanism PRD-001's merge validation uses).
- **Storage:** manifest `releases` + per-part `bom` fields (schema bump,
  old files load); bundles under `exports/releases/` (untracked, derived —
  regenerable from the tag by construction).
- **Frontend:** `tree.js` gains the BOM/Releases views; inline field edits
  go through the same PATCH plumbing as parameters; release flow reuses
  PRD-002's proposal UI.
- **Attribution:** approvals and waivers record the client identity
  (`X-Agent-Id` / authenticated principals once PRD-005 lands) — the audit
  answer to "who released this."

## MVP & phasing

- **MVP:** BOM builder + `get_bom`/`export_bom` (flat structure, quantities
  from today's flat instances), `set_bom_fields`, CSV/JSON exports, BOM
  view in the UI (FR1–FR5 minus sub-assembly recursion).
- **Phase 2 (with PRD-001/002/003):** revision state machine, release gate,
  proposal-backed approval, tag pinning, immutability (FR6–FR9, FR12).
- **Phase 3 (with PRD-014, PRD-013):** full bundles with drawings and
  roll-ups through sub-assemblies/patterns, reproducibility manifest
  (FR10–FR11); Sheets-verified exports; PRD-023 release notes drafting.

## Acceptance criteria

- AC1. The rocketry project cuts a release end-to-end in the browser:
  gate report shown, approval recorded with principal and timestamp, tag
  created, bundle downloadable — zero terminal use (browser session +
  test).
- AC2. Quantity roll-ups: a fixture with a sub-assembly instanced twice,
  each containing an 8-member bolt pattern, yields one screw line with
  `qty: 16`; flat and indented structures agree (test; with PRD-013).
- AC3. `exports/bom.csv` re-imports into a spreadsheet losslessly: parsed
  by a strict CSV reader in the test with expected headers, quoting
  round-trips a label containing commas/quotes, and totals match the JSON
  export (test; manual Sheets open once).
- AC4. A failing spec (PRD-003) blocks `release_start` with the failing
  check named in `details.gate`; adding `waive: {reason}` proceeds and the
  waiver appears in `get_release` (test).
- AC5. Mutating a part on the released tag's state is a `conflict_error`;
  branching from the tag and editing there succeeds (test, per PRD-001
  semantics).
- AC6. Bundle reproducibility: two bundle runs at the same tag produce
  identical `artifacts.json` hashes for drawings/BOM/flat patterns/README,
  and STEP files match after timestamp-line normalization (test).
- AC7. Per-config identity: a three-config flange yields three BOM lines
  with distinct part numbers per the config suffix rule and per-config
  masses (test; with PRD-012).
- AC8. Full suite green; projects without releases/BOM fields behave exactly
  as today.

## Risks & open questions

- **Part-number policy** (auto-assign scheme, uniqueness scope, config
  suffix format) has real-world religion attached. v1: free-text field +
  optional per-project auto-scheme + uniqueness warning, not enforcement;
  registry-scoped numbering waits for PRD-011.
- **STEP nondeterminism** (writer timestamps, potential ordering drift
  across OCCT versions) limits FR11 to normalized comparison; document the
  normalization precisely and pin the toolchain per release environment.
- **Gate latency:** a full spec + CI + drawing regeneration gate on a large
  project could take minutes; run it as a background job with progress
  (PRD-020 alignment) rather than a blocking call.
- **Superseded ≠ obsolete:** teams sometimes need to mark a revision
  withdrawn (not just superseded); add an `obsolete` transition when
  demanded rather than modeling the full lifecycle now.
- **Cost honesty:** material-derived estimates (FR3) risk being read as
  quotes; the `cost_source` flag and UI labeling must survive into CSV
  exports — a dropped column here misleads a buyer.

## Competitive references

Onshape: release management assigning revisions against immutable versions,
no vault — its single biggest purchase driver; we match the outcome on a git
substrate where versions are tags and approvals are proposals
(market_research.md, "Cloud-native CAD: Onshape", gap matrix "Release
management / revisions"). Vault PDM (SolidWorks PDM et al.) and enterprise
PLM ceremony are documented liabilities we deliberately do not inherit
("What we deliberately will not build"). We differ by: BOMs derived from the
model rather than maintained beside it, releases whose bundles regenerate
reproducibly from the tag, spec-green enforcement wired into the gate
(PRD-003/004) — and a release an agent can assemble end-to-end with a human
approval as the only manual step.
