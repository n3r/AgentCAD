# 0253 — 2026-08-19 — The PRD-013 "Linux hang" was a stalled apt mirror, not confinement: harden apt, revert the diagnostic

## Summary

PR #23 (PRD-013 Assembly v2) was green on macOS pytest and Windows portability
but had its ubuntu legs — `check (construction)`, `check (fasteners)`, and
`pytest (ubuntu-latest, portability)` — hang for the full 30 minutes and get
cancelled with no output, while `check (rocketry)`/`check (prototyping)` passed
in ~2 minutes. A code-level investigation reasonably suspected an interaction
between PRD-013 and PRD-006's new Linux self-confinement (an `RLIMIT_NPROC` /
OCCT-TBB thread-starvation hang). The branch-local diagnostic added in 0252
(`budget`, unbuffered output, a serial-thread A/B leg) **disproved that** and
found the real cause in the logs:

- The re-run went **fully green, including the default `construction`/`fasteners`
  legs** — a genuine confinement hang would have tripped the 1200 s budget into
  a red report, not passed.
- The passing check does its geometry in **1.3 s** and prints **no
  `[agentcad-sandbox]` line at all** — confinement is not even active in the
  geometry-CI worker, so it could not have been the cause.
- The original three hung jobs share one exact signature — a **~1773 s gap**
  immediately after
  `Get:5 https://archive.ubuntu.com/ubuntu noble-security InRelease [126 kB]`,
  then `The operation was canceled.` The stall is in **`apt-get update`**, in
  the Linux-only "Install OCCT system libraries" step, waiting on a wedged
  Ubuntu mirror connection that has no client-side timeout.

So this was never a geometry, PRD-013, or PRD-006 defect: it is an
**intermittent GitHub-Actions apt-mirror flake** — Linux-only (apt is Linux-only
here), silent (the check never runs, so no report), and it hits whichever legs
happen to draw the bad mirror (it cancelled a run on `main` too, for the same
reason). PRD-013 remains SHIP per two independent reviews and is functionally
inert for these two flat, mate-less examples (they take the byte-identical v1
fast path).

## Changes

- **`.github/actions/agentcad-check/action.yml`** and
  **`.github/workflows/ci.yml`** — the "Install OCCT system libraries (Linux)"
  step now bounds every apt fetch and retries the update:
  `-o Acquire::Retries=3 -o Acquire::http::Timeout=30
  -o Acquire::https::Timeout=30`, wrapped in a 3-attempt loop. A stalled mirror
  connection now aborts after 30 s and retries instead of hanging to the
  30-minute job cancel. The two steps are kept in sync (as their library list
  already is).
- **`.github/workflows/geometry-ci.yml`** — the 0252 diagnostic scaffolding
  (`budget: 1200`, `PYTHONUNBUFFERED`/`PYTHONFAULTHANDLER`, the serial-thread
  A/B matrix legs) is **reverted**; it had served its purpose. The `examples`
  job is back to the clean four-example matrix.

## Notes

The 0252 entry is left on the record as the honest diagnostic step that found
this — its confinement hypothesis was wrong, and 0253 is the correction. The
fix is pure CI robustness: no product, kernel, or geometry code is touched, and
it makes this class of flake self-healing for every future run of both CI
workflows.

`make test` — **4442 passed, 30 skipped** (measured on this tree; the two
`test_supervisor.py` RSS-killer timing tests that flaked under concurrent-suite
contention pass in isolation — see 0252). This commit changes only
`.github/` workflow/action YAML, which the suite does not exercise. CI on the
clean three-OS matrix is the authoritative validation.
