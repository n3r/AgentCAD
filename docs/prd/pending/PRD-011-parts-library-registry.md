# PRD-011 — Parts library and package registry

- **Status:** pending
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026) + founder idea #1d ("Library" tab, Aug 2026)
- **Depends on:** PRD-003 (hard — the publish gate runs specs) · PRD-005 (soft — hosted cloud index)
- **Related:** PRD-010 (holes pair with fasteners), PRD-012 (package presets align with configurations), PRD-013 (sub-assembly packages later), PRD-025 (the Library workspace surfaces this), PRD-031 (the public marketplace layer above)

## Problem & motivation

Standard content is assumed by every daily-driver CAD: SolidWorks Toolbox is
part of the "professional depth" definition, Onshape ships Standard Content,
and McMaster-Carr is the de-facto standard-parts infrastructure engineers
design around (market_research.md, "The desktop incumbents", "Open-source
CAD: FreeCAD, code-CAD, and the Ondsel lesson"). AgentCAD has bd_warehouse
threads and nothing else — every project re-derives its fasteners, bearings,
and motor mounts from scratch, and nothing a user builds is reusable across
projects except by copy-paste.

The open territory is bigger than parity: "npm-for-parts is unclaimed" —
PartCAD sits at 483 stars with its registry "in progress"
(market_research.md, "Open-source CAD"). The Gap matrix verdict is
**build-differentiated: agent-validated open registry**. The differentiation
is economic: a *validated* parts registry was always prohibitively
labor-intensive to curate by hand, which is why Toolbox content is closed
and community libraries are unreliable. Agents flip that — they generate,
test, repair, and document packages at scale while the kernel referees every
one (builds at parameter extremes, specs pass, connectors mate). The
interface contract already exists: typed PARAMS, `connectors(p, part)`, and
SPECS (PRD-003) are exactly a package's public API, and the param-extremes
build harness is exactly what the examples test suite runs today.

## Users & jobs

- **Part consumer (human):** search "608 bearing", drop it into the
  project, mate it — and never model a screw again.
- **Part consumer (agent):** `use_part` a mate-ready fastener mid-task;
  read package docs, params, and connectors as structured context.
- **Package author (human or agent):** wrap a parametric family with
  PARAMS/connectors/specs/docs and publish it past the validation gate.
- **Org librarian:** curate a private library (McMaster wraps, company
  standard parts) their team and agents pull from, surfaced by the PRD-025
  Library workspace.
- **Marketplace publisher (later):** PRD-031's public layer rides this
  exact format and gate.

## Goals

- G1. A package format that *is* the part contract, versioned: scripts with
  typed PARAMS + connectors + SPECS + presets + docs, semver'd,
  content-hashed.
- G2. Projects declare dependencies; a lockfile in the manifest pins exact
  versions and hashes; installs are reproducible and work offline from the
  local cache.
- G3. Three index kinds with one client shape: local directory, git-hosted
  (a repo is an index), and cloud registry (personal/org scopes riding
  PRD-005).
- G4. Search that serves agents and humans: structured name/keyword/param
  filters always; semantic search over descriptions and docs when an
  embedding provider is configured.
- G5. Publishing gated on kernel validation — builds at every parameter
  extreme, specs pass, connectors resolve and mate — so "it's in the
  registry" *means* something. A corrupted package cannot publish.
- G6. Useful on day one: bd_warehouse wrapped as packages; an agent-built
  COTS starter set (ISO/DIN fasteners, bearings, extrusions, NEMA motors);
  a McMaster-STEP ingestion path producing private mate-ready packages.

## Non-goals

- Marketplace UX — discovery feeds, ratings, monetization — PRD-031 (the
  public layer above this substrate).
- Sub-assembly packages — after PRD-013 lands assembly structure; v1
  packages contain parts.
- A package-specific scripting dialect — package parts are ordinary part
  scripts, portable by construction.
- Automatic dependency upgrades — installs are pinned; upgrades are
  explicit (no left-pad events in anyone's rocket).

## Experience

**Agent path.** `search_packages {"query": "M5 socket head cap screw"}` →
hits with name, version, summary, param digest.
`add_package {"project": "rig", "name": "iso4762"}` records the dependency
+ lockfile entry and populates the cache.
`use_part {"project": "rig", "package": "iso4762", "part": "cap_screw",
"preset": "M5x16", "part_id": "screw_1"}` materializes a part with preset
params and connectors ready; `set_mate` drops it onto a PRD-010 tapped
hole. To publish, an agent authors the package directory and runs the gate:
failures name the failing check ("build failed at length=max for size=M3"),
the agent fixes and re-validates — the kernel referees curation.

**Human path.** A Library surface (a dialog first; the PRD-025 Library tab
when it lands) lists installed packages and searches configured indexes
with preview renders and param tables; "Add to project" picks a preset and
the part appears in the tree. Org admins see the publish queue and
validation reports on the cloud index.

**Handoff.** "Fasten the lid with M4 screws from the library" in chat uses
the same tools; everything installed is visible, pinned, and diffable in
`project.json`.

## Functional requirements

**Package format**
- FR1. A package is a directory/archive: `package.json` (name matching
  `[a-z][a-z0-9_-]{0,39}`, semver version, summary, keywords, license,
  authors, minimum AgentCAD version), `parts/*.py` (the standard
  part-script contract — PARAMS, `build`, optional `connectors` and SPECS),
  `presets.json` (named param sets per part; schema shared with PRD-012
  configurations), `docs/README.md`, `previews/*.png` (render_view output),
  optional `imports/*.step` for reference-part packages.
- FR2. A package version resolves to the sha256 of its canonical archive;
  the hash is recorded on install and verified from cache — tampering is
  detected, not trusted.

**Dependencies & lockfile**
- FR3. `project.json` gains `packages` (name → version requirement +
  source) and `packages_lock` (name → exact version, hash, source);
  the lock updates only through explicit add/update operations.
- FR4. `use_part` materializes the part into the project's `parts/` with a
  provenance header (`package@version`, hash) — projects stay
  self-contained git repos that clone, push, and build (PRD-005) with no
  registry reachable. Re-materialization from cache is idempotent.
- FR5. Local cache at `~/.agentcad/packages/<name>/<version>/`,
  content-verified; installs resolve from cache when offline.
- FR6. `remove_package` drops the dependency and lock entries;
  materialized parts remain (they are project files) and their provenance
  reports the package as removed — a warning, not breakage.

**Indexes & search**
- FR7. Index kinds: local path (config-registered directory), git (a repo
  containing `index.json` + package archives, added by URL, fetched via
  git), cloud (a PRD-005 instance with personal and org scopes,
  role-guarded). Multiple indexes configure with precedence; all three
  answer the same client shape.
- FR8. `search_packages` runs structured filters (name, keywords, param
  names/ranges) against all configured indexes; semantic search (embeddings
  over summary/docs/param descriptions) applies when the instance has an
  embedding provider configured and degrades to keyword search honestly —
  never a hard dependency.

**Publishing & validation**
- FR9. `agentcad publish <dir> --index <name>` (and the cloud publish
  route) runs the gate through the normal service/kernel path: every part
  builds at every parameter's min and max (the examples-suite harness
  generalized), SPECS pass (PRD-003 `run_specs`), every connector resolves
  and completes a smoke mate on a scratch project, presets validate against
  PARAMS, previews and docs exist. The report lists every check; any
  failure blocks publish with the failing check named.
- FR10. Published versions are immutable; republishing an existing version
  is a `conflict_error`; yanking marks-not-deletes so existing lockfiles
  keep resolving.

**Seed content**
- FR11. bd_warehouse wrap: cap screws, hex bolts, and threaded inserts as
  packages with presets and connectors (axis + head-seat), exposing the
  cosmetic-vs-real thread choice the threads toolkit documents.
- FR12. Agent-built COTS starter set, every entry passing the same gate
  (the gate *is* the curation): ISO 4762/4014/7380 fasteners, DIN 625
  (608-class) bearings, 2020/3030 extrusions, NEMA 17/23 motor outlines —
  each with connectors, specs asserting interface dims, and docs.
- FR13. McMaster-STEP ingestion: a flow over the `import_cad_file`
  precedent (`package_from_step`) that wraps a vendor STEP as a
  reference-part package, agent-assists connector placement (via
  `face_info` and detected features), and confines vendor-licensed content
  to personal/org indexes — never public — with the tool saying so.

**Integrity**
- FR14. The registry client performs no kernel imports (data and files
  only); all validation runs through the normal service → kernel pipeline.
- FR15. `get_project`/`get_part` expose package provenance; everything is
  additive — projects without packages are byte-identical in behavior.

## Agent surface

New tools: `search_packages {query, index?, limit?}` ·
`add_package {project, name, version_req?, index?}` ·
`remove_package {project, name}` · `list_packages {project?}` (installed +
configured indexes) ·
`use_part {project, package, part, part_id, preset?, params?}` ·
`package_from_step {project, source, name, connectors?}` (phase 3).
CLI: `agentcad publish <dir> --index <name>` · `agentcad package validate
<dir>` (run the gate locally without publishing).
Errors: `validation_error` with `details.checks[]` for gate failures;
`conflict_error` for version collisions; `not_found_error` for unresolvable
names — the house structured contract throughout.
Events: `project_changed` on add/remove/use (normal store writes).

## Technical approach

- **Core module** `agentcad/core/packages.py` (format, cache, lockfile,
  index clients) + **tool pack** `agentcad/core/tools_packages.py` +
  **route pack** `agentcad/server/routes_packages.py`; cloud index routes
  land under PRD-005's tenancy. Extension-point contract; cores untouched.
- **Validation gate as orchestration over existing seams:** the service
  rebuild pipeline runs the param-extremes matrix (kernel pool fan-out,
  `affinity=` per package part); `run_specs` (PRD-003); `mates.resolve` +
  a `set_mate` smoke on a temp scratch project; `render_view` verifies
  previews. No user project is ever touched by a gate run.
- **Semantic search:** embeddings computed at publish (cloud) or index
  build (local/git script); vectors stored in the index; provider behind a
  small abstraction with mandatory keyword fallback (the chat agent
  already depends on an Anthropic key; embeddings must not become a second
  hard dependency).
- **Materialize-on-use** (copy + provenance) chosen over
  reference-resolution: portability and PRD-005 clone/push win; staleness
  is handled by `list_packages` reporting newer versions (explicit update
  tool later).
- **Frontend:** a library module (dialog) reusing the inspector's param
  tables and preview PNGs; the PRD-025 Library tab adopts it.
- Manifest changes are additive (`packages`, `packages_lock`); the PRD-001
  manifest merge driver treats both key-wise.

## MVP & phasing

- **MVP (roadmap):** package format + cache + lockfile; local and
  git-hosted indexes; keyword search; `add_package`/`use_part`/
  `list_packages`/`remove_package`; publish CLI with the full validation
  gate; the bd_warehouse fastener starter set with connectors and presets.
- **Phase 2:** cloud index on PRD-005 (personal/org scopes, publish route,
  publish-queue UI); semantic search; the agent-built COTS set (bearings,
  extrusions, NEMA); the Library dialog.
- **Phase 3:** `package_from_step` McMaster ingestion; PRD-025 Library
  workspace surfacing; PRD-031 marketplace handshake (public scope,
  discovery, provenance display).

## Acceptance criteria

- AC1. `add_package` (iso4762) + `use_part` ("M5x16" preset) mates a real
  cap screw into the prototyping example via its connector — end-to-end
  test on a copy (the roadmap's done-when).
- AC2. A corrupted package — one variant breaking at a param extreme, and
  separately a broken connector — fails `agentcad package validate` and
  publish with the failing check named in `details.checks` (two tests).
- AC3. Reproducibility: with only the cache populated (index unreachable),
  `use_part` re-materializes byte-identically; a tampered cache entry is
  detected by hash and refused (test).
- AC4. A git-hosted index added by URL serves search and install; the
  install keeps working offline from cache after the index disappears
  (test).
- AC5. Republishing an existing version returns `conflict_error`; a yanked
  version still resolves from an existing lockfile (test).
- AC6. Provenance: `get_part` of a materialized part names
  `package@version`; after `remove_package` the part still builds and its
  provenance warns (test).
- AC7. Browser session: search the library dialog, insert a preset
  fastener, see it in tree and viewport — zero console errors.
- AC8. Full suite green; the no-OCP-outside-kernel guarantee holds for all
  new modules (import-hygiene test).

## Risks & open questions

- **Package scripts are code** executed by consumers' kernels — a
  malicious package is arbitrary code in the worker. PRD-006 confinement
  is the backstop and the publish gate is *not* a security boundary (state
  this everywhere the feature is documented). Cloud-index signing/
  provenance is an open question for phase 2 design.
- **Copy-in materialization** means consumers don't get fixes
  automatically; mitigation: staleness reporting in `list_packages`, an
  explicit `update_package` later. Revisit if update friction dominates.
- **Vendor content licensing** (McMaster et al.): private-scope
  enforcement + provenance labeling in MVP design; legal review before
  any public seeding of vendor-derived geometry.
- **Preset ↔ configuration convergence:** presets must *be* PRD-012
  configurations (one schema), or the product grows two variant systems;
  freeze the shared schema before either MVP ships.
- **Embedding provider coupling:** semantic search stays optional; curated
  keywords in `package.json` keep keyword search good enough to ship
  without it.
- **bd_warehouse/build123d pinning inside packages:** packages declare a
  minimum AgentCAD version; the app's pinned kernel stack remains the
  compatibility harness — a package cannot demand a different build123d.

## Competitive references

SolidWorks Toolbox and Onshape Standard Content set the expectation — and
both are closed content users cannot extend (market_research.md, "The
desktop incumbents", "Cloud-native CAD: Onshape"). McMaster-Carr is the
de-facto infrastructure everyone designs around; PartCAD's npm-for-parts is
embryonic (483 stars, registry "in progress") — the territory is unclaimed
("Open-source CAD: FreeCAD, code-CAD, and the Ondsel lesson"; Gap matrix:
**build-differentiated**). We differ by: packages are reviewable scripts
with typed public interfaces (PARAMS + connectors + specs), publication is
kernel-refereed rather than curator-refereed, agents author and repair the
catalog at a scale hand curation never reached, and the whole substrate is
open — PRD-031's marketplace is the storefront, not the format.
