# 0363 — PRD-018 slice 6: Generate panel + candidate gallery (frontend)

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Claude (Sonnet subagent) / Nikita Fedorov

## Summary
The PRD-018 Experience surface: a Generate panel (prompt + attachment
well), a live-converging transcript, a candidate gallery with spec chips
and honest budget-exhausted state, accept-with-provenance-badge.

## Changes
- `frontend/js/generate.js` (new): the panel + gallery, following the
  `dialogs.attachLegacy` + `init(panelApi)` idiom (skills.js/materials.js);
  attachments upload via `api.uploadImport` (bucketed images/files by
  extension) → the dedicated `POST /projects/{p}/generate` route (honest
  `generation_unavailable` 422 when unconfigured).
- Live progress: `main.js` forwards `generation_progress`/`generation_done`
  + tagged `chat_tool_call`/`chat_tool_result` (filtered by
  `generation_id`) into `generate.handleEvent`, rendering per-candidate
  lanes reusing the chat dock's `.tool-chip` shape + a refetched iso
  thumbnail on each `render_view`.
- Gallery on `generation_done`: iso render, mass/bbox, PARAMS table,
  `.spec-chip`s from `spec_report`, failed candidates collapsed, and an
  honest `spec_green:false` "budget exhausted — best so far, N checks
  failing" state (never a fake success). Accept → `accept_candidate` →
  refresh + open the part.
- `inspector.js`: a `GENERATED` provenance badge (model/iterations/
  created/by) from the `generated` key `get_part` now returns (FR11).
- Server fix folded in (found during S6 verification): the
  `POST /projects/{p}/generate` route ran the minutes-long tool
  synchronously in the async handler, blocking the event loop (a
  concurrent `/api/health` took 3.87 s) and starving WS delivery — now
  offloaded to a worker thread under a copied context (the tenant must
  reach the tool — the PRD-005 lesson), so progress events trickle live.
- `tests/test_frontend_shell.py`: `"generate"` added to `ADOPTED_MODALS`
  (the modal-self-registration closure test).

## Files
- `frontend/js/generate.js` (new), `frontend/js/{main,api,inspector}.js`,
  `frontend/index.html`, `frontend/css/app.css`,
  `agentcad/server/routes_generate.py` (event-loop fix),
  `tests/test_frontend_shell.py`

## Notes
Playwright-verified against a fake-model serve via the existing
`tools_generate.CLIENT_FACTORY` seam (no production code added for the
test) — panel, live progress, gallery, accepted part + badge, honest
budget-exhausted state; zero console errors (screenshots in the PR).
`make test` — 7220 passed, 51 skipped (13:15); non-passing were the count-guards (this wave cites the count) and the documented prd028 FEM + supervisor/test_server load timeouts (pass in isolation).
