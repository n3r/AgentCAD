# 0229 — 2026-08-19 — PRD-031a closed out: the marketplace catalog ships, one anonymous kernel wall

## Summary

Bookkeeping after PR #21 (seeded marketplace catalog) merged to main with all
seven checks green on the first run. The PRD moves to `docs/prd/completed/` and
the roadmap's marketplace-chain **step 4 is done**. A logged-out visitor can
now browse the seeded PRD-011 catalog, open a listing, move a slider to rebuild
a bounded variant, download STEP/STL/3MF, and — once signed in — add a package
to their project. This is the payoff of the resequencing: 011's catalog +
005a's hosting + 007's customizer combined into a browsable Market.

Next on the chain: **step 5, PRD-006 (sandboxing & quotas)** — the deferred
deployment work that becomes blocking only now, for 031b's open publishing
(third-party code running on our servers). Everything before it needed only
our own seeded content.

## Changes

- `docs/prd/in-progress/PRD-031a-marketplace-catalog.md` →
  `docs/prd/completed/`, status "completed — merged to main in PR #21".
- `docs/roadmap.md`: step 4 marked **DONE (PR #21)**; the 031a row links to
  `prd/completed/`.

## Notes

The defining risk of a marketplace built on PRD-007 was a *second* anonymous
`exec()` path with weaker limits than the one 007 already contains. It was
closed by construction and an independent security reviewer confirmed it under
attack: both `/variant` and `/download` reach the kernel only through 007's
process-global in-flight semaphore (proven — with the semaphore held, both 429
without building) and the pool-reservation 503; the per-IP `TokenBucket` +
login gate is one shared `CustomizerGuard` (draining it via `/s/` throttles
`/market`); the per-IP key is the proxy-resolved address (the PRD-005a M3
lesson holds); browse/search/script/params/mesh are provably zero-kernel; and
a `scope: private` index is byte-identical to nonexistent on all seven market
routes including `market_install` (the 005a M-2 lesson).

Two independent reviewers this cycle, split by axis — security/containment
(SHIP, no defects) and correctness/data-integrity (which verified the listing
payload is *truthful*: digest param ranges spot-checked equal to the real
PARAMS, the digest drives real validation, install reproducibility holds). The
one blocking finding was a shipped placeholder `make test` count in the
close-out changelog — the build agent looped on a suite-completion event it
could not receive and never filled the number, and the count guard was fooled
by a prior-tree count quoted as provenance. Fixed, with the guard given real
teeth (a regex for `\d{3,} passed`). The confirming full suite also caught a
PRD-008 identity regression the build agent's incomplete run missed: the public
market fetches carried the browser `X-Agent-Id`; dropped, because an anonymous
browse must not carry a per-profile fingerprint.

Two caveats on the record, as with 005a and 007:

1. **Codex's second review did not run** — rate-limited until 20 Aug 2026.
   Coverage this cycle was two independent Opus reviewers on split axes plus
   adversarial verification. Re-running Codex against merged main when credits
   reset is a worthwhile follow-up on the accumulated anonymous surface.
2. **AC9's browser half is unverified** across every session (the
   Claude-in-Chrome extension was unavailable). The API/HTTP contracts are
   tested and the flows ran against a real hosted instance; no browser rendered
   the Market pages (`list_connected_browsers` → `[]`). AC9 is **graded as
   evidence** — never rendered by a browser — and pinned by a test that fails
   if that admission is removed.

Two LOW residuals recorded as acceptable for the seeded scope: the digest's
param spec is bound to the pinned script at publish time, not re-verified on
the anonymous customizer read (a runtime content-id check is 031b work once
third parties publish); the frontend "Add to library" hardcodes the catalog
index name while the `market_install` tool resolves it dynamically.

Final suite: `make test` — 4068 passed, 1 skipped (clean run on the committed
tree). Suite growth across the PRD: 3974 → 4068.
