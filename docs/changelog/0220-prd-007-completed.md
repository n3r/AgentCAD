# 0220 — 2026-08-18 — PRD-007 closed out: the growth loop ships, the anonymous exec is contained

## Summary

Bookkeeping after PR #20 (share links & customizer) merged to main with all
seven checks green. The PRD moves to `docs/prd/completed/` and the roadmap's
marketplace-chain **step 3 is done**. A hosted instance can now publish a part
as a capability-token URL: a read-only viewer link (zero kernel calls) or a
customizer whose slider moves are bounded-param rebuilds.

Next on the chain: **step 4, PRD-031a** — the seeded public read-only catalog
with add-to-library. Its browse payload already exists (005a serves
`catalog/index.json`'s metadata and previews anonymously, filtered to
`scope: public` indexes) and add-to-library is the existing authenticated
`add_package`/`use_part` path, so 031a is now unblocked with 011 + 005a + 007
all shipped.

## Changes

- `docs/prd/in-progress/PRD-007-share-links-customizer.md` →
  `docs/prd/completed/`, status "completed — merged to main in PR #20".
- `docs/roadmap.md`: step 3 marked **DONE (PR #20)**; the 007 index row links
  to `prd/completed/`.

## Notes

This PRD deliberately opened the first hole in PRD-005a's proven "anonymous =
zero kernel" invariant — the customizer rebuild is the first anonymous request
that legitimately reaches `build(p)` — and the whole review turned on whether
that hole is contained without the deferred PRD-006 sandbox. It is: an
independent reviewer confirmed anonymous cannot reach *unbounded* kernel work
(a global in-flight semaphore, per-link + per-IP token buckets keyed on the
proxy-resolved address, a per-build timeout, and param parity), re-verified
the viewer stays zero-kernel and the pin stays immutable, and ran a clean
bypass sweep.

The review found two overclaims — docs asserting a containment the code did
not provide — both fixed with real code and re-verified: `affinity=` was
cache-warmth routing, not "segregation," and the default in-flight cap could
exceed the pool, so the fix reserves a member worker (`pool_size - 1`, proven
live by a real member build completing while the share slot was held) and
refuses the customizer with a clear 503 on a single-worker pool; and the
variant cache keyed on pre-clamp params, so a shared clamp module now keys it
on clamped params with byte-exact worker parity. The load-bearing design
choice — the rebuild is a GET, a pure read of a content-addressed artifact —
is what let CSRF be moot and embedding work by construction.

Two caveats are on the record, in the PR body and the PRD's own tables:

1. **Codex's second independent review did not run** — rate-limited until
   20 Aug 2026. Like PRD-005a, this security-surface PRD had one independent
   reviewer plus adversarial verification rather than two independent reviews.
   Re-running Codex against merged main when credits reset is the most
   valuable follow-up.
2. **AC1/AC7's browser half is unverified** across every session (the
   Claude-in-Chrome extension was unavailable). The HTTP contracts are tested
   and the flows ran against a real hosted server; no browser rendered the
   pages. Graded honestly, pinned by a test.

CI green on the first full run after two fixes: a merge conflict with PR #19
(PRD-012 follow-up) in `core/model.py` — both branches added error-handling
code, kept both, verified semantically clean — and a test-environment fix
(the customizer build-path acceptance tests must declare the pool>=2 the
feature now requires, since CI pins the pool to 1; the product correctly
refuses on pool=1). Deferred hardening (peak-memory caps, disk GC for the
variant cache) is recorded as PRD-006 work with the login-gate knob as the
pre-006 backstop.

Final suite: `make test` — 3974 passed, 1 skipped (one PRD-009 wall-clock
test flaked under local machine contention, passes standalone; a follow-up to
move it into the CI serial tail). Suite growth across the PRD.
