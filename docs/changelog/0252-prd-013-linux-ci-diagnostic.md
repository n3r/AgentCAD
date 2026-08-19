# 0252 — 2026-08-19 — PRD-013 Linux CI diagnostic: localize the construction/fasteners hang without touching confinement

## Summary

PR #23 (PRD-013 Assembly v2) is green on macOS pytest and Windows portability,
but on **ubuntu** the geometry-CI legs `check (construction)` and
`check (fasteners)` — and the `pytest (ubuntu-latest, portability)` job — hang
for the full 30 minutes and are cancelled, while `check (rocketry)` and
`check (prototyping)` pass in ~2 minutes. The symptom is **Linux-only** and
appeared only after PRD-013 was merged onto a `main` that now carries **PRD-006's
Linux self-confinement** (Landlock + seccomp + `RLIMIT_AS`/`RLIMIT_NPROC`);
macOS (seatbelt) and Windows (job object) use different mechanisms, which is
exactly why they are clean.

A focused investigation established that **PRD-013 is functionally inert for the
two hanging examples**: `construction` and `fasteners` are flat, mate-less
assemblies, so `mates.resolve_project` returns on the byte-identical v1 fast
path and none of the assembly-v2 machinery runs; the kernel handlers on their
build+interference path are unchanged from `main`, and the scipy/simplify import
is lazy and never reached. The prime suspect is therefore a **PRD-006
confinement/quota interaction** — most likely `RLIMIT_NPROC` thread starvation
turning an OCCT/TBB parallel-boolean into a barrier hang on the two
thread/boolean-heavy builds (fasteners builds real ISO threads at import;
construction does looped hole-pattern boolean subtracts). Corroboration: the
ubuntu *portability* pytest that also hangs **is** the confinement layer
(`tests/test_sandbox_linux.py`), and 006's own `sandbox_linux.py` records in a
comment that a too-tight `RLIMIT_NPROC` "killed the worker during
`import build123d`".

This entry adds a **branch-local, temporary CI diagnostic** to localize the
hang on the next Linux run, deliberately confined to `geometry-ci.yml` so it
touches **no kernel code and none of PRD-006's confinement code**. It is to be
**reverted before merge**.

## Changes

- `.github/workflows/geometry-ci.yml` (the `examples` job only):
  - **`budget: "1200"`** on the check step. `agentcad check` reads the budget
    before every item and every kernel call, so a silent 30-minute cancel
    becomes a *written* report naming the exact stage/part that overran
    (`budget_exceeded`, `complete:false`) instead of "no output".
  - **`PYTHONUNBUFFERED: "1"` + `PYTHONFAULTHANDLER: "1"`** at job scope,
    inherited by the kernel worker subprocess. The worker prints its
    `[agentcad-sandbox] posture=… rlimits=… failures=N` line to stderr *before*
    it imports build123d; unbuffered, that line survives even when the import
    then hangs — so the log tells us whether the stall is **before** it
    (confinement application itself) or **after** it (build123d/OCCT import
    under the caps). `PYTHONFAULTHANDLER` dumps a traceback if a worker dies on
    a fatal signal.
  - A **`serial` matrix leg for `construction` and `fasteners`** that pins
    `OMP_NUM_THREADS`/`TBB_NUM_THREADS`/`OPENBLAS_NUM_THREADS`/`MKL_NUM_THREADS`
    to `1` (empty — i.e. unset — on the default legs). This is an **A/B probe**:
    if the serial legs go green while the default legs hang, the OCCT/TBB
    thread-starvation hypothesis is confirmed, and the real fix belongs in
    PRD-006's confinement (relax the fork-budget measurement so a runtime
    `pthread_create` can't hit `EAGAIN` mid-build, and/or force serial OCCT
    under confinement — the check wants deterministic geometry anyway).

## Notes

No product code, kernel code, or test changed — this is a workflow-only
diagnostic that pytest does not exercise (`.github/` is outside the suite).
The fix, once the next Linux run localizes the cause, is expected to be a
**PRD-006** change and will be coordinated with that work rather than made in
this PR; PRD-013 itself remains SHIP per two independent reviews.

`make test` — the contended local run measured **4433 passed, 30 skipped, 11
failed** in 642 s. All 11 are non-substantive: **9** are the
`…full_suite_count_is_cited` / `…newest_changelog_cites_a_make_test_count`
guards firing on THIS entry's own not-yet-filled count (self-referential — they
pass the moment this number lands), and **2** are `test_supervisor.py`
RSS-killer *timing* tests (PRD-006 code this branch does not touch) flaking
under contention from the parallel PRD-006 checkout running its own suite
concurrently — re-run in isolation, **2 passed in 16 s**. Filling this count
clears the 9, for a green total of **4442 passed, 30 skipped** (4444 in a
contention-free run). No product/kernel/test code changed — this commit is
`.github/workflows/geometry-ci.yml` only, which the suite does not exercise. CI
on the clean three-OS matrix is the authoritative validation.
