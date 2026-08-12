# 0124 — PRD-008 second review round: the same mis-pin in the script matcher, a half-undone merge, and four unbounded surfaces

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Claude (Opus 5)

## Summary
A second independent review (Codex, xhigh) of the PRD-008 branch returned
CHANGES-REQUIRED with eleven findings the first round had not reached. Five of
them were already closed by changelog `0123` and were verified as such rather
than re-fixed; the remaining ten are fixed here, each with a failing regression
test written first.

Three of them changed a **stated contract**, and all three are the same kind of
mistake this feature keeps making — *treating "nothing left to compare against"
as "certain"*:

- `find_snippet` skipped the stored context whenever exactly one copy of the
  snippet remained, so deleting the anchored one of two identical lines
  re-pinned the thread onto the unrelated survivor at **confidence 1.0**. This
  is `0123`'s `LONE_AREA_REL` finding again, in the script matcher;
- a scoped undo of a **fast-forward merge** reverted only the source tip and
  left every earlier merged commit standing — a half-undone merge reported as
  a completed undo;
- **presence had no bound of any kind**: no roster cap, no identity-length cap,
  no bucket eviction, and a rotating `X-Agent-Id` bypassed the rate limiter
  outright while growing every broadcast.

## Already fixed — verified, not re-done

Findings 1 (`reset --hard` eating a dirty tree), 4 (the lone *face* candidate),
13 (a re-added mention delivering twice) and 14 (`resolve()` calling itself a
pure read while writing a `.facesig.json` sidecar) are closed by `0123` and
were re-read against the current code before being skipped. Finding 12 is
**partly** closed: the leave beacon still accepts a `client_id` from its body —
it has to, `sendBeacon` cannot set headers — but its blast radius is now one
roster row that the next heartbeat restores, not the leaver's claims. That is
the residual the router's docstring already states, and it is not re-opened
here.

## The fixes

### K2 — `revert` could partially apply on the way out

