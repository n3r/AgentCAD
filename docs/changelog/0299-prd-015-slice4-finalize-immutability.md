# 0299 — 2026-08-20 — PRD-015 slice 4: release_finalize, tag pinning, immutability

## Summary

Slice 4 of BOM & release management — `release_finalize` (FR9) and release-record
immutability (FR12): approving the release proposal pins a `release/<rev>` tag,
registers it as referenced, transitions the record to `released`, supersedes the
prior rev, and makes the finalized record append-only.

## Changes

- **`agentcad/core/releases.py`**: `release_finalize(service, project, rev)` —
  idempotent (a `released` rev → deepcopy no-op; a `draft` → `ValidationError`
  "gate has not passed"; a `superseded`/terminal → `ConflictError`). It requires
  the release proposal **approved** (`_release_approvals` mirrors
  `ProposalManager._approvals_gate` exactly — latest counted verdict per actor,
  author self-approve excluded unless policy allows), then: tags
  `release/<rev>` via `branches.tag`, registers the referrer, RMW-writes the
  record to `released` with `approvals: [{principal, ts}]` from the approve
  reviews, marks the immediately-prior released rev `superseded`, and emits
  `release_changed {project, rev, status}`. `_ensure_mutable(record)` guards the
  record (a `released`/`superseded` record is append-only → `conflict_error`
  "branch off its tag to evolve it").
- **`agentcad/core/branches.py`**: a minimal `add_referrer(proj, tag, referrer)`
  appending to `tags.json[tag]["referrers"]` (the FR9 field `tag()` scaffolds
  empty), value-idempotent, under the branch lock + atomic write. All existing
  signatures byte-stable.
- **`agentcad/core/tools_releases.py`**: the `release_finalize` tool.
- **`tests/test_release_finalize.py`** (new, 9): finalize tags + records
  approvals; idempotent; refused before approval (no tag); a draft reports the
  gate; the next rev supersedes the prior; re-finalizing a superseded rev is a
  conflict; **AC5** — a released tag is only editable via `branch_create(from_ref
  ="release/a")` (a tag is not a branch, `switch` refuses; editing on the branch
  succeeds).

## Notes (documented deviation)

The tag is `release/a` (rev **lower-cased**), not `release/A`: the project's
`valid_ref_name`/`_REF_RE` (`^[a-z0-9][a-z0-9._/-]{0,63}$`) forbids uppercase, so
a literal `release/A` is unresolvable — lowercasing avoids touching the shared
ref-name regex (which would broadly affect branches/versioning). The record's
`tag` stores the resolvable lowercased name; the referrer payload keeps the
uppercase rev (`{"release": "A"}`) as plain data.

Immutability (FR12) is mostly **structural** — no write path lands on a tag's
tree (you cannot `switch` to a tag, only `branch_create(from_ref=tag)`), so the
store `write_guard` is unchanged and the record-level `_ensure_mutable` is the
real guard.

Verified: 24 finalize+release tests + 65 branches/versioning tests + proposals;
OCP boundary clean.

`make test` — **4601 passed, 38 skipped** (clean run; the full suite measured
4600 with the one self-referential newest-changelog count guard, green once this
count lands; suite grew 4589→4601 with slice 4's finalize tests).
