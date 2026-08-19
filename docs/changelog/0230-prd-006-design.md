# 0230 — PRD-006 design: sandboxing & quotas — Linux self-confinement, honest quota tiers, metering

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov (orchestrated; Claude Fable 5 + Opus spikes)

## Summary
Design commit for PRD-006 (cross-platform sandboxing and resource quotas):
the PRD moves to `docs/prd/in-progress/`, and the design spec records the
decisions — most of them evidence-based, from three spikes run against the
real worker inside the shipped compose image and on this macOS box.

## Changes
- `docs/superpowers/specs/2026-08-18-sandboxing-quotas-design.md` — the design.
  Load-bearing decisions: Linux confinement is **in-process Landlock + seccomp
  via ctypes** in a worker preamble (bwrap is absent from the image and
  `unshare -Ur` is denied under Docker's default seccomp; the ctypes path
  works as uid 10001 with no capability at 0.3 ms and `import build123d` +
  a real build succeed after restriction) · a **private per-worker temp dir**
  (the spike found that granting `/tmp` wholesale leaks across workers) ·
  two read postures, `local` (global read) and `hosted` (allow-list that
  excludes the state dir, so a script can no longer read the session secret)
  · quotas are **tiers with an honest name** — cgroup v2 only by operator
  delegation (`--cap-add SYS_ADMIN` rejected; `/sys/fs/cgroup` is read-only
  in the compose posture), Linux rlimits (`RLIMIT_AS` makes balloons a
  recoverable `MemoryError`; `RLIMIT_NPROC` = live uid count + headroom
  because a fixed 32 kills the worker at import), Windows job objects, and a
  **parent-side RSS supervisor everywhere** because `setrlimit(RLIMIT_AS)` is
  `EINVAL` on Darwin (measured) · metering as a per-response `usage`
  envelope (Linux `clear_refs=5` gives a true per-request peak; `ru_maxrss`
  is bytes on macOS, KiB on Linux) rolled up by a service-owned meter into
  `/api/health` and a `get_usage` tool · breaches classified onto the
  existing errors (`details.reason`, `details.denied`, `details.usage`) ·
  disk budgets in `ProjectStore` with a cache janitor · Windows AppContainer
  carved out as PRD-006b (letter-suffix precedent) · CI proves containment
  with `AGENTCAD_EXPECT_SANDBOX=active` so degradation is red, not skipped.
- `docs/superpowers/plans/2026-08-18-sandboxing-quotas.md` — the five-slice
  implementation plan (quotas+facade+macOS · worker self-confinement+meter ·
  Linux/Windows backends+supervisor · service/health/usage/disk · CI/docs/AC).
- `docs/prd/pending/PRD-006-sandboxing-quotas.md` → `docs/prd/in-progress/`.

## Files
- `docs/superpowers/specs/2026-08-18-sandboxing-quotas-design.md` — new
- `docs/superpowers/plans/2026-08-18-sandboxing-quotas.md` — new
- `docs/prd/in-progress/PRD-006-sandboxing-quotas.md` — moved from `pending/`
- `docs/changelog/0230-prd-006-design.md` — this entry (numbered 0213 on the branch; renumbered at merge)

## Notes
Branch `prd-006-sandboxing-quotas`, built in parallel with PRD-007 (which is
file-disjoint: `security.py`/`presence.py`/share packs vs `kernel/*`). Two
seams to coordinate at merge: 007's variant store under
`<state-dir>/publications/build/` must be a granted write root once Linux
confinement exists (`_writable_roots` does not include the state dir), and
007's public error mapping should whitelist `details` fields now that
`kernel_crash` carries `reason`/`usage`. Changelog numbers will collide
with 007's at merge (0212 is already double-booked); renumber then. The
spike found one pre-existing protocol hazard worth fixing in the build: a
forked child of a script inherits fd 1 and can forge a response with the
predictable next request id — the design makes the id unguessable.
