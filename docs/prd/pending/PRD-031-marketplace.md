# PRD-031 — Marketplace & community hub

- **Status:** pending — **split.** The seeded read-only catalog (browse/search/listing/customizer + add-to-library) is carved into [PRD-031a](../in-progress/PRD-031a-marketplace-catalog.md) (marketplace chain step 4, needs only 011 · 005a · 007). This PRD retains the **031b remainder**: open publishing and everything that runs third-party code on our servers (needs PRD-006). See "Carved out to PRD-031a" below.
- **Phase:** v6 — moats
- **Created:** 2026-08-09
- **Origin:** founder idea #1e (Aug 2026), engineering-reviewed; grounded by dedicated research (market_research.md, "Marketplace & community")
- **Depends on:** PRD-005 (identity/hosting — hard) · PRD-007 (customizer preview — hard) · PRD-011 (package format & validation — hard) · PRD-006 (sandbox — hard, the safety boundary)
- **Related:** PRD-031a (the seeded read-only catalog, in progress) · PRD-029 (skills as a second content type), PRD-021 (rule packs as a third), PRD-024 (quality scoring), PRD-025 (Market workspace)

## Carved out to PRD-031a (18 Aug 2026)

Following the founder decision in [roadmap.md](../../roadmap.md), "Sequencing decision —
the marketplace chain (16 Aug 2026)", the **seeded read-only catalog** is its own
PRD ([PRD-031a](../in-progress/PRD-031a-marketplace-catalog.md), chain step 4).
The fault line is execution: anything that **reads/serves the platform-seeded
catalog and adds to a library** is 031a and needs only completed deps
(011 · 005a · 007); anything that **executes third-party uploaded code on our
servers** or **accepts external publishing** is retained here as 031b and is
blocked on PRD-006 (chain step 5). This PRD stays `pending` with the retained
remainder below.

| | Moved to PRD-031a (step 4) | Retained here (031b, needs PRD-006) |
|---|---|---|
| **FR1** listings + public read | ✓ served from the bundled `catalog/` via 005a public read (no cloud publish pipeline) | the cloud publish/storage pipeline for author-uploaded listings |
| **FR2** publish gate (AST/policy/signing) | — | ✓ static AST gate, policy/malware/name-squat scan, signing |
| **FR3** sandbox-only execution + rebuild limits | the customizer rebuild limits are reused from PRD-007 for *seeded* content | ✓ sandbox-confined execution of *uploaded* code (the PRD-006 blocker) |
| **FR4** remix/ancestry, license-constrained | — | ✓ |
| **FR5** add-to-library, lockfile pin, provenance in artifacts | ✓ the existing authenticated `add_package`/`use_part`; `market_install` | artifact-embedded provenance metadata (3MF/STEP header) beyond the header we ship |
| **FR6** identity & trust tiers | — | ✓ verified publisher / curated shelf |
| **FR7** moderation, takedown, name-reuse protection | — | ✓ |
| **FR8** local-mode read-only browse/install | ✓ (self-hosted browses the public catalog, no account) | "publishing requires a cloud identity" (nothing to publish until 031b) |
| **FR9** economy (free/paid, payouts) | — | ✓ |
| **FR10** quality surfacing | ✓ the `gate: green` validated badge, read-only | makes/uses counters, curated collections, install-stats |
| **AC1** browse + customizer + STEP download | ✓ | — |
| **AC2** publish gate rejects `import os` / breaking geometry | — | ✓ |
| **AC3** installed package pins version in lockfile | ✓ (PRD-011 inherited) | — |
| **AC4** remix ancestry / CC-ND block | — | ✓ |
| **AC5** agent publishes with `disclosure: agent`; `market_search` filters | `market_search` (as anonymous `/search`, and the disclosure filter surface) | *publishing* a listing |
| **AC6** removed-listing name cannot be re-registered | — | ✓ |
| **AC7** self-hosted browses/installs without account; publish refused | ✓ browse/install | publish-refused message (no publish path until 031b) |

The remaining FRs and ACs in this file are the **031b** scope. Their acceptance
is deferred until PRD-006 makes third-party server-side execution safe.

## Problem & motivation

