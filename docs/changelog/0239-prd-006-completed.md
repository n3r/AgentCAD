# 0239 — 2026-08-19 — PRD-006 closed out: workers confine themselves, quotas have honest tiers, the account is no longer a shell

## Summary

Bookkeeping after PR #22 (cross-platform sandboxing & resource quotas) merged
to main with every check green on its second CI run — the first execution of
the branch on x86_64 Linux and on Windows. The PRD moves to
`docs/prd/completed/` and the roadmap's marketplace-chain **step 5 is done**
(built in parallel with steps 3 and 4 rather than after them). The remaining
chain step is 031b (open publishing), which is exactly what PRD-006 was
blocking for.

## Changes

- `docs/prd/in-progress/PRD-006-sandboxing-quotas.md` →
  `docs/prd/completed/`, status "completed — merged to main in PR #22", the
  acceptance paragraph now cites the green three-OS run (ubuntu with Landlock
  ABI 7 live and `AGENTCAD_EXPECT_SANDBOX=active`; windows with the job-object
  tier sampling the interpreter behind the venv launcher; macOS with the real
  seatbelt + supervisor). The `in-progress/` directory is empty again and gone.
- `docs/roadmap.md`: chain step 5 marked **DONE (PR #22)**; the 006 row links
  to `prd/completed/` (006b stays pending — the Windows AppContainer carve-out).
- The design spec's PRD link follows the move.

## Notes

What the CI matrix added to the local evidence: Landlock is enabled in the
`ubuntu-latest` kernel's `lsm=` list at ABI 7, so the honesty gate that would
have turned a missing LSM into a red job stayed green for the right reason;
the Windows run found that a venv's `python.exe` is a *launcher* whose child
is the interpreter — the job object still capped the child (the commit-limit
balloon test passed on the first run) but the supervisor had sampled a 4 MB
stub, so it now walks the job's process list (`0238`). Codex's independent
review was quota-blocked until 20 Aug; two Opus reviews per slice, an Opus
whole-branch review (which found two live-verified Criticals — a
`pidfd_send_signal` bypass of the signal filter and `RLIMIT_NPROC` sized once
for a whole pool) and an independent Opus re-review of the fix wave stood in.

Rulings the orchestrator made on the founder's behalf are listed in the design
spec's ledger and in the PR conversation; the two a founder is most likely to
want to revisit: Windows AppContainer carved out as PRD-006b (fold it back if
a Windows box is available for the spike), and the own-cgroup route being
opt-in (`AGENTCAD_CGROUP_DIR=auto`) rather than probed by default.
