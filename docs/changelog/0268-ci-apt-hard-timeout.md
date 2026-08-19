# 0268 — 2026-08-19 — CI: a hard timeout around apt so a stalled mirror can't hang the job

## Summary

The PRD-013 apt hardening (0253) reduced the Ubuntu-mirror flake but did not
eliminate it: PR #25's geometry-CI legs `rocketry`/`prototyping`/`fasteners`
still hung 30 minutes and were cancelled with the exact old signature — a ~1773 s
gap right after `Get:5 https://archive.ubuntu.com/ubuntu noble-security
InRelease`. Root cause of the *remaining* gap, now understood: two things the
0253 fix missed —

1. `Acquire::http::Timeout`/`Acquire::https::Timeout` do **not** reliably abort a
   mirror that accepts the connection and then stalls mid-body; and
2. the retry `for` loop only re-runs when `apt-get update` **returns** non-zero —
   but a hung apt never returns, so the loop never fires.

## Fix

Wrap each apt call in a hard **`timeout`** in both CI workflows
(`.github/actions/agentcad-check/action.yml` and `.github/workflows/ci.yml`,
kept in sync): `sudo timeout 150 apt-get … update` in a 3-attempt loop, then
`sudo timeout 300 apt-get … install …`. `timeout` kills the wedged fetch
(SIGTERM/SIGKILL), so `apt-get` returns non-zero and the retry actually runs;
three bounded attempts cap the update at ~450 s (well under the 30-minute job
limit), and if all three fail the step exits **1** — an honest, fast red naming
the infrastructure, not a silent 30-minute cancel. The `-o Acquire::*` options
stay as a first line of defence, and the `-o` flags remain **after**
`--no-install-recommends` so `test_geometry_ci_action`'s lib-sync guard is
unaffected (still 37 passed).

## Notes

Pure CI robustness — no product/kernel/test code changed; the suite is unchanged
from the PRD-014 merge. This makes the class of flake self-healing for every
future run of both workflows, on PR #25 and after.

`make test` — **4550 passed, 38 skipped** (unchanged from the PRD-014 + PRD-006b
merged tree; this commit is `.github/` workflow YAML only, which the suite does
not exercise). CI on the three-OS matrix is authoritative.
