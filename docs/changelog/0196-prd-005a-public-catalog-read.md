# 0196 — PRD-005a slice 7: the scope-filtered public catalog read

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (with Nikita Fedorov)

## Summary

The last four entries of the nine-entry anonymous surface: a `routes_public.py`
pack that serves the bundled catalog — package list, package, version, preview
PNG — with no credential, no kernel call and, the point of the whole pack, **no
private index**. `NOT_YET_BUILT` in `tests/test_hosted_surface.py` is now
empty, so the enumeration and the kernel-silence proof cover the surface in
full (FR16 / AC6 / AC7).

## Changes

- **`agentcad/server/routes_public.py` (new).** Four `GET` routes under
  `/api/public/packages`.
  - `_public_indexes(service)` is the entire access rule: an index is admitted
    for `scope == "public"` and for nothing else, and the filter runs **before**
    any lookup, so a private index is not consulted at all rather than
    consulted-and-then-hidden. Anything whose scope is not the literal
    `"public"` — including a future index kind that has no scope attribute — is
    refused, so the failure direction is invisible.
  - **One name-free 404 message** (`NO_SUCH_PACKAGE`) for every miss on every
    route: a package carried only by a private index and a package that does
    not exist must be indistinguishable, and two bodies that differ by a quoted
    name are an existence oracle over the private index.
  - The preview handler reuses `routes_packages.py`'s exact two-part
    containment — the `.png` suffix check, then `content.resolve_within` — and
    every response carries `Cache-Control: public, max-age=300`.
  - No `registry.call`, no `service.kernel`, no `service.store`; the payload is
    what `catalog/index.json` already ships plus the three facts the caller
    cannot derive (`name`, `version`, `index`).
- **`tests/test_public_catalog.py` (new)** — 29 tests.
- **`tests/test_hosted_surface.py`** — `NOT_YET_BUILT` is now empty (kept, with
  a comment, rather than deleted: PRD-007's share links should grow
  `EXPECTED_PUBLIC` and the subtrahend together), and
  `test_public_surface_makes_no_kernel_calls` now runs against a client with
  the catalog configured and **asserts the 200s**.
- **`tests/conftest.py`** — `hosted_with_catalog` / `hosted_with_private`
  fixtures, the `configure_private_index` helper, and `AGENTCAD_CONFIG` pinned
  to `tmp_path` inside the `hosted` fixture.
- **`tests/test_packages_api.py`** — `test_the_gate_is_not_reachable_over_http`
  learns the four new paths (see Notes).

## Files

- `agentcad/server/routes_public.py` — new
- `tests/test_public_catalog.py` — new
- `tests/test_hosted_surface.py` — `NOT_YET_BUILT` emptied; the kernel-silence
  test given real data and positive assertions
- `tests/conftest.py` — catalog/private-index fixtures + config isolation
- `tests/test_packages_api.py` — the route-inventory test updated

## Notes

- **The kernel-silence test was proving silence about nothing.** With no index
  configured, `_fill()` turned every public catalog path into a 404 and the
  assertion `calls == before` held trivially. It now drives
  `hosted_with_catalog` and asserts that `/`, `/api/health`, `/js/api.js`,
  `/css/app.css` and the three JSON catalog routes really answered `200`
  (the preview separately, because `_fill` cannot supply a `?path=`). The
  positive control on the counter itself
  (`test_the_kernel_counter_actually_counts`) is slice 2's and still stands.
- **Config isolation was a live hazard the moment this pack existed.** The
  `hosted` fixture never set `AGENTCAD_CONFIG`, so the first hosted test to
  read `service.packages` would have loaded the **developer's own**
  `~/.agentcad/config.json` — git indexes included, which shell out to git —
  under eight parallel workers. Pinned in the fixture rather than in the one
  test file that noticed.
