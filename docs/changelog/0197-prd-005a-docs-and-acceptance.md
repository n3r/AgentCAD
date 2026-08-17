# 0197 — PRD-005a slice 8: CI, documentation, and the acceptance pass

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (with Nikita Fedorov)

## Summary

Closes PRD-005a. The compose deployment is smoke-tested in CI (a seconds-long
`config --quiet` lint on every PR, the full `up`/`exec`/restart cycle on main
and weekly), the documentation says what the code actually does — including the
six slices' deliberate divergences — and `tests/test_prd005a_acceptance.py`
grades AC1–AC11 one test per criterion, with AC3's browser half recorded as
**unverified** rather than quietly counted as a pass.

## Changes

- **`.github/workflows/deploy-smoke.yml` (new).** `push: [main]` (path-filtered),
  a weekly `schedule`, and `workflow_dispatch` — deliberately **not**
  `pull_request`, because the OCCT wheels make the image multi-GB. Builds,
  waits on the healthcheck, asserts `/api/health` is exactly `{status, mode}`
  with `mode: hosted`, serves `/`, `/js/api.js` and `/api/public/packages`
  anonymously while `/api/projects` is 401, creates an admin through
  `docker compose exec`, enrols, signs in, proves the enrolment **replay is a
  404**, creates a project, then `down`/`up` and proves the account, the live
  session cookie and the project all survived. Logs on failure, `down -v`
  always.
- **`.github/workflows/ci.yml`** — `docker compose -f compose.yaml config
  --quiet` in the existing `ubuntu-latest` job. Seconds, no build.
- **`agentcad/cli.py`** — `_trust_sentence_capitalized()` replaces
  `TRUST_SENTENCE.capitalize()` at its four call sites. `str.capitalize()`
  lower-cases everything after the first character, so `agentcad admin --help`
  had been printing "arbitrary python"; the language is a proper noun and this
  is the one sentence FR17 puts in front of every person about to be handed an
  account. Found by the acceptance test that greps for it.
- **`AGENTS.md`** — a "Hosted-core gotchas (PRD-005a)" section: 22 items, each
  traceable to a design decision or a test, opening with the trust statement
  because every other item is downstream of it.
- **`CLAUDE.md`** — the condensed version in the traps list, plus
  `docs/deployment.md` in the deeper-docs line.
- **`docs/architecture.md`** — a hosted variant of the process diagram and a
  hosted paragraph in the **Trust model** section: authentication is real,
  confinement is not; an account is a shell; the anonymous surface is nine
  kernel-free entries; what hosted mode refuses rather than confines; and the
  residuals it does not close. Also corrects the stale tool count (73 → **83**
  measured, 86 with `[fem]`) in the three places it appears.
- **`docs/agent-api.md`** — the three new error types **with the names the code
  actually uses**; `whoami` and the hosted MCP paragraph; and the "Identity is
  self-asserted" paragraph rewritten to separate local mode (unchanged) from
  hosted mode (`X-Agent-Id` is not an identity).
- **`docs/user-guide.md`** — a "Signing in (a hosted instance)" section and the
  `state/auth/*.json` row in "Where files live".
- **`docs/deployment.md`** — a "What a stranger can reach" section: the nine
  entries as a table, the kernel-silence proof and its positive control, the
  `scope: "public"` filter and the indistinguishable `404`, why
  `GET /api/packages/search` stays authenticated, and the `Cache-Control` that
  makes a proxy the answer to a flood. Slice 6 wrote this file before
  `routes_public.py` existed, so the anonymous surface it described was four
  entries short.
- **`docs/roadmap.md`** — the `005a` row and chain step 2 read *implemented*
  (moving to `completed/` at merge) and name the one criterion that is graded
  as evidence; step 3 (PRD-007) spells out all four things it inherits.
- **`docs/prd/in-progress/PRD-005a-hosted-core.md`** — `Status: implemented`, a
  **Verification levels** table (direct test / real server / real container /
  real browser, per criterion) and a **Residual gaps** section.
- **`tests/test_auth_routes.py`** —
  `test_the_rate_limit_is_taken_before_the_password_is_hashed` **counts
  `hashlib.scrypt` calls** instead of timing the request, and that took two
  attempts to get right. The original `elapsed < 0.03` is a claim about the
  *machine*: it passes standalone in 3.6 s and failed at **0.083 s** in an
  8-way full-suite run. Rewriting it as a ratio against the same run's own
  scrypt failed too — **0.067 s throttled against 0.076 s hashing** — because
  on a loaded box the ASGI round trip alone can cost more than the KDF. No
  wall-clock formulation of this property is stable (the `test_sketch_bench` /
  `test_sketch_drag` flake class, changelogs 0186 and 0195). The property is
  structural, so it is now asserted structurally: a wrong password must reach
  scrypt (the positive control) and the throttled request must add **zero**
  calls. Verified by moving `verify_password` in front of the buckets — the
  count goes to one and the test goes red.
