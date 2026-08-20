# 0301 — PRD-026 closed out: the workbench shell ships

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (with Claude)

## Summary

Bookkeeping after PR #29 (workbench shell revamp) merged to main. The PRD moves
to `docs/prd/completed/` and its roadmap row flips to **completed (PR #29)** —
founder idea #8 is shipped, and the shell primitives every other UI-carrying
PRD (025, 016, 027, 008's threads UI) will compose now exist.

## What shipped (MVP + Phase 2)

- **`frontend/js/shell/`** — the action registry, the shortcut table (conflicts
  throw; the machine path is total), the dialog primitive (focus trap, one Esc
  owner, overlay stack, view registry, agent attribution), toasts, the ⌘K
  palette over the live tool registry with JSON-Schema argument forms, the
  menu bar, the layout manager, the UX-events client.
- **Zero native dialogs** — all 21 `prompt()`/`confirm()` sites are first-party
  dialogs and all nine `.modal-overlay`s (incl. PRD-028's materials browser)
  go through the stack, closed by a test that parses `index.html`.
- **Agent surface** — `ui_open {view, args}` (broadcast, capability-honest,
  rate-limited, `when`-gated, attributed) and member-only
  `POST /api/ui/events`.
- Phase 3 (user shortcut remapping, layout presets, palette frecency) and five
  mid-flow dialogs' agent-openability are recorded as deferred in the PRD.

## Changes

- `docs/prd/in-progress/PRD-026-workbench-shell.md` → `docs/prd/completed/`,
  status "completed — merged in PR #29".
- `docs/roadmap.md`: the 026 row → **completed (PR #29)** with design/plan
  links.

## Notes

Six slices, each with an adversarial task review + scoped re-review; a final
whole-branch review (SHIP after fixes → one fix wave → clean re-review) plus an
independent verifier (21,504-combo shortcut fuzz, live-server contract checks,
XSS probes) and a live Playwright browser pass (12/12, zero page errors). The
Codex seat was quota-blocked this session and is owed as a post-merge
follow-up if the user wants it. CI: the three-OS matrix went green after one
re-run of the Windows job (9 setup errors in the PRD-006b AppContainer suite —
the confined worker crashed at startup on the runner; no kernel or sandbox
file is in the diff, and the re-run passed).

`make test` — **5303 passed, 48 skipped** on the merged tree (docs-only
close-out; the suite is unchanged from PR #29's final count).
