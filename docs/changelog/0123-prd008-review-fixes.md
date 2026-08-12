# 0123 — PRD-008 code-review fixes: data loss in `revert`, a real mis-pin, and a claim that disabled FR11

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Claude (Opus 5)

## Summary
An independent review of the PRD-008 branch returned CHANGES-REQUIRED with
three reproduced majors and seven minors. All ten are fixed here, each with a
failing regression test written first. Two of them changed a **stated
contract** rather than only an implementation:

- the anchor matcher's advertised invariant — *"never a wrong face, 0
  mis-pins"* — **was not true**. The review reproduced a mis-pin, and
  re-measuring with a stricter ground-truth oracle found the original spike's
  zero was partly an artifact of that oracle. The hole is closed
  (`LONE_AREA_REL`) and every place that made the claim now quotes the measured
  rate instead: **2 mis-pins in 2 693 known-truth faces, 53.9% resolved**;
- a **leave beacon no longer releases claims**, and a claim now expires on its
  90 s TTL instead. The route's docstring promised a blast radius of "one
  roster row"; the code released every claim the named identity held, and the
  identity comes from a body anyone can post.

## The three majors

### M1 — `revert()` deleted the user's uncommitted work

`git revert --no-commit` **refuses to start** when the tree has a local
modification to a file it would touch. The failure path then ran
`revert --abort` *and* an unconditional `git reset --hard HEAD` — so the
rollback discarded an edit the revert had never touched. The user guide
documents editing `parts/<id>.py` in an external editor, and any other client's
`undo {scope: "mine"}` reaches this path, so an unsaved buffer could be
destroyed by somebody else's undo.

Two changes, both in `ProjectHistory.revert`:

- **a dirty tree is refused up front** — `ConflictError`, `reason:
  "uncommitted_changes"`, `paths` naming the tracked files, and a message that
  says what to do. Untracked (`??`) and ignored (`!!`) entries are deliberately
  *not* dirty: `reset --hard` never removes them and the next snapshot adds
  them, so blocking undo on a scratch file would make the guard useless;
- **the rollback became conditional** (`_rollback_revert`): `revert --abort`
  only when git actually opened a sequencer (`git rev-parse --git-path
  REVERT_HEAD` exists), `reset --hard` only when something is still dirty
  afterwards. From a guaranteed-clean starting tree, anything dirty at that
  point is the revert's own doing — which is the only state in which a
  rollback can be honest about what it is undoing. The `already_reverted` exit
  uses the same helper.

### M2 — a lone candidate re-pinned a comment onto a different face

`match_face` ran its ambiguity check as `if len(scored) > 1 and margin <
AMBIGUITY_MARGIN`. With **exactly one** candidate the check never ran at all,
so a face that had been *cut away* re-pinned onto whatever survived inside the
(deliberately generous) `NORMAL_DOT` / `UVW_DIST` radii, and reported
`margin = best_score - 0.0` — near-maximal confidence for evidence nothing had
corroborated. The review's reproduction: a boss widened until its top face has
the same normal and the same normalized position as the plate top beneath it,
then deleted; the thread moved to the plate at confidence 0.87.

`AMBIGUITY_MARGIN` — the constant the whole "orphan, never mis-pin" claim
rested on — is structurally unable to reach that case.

**The fix:** a lone candidate must clear an *absolute* bar of its own,
`LONE_AREA_REL = 0.30`, instead of the `AREA_REL = 0.5` that applies when
rivals exist.

**Why area, and why 0.30.** The face that replaces a destroyed one is *by
construction* at the same normal and normalized position — that is precisely
why it was the only candidate — so area share is the only feature left with
information in it. Measured over the 1 174 correct lone-candidate matches in
the re-run: area-share error runs median 0.025, p90 0.194, p95 0.218, p99
0.322, max 0.432. 0.30 keeps ~98% of them, refuses the 0.434 mis-pin, and costs
1.1 points of overall resolution. Two alternatives were measured and rejected:

- a **score** bar cannot work — correct lone matches score down to 0.7407 and
  the mis-pin scored 0.8697, so any score bar that caught it would orphan a
  fifth of the true matches (sweep: 0.95 → 44.9% resolved, from 53.9%);
- an **absolute-centroid drift** bar (`|Δcentroid| / √area`, both terms in the
  stored signature) is worse still: true pairs run to p90 0.80 / p95 1.71 while
  the mis-pin sits at 0.297, so no bar separates them and a 0.3 bar would cost
  7.8 points.

### The numbers, re-measured

