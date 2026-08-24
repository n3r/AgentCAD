# 0353 — PRD-005 slice 10: acceptance tests, final wirings, docs

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (Opus + Sonnet subagents) / Nikita Fedorov

## Summary
The close-out slice: AC1–AC8 machine/evidence halves (24 tests), the two
flagged wirings (the general audit tap onto the serve-path registry; an
`agentcad admin org` CLI replacing the smoke's python one-liner), and the
documentation set (agent-api, user-guide, architecture, AGENTS.md/
CLAUDE.md trap lists).

## Changes
- `tenancy_wiring._install_registry` now composes `audit.tap_registry`
  **outermost** (a refused call is recorded with
  `outcome: "permission_error"`); the log is reached lazily per row via
  the config's auth-store root, so local mode constructs no AuditLog and
  writes nothing (tested both ways; double-install writes one row).
- `agentcad admin org add | org workspace add | org member add` (no
  kernel, `_auth_store`-style construction); deploy-smoke's bootstrap
  uses it.
- `tests/test_prd005_acceptance.py` (24): AC1 two-user live-WS
  attribution; AC2 both honest shapes (name-free 404 for a non-member
  person — the design's non-oracle — and permission_error for an
  addressable-but-roleless token, then the view→edit flip); AC3 real
  clone/offline-build/divergence-conflict round trip; AC5 scoped token +
  next-request revocation; AC6 user/chat/bearer distinguished through
  the **general** tap; AC7 local-mode probes + the count guard; AC4/AC8
  structural workflow-evidence asserts (runtime evidence: the post-merge
  smoke run and a certs-provisioned release run).
- Docs: agent-api (tools table, error family incl. the exact
  `kernelbusy_error` derivation, floors, header/token precedence, /git
  surface), user-guide (sign-in, switcher, panels, offline walkthrough
  with verbatim hook refusals), architecture ("Multi-tenant cloud"
  section; a stale trust-model residual fixed), AGENTS.md 14-bullet
  gotcha section + CLAUDE.md bullet; deployment.md's tap paragraph
  updated (was written a slice earlier and went stale the moment the
  wiring landed); roadmap row → in-progress (the folder/index same-commit
  rule, missed at the spec commit).

## Files
- `tests/test_prd005_acceptance.py` — new
- `agentcad/core/tenancy_wiring.py`, `agentcad/cli.py`,
  `.github/workflows/deploy-smoke.yml` — wirings
- `docs/agent-api.md`, `docs/user-guide.md`, `docs/architecture.md`,
  `AGENTS.md`, `CLAUDE.md`, `docs/deployment.md`, `docs/roadmap.md` — docs

## Notes
Known, deliberately-unreconciled residual for the review wave: the four
`tools_cloud` admin tools write a second pack-local audit row beside the
general tap's (documented in AGENTS.md/architecture.md; the fix wave
decides which side yields). `make test` on this tree — 7022 passed, 51
skipped (18:31, while the slice's own 374-test run raced in the same
window); non-passing were the documented families (prd028 AC6
pre-existing; supervisor/sheetmetal load timeouts — 22/22 green in
isolation).
