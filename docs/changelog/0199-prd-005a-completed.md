# 0199 — 2026-08-18 — PRD-005a closed out: the hosted core is real, and its boundary held

## Summary

Bookkeeping after PR #17 (hosted core) merged to main with all seven checks
green on the first run. The PRD moves to `docs/prd/completed/` and the
roadmap's marketplace-chain **step 2 is done**. PRD-005 stays pending as the
explicit remainder (orgs, roles, audit principals, OIDC/passkeys, per-tenant
scheduling, local-first sync), carved FR-by-FR in its header.

Next on the chain: **step 3, PRD-007 (share links & customizer)** — the growth
loop, which inherits four things 005a shipped and tested for it: the
`PUBLIC_PATHS` allowlist with the reachable-set *equality* test, the route-pack
`PREFIX` seam, `TokenBucket` as the rate-limit primitive, and the recorded
verdict that bounded params on a member-authored script is shippable before
PRD-006.

## Changes

- `docs/prd/in-progress/PRD-005a-hosted-core.md` → `docs/prd/completed/`,
  status "completed — merged to main in PR #17".
- `docs/roadmap.md`: step 2 marked **DONE (PR #17)**; the 005a index row links
  to `prd/completed/`.

## Notes

The one boundary this PRD existed to hold — **anonymous reaches zero kernel
execution, zero arbitrary file read, zero private data** — held under an
independent reviewer's from-scratch auth-bypass sweep run *three times* (101
routes; every path-normalization, prefix, traversal and WS trick), proven by a
runtime kernel-call counter with a positive control and symlink/`..`/absolute
containment on the single anonymous file-read path.

The review found four real web-security defects, all fixed with
reproduce-then-verify evidence:

- **Login CSRF was dead code** — the Origin check sat below the anonymous
  return, so a cross-site `text/plain` POST silently signed a victim into the
  *attacker's* account. The control was asserted in the code comment,
  AGENTS.md, and changelog 0189, yet never executed and had no test — the exact
  "green with a wrong answer" shape this gate exists to catch, in the security
  surface where it matters most.
- **An index's own document could override the operator's `scope: private`.**
  `scope` was repurposed from PRD-011's publish policy into anonymous access
  control without changing who authors it.
- **The per-handle login limiter was a permanent-lockout primitive** — and its
  first fix was *localhost-only*, collapsing behind the very reverse proxy the
  deployment guide prescribes. Fixed properly by resolving the client address
  through uvicorn's bounded proxy-header parsing, with trust-everyone refused
  by *meaning* (`0.0.0.0/0`, `::/0`), not just the literal `*`.
- **Password recovery left sessions live** for 30 days.

Two caveats carried into the PR body rather than buried, and worth keeping on
the record:

1. **Codex's second independent review did not run** — the account hit its
   usage limit (resets 20 Aug 2026). This PRD had one independent reviewer plus
   three adversarial verification passes rather than the usual two independent
   reviews. For a security surface that is a genuine reduction in coverage;
   re-running Codex against the merged branch when credits reset is worth
   doing, and is the single most valuable follow-up on this feature.
2. **AC3's browser half is unverified** across four sessions (the
   Claude-in-Chrome extension was unavailable to every build agent, the
   reviewer, and the orchestrator). Every HTTP contract behind the sign-in and
   enrol UI is tested and the flows ran against a real hosted server and a real
   container, but no browser rendered the pages. Merging with it graded
   unverified was an explicit founder decision; the PRD grades it honestly and
   a test fails if the admission is removed.

Deferred hardening recorded as PRD-006-adjacent follow-ups: container
`cap_drop`/`no-new-privileges`/`mem_limit`/`pids_limit`, `AGENTCAD_SECRET_KEY`
visibility via `docker inspect` when set explicitly (the generated-key file is
the recommended path), and Origin/Host case-sensitivity. GitHub was mid-outage
when the PR opened; it was created and merged once the API recovered.

Final suite: `make test` — 3689 passed, 1 skipped. Suite growth across the
PRD: 3316 → 3689.
