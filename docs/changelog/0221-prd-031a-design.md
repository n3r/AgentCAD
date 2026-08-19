# 0221 — 2026-08-18 — PRD-031a (seeded marketplace catalog) design: the payoff of the chain, one shared containment

## Summary

The design round for marketplace-chain **step 4**, carving 031a out of
PRD-031: a public read-only Market over the *already-seeded* PRD-011 catalog —
browse/search/filter, listing pages (metadata, previews, specs, provenance,
versions, read-only script, a customizer preview), artifact download, and
add-to-library for logged-in users. This is where 011's catalog, 005a's
hosting, and 007's customizer combine into something a visitor can browse.
031b (open publishing, the AST gate, policy scan, signing, remix, moderation,
economy) stays in PRD-031, which needs PRD-006 and is now marked "split" with
the remainder recorded FR-by-FR.

## The decision that matters: one anonymous kernel path, not two

The sharpest risk in a marketplace-over-007 is a *second* anonymous `exec()`
path with weaker limits than the customizer 007 already contains. The design
closes it by construction:

- **The listing customizer is 007's containment scoped to a catalog
  `content_id`** — a catalog version is already content-addressed and
  immutable, so it *is* the pin; `ShareBuilder.build_catalog_variant` reuses
  the muzzled build service, the process-global in-flight semaphore, the
  pool-reservation 503, `normalize_params` parity, the `paramclamp`
  clamp-before-cache, and the content-addressed variant cache. No
  `Publication`, no token, no expiry.
- **The one primitive that could double a visitor's allowance — the per-IP
  `TokenBucket` + hourly login gate — is shared** across `/s/` and `/market`
  via a new `service.customizer_guard` extracted from `routes_share_public`;
  an AC asserts the shared-object identity.
- **The param spec comes from the pre-generated `index.json` digest**
  (`parts.<id>.params` already carries `{type,min,max,choices}`), so browse,
  search, listing and params stay zero-kernel and a variant is *exactly one*
  kernel call — the `inspect` path is never hit anonymously.

Kernel-free routes (search, script, params) extend `routes_public.py`; the two
kernel-reaching routes (variant, download) live in a new `routes_market.py`,
so `routes_public`'s zero-kernel invariant stays literally true. Public search
is `search.search(_public_indexes(service), …, refresh=False)` — `scope:
public` only (an operator-private index must never surface — the 005a M-2
lesson), no git fetch on the anonymous path.

## Founder-default decisions taken this round (recorded, not asked)

These are internal implementation choices with obvious defaults, not
user-facing product forks, so they were decided rather than escalated:

- **Fixed `{step,stl,3mf}` export set** for all listings (a per-package owner
  mask is a PRD-011 format change, deferrable to 031b); a format outside the
  set 404s before the builder, gated identically to 007.
- **Extend `/api/public/packages`** rather than mint `/api/public/market` —
  one namespace, reuse `_public_indexes`/`_find`/`_miss` in file (the only
  cost is declaring `/search` before `/{name}`).
- **Reuse PRD-007's `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE`** via the shared
  guard, not a catalog-specific threshold.

## add-to-library reuses PRD-011 verbatim

A logged-in user's browser calls the existing authenticated `add_package`
(index = the public catalog) + `use_part`; the PRD-011 lockfile pins the
version. A new seeded-catalog-scoped `market_install` agent tool. A
marketplace is a registry index + a web front + (later) an economy; 031a is
the first two over content that already exists.

## Files

- `docs/superpowers/specs/2026-08-18-marketplace-catalog-design.md` —
  9 decisions, the exposure table, the customizer-reuse decision, divergences,
  "what 031b/006 owes"
- `docs/superpowers/plans/2026-08-18-marketplace-catalog.md` — 6 TDD slices
- `docs/prd/in-progress/PRD-031a-marketplace-catalog.md` — carved from
  PRD-031 (AC1–AC9, verification-levels + residual-gaps tables)
- `docs/prd/pending/PRD-031-marketplace.md` — "split", remainder recorded
- `docs/changelog/0221-prd-031a-design.md` — this entry

## Notes

Docs only; no production code. Lighter than 005a/007 (6 slices) because the
substrate — the catalog, public read, the customizer, add-to-library — is all
shipped; 031a is primarily a public browse/listing API and Market UI over
existing data. All flagged edits are additive (catalog variant methods on
`share_build`, the shared guard, a `license`/`refresh` filter on `search`); no
`worker.py`/`tools.py`/`app.py`/`service.py` core touched. Last full-suite
measurement: 3974 passed, 1 skipped (0220).
