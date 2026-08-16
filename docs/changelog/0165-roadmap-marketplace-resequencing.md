# 0165 — 2026-08-16 — Roadmap resequenced: the marketplace chain moves onto the critical path

## Summary

A founder-level strategy review of the completed and pending PRDs, resolving a
contradiction that had been sitting in the plan unnoticed: **PRD-031
(marketplace) was not scheduled late, it was unreachable.** All four of its
hard dependencies — 005 multi-tenant cloud, 006 sandboxing, 007 share links,
011 package format — included the two PRDs that had been deferred as
production-deployment work, plus a third (007) that depends on both.

The decision: a minimal hosted slice of PRD-005 (deploy + identity + public
read) is lifted out of the deferred work; the rest of 005 stays deferred; and
PRD-031 splits so that sandboxing blocks only the half that actually needs it.

## The argument, in short

The premise is that the software is the copyable part — a competent team with
coding agents reproduces this feature set. What compounds is a catalog of
kernel-validated parts, a format others adopt, and an audience.

The load-bearing observation is that **PRD-031 fuses two separable things**:

- **the asset** — validated catalog + format, which needs *none* of 031's four
  blockers, because PRD-011's MVP already supports git-hosted indexes (a repo
  is an index);
- **the storefront** — identity, listings, moderation, economy, which is where
  all four blockers live and is the commodity half.

Inside that split is a real technical fork. PRD-031's safety inversion —
marketplace code never executes on a consumer's machine — is *precisely* what
forces cloud + sandbox. A git-hosted registry has the opposite model: code runs
locally, like pip. Normal for a developer tool, unacceptable for a consumer
marketplace. Only the first is reachable today, and it is the one that
accumulates the asset.

Two further points that changed the ordering rather than just the phase labels:

- **Audience comes from share links before it comes from a market.** PRD-031's
  own cold-start mitigation is the PRD-007 customizer loop. An empty shelf is
  not a growth surface; a part that becomes a URL is.
- **The gap between format and open publishing is healthy.** Changing a
  published format breaks every pinned consumer, so the seeded catalog is the
  format's proving ground — an argument for the gap, not against it.

## Changes

- `docs/roadmap.md`: new "Sequencing decision — the marketplace chain
  (16 Aug 2026)" section recording the reasoning and the resulting six-step
  order (011 registry-first → 005-lite → 007 → 031a → 006 → 031b), which
  supersedes the phase ordering where they conflict.
- Index rows updated: 005 (005-lite pulled onto the path, rest deferred), 006
  (step 5, blocking only 031b), 007 (promoted to step 3, dependency corrected
  to 005-lite), 011 (in progress, registry-first), 031 (split 031a/031b with
  per-half dependencies).
- Demoted behind the chain: 013, 014, 015, 017 — daily-driver depth that buys
  credibility but does not compound. 026/027 noted as movable: if we are
  inviting an audience, the shell is the shop window.

## Files

- `docs/roadmap.md` — sequencing section + five index rows
- `docs/changelog/0165-roadmap-marketplace-resequencing.md` — this entry

## Notes

The success metric for the catalog is recorded as **usefulness, not
contributors**. We will not out-community GrabCAD's 7M engineers; the winnable
bet is out-*availability* against McMaster (700k parts) and TraceParts (100M),
both closed and vendor-fed, on mate-ready *parametric* parts — because agents
author and the kernel referees. That bet does not require a contributor base,
which is what makes it reachable from where this project actually is.

**Open blocker recorded, not resolved:** the repo still has no LICENSE file and
no license field. It first surfaced in PRD-009 when a GPL-3.0 solver dependency
was declined, and was a footnote for a private tool. It is blocking for a
published package format carrying a `license` field, and must be resolved
before 031a.

No code changed; `make test` is unaffected by this entry (docs only). The last
measured full run remains 2527 passed, 1 skipped (changelog 0163).
