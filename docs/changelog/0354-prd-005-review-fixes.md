# 0354 — PRD-005 review fixes (4-way review: 2 Opus lenses, adversarial verifier, Codex xhigh)

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (Opus + Sonnet fixers) / Nikita Fedorov

## Summary
One consolidated fix wave for the findings of four independent security
reviews. The load-bearing find was a **proven remote-code-execution +
total tenant-isolation break** in git sync, confirmed end-to-end by two
reviewers; a second blocker (the chat surface running outside tenancy) was
found by the conformance lens. Both are closed, along with the CI/release
blockers and an auth/audit/frontend cluster.

## Changes — the RCE (BLOCKER, two independent repros)
Git's `verify_path` only guards the name `.git`; AgentCAD's GIT_DIR is
`<project>/.history` inside the work tree, so a pushed commit whose tree
carries `.history/**` (a hook, a config) or a nested `.git` was written
into the live git internals by `checkout -f`, and the planted hook ran as
the **unconfined server user** — an `edit` member of one org reaching
`secret.key` and every other tenant. Closed on every path:
- Server `pre-receive` gains a fourth all-or-nothing rule refusing any
  pushed commit whose tree (over `--not --all`, `-c` combined-diff so a
  merge-introduced path is caught, `--root` for the first push) contains
  a `.history/` path or a `.git` component. `core.hooksPath` is repointed
  to a **server-owned** dir holding only the managed hook — so no
  unmanaged hook name can run and a pushed config redirect is reset, not
  obeyed; `filter.*`/`core.fsmonitor`/`core.sshCommand` reconciled;
  re-asserted before every materialize (not just on mtime change).
- `materialize` refuses to check out a tree with git-internals entries
  (belt for a pre-hook repo) and pins `-c core.hooksPath=/dev/null
  -c core.fsmonitor=false` on every server git call.
- Client (`sync.py`): the same tree refusal before every `checkout -f`,
  `merge`, and the ff `update-ref`; `-c core.hooksPath=/dev/null
  -c core.fsmonitor=false` on every invocation (kills push-a-`post-merge`
  and fsmonitor-on-`git status`); remote-advertised HEAD validated
  through `_REF_RE` (an argv-injection `-evil` branch); clone cleanup +
  token-restore on failure.
- **The FR9 merge path** (a divergent `agentcad pull` → PRD-001 staged
  merge) — the incoming tip AND the merge *result* tree are checked for
  git-internals before materialization (a merge can introduce a path in
  neither parent), aborting the pull unchanged. The FR9 merge path (Fixer 5): the incoming tip AND the merge-result tree (a merge can synthesize a `.history` path in neither parent — proven with a real directory-rename repro) are checked before materialization.

## Changes — chat tenancy (BLOCKER)
`chat.py` dispatched tools via `run_in_executor`, which does not
propagate contextvars, so `tenant_var` was `None` inside chat tool calls:
no role floor (a viewer drove any mutating tool), no audit row, the flat
storage root. Fixed by capturing the tenant at the dispatch site and
setting it inside `_call_tool` beside the client-id — deliberately *not*
`copy_context()`, which would drag the principal var across and
misattribute the audit row to the human who typed. AC6 now drives the real
executor path.

## Changes — CI / release (BLOCKER)
- `ci.yml` (both jobs) + `Dockerfile` gain `--extra cloud` — the passkey
  suite (27 tests) and the shipped image could not do passkeys at all.
- `release.yml`: `github.ref_name` moved out of every `run:` body into
  `env:` (a tag `v9.9.9$(id)` executed in the job holding the signing
  key); `setup-uv` pinned to a SHA in that job. deploy-smoke gains a
  least-privilege `permissions:` block and redacts a token secret it
  echoed on the failure path.

## Changes — RBAC / audit / auth / frontend cluster
- The seven REST route packs mapped a `permission_error` to a 422
  envelope; now `permission_error`→403 / `auth_error`→401 (the floor was
  always enforced — only the wire shape was wrong).