- **`tests/test_prd005a_acceptance.py`** AC5's timing half takes `min` of three
  samples per handle, the same idiom, for the same reason. Both halves hash, so
  load moves them together.
- **`tests/test_authstore.py`** — `test_secrets_are_not_stored_raw` slices the
  bearer with `split("_", 2)`. The plan's `split("_")[2]` checked only the
  secret's first underscore-free *fragment* (`token_urlsafe`'s alphabet
  contains `_`), which changelog 0188 recorded as a weakness and kept verbatim
  anyway. It is worse than weak: roughly once in sixty-four that fragment is a
  single character, and the assertion becomes "does the letter `0` appear in a
  64-hex-digit digest". It took the full suite down on the third consecutive
  run of this branch (`assert '0' not in '{...}'`). With the maxsplit, `[2]` is
  the entire secret — the module's own idiom — plus a length assertion so a
  fragment can never masquerade as one again. Demonstrated on a forced
  `"a_zzz…"` secret: the naive slice is `'a'` and **is** in the file; the fixed
  one is not.
- **`.gitignore`** — `.env`, which was in `.dockerignore` and nowhere else.
  The quick start is literally `cp .env.example .env` and the file it makes
  holds `AGENTCAD_SECRET_KEY`; keeping it out of an image layer and out of the
  repository are the same requirement, and only the first was covered.
  `tests/test_deploy_config.py::test_dot_env_is_never_committed_either` pins
  both halves, including that `.env.example` is *not* swallowed by the rule.
- **`tests/test_prd005a_acceptance.py` (new)** — 23 tests, AC1–AC11 plus five
  record checks.

## Files

- `.github/workflows/deploy-smoke.yml` — new
- `.github/workflows/ci.yml` — the compose lint step
- `agentcad/cli.py` — `_trust_sentence_capitalized()`
- `.gitignore` — `.env`
- `tests/test_deploy_config.py` — the `.gitignore` half of the `.env` rule
- `tests/test_auth_routes.py` — the login-throttle timing test, de-flaked
- `tests/test_authstore.py` — the raw-secret test, de-flaked
- `AGENTS.md`, `CLAUDE.md`, `docs/architecture.md`, `docs/agent-api.md`,
  `docs/user-guide.md`, `docs/roadmap.md`
- `docs/deployment.md` — "What a stranger can reach"
- `docs/prd/in-progress/PRD-005a-hosted-core.md` — status, verification levels,
  residuals
- `tests/test_prd005a_acceptance.py` — new

## Notes

- **The PRD stays in `in-progress/`.** The house rule (changelog 0164, and the
  separate "PRD-0NN completed" commits in this repo's history) is that a PRD
  moves at **merge**, not when the build finishes, and `_find_prd()` searches
  all three directories so nothing breaks either way. Moving it now would also
  have claimed a completeness AC3 does not have.
- **The browser pass did not happen, for the third time.**
  `list_connected_browsers` returned `[]` in the slice-3 session, the
  slice-4–6 session and this one. The sign-in view, the identity chip, a lock
  chip under a real edit and the enrolment page have **never been rendered by a
  browser**. Rather than weaken AC3, the criterion is unchanged and the record
  is explicit in three places: the PRD's verification table, this entry, and
  `test_ac3_the_browser_half_is_recorded_as_unverified`, which asserts the PRD
  still admits it — a test that *fails* if someone quietly deletes the
  admission. What is verified instead, and at what level, is the table in the
  PRD. The PRD-011 AC7 precedent is followed for the shape (grade the
  evidence), not for the outcome (its session actually ran).
- **The smoke workflow has never executed on GitHub, so its assertions were
  executed here instead**, step for step, against the real container: build →
  `up -d` → `healthy` after ~20 s → `{"status":"ok","mode":"hosted"}` with
  exactly two keys → `/` and `/js/api.js` 200, `/api/public/packages` serving
  the nine-package catalog anonymously, `/api/projects` 401 → `docker compose
  exec agentcad agentcad admin user add smoke --admin` printing an enrolment
  link → enrol → `/api/projects` 200 with the cookie → **replay 404** → create
  a project → `down` + `up` → the same cookie still authenticates
  (`{"principal":"user:smoke","kind":"user","role":"admin","mode":"hosted"}`),
  the project is still there, `admin user list` still shows `smoke`. Then
  `down -v`. A workflow that has been run this way can still fail on a runner
  for runner reasons; that is what the first run on main is for.
- **`docker compose build`, not `buildx bake` with a GHA cache.** Exporting
  layers to the GitHub cache means building by a different path from the one an
  operator runs — and this job exists to prove *that* path. It runs weekly and
  on main, so a cold build is affordable. Stated because "with layer caching"
  is what the plan asked for.
- **The acceptance file follows the house pattern**: `_find_prd()` (the PRD-010
  close-out trap, changelog 0164), property-based status assertion
  (`implemented` **or** `completed`), the newest-changelog-cites-a-count check
  (PRD-004 AC10 → PRD-011 AC8), and one test per criterion naming it in the
  docstring. Two of its tests are deliberately *evidence* checks and say so:
  AC8 (a container is not a unit test) and AC9's suite count (recomputing it
  would mean running the full suite from inside the full suite).
