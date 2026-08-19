# 0255 — PRD-006b design: Windows AppContainer confinement, probe-first

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov (orchestrated; Claude Fable 5)

## Summary
Design commit for PRD-006b (the Windows confinement half carved out of
PRD-006): the PRD moves to `docs/prd/in-progress/`, its stale link to the
006 PRD is fixed, and the design + plan are recorded.

## Changes
- `docs/superpowers/specs/2026-08-19-windows-appcontainer-design.md` — the
  Windows worker is created inside a per-installation AppContainer (no
  capabilities = no network; write roots and the private temp dir granted to
  the package SID via `icacls`; read ACEs on the interpreter/venv/app tree);
  a `Backend.spawn()` hook plus a ctypes `CreateProcessW`/`STARTUPINFOEX`
  `ConfinedProcess` because `subprocess.Popen` cannot pass
  `SECURITY_CAPABILITIES`; the worker proves the confinement from its own
  token (`TokenIsAppContainer`) so `active` stays measured; the job object is
  assigned during a suspended start; and — because no Windows box exists
  here — a dispatch-only probe workflow runs the whole experiment on
  `windows-latest` before any implementation lands.
- `docs/superpowers/plans/2026-08-19-windows-appcontainer.md` — three slices:
  probe loop · implementation + tests · docs/CI gate/close-out.
- `docs/prd/pending/PRD-006b-windows-appcontainer.md` → `docs/prd/in-progress/`
  (link to the completed PRD-006 fixed).

## Files
- `docs/superpowers/specs/2026-08-19-windows-appcontainer-design.md` — new
- `docs/superpowers/plans/2026-08-19-windows-appcontainer.md` — new
- `docs/prd/in-progress/PRD-006b-windows-appcontainer.md` — moved
- `docs/changelog/0255-prd-006b-design.md` — this entry (0240 on the branch; renumbered at close-out)

## Notes
Branch `prd-006b-windows-appcontainer`. Every verification of this PRD is a
Windows-CI round trip; the plan front-loads a probe so the implementation
slice starts from measured facts (the first denied DLL/dir, ACE propagation
to pre-existing children, the token self-check) rather than from guesses.
`make test` — unchanged by this commit (docs only; 4349 passed on the merged
PRD-006 tree, changelog 0238).
