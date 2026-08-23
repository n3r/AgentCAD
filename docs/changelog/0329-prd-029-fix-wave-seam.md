# 0329 — PRD-029 fix wave (seam side): the header-less trust bypass, the review read, asset accounting, the lane filter

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary

The other half of the PRD-029 fix wave (0328 is the library half). Three
independent reviews — an Opus code review, an adversarial verifier with probes,
and Codex xhigh — found six defects in the surfaces *over* `core/skills.py`: the
trust route's human gate, the preview route, the two agent tools, the chat
engine's budget bookkeeping, the dock's chip, and the docs. Every fix landed
test-first; the failing test is named beside each change.

## Changes

- **An agent could approve its own instructions by dropping a header.**
  `server/app.py` turns a request with no `X-Agent-Id` into the bare client id
  `"browser"`, and `proposals.actor_kind("browser")` is `"human"` — so
  `POST …/skills/{name}/trust` with no header was a 200. `_require_human` now
  demands a human `actor_kind` **and** an explicit principal:
  `browser:<non-empty>` (what `frontend/js/api.js` mints and stores) or
  `user:<non-empty>` (a hosted principal, bare or composed as
  `user:x/browser:y`). `browser`, `chat`, `chat:<s>`, `mcp`, `agent:*` and
  `local` are refused, and the gate still runs before the name check so a
  non-human learns nothing about which skills exist.
  `tests/test_skills_routes.py::test_a_request_with_no_agent_id_header_cannot_trust_a_skill`,
  `…::test_an_explicit_user_principal_is_a_human_here_too`.
- **A human can now read an untrusted skill — which is what reviewing is.**
  The preview route delegated to the trust-enforcing tool, so the panel refused
  to show the text it was asking a person to approve. `GET
  /projects/{p}/skills/{name}` from a human client reads
  `service.skills.load(..., enforce_trust=False)` directly: no registry call,
  no `skill_loaded` event, no engine bookkeeping — a person reading a file is
  not an agent loading instructions. Every other refusal (disabled, invalid,
  capability, unknown) is unchanged, and the same URL from an agent still goes
  through `load_skill` and is still refused with `skill_untrusted`.
  `tests/test_skills_routes.py::test_a_human_reads_an_untrusted_skill_to_review_it_and_logs_nothing`,
  `…::test_the_review_read_still_refuses_a_disabled_or_unknown_skill`.
