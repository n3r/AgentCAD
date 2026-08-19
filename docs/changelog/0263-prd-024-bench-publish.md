# 0263 — PRD-024 Task 7: `bench publish`, the leaderboard and the full-disclosure rule as code

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (PRD-024 Task 7)

## Summary
Adds `agentcad/bench/publish.py` — the leaderboard renderer and the five
fail-closed disclosure rules of design Decision 12. A row that does not disclose
everything is not dimmed, footnoted or flagged: it is rejected, and `publish`
raises **before writing a byte**, so the page is all-or-nothing. The output is a
single self-contained HTML file with no script, no remote asset and no clock
reading, ordered so that republishing the same input produces the same bytes.

## Changes
- **New module `agentcad/bench/publish.py`** (OCP-free, like the rest of
  `agentcad/bench/`), exporting `ROW_SCHEMA`, `REQUIRED_ROW_KEYS`,
  `row_problems`, `load_rows`, `render_leaderboard`, `publish`.
- **The five rules**, each producing a sentence that names the failing key:
  1. every key of `REQUIRED_ROW_KEYS` present, every string one non-empty
     (`notes` may be empty, `config` may be `{}` but must be present, `date`
     must be a real ISO `YYYY-MM-DD`);
  2. `report.json` validates against the design-§10 shape (schema, header
     strings, integer `harness`/`n`, finite `total`, finite per-category and
     per-task totals, list `warnings`);
  3. the row's `task_set` / `harness` / `agentcad` equal the report's;
  4. `submission` and `transcript` are absolute `https://` URLs (with an
     authority — a bare `https://` names no artefact), or paths **relative to
     the row's own directory** that stay inside it **and exist there**;
  5. the report covers every task of the declared roster.
- **Rulings written into the module, not just the tests:**
  - *A task the report flags `missing: true` is a partial run just as much as an
    absent key.* `report.aggregate` writes that flag for a roster task that was
    never scored; accepting it would let a row buy a leaderboard place by not
    running the hard half.
  - *The row's identity is its directory name, never the document's `id`* —
    `report._score_paths`' ruling one level up. A `row.json` whose `id`
    disagrees is rejected rather than given two names.
  - *An unreadable/absent row document is a rejected row (exit 1), not a harness
    error (exit 2).* A row we cannot read has disclosed nothing, which is what
    rule 1 refuses. Exit 2 stays reserved for the input the caller named: a
    leaderboard directory that is not a directory, a `rows/` that is not there,
    an output path that will not take bytes.
  - *Rule 3 and rule 5 are skipped when rule 2 already failed*, so a malformed
    report does not bury its own reason under the mismatches it caused.
  - *Plain `http://` is refused with every other scheme* (so is `javascript:`,
    an absolute path, and any `..` traversal): evidence fetched over a channel
    anyone can rewrite is not evidence.
  - *Rule 4 is deliberately narrower than design §12's "repo-relative paths
    that exist"* — recorded as **ledger D24** in the design spec by this round.
    A relative link resolves against the **row's own directory**
    (`<leaderboard>/rows/<row-id>/`) and must stay inside it, checked twice:
    textually (no `..` component, no absolute path, no scheme — so a hostile
    value is never resolved) and then via
    `target.resolve().is_relative_to(base.resolve())`, which catches what the
    text cannot see — a symlink inside the row directory pointing out of it,
    and the Windows spellings `PurePosixPath` reads as one innocent component.
    Every refusal message says "relative to the row directory
    (<leaderboard>/rows/<row-id>/)" so a submitter who followed §12 literally
    sees the narrowing instead of guessing at it.
- **The page.** Inline `<style>` (light/dark via `prefers-color-scheme`, system
  font stack, no web font, no remote asset, no `<script>`), an `<h1>`, three
  lede paragraphs stating *what is measured* (build, per-solid validity, PRD-003
  specs, volumetric IoU against a checked-in reference solid, interference,
  metric windows) and *what is not* (**no LLM judging anywhere, no human
  panel**), the ranked table (rank · row · agent · model · agentcad · harness ·
  task set · date · per-category means · total · submission · transcript), a
  per-row Disclosure block (the exact command and the canonical-JSON config),
  and a footer naming `agentcad bench score <submission> --task <id>` as the
  command that reproduces any row.
- **Determinism:** rows sort by `total` descending, ties by `id` ascending;
  category columns come from `tasks.CATEGORIES` order then extras sorted; the
  config block is rendered through `_json.canonical_json`; nothing in the output
  reads the clock or a filesystem path. A test asserts two publishes of the same
  board are byte-identical.
- **Escaping:** every row-sourced string goes through `html.escape(..., quote=True)`
  — an agent name and a model name are submitter-controlled text on a public
  page. A test feeds `<img src=x onerror="alert(1)">` through and asserts it
  comes out as inert text.
- **The write** goes through `ProjectStore._atomic_write` (staged under a random
  name, then `os.replace`), so a concurrent publisher loses rather than corrupts.
- **New data layout:** `benchmarks/leaderboard/rows/.gitkeep` — the checked-in
  board, empty until the first row is recorded. A test publishes it to `tmp_path`
  and asserts the page renders (the directory itself is never written by tests).

## Files
- `agentcad/bench/publish.py` — new: the five rules, the loader, the renderer,
  `publish`.
- `tests/test_bench_publish.py` — new: 31 tests (self-contained page, the
  measured/not-measured statement, byte-identical republish, ordering, escaping,
  the six parametrized rule rejections, partial run by absence and by
  `missing: true`, report schema mismatch, absent report, relative-link
  existence and containment, four refused link forms, a symlink out of the row
  directory, a degenerate `https://`, an `id` disagreeing with its directory,
  an unreadable `row.json` becoming a problem rather than a raise, a mixed
  `[good, bad]` board writing nothing at all, `load_rows` returning problems
  instead of raising, the checked-in layout, an absent leaderboard directory,
  and the three direct-API tests).
- `benchmarks/leaderboard/rows/.gitkeep` — new: the board layout.
- `docs/superpowers/specs/2026-08-19-agentcad-bench-design.md` — §17 ledger
  gains **D24**, recording the rule-4 narrowing (row-relative + contained).
- `docs/changelog/0263-prd-024-bench-publish.md` — this entry.

## Notes
- `agentcad/bench/cli.py` is **not** touched here (another worker owns it in this
  PR). The handler the CLI needs is:
  `rows, problems = publish.load_rows(dir, expected)` → print each problem to
  stderr and `return 1`; otherwise `result = publish.publish(dir, out, title=…,
  expected_tasks=…)` → `return 0`. `publish()` itself raises `ValidationError`
  carrying `details["problems"]`, so a handler may equally catch that and return
  1, keeping `AppError`/`Exception` → 2 for the harness lane.
- `REQUIRED_ROW_KEYS` is the **disclosure** list; `schema` and `id` are the
  envelope and are checked separately (they identify the document, they disclose
  nothing about the run).
- **`details["problems"]` is the exit-1 discriminator**, and `publish()`'s
  docstring now says so: a `ValidationError` carrying that key is "a row was
  rejected for incomplete disclosure" (§9.3 exit 1); every other `AppError` out
  of `publish` — an unreadable leaderboard directory, an unwritable output — is
  the harness lane (exit 2). A handler should branch on the key, not on the
  message text.
- Targeted test evidence: `uv run pytest -q tests/test_bench_publish.py` →
  **31 passed**; `uv run ruff check agentcad/bench/publish.py
  tests/test_bench_publish.py` → clean.
- Full suite: `make test — <orchestrator fills>`.
