# Marketplace catalog (PRD-031a) — design

- **PRD:** [PRD-031a](../../prd/in-progress/PRD-031a-marketplace-catalog.md) — seeded read-only marketplace catalog
- **Date:** 2026-08-18
- **Roadmap position:** **step 4 of the marketplace chain** ([roadmap.md](../../roadmap.md), "Sequencing decision — the marketplace chain"): "Public read-only catalog we seed, with add-to-library. Needs 011 + 005a + 007. The browse payload is already pre-generated."
- **Builds on (completed):** PRD-011 (the content-addressed package format, the nine-package COTS catalog served from `catalog/index.json`, `search_packages`/`add_package`/`use_part`) · PRD-005a (the anonymous surface, `server/routes_public.py`, the dual `scope: public` filter, the `EXPECTED_PUBLIC` equality test) · PRD-007 (the customizer containment — the global in-flight semaphore, the per-link/per-IP `TokenBucket`s, the server-side range clamp, the content-addressed variant cache, the pool-reservation 503)
- **Explicitly does NOT depend on:** PRD-006 (sandboxing). **That is the whole line between this slice and 031b.** 031a serves *our own seeded catalog* — the same "bounded params on a member-authored script" threat PRD-005a Decision 2 and PRD-007 already accepted. Third-party code executing on our servers (open publishing) is 031b and needs PRD-006.
- **Plan:** [2026-08-18-marketplace-catalog.md](../../superpowers/plans/2026-08-18-marketplace-catalog.md)

---

## Problem

PRD-031 fuses two separable things: **the asset** (a validated, parametric, standards-tagged component catalog) and **the storefront** (open publishing, an economy, moderation). The roadmap split them (`031a`/`031b`) because the asset already exists — PRD-011 shipped a nine-package COTS catalog (`catalog/index.json`), content-addressed and gate-validated, and PRD-005a already serves its metadata and shipped previews anonymously. What is missing is *only the web front*: search with filters, listing pages, the "move two sliders, download STEP" preview (PRD-031 AC1), and add-to-library. Every one of those has a shipped substrate to reuse.

So this design settles almost nothing new; it settles **reuse boundaries**. Four questions decide the rest:

1. Does the listing customizer get its own anonymous kernel path, or does it flow through PRD-007's exact containment? (Decision 1, 2 — the sharpest risk lives here.)
2. Where does a listing's param spec come from without a kernel call on the browse path? (Decision 3.)
3. Does the market get a new API namespace, or extend PRD-005a's public catalog read? (Decision 4.)
4. What of PRD-031's FRs is 031a, and what does 031b/006 still owe? (Decision 9, and the carve in [PRD-031a](../../prd/in-progress/PRD-031a-marketplace-catalog.md).)

---

## Architecture at a glance

```
 ANONYMOUS (no credential, hosted mode)          AUTHENTICATED (session)
 ─────────────────────────────────────           ───────────────────────
 GET /api/public/packages            [005a]       POST /api/projects/{p}/packages     add_package
 GET /api/public/packages/{n}        [005a]       POST /api/projects/{p}/packages/{n}/use  use_part
 GET .../versions/{v}                [005a]         │  (PRD-011 verbatim — the marketplace
 GET .../versions/{v}/preview        [005a]         │   is a registry index + a web front)
 GET /api/public/packages/search     [031a] ─┐      │
 GET .../versions/{v}/script/{part}  [031a]  │ zero  market_install tool  [031a]
 GET .../versions/{v}/params/{part}  [031a]  │ kernel   = add_package(index=public) + use_part
                                             ─┘
 GET .../parts/{part}/variant        [031a]  ── K ── the ONE anonymous kernel path
 GET .../parts/{part}/download/{fmt} [031a]  ── K ──   ▼ share_build containment (PRD-007), reused
                                                    ┌──────────────────────────────────────────┐
                                                    │  require_customizer_capacity() → 503       │
                                                    │  service.customizer_guard: per-IP bucket + │
                                                    │    hourly login gate  (SHARED with /s/)    │
                                                    │  ShareBuilder.build_catalog_variant():     │
                                                    │    normalize_params · _clamp_params ·      │
                                                    │    content-addressed .cache/ · _inflight_  │
                                                    │    slot (PROCESS-GLOBAL semaphore) · muzzle │
                                                    └──────────────────────────────────────────┘
```

