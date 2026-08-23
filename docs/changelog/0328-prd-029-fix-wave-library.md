# 0328 — PRD-029 fix wave (library side): redaction, token-set search, the tree digest, capped reads, serialized trust

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary

Three independent reviews of PRD-029 (an Opus code review, an adversarial
verifier with probes, and Codex xhigh) found eleven defects in the skills
library. This entry is the library half: `core/skills.py`, `core/skills_lint.py`
and the two lines of budget plumbing in `core/service.py` and `bench/cli.py`.
Every fix landed test-first — the failing test is named beside each change
below.

## Changes

- **Untrusted project metadata no longer reaches a model verbatim.**
  `SkillLibrary.index(project, *, redact_untrusted=False)` and
  `search(query, project, *, redact_untrusted=False)` replace an unreviewed
  project entry's `description` with `UNREVIEWED_DESCRIPTION` and its
  `triggers` with `[]`; `compact_index` (the system-prompt block) renders such
  an entry as `- name (unreviewed project skill — not loadable until a human
  approves it in the Skills panel)`. The default is False because the human
  surfaces need the real metadata to review at all. Redaction copies the
  entries — the raw view is never mutated.
- **`load(..., enforce_trust=False)`** skips *only* the trust check, for the
  human review read behind `GET /projects/{p}/skills/{name}`: a person cannot
  approve what they may not read. Disabled/invalid/capability refusals are
  unchanged.
- **`asset` may no longer name the skill's own file.** `asset="SKILL.md"`
  returned the whole body with no section truncation and no `truncated` flag —
  an eight-character bypass of the one cap on what a skill costs a context. It
  is now a `NotFoundError` (`skill_not_found`, `details.asset`). An asset
  outside the bounded `walk_files` listing is refused for the same reason the
  digest covers only that listing.
- **Search ranks token sets, not substrings.** Query tokens are `[a-z0-9]+` of
  three characters or more (all-short queries keep their tokens, so `m8` still
  searches); the name scores 100 exact, else 60 for a token equal to a
  hyphen-part or a ≥4-char token inside the name; 40 per trigger whose token
  set meets the query's; 10 per shared description token. Measured on the
  shipped library, `"make a snap fit lid"` scored `robust-parametrics` **550**
  and `snap-fits` 360 before (the token `"a"` is inside eleven of its
  triggers); it now scores 200 and **0**.
- **Trust is keyed by the TREE digest.** `tree_digest` hashes
  `relpath + "\0" + sha256(bytes)` for every file in `walk_files` order — the
  SKILL.md plus every asset — so rewriting `snippets/x.py` after approval, or
  adding/removing a file, revokes trust. `SkillRecord.digest` became a
  `cached_property` so a core skill (trusted by construction) never pays for
  it: `index()` over the shipped library stays **1.6 ms/call** instead of
  5.2 ms.
- **Symlinks are never followed.** `scan_layer` skips a symlinked entry (dir or
  flat file) and anything whose `resolve()` leaves the resolved layer root — a
  skill directory linked outside the project was indexed and its *neighbours*
  were readable as assets, because the asset guard compares against
  `record.dir`. `walk_files` now skips symlinked files too, not just
  directories.
- **Every read is size-checked before it happens.** `_read_capped` stats
  first and then reads at most `limit + 1` bytes; a 600 MB sparse `SKILL.md`
  cost 630 MB of RSS per index call. It backs `read_record`, asset reads,
  `trust.json` and both lint readers. An oversize file is `invalid` (empty
  state for `trust.json`, a `skill_invalid` refusal for an asset).
- **The three trust writers are serialized per project.** `_trust_scope` is a
  `threading.RLock` plus an advisory `fcntl.flock` on `trust.lock` beside
  `trust.json` — `packages.LocalIndex._index_scope`'s shape, without its
  "outside the index" rule, since `.history/agentcad/skills/` is ours. Forty
  concurrent `trust` calls used to leave one entry in the file. The index also
  reads the trust state **once per call** and passes it down instead of
  re-reading `trust.json` per record, and `_entry` reports
  `enabled: name not in disabled` (it was hardcoded `True`).
