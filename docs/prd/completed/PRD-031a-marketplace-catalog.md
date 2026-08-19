# PRD-031a — Marketplace catalog: seeded, read-only, add-to-library

- **Status:** completed — merged to main in PR #21. Step 4 of the marketplace chain, all six slices landed (TDD, see the plan). The API acceptance criteria (AC1–AC8) are machine-checked in `tests/test_prd031a_acceptance.py`; AC9 (and AC1's visual half) is **graded as evidence** — the pages were **never rendered by a browser** (`list_connected_browsers` → `[]`, the PRD-005a/007 precedent).
- **Phase:** v6 — moats
- **Created:** 2026-08-18
- **Origin:** carved out of [PRD-031](../pending/PRD-031-marketplace.md) by the founder decision recorded in [roadmap.md](../../roadmap.md), "Sequencing decision — the marketplace chain (16 Aug 2026)": *"Public read-only catalog we seed, with add-to-library. Needs 011 + 005a + 007. The browse payload is already pre-generated: 005a serves `catalog/index.json`'s metadata and shipped previews anonymously, filtered to indexes whose `scope` is `public`; add-to-library is the existing authenticated `add_package`/`use_part` path."*
- **Depends on:** PRD-011 (completed — the content-addressed package format, the nine-package COTS catalog, `search_packages`/`add_package`/`use_part`) · PRD-005a (completed — the anonymous surface, `routes_public.py`, the dual `scope: public` filter, the `EXPECTED_PUBLIC` equality test) · PRD-007 (completed — the customizer containment). **NOT PRD-006:** 031a serves only our own seeded catalog, so it stays inside the "bounded params on a member-authored script" threat 005a/007 already accepted. PRD-006 is 031b's.
- **Related:** PRD-031 (the deferred remainder — open publishing, economy) · PRD-025 (the Library/Market workspace, later) · PRD-029 (skills as a second content type, 031b)
- **Design:** [2026-08-18-marketplace-catalog-design.md](../../superpowers/specs/2026-08-18-marketplace-catalog-design.md) · **Plan:** [2026-08-18-marketplace-catalog.md](../../superpowers/plans/2026-08-18-marketplace-catalog.md)