- **AC3's history assertion is at the trailer, not at the route.** The `hosted`
  fixture's service has `bus.on_publish = None` — no synchronous git snapshot —
  so `GET …/history` is empty by construction there. The test pins
  `history.with_client_trailer` / `author_of` under the composed principal, and
  the end-to-end version is on the record in changelog 0190 (a real hosted
  server writing `Client: user:nikita/browser:7f3a1b2c`). Asserting against an
  empty list would have been a green test about nothing.
- **AC5's timing assertion is a 5× window, not an equality.** The dummy scrypt
  is what makes "no such account" cost what "wrong password" costs (~63 ms
  each); skipping it would make the unknown-handle path ~0 ms, which a 5×
  window catches while surviving an 8-worker co-loaded run.
- **The gotcha section states the `open_project` residual at its real
  strength** — a registry-level tool that is *not* refused, because
  `core/tools.py` is off-limits to this feature and `ToolRegistry` has no
  unregister seam; reachable only by an authenticated member who can already
  run arbitrary Python; a real gap in FR19's letter and nothing more. The same
  wording is in the PRD's residuals and in `CLAUDE.md`.

## Verification

- `.venv/bin/python -m pytest tests/test_prd005a_acceptance.py -q` →
  **23 passed in 27.57 s**.
- With the feature's other suites (`test_public_catalog`,
  `test_hosted_surface`, `test_cli_admin`, `test_deploy_config`,
  `test_auth_routes`, `test_security_guard`, `test_tokens`,
  `test_hosted_hardening`) plus `test_prd011_acceptance` → **276 passed**.
- **The `deploy-smoke.yml` assertions, executed locally against the real
  container** — the walk quoted in the Notes above, ending `SMOKE OK`.
- `make test` → **3693 passed, 1 skipped in 775.29 s** (8 workers, this machine). The branch
  baseline before slice 7 is 0194's **3640 passed, 1 skipped in 660.31 s**, so
  slices 7 and 8 add exactly **53** tests — 29 in `test_public_catalog.py`,
  23 in `test_prd005a_acceptance.py` and 1 in `test_deploy_config.py` — and no
  regressions.
  - **Five runs were needed, and every red was a pre-existing flake this
    slice then fixed** — none was a regression from slices 7–8, and each is
    written up above:
    `test_the_rate_limit_is_taken_before_the_password_is_hashed` twice (a
    fixed 30 ms wall clock at 0.083 s, then a ratio at 0.067/0.076, now a
    scrypt **call count**) and `test_secrets_are_not_stored_raw` once (a
    ~1-in-64 random slice of the bearer). Running the suite five times is what
    surfaced two flakes that had been sitting in it since slices 1 and 3 — and
    the reason to run it five times rather than re-run until green is that
    "it passed the second time" is not evidence about anything.
  - The first run, taken *before* this line existed, reported **3687 passed,
    5 failed in 664.88 s**. All five are
    `test_ac*_the_full_suite_count_is_cited` (PRD-005a, 008, 009, 010, 011):
    each requires the **newest** changelog entry to cite a suite count, and the
    newest entry was this one, without a count yet. That is the bootstrap 0194
    records for the same reason, not a regression — 3687 + 5 = 3692, the same
    population, and the `.gitignore` test came after it.
- `make test-portability` → **726 passed in 353.94 s**, unchanged from 0194's
  count: neither slice adds a `portability` row.
- Browser: **not verified** — `list_connected_browsers` → `[]`, for the third
  session running. Chrome itself is running on this machine; the MCP bridge
  reports no connected browser, which is a pairing state this agent cannot
  change.
