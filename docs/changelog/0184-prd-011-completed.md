# 0184 — 2026-08-17 — PRD-011 closed out: the registry exists, and this repo is its first index

## Summary

Bookkeeping after PR #15 (parts library & package registry) merged to main.
The PRD moves to `docs/prd/completed/` and the roadmap's marketplace-chain
step 1 is **done**: the package format is live, the nine-package COTS catalog
is published through the real gate, and this repository serves it as a git
index via `subdir: "catalog"` — "a repo is an index" is now a fact about
*this* repo, not a synthetic fixture.

Next on the chain: **step 2, 005-lite** (deploy + identity + public read),
then 007 share links, then the 031a seeded read-only catalog.

## Changes

- `docs/prd/in-progress/PRD-011-parts-library-registry.md` →
  `docs/prd/completed/`, status "completed — merged to main in PR #15
  (AC1–AC9 verified; AC9 adopted at design review)".
- `docs/roadmap.md`: sequencing step 1 marked **DONE (PR #15)**; the index
  row links to `prd/completed/`.

## Files

- `docs/prd/completed/PRD-011-parts-library-registry.md` — moved + status
- `docs/roadmap.md` — step row + index row
- `docs/changelog/0184-prd-011-completed.md` — this entry

## Notes

Feature history: a design round that also settled the repo's license
(0166), 14 TDD slices (0167–0180), and a review cycle that is the longest
and hardest-fought of any PRD here (0181–0183): two independent reviews —
Opus, 18 findings; Codex GPT-5.6 xhigh, 13 findings, 8 novel — three
adversarial verification passes, four fix rounds, and a verdict of SHIP only
after every fix had been attacked with its probe quoted.

The review's through-line deserves its place in the record: **"gate: green
is not load-bearing evidence"** — it could be green for a package with no
script, five loose solids, tautological specs, and stages that never ran.
The fixes were structural, not cosmetic: the gate snapshots and attests the
bytes it measured by construction; publish re-derives its verdict from rows;
materialisation answers to the git-tracked lockfile, not a machine-local
receipt. A marketplace whose central promise is "kernel-validated" needed
exactly this fight, and it needed it *before* 031a, not after.

Six CI rounds closed the PR, five of them Windows: one flaky perf benchmark
(pre-existing, PRD-009's), and one real lesson in three coats — **content
addressing is only as good as byte stability**, and Windows attacks it from
the clone side (`core.autocrlf` rewrites at checkout), the repo side (a git
index must carry `.gitattributes * -text`), and the write side (text-mode
`\n` → `\r\n` translation, which also sat latent in `from_step.scaffold` and
would have broken every Windows author's first package). All three are now
pinned in code, in `.gitattributes`, and as an AGENTS.md rule.

Also fixed along the way, outside the PRD's nominal scope and flagged as
such: `core/project.py`'s `_atomic_write` staged every write through one
fixed `<name>.tmp`, so concurrent writers corrupted each other's manifests
(measured: 293 corrupt reads before, 0 after). The same idiom remains in
`kernel/worker.py:403` and `config.py:38` — recorded as follow-ups with the
`tests/test_packet.py:1166` breadcrumb, deliberately not smuggled into this
PR.

Final suite: `make test` — 3235 passed, 1 skipped. Suite growth across the
PRD: 2527 → 3235. One anomalous run is preserved in 0183 (four transient
engine-example failures, unreproducible): a recurrence is the second
occurrence of a real bug, not the first of a fluke.
