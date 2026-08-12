# 0118 — PRD-008 slice 7: per-part soft claims at the `write_guard` seam

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude (Opus 5)

## Summary
Two humans can no longer silently clobber each other on the same part. A claim
is taken by *editing* — a heartbeat that says the buffer is dirty, or any
successful part-scoped write — held for 90 s, and enforced at
`ProjectStore.write_guard`, the one seam every persistent write passes through.
A blocked write is a 409 naming the holder and saying `overridable: true`; the
browser arms a single-use 30-second override and retries. **AC5 and AC6 hold**
(`tests/test_claims.py`, 18 tests), and `tests/test_locks.py` passes
**unmodified**, which is AC6's actual gate.

Three decisions carry the whole slice, and all three are about not lying:

- **Claims are human-vs-human only.** An agent writing to a human-claimed part
  proceeds, and does not steal the claim. If it were otherwise, the product's
  flagship loop — human pins a comment on a face, agent fixes it and replies —
  would 409 on the agent's very first write. Agents are governed by turns.
- **The turn holder is never claim-checked** (FR12), and the turn lock still
  decides first, with its existing code path, message and details.
- **Coverage is bounded and said out loud.** Only `write_script` and
  `update_part_entry` are claim-covered. `add_part`, `remove_part`, assembly
  edits, project materials, restore and undo are whole-manifest or
  project-wide, and are turn-locked *only* — there is a test asserting exactly
  that, because pretending a part claim guards project-wide operations would be
  a lie told by a green test.