The spike's 91-pair harness was scratch and not committed, so it was rebuilt
(`collect.py`/`collect2.py` + `evaluate2.py`, scratch again) with **one method
change**: the whole ≤2% chain is retained so ground truth can be recomputed
offline, and each mutual-nearest-neighbour hop now needs a **Lowe ratio test**
in both directions (nearest at least 2× nearer than the runner-up). Without it,
plain MNN pairs a face with the wrong near-twin on a repeated-feature part and
16 composed hops turn that into confident *wrong* truth — the first run's
"19 mis-pins" collapsed to 2 once the oracle stopped guessing. An ambiguous hop
now drops the face from the sample, so the oracle costs sample size, never
correctness.

30 chains (4 example projects, 11 parts, up to 3 numeric parameters each),
3 102 face pairs at +1%/+10%/+30%, 2 693 with a truth mapping. **With the
lone-candidate gate:**

| change | resolved | orphaned | **mis-pinned** |
|---|---|---|---|
| +1%  | 540 / 949 (56.9%) | 407 | **2** |
| +10% | 486 / 879 (55.3%) | 393 | 0 |
| +30% | 455 / 865 (52.6%) | 410 | 0 |
| all  | 1 451 / 2 693 (**53.9%**) | 1 240 | **2** |

Plus 409 faces with no truth mapping (146 orphaned, 263 matched) — the oracle
drops what it cannot pair unambiguously, so most of those still exist.

**Two mis-pins remain and are not tuned away.** Both are `rocketry/nozzle` at
+1% on `chamber_d`: a body of revolution whose seam faces the oracle pairs one
way and the matcher another, at dot ≈ 1.0 and area error ≈ 0.01. One has three
candidates and clears the ambiguity margin; one is lone and clears the area
gate. Neither is reachable by any tolerance that would not also orphan hundreds
of true pairs, and the data cannot say which of the two answers is right. So
the contract is now **"orphan rather than guess; mis-pins are rare (2 in 2 693
measured), not impossible"**, and every surface that said "never" was narrowed:
`anchors.py` (module docstring + the tolerance block), `AGENTS.md`,
`tools_comments._FACE_ODDS` (the words an agent acts on — it now says to
confirm with `face_info` when the answer decides something expensive),
`docs/agent-api.md`, `docs/architecture.md`, `docs/user-guide.md`, the PRD's
as-built divergence, and `tests/test_prd008_acceptance.py`'s own docstring.

**One product cost, stated rather than hidden.** Any area bar that refuses the
0.434 mis-pin also refuses AC1's fixture, which widened the commented boss from
r=8 to r=11 — a 0.45 move in that face's area share, *beyond the maximum
correct lone match in the whole measurement*. AC1's agent fix is now r=9 (0.20
move), which is both a realistic "make it wider" and inside the measured
envelope. The ceiling — an edit that moves a lone-candidate face's share of the
part's surface area by more than ~30% orphans the thread — is documented in the
user guide next to the two ceilings slices 8–9 found.

### M3 — an agent's write stole the part claim and switched FR11 off

`claim_write` acquired the claim for **any** caller, including agents, and
`check` exempted a conflict when *either* side was an agent. Together: one
agent write took the claim on a part, and from then on `check` returned early
for everyone, so a human's heartbeat could not take that claim and a second
human wrote straight over the first — no conflict, no dialog, no chip — for 90
seconds, refreshed by every further agent write. `docs/agent-api.md`'s table
documents the safe behaviour; the code deviated.

The asymmetry is wanted in **one direction only**, and is now written that way:

- `ClaimRegistry.acquire` **returns `None` for a non-human holder** — agents
  are claim-less. This is the single door claims are taken through, so the rule
  lives there rather than in each policy caller;
