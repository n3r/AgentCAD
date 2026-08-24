# 0351 — PRD-005 slice 8: frontend — workspace switcher, members/tokens panels, role affordances

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (Sonnet subagent) / Nikita Fedorov

## Summary
The tenancy Experience surface: an org/workspace switcher feeding
`X-Agentcad-Workspace` on every request (+ `?workspace=` on the WS), org
members and agent-token dialogs over the `tools_cloud` surface, identity
chips rendering real principals, and role-gated edit affordances.

## Changes
- `auth.session()` layers one `whoami` call over the 005a session so
  `boot()`'s single identity object carries orgs/roles/scope; untenanted
  instances see the byte-identical 005a shape and render nothing new.
- Switcher persists in localStorage; `api.js` spreads the header at the
  six literal identity-header call sites (kept literal — `test_presence`
  pins the header count). WS carries `?workspace=` per
  `security._ws_headers`.
- Members + tokens dialogs (`cloud.js`, shell `dialogs.register`):
  grant/revoke via tools with verbatim `permission_error` toasts; token
  mint shows the secret once with copy + "won't be shown again";
  admin-gated affordances. A found-during-verification bug fixed:
  `cloud.js.init()` reset the identity after `setIdentity` (both panels
  silently dead).
- `state.canEdit` from `whoami.roles` vs the ladder (open when
  untenanted): gates undo/redo, part delete, branch create/delete in the
  action registry; parameter inputs + script editor go disabled/readOnly
  for viewers (two shortcut predicates are pinned by tests, so those
  gate below the registry); comments/proposals stay enabled.
- Verified with Playwright against a real hosted serve: admin + viewer
  contexts, grant flow, token mint, viewer affordances (screenshots in
  the session scratchpad, referenced in the PR).

## Files
- `frontend/js/workspace.js`, `frontend/js/cloud.js` — new
- `frontend/js/{api,auth,main,inspector,editor,state,presence,versions,releases,proposals,comments}.js`,
  `frontend/js/shell/actions.js`, `frontend/css/app.css`,
  `frontend/index.html` — extended

## Notes
Role-gating covers the named surfaces; bulk ops/drag-transforms/tag
editing can opt into `context().canEdit` later (recorded). Panels
grant/mint by typed project ids — no per-project role listing exists on
the tool surface yet.
`make test` — 7002 passed, 51 skipped (12:41); non-passing were the documented families only: the pre-existing prd028 AC6 local solver timeout (skips on CI) and supervisor/share_isolation load flakes — 30/30 green in isolation.
