# 0172 — 2026-08-16 — PRD-011 slice 6: `agentcad package validate`

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

The gate becomes usable by a human and by CI before it becomes a publisher.
`agentcad package validate <dir>` runs the nine stages headless — one warm
kernel, no server, no port, no API key — prints a stage table and the failing
rows on stderr, writes the JSON report with `--report`, prints the security
non-claim once above the verdict, and exits `0` green · `1` the package is
wrong · `2` harness. It is `cmd_check`'s shape, deliberately, down to the
`finally` that stops the kernel and the setup that lives *inside* the
exit-code mapping.

## Changes

- `agentcad/cli.py` (edited — the second and last edit this plan makes to an
  existing non-test module)
  - `cmd_package(args)` dispatches the `package` subcommand; a bare `agentcad
    package` is exit 2 with a usage line rather than a traceback.
  - `cmd_package_validate(args)` — `--work-dir` resolved to an absolute path
    and handed to `_build_service(extra_writable=…)` **before the workers
    spawn** (the seatbelt profile is fixed at spawn and cannot be widened
    afterwards), `locks.set_client_id("ci")`, the gate, the kernel stopped in a
    `finally`, and everything after the run under the same exit-code mapping
    as the run itself. The report is written first, so a printing failure
    still leaves the evidence on disk.
  - `_package_lines(report, written)` / `_package_verdict(report)` — the stage
    table (`pass/fail/skip/error/total/time` per stage, with the stage's skip
    reason in parentheses), the failing rows, **what was not measured**
    (`exempt_skips`), what blocks publication when nothing failed outright,
    the warnings, the harness errors, and one verdict line on stdout carrying
    `publishable: yes|no`.
  - the `package validate` parser: `path`, `--projects-dir`, `--strict`,
    `--report PATH`, `--jobs N`, `--work-dir DIR`, `--budget SECONDS` (through
    the existing `_finite_arg`, so `nan`/`inf`/negative are refused at the
    parser before a kernel starts), and `package` added to the subparser
    `metavar`.
- `tests/test_packages_cli.py` (new) — 24 tests.
- `tests/fixtures/packages/{break_at_extreme,broken_connector}/docs/README.md`
  — each now names its part, so each fixture is wrong in exactly **one** way
  (the gate's own `docs` stage caught them: "docs/README.md never mentions
  strut").

## Files

- `agentcad/cli.py` — `cmd_package`, `cmd_package_validate`, the output
  helpers, the parser, the metavar, the module docstring
- `tests/test_packages_cli.py` — new
- `tests/fixtures/packages/break_at_extreme/docs/README.md` — names `strut`
- `tests/fixtures/packages/broken_connector/docs/README.md` — names `bracket`

## Divergences from the plan, and why

- **No `--stages` flag.** The plan's flag list for this slice is `--strict`,
  `--report`, `--jobs`, `--work-dir`, `--budget`, and it is exactly what
  ships. A stage subset is available where it is useful and safe — the
  `validate_package` *tool* (slice 7) takes `stages` — and on the CLI it would
  be a foot-gun: an unselected stage makes `publishable` false (slice 4's
  verdict rule), so `--stages format` would print "publishable: no" about a
  package that is fine.
- **No `--quiet` / `--json`.** Same reason: not in the plan's list for this
  slice. `--report` is the machine surface, and the verdict line on stdout is
  the human one.
- **The docs are slice 14's.** `docs/packages.md`, `docs/agent-api.md` and the
  `AGENTS.md` gotchas section land there, per the plan; `agentcad --help` and
  the two `description=` strings are the only documentation this slice adds,
  and both carry the non-claim.

## Verification

Targeted:

```
.venv/bin/python -m pytest -q tests/test_packages_cli.py
24 passed in 17.84s
```

The real command, on the green fixture (`echo $?` after it):

```
$ .venv/bin/agentcad package validate tests/fixtures/packages/widget_good \
      --projects-dir <scratch>/cliprojects --report <scratch>/r.json
widget_good@1.0.0 · sha256:c5dfda79cd3522bb152386cec21dfa74f738725c98f4eef5d4eb9cebf8103091
  stage       status  pass  fail  skip  error  total     time
  format      green      5     0     0      0      5    0.0 s
  contract    green      5     0     0      0      5    0.0 s
  presets     green      2     0     0      0      2    5.4 s
  build       green     11     0     0      0     11    0.1 s
  specs       green     22     0     0      0     22    0.0 s
  connectors  green      3     0     0      0      3    0.0 s
  previews    green      2     0     0      0      2    0.0 s
  docs        green      2     0     0      0      2    0.0 s
  policy      green      0     0     1      0      1    0.0 s
not measured (exempt from the publish verdict):
  - no_policy_configured
wrote <scratch>/r.json
The publish gate is a CORRECTNESS gate, not a security boundary: it proves
that the geometry builds, that the specs pass and that the connectors mate.
Package scripts run in your kernel worker with your privileges. See
docs/packages.md.
package validate: green — widget_good@1.0.0 · 52 passed, 0 failed, 1 skipped,
0 errors of 53 in 5.9 s · publishable: yes (exit 0)
$ echo $?
0
```

…and on the AC2a fixture, exit **1**, with `build:strut@length=max` named
under `failures:` and `publishable: no`.

All three PRD-011 slices in this sequence, together:

```
.venv/bin/python -m pytest -q tests/test_packages_gate.py \
    tests/test_packages_cli.py tests/test_packages_ocp_free.py
130 passed in 29.92s
```

Full suite, with PRD-011 slices 4–6 in the tree:

```
.venv/bin/python -m pytest -q -n 2 --dist loadscope -rs
2885 passed, 1 skipped in 25:06
```

The baseline after slices 1–3 was **2763 passed, 1 skipped** (changelogs
0167–0169); slices 4–6 add **122** tests (97 gate + 24 CLI + 1 OCP-free
probe). `make test` is that command (`test-full`). The single skip is
pre-existing and explained — `tests/test_analysis.py:166: agentcad[fem]
installed; the 501 fallback is unreachable`. The number is cited in all three
of this sequence's entries because the three slices were built and verified as
one run; nothing between them changes the count.

## Notes

- **The exit code is asserted through a real subprocess** for all three
  values, because an exit code is the one thing a unit test cannot honestly
  stand in for. The flag plumbing, an unwritable `--report`, a raising gate
  and the partial-report-before-exit-2 rule are tested against a stubbed gate,
  so they cost no kernel.
- **`--work-dir` inside the projects root is exit 2 with both paths named and
  the directory never created** — the CLI resolves it, the gate refuses it,
  and the test asserts the absence afterwards.
- The non-claim is the **last line of stderr**, immediately above the one line
  stdout carries, and it is also `report["note"]`, so it travels with every
  copy of the evidence. A test asserts it appears exactly once.
