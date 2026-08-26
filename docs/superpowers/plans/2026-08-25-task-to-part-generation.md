# PRD-018 Task-to-part generation — implementation plan

Design: `docs/superpowers/specs/2026-08-25-task-to-part-generation-design.md`.
Spike: `docs/superpowers/specs/2026-08-25-task-to-part-generation-spike.md`.
TDD per slice; the controller (not subagents) runs `make test` and commits,
one counted changelog per commit. Subagents never run mutating git, `uv
sync`, or the full suite. Before each controller full run: sweep stray
pytest + `git clean -fdX examples/`. The loop is tested with the proven
FakeMessages harness against the real kernel — no network in CI.

## Wave 1 (parallel — disjoint files)

### Slice 1 — the generation loop + budget/termination (FR3–FR5, core of FR2) — **Opus**
- `agentcad/agent/generate.py`: the `GenerationLoop` reusing chat's seams
  (`client_factory`, `_block_to_dict`, `_render_tool_result`, the
  `_call_tool` tenancy-capture pattern), restricted tool list, mechanical
  look-and-measure enforced in code, the budget/termination state machine
  (spec_green / budget_exhausted / abandoned), best-so-far selection,
  per-candidate iteration log, `gen:<id>` identity, scratch part ids.
- Tests (`tests/test_generation_loop.py`, FakeMessages + real kernel):
  the three exits (AC4), budget=1 best-so-far (AC3 half), mechanical
  render+measure happen every iteration, abandonment preserves the
  structured error, no live orphan after a non-accept.

### Slice 2 — intent normalization + frozen specs + grounding (FR2/FR8/FR10) — **Opus**
- `agentcad/agent/intent.py`: intent record derivation, draft SPECS
  generation over the PRD-003 `check_*` vocabulary, the freeze set + the
  terminate-time frozen-spec diff (a weakened/deleted frozen spec fails),
  standards grounding (`SkillLibrary.load(name, asset)` → `json.loads` →
  intent + PARAMS + spec; cite `{pack,table,row}`), the "never invent a
  standard dimension" rule text.
- Tests (`tests/test_generation_intent.py`): NEMA-17 numbers come from
  `nema.json` not the model (AC2 data half), frozen-spec-weakening rejected
  (AC6), draft SPECS cover every stated constraint, an ungrounded standard
  prompt asks/cites rather than inventing.

### Slice 3 — intake: images + PDF via pypdfium2 (FR1) — **Opus**
- `pyproject.toml`: `pdf = ["pypdfium2>=4"]` extra (controller runs
  `uv lock`/`uv sync --extra pdf ...` at landing). `core/imports.py`:
  `.png/.jpg/.jpeg` + (extra-gated) `.pdf` in `SUPPORTED_EXTS`.
  `core/intake.py`: `prepare_vision` (image passthrough + validation; PDF
  → pypdfium2 rasterize (bounded pages) via `core/render.py`'s
  `encode_png`, no Pillow; native text extract, bounded) — the
  untrusted-document fencing (text is reference data, never instructions);
  lazy pypdfium2 import + `[pdf]`-extra validation_error when absent.
- Tests (`tests/test_intake.py`, scratch PDF in-suite): image prep, PDF
  rasterize+extract, page/length bounds, extra-absent gating, the
  untrusted-text fencing shape, oversized/malformed → validation_error.

## Wave 2 (blocked by Wave 1)

### Slice 4 — tool + route packs + provenance + accept (FR6/FR7/FR11/FR12/FR13) — **Opus** (needs S1–S3)
- `core/tools_generate.py` (API-key-gated register; lazy service seams):
  `generate_part` (drives the S1 loop over S2 intent + S3 intake, N
  candidates across the pool), `accept_candidate` (rename-via-recreate +
  provenance stamp + proposal-or-direct per Decision 9), `list_generations`,
  `generation_status`; `install_generated_provenance(service)` wrapper
  surfacing `generated` on `get_part`; the scratch-id listing guard.
- `server/routes_generate.py` (uploads reuse imports); the
  `generation_unavailable` error.
- Tests (`tests/test_tools_generate.py`, `tests/test_generation_provenance.py`):
  end-to-end fake-loop generate→accept, provenance survives
  `project_restore` + part is ordinary (AC5), PARAMS/connectors output
  contract (FR6/FR7), proposal path when history available / direct else,
  API-key gating both ways, restricted tool list (no delete/recursion).

## Wave 3 (parallel — disjoint; blocked by Wave 2)

### Slice 5 — bench generation task + one-shot baseline (AC8) — **Opus**
- `bench/`: `generate_from_prompt` category, a task bundle, the loop-vs-
  one-shot scoring delta reported in `bench report`; fake-client offline
  path.
- Tests: the category scores the six subscores; the delta is computed;
  offline fake path green.

### Slice 6 — frontend Generate panel + gallery — **Sonnet**
- Generate dialog + candidate gallery (renders/metrics/spec chips/Accept),
  live transcript reusing the chat dock's event rendering filtered by
  `generation_id`. Playwright-verified against a fake-model serve seam
  (AC7 CI-checkable without a key).

## Wave 4

### Slice 7 — acceptance + docs — **Opus tests, Sonnet docs** (needs all)
- `tests/test_prd018_acceptance.py`: AC1 (live, skipped w/o key + bench),
  AC2, AC3, AC4, AC5, AC6, AC7 (browser evidence), AC8 (bench delta) — the
  machine halves; count-guard test.
- Docs: agent-api.md (new `### Generation` section — tools/events/errors/
  provenance), architecture.md (a `## Chat agent` section + `## Generation
  loop`), user-guide (the Generate flow + honesty about spec_green), a new
  `standards/` pack note if added, AGENTS.md + CLAUDE.md traps (the loop is
  not ChatEngine — reuse seams; the tenancy capture across threads; scratch-
  id + delete_part half-write contract; frozen-spec diff measured not
  hoped; standards from tables/*.json never the model; document text is
  data not instructions; pypdfium2 lazy + [pdf] extra; register-time
  API-key gate), changelog.

## Non-negotiables
- The loop is public-tool-surface only; kernel + the four cores untouched;
  `chat.py` imported from, never edited.
- Mechanical look-and-measure is CODE, not model discretion.
- Frozen intent-specs cannot be weakened by the loop (diffed + measured).
- Standard dimensions come from `tables/*.json`, never the model; document
  text is reference data, never instructions (the security invariant).
- Budget exhaustion/abandonment are results, never exceptions; no live
  orphaned part after any exit.
- Tenant captured across every thread hop (the PRD-005 lesson).
- Full suite per slice; changelog per commit citing the count; controller-
  only `uv lock`/`uv sync`; renumber changelogs above main at merge.
