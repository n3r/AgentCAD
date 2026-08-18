# 0218 — PRD-007 slice 6: docs, acceptance, and the close-out

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
The operator- and contributor-facing docs, the acceptance battery, and the
truthful status for PRD-007 (share links & the customizer), landed in one
change. The design's Decision 8 divergences are folded back into the PRD so it
matches what shipped, and the two browser ACs are graded as evidence (no Chrome
extension available — the PRD-005a AC3 precedent), not claimed as visual passes.

## Changes
- **`docs/deployment.md`** — the two share env knobs (`AGENTCAD_SHARE_MAX_INFLIGHT`,
  `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE`) in the config table; three rows added to
  "What a stranger can reach" (the `/s/`+`/embed/` shells, the four kernel-free
  reads, and the two kernel-reaching customizer routes); the count corrected
  from "nine, zero kernel" to "fifteen templates, thirteen zero-kernel, two the
  deliberate exception"; the **memory residual** stated plainly (peak RSS
  uncapped until PRD-006) with the login-gate backstop and the trusted-proxy
  caveat on the per-IP limit.
- **`docs/user-guide.md`** — a "Sharing a part (a hosted instance)" section:
  the Share dialog, the URL-is-the-capability warning, the embed, watch/revoke,
  and why a visitor cannot touch the owner's work.
- **`docs/agent-api.md`** — a "Share links and the customizer (PRD-007)"
  subsection documenting `share_create` / `share_list` / `share_revoke` and the
  `share_changed` event, with the pin-is-a-copy and GET-is-a-pure-read notes.
- **`AGENTS.md`** — a "Share/customizer gotchas (PRD-007)" section: the
  GET-not-POST rebuild, the trailing-slash prefix rule, the copy-not-reference
  pin, the muzzled build service, the full customizer containment (param parity,
  the buckets, the in-flight semaphore, `request.client.host` not a forged
  `X-Forwarded-For`), the promoted `TokenBucket`, the four `token` senses, and
  the `NOT_YET_BUILT == set()` surface-growth discipline.
- **`docs/prd/in-progress/PRD-007-…`** — Status → **implemented**; the
  depends-on rewritten to the as-built chain (005a/001/011/004/012, 006 not
  required); the design divergences folded in as "what shipped"; new
  **Verification levels** and **Residual gaps** tables in the 005a mould (the
  peak-memory residual named, the browser ACs graded as evidence).
- **`docs/roadmap.md`** — the PRD-007 index row: status → implemented, and the
  link fixed from `prd/pending/…` to `prd/in-progress/…` (it had drifted).
- **`tests/test_prd007_acceptance.py`** — new: `_find_prd()` (the 0164 move
  trap), one test per AC1–AC9, the anonymous-surface equality AC including the
  two customizer routes, a "viewer reaches zero kernel" AC with a positive
  control, a "customizer is bounded and param-validated" AC with the in-flight
  positive control, and the PRD/roadmap/AGENTS record checks.

## Files
- `docs/deployment.md` — share env vars + stranger-reach rows + memory residual
- `docs/user-guide.md` — "Sharing a part" section
- `docs/agent-api.md` — the `share_*` tools + `share_changed`
- `AGENTS.md` — "Share/customizer gotchas (PRD-007)"
- `docs/prd/in-progress/PRD-007-share-links-customizer.md` — status, divergences,
  verification + residual tables
- `docs/roadmap.md` — the index row + fixed link
- `tests/test_prd007_acceptance.py` — new: the acceptance battery

## Notes
Verification: `pytest tests/test_prd007_acceptance.py` → **17 passed**
(2026-08-18). Full suite: `make test` (`make test-full`, `-n auto`) → **3994
passed / 1 skipped** in 558 s (2026-08-18) — the count measured on this branch
at close-out. Prior recorded baselines: 3689 passed / 1 skipped (changelog 0199,
pre-PRD-012-merge) and the slice-3 subset measurements in 0215. The single skip
is the FEM suite's `importorskip` without the `[fem]` extra, unchanged.

**Browser ACs (AC1, AC7) graded as evidence, not driven.**
`mcp__claude-in-chrome__list_connected_browsers` → `[]`, so the viewer page, a
slider drag, a download and the embedded iframe were never rendered by a
browser. The HTTP contracts, the served HTML's shape, the `kernel_counter`
deltas, the response headers and the JS parsing are what is graded; the visual
pass is the first thing a reviewer with a browser should close.
