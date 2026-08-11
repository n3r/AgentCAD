# 0103 — 2026-08-11 — PRD-004 slice 6: posting a check to a proposal, and the `checks` gate

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary

Sixth slice of PRD-004 (geometry CI): a check report becomes a *decision*
(FR9, G4). `agentcad check --proposal 3` / `run_checks {proposal: "3"}` writes
the report durably to `proposals/3/checks.json`, appends one `audit.jsonl` line
and publishes `proposal_changed{reason: "checks"}`; a gate provider named
`checks` replaces PRD-002's built-in placeholder and reads that file, so a red
or stale CI verdict blocks `proposal_merge`. `proposals.py`, `packet.py`,
`merge.py` and `specs.py` are untouched — the whole enforcement surface is one
new file beside the packet and one `append` to `service.gate_providers`.

**The one design change, recorded in the design spec's as-built section:** the
gate **never answers `pending`**. The spec had a moved source head as `pending`;
`ProposalManager.merge()` blocks a gate whose state is `fail` and *nothing
else*, so `pending` is merge-**permissive** and a green posted against an older
commit would have stood there while the source moved on. That is PRD-003's X8
finding, and it is closed the same way here: a stale posted report is `fail`
whose summary names both SHAs and says re-run.

## Changes

- `agentcad/core/checks.py`:
  - `CheckStore` — the durable slot, `proposals/<pid>/checks.json`, written with
    `ProjectStore._atomic_write`. The path comes from the **public**
    `ProposalStore.packet_path(...).with_name("checks.json")`, so the id is run
    through PRD-002's `_valid_id` whitelist before it touches the filesystem;
    the store instance is the manager's own when there is one (so an audit
    append shares its lock) and a bare `ProposalStore` otherwise, because
    *reading* a posted report needs neither git nor the lifecycle.
    `read()` returns `None` for "nothing posted" and raises `ValidationError`
    for a file that exists and will not parse — the gate's safety argument rests
    on those being different answers.
  - `CheckRunner.post_to_proposal(proj, pid, report)` — resolves the proposal
    through `ProposalManager.reconcile` (not `get`, which returns the view *and
    the gates*, i.e. would ask this very provider to read the report being
    posted), refuses a **terminal** proposal with `ConflictError`, writes the
    envelope (`schema, posted_at, posted_by, actor_kind, source, head, status,
    exit_code, complete, strict, summary, stages[], report`), appends one
    `checks_posted` audit entry through `ProposalStore.append_audit`
    (append-only), publishes `proposal_changed{reason: "checks"}` and returns a
    receipt. `head` is the commit **the report says it measured**
    (`source.sha`), never the head at posting time — noticing when those two
    have drifted apart is the gate's entire job.
  - `CheckRunner.post_target(proj, pid)` — the same resolution and refusal,
    called *before* a run so a mistyped id costs a millisecond instead of a full
    rebuild, and again inside the post because a proposal can merge while a
    check is measuring.
  - `CheckRunner.measured_branch` / `matching_proposals` — `--auto-proposal`'s
    candidate set: the **active** proposals whose `source` is the branch the
    report measured (a `--ref` run names it; a working-tree run is
    `branches.current`, which is per client and therefore why the CLI sets its
    identity to `ci` first; a tag or a bare commit names no branch and matches
    nothing).
  - `CheckRunner.posted_report(proj, pid)` — the durable counterpart of
    `last_report`, a `NotFoundError` when nothing was posted.
  - `CheckRunner.gate_provider()` → a closure literally named `checks`, plus
    `_checks_verdict`. States: nothing posted → `skipped` ("no checks posted",
    byte-identical to the placeholder) · head ≠ current source head, or either
    unresolvable → `fail` naming both SHAs and saying re-run · `complete: false`
    → `fail` (a blown budget is not a verdict) · unparseable or unevaluatable →
    `fail` · `status: red` → `fail` · `status: skip` (nothing measured at all) →
    `skipped` · green, complete and current → `pass`. Both except-branches
    answer `fail`, because `ProposalManager` degrades a *raising* provider to
    `pending` — the outcome this gate exists to avoid. The provider never
    raises. `details` carry the envelope plus up to `GATE_FAILURES = 20` failing
    row ids and messages (never the whole report: gate details are read on every
    proposal fetch).
  - New constants `CHECKS_SCHEMA`, `CHECKS_FILE`, `GATE_FAILURES`; new imports
    `json`, `locks`, `ProjectStore`, and `ACTIVE`/`TERMINAL`/`ProposalStore`/
    `actor_kind` from `proposals` (all server-side, no kernel).