- **An unreviewed project skill's description no longer reaches a model.**
  `list_skills` reads the index — and the ranked search — with
  `redact_untrusted=True` (0328's flag). The skill is still listed with its
  name, layer and `trusted: false`; its `description` and `triggers` are not.
  `GET /projects/{p}/skills` reads `service.skills.index(proj)` *unredacted*,
  because the panel is the human surface, and it now takes its own 404 from
  `store.path_of` instead of the tool's.
  `tests/test_skills_tools.py::test_an_unreviewed_project_skills_description_never_reaches_an_agent`,
  `…::test_a_query_cannot_pull_an_unreviewed_description_out_either`,
  `tests/test_skills_chat.py::test_an_unreviewed_project_skill_cannot_speak_in_the_system_prompt`,
  `tests/test_skills_routes.py::test_the_index_route_is_the_human_surface_and_redacts_nothing`.
- **The budget counted the wrong number.** The engine recorded
  `result["chars"]` (the skill's text) while the transcript holds the whole
  serialized `tool_result` — `provenance`, `assets` and above all
  `omitted_sections`, which a probe measured at 768 kB for one truncated skill.
  The cost is now the length of what the transcript actually holds.
  `tests/test_skills_chat.py::test_the_cost_counted_is_the_whole_tool_result_the_transcript_holds`.
- **An asset read is a budget entry.** It was neither counted nor evictable, so
  `load_skill {asset: …}` sat in the transcript forever while the engine
  reported nothing loaded. It is now its own entry keyed `"{name}#{asset}"`,
  with its own `tool_use_id`, cost and eviction; the stub names the file. It
  stays out of the "Loaded this session:" line — one file out of a guide is not
  that guide. `loaded_skills()` returns `{name, layer, chars, asset?}`.
  `tests/test_skills_chat.py::test_an_asset_read_costs_context_and_is_recorded_as_its_own_entry`,
  `…::test_an_asset_is_evicted_like_a_skill_and_its_stub_names_the_file`,
  `…::test_the_body_and_its_asset_are_two_entries_not_one`.
- **A re-load left the previous copy in the transcript.** Only the newest
  `tool_use_id` is remembered, so a later eviction stubbed only the newest and
  the older full copy was unreachable. A re-load now rewrites the copy it
  supersedes to `RELOAD_STUB`, silently — the skill *is* loaded, by the block
  below, so publishing `skill_unloaded` would strike a live chip.
  `tests/test_skills_chat.py::test_reloading_the_same_skill_stubs_the_PREVIOUS_copy`.
- **A chip from another chat lane rendered in the dock and could never be
  un-struck.** `skill_loaded` carried no `session`, so `chat.js` defaulted it
  to the dock's lane and drew a chip; the matching `skill_unloaded` *does*
  carry a session and was filtered out. The tool now publishes `session`
  (`tools_skills.chat_session`: `chat` → `"main"`, `chat:<s>` → `"<s>"`, else
  `null`) and `asset`, `skills_model.sessionOf` mirrors the derivation in the
  browser, and the chip filters on both. Chips key on `data-skill` *and*
  `data-asset`, so evicting a snippet no longer strikes the guide's chip.
  `tests/test_skills_tools.py::test_the_event_names_the_chat_lane_that_loaded_the_skill`,
  `tests/test_frontend_skills.py::test_session_of_*`.
- **A single skill larger than `max_loaded_chars` stayed loaded above the
  bound.** The engine never evicts the load it is answering (it would answer a
  load with an unload, and loop), so the only thing that can bound one skill is
  the truncation cap — which 0328 now normalizes down to `max_loaded_chars`.
  `tests/test_skills_chat.py::test_a_capped_skill_fits_the_session_budget_and_is_never_self_evicted`
  pins both halves.
- **Tests that graded text instead of behaviour.** AC5 used to `read_text()`
  `tests/test_bench_skills.py` and grep for `"--skills"` — a claim about source
  that passes against a commented-out test. It now runs the scenario: two real
  `agentcad bench run`s (`--skills none`, `--skills snap-fits`) and a real
  `bench report --baseline`, offline through `runner.CLIENT_FACTORY`, ~16 s.
  AC2 gained the sentence-shaped queries an agent actually sends.
- **Docs.** The tool count was three releases stale: **85 → 106** (109 with the
  optional `[fem]` extra), measured with `len(build_registry(service).list())`,
  in `README.md`, `docs/architecture.md`, `docs/user-guide.md` and
  `docs/agent-api.md`. `docs/skills.md`, `docs/agent-api.md`,
  `docs/user-guide.md` and the design spec were updated for the token-set
  ranking, the tree digest, redaction, the explicit principal, the review read,
  asset accounting and the re-load stub. Two drifted claims fixed: the
  cheat-sheet's **selectors** section is *copied* into
  `selectors-and-occt-failures`, not deleted (it is the generic minimum for
  writing any script — `tests/test_part_template_compat.py` pins both halves),
  and the authored-skill ceiling is **≤ 12 700 chars**, not 12 000
  (`fem-workflow` is 12 655).

## Files

- `agentcad/agent/chat.py` — `ASSET_UNLOAD_STUB`/`RELOAD_STUB`, `_unload_stub`,
  `_reload_stub`, `loaded_skills`, `_system_prompt`, `_record_skill`, `_evict`,
  `_unload_in_history`, the tool loop's cost/asset capture
- `agentcad/core/tools_skills.py` — `chat_session`, redaction on both
  `list_skills` paths, `session`/`asset` on `skill_loaded`
- `agentcad/server/routes_skills.py` — `_is_human`, `_require_human`, the
  unredacted index read, the human review read
- `frontend/js/skills_model.js` — `sessionOf`, `chipLabel` for an asset
- `frontend/js/chat.js` — the lane filter, `data-asset`, `markSkillUnloaded`
- `frontend/js/skills.js` — the untrusted preview banner + Trust button
- `tests/test_skills_chat.py`, `tests/test_skills_tools.py`,
  `tests/test_skills_routes.py`, `tests/test_frontend_skills.py`,
  `tests/test_prd029_acceptance.py`
- `README.md`, `docs/architecture.md`, `docs/user-guide.md`,
  `docs/agent-api.md`, `docs/skills.md`,
  `docs/superpowers/specs/2026-08-23-agent-skills-design.md`

## Notes

- **The review read publishes nothing on purpose.** Restoring an audit event
  "for symmetry" would put a human's reading in the same stream the chip and
  the engine's bookkeeping read as agent loads.
- **Two visible behaviour changes.** A browser preview no longer emits
  `skill_loaded` (a header-less `curl` still does, because it is not a named
  human), and the trust routes now refuse a header-less request. Both are the
  point of the fix, not side effects.
- The re-review of this wave (static, Opus) verified all sixteen fixes and
  added three follow-ups landed in the same commit: an unreviewed project
  skill's `invalid`/`problem` text (it quotes the offending source line) is
  withheld like its description, on `list_skills` and on `resolve`'s
  `skill_invalid` refusal, which fires before `load`'s trust check
  (`UNREVIEWED_PROBLEM`, `test_an_unreviewed_broken_skill_leaks_nothing_through_its_error`);
  `SkillBudget` clamps the content cap to `ENVELOPE_SHARE` (0.8) × the session
  cap so the serialized envelope of one capped skill always fits;
  `routes_skills._is_human` states out loud that in local mode the gate is a
  consent gate, not a security boundary (`X-Agent-Id` is unvalidated there).
- The suite: `make test` on the final tree, quiet machine — 5967 passed, 66 skipped, 4 failed in 765 s on the tree merged with
`origin/main` (PRD-017 landed underneath; the merge kept both trap blocks in
`AGENTS.md`/`CLAUDE.md` and ported main's one cheat-sheet edit —
`check_clearance(a, b, min_mm, max_mm=)` — into the `design-specs` skill).
The four: the known local `[fem]` real-solver timeout that skips on CI, and
three 120 s kernel-wait timeouts under the suite's own load
(`test_supervisor` ×2, `test_share_viewer`) that re-run green in isolation
(3 passed, 28 s). The
  pre-final run of the same tree minus the three follow-ups was 5739 passed,
  51 skipped, 14 failed in 781 s: eleven were the count guards reading this
  entry's own placeholder, one the known local `[fem]` real-solver timeout
  that skips on CI, and two timing tests (`test_authstore` constant-time
  compare, `test_share_frontend`) that re-run green in isolation (2 passed).
- The seam-side suites this entry touches — `tests/test_skills_chat.py
  tests/test_skills_tools.py tests/test_skills_routes.py
  tests/test_frontend_skills.py tests/test_prd029_acceptance.py
  tests/test_chat.py tests/test_hosted_surface.py
  tests/test_prd005a_acceptance.py tests/test_frontend_shell.py` — green
  (434 cases, 39 s), with the two guards above counted as green only because
  this entry is the newest one they read.
