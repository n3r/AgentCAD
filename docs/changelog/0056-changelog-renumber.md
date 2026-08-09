# 0056 — Renumber v3 changelog entries 0034–0053 → 0036–0055

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

The `light-ui` branch (PR #1, merged to main earlier today) used changelog
numbers 0034 and 0035 in parallel with this branch's v3 wave, which had
claimed the same numbers. Since main's sequence is authoritative and light-ui
landed there first, this branch's twenty entries shift by +2 so the merged
history has one strictly increasing sequence.

## Changes

- `git mv` of all twenty v3 entries (0034-v3-roadmap-plan → 0036-…, through
  0053-v3-roadmap-complete → 0055-…); each file's `# NNNN` header updated.
- In-body cross-references updated to the new numbers (per-solid → typed
  PARAMS, multi-agent → turn-locking, surfacing → sheet metal, CI self-path,
  and the close-out entry's plan/range/browser-pass references).
- The v3 plan document's "next: 0034" note updated with the collision
  explanation.

## Files

- `docs/changelog/0036-…` through `docs/changelog/0055-…` (renames + header
  and reference edits)
- `docs/superpowers/plans/2026-08-09-agentcad-v3-roadmap.md`

## Notes

Content of every entry is otherwise untouched — this is a sequence fix, the
one kind of edit the changelog README sanctions for historical records.