- the exemption in the new `_blocking` helper is exactly `caller_kind ==
  "agent" and holder_kind == "human"`. An agent is never blocked by a human
  (the flagship loop must not 409 on the agent's first write); the mirror
  exemption is gone, and is unreachable anyway now that no agent can hold a
  claim.

`check` and `claim_write` are both expressed in terms of `_blocking` plus a
single `_refusal` constructor, so the refusal payload the conflict dialog
renders is built in one place.

## The minors

- **M4** — an armed override was consumed (and force-stole a claim) even when
  no conflict would have occurred. It is now computed only against a real
  `_blocking` result, so a single-use override survives a write that would have
  succeeded anyway.
- **M5** — `MAX_MENTIONS = 8`, matching `MAX_ATTACHMENTS` and for the same
  reason: each deliverable mention mints a `notifications.jsonl` line *and* a
  broadcast WebSocket frame, so one comment could cost ~900 of each. Refused
  (`{max, given}`) rather than truncated, because the body is stored verbatim
  and a silently dropped `@name` would sit in the text notifying nobody. And
  `seq` is now carried forward by `_next_seq` instead of re-reading the file
  per append — the old `_line_count(path) + 1` made a burst of appends O(n²).
- **M6** — a leave beacon now drops **only** the roster row. `sendBeacon`
  cannot set headers, so the leave names its own `client_id` in the body:
  anybody can send one for anybody. Dropping a row on that basis is harmless
  (the next heartbeat restores it); releasing that identity's *claims* was a
  way to disarm the protection another human was editing under. Claims wait out
  their 90 s TTL, which is what a soft claim is designed around. The router
  docstring's stated blast radius is now the whole blast radius.
- **M7** — an edit no longer re-delivers a mention that was removed and put
  back. "Already delivered" is read from the thread's append-only `audit.jsonl`
  (`_delivered`) instead of from `comment["mentions"]`, which an edit rewrites.
- **M8** — `test_ac6_the_lock_suite_is_unmodified` runs `git diff main...HEAD
  -- tests/test_locks.py` and asserts it is empty (plus the working tree),
  skipping where the question cannot be asked (no git, not a work tree, no
  `main`). It used to assert only that a changelog *said* the suite was
  unmodified. Two named-missing tests were added: revert under a dirty tree,
  and both halves of the post-restart `scope: "mine"` fallback, which is the
  one place `author` comes from a git trailer rather than from the stack. The
  AC9 count-citation test stays an evidence check — recomputing the number
  would mean running the full suite from inside the full suite, and
  `--collect-only` counts cases, not what `make test` reports — but it now also
  requires the *newest* changelog entry to cite a count, so a later entry
  cannot silently contradict it.
- **M9** — the user guide names the upgrade consequence of per-browser
  identities: an existing browser is a new client, loses its per-client branch
  checkout row (and lands on the default branch), and a turn held under the old
  id has to wait out its TTL.
- **M10** — `resolve()`'s "never builds" is now precise about what it does and
  does not touch: no kernel call, no rebuild, no packet regeneration, no write
  to a thread/anchor/manifest/proposal — but it *does* memoize the face table
  as a `<key>.facesig.json` sidecar in `.cache`, so "read-only" would be the
  wrong word and is not used.

## Files
- `agentcad/core/history.py` — `revert` refuses a dirty tree; new
  `_dirty_paths` and `_rollback_revert`; docstring rewritten
- `agentcad/core/locks.py` — `acquire` refuses non-human holders; new
  `_blocking`/`_refusal`; `check` and `claim_write` rebuilt on them; module
  docstring rule 3 rewritten
- `agentcad/core/presence.py` — `sync_claim` handles `acquire`'s `None`
- `agentcad/server/routes_presence.py` — the leave path drops the roster row
  only; module docstring's blast radius corrected
- `agentcad/core/anchors.py` — `LONE_AREA_REL` + the lone-candidate gate in
  `match_face`; the tolerance block re-measured; module docstring and
  `resolve`'s docstring narrowed
- `agentcad/core/comments.py` — `MAX_MENTIONS`, `_delivered`, `_next_seq`
- `agentcad/core/tools_comments.py` — `_FACE_ODDS` re-worded to the measurement
- `tests/test_undo_authors.py` — 3 revert cases (dirty tree, unrelated dirty
  file, untracked file) + 2 post-restart `mine` fallback cases
- `tests/test_claims.py` — agents are claim-less (registry + HTTP), the
  override is not spent without a conflict, the leave test rewritten
- `tests/test_comments_notifications.py` — re-added mention, the mention cap,
  incremental `seq`
- `tests/test_anchors.py` — the lone-candidate bar; the constants test
- `tests/test_anchors_kernel.py` — the review's exact reproduction (`WIDE_BOSS`
  → `WIDE_NO_BOSS`) asserts `orphaned`/`area_mismatch`
- `tests/test_prd008_acceptance.py` — AC6 asks git; AC1's fix is r=9 with the
  ceiling explained; AC9 also checks the newest entry; docstring re-measured
- `tests/test_comments_api.py` — the tool-description contract asserts the
  re-measured rate instead of the superseded one
- `AGENTS.md`, `docs/agent-api.md`, `docs/architecture.md`,
  `docs/user-guide.md`, `docs/prd/in-progress/PRD-008-review-threads-presence.md`
  — the invariant narrowed to the evidence; the upgrade note; the new ceiling