The market is a **web front over the public catalog read**. The catalog package version is *already* content-addressed and immutable (its `content_id`) — so there is no new pin, no `Publication`, no share token. Everything an anonymous visitor reads is one file read of a pre-generated `index.json` document or a shipped asset (PRD-005a's invariant, extended). The single exception — the customizer variant — reaches `exec()` through **PRD-007's containment, verbatim**, never a second path with its own limits.

---

## Decision 1 — the listing customizer is PRD-007's containment scoped to a catalog version, NOT a `Publication` per package

PRD-031 AC1 ("move two sliders, server rebuilds, download STEP") is *exactly* PRD-007's `/s/{token}/variant`. The only difference is what is pinned: a share link pins one owner part at a resolved commit; a listing pins **a catalog package version's part**, which PRD-011 already made content-addressed and immutable. The `content_id` in `index.json` *is* the pin — the same durable identity a share token's `script_sha` copy provides, minted for us by the publish gate.

So 031a materialises **no `Publication`**. Minting a publication per catalog package would give each a `share_token`, an expiry, a revoke path and a `PublicationStore` write — none of which mean anything for a permanent seeded listing, and each of which is a second thing to keep in sync with the index. Instead the market customizer route:

1. resolves the version through the **same** `routes_public._public_indexes` + `_find` (dual `scope: public`, no `refresh()` — kernel-free);
2. confirms `part` is in the entry's `parts` and declares at least one parameter (a part with no params has no customizer — the `customizer: false` → 404 analogue, structural before the builder);
3. reads the part's script bytes from `index.fetch(name, version)/parts/{part}.py` — a **local** file read (the bundled `catalog/` is a `LocalIndex`; a `GitIndex` reads its already-checked-out tree; `refresh()` is never called on the anonymous path — `routes_public.py`'s M2 discipline, and the preview route already does exactly this `fetch`);
4. pins those bytes into `ShareBuilder`'s existing content-addressed build project by `script_sha = sha256(bytes)` — `ShareBuilder._ensure_project` verbatim, idempotent, shared across requests and across two listings with identical bytes;
5. builds the variant through the shared containment (Decision 2).

*Rejected: a `Publication` per catalog package.* It duplicates the immutable identity the `content_id` already is, and adds token/expiry/revoke state that a seeded shelf never uses.

The pin's material and default params come from the package, not an owner: `pinned material` is the part's declared material (or `DEFAULT_MATERIAL`); the visitor supplies the rest as **data**, validated to parity (Decision 3). The `_content_signature`/cache key hashes the pinned bytes + clamped params + density exactly as the authoring path does — determinism is inherited, not re-derived.

---

## Decision 2 — one containment, shared: the market customizer reaches the kernel through the SAME primitives as `/s/`, not a second path

This is the defining risk of the slice, stated first: **do not open a second anonymous kernel path with weaker limits than PRD-007's.** The design closes it by construction — every containment primitive is reused, and the two that are per-app are *shared*, not copied.

**Reused verbatim (already process-global, so shared for free):**

- **The global in-flight `BoundedSemaphore`** (`share_build.inflight_semaphore` / `_inflight_slot`). It is module-global in `share_build.py`, so a market variant build and an `/s/` variant build contend on the *same* object automatically. This is "the real containment wall" (`share_build.py:70-73` comment) — the `pool_size - 1` worker reservation that keeps a signed-in member's worker free.
- **`require_customizer_capacity()`** — the `503` on a single-worker pool, naming `AGENTCAD_KERNEL_POOL_SIZE`. Same call, same message.
- **Param parity** — `service.normalize_params(spec, coerced)` rejects wrong types, non-member enum choices and unknown names *before* any build; visitors supply data, never code (PRD-005a Decision 2).
- **The server-side range clamp** — the shared pure `kernel/paramclamp.py` via `share_build._clamp_params`, applied *before* the variant cache key so clamp-equal requests coalesce (PRD-007 M-2); NaN refused, not a degenerate 200.
- **The content-addressed variant cache** — a repeat param set is a disk read, zero kernel, no in-flight slot (PRD-007 AC2).

**New builder entrypoint (a thin refactor, not a fork):** `ShareBuilder.build_catalog_variant(script_sha, material, params)` and `export_catalog_variant(...)`. `build_variant(pub_id, ...)` today reads only `record["script_sha"]` and `record.get("material")` before delegating to `_validated_params`/`_variant_cache_key`/`_build`/`_inflight_slot`. Extract that tail into a private `_variant(script_sha, material, params)` core; `build_variant(pub_id)` and `build_catalog_variant(script_sha, material)` both call it. This touches one PRD-007 file (`core/share_build.py`) additively and is covered by the same isolation tests.

