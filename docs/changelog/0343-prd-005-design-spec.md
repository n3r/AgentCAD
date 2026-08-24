# 0343 — PRD-005 multi-tenant cloud: design spec + slice plan; PRD moved to in-progress

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (orchestrator) / Nikita Fedorov

## Summary
Design groundwork for PRD-005 (the post-005a remainder: orgs/workspaces/
roles, OIDC/passkeys, agent-token tools, git push/pull/clone sync, fair
scheduling, audit, secrets-gated signed builds). No code — spec, executed
spike evidence, slice plan, PRD moved to in-progress.

## Changes
- Design spec with the recorded scope rulings: FR15/AC8 lands as a
  **secrets-gated release pipeline** (no agent can conjure signing
  identities); **FR13 is satisfied by the `Client:` trailer + UI
  rendering** (history.py's fixed-author design is deliberate and stays);
  tenancy keeps `org → workspace → project` (the shipped shell's
  `workspace` is an internal layout key; PRD-025 renames its tabs);
  audit is this repo's **first SQLite** (WAL — the one place 005a's
  Decision 14 permits).
- Executed spike (report preserved): `receive.denyCurrentBranch=
  updateInstead` is **structurally unusable** against the `.history`
  layout (receive-pack strips `/.git` and ignores `core.worktree`) —
  the design is `ignore` + explicit write-scoped `checkout -f`; git's
  own knobs leave **three FR9 holes** (non-FF force, tag rewrite, tag
  delete — the latter two are `refs/heads/`-only checks), closed by one
  pre-receive hook; `git http-backend` CGI beats hand-rolled
  `--stateless-rpc` (which silently downgrades to protocol v0);
  credential-helper auth (extraHeader and URL tokens both leak);
  bare-clone-then-flip for `agentcad clone`; OIDC needs **zero new
  deps** (httpx + pyjwt already in closure — promoted to explicit);
  passkeys via `webauthn>=3` behind a new `agentcad[cloud]` extra,
  CI-testable with a virtual ES256 authenticator (2–3 ms ceremonies;
  `soft-webauthn` rejected — forces `cryptography<45`); audit SQLite WAL
  is 126× faster than JSONL on queries, with `VACUUM INTO` as the
  documented backup step (a raw `cp` of a WAL db loses rows — measured).
- Slice plan: 10 slices in 4 waves; the tenancy integration rides two
  named surgical seams (`ProjectStore.root_resolver`, `KernelPool` fair
  pick) plus wrappers — cores untouched.

## Files
- `docs/prd/in-progress/PRD-005-multi-tenant-cloud.md` — moved from pending/
- `docs/superpowers/specs/2026-08-24-multi-tenant-cloud-design.md` — new
- `docs/superpowers/specs/2026-08-24-multi-tenant-cloud-spike.md` — new
- `docs/superpowers/plans/2026-08-24-multi-tenant-cloud.md` — new

## Notes
`make test` not run for a docs-only commit; the last measured merged tree
ran 5638 passed, 40 skipped (0319). Slice commits will carry their own
counts.