- **`omitted_sections` is capped** at `MAX_OMITTED_SECTIONS` (40) plus a final
  `…and N more sections`; 6 000 headings turned a capped load into a 768 kB
  tool result. `chars` is still `len(content)`.
- **`SkillBudget` normalizes on construction**: `max_skill_chars` is clamped to
  `ENVELOPE_SHARE` (0.8) × `max_loaded_chars` — the engine books the whole
  serialized result, which runs up to ~15 % over the content — so a single
  capped skill always fits the session budget (the re-review tightened this
  from "clamped to `max_loaded_chars`", which left one just-loaded skill
  above the bound when the two caps were equal). The defaults are untouched.
  `AgentCADService` now builds `SkillLibrary(self.store,
  budget=SkillBudget.from_config())` — `max_skill_chars` was dead config — and
  the bench's `_install_skills` derives its library's budget the same way
  `run_task` derives the engine's.
- **Lint:** an unterminated fence at EOF is a `code_fence_unterminated` error
  and a ```python one is still `ast.parse`d (the old loop only parsed on the
  closing marker, so the end of a file was where a broken snippet was certain
  to hide).

## Files

- `agentcad/core/skills.py` — `UNREVIEWED_DESCRIPTION`/`UNREVIEWED_COMPACT`/
  `MAX_OMITTED_SECTIONS`/`MIN_TOKEN_CHARS`, `SkillTooLarge`, `_read_capped`,
  `_file_digest`, `tree_digest`, `_query_tokens`, `_redact`, `_cap_omitted`,
  `_trust_scope`, and the changes to `walk_files`, `scan_layer`,
  `SkillRecord`, `SkillBudget`, `_entry`, `index`, `search`, `compact_index`,
  `load`, `_read_asset`, `trust_state`, `is_trusted`, `trust`, `untrust`,
  `set_enabled`
- `agentcad/core/skills_lint.py` — `_read_text` goes through `_read_capped`;
  `_lint_fences` reports and parses an unterminated fence
- `agentcad/core/service.py` — the library is built with
  `SkillBudget.from_config()`
- `agentcad/bench/cli.py` — `_install_skills` passes the same budget
- `tests/test_skills_library.py`, `tests/test_skills_lint.py` — a failing test
  per defect, written first (redaction, the review read, the two asset
  refusals, the shipped-library ranking, one-letter tokens, the tree digest,
  three symlink cases, three capped-read cases, 40-thread `trust` and
  `set_enabled`, the enabled flag, the single state read, the omitted cap, the
  budget normalization and the service's budget)

## Notes

- **Two behaviour changes worth naming.** An asset naming `SKILL.md` used to
  succeed and now refuses; an asset over the ceiling used to be silently
  truncated to 1 MB and now refuses with `skill_invalid`. Both were bypasses of
  a stated guarantee, so failing closed is the fix, not a regression.
- **The digest of an oversize file is a size marker, not a hash.** Hashing it
  would be the exact allocation `_read_capped` exists to refuse. Two oversize
  files of equal length are therefore indistinguishable to the digest — and
  neither can be served (the SKILL.md is `invalid`, the asset is refused), so
  no bytes reach an agent either way.
- `provenance.digest` is now the tree digest. Any approval recorded before this
  change no longer matches and reads as untrusted — which is the safe
  direction, and re-approving rebuilds it.
- **Verification.** `uv run pytest -q tests/test_skills_library.py
  tests/test_skills_lint.py tests/test_skills_cli.py
  tests/test_skills_core_library.py tests/test_bench_skills.py
  tests/test_part_template_compat.py tests/test_skills_tools.py
  tests/test_skills_routes.py tests/test_skills_chat.py` — **250 passed, 11
  skipped** (the last three are 0329's, run here to prove the seam);
  `.venv/bin/agentcad skill lint --core` — 0 errors, 0 warnings. `make test`
  over the whole branch is cited in **0329**, which lands with this one.
