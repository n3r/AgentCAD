# PRD-011 — Parts library and package registry

- **Status:** completed — merged to main in PR #15 (AC1–AC9 verified; AC9 adopted at design review)
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026) + founder idea #1d ("Library" tab, Aug 2026)
- **Depends on:** PRD-003 (hard — the publish gate runs specs) · PRD-005 (soft — hosted cloud index)
- **Related:** PRD-010 (holes pair with fasteners), PRD-012 (package presets align with configurations), PRD-013 (sub-assembly packages later), PRD-025 (the Library mode surfaces this), PRD-031 (the public marketplace layer above)

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

## As built

Fourteen slices, `docs/changelog/0166`–`0180`, against the design spec
`docs/superpowers/specs/2026-08-16-parts-library-registry-design.md` and the
plan beside it. User-facing reference: `docs/packages.md`; the traps an
implementer must not hit: `AGENTS.md`, "Package gotchas".

**The publish gate is a CORRECTNESS gate, not a security boundary**, and that
sentence ships in the eight places the design spec fixes: `docs/packages.md`'s
first screen, every tool description that installs or runs package code, the
report's own `note` field, the index format documentation, the materialised
provenance header in the consumer's repository, and the Library dialog's
install affordance as **visible text**. What the feature *does* enforce is
content integrity — the index declares a content id, the cache verifies every
fetch against it and re-verifies the whole tree on every materialisation — and
the reserved, empty `signatures` slot is the answer to an index that lies about
both (PRD-031 FR2(d)). PRD-006 remains the confinement backstop.

`agentcad/core/packages/` is an eleven-module subpackage that imports no
geometry (every module asserted OCP-free in a fresh interpreter), reached
through the ordinary extension points: `core/tools_packages.py` (seven tools,
**no gate provider**), `server/routes_packages.py`, `frontend/js/library.js`,
two CLI subcommands, and one worker handler pack
(`kernel/handlers/reffaces.py`). The two edits to pre-existing non-test
modules the plan budgeted were `core/manifest_merge.py` (two key-wise heads)
and `agentcad/cli.py`; a third was forced and is recorded below.

### Verification — one named test per criterion

`tests/test_prd011_acceptance.py` is the contract layer; the depth is in
`tests/test_packages_*.py` and `tests/test_catalog.py` beside it.

| AC | What it asks | Proving test | Verdict |
|---|---|---|---|
| **AC1** | a catalog cap screw mates into the prototyping example | `test_ac1_a_catalog_cap_screw_mates_into_the_prototyping_example` · `tests/test_catalog.py::test_ac1_a_catalog_cap_screw_mates_onto_a_tapped_hole` | **met** — on a **copy** of the example, `add_package` → `use_part {preset: "m5x16"}` → `set_mate` resolves to `[0, 0, 10]`, and interference is asserted in **both** directions (cosmetic clears the ⌀4.2 tap drill; `real` overlaps it, which is engagement) |
| **AC2** | a corrupted package fails validate **and** publish, named in `details.checks` | `test_ac2a_a_variant_that_breaks_at_an_extreme_fails_validate_and_publish` · `test_ac2b_a_broken_connector_fails_validate_and_publish` | **met** — two committed fixtures wrong in exactly one way each: `build:strut@length=max` (green at default and at `length=min`) and `connectors:bracket` (green at every extreme). Both publish attempts leave the index tree hash unchanged |
| **AC3** | cache-only re-materialisation is byte-identical; a tampered cache is refused | `test_ac3_re_materialisation_is_byte_identical_and_a_tampered_cache_is_refused` | **met** — with every index dropped, two materialisations produce identical script bytes; one appended byte in the cached script makes `verify` report `tampered` and `use_part` refuse, naming the file, leaving no part behind |
| **AC4** | a git index by URL serves search and install, and survives its own death | `test_ac4_a_git_index_serves_search_and_install_and_survives_its_own_death` · `tests/test_packages_git_index.py` | **met, and sharpened** — hermetic `file://` bare repo. Deleting the *remote* is not enough (the last good checkout keeps answering, by design), so the test deletes the checkout too and then installs from the **cache**, with a lock entry byte-identical to the online one |
| **AC5** | republish is a conflict; a yanked version still resolves from a lockfile | `test_ac5_a_version_is_immutable_and_a_yank_never_breaks_a_lockfile` · `tests/test_packages_publish.py` | **met, and it found a hole** — republishing is refused *even at an identical content id*. A yank breaks nothing that names it, and a fresh **range** now refuses from the warm cache too (the fix that test forced; changelog 0180) |
| **AC6** | provenance names `package@version` and degrades to a warning | `test_ac6_provenance_names_the_package_and_degrades_to_a_warning` | **met** — `ok` → `remove_package` → the part **still builds** and reads `removed` (no script byte rewritten) → a local edit reads `modified`. The status is computed on every read; nothing stores it |
| **AC7** | a browser session: search, insert a preset fastener, zero console errors | `test_ac7_the_library_dialog_and_its_routes_exist_and_carry_the_non_claim` (evidence) · the sessions themselves in changelogs 0177 and 0178 | **met** — driven twice in real Chrome: once on a one-package catalog, again on all nine (nine hits, nine decoded 640×480 thumbnails, two packages materialised into one project). **0 console errors, 0 page errors, 0 responses ≥ 400** |
| **AC8** | full suite green; the no-OCP guarantee covers every new module | `test_ac8_the_ocp_free_guarantee_covers_every_new_module` · `test_ac8_the_full_suite_count_is_cited` | **met** — a fresh-interpreter probe per module with `OCP`/`build123d` blocked at `sys.meta_path`, plus a list-matches-the-tree test so a module cannot be added without one. The one file allowed to import OCP is the worker handler pack |
| **AC9** | *(adopted at design review)* an agent takes a red package green from the report alone | `test_ac9_an_agent_takes_a_red_package_green_from_the_report_alone` | **met, with its limit stated** — three faults (a spec that fails only at an extreme, a preset naming a part that is not shipped, a README that stopped naming its part), one mechanical fix per row, green in **one pass**. The fixer may read the report and the files the report names and knows nothing else about the package. It proves the rows are *addressable*, not that an agent will choose the right repair — the spec row admits two consistent ones |

