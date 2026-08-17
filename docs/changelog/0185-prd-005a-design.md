# 0185 — 2026-08-17 — PRD-005a (hosted core) design: an account is a remote shell

## Summary

The design round for marketplace-chain step 2, carving 005-lite out of
PRD-005 as a new PRD-005a (hosted core): deploy + identity + public read.
Orgs, roles, audit principals and local-first sync stay in PRD-005, whose
header now records the carve-out FR by FR.

## The decisions that shaped it

- **Security posture first.** `sandbox.py` gates confinement on darwin, so on
  Linux — the deployment platform — **an account is a remote shell**. Hence:
  registration is closed (no register route exists at all), roles are
  explicitly not a boundary between members, and the anonymous surface is
  pre-generated content with a test asserting **zero kernel calls**.
- **PRD-007's customizer analysed now so 007 does not re-derive it:** bounded
  params are data passed to `build(p)` — no code execution for a visitor.
  Shippable before 006 given param-validation parity, token buckets, the
  variant cache and no owner writes; what 006 still adds (memory/pid caps,
  egress denial) is listed rather than waved at.
- **Identity:** invite-only local accounts (`hashlib.scrypt`), opaque
  server-side sessions, bearer tokens for agents (sha256 — 256-bit entropy has
  nothing to brute-force and scrypt would tax every agent request ~100 ms).
  Zero new dependencies; passkeys/OIDC/magic-links each pull one in.
- **Storage diverges from PRD-005:** atomic JSON + flock, not SQLite — the
  audit volume that motivated SQLite is deferred with the audit work, and
  `docker compose exec … admin` is routinely a second writer (the
  `LocalIndex._index_scope` situation exactly).
- **Middleware seam:** confirmed absent; an explicit `security=` parameter on
  `create_app` over a discovered pack, because **pack discovery fails open**
  and a security middleware that silently fails to load leaves the box open.
  A core edit, justified and flagged.

## A latent cross-PRD bug the design surfaced

`proposals.actor_kind` classifies by `startswith("browser:")`, so a hosted
identity `user:nikita/browser:…` classifies as **agent** — and
`ClaimRegistry.acquire` returns None for agents while `_blocking` never
blocks them: PRD-008's per-part claim protection would silently switch off
the day hosting turned on, with no error anywhere. Two lines; its four
consumers import it; AC10 pins it.

## Files

- `docs/superpowers/specs/2026-08-17-hosted-core-design.md` — 14 decisions,
  route-by-route exposure table, threat model, divergences
- `docs/superpowers/plans/2026-08-17-hosted-core.md` — 8 TDD slices
- `docs/prd/in-progress/PRD-005a-hosted-core.md` — 27 FRs, 11 ACs
- `docs/prd/pending/PRD-005-multi-tenant-cloud.md` — carve-out header
- `docs/roadmap.md` — 005a row + chain steps updated
- `docs/prd/README.md` — stale `shipped/` → `completed/`
- `docs/changelog/0185-prd-005a-design.md` — this entry

## Notes

Orchestrator calls at review: the catalog browse *page* stays out (031a's
deliverable; the JSON API ships), and member-vs-member delete semantics are
deferred to PRD-027, which is the first feature that forces them. The
design's note that 007 is the one chain step whose risk is not fully retired
by the step before it is recorded for the 007 design round — its own
mitigation (a per-deployment require-login-above-N-rebuilds switch) rides
there. Docs only; no code. Last full-suite measurement: 3235 passed, 1
skipped (0184; `make test` is now 8-way parallel after PR #16).
