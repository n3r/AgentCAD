# 0323 — PRD-029 slice 2: `list_skills`/`load_skill`, the chat seam with a budgeted loaded set, skill routes

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
Skills reach every agent surface (PRD-029 FR3, FR4, FR5, FR7's gating):
one tool pack registers `list_skills` and `load_skill` for chat, MCP and
HTTP alike; the built-in chat engine carries the compact skill index in its
system context, keeps a per-session loaded set under the configured budget,
and evicts LRU by rewriting the evicted `tool_result` in the transcript; a
route pack serves the browser's index/preview and the three human-only trust
writes.

## Changes
- `core/tools_skills.py` (loads at `sk`; reads `service.skills` inside
  handlers only; no gate provider): `list_skills {project?, query?}` →
  `{skills, matched, hidden}`; `load_skill {project?, name, asset?}` → the
  library's `load` payload. A given `project` must exist (`NotFoundError`
  otherwise — a missing `skills/` dir is an empty layer, so without the
  check a typo answered with the core list). The handler publishes
  `skill_loaded {project, name, layer, chars, client}` with
  `locks.current_client_id()` — so chat, MCP and HTTP all log the same
  way, and the engine publishes no second event.
- `agent/chat.py`: `ChatEngine(..., skills=None, budget=None)`;
  `SKILLS_RULE` (the one paragraph that introduces the index and fences
  skill text as data); `_system_prompt(project, session)` = `SYSTEM_PROMPT`
  byte-for-byte when there is no library or an empty index, else
  `SYSTEM_PROMPT + SKILLS_RULE + compact index + "Loaded this session: …"`
  (an `AppError` while indexing falls back to core-only, then to the bare
  prompt); `loaded_skills()`; after a successful `load_skill` (not an
  `asset` read) the skill is recorded in `_skills_loaded[(project,
  session)]` (MRU on re-load) and LRU entries are evicted while `len >
  max_loaded` or `Σ chars > max_loaded_chars` — never the one just loaded —
  each eviction replacing that skill's `tool_result` content with
  `UNLOAD_STUB` and publishing `skill_unloaded {project, session, name,
  reason: "budget"}`; `clear_history` drops the set. The two
  `part_template` sentences of the working rules now say: call
  `part_template` for the contract, then `load_skill` for the matching
  craft guide.
- `cli.py::_make_chat_engine` passes `skills=service.skills,
  budget=SkillBudget.from_config()`.
- `server/routes_skills.py` (`/api`): `GET /projects/{p}/skills` →
  `{skills, hidden, trust}`; `GET /projects/{p}/skills/{name}[?asset=]`
  through `registry.call("load_skill")` (so a browser preview logs like
  every other read); `POST …/{name}/trust`, `POST …/{name}/untrust`, `PATCH
  …/{name}/enabled {"enabled": bool}` — each refused with 403 unless
  `actor_kind(client) == "human"`, each publishing `skills_changed
  {project}` and returning the updated index entry. `{name}` is
  `NAME_RE`-gated before it reaches the library. All five are member-only in
  hosted mode (nothing anonymous added; the equality test is untouched).
- Tests: `test_skills_tools.py` (10), `test_skills_chat.py` (13 — scripted
  client: index in the first request's `system`, "Loaded this session" after
  a load, count and chars eviction with the stub rewrite and the event,
  re-load evicts nothing, failed load records nothing, `skills=None` →
  byte-identical prompt), `test_skills_routes.py` (12 — incl. `browser:x`
  trusts at 200, `mcp` is 403, anonymous hosted 401s).

## Files
- `agentcad/core/tools_skills.py`, `agentcad/server/routes_skills.py` — new
- `agentcad/agent/chat.py`, `agentcad/cli.py` — the seam
- `tests/test_skills_tools.py`, `tests/test_skills_chat.py`, `tests/test_skills_routes.py` — new

## Notes
Bookkeeping runs after `history.append(results)` so a batch that loads more
skills than the budget holds can rewrite a `tool_result` it just added. A
re-load keeps only the newest `tool_use_id`, so a later eviction rewrites
only that block — an earlier duplicate copy of the same skill stays in the
transcript (plan-faithful; noted for the review). `skill_loaded` is also
published for asset reads (content reached an agent) while the engine's
bookkeeping skips them. `make test` on the combined tree of slices 1–4 —
5646 passed, 51 skipped, 12 failed, 1 error in 852 s (nine are the
changelog count-guard tests reading this entry's own not-yet-filled count,
`test_prd028_acceptance::test_ac6_real_solver` is the known local `[fem]`
timeout that skips on CI, and `test_supervisor`'s memory-cap kill plus
`test_server::test_project_and_part_flow` were timeouts under the load of
concurrent slice agents — both re-run green in isolation, 2 passed in 13 s).
