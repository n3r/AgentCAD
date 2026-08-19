# 0258 — 2026-08-19 — PRD-006b closed out: all three OSes confine the kernel worker

## Summary

Bookkeeping after PR #24 (Windows AppContainer confinement) merged to main
with every check green on its third CI run. The PRD moves to
`docs/prd/completed/`, PRD-006's header loses its Windows asterisk (AC3 is now
closed on all three OSes), and the branch's changelogs `0240`–`0242` become
`0255`–`0257` because PRD-013 took `0241`–`0254` while the branch was open.

## Changes

- `docs/prd/in-progress/PRD-006b-windows-appcontainer.md` →
  `docs/prd/completed/`, status "completed — merged to main in PR #24"; the
  `in-progress/` directory is empty again and gone.
- `docs/prd/completed/PRD-006-sandboxing-quotas.md`: AC3's Windows clause
  recorded as closed by 006b (Windows reports `active`/`appcontainer` from
  the worker's own token).
- `docs/roadmap.md`: the 006b row links to `prd/completed/` and reads
  completed; the chain's step-5 row notes that all three OSes now confine.
- `docs/changelog/0240-…0242-prd-006b-*.md` → `0255`–`0257` (headings and
  self-references updated; `AGENTS.md`'s one reference too).
- The design spec's PRD link follows the move.

## Notes

What the build proved, in order: the probe-first loop (three rounds of a
dispatch-only `windows-latest` workflow driving the *real* worker) found that
an AppContainer rewrites `TEMP` to `Packages\<name>\AC\Temp`, that inheritable
ACEs propagate to pre-existing children (no `icacls /T`), and that the
PRD-006 Windows meter raised `STATUS_INVALID_HANDLE` inside the container —
all before a line of product code landed; the implementation then passed the
battery on the first PR run except for a POSIX-only `cli._is_path` that had
never granted a `--project C:\…` write root. The whole-branch review added
the salted profile name (the package SID is otherwise derivable by any local
user), the SID match in the honesty chain, and the removal recipe. Codex
remains quota-blocked until 20 Aug; two Opus reviews plus an independent
re-review stood in. `make test` on the merged tree is the PR's evidence
(4387 passed, 42 skipped on the branch; three green CI jobs).
