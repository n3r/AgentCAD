# 0175 — 2026-08-16 — PRD-011 slice 9: the git index — a repo is an index

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

The headline capability of the roadmap's step 1, and it is small because slice
3 made a local index the general case: `GitIndex` is
`~/.agentcad/indexes/<name>/` at a pinned ref, `refresh()` is a shallow clone
then a shallow fetch and a **hard reset**, and everything after that **is**
`LocalIndex` over the checkout. `_git.py` is a second, small git runner with a
docstring that says in full why it is not `history._run`. **AC4 is won here**:
the remote is deleted outright and `use_part` still works, while
`add_package` of the already-cached package still succeeds offline and writes a
byte-identical lock entry.

## Changes

- `agentcad/core/packages/_git.py` (new)
  - The docstring states the three things `history._run` does that are wrong
    here — hard-coded `--git-dir`/`--work-tree`, a **10 s** timeout, and
    `HOME`/`XDG_CONFIG_HOME` redirected into `.history` with
    `GIT_CONFIG_NOSYSTEM=1` — and why each is wrong for a remote index (there
    is no work tree, a clone routinely exceeds 10 s, and a private index is
    exactly the case that needs the user's credential helper).
  - `validate_url(url)` — `https://`, `ssh://`, `git+ssh://`, `file://`,
    `git@host:path` or an absolute path; **never** starting with `-`, never
    carrying a shell metacharacter. Fixed argv makes a metacharacter inert; it
    is refused anyway, because defence that only works when the *other*
    defence works is not defence.
  - `run(*args, cwd=, timeout=DEFAULT_TIMEOUT)` — fixed argv, no shell,
    **120 s**, `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=""`, `SSH_ASKPASS=""`,
    and `GIT_SSH_COMMAND="ssh -o BatchMode=yes"` **only when the caller has
    not set one**. `HOME` is not redirected. A non-zero exit or a timeout is a
    `GitError` carrying stderr.
  - `clone(url, dest, ref)` = `clone --depth 1 --branch <ref> -- <url>`;
    `fetch(dest, url, ref)` = `fetch --depth 1` + **`reset --hard
    FETCH_HEAD`** (reset, never merge: a force-pushed index repo must not
    leave the client on a branch that no longer exists, and a merge would
    invent a document nobody published).
  - `available()` — the same cached `shutil.which("git")` probe
    `ProjectHistory.available` makes, used only when a caller has no service
    to borrow one from; `indexes_root()` — `AGENTCAD_INDEXES_DIR` else beside
    `config.json`, `cache.root`'s rule.
- `agentcad/core/packages/indexes.py`
  - `GitIndex(LocalIndex)` — `kind = "git"`, `url`, `ref` (default `main`),
    `stale`, `stale_reason`. `refresh(force=False)` clones on first use and
    fetches afterwards, **once per client instance** unless forced (an index
    client is long-lived on the service, and a network fetch per keystroke in
    the Library dialog is not a search). An unreachable remote is a *warning*:
    the last good checkout keeps answering. `entries()` on a never-cloned
    index is a `not_found_error` naming the URL and the ref. `source_of`
    records `{kind, url, ref, path}` — configuration, never a machine fact.
  - `load_indexes(config, warnings, *, git_available=None)` builds `git`
    entries, defaults `ref` to `main`, and **skips every git index with a
    warning when git is not on PATH** — the versioning/proposals self-disable
    precedent. `_PLANNED` now holds only `cloud`.
  - `GitIndex` **overrides `publish` and `yank` to refuse.** Both are
    inherited from `LocalIndex` and both would write into a checkout that the
    very next `refresh()` hard-resets — a publish that vanishes with no error
    is the worst shape a failure can take. The refusal names the fix: publish
    into a clone you control (a *local* index over your working tree) and push
    it, which is what makes a repository an index.
  - Module docstring updated for `GitIndex` and the publisher.
- `agentcad/core/packages/manager.py` — `reload_indexes` passes
  `service.history.available` as the git probe (already cached; no new probe),
  falling back to `_git.available` for a service without one.
- `tests/test_packages_git_index.py` (new, `portability`) — 37 tests.
- `tests/test_packages_index.py` — the two slice-3 tests that asserted "git
  indexes are not available in this build" now use `cloud`, the kind that
  still is not.
- `tests/test_packages_ocp_free.py` — a probe for `_git`.

## Divergences from the plan, and why

- **`refresh()` is once-per-instance unless forced.** The plan does not say
  when a refresh happens, and the obvious reading — every call — means
  `search_packages` fetches on every keystroke. Once per client instance is
  deterministic (no clock, no throttle window to test), `reload_indexes()`
  mints new clients, and `refresh(force=True)` is the explicit re-fetch.
- **`GIT_SSH_COMMAND` gains `BatchMode=yes` when the caller has not set one.**
  Not in the spec's environment list. `GIT_TERMINAL_PROMPT=0` does **not**
  cover ssh, so an ssh index with a passphrase-protected key would block a
  server for the full 120 s — the exact failure that variable exists to
  prevent. An ssh-agent key still works, and a user with their own
  `GIT_SSH_COMMAND` keeps it (there is a test for that).
- **`source_of` includes `path` as well as `{kind, url, ref}`.** The spec's
  example lock entry shows the first three. The path is index-relative and
  content-determined, so it costs nothing in byte-identity and it is what makes
  an offline reconstruction identical to an online one — which AC4 asserts by
  comparing the two documents.
- **`git+ssh://` and `file://` are accepted** alongside the spec's four
  shapes. `file://` is what makes the whole suite hermetic; `git+ssh://` is the
  same transport under a spelling pip taught people.

## Notes

- **Every test is hermetic.** The "remote" is a bare repository in `tmp_path`
  reached over `file://`, created and committed to inside the fixture — so
  nothing needs a network and nothing is skipped for lacking one. The only
  skip condition is `git` not being on PATH.
- **The negations are tested, not assumed.** A URL starting with `-`, six
  shapes of shell metacharacter, a bad scheme and an empty URL are refused
  **with `subprocess.run` monkeypatched to raise if it is ever reached**. The
  environment test sets `HOME` to a sentinel and asserts it survives, asserts
  `GIT_CONFIG_NOSYSTEM` is absent, asserts the timeout is ≥ 120 and asserts
  `--git-dir`/`--work-tree` never appear in the argv.
- **The force-push case is a real force-push.** A second, unrelated history is
  built in a scratch directory and pushed with `--force`; the client follows
  it to `2.0.0` and stays non-stale. Mutating `reset --hard` to `merge
  --ff-only` reddens that test and one other, which is how the claim was
  checked rather than asserted.
- **A tampered checkout installs nothing.** The checked-out package script is
  edited after the clone; `cache.install`'s content-id comparison refuses,
  names both ids, and the cache stays empty. The trust boundary is stated by
  that test: the index declares content ids and the cache verifies the fetch
  against the declaration — an index that lies about *both* is a compromised
  index, which is what signatures (PRD-031 FR2(d), the reserved `signatures`
  slot) are for.
- **One broken git index does not stop the next one.** An unreachable remote
  in front of a good one leaves the good one answering, with the failure in
  `tried` — the same rule `load_indexes` and `manager.resolve` already follow.

## Verification

Targeted, this slice:

```
.venv/bin/python -m pytest -q tests/test_packages_git_index.py tests/test_packages_ocp_free.py
50 passed
```

All three slices in this sequence (7, 8 and 9) together:

```
.venv/bin/python -m pytest -q tests/test_packages_tools.py tests/test_packages_publish.py \
    tests/test_packages_git_index.py tests/test_packages_ocp_free.py \
    tests/test_packages_gate.py tests/test_packages_cli.py tests/test_packages_index.py \
    tests/test_packages_cache.py tests/test_packages_format.py tests/test_manifest_merge.py
552 passed in 47.04s
```

Full suite (`make test` — `pytest -q -n 2 --dist loadscope -rs`):

```
3008 passed, 1 skipped in 1549.33s (0:25:49)
```

The single skip is pre-existing and explained — `tests/test_analysis.py:166:
agentcad[fem] installed; the 501 fallback is unreachable`.

The baseline after slice 6 was **2885 passed, 1 skipped** (changelogs
0170–0172); slices 7–9 add **123** tests (51 tools + 30 publish +
37 git + 4 OCP-free probes + 1 gate verdict-shape).
