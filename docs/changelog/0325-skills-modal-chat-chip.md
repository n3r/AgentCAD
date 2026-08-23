# 0325 — PRD-029 slice 6: the Skills modal and the chat's skill chips

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
The human path of PRD-029 (FR7's provenance labels and first-load consent,
AC1's chip, AC3's badge) on the PRD-026 shell: a Skills modal listing the
project's effective index with provenance badges, enable toggles, a preview
and the trust flow, and a `📘 name · layer` chip in the chat dock for every
skill the built-in agent loads.

## Changes
- `frontend/js/skills_model.js` (pure, `__skillsModel__`): `badgeFor`
  (`core` / `project` / `overrides core` / `needs review` / `changed since
  trusted` / `invalid`), `sortRows`, `needsConsent`, `chipLabel`,
  `isChatClient` (`^chat(:[a-z0-9_-]{1,32})?$`), `formatAssets`, and the
  provenance/truncation formatters.
- `frontend/js/skills.js`: `init`/`open`/`refresh`/`close`; one `GET
  /api/projects/{p}/skills` per open; rows with name, version, badges, an
  enable checkbox (`PATCH …/enabled`), click-to-preview (`GET
  …/skills/{name}` → `content` in a `<pre>`, assets, provenance, a
  "truncated — N sections omitted" note); "Review & trust" → `POST …/trust`
  (and "Untrust"); a consent banner while any project skill is untrusted;
  the footer "Teach: save <project>/skills/<name>.md"; every string via
  `textContent`. Registered as view `skills` through
  `dialogs.attachLegacy` with action `agent.skills` ("Skills…", group
  Agent), the `#skills-btn` toolbar button and the `#skills` hash.
- `frontend/js/chat.js`: `skill_loaded` draws a `.skill-chip` when the
  event's `client` is the chat's own identity (so the modal's preview, which
  also goes through `load_skill`, draws none); `skill_unloaded` marks the
  chip `.unloaded`.
- `frontend/js/main.js`: the WS dispatcher forwards the two skill events to
  the chat and `skills_changed` to an open modal; `frontend/index.html` and
  `frontend/css/app.css` carry the markup and styles.
- `tests/test_frontend_skills.py` (47): node harness over every model
  function plus live contract tests (index keys, `browser:` trust 200,
  `mcp` trust 403); `tests/test_frontend_shell.py` registers the overlay in
  `ADOPTED_MODALS`.

## Files
- `frontend/js/skills.js`, `frontend/js/skills_model.js`, `tests/test_frontend_skills.py` — new
- `frontend/js/main.js`, `frontend/js/chat.js`, `frontend/index.html`, `frontend/css/app.css`, `tests/test_frontend_shell.py`

## Notes
Verified in Chrome (Playwright, `channel: "chrome"`) against `agentcad
serve` on a scratch projects dir: 16 core rows with `core` badges, a project
skill shows `needs review` and becomes `project` after "Review & trust", a
`load_skill` with `X-Agent-Id: chat` renders one chip and the same call as
`browser:test` renders none; zero page errors. `skills.js` issues its four
requests through a local helper rather than new `api.js` methods — a clean
follow-up. `make test` on the combined tree of slices 1–4 and 6 —
5646 passed, 51 skipped, 12 failed, 1 error in 852 s (nine are the
changelog count-guard tests reading this entry's own not-yet-filled count,
`test_prd028_acceptance::test_ac6_real_solver` is the known local `[fem]`
timeout that skips on CI, and `test_supervisor`'s memory-cap kill plus
`test_server::test_project_and_part_flow` were timeouts under the load of
concurrent slice agents — both re-run green in isolation, 2 passed in 13 s).