## Verification
The review's three reproductions, after the fixes:

```
repro_revert2.py
  before: 'line1\nCHANGED\nline3\nPRECIOUS UNSAVED WORK\n'
  raised: ConflictError cannot undo 97a3eee4: the project has uncommitted
          changes; save or discard them and try again
          {'commit': '97a3eee4...', 'reason': 'uncommitted_changes',
           'paths': ['parts/a.py'], 'blocked_by': []}
  after : 'line1\nCHANGED\nline3\nPRECIOUS UNSAVED WORK\n'      <- intact

repro_mispin_real.py
  RESOLUTION AFTER THE FACE WAS CUT AWAY:
  {'status': 'orphaned', 'reason': 'area_mismatch',
   'hint': 'the only matching face is a very different size, ...',
   'confidence': 0.8697, 'margin': 0.8697, 'n_faces': 6, ...}

repro_claims.py
  --- an agent touches the same part first ---
    claim after agent write: None
    A's heartbeat acquire -> browser:A          (A wanted the claim)
    B blocked: browser:A is editing box         (FR11 intact)
  --- armed override against an agent's claim ---
    outcome: {..., 'overridden': False}
    override still armed? 1786482098.64          (not spent)
```

> **Superseded by 0124 (K8), and read the last two lines the other way round.**
> "Not spent" was recorded here as the desired outcome, and it is the bug: an
> arming that survives the write it was shown for is authorization for a write
> nobody was asked about — if the holder releases and takes the part back
> inside the 30-second window, the *next* ordinary write finds it still sitting
> there and steals the new claim silently. 0124 made the arming spent by the
> first write it authorizes and *used* only against a real conflict, and
> rewrote `test_an_armed_override_is_not_spent_when_nothing_would_block` into
> `test_an_armed_override_never_force_steals_a_claim_nobody_defended`. 0125
> moved the same consumption in `ClaimRegistry.check`, which had kept the
> spend-first order. The `LONE_AREA_REL` reproduction quoted just above is also
> narrower than it looks — see 0125 for the sizes it does not cover.

```
uv run pytest -q tests/test_comments.py tests/test_comments_api.py \
  tests/test_comments_notifications.py tests/test_comments_proposals.py \
  tests/test_anchors.py tests/test_anchors_kernel.py tests/test_claims.py \
  tests/test_presence.py tests/test_undo_authors.py \
  tests/test_prd008_acceptance.py
  -> 228 passed in 92.61s (0:01:32)

make test-fast -> 1107 passed, 1 skipped in 246.41s (0:04:06)
make test      -> 1411 passed, 1 skipped in 1431.79s (0:23:52)
```

Baseline before this change was 1398 passed, 1 skipped. Net **+13**: 15 test
functions added, 2 replaced in place (`test_leaving_releases_every_claim` →
`test_leaving_drops_the_roster_row_and_leaves_the_claims_to_the_ttl`, whose
assertion inverts with M6; `test_ac6_the_lock_suite_is_unmodified_evidence` →
`test_ac6_the_lock_suite_is_unmodified`, which now asks git).

One pre-existing test failed on the first full run and was updated rather than
worked around: `test_the_descriptions_state_the_honest_anchor_contract` pinned
the *old* face-match rate ("two times in three") in the `list_comments` tool
description. A tool description is the worst possible place for a superseded
number to survive, so it now asserts the new one — "about half", the sample
size, "rare but not impossible", and the instruction to confirm with
`face_info`.

## Notes
- **The `LONE_AREA_REL` trade is real and one-directional.** It buys the
  cut-away-face class at the cost of orphaning a lone-candidate face whose
  area share moves more than 30%. That is the safe direction, and the module's
  standing rule — never loosen a tolerance to make a pin appear — is unchanged;
  this is the first time it has been *tightened*.
- **Two mis-pins remain** (`rocketry/nozzle`, +1% `chamber_d`). They are
  reported everywhere the rate is quoted rather than being designed around. A
  future signature with a genuinely independent feature — a loop/edge count, or
  a UV-parameter fingerprint — is the way to attack them; a tolerance is not.
- **The claim model is simpler after M3, not more complex**: agents have no
  claims at all, so there is one rule ("a claim binds two humans") instead of
  two exemptions that interacted badly.
- The measurement harness is scratch again, and deliberately: it needs ~600
  kernel builds and 4 MB of intermediate JSON. Every number it produced is
  transcribed into `anchors.py`'s tolerance block, which is where a future
  reader will look.