> **Carve-out note.** This is PRD-031's *first* slice, not a replacement. PRD-031
> stays in `pending/` and keeps the deferred remainder: open publishing, the
> author Publish wizard, the static AST gate, policy/malware/name-squat scan,
> signing, remix/ancestry trees, verified-publisher tiers, moderation/takedown,
> the economy (paid listings, payouts) and skills listings. Everything that
> **executes third-party uploaded code on our servers** or **accepts external
> publishing** needs PRD-006 and is 031b. The FR-by-FR mapping is in
> [PRD-031's "Carved out to PRD-031a" section](../pending/PRD-031-marketplace.md).

## Problem & motivation

The asset and the storefront are separable, and PRD-031 fused them. The asset already exists: PRD-011 shipped a content-addressed, gate-validated, standards-tagged, nine-package COTS catalog (`catalog/index.json`), and PRD-005a already serves its metadata and shipped previews to an anonymous visitor. What is missing is only the **web front**: search with filters, listing pages, the "move two sliders, download STEP" customizer preview (PRD-031 AC1), and add-to-library for signed-in users. Every one of those reuses a shipped substrate — nothing here executes new code from anyone.

The shelf is never empty on day one: it is the nine-package COTS catalog. The success metric is **usefulness, not contributors** (roadmap) — which is exactly why the read-only catalog can ship, and prove the format, before open publishing (031b) is safe.

## Users & jobs

- **Anonymous visitor (browser or self-hosted instance):** find a standard part (a NEMA-17 mount, an ISO 4762 cap screw), see its specs/standards/license, customize it with sliders, download a STEP — without an account and without running catalog code locally.
- **Signed-in member:** add a catalog package into a project; the lockfile pins the exact version; the part rebuilds byte-identically forever.
- **Agent:** `market_install` a seeded package into a project in one call, then `use_part` it.

## Goals

- G1. **The catalog is browsable and searchable anonymously** — search/filter/category over `scope: public` indexes, kernel-free and network-free.
- G2. **A listing customizer that reaches the kernel exactly once, under PRD-007's exact wall** — no second anonymous kernel path with weaker limits.
- G3. **Consumption without local execution** — parameters, previews and generated artifacts flow to the visitor; the script runs only in our server-side kernel.
- G4. **Add-to-library pins the version** — PRD-011's lockfile, verbatim.
- G5. **Provenance/license/validated-badge shown read-only** — no remix, no economy.

## Non-goals

- **Open publishing, the Publish wizard, the AST gate, policy/malware/name-squat scan, signing** — PRD-031/031b (need PRD-006: third-party code on our servers).
- **Sandbox-confined execution of uploaded code** — PRD-006.
- **Remix/ancestry, verified-publisher tiers, moderation/takedown, the economy (paid, payouts), skills listings** — PRD-031/031b.
- **The Library/Market workspace IA** — PRD-025. 031a ships the pages, not the workspace.
- **A separate cloud publish pipeline / storage** — 031a serves the bundled `catalog/` index via completed PRD-005a public read.

## Experience

- **Browse (anonymous):** a Market page — a search box (name/keyword/standard/license/param-range) and a grid of listing cards (name, summary, license, disclosure badge, validated badge, preview thumbnail).
- **Listing (anonymous):** metadata (license, standards, disclosure, `min_agentcad`), a preview carousel, the param and spec tables, a versions selector, the read-only script with syntax highlight, and the customizer viewport — drag a slider, the server rebuilds, metrics update, download STEP/STL/3MF.
- **Add to library (signed-in):** an "Add to library" button on a listing → the package is added to the current project (`add_package`, pinning the public index) and the chosen part materialised (`use_part`); the lockfile pins the version.
- **Agent:** `market_install(project, package, part, part_id)` → the same, in one call, scoped to the seeded catalog.
- **Self-hosted:** a local instance browses and installs from the public catalog read-only, no account; publishing does not exist here (031b).

## Functional requirements

### Public browse & search (kernel-free)
- **FR1.** Anonymous search over `scope: public` indexes: name/keyword/standard/**license**/param-range filters, deterministic explainable ranking with a `why` per hit — `core/packages/search.search(_public_indexes(service), …, refresh=False)`. No kernel call, **no network** (never `refresh()`), private indexes never scored.
- **FR2.** A listing-detail payload from `index.json`: all versions, per-version metadata, the `parts` digest (params with type/min/max/choices, connectors, specs), presets, previews, `gate`, `license`, `disclosure`, `standards`, `signatures`. Served by the existing `GET /api/public/packages/{name}` and `.../versions/{version}` plus new `.../script/{part}` (read-only script text, `content.resolve_within`, `.py`) and `.../params/{part}` (the digest param list).
- **FR3.** Every anonymous route filters on **both** `configured_scope == "public"` (the operator's word) and `scope == "public"` (the document's) — an operator-private index never surfaces, and a private/nonexistent miss is one name-free 404 with a cache header (no oracle).

### The listing customizer (the one anonymous kernel path)
- **FR4.** `GET .../parts/{part}/variant?<params>` rebuilds a bounded variant of a catalog part and `.../download/{fmt}` exports it, through **PRD-007's containment reused verbatim**: `require_customizer_capacity()` (503 on a single-worker pool), the process-global in-flight `BoundedSemaphore` (the `pool_size − 1` worker reservation), a per-IP `TokenBucket` + hourly login gate **shared with `/s/`** via `service.customizer_guard`, a per-version bucket, `normalize_params` param parity, the server-side `paramclamp` clamp before the cache key, and the content-addressed variant cache.
- **FR5.** The pin is the catalog version's `content_id` — already content-addressed and immutable. No `Publication`, no share token. The part's script bytes are pinned into `ShareBuilder`'s content-addressed build project (`build_catalog_variant`); the param spec is the **pre-generated index digest** (so browse and the spec read stay zero-kernel; a variant is exactly one kernel call — the build).
- **FR6.** Downloads are gated by a **fixed** allowed export set `{step, stl, 3mf}`; a format outside it 404s **before** the builder. A part with no declared params has no customizer (a `customizer: false` analogue — 404 before the builder).
- **FR6a (the mesh read — slice 4).** `GET .../parts/{part}/mesh/{key}` serves the rebuilt **mesh** `.acm` bytes the browser viewport needs after a `/variant` returns a `mesh_key`. It is **kernel-free** — it reads a variant *already in the cache* and 404s an absent one, **never building** (the `/s/{token}/mesh/{key}` / `get_mesh_by_key` discipline), the `key` hex-gated (`_is_cache_key`) against traversal. It closes the one functional gap slices 1–3 flagged (the customizer viewport had no anonymous route to fetch its mesh); it lives beside `/variant` in `routes_market.py` and joins `EXPECTED_PUBLIC` in the same change, `NOT_YET_BUILT` staying `== set()`.

### Add-to-library (authenticated, PRD-011 verbatim)
- **FR7.** A signed-in user adds a catalog package into a project via the existing `POST /api/projects/{proj}/packages` (`add_package`, `index` = the public catalog) + `.../use` (`use_part`); the PRD-011 lockfile (`packages_lock[name].{version, content_id}`) pins the version. Session-required; not on the anonymous surface.
- **FR8.** A `market_install` agent tool composes `add_package(index=<public catalog>)` + `use_part`, **scoped to install-from-seeded-catalog only** (a package resolvable only from a private index is refused).

### Provenance & trust (read-only)
- **FR9.** A listing surfaces `license`, `disclosure`, `standards`, the `gate: green` **validated** (correctness, not security) badge and the empty `signatures` reserved slot ("unsigned"), read-only. The read-only script carries its own PRD-011 provenance header. No remix, no economy.

## Agent surface

- **New tools:** `market_install(project, package, part, part_id, version_req?, preset?, params?)` (`core/tools_market.py`). *(`market_search` is served anonymously by `GET /api/public/packages/search`; authenticated agents keep `search_packages` — no new `market_search` tool.)*
- **New routes (anonymous, `scope: public`):** `GET /api/public/packages/search`; `.../versions/{version}/script/{part}`; `.../versions/{version}/params/{part}` (kernel-free, in `routes_public.py`); `.../versions/{version}/parts/{part}/variant`; `.../parts/{part}/download/{fmt}` (**K**, in new `routes_market.py`); `.../parts/{part}/mesh/{key}` (kernel-free mesh read, in `routes_market.py` — slice 4, FR6a). All six join `EXPECTED_PUBLIC`.
- **New routes (authenticated):** none — add-to-library reuses the existing package routes.
- **Changed (additive):** `core/share_build.py` (+`build_catalog_variant`/`export_catalog_variant`, `_variant` core, `ensure_share` installs `customizer_guard`); `server/routes_share_public.py` (throttle/gate → `service.customizer_guard`); `core/packages/search.py` (+`refresh`, +`license` filter).
- **New error types:** none — misses are `routes_public._miss`; bad params are `validation_error`; a full pool is PRD-007's `service_unavailable`/`rate_limited`.
- **Unchanged:** `worker.py`, `tools.py`, `app.py`, `service.py`, the PRD-011 lockfile/manager, the PRD-007 `/s/` behaviour.

## Technical approach

- **Reuse `routes_public.py`'s** `_public_indexes` (dual scope), `_find`, `_miss`, `CACHE_CONTROL` for every data route; add the three kernel-free routes into that file (its zero-kernel invariant stays literally true).
- **Reuse `share_build.py`'s** global semaphore, `require_customizer_capacity`, `_clamp_params`, `normalize_params` parity and content-addressed cache through a thin `build_catalog_variant(script_sha, material, params)` — the catalog version's `content_id` is the pin, the digest is the param spec.
- **Share the per-IP throttle** across `/s/` and the market via `service.customizer_guard` (extracted from `routes_share_public.py`), so a visitor cannot double their allowance across the two anonymous kernel paths.
- **Reuse `add_package`/`use_part`** for add-to-library; `market_install` is a seeded-catalog-scoped composition.
- **OCP-free** browse/search/listing modules, asserted in a fresh interpreter with `OCP` blocked.

## MVP & phasing

- **MVP (this PRD):** anonymous browse/search/listing/customizer over the seeded catalog, add-to-library for signed-in users, `market_install` for agents, read-only provenance/license/validated-badge.
- **Phase 2 (PRD-031b, needs PRD-006):** open publishing, the Publish wizard, the AST/policy/signing gate, remix/ancestry, verified-publisher tiers, moderation/takedown, skills listings.
- **Phase 3 (PRD-031b):** paid listings + payouts, curated engineering shelf, rule-pack listings, install-stats API.

## Acceptance criteria

- **AC1.** A logged-out visitor searches the catalog, opens the NEMA-17 listing, moves the `body_length` slider (server rebuilds), and downloads a STEP — **no catalog code ran on their machine** (test: anonymous `TestClient` sweep).
- **AC2 (the invariant).** Browse, search, listing-detail, `/script`, `/params` and `/preview` make **zero** kernel calls over a full sweep — proven with the `kernel_counter` fixture and a **positive control** that does increment it.
- **AC3 (the surface).** `_reachable(hosted_app) == EXPECTED_PUBLIC` including the five new templates; a private-scoped index's listing/search/variant miss is **byte-identical** to a nonexistent package (no oracle).
- **AC4 (the containment).** The customizer inherits PRD-007's caps: a **second** visitor at the same params is served from the variant cache — **exactly one** kernel build for two requests; an out-of-range flood coalesces at the clamp; a single-worker pool 503s naming `AGENTCAD_KERNEL_POOL_SIZE`; the per-IP bucket is the **same** object as `/s/`'s (identity assert on `service.customizer_guard`).
- **AC5.** A signed-in user adds a catalog package into a project (on a **copy**): both manifest maps gain the entry, the lockfile pins `version`+`content_id`, and a materialised part rebuilds byte-identically (PRD-011 AC3 inherited).
- **AC6.** `market_install` installs only from the seeded public catalog; pinning a private index is refused; it returns the lock entry.
- **AC7.** A listing surfaces `license`, `disclosure`, `standards`, the `gate: green` badge and the empty `signatures` slot read-only; the read-only script carries its provenance header; no remix/economy affordance exists.
- **AC8 (OCP-free).** The market browse/search/listing/script/params modules import no `OCP`/build123d (fresh interpreter, `OCP` blocked).
- **AC9 (browser, graded as evidence).** The browse and listing pages render and the customizer drives a rebuild in a real browser — graded on the 005a/007 precedent. The pages were **never rendered by a browser** in this build (`list_connected_browsers` → `[]`); the API ACs (AC1–AC8) are the machine-checked backstop, and the shipped market view (`frontend/js/market.js`, wired into `index.html`/`main.js`, reusing the PRD-007 `share-viewport.js`) is asserted to exist and call the routes it claims to.

## Verification levels

| AC | Direct test | Real server | Real browser |
|---|---|---|---|
| AC1 anonymous flow | ✓ (TestClient) | ✓ | evidence (AC9) |
| AC2 zero kernel + positive control | ✓ | — | — |
| AC3 surface equality + no oracle | ✓ | — | — |
| AC4 containment inheritance | ✓ | — | — |
| AC5 lockfile pin on a copy | ✓ | — | — |
| AC6 `market_install` scope | ✓ | — | — |
| AC7 provenance surfaces | ✓ | — | evidence |
| AC8 OCP-free | ✓ (fresh interp) | — | — |
| AC9 pages + customizer | — | — | evidence (Chrome-permitting) |

## Residual gaps (recorded, not fixed)

- **Peak memory/disk of one bounded variant build** — the PRD-007 residual, unchanged; PRD-006 owes memory/pid/disk-budget/egress caps. On the seeded catalog the working set is nine immutable packages, so it is strictly smaller than share links'.
- **The export mask is fixed, not per-listing** — `{step, stl, 3mf}` for every listing. A per-package `exports` field is a PRD-011 format addition, deferred (founder decision recommended: fixed set for 031a).
- **No signing** — the `signatures` slot is present and empty; signing is 031b/006.
- **`default`/`step` not in the digest** — the customizer opens sliders at `min` (or the first variant's metrics); parity for validation/clamp holds from the digest's type/min/max/choices.

## Risks & open questions

- **The defining risk is FR4/FR5's:** a second anonymous `exec()` path with weaker limits than PRD-007's. Closed by construction — the global semaphore and `require_customizer_capacity` are process-global and reused; param parity/clamp/cache are reused via `build_catalog_variant`; the per-IP bucket + login gate are shared via `service.customizer_guard` (AC4 asserts the identity).
- **Founder decisions:** (1) fixed export set `{step,stl,3mf}` vs per-package `exports` (recommend fixed); (2) extend `/api/public/packages` vs a `/api/public/market` namespace (recommend extend); (3) reuse PRD-007's login-above-N knob for the catalog (recommend the shared knob).

## Competitive references

MakerWorld/Printables (parametric slider consumption, hobbyist meshes), McMaster-Carr/TraceParts (closed vendor catalogs), GrabCAD (7M engineers, no validation/parametrics). The open lane: community-contributed, kernel-validated, parametric code components with STEP/drawing outputs and standards metadata (`docs/market_research.md`). 031a proves the format read-only; 031b opens contribution.