- Audit: the general tap's "is this a mutation" test now asks the floor
  (`floor_of != view`) instead of a name-prefix heuristic (reads were
  logged); `tools_cloud`'s four self-taps deleted (the general tap is a
  strict superset, records refusals too) so no more duplicate rows;
  `token_id`/`credential_id` no longer redacted (every revocation had an
  identical digest); `project_changed` now carries the acting principal.
- `package_from_step`/`validate_package` (host-path fs writes) moved from
  `view` to `edit`.
- Passkeys now **2FA** (`require_user_verification=True`); the
  `login/begin` enumeration oracle closed (no `allowCredentials` leak);
  `login/complete` rate-limited on the shared address bucket; register
  and login challenge stores split so a register flood can't evict a
  stranger's login challenge; monotonic-clock TTLs.
- Frontend: `skills.js` mutations + drawing/thumbnail/beacon URLs now
  carry the workspace via header or a `?workspace=` query fallback
  (`resolve_tenant` reads it below the header and scoped token — a query
  cannot move a scoped token); the thumbnail warmer subscribes through
  the unfiltered bus seam and renders under each event's own tenant
  (it was dead on every hosted instance with orgs).
- Docs corrected against code (a token *can* grant/revoke a role within
  an admin-scoped project — only token minting is person-only; the audit
  tap is wired; the org bootstrap CLI exists; the tenant "sole
  membership" rung; PRD-025 rename note).

## Files
See the commit diff — 4 workflows/Dockerfile, ~15 Python modules, 3 JS,
6 docs, 10 test files (2 new: test_chat_tenancy, test_tenant_resolution,
test_sync_merge_rce), conftest (installs tenancy_wiring in the hosted
fixture — which is what had masked the missing general tap).

## The case-fold re-opening (adversarial re-check → Fixer 6)
A dedicated adversarial re-check DEFEATED the first RCE fix: the predicate `(^|/)\.(git|history)(/|$)` was case-sensitive, but `.History/config` folds to `.history` on a case-insensitive fs (macOS APFS, Windows NTFS), and AgentCAD's own `history._exec` ran git unpinned — full chain to a fired marker. Closed with a **component-wise fold** (strip `::$DATA` ADS suffix; strip git's exact 16-codepoint protectHFS-ignorable set; NFC; rstrip NTFS trailing dots/spaces; casefold; compare to `{.git, .history}`) shared as the single source of truth across sync_server/sync/merge, the hook delegating to a stdlib sidecar with the in-process belt as the authoritative backstop, and a defense-in-depth pin (`-c core.fsmonitor=false -c core.hooksPath=/dev/null`) on `history._exec` — which required adapting one pre-existing test (`test_undo_authors`'s post-apply atomicity check induced its failure via a repo `pre-commit` hook, which the pin now correctly bypasses): the failure is now injected at the commit step, and a companion test asserts the hostile hook is ignored. Verified: the fold is a correct superset of APFS folding (Turkish-İ/ı and fullwidth create distinct entries and are correctly allowed; `.gitignore`/`.gitattributes`/`x.history` allowed); the HFS set equals git's; materialize refuses variants the hook grep misses. A round-2 adversarial re-check re-attacked the new fold and found no landing bypass.

## Notes
Cleared by both security reviewers with live attacks: cross-tenant
read/export/WS/locks, scoped-token redirect, the RBAC floor map over 122
tools, OIDC/passkey forgery, audit SQL-injection and cross-org reads,
fair-scheduling accounting, local-mode no-op. The `0346` changelog's
passkey count (cited 24, was 23) is superseded — the suite is now 27.
`make test` (functional half of the wave) — 7077 passed, 51 skipped (test_server isolation-clean; only the pre-existing prd028 AC6 FEM timeout otherwise). RCE + history regression suites — 227 passed. A final full-suite run on the complete wave is cited in the merge close-out.