- `agentcad/core/tools_run_checks.py`:
  - `install_checks_gate(service)` — idempotent by name, tolerant of a service
    with **no** `gate_providers` (`tools_proposals` self-disables without git),
    called from `register()`, which is only safe because the pack loads at `r`,
    after `tools_proposals` has assigned `service.gate_providers = []`.
  - `run_checks` now really posts: `post_target` before the run (an unknown id
    is `not_found_error` and a merged one `conflict_error`, *before* any
    measurement), `post_to_proposal` after it, and the receipt on
    `report["posted"]`. A refusal that can only happen mid-run (the proposal
    merged while measuring) does **not** throw the report away: `posted.ok` is
    false, the error payload is attached and a `warnings[]` entry says so.
  - The tool description states the gate table, that a stale report is a fail
    rather than a pending, and why.
- `agentcad/server/routes_checks.py`: `GET /api/projects/{p}/checks?proposal=<id>`
  returns the record posted to that proposal (404 when there is none) — the
  durable copy, as opposed to the in-memory last report.
- `agentcad/cli.py` (inside `cmd_check` and its two helpers — no new sanctioned
  edit): `--proposal`/`--auto-proposal` are wired. The target is resolved before
  the kernel measures anything; the post happens **after** `--report`/`--md` are
  written and from the report exactly as measured, so the file on disk and the
  copy in the proposal are the same document. `--auto-proposal` with no match is
  a warning and the check's own exit code; with more than one match it is exit 2
  (guessing which proposal a verdict belongs to is worse than refusing); a
  refused post is exit 2. A project with **no proposals at all** (no git — the
  CI-runner case) is a warning, not an error. Posting notes print to stderr even
  under `--quiet`: "you asked me to post this and I did not" is not something an
  exit code can say.

## Files

- `agentcad/core/checks.py` — `CheckStore`, the posting API, the `checks` gate
  provider and its verdict table, three new constants
- `agentcad/core/tools_run_checks.py` — `install_checks_gate`, real `proposal`
  wiring in `run_checks`, description
- `agentcad/server/routes_checks.py` — `?proposal=<id>` on the GET
- `agentcad/cli.py` — `_can_post`, `_post_note`, `_post_check`; `cmd_check`
  wiring; `--proposal`/`--auto-proposal` help
- `tests/test_checks_gate.py` (new, 28 tests, `integration` + `portability`,
  skipped without git) — posting, the four gate states plus the three failure
  modes, the load order and idempotence, the tool, the route, and the CLI's
  auto-matching
- `tests/test_specs_gate.py`, `tests/test_proposals_api.py` — **one assertion
  line each**: the expected `gate_providers` list is now `["checks", "specs"]`
- `docs/superpowers/specs/2026-08-11-geometry-ci-design.md` — Decision 8 gains
  an as-built section (`pending` is gone from this gate) and divergence 8

## Notes

- **Two pre-existing test files were edited, one assertion line each.** Slice 6
  necessarily adds a second gate provider, and both files pinned
  `[p.__name__ for p in service.gate_providers] == ["specs"]` — a statement that
  cannot survive this feature and that no implementation choice can preserve
  (installing the provider lazily, e.g. on the first post, would leave a red
  `checks.json` un-gated after a process restart, which is the merge-permissive
  bug this slice exists to prevent). Each edit changes the expected list and
  nothing else; both tests still assert what they were written to assert.
- **Posting is how a proposal opts into the gate.** Absent evidence is
  `skipped`, so this gate can only ever block a proposal someone posted a check
  to; from the first post on it is fail-closed. That asymmetry is deliberate: an
  optional CI report is not a declared-but-unmeasured spec, and PRD-003's
  fail-closed `specs` gate already covers the latter.
- The posted record embeds the report **as measured** — the `posted` receipt the
  tool attaches to its return value is a delivery note and is deliberately not
  inside the stored copy.
- Identity: `cmd_check` already set `locks.set_client_id("ci")` (slice 4), so a
  post is attributed to `ci` and `actor_kind` classifies it as an **agent**
  action (only the browser is a human), and a CI run never collides with a
  human's per-client checkout in `checkouts.json`.
- Slice 7 (the GitHub Action) consumes this: the action passes `--proposal` /
  `--auto-proposal` through to the same CLI. The proposals UI showing the gate's
  new details is slice 8's documentation surface, not a code change.