## Changes
- **`agentcad/core/locks.py` — purely additive; `TurnLock` is untouched.**
  - `ClaimRegistry`, built to `TurnLock`'s shape (one `threading.Lock`, one
    dict, wall-clock TTL, lazy expiry, raise-never-block): `acquire` (never
    steals without `force`, never raises — refusing a write is `check`'s job),
    `release`, `release_all`, `get`, `all`, `arm_override`, `check`,
    `claim_write`.
  - `check(key, part, client_id, *, override=False)` is the rule, stated once:
    a whole-manifest write, our own claim, an expired one, an agent on *either*
    side, an armed override or an enclosing `claim_override()` all pass;
    anything else is `ConflictError(f"{holder} is editing {part}", {"claim":
    {...}, "overridable": True})`.
  - `claim_write` adds the acquisition policy on top: take a free part,
    refresh our own, steal under a spent override, and **touch nothing** when
    the write got through only because one party is an agent.
  - `write_part_var` / `override_var` and the `write_scope` / `claim_override`
    context managers, plus `current_write_part()`. This is how the *part*
    reaches the guard **without changing `write_guard`'s signature**:
    `service.py`'s default lambda, `tools_versioning.install_write_guard` and
    every guard installed in a test keep working byte-identically, because they
    simply do not look.
  - `_kind()` imports `proposals.actor_kind` **lazily** — `proposals` imports
    `locks`, so a module-level import would be a cycle — and the human/agent
    rule stays defined exactly once.
- **`agentcad/core/project.py` — two `with locks.write_scope(part_id):`
  wrappers.** `write_script` wraps its existing body; `update_part_entry`
  becomes a two-line wrapper around the unchanged body, now `_update_part_entry`
  (a rename, not a rewrite, so the diff stays readable). Nothing else in the
  file changes.
- **`agentcad/core/presence.py` — the wiring** (the plan's stated home for it:
  claims and presence are the same fact seen twice, and one route pack owns
  both).
  - `ensure_claims(service)` / `ensure_claim_guard(service)` — the wrapper calls
    the previous guard **first**, so `ensure_checkout` and the turn check keep
    their order and their errors; idempotent by the `_claims_installed`
    function attribute; **lazy**, because tool packs load alphabetically and
    `tools_versioning` (`v`) *replaces* `write_guard` after anything at `c`
    could have wrapped it (risk R7, PRD-004's exact trap). It runs from
    `routes_presence.build_router` and from every claims entry point — the
    `ProposalManager.ensure_branch_guard` precedent.
  - The guard reads `service.claims` on **every call**, never captures it, so
    the plan's documented kill switch (`service.claims = None`) really does
    turn it into a passthrough. Tested.
  - `sync_claim(...)` — `claim: true` takes/refreshes the claim on the focused
    part; anything else releases everything that client held on that key.
    **Viewing never claims**: a claim nobody is using teaches people to click
    Override reflexively.
  - `publish_claim(...)` — `claim_changed {project, part, holder, holder_kind,
    expires_at, overridden_by?}`; `holder: null` on a release. Never
    `project_changed`.
- **`agentcad/server/routes_presence.py`** — the heartbeat now carries claims,
  a leave drops them (a closed window is not still editing), and
  `POST /api/projects/{proj}/claims/override {part}` → `{part, armed_until,
  claim}` arms the single-use override and publishes `claim_changed` with
  `overridden_by`, so taking somebody's part is on the record *before* the
  write lands. The route exists because the two part-write routes live in
  `app.py:203,214` — a core this feature may not edit — and so cannot grow an
  `override` body key.
- **`tests/test_claims.py` (new, 18 tests).** Registry semantics (acquire never
  steals, lazy TTL expiry, human-vs-human, single-use override through both
  entry points, the acquisition policy, `write_scope` unwinding on an
  exception); **AC5** end to end over HTTP (409 naming the holder → arm →
  retry lands → `claim_changed` with `overridden_by`, with part `lid`
  untouched throughout); the params path; an agent writing straight through;
  the turn-holder exemption (and that a plain turn 409 offers **no** override);
  whole-manifest writes; heartbeat claim/release/move; leave; 404/422; and
  **R7** twice — the guard survives a full `build_registry` (it is dropped and
  lazily re-installed) and `checks.py`'s ephemeral service still ends with
  `write_guard is None` and no claims registry at all.

## Files
- `agentcad/core/locks.py` — `ClaimRegistry`, the two contextvars, the two
  context managers, `current_write_part`
- `agentcad/core/project.py` — `write_scope` around the two part-scoped writes
- `agentcad/core/presence.py` — `ensure_claims`, `ensure_claim_guard`,
  `sync_claim`, `publish_claim`
- `agentcad/server/routes_presence.py` — claims on the heartbeat, the override
  route
- `tests/test_claims.py` — new

## Notes
- **Verification.** `uv run pytest tests/test_claims.py -q` → **18 passed**.
  `uv run pytest tests/test_locks.py tests/test_checks_ref.py
  tests/test_branches.py tests/test_project.py tests/test_presence.py -q` →
  **118 passed**. `make test-fast` → **1072 passed, 1 skipped**. `make test` →
  **1371 passed, 1 skipped in 1466.42s** (baseline 1332 + 1; +21 from slice 6
  and +18 here). `tests/test_locks.py` (AC6) and `tests/test_checks_ref.py`
  (R7) are unmodified.
- **Verified against a real server**, not only `TestClient`: `agentcad serve`
  on a scratch project, `browser:aaaaaaaa` heartbeats `{part_id: box, claim:
  true}` (the roster comes back with the claim), `browser:bbbbbbbb`'s
  `PUT …/parts/box` answers `409 {"error": {"message": "browser:aaaaaaaa is
  editing box", "details": {"claim": {…}, "overridable": true}}}`,
  `POST …/claims/override {part: "box"}` returns `armed_until`, and the retry
  is a 200 with the claim now held by `browser:bbbbbbbb`.
- **The tests were checked against a mutation**, not just against green: with
  `locks.write_scope` neutered to a no-op, `test_ac5_…` and
  `test_the_params_path_is_claim_covered_too` fail (409 → 200) while the
  turn-lock test still passes. The claim plumbing is load-bearing in exactly
  the places claimed.
- **The claim-covered call sites, enumerated from the diff** (there are no
  others): `service.update_part` — reached by the `PUT …/parts/{id}` route, the
  `edit_script` tool and `push_face` — and `service.set_params` /
  `update_part_entry`, reached by `PATCH …/params` and `set_params`/
  `set_material`. Merges and `project_restore` write through git and
  `ProjectStore._atomic_write`/`save_manifest` rather than `write_script`, so
  they are turn-locked only, exactly as the design says.
- **Not verified visually.** The conflict/override dialog, the "editing" chip
  and the tree chips are slice 9; today a 409 with `details.overridable` simply
  reaches the browser's existing error toast. The heartbeat already sends
  `claim: false` and `presence.js` exports `setClaiming(on)` for that slice to
  drive.
