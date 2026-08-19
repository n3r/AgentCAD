# Marketplace catalog (PRD-031a) — implementation plan

- **Design:** [2026-08-18-marketplace-catalog-design.md](../specs/2026-08-18-marketplace-catalog-design.md)
- **PRD:** [PRD-031a](../../prd/in-progress/PRD-031a-marketplace-catalog.md)
- **Branch:** `prd-031a-marketplace-catalog`

## Why this is lighter than 005a/007 (slice count justification)

005a built the whole anonymous surface, identity, modes and deployment; 007 built the pin, the muzzled build service, the containment and the viewer from scratch. **031a builds almost no new machinery** — it is a web front over three shipped substrates:

- the public catalog read (`routes_public.py`, the dual-scope filter, `_miss`) — reused verbatim;
- the customizer containment (`share_build.py`'s global semaphore, clamp, cache, param parity, 503) — reused verbatim through a thin new `build_catalog_variant` entrypoint;
- `search`/`add_package`/`use_part` — reused verbatim (search gains a `refresh=False`/`license` flag; nothing else).

So the work is **six vertical slices**: kernel-free data API first (the widest reuse, zero risk), then the one kernel path (the whole risk of the slice, reusing 007's wall), then add-to-library, then UI, then docs/acceptance. Each slice is TDD, parallel-safe (bind 0, no fixed paths, `--projects-dir` under scratch), and leaves the tree green.

---

## Slice 1 — public browse/search API, kernel-free (widest, first)

**Goal:** anonymous search + the read-only script + the digest param spec, all zero-kernel, public-scoped, refresh-free.

1. **`core/packages/search.py`:** add `refresh: bool = True` (default preserves behaviour); the public caller passes `refresh=False`. Add a `license` AND filter beside `keywords`/`standards`. Tests: refresh-free search over a fixture index makes no `refresh()` call (spy); `license` filters correctly; existing `search` tests unchanged.
2. **`server/routes_public.py`:** add three routes — `GET /api/public/packages/search` (declared **before** `/{name}` so Starlette does not bind `{name}=="search"`), `.../versions/{version}/script/{part}`, `.../versions/{version}/params/{part}`. Search calls `search.search(_public_indexes(service), …, refresh=False)`; script is `index.fetch` + `content.resolve_within` (`.py`) — the preview route's containment; params returns the entry digest's `parts[part].params`. All carry `CACHE_CONTROL`; every miss is `_miss()`.
3. **Tests (`tests/test_public_catalog.py` or a new `test_market_api.py`):** search returns public-only hits with `why`; a private-scoped index never appears and its miss is byte-identical to a nonexistent one; `/script` refuses `..`/absolute/non-`.py`; `/params` returns the digest list; **zero kernel** over the sweep with a positive control; OCP-free import assertion (fresh interpreter, `OCP` blocked).

**Green when:** the three routes serve public catalog data, refresh-free, kernel-free, with the dual-scope filter and the one name-free miss.

---

## Slice 2 — the listing customizer, reusing PRD-007's containment (the whole risk)

**Goal:** the one anonymous kernel path — variant + download — under 007's exact wall, no second set of limits.

1. **`core/share_build.py`:** extract the tail of `build_variant(pub_id)` into a private `_variant(script_sha, material, params)` (validate → clamp → cache probe → `_inflight_slot` build); `build_variant(pub_id)` now calls it. Add `ensure_catalog_pin(script_bytes, material) -> {script_sha}` (reuses `_ensure_project`; params spec is supplied by the route from the digest, so **no `_params_spec` kernel call here**), `build_catalog_variant(script_sha, material, params)` and `export_catalog_variant(...)`. `_validated_params` accepts an explicit spec argument (the digest) so the catalog path passes it instead of reading a sidecar. Tests: a catalog variant builds against the muzzled service, never touches a user store; two identical param sets → one kernel build (counter); an out-of-range flood coalesces at the clamp; NaN refused; `require_customizer_capacity` 503 on a single-worker pool. Existing `test_share_customizer`/`test_share_isolation` stay green.
2. **`ensure_share` + `CustomizerGuard`:** extract `routes_share_public`'s `_client_host`/`_throttle`/`_gate`/buckets/`_HourlyCounter` into a `CustomizerGuard` installed on `service.customizer_guard` by `ensure_share`; `routes_share_public` calls it. Test: `/s/` behaviour byte-identical; the per-IP bucket is one shared object (identity assert).
3. **`server/routes_market.py`** (new pack, `PREFIX="/api"`): `GET /api/public/packages/{name}/versions/{version}/parts/{part}/variant` and `.../download/{fmt}`. Order = resolve version via `_find` (404), confirm the part declares params (else `_miss`), read script bytes via `fetch`, `ensure_catalog_pin`, `require_customizer_capacity`, `guard.gate`, `guard.throttle("catalog:<name>@<version>/<part>", addr)`, then `build_catalog_variant`; download checks `fmt` in the fixed allowed set `{step,stl,3mf}` before the builder.
4. **`tests/test_hosted_surface.py`:** add the two `K` templates to `EXPECTED_PUBLIC` (in the same change that mounts them — never via `NOT_YET_BUILT`); the equality test passes.
5. **Tests (`test_market_customizer.py`):** AC1 end-to-end (search→variant→download, anonymous); AC4 (cache coalescing, 503, shared bucket); a download of a format outside the set 404s before any build; a private-index variant is `_miss`.

**Green when:** the customizer rebuilds and exports through the shared containment, the surface-equality test includes the two routes, and no PRD-007 test regresses.

---

## Slice 3 — add-to-library (`market_install`, PRD-011 verbatim)

**Goal:** the agent convenience; confirm the browser path reuses existing routes.

1. **`core/tools_market.py`** (new pack): `market_install(project, package, part, part_id, version_req?, preset?, params?)` = `add_package(index=<public catalog>)` + `use_part`. Read `service.packages` **inside** the function (load-order: `mar` < `pac`). Refuse a package resolvable only from a private index (seeded-catalog scope).
2. **Tests (`test_market_install.py`, on a copy):** installs from the public catalog, pins version+`content_id` in the lockfile, materialises a part that rebuilds byte-identically (AC5); pinning a private index is refused (AC6); the existing `POST /api/projects/{proj}/packages` + `.../use` routes are unchanged and session-gated (already covered — assert they are not in `PUBLIC_PATHS`).

**Green when:** `market_install` installs only from the seeded catalog and the lockfile pins the version.

---

## Slice 4 — the Market UI (browse + listing pages)

**Goal:** the pages PRD-031's MVP ships; reuse the inspector param tables and the 007 customizer viewport.

1. **`frontend/`:** a browse page (search box → `/search`, listing grid → `/api/public/packages`, category chips from keywords/standards) and listing pages (metadata + preview carousel + param/spec tables via the inspector renderer + provenance/license block + versions selector + read-only script via the existing highlighter + the customizer viewport reusing `viewport.js::parseACM` and the `share.html` slider UI, pointed at the market variant route). An authenticated "Add to library" button calling the existing package routes.
2. **Verification:** graded on the 005a/007 precedent — the API slices are the machine-checked backstop; a real-browser pass (search → open listing → drag slider → download → add-to-library) is run and captured if Chrome is available, otherwise graded as evidence with the reason recorded.

**Green when:** the pages render against the real API; browser AC graded honestly.

---

## Slice 5 — the PRD carve + docs

1. **`docs/prd/in-progress/PRD-031a-marketplace-catalog.md`** — the carved PRD (already drafted; finalise ACs against the shipped tests).
2. **`docs/prd/pending/PRD-031-marketplace.md`** — add the `## Carved out to PRD-031a` section (the Moved/Retained FR+AC table); PRD-031 stays pending with the 031b remainder.
3. **`docs/roadmap.md`** — mark chain step 4 in progress/done; **`AGENTS.md`** — a short "Marketplace gotchas (PRD-031a)" section (the shared `customizer_guard`, the digest-as-param-spec, the fixed export set, `refresh=False`, the `search`-before-`{name}` route order, `_public_indexes` dual scope). **`docs/packages.md`/`docs/user-guide.md`** — a Market section.
4. **`docs/changelog/NNNN-*.md`** — one entry per commit from the actual diff (CLAUDE.md rule).

---

## Slice 6 — acceptance sweep

Run AC1–AC9 as a dedicated `test_prd031a_acceptance.py` (the 005a/007 pattern): the anonymous end-to-end, the zero-kernel positive control, the surface equality, the containment inheritance, the lockfile pin on a copy, the OCP-free assertion, the provenance surfaces, the browser AC graded. Cite `make test-fast` / targeted counts (the suite is 8-way parallel — keep runs targeted; the machine may be contended by a second checkout).

---

## Parallel-safety & constraints (every slice)

- Bind `0`; `TestClient(base_url="http://127.0.0.1")`, `extra_allowed_hosts={"testserver"}`; `--projects-dir` under the scratchpad; never `~/AgentCAD`.
- No `uv sync`/`uv pip install`; no state-changing git; scratch venv only if a subagent needs one.
- Extension points only: three route additions to `routes_public.py`, one new `routes_market.py` pack, one new `tools_market.py` pack, additive edits to `share_build.py`/`routes_share_public.py`/`search.py`. **No edit to `worker.py`/`tools.py`/`app.py`/`service.py`.** The `create_app(security=)`, `PREFIX`, `routes_public`, `share_build` and `ensure_share` seams already exist.
- Every new anonymous route joins `EXPECTED_PUBLIC` in the change that mounts it. `NOT_YET_BUILT` stays `== set()`.