- **Divergence from the plan, deliberate: the pack is live in local mode too.**
  `routes_auth.py` answers `404` without a `SecurityConfig` because identity
  state must not follow a service into `checks._ephemeral_service`. This pack
  has no such state: it reads public catalog files and is strictly *narrower*
  than the `/api/packages/search` a loopback bind already serves to anybody.
  Running one code path in both modes is what keeps the scope filter on a line
  the whole suite exercises;
  `test_the_pack_is_mounted_in_local_mode_too_and_still_filters` pins it.
- **The shadowing case the plan does not name.** A private index configured
  *first* wins precedence in `service.packages.indexes`. An implementation that
  looked a name up across all indexes and then checked the scope of whichever
  one answered would serve private content under a public name.
  `configure_private_index` therefore always installs the private index first,
  and `test_a_private_index_cannot_shadow_a_public_package` carries a private
  `din625` against the public one.
- **`refresh()` is never called.** `GitIndex.refresh` clones or fetches; the
  public read only ever reads an existing clone, so an anonymous request cannot
  make the server perform a network operation. `search` refreshes; this does
  not, and that difference is deliberate.
- **A broken `index.json` cannot take the anonymous route down.**
  `load_indexes` already refuses to let one broken index hide the others at
  *load* time; `_entries()` applies the same rule on *read*, since there is no
  credential in front of this route to slow an attacker who can get a hostile
  index configured.
- **One pre-existing test edited**, the second in this feature (slice 3 edited
  `test_packages_cli.py`). `test_packages_api.py::
  test_the_gate_is_not_reachable_over_http` asserts the *complete* set of
  package routes by equality — it did its job and caught four new ones. Rather
  than filtering `/api/public/` out of the set (which would blind it), the four
  are listed, plus a new assertion that every `/api/public/` path carries `get`
  and nothing else.
- **Both negations verified by breaking the code**, not asserted: replacing
  `_public_indexes` with `list(service.packages.indexes)` fails 5 tests
  (invisibility, version+preview invisibility, shadowing, the unit test, and
  the local-mode one); putting the requested name back into the 404 message
  fails the two indistinguishability tests.

## Verification

- `.venv/bin/python -m pytest tests/test_public_catalog.py
  tests/test_hosted_surface.py -q` → **49 passed in 11.49 s**.
- With the neighbouring suites (`test_security_guard`, `test_auth_routes`,
  `test_tokens`, `test_cli_admin`, `test_hosted_hardening`, `test_actor_kind`,
  `test_claims`, `test_route_prefix`, `test_server`, `test_packages_api`,
  `test_catalog`, `test_packages_index`) → **425 passed in 117.43 s**.
- **Against a real hosted `agentcad serve`** (port 8645, scratch config, state
  and projects dir, a private `acme` index configured *first* beside the
  bundled catalog):
  - anonymous `GET /api/public/packages` →
    `['din625', 'extrusion_2020', 'extrusion_3030', 'iso4014', 'iso4762',
    'iso7380', 'nema17', 'nema23', 'thread_insert']` with
    `cache-control: public, max-age=300` — the private `acme-internal` absent;
  - anonymous `…/din625/versions/1.0.0` → `index: agentcad-core`,
    `gate: green`, `previews: ['previews/ball_bearing_iso.png']`;
  - anonymous preview → `200 image/png 6865B`, `PNG image data, 640 x 480`;
  - `…/acme-internal` and `…/does-not-exist-at-all` → **byte-identical**
    `{"error":{"type":"NotFoundError","message":"no such package in the public
    catalog","details":{}}}` `[404]`, and the private preview the same;
  - `?path=../../../../etc/passwd` → 422, the sibling-package spelling
    `../../iso4762/1.0.0/previews/...png` → 422 from `resolve_within`,
    `?path=parts/ball_bearing.py` → 422;
  - anonymous `/api/packages/search` and the authenticated preview route →
    **401** (they walk every index, which is why they stay private);
  - signed in, `/api/packages/search` → hits include `acme-internal`,
    `indexes: ['acme', 'agentcad-core']` — the private index is not disabled,
    it is not anonymously readable.
- The full-suite count for this branch is in the next entry (0197), which is
  the one that closes the feature.