### What could not be delivered as written

Eight items the design spec folded back before code was written, plus three the
slices found. None is a refusal; each is a restatement with the measurement
that forced it.

1. **FR1: presets did not have PRD-012's schema, so PRD-012's was changed.**
   The frozen entry is the wrapped `{params, label?, description?}` and
   **PRD-012 FR1 is amended to match** (that edit is in this feature's commit,
   not "later" — the schema froze the moment the catalog published). The flat
   `{name: {param: value}}` map is ambiguous the day a part declares a
   parameter called `label`, and four PRDs already need the metadata.
2. **FR2: there is no archive.** A package is a directory and its id is a
   canonical **content listing** digest, because tar is not byte-stable across
   producers — the DXF lesson. "The sha256 of its canonical archive" became
   "the sha256 of its canonical content listing".
3. **FR9: "every part builds at every parameter's min and max" is not
   achievable as written.** `min`/`max` are *optional* in the PARAMS contract,
   so the gate additionally **requires** them of a published package and fails
   the `contract` stage otherwise. And the sweep is **one parameter at a time
   plus every declared configuration — a sum, never the cross product**, which
   would redden correct content whose parameters are mutually constrained.
4. **FR9: the gate does not check drawings.** A package contains parts, not
   sheets, and `generate_drawing` is a project-scoped tool over PMI a package
   does not ship. PRD-031 FR2(a)'s parenthetical should drop "drawings".
5. **FR7/FR8: the cloud index and semantic search ship as interfaces, not
   implementations.** `semantic` is present in every search result and always
   `false` with `no_embedding_provider`; the cloud client is a
   registered-by-nothing protocol until PRD-005-lite.
6. **Only the `publish` CLI ships, not a publish tool or route.** A
   `publish_package` tool that can write only to a local directory is a tool an
   agent cannot usefully call, and the route needs PRD-005's tenancy.
7. **FR13 ships thinner than written, in two ways.** Agent-assisted connector
   *placement is not automated*: `package_from_step` reports the imported
   solid's planar and cylindrical faces as candidates and the author writes
   `connectors` (PRD-032 is where inference belongs). And **`use_part` refuses
   a reference part** — the provenance header lives inside the script and a
   reference part has none, so a materialised one could carry no provenance at
   all; the package validates, publishes and installs, and `import_cad_file`
   over the cached file is the documented path. A vendor file above the
   published **5 MB per-file ceiling** is refused with the number rather than
   the ceiling being raised (a raised ceiling makes a package uninstallable by
   a pinned client).
8. **The PRD's own MVP ordering was superseded by the roadmap**: the git index
   and the seeded COTS catalog were MVP (step 1, registry-first) and the
   Library dialog landed behind them.
9. **A third pre-existing module was edited** against a plan that budgeted two:
   `agentcad/toolkit/threads.py`. Writing the `iso4014` package found that
   `threads.hex_bolt` imported `HexBolt`, which the pinned bd_warehouse does
   not export — every call had raised `ImportError` since changelog 0023 and
   nothing in the tree called it. Reported rather than worked around; the
   alternative was leaving a documented, advertised helper broken.
10. **The build fan-out does not clear its own keep-bar.** The plan's rule was
    "under 1.5× on a 3-worker pool, delete it". Measured on the whole catalog
    (107 variants, five interleaved repetitions): **1.40×**, with a per-package
    spread of 1.20×–2.46× tracking build *evenness*. Only 44% of the build
    stage is inside kernel calls, so Amdahl's ceiling at three workers is
    1.42×. The executor is still in the tree and the decision is the review
    round's; `jobs=1` is a first-class path and a test pins that the two agree
    row for row, so removing it is a one-function change with a byte-identical
    report. **No document may advertise a speedup the catalog does not get.**
11. **A yank was defeated by a warm cache**, found by writing AC5: the offline
    fallback resolved a version the index had withdrawn. Fixed — the cache is
    for "no index answered", not for "the index answered no" — with the
    explicitly-named case still installing, as it does online.

### Still open

- ~~The repository has no LICENSE~~ **Resolved during this PRD** (founder
  decision, Aug 2026): the repository is Apache-2.0 (`LICENSE` +
  `pyproject.toml` fields, landed with the design commit), matching the nine
  seed packages. The 031a licensing blocker recorded in the roadmap is
  closed.
- **`merge_bundled` is a replacement, not a merge.** A user index named
  `agentcad-core` replaces the bundled catalog outright, including as a publish
  target. That is the escape hatch for shadowing the shipped catalog; it is
  documented as one.
- **The index digest carries no parameter `description`s**, so the Library
  dialog's description column is empty for catalog packages.
- **No `update_package`.** Copy-in means consumers do not get fixes
  automatically; `list_packages` reports `latest` and `stale`.

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
