# 0212 — 2026-08-18 — PRD-007 (share links & customizer) design: the first anonymous exec, contained

## Summary

The design round for marketplace-chain step 3, riding directly on 005a: a
`Publication` object (a capability token, not project data), a kernel-free
read-only viewer link, and a customizer whose slider moves are bounded-param
rebuilds. This is the FIRST anonymous request that legitimately reaches
`build(p)` — 005a's proven "anonymous = zero kernel" invariant, broken on
purpose and contained.

## The decisions that shaped it

- **The rebuild is a `GET`, not a `POST`.** A variant is a pure read of a
  content-addressed artifact (owner state never changes on the visitor path),
  so `GET` makes CSRF moot, makes cross-origin embedding work by construction,
  and keeps the entire `security.py` change to two `PUBLIC_PREFIXES` entries —
  no Origin-check exemption to the guard 005a's review hardened.
- **One `Publication` with a `customizer` boolean.** Viewer and customizer are
  one record; escalation is structural (the flag is owner-written; `/variant`
  404s before the builder when it is off), never a request-time check.
- **The pin is a copy, not a reference.** Publish resolves a PRD-001 tag, reads
  the script bytes at that commit (`packet._script_at`, no worktree),
  content-addresses them (PRD-011 sha256), and copies into a one-part muzzled
  build service under the state dir. A later owner edit cannot change what a
  live link serves; the visitor path never touches a user `ProjectStore`.
- **State in `<state-dir>/publications/`**, atomic-JSON + `flock`, shaped like
  005a's auth store — a token outlives branch switches, so it is not project
  data.
- **Param parity is inherited, not forked:** `service.normalize_params`
  rejects, `worker._resolve_params` clamps-and-warns — the authoring path's
  own validators.
- **`TokenBucket` promoted to `core/ratelimit.py`** (its second consumer; a
  one-line re-export keeps `presence.py` behaviour-identical).

## Containment of the anonymous exec

A global `BoundedSemaphore(AGENTCAD_SHARE_MAX_INFLIGHT)` (default conservative)
sized below the kernel pool, plus `affinity="share:<pub>"` segregation, in
front of per-link and per-IP token buckets and the content-addressed variant
cache. Named residual until PRD-006: **memory caps** — a params-driven mesh can
still balloon RSS (a per-build timeout does not bound peak memory), plus
pid/disk/egress. The pre-006 backstop is `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE`
(off by default).

## Founder decisions (18 Aug 2026)

- `/embed/` ships **`frame-ancestors *`** (the growth loop; a public auth-free
  read-only customizer has little to hijack), and — decided this round — **the
  main app now sends `frame-ancestors 'none'`** so the authenticated surface
  can't be framed (a header on the non-share routes, not a new reachable path).
- Link expiry defaults to **never, revocable**; per-link `expires_days` stays
  an additive opt-in.
- The login-above-N gate ships **off**; viewer links need **no account**.

## Divergences from the PRD as written (recorded in the spec)

- The rebuild is a GET (above). The viewer streams **ACM**, not glTF —
  `core/gltf.py` is deferred to PRD-017, which the PRD's own risk note already
  assigns there. Store-backed sha256 token, not HMAC. Two route packs, not one
  (a pack carries one `PREFIX`). **Part-scope MVP**; project/assembly scope,
  embeds-with-sliders, drawings and branch-following are Phase 2.
- The PRD header still lists 005/006 as hard deps; the roadmap already
  corrected this (005a suffices, 006 not required for our own content). The
  design note in the PRD folds it in.

## Files

- `docs/superpowers/specs/2026-08-18-share-links-customizer-design.md` —
  9 decisions, route-by-route exposure table, containment threat section,
  object model, divergences
- `docs/superpowers/plans/2026-08-18-share-links-customizer.md` — 6 TDD slices
- `docs/prd/in-progress/PRD-007-share-links-customizer.md` — moved from
  `pending/`, with a design-alignment note
- `docs/changelog/0212-prd-007-design.md` — this entry

## Notes

Docs only; no production code. Slices order store → publish → kernel-free
viewer → capped customizer → UI/embed → docs/acceptance. Last full-suite
measurement: 3689 passed, 1 skipped (0199, 005a close-out); PRD-012 has since
merged, so the build round will re-baseline.