**The one sharing subtlety — the per-IP bucket.** The per-link and per-IP `TokenBucket`s in `routes_share_public.py` are per-*app* instances. If `routes_market.py` constructed its own per-IP bucket, one visitor would get *two* separate per-IP allowances (one for `/s/`, one for `/market`). Per `share_build.py`'s own comment these buckets "only shape one link's / one address's request rate" — the semaphore is the wall — so this does **not** weaken containment, only rate-shaping fairness. We still fix it: extract the route-level throttle into a shared `CustomizerGuard` (per-IP `TokenBucket` + the `_HourlyCounter` login gate + `_client_host`) installed once on `service.customizer_guard` by `ensure_share`, and have both `routes_share_public` and `routes_market` call `guard.throttle(who, addr)` / `guard.gate(request, addr)`. The per-*link* bucket stays route-local (keyed differently: `share:<pub_id>` vs `catalog:<name>@<version>/<part>`). This is a light, test-covered refactor of a shipped PRD-007 pack — flagged, additive, no behaviour change for `/s/`.

> **⚠️ Flagged edits to PRD-007 files.** `core/share_build.py` gains `build_catalog_variant`/`export_catalog_variant` + the `_variant` core; `server/routes_share_public.py` moves its throttle/gate into `service.customizer_guard`. Both are additive and asserted by the existing `test_share_customizer`/`test_share_isolation` suites plus new market tests. No core (`worker.py`/`tools.py`/`app.py`/`service.py`) is edited.

**What a malicious anonymous visitor can do** — identically to PRD-007, and on *safer* code: bounded CPU per build (kernel per-request timeout + kill-and-respawn), rate-shaped by two buckets + the login-above-N knob, and capped in concurrency by the global reservation so members always keep a worker. The residual is the *same* one PRD-007 named and PRD-006 owes: **peak memory / disk of one bounded build, until PRD-006.** There is no *new* residual — and the catalog scripts are our own seeded content, a strictly narrower threat than a member-authored share link.

---

## Decision 3 — the customizer's param spec comes from the pre-generated index digest, so browse stays zero-kernel and a variant is exactly ONE kernel call

The customizer needs a typed param spec to (a) render sliders and (b) drive `normalize_params` + `_clamp_params`. PRD-007 warms this at *publish* time (`service._params_spec` — a kernel `inspect` call, `service.py:1027`) into a cached sidecar, because a share link has an authenticated publish step. A catalog listing has none.

**The digest already carries it.** Each catalog entry's `parts.<id>.params` is the gate-published projection of `_params_spec`: for a number, `{name, type: "number", min, max, unit}`; for an enum, `{name, type: "enum", choices}` (verified against `extrusion_2020`, `nema17`, `iso4762`). That is exactly what type-rejection, enum-membership and range-clamp read. So:

- `GET .../versions/{v}/params/{part}` returns the digest's param list — a **zero-kernel** JSON read (the `/s/{token}/params` analogue, but from `index.json` instead of a sidecar).
- The variant route feeds that same digest spec to `normalize_params`/`_clamp_params`. Param parity holds from pre-generated data.