`git revert --no-commit` succeeded and the `commit` behind it then failed (the
review's scenario: a repository hook rejecting it). The inverse patch stayed
applied and staged while `UndoCursor` caught the error, put the entry back and
told the caller nothing had happened — the exact "never a partial apply"
violation FR14 forbids, only in the direction nobody had looked at.

The post-apply steps are now wrapped, and the rollback learned the one thing it
was missing: `_rollback_revert(path, before)` takes the commit the tree started
on, because a failure *after* the commit landed (a `rev-parse` failing behind
it) would otherwise `reset --hard HEAD` onto the very commit the rollback
exists to remove. It resets tree, index **and** HEAD.

### K3 — script anchors mis-pinned, the same class as the face bug

`find_snippet`'s `if len(hits) <= 1: return hits` meant the stored
`before`/`after` were consulted **only** to break a tie. A lone hit is now
scored too, and must be corroborated by the stored context.

Three deliberate choices, and the third is the honest gap:

- **one side, not both.** A real edit around a moved block routinely rewrites
  the line after it while the lines before it stand; demanding both would
  orphan the ordinary case to catch the rare one;
- **only a side that stored something can corroborate.** The old scoring gave
  an empty `before` a free point on every candidate (`[] == []`), which would
  have made the new gate vacuous for any anchor at the top of a file;
- **an anchor that stored no context at all is not gated.** The top *and*
  bottom of a file, or an anchor written before context existed, has nothing to
  corroborate. That shape keeps the old behavior and is stated in `AGENTS.md`,
  the PRD and `docs/agent-api.md` rather than papered over.

Refusing here is not the end of the line: `_resolve_script_range` falls through
to the tier-2 `difflib` map against the blob at the anchor's own head, which
answers from the real diff instead of from a coincidence.

### K5 — a scoped undo ignored `undo_to`

A fast-forward merge moves the target branch onto a commit whose first parent
belongs to the *source*, which is why PRD-001's D3 fix introduced `undo_to`.
The scoped path — which reverts rather than restores, because a whole-tree
restore would take everyone else's later work with it — reverted `entry["id"]`
alone.

`ProjectHistory.revert` grew a `since` parameter: the range `since..commit`,
inverted into one commit. `_range_base` refuses a base that is malformed,
unknown, equal to the tip or not an ancestor, degrading to the single-commit
revert rather than raising. A range containing a merge commit fails cleanly
(git needs a per-commit mainline it cannot be given for a range) and K2's
rollback puts the tree back — documented in the method rather than pretended
away.

### K6 — post-restart `scope: "mine"` only looked at the head

`log(limit=1)` made "is the newest commit mine?" stand in for "have I got
anything to undo?", so one edit by anybody else on top of the caller's was
enough to hear "nothing of yours to undo" about a commit still sitting
reachable in history. The fallback moved into `UndoCursor._from_log`, which
searches back `FALLBACK_SEARCH = 100` commits under `"mine"` (one git call, the
same order as `UNDO_LIMIT` and the maximum `log()` will hand out) and keeps
`limit=1` under `"any"`, where "the latest snapshot" is the whole of the
question. The `restore ` guard is unchanged in both.

### K7 — merge authorship was lost

`author_of` recognized only `Client:`, while `merge.py` writes `Merged-by:` on
a two-parent merge. So `project_history` reported `author: null` for the one
commit that always has a person behind it, and a post-restart scoped undo could
never select it. **`merge.py` was not touched** — it is PRD-001 code whose exact
commit message is pinned by `tests/test_merge.py` — `author_of` reads both
trailers instead, `Client:` first.

### K8 — a stale armed override could steal a later claim

An arming was consumed only when the retry still hit a conflict. If the holder
released first, the retry succeeded and left the arming sitting there; if the
holder then took the part back inside the 30-second window, the next *ordinary*
write spent it and stole the new claim with no second confirmation.

The arming is now spent by the first write it authorizes and **used** only
against a real conflict — two rules that sound like one and are not. `0123`'s
M4 fix (don't force-steal a claim nobody was defending) is preserved intact;
what it also asserted — that the arming survives such a write — was the bug,
and that assertion is replaced.

### K9 — the claim guard was absent for a window after a registry rebuild

`tools_versioning.install_write_guard` *replaces* `write_guard`, so a
`build_registry` after the app was built left the seam claim-free until the
next heartbeat or override request. The committed test inserted exactly such a
heartbeat before asking about the conflict, which is how it stayed green over a
real hole.

The fix is at the seam that causes it: `install_write_guard` re-installs the
claim wrapper itself, **conditionally on `service.claims` already existing** —
so `checks.py`'s ephemeral service, which PRD-004 pins as ending with
`write_guard is None` and `service.claims is None`, is untouched. The lazy
entry points stay: they are what installs the guard the first time. The test
now asks the question with no intervening claims entry point at all.

### K10 — other-branch anchors were mis-classified

`_elsewhere` ran one level too shallow: it asked "is the parent part gone?"
first and consulted the branch only if it was. A part that exists on both
branches while the *parameter*, the *lines* or the *face* inside it exists on
only one — the ordinary shape of a branch — reported
`orphaned`/`param_removed`: a claim that somebody deleted the target, made from
an anchor that was never about the reader's branch. Every absence verdict in
`_resolve_param`, `_resolve_script_range` and `_resolve_face` now routes
through `_elsewhere` first.

### K11 — presence was unbounded

Four bounds, and the reasoning for each is in the module docstring because none
of them is arbitrary:

- **`MAX_ID_CHARS = 64`**, refused rather than truncated: two ids cut to the
  same 64 characters would be *one* client to the roster, the claims and the
  mentions, and a silent identity merge is a worse answer than an error;
- **`MAX_CLIENTS = 200`**, and a full roster refuses a **new** row rather than
  evicting the oldest. A flood of rotating ids is by construction the
  most-recently-seen rows, so LRU eviction would hand it every real client's
  seat; an incumbent keeps refreshing through the cap because its slot already
  exists, and the ceiling clears itself one TTL after the flood stops;
- **`MAX_BUCKETS = 512`** on the rate limiter: a bucket is minted by the first
  heartbeat under an id, which is *before* the roster gets a say, so without it
  a rotating id leaks a dict entry per beat. Eviction drops buckets refilled to
  full burst first — those are indistinguishable from absent ones, so dropping
  them grants nobody anything — and only then the least recently used.

  > **Corrected by 0125.** This bullet said `MAX_BUCKETS` "is the bound that
  > matters most … without it rotation bypasses the limit outright", and that
  > is not what it does: a rotating identity gets a fresh bucket exactly like a
  > real newcomer, so 5 000 ids used once each got 5 000 heartbeats through
  > with the cap in place. `MAX_BUCKETS` bounds **memory**. The LRU half of the
  > eviction was what made that true — the least recently used bucket under a
  > flood is one somebody just spent tokens from, so dropping it handed that
  > identity a fresh burst — and 0125 removes it: when nothing has refilled
  > there is no room and the beat is throttled, which takes the same 5 000 ids
  > from 5 000 grants to 512.
- the **broadcast** needs no separate bound: a `presence_changed` frame *is*
  the roster.

One deliberate deviation from the review's suggestion, stated because it looks
like a miss: buckets are **not** evicted from the leave beacon. That body is
self-asserted, so it would make "I'm leaving" a way to ask for a fresh burst.
Eviction on *expiry* is subsumed by the full-bucket rule anyway — a row expires
after 45 s and a bucket refills in five.

### K15 — the UI drew a guessed pin

`syncPins` fell back to `thread.anchor.signature.centroid` when
`viewport.faceCentroid` had no answer yet. That is the anchor's **old**
position on geometry that has since changed — wrong exactly when the thread
`moved`, indistinguishable from a located pin, and permanent if the sidecar
fetch fails. The fallback is gone: no centroid, no pin, and `meshChanged()`
re-runs `syncPins` when the map arrives. `viewport.faceCentroid`'s docstring no
longer tells callers to fall back.

### K16 — the browser acceptance test asserted prose only

`test_ac1_browser_half_evidence_is_recorded` checked that changelogs contain
the words "pin", "console" and "override", so deleting `comments.init()`, the
pin overlay or the claim dialog would have left it green. A second test,
`test_ac1_browser_half_is_wired_into_the_shipped_frontend`, reads the shipped
modules and asserts the surfaces are defined and wired: `comments.js` defines
`init` / `meshChanged` / `handleEvent` / `syncPins` / `positionPins` and places
pins through `viewport.faceCentroid`; `main.js` calls `comments.init`,
`comments.meshChanged`, handles `comment_changed` and `claim_changed`, and
calls `api.overrideClaim`. Its docstring says what it does not prove — that
anything renders, that a pin lands in the right place, or that the console is
clean — and leaves the evidence check in place for those.

## Files
- `agentcad/core/history.py` — `_MERGED_BY_TRAILER_RE` and `author_of` reading
  both trailers; `revert(since=…)` + `_range_base`; the post-apply rollback;
  `_rollback_revert(before)`; `UndoCursor._from_log` + `FALLBACK_SEARCH`;
  `revert_since` in `_step`; module and class docstrings
- `agentcad/core/anchors.py` — `find_snippet`'s lone-hit context gate;
  `_elsewhere` before every absence verdict in `_resolve_param`,
  `_resolve_script_range` and `_resolve_face`
- `agentcad/core/locks.py` — the arming is spent on any write it authorizes
  (`_consume`, `check`, `claim_write`)
- `agentcad/core/presence.py` — `MAX_ID_CHARS`/`MAX_CLIENTS`/`MAX_BUCKETS`,
  `_identity`, the roster cap in `touch`, `TokenBucket._evict`/`forget`;
  module docstring rule 5; `ensure_claim_guard`'s docstring
- `agentcad/server/routes_presence.py` — the identity is bounded at the route,
  before the rate limiter allocates a bucket keyed by it; module docstring
- `agentcad/core/tools_versioning.py` — `install_write_guard` re-installs the
  claim wrapper when `service.claims` exists
- `frontend/js/comments.js` — no fallback pin position
- `frontend/js/viewport.js` — `faceCentroid`'s docstring
- `tests/test_undo_authors.py` — the hook-rejected revert, merge authorship +
  post-restart selection, the fast-forward `undo_to` scenario, the deeper
  post-restart search (4 new, plus a `branched` fixture and `_restarted_cursor`)
- `tests/test_anchors.py` — 3 `find_snippet` cases, 2 cross-branch cases
- `tests/test_claims.py` — the stale-arming steal; the rebuild window (1 new,
  2 rewritten)
- `tests/test_presence.py` — rotating identities, the id cap at the registry
  and at the route (3 new)
- `tests/test_prd008_acceptance.py` — the structural AC1 browser test; the AC
  table
- `AGENTS.md`, `docs/agent-api.md`, `docs/architecture.md`,
  `docs/user-guide.md`, `docs/prd/in-progress/PRD-008-review-threads-presence.md`
  — the script-anchor honesty, the presence bounds, the widened cross-branch
  rule, `Merged-by:`, `undo_to`, revert atomicity, the single-use override

## Verification
```
uv run pytest -q tests/test_comments.py tests/test_comments_api.py \
  tests/test_comments_notifications.py tests/test_comments_proposals.py \
  tests/test_anchors.py tests/test_anchors_kernel.py tests/test_claims.py \
  tests/test_presence.py tests/test_undo_authors.py \
  tests/test_prd008_acceptance.py tests/test_history.py tests/test_merge.py \
  tests/test_branches.py
  -> 357 passed in 294.40s (0:04:54)

node --check frontend/js/comments.js frontend/js/viewport.js frontend/js/main.js
  -> clean

make test-fast -> 1120 passed, 1 skipped in 253.86s (0:04:13)
make test      -> 1425 passed, 1 skipped in 1428.62s (0:23:48)
```

(A verification round read the `make test-fast` line as understating the count
by one — 1121 rather than 1120. **The line is right and stays**: 1121 is
`passed + skipped`, and this line reports them separately, the way pytest does.
Checked by arithmetic rather than by argument in 0125: the same target on the
tree one commit later reports 1134 passed, 1 skipped, and that commit adds 16
tests of which 2 are `slow` and so are not in `test-fast` — 1134 − 14 = 1120.)

Baseline before this change was 1411 passed, 1 skipped (changelog 0123). Net
**+14**: 15 test functions added, 3 rewritten in place —
`test_an_armed_override_is_not_spent_when_nothing_would_block` →
`test_an_armed_override_never_force_steals_a_claim_nobody_defended` (its
"survives the write" assertion is K8's bug, and inverts),
`test_the_guard_survives_a_full_build_registry` (its heartbeat-before-the-
question is K9's blind spot), and the AC1 evidence test, which gained a
structural sibling rather than being replaced. No pre-existing test needed a
change beyond those three.

## Notes
- **The browser was not re-driven for K15.** `node --check` passes and the
  change is a deletion of a fallback branch; the pin path itself is unchanged.
  The user-visible consequence — no pin during the window between a rebuild and
  the face map arriving — is documented in the user guide.
- **`merge.py` was not modified.** K7 was fixable entirely in the reader, which
  is the right side of that seam: PRD-001 pins the merge commit message
  byte-for-byte, and adding a second trailer to satisfy a reader would have put
  the risk in the wrong module.
- **The `undo_to` a post-restart fallback cannot have.** That fact lives only in
  the in-memory stack, so a fast-forward merge undone after a restart goes back
  through its first parent. Stated in `UndoCursor`'s docstring; fixing it would
  mean persisting the cursor, which is a different feature.
- **`find_snippet`'s residual gap is real**: an anchor with no stored context at
  all is still ungated. It is a narrow shape (a snippet that is the entire file)
  and closing it would mean inventing evidence rather than checking it.
