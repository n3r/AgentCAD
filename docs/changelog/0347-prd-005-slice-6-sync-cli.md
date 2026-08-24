# 0347 — PRD-005 slice 6: the sync CLI, credential helper and remote MCP

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
The client half of git sync (FR8-client) and remote MCP (FR10):
`agentcad login | clone | push | pull | status`, a git **credential
helper** (`agentcad credential`) so a token never reaches an argv, a URL
or a file inside a clone, and `agentcad mcp --remote <url>`. A divergent
pull drives the **PRD-001 merge machinery** — it never resets.

## Changes
- `core/sync.py` (new): a second git runner, the `packages/_git.py` shape
  and for the same reason. `run()` is **network-facing and
  credential-friendly** (120 s, no `HOME` redirect — a user's proxy/CA/ssh
  config must apply, `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=""`, validated
  http(s) URLs, `-c core.autocrlf=false`, and a per-invocation
  `credential.helper=` reset + `!agentcad credential`, so a global
  keychain helper cannot answer for our host with a stale credential).
  `local()` is the **local** counterpart: `--git-dir <p>/.history
  --work-tree <p>` with `history._exec`'s hermetic environment
  (`GIT_CONFIG_NOSYSTEM`, `HOME` into the GIT_DIR), because a fast-forward
  must not consult a user's merge drivers or hooks.
- Token store `~/.agentcad/sync.json` (`AGENTCAD_SYNC_CONFIG` overrides;
  otherwise beside `config.json`, so `AGENTCAD_CONFIG` isolates tests):
  `{instances: {"<scheme>://<host:port>": {token, principal}}, clones:
  {"<abs dir>": {remote}}}`. Written **0600 from the first byte**
  (`os.open(..., O_EXCL, 0o600)` on a random-suffixed staging name, dir
  0700) — never write-then-chmod, which leaves a world-readable window.
  Keyed by `protocol://host` because that is exactly how **git** keys a
  credential query; a finer key could never be found by the helper.
- `login(url, token)` verifies **before** storing, against
  `GET /api/auth/session` — `/api/health` cannot answer this (it is in
  `security.PUBLIC_PATHS`, so a wrong token still gets 200, merely a
  trimmed body). 401/403 → refusal naming the fix; 404 → a local instance
  (recorded `mode: "local"`, not an error).
- `clone(url, dest)` is the spike §A10 flip: `clone --bare` into
  `<dest>/.history`, then `core.bare=false`, `core.worktree=<dest>`,
  `remote.origin.fetch=+refs/heads/*:refs/remotes/origin/*` (`--bare`
  writes `…:refs/heads/*`, which would move local branches on the next
  fetch), `info/exclude` from `history._EXCLUDE_LINES` **before** any
  status, the repo-local identity `_ensure_repo` writes on `git init`
  (without it every snapshot in a clone silently returns `None`),
  a persisted `credential.helper`, `checkout -f <default>`, and one
  incremental `fetch` so remote-tracking refs exist immediately.
  `verify_layout()` then asserts the four properties (no `.git`,
  `.history/HEAD`, non-bare, work tree == project dir). A failure removes
  the half-made clone and rolls back a token it added.
- `push(dir)` sends `refs/heads/*:refs/heads/*` +
  `refs/tags/*:refs/tags/*` — never `--mirror` (forces and propagates
  deletes, both refused by the server's hook), never `--follow-tags`
  (annotated tags only, measured), never a leading `+` (the *client*
  refuses a non-fast-forward, one round trip earlier and with a kinder
  message). A wildcard only pushes refs that exist locally, so "push
  everything" never deletes. The one expansion: `pull`'s internal
  `incoming/*` staging branch is filtered out of the wildcard.
- `pull(dir, merger=…)`: refuses a dirty tree, fetches, then per branch —
  fast-forward via `merge --ff-only` in the branch's own tree (or
  `update-ref` for a branch with no checkout), and a **divergence** goes
  to `merge_diverged()`, which parks the fetched side in a local
  `incoming/<branch>` and calls **`service.merges.merge()`** (PRD-001's
  `MergeOrchestrator`): staged worktree, structure-aware `project.json`
  driver, kernel validation, two-parent commit, and the same
  `{"error": {"type": "merge_conflict", …}}` payload the UI renders. No
  path resets, forces or discards.
- `status(dir)` — ahead/behind per branch, offline by default (this is
  what slice 5's `sync_status` tool should call; `fetch=True` is the
  CLI's opt-in).
- `cli.py`: one bounded block (`# PRD-005 slice 6 …` → `# end of the
  PRD-005 slice 6 block`) with `cmd_login/clone/push/pull/status/
  credential`; parsers in their own block before `parse_args`; exit codes
  0 ok / 1 refusal (server said no, conflicts, divergence) / 2 usage or
  harness. `cmd_mcp` gains `--remote`/`--token`: the MCP server has
  always been an HTTP proxy, so this resolves the token (flag → `login`
  store → env) and sets `AGENTCAD_URL`/`AGENTCAD_TOKEN`; local mode is
  untouched and a remote is never auto-started.
- `agentcad credential get` implements git's protocol: `username=
  x-access-token`, `password=<token>` for a known host, **silence**
  otherwise (git then falls through); `store`/`erase`/unknown verbs are
  accepted no-ops — `agentcad login` owns that file, and a helper that
  let git write into it would let a redirect target's 401 plant a
  credential nobody typed. The server ignores the username entirely
  (`routes_sync._promote_basic_to_bearer`).

## Files
- `agentcad/core/sync.py` — new
- `agentcad/cli.py` — new subcommand block + `cmd_mcp --remote/--token`
- `tests/test_sync_cli.py` — new (29 integration tests: real uvicorn,
  real git, real kernel)
- `tests/test_packages_cli.py` — the `--help` metavar literal now lists
  the five new commands

## Notes
- Measured: `tests/test_sync_cli.py test_sync_server.py test_history.py
  test_branches.py test_merge.py test_packages_cli.py test_mcp_remote.py
  test_cli_admin.py` → **231 passed in 301 s** (macOS, git 2.50.1). The
  full-suite count belongs to the landing commit.
- The spike's leak measurements are ported as assertions: after a real
  clone + push with a real token, **0** files under the clone contain it,
  `remote.origin.url` carries no userinfo, no recorded git argv contains
  it, and a `ps -eww` sampler running for the duration of the push never
  sees it.
- `git worktree list` reports the **GIT_DIR** as the main worktree for
  this layout (it strips a trailing `/.git`, finds `/.history`, and
  ignores `core.worktree` — the same derivation that makes
  `denyCurrentBranch=updateInstead` unusable server-side).
  `_worktrees()` translates it back to the project directory.
- A `--bare` clone copies all of `refs/heads/*`, so a clone holds every
  server branch as a real local branch. That is the right shape here —
  branches belong to the project and `BranchManager` materializes their
  trees on demand.
- Not done here, by design: docs (slice 10) and `sync_status`'s tool
  wiring (slice 5 — call `sync.status(store.canonical_path_of(proj))`).

`make test` — 6982 passed, 51 skipped recorded (13:12); one real 5-test regression in that run (tenancy_wiring.install on cmd_serve's stub services — AttributeError) was fixed before commit (guard honoring the docstring's 'safe on a service with no tenancy'; 183 passed re-verified incl. the integration suite); the rest were the documented flake families (share_publish/sketch_drag green in isolation) and the pre-existing prd028 AC6 local solver timeout (skips on CI).