The digest omits `default` (a slider's initial position) and `step`. `default` is not needed for correctness — an unset param is filled by the script's own `PARAMS` default at build; the UI opens sliders at the declared `min` (or the default the first variant's metrics report). So **the only kernel call on the entire market anonymous surface is the variant build itself** — browse, search, listing, script, params, preview all reach zero kernel, proven by the positive-control counter (Decision 8).

*Rejected: warming `_params_spec` lazily on first listing view.* It puts a kernel `inspect` on the browse path, breaking PRD-005a's zero-kernel invariant for a value the digest already holds.

---

## Decision 4 — extend PRD-005a's public catalog read; the market is not a new namespace

The kernel-free data routes reuse `routes_public._public_indexes` / `_find` / `_miss` / `CACHE_CONTROL` **verbatim** — the same dual-scope filter (`configured_scope == "public"` AND `scope == "public"`, the M2 lesson), the same one name-free 404, the same `public, max-age=300` so a CDN absorbs a miss flood. They therefore live under the existing `/api/public/packages` family, and the kernel-free ones are added **into `routes_public.py`** (whose docstring/tests already assert zero kernel — keeping them there keeps that invariant honestly true, in one place):

- `GET /api/public/packages/search` — public, refresh-free search (Decision 5).
- `GET /api/public/packages/{name}/versions/{version}/script/{part}` — the read-only script text (PRD-031's "the code, read-only with syntax highlight"), a kernel-free file read via `index.fetch` + `content.resolve_within` (the preview route's containment, `.py` suffix).
- `GET /api/public/packages/{name}/versions/{version}/params/{part}` — the digest param spec (Decision 3).

The **two kernel routes** (`.../parts/{part}/variant`, `.../parts/{part}/download/{fmt}`) go in a **new pack `server/routes_market.py`** — precisely because `routes_public.py` promises zero kernel, the one `exec()`-reaching surface must be an isolated, separately-reviewable module. It imports `_public_indexes`/`_find` from `routes_public` and the shared `service.customizer_guard`, and mounts under `/api` (its paths are under `/api/public/packages/...`, already inside `PUBLIC_PREFIXES`).

The listing-detail payload (everything a listing page shows: all versions, per-version metadata, the full `parts` digest, presets, previews, `gate`, `license`/`disclosure`/`standards`, `signatures`) needs **no new route** — the existing `GET /api/public/packages/{name}` (latest summary + versions) and `GET /api/public/packages/{name}/versions/{version}` (the full `_document`) already serve it. 031a adds only script + params + the two kernel routes.

*Founder question, recommended answer inside:* **extend `/api/public/packages`, do not mint `/api/public/market`.** "Market" is the UI's product name; the API is the public catalog. The one cost is route ordering — `GET /api/public/packages/search` must be declared *before* `GET /api/public/packages/{name}` or Starlette binds `{name} == "search"` (noted in the plan). A `/api/public/market` namespace is viable if a clean product URL is preferred, at the cost of a sibling helper import; it changes nothing else.

### The route-by-route anonymous-exposure table (extending PRD-005a Decision 8 + PRD-007 Decision 2)

**K** marks a route that can reach `exec()` in the kernel worker. `—` is zero kernel. Every row joins `EXPECTED_PUBLIC` in `tests/test_hosted_surface.py` in the change that mounts it (never staged through `NOT_YET_BUILT`, which `test_prd005a_acceptance` asserts `== set()`).

| Method | Path (template) | Kernel | Why it is safe |
|---|---|---|---|
| GET | `/api/public/packages` | — | *(005a)* index JSON on disk, dual `scope: public` only |
| GET | `/api/public/packages/{name}` | — | *(005a)* ditto — listing summary + versions |
| GET | `/api/public/packages/{name}/versions/{version}` | — | *(005a)* the full entry `_document` — the listing-detail payload |
| GET | `/api/public/packages/{name}/versions/{version}/preview` | — | *(005a)* shipped PNG, `content.resolve_within`, `.png` |
| GET | `/api/public/packages/search` | — | **new.** `search.search(_public_indexes(service), …, refresh=False)`; kernel-free, network-free, public-scoped |
| GET | `.../versions/{version}/script/{part}` | — | **new.** the read-only `parts/{part}.py` text; `index.fetch` (local) + `content.resolve_within`, `.py` suffix |
| GET | `.../versions/{version}/params/{part}` | — | **new.** the digest param list — a JSON read of `index.json`, no `inspect` |
| GET | `.../versions/{version}/parts/{part}/variant?<p>` | **K** | **new.** the listing customizer; PRD-007 containment reused *verbatim* (Decision 2): `require_customizer_capacity` 503, shared per-IP bucket + per-version bucket, `normalize_params` parity, `_clamp_params` before the cache key, global in-flight semaphore, content-addressed cache |
| GET | `.../versions/{version}/parts/{part}/download/{fmt}?<p>` | **K** | **new.** a variant export; same caps; a format outside the allowed set 404s before the builder (Decision 6) |

Five new templates. Three make zero kernel calls; two are the customizer path, capped exactly as `/s/`'s two are. The complete anonymous market surface reaches the kernel at exactly one place, under one shared containment.

---

## Decision 5 — public search is `search.search` with `refresh=False`, over the public indexes only

`search_packages` (PRD-011) already does deterministic, explainable, honestly-degraded search — exact-name/prefix/standard/keyword/summary/param-range scoring with a `why` on every hit, kernel-free. Two things make it unsafe to reuse *directly* on the anonymous path:

1. **It calls `candidate.refresh()`** (`search.py:78`) — a git fetch. PRD-005a forbids network on the anonymous path (M2). So `search.search` gains a `refresh: bool = True` parameter (default preserves the authenticated behaviour); the public route passes `refresh=False`.
2. **It is handed a caller-supplied index list.** The public route passes `_public_indexes(service)` — the dual-scope-filtered list — so a private index is never scored, exactly as `routes_packages.search` walking *all* indexes is why the public read is a separate pack.

The public search adds a **`license`** AND filter (PRD-031's license facet) alongside the existing `keywords`/`standards`/`param` filters; `param` (range overlap) is already there. Category browse is the same call with a `keyword`/`standard` filter and an empty query (which lists in name order — what the browse grid opens on). One scoring implementation, one ranking, one `why`.

---

## Decision 6 — add-to-library is PRD-011 verbatim, session-gated; `market_install` is the agent convenience

A marketplace is a registry index + a web front. Installing from it is `add_package` (resolve + content-verify + record both manifest maps) then `use_part` (materialise a part, PRD-011 lockfile pins the version) — **the existing authenticated path, unchanged**:

- **Browser:** the Market "Add to library" button calls the existing `POST /api/projects/{proj}/packages` (with `index` = the public catalog index name, so the pin is explicit) then `POST /api/projects/{proj}/packages/{name}/use`. No new route. These are private (a session is required in hosted mode — they are *not* in `PUBLIC_PATHS`).
- **Agent:** a new `market_install` tool (PRD-031's agent surface), a thin composition of `add_package(index=<public catalog>)` + `use_part`, **scoped to install-from-seeded-catalog only** — it pins `index` to a public-scoped catalog index, so it can never pull from a private index. Lives in a new pack `core/tools_market.py`.

> **Load-order note.** `tools_market` (`mar`) sorts *before* `tools_packages` (`pac`), so `service.packages` does not yet exist at `tools_market.register` time. Read `service.packages` **inside** the tool function, never in `register` — the same discipline `tools_run_checks`/`tools_packages` already follow. (Or the tool simply invokes the `add_package`/`use_part` registry tools.)

The lockfile pin (`packages_lock[name].content_id`) and byte-identical re-materialisation are PRD-011's guarantees; 031a inherits AC3 (a later listing update does not change a consumer's build until an explicit upgrade) for free.

---

## Decision 7 — provenance, licensing and the validated badge are read-only surfaces over the index entry

Everything a listing shows about trust is already in the entry or the shipped tree; 031a renders it and adds nothing:

- **License** (`license`, e.g. `Apache-2.0`), **disclosure** (`disclosure`: `agent`/`human`), **standards** (`standards`, e.g. `DIN 625-1`), **keywords** — straight from the entry.
- **The validated badge** — `gate.status == "green"` plus the gate's `agentcad`/`build123d`/`report_id`. This is a **correctness** badge (PRD-011: the gate is not a security boundary), and the listing text says so, exactly as the Library dialog does.
- **Signatures** — the entry's `signatures: []` reserved slot renders as "unsigned". Signing is 031b/006's; the slot is present and honest.
- **Per-cell provenance** — the provenance header PRD-011 writes *inside each script* travels with the read-only script text (`.../script/{part}`); no separate call.

No remix, no ancestry, no economy — those are 031b (Decision 9).

---

## Decision 8 — security posture: browse/search/listing reach zero kernel, proven by a positive-control counter; the customizer is the one bounded exception

The PRD-005a invariant, extended and re-proven for the new routes:

- **Zero kernel on browse/search/listing/script/params/preview.** Proven the 005a way: instrument `service.kernel.request` with a counter, sweep every new kernel-free route, assert **zero** — with a **positive control** (one request that *does* build increments the counter, so the zero is meaningful, not a dead meter). This is `test_hosted_surface.test_public_surface_makes_no_kernel_calls` extended to the market routes.
- **The surface-equality test.** The five new templates are added to `EXPECTED_PUBLIC`; `test_the_public_surface_is_exactly_this` asserts equality, and `test_paths_that_must_not_be_public` gets negation params for near-miss prefixes (there are none new — all under the existing `/api/public/` prefix, which already ends in `/`).
- **The customizer inherits 007's caps** — asserted by reusing the same primitives (Decision 2): a second visitor at the same params is one cached read (exactly one kernel build for two requests); an out-of-range flood coalesces at the clamp; a single-worker pool 503s; the shared per-IP bucket means `/s/` and `/market` do not double a visitor's allowance.
- **`scope: public` everywhere** — the dual filter on every data route and both kernel routes; an operator-private index never surfaces (no search hit, no listing, no variant), and its miss is byte-identical to a nonexistent package.
- **add-to-library requires a session** — the existing private routes; `market_install` is an authenticated tool.

**What a malicious anonymous visitor can do:** the *same* bounded surface PRD-007 already ships — a rate-shaped, concurrency-capped, param-validated rebuild of *our seeded* scripts, whose only residual (peak memory/disk of one bounded build) PRD-006 owes. Arbitrary script upload by an unauthenticated party is refused until PRD-006, without exception — that is 031b.

---

## Surfaces

**Tools** (`core/tools_market.py`): `market_install(project, package, part, part_id, version_req?, preset?, params?)` — `add_package(index=<public catalog>)` + `use_part`, seeded-catalog-scoped. *(`market_search` in PRD-031's agent surface is served for anonymous callers by `GET /api/public/packages/search`; authenticated agents already have `search_packages`.)*

**Routes** — three kernel-free, added to `server/routes_public.py`; two kernel, in new `server/routes_market.py` (see the table, Decision 4). All under `/api/public/packages/...`.

**Errors** — no new types. Every market miss is `routes_public._miss` (one name-free 404 + cache header); a bad param is the authoring path's `validation_error`; a full pool is `service_unavailable`/`rate_limited` (PRD-007's).

**Frontend** (`frontend/`): a Market browse page (search box + listing grid from `/api/public/packages` and `/search`) and listing pages (metadata, preview carousel, param/spec tables reusing the inspector's renderer, provenance/license block, versions selector, read-only script with the existing syntax highlighter, and the PRD-007 customizer viewport — `viewport.js::parseACM` + the `share.html` slider UI — pointed at the market variant route). An authenticated "Add to library" affordance calls the existing package routes. The PRD-025 Library/Market *workspace* is later; 031a ships the pages.

**CLI / Deployment** — none. The public catalog index is configured exactly as 005a documents (`scope: public`, operator-set `configured_scope: public`).

**Changed (flagged):** `core/share_build.py` (+`build_catalog_variant`/`export_catalog_variant`, `_variant` core), `server/routes_share_public.py` (throttle/gate → `service.customizer_guard`), `core/packages/search.py` (+`refresh`, +`license` filter), `core/share_build.ensure_share` (+`customizer_guard`). All additive; PRD-007/011 suites stay green.

---

## Acceptance criteria (crisp, testable, in the PRD-005a mould)

- **AC1.** A logged-out visitor searches the catalog, opens the NEMA-17 listing, moves the `body_length` slider (server rebuilds), and downloads a STEP — **no catalog code ran on their machine** (test: anonymous `TestClient` sweep of search → listing → `/params` → `/variant` → `/download`).
- **AC2 (the invariant).** Browse, search, listing-detail, `/script`, `/params` and `/preview` make **zero** kernel calls over a full sweep, proven with the `kernel_counter` fixture and a **positive control** that does increment it.
- **AC3 (the surface).** `_reachable(hosted_app) == EXPECTED_PUBLIC` including the five new market templates; a private-scoped index yields a listing/search/variant miss **byte-identical** to a nonexistent package (no oracle).
- **AC4 (the containment).** The listing customizer inherits PRD-007's caps: a **second** visitor at the same params is served from the variant cache — **exactly one** kernel build for the two requests; an out-of-range flood coalesces at the clamp; a single-worker pool 503s naming `AGENTCAD_KERNEL_POOL_SIZE`; the per-IP bucket is **shared** with `/s/` (one visitor does not get double allowance) — asserted against `service.customizer_guard` identity.
- **AC5.** A signed-in user adds a catalog package into a project (on a **copy**): `packages`/`packages_lock` gain the entry, the lockfile pins the exact `version`+`content_id`, and a materialised part rebuilds byte-identically (PRD-011 AC3 inherited).
- **AC6.** `market_install` installs only from the seeded public catalog — pinning a private index is refused; the tool composes `add_package`+`use_part` and returns the lock entry.
- **AC7.** A listing surfaces `license`, `disclosure`, `standards`, the `gate: green` validated badge and the empty `signatures` slot read-only; the read-only script carries its own provenance header. No remix/economy affordance exists.
- **AC8 (OCP-free).** The market browse/search/listing/script/params modules import no `OCP`/build123d, asserted in a fresh interpreter with `OCP` blocked (the `test_packages_ocp_free` pattern).
- **AC9 (browser, graded as evidence).** The browse and listing pages render and the customizer drives a rebuild in a real browser. Graded on the 005a/007 precedent (Chrome has been unavailable many sessions): the API ACs above are the machine-checked backstop; the browser half is evidence when a browser is available.

---

## Risks & open questions

- **The defining risk is Decision 2's:** a second anonymous `exec()` path with weaker limits than PRD-007's. The design closes it structurally — the global in-flight semaphore and `require_customizer_capacity` are process-global and reused as-is; param parity, the clamp and the variant cache are reused via `build_catalog_variant`; the per-IP bucket + login gate are *shared* through `service.customizer_guard`. AC4 asserts the shared identity. Accepted, capped, residual named — the same residual PRD-006 already owes, on safer (seeded) code.
- **Founder decisions needed:**
  1. **Download export mask.** A share link masks downloads by the owner's `settings.exports`; a catalog listing has no owner. Recommendation: a **fixed** allowed set `{step, stl, 3mf}` for every listing (the `EXPORT_FORMATS` intersection PRD-007 ships), gated the same way (a format outside the set 404s before the builder). Alternative: a per-package `exports` field in `index.json` (a PRD-011 format addition — heavier). **Recommend the fixed set for 031a.**
  2. **API namespace.** `/api/public/packages/...` (recommended — reuse `_public_indexes`/`_find` in-file) vs a `/api/public/market/...` product namespace (a sibling pack importing the helpers). **Recommend extend.**
  3. **Login-above-N default.** PRD-007's `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE` is off by default. Should the seeded catalog reuse the same knob/counter (recommended — the shared `customizer_guard` gives this for free) or get its own threshold? **Recommend the shared knob.**
- **Bounded, seeded working set.** Unlike share links (unbounded, user-minted), the catalog is nine immutable packages — the content-addressed build projects warm once and stay warm, so the variant cache is a fixed, small set. This is strictly easier than PRD-007's GC story; the PRD-006 disk-budget residual is even smaller here.

## Naming traps (live collisions in this tree today)

- **`scope`** has three senses: the package-index `scope`/`configured_scope` (PRD-011/005a access control — what 031a filters on), PRD-007's `share_scope` (`part`/`project` reach of a publication), and `locks.write_scope` (turn locking). 031a uses only the first.
- **`market_search`** (PRD-031's named agent tool) is, in 031a, the anonymous `GET /api/public/packages/search`; authenticated agents keep `search_packages`. There is no new `market_search` *tool* — do not add one.
- **`variant`** — the customizer variant (`build_variant`/`build_catalog_variant`) is unrelated to a build123d "variant" or a PRD-012 configuration; and `default_variant_key` is a publication field 031a does not use.
- **`_public_indexes`** requires **both** `configured_scope == "public"` and `scope == "public"` — never collapse to one (the M2 access-control lesson).
- **`preview`** — the shipped `previews/*.png` (read-only listing images) is not the customizer *variant* (a rebuilt mesh). Two different anonymous reads, one kernel-free, one **K**.

## PRD divergences to fold back

| PRD-031 says | This design (031a) does | Why |
|---|---|---|
| FR1: served "from the cloud service (PRD-005) with public read" | served from the bundled `catalog/` `LocalIndex` via completed PRD-005a public read | the asset needs none of a cloud publish pipeline; 011's git-hosted index already is the registry |
| FR2/FR3: publish gate (AST/policy/signing), sandbox-only execution | **out of scope** — no publishing in 031a | open publishing runs third-party code on our servers → PRD-006 → 031b |
| FR4/FR6/FR7/FR9: remix, tiers, moderation, economy | **out of scope** | all presuppose open publishing → 031b |
| FR5: add-to-library, lockfile pin | the existing authenticated `add_package`+`use_part`; `market_install` convenience | PRD-011 verbatim; a marketplace is a registry index + a web front |
| `market_search` / `market_install` agent tools | `market_search` → anonymous `/api/public/packages/search`; `market_install` → seeded-catalog-scoped tool | no new anonymous kernel-reaching tool surface |
| AC1 customizer download | reuse PRD-007 containment scoped to a `content_id`; fixed export set | one shared containment, no second kernel path |
