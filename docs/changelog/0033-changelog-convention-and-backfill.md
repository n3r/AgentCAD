# 0033 — Changelog convention + backfill of all prior commits

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Establishes a per-commit changelog convention under `docs/changelog/` and
backfills a detailed entry for every one of the 32 pre-existing commits, so the
project's history is documented in more depth than the commit messages alone.

## Changes
- **Rule added** to `AGENTS.md` (new "Changelog — REQUIRED for every commit"
  section + a new item in the definition-of-done) and to `CLAUDE.md` (a
  "Changelog — required every commit" section + DoD item): every commit must
  stage a `docs/changelog/NNNN-<slug>.md` entry, written from the real diff.
- **Convention doc** `docs/changelog/README.md`: filename scheme
  (`NNNN-<slug>.md`, zero-padded sequence = authoritative order), the entry
  template (header with commit/date/author, then Summary / Changes / Files /
  Notes), and the "write from the diff, don't rewrite history" rules.
- **Backfill** `docs/changelog/0001-…` through `0032-…`: one entry per prior
  commit (`c0d6c61` … `dd15c11`), each grounded in `git show` of that commit —
  covering the v1 build (design/plan, kernel, core, server, frontend, agent
  layer, examples, reviews) and the entire v2 arc (scope, spikes, Wave 0–2
  scaffolding and verticals, review fixes, the mesh-shading fix, the STL import,
  and the contributor guides).
- This entry (0033) documents the convention change itself.

## Files
- `AGENTS.md` — changelog rule + DoD item
- `CLAUDE.md` — changelog rule + DoD item
- `docs/changelog/README.md` — convention + template (new)
- `docs/changelog/0001-*.md … 0032-*.md` — backfilled entries (new)
- `docs/changelog/0033-changelog-convention-and-backfill.md` — this entry (new)

## Notes
Docs-only change; no source or tests touched, so the geometry/test surface is
unaffected. The 32 backfilled entries carry each commit's real short hash;
this entry is `pending` because its own hash isn't known until commit time (the
documented behavior for pre-commit entries — the sequence number is the
authoritative ordering).