Founder idea #1e: "an observable marketplace where people/agents share
their files/models/projects and anyone can add them to their library and
re-use or learn." The research says the lane is genuinely open: consumer
platforms (MakerWorld, Printables) prove massive demand for parametric
slider-consumption but cap out at hobbyist meshes; engineering catalogs
(McMaster 700k parts, TraceParts 100M) are closed, vendor-fed, and
non-contributable; GrabCAD has 7M engineers and zero validation or
parametrics. **Nobody offers community-contributed, kernel-validated,
parametric code components with STEP/drawing outputs and standards
metadata** — a "community McMaster-Carr" (market_research.md, "Marketplace
& community", "The engineering gap").

The same research defines the hard constraint: our models are *code*, and
every executable-content marketplace gets attacked (VS Code removed 110
malicious extensions in a year despite signing+scanning; npm's Shai-Hulud
worm propagated through 500+ packages in Sept 2025). The structural answer
is already in our architecture: **marketplace code never executes on a
consumer's machine by default** — it runs in the server-side sandboxed
kernel, and consumers receive parameters, previews, and generated artifacts
(STEP/3MF/drawings). That inversion (the "safetensors move") is both the
safety boundary and the differentiator.

## Users & jobs

- **Consumer engineer/maker:** find a proven component ("NEMA 17 mount",
  "GT2 idler", "IP54 enclosure"), preview and customize it (PRD-007),
  add it to a library (PRD-011), and drop it into an assembly mate-ready.
- **Author (human, agent, or hybrid):** publish a component/project/skill
  once; get attribution, usage stats, and optionally revenue.
- **Learner:** read real, working parametric code with its drawings and
  specs — the marketplace as a corpus of engineering craft.
- **Agent:** search programmatically, cite provenance, and propose
  library additions to its human ("this bracket exists — reuse instead
  of remodeling?").
- **Curator/moderator:** keep the shelf trustworthy at scale.

## Goals

- G1. Publish/discover/consume for three content types on one identity
  and review substrate: **part packages** (PRD-011 format), **projects**
  (full worked examples), **skills** (PRD-029); rule packs (PRD-021)
  follow the same path later.
- G2. Every listing is kernel-validated at publish and on every version:
  builds at parameter extremes, specs pass, connectors check, drawings
  regenerate — the "kernel-validated" badge is the platform's core
  quality signal.
- G3. Consumption without execution: browse, customize (server-rebuilt
  preview via PRD-007), download artifacts, or add-to-library — reading
  the code is encouraged; running it locally is an explicit, flagged
  choice.
- G4. Provenance & licensing as first-class data: author identity
  (verified tiers), human/agent/hybrid authorship disclosure, license
  menu (CC family + permissive code licenses), remix trees with
  preserved attribution, immutable signed versions.
- G5. An economy that resists gaming: no points-for-downloads (the
  MakerWorld farming lesson); free/CC tier + optional paid listings with
  ~80/20 author-favoring revenue share; curation (featured, verified
  engineering shelf) over leaderboards.
- G6. Agent-native throughout: search/install/publish are tools; agents
  are legitimate authors (disclosed) and legitimate reviewers
  (validation + automated review runs), never silent.

## Non-goals

- Local execution of marketplace code by default (opt-in only, with
  provenance warning) — the safety boundary.
- A general 3D-mesh asset store (decorative STL business — Meshy/CGTrader
  territory; wrong artifact, per the analysis).
- Points/rewards economies and paid-boost mechanics (farming magnets).
- Owning manufacturing fulfillment (PRD-022 connectors handle quoting).
- Federation/self-hosted marketplace mesh at v1 (the open-source registry
  protocol from PRD-011 keeps the door open).

## Experience

**Consumer path.** The Market workspace (PRD-025): search + category
browse (fasteners, motion, enclosures, frames, examples, skills…) with
filters (standards, license, validated-badge, works-with version). A
listing page shows: customizer preview (live params → server rebuild →
viewport), generated artifacts (STEP/STL/3MF/drawing previews), the code
(read-only with syntax highlight — learning is a feature), specs and their
pass state, provenance (author, authorship disclosure, remix ancestry),
license, versions, stats. Actions: Add to library (project or org
library, PRD-011 lockfile pins the version) · Download artifacts · Remix
(fork into your project with attribution recorded).

**Author path.** From a project: "Publish…" wizard → pick content
(part/package/project/skill) → license + disclosure + description
(agent-drafted from the code and drawings, human-approved) → the publish
gate runs (validation suite + static AST gate + policy scan) → signed
immutable version goes live. Updates create versions; yanking hides but
never breaks pinned consumers (npm lesson).

**Agent path.** `market_search {query, filters?}` ·
`market_get {listing}` · `market_install {listing, version?}` (→ library,
PRD-011 mechanics) · `market_publish {…}` (same gate; agent authorship
auto-disclosed). Agents cite listings in proposals ("using iso7380-m5
v1.2.0 from @fastener-guild, kernel-validated").

## Functional requirements

**Platform**
- FR1. Listings with immutable versioned artifacts: source bundle,
  generated previews/artifacts per version, metadata (license, standards
  refs, authorship disclosure, requires/capabilities); served from the
  cloud service (PRD-005) with public read.
- FR2. Publish gate, in order: (a) PRD-011 kernel validation (param-
  extreme builds, specs, connectors, drawings); (b) static AST gate —
  import allowlist (build123d + approved toolkit/stdlib subset), no
  exec/eval/dynamic-import/dunder escapes; (c) policy scan (malware
  signatures, name-squatting distance check against existing listings);
  (d) signing (platform signature over content hash). Every gate failure
  is a structured, author-visible report.
- FR3. Sandbox-only execution: all marketplace builds/rebuilds run in the
  PRD-006 confined workers with quotas; customizer rebuild rate limits
  per listing/visitor.
- FR4. Remix: fork records `{ancestor, version}` in the package metadata;
  ancestry renders on listings; licenses constrain remix options
  (CC-ND blocks remix-publish, etc. — enforced at publish).
- FR5. Consumption: add-to-library pins exact versions (lockfile);
  artifact downloads carry provenance metadata (generator version,
  content hash) embedded where formats allow (3MF metadata, STEP header).
- FR6. Identity & trust tiers: verified email (baseline) < verified
  publisher (2FA + review) < curated shelf ("verified engineering":
  human+agent review, standards spot-checks); tier shown on listings.
- FR7. Moderation: report flow, staged takedown (hide → review → remove),
  name-reuse protection for removed listings (VS Code hijack lesson),
  audit trail; abuse-resistant stats (installs by unique identity, not
  raw downloads).
- FR8. Local-mode story: self-hosted instances browse the public market
  read-only (no account) and install into local libraries; publishing
  requires a cloud identity.

**Economy**
- FR9. Free tier: open licenses, unlimited listings. Paid listings
  (later phase): platform take ≤20%, payout infra via a standard PSP;
  no exclusivity requirements, no boost purchases.
- FR10. Quality surfacing: validated badge (automatic), makes/uses
  counter (from installs + customizer exports), curated collections;
  no global points leaderboard.

## Agent surface

New tools: `market_search {query, filters?}` · `market_get {listing,
version?}` · `market_install {project, listing, version?}` ·
`market_publish {project, content, license, disclosure, description?}` ·
`market_report {listing, reason}`. Events: `market_installed {project,
listing, version}`. All registered only when a marketplace endpoint is
configured (capability rule).

## Technical approach

- **Service side (cloud, PRD-005):** listings/versions/identity/
  moderation as the cloud app's domain; storage = object store for
  bundles/artifacts + the same git-substrate for source versions;
  validation farm = the standard kernel pool under PRD-006 confinement
  with a job queue (PRD-020 machinery).
- **Client side:** Market workspace UI (PRD-025) over public REST;
  install path reuses PRD-011's package manager verbatim (a marketplace
  is a registry index + a web front + an economy).
- **Static gate:** `ast`-based checker shared with PRD-011's publish CLI
  and PRD-029's skill lint (one policy module, three consumers).
- **Signing:** platform-side signature (sigstore-style keyless or
  platform key — decide in design) verified by clients on install.
- **Seed content:** the agent-built COTS library (PRD-011), the bundled
  examples as projects, core skills (PRD-029) — the shelf is never
  empty on day one.

## MVP & phasing

- **MVP:** public read-only market (search/browse/listing pages with
  customizer preview + artifact downloads) serving platform-seeded
  content; add-to-library for logged-in users; publish gate operational
  for internal/curated authors only.
- **Phase 2:** open publishing with verified-publisher tier, remix trees,
  reports/moderation, skills listings.
- **Phase 3:** paid listings + payouts, curated engineering shelf with
  review workflow, rule-pack listings, install-stats API.

## Acceptance criteria

- AC1. A visitor (logged out) finds the NEMA-17 mount, moves two sliders
  (server rebuild), downloads STEP of their variant — code never ran on
  their machine (verified by design: no wasm/kernel client-side; browser
  session test).
- AC2. Publish gate: a package with `import os` + file write fails the
  AST gate with the violation named; a geometry that breaks at param max
  fails validation with the kernel error attached (tests).
- AC3. Installed package pins its version in the project lockfile; a
  later listing update does not change the consumer's build until
  explicit upgrade (test via PRD-011).
- AC4. Remix of a CC-BY listing records ancestry and renders attribution;
  remix-publish of a CC-ND listing is blocked at the gate (tests).
- AC5. An agent publishes a generated bracket with `disclosure: agent`;
  the listing shows the badge; `market_search` filters by disclosure
  (end-to-end test).
- AC6. A removed listing's name cannot be re-registered by a different
  identity (hijack test).
- AC7. Self-hosted instance browses and installs from the public market
  without an account; publish from it is refused with a clear message
  (test).

## Risks & open questions

- **Cold start:** seeded shelf + customizer links (PRD-007 embeds) as
  the growth loop; success metric is installs-into-projects, not listing
  count.
- **Moderation load:** automated gates catch the mass; the curated shelf
  concentrates human effort where trust is sold; budget a review SLA in
  design.
- **License compatibility** questions (CC for code vs content) need a
  reviewed license menu with plain-language guidance (design spec with
  actual OSS-legal references).
- **Agent-generated flood** degrading search: disclosure + validation +
  install-weighted ranking; revisit with data.
- **EU/consumer rules for paid digital goods** (VAT, withdrawal) — scope
  paid phase to jurisdictions the PSP handles cleanly.

## Competitive references

MakerWorld (parametric customizer at scale; points-farming failure),
Printables (curation + tangible rewards), Thingiverse (neglect decay),
Cults (80/20 split), GrabCAD (engineers, zero validation), McMaster/
TraceParts (closed catalogs) — market_research.md, "Marketplace &
community". We differ: kernel-validated parametric code with engineering
artifacts, execution kept server-side by design, provenance/disclosure as
data, and agents as first-class authors and librarians.
