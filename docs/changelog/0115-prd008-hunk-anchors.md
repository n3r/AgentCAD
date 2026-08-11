# 0115 — PRD-008 slice 4: `proposal_hunk` anchors, re-mapped by header

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude (Opus 5)

## Summary
The sixth anchor kind was in `ANCHOR_KINDS` and documented in `add_comment`
from slice 1 on, but refused by every public path. It now works, and it answers
the PRD's one open question — *"when a packet regenerates, do hunk-anchored
threads pin to the old hunk or re-map?"* — the way design Decision 8 does:
**re-map by a byte-identical hunk header inside the new generation, orphan
otherwise, and never regenerate a packet in order to resolve a comment.**

The whole surface reads the *persisted* `packet.json` and nothing else.
`service.packets.packet(...)` regenerates a stale packet, which rebuilds
geometry on both sides of the proposal and can move the proposal's own state —
so calling it from a comment read would rewrite the evidence the comment is
about. `anchors.stored_packet` is the one door.

## Changes
- **`agentcad/core/anchors.py` — validation.** `validate_proposal_hunk()`
  checks, against the persisted packet only: the proposals pack is installed
  (it self-disables without git; comments do not), the proposal exists, the
  packet is on disk, `file` is a path in the packet's script diffs, that diff
  is not truncated, and `0 <= hunk < len(hunks)`. Each refusal is a
  `validation_error` carrying what the caller needs to fix it (`files`,
  `hunks`, and for an absent packet the hint *call `proposal_packet` first*).
  It stores `{proposal, file, hunk, hunk_header, generation}` — the header
  byte-for-byte and the generation it was read from — beside the branch/head
  provenance every anchor gets.
- **`agentcad/core/anchors.py` — resolution.** `_resolve_proposal_hunk()`
  implements Decision 8's table:

  | condition | status |
  |---|---|
  | proposals pack absent | `unverified` / `proposals_unavailable` |
  | proposal gone | `orphaned` / `proposal_removed` |
  | `packet.json` gone | `orphaned` / `packet_missing` |
  | packet `frozen`, or the proposal is `merged`/`closed` | `unverified` / `packet_frozen` |
  | file no longer in the diff | `orphaned` / `file_not_in_diff` |
  | same generation, header still at that index | `ok` |
  | new generation, that header present exactly once | `moved` / `hunk_remapped_by_header` |
  | header absent or non-unique | `orphaned` / `hunk_regenerated` |
  | anchor stored no header (hand-edited) | `unverified` / `no_header` |

  Three orderings are deliberate and commented in the code: **frozen wins over
  generation** (the diff a frozen packet describes is history the moment a
  merge lands, and answering `ok` would invite a UI to open a live diff that no
  longer exists); **a new generation is `moved` even at the same index** (a
  generation is one measurement, and a different one measured different
  commits, so `ok` would be a claim nobody checked); and **two matching headers
  orphan**, the same "ambiguity is an orphan, never a guess" rule the face
  matcher runs on. A `merged`/`closed` proposal counts as frozen whether or not
  the flag was written, because `PacketBuilder` refuses to re-measure one.
  Decision 7's cross-branch rule does **not** apply — a proposal is branch-free
  storage that *names* its two branches.
- **New helpers in `anchors.py`:** `stored_packet(service, proj, pid)` (the
  only read of `packet.json`; every failure — no pack, bad id, missing file,
  corrupt JSON — is `None`, answered as a state by the caller) and
  `packet_diffs(packet)` (`{path: script_diff}` over the packet's part rows).
- **`agentcad/core/comments.py`:** `proposal_hunk`'s entry in
  `_ANCHOR_EVIDENCE` is `("hunk_header", "generation")`, so a caller cannot
  assert either — a header a client supplies is not evidence of anything;
  `_validate_proposal_hunk` delegates to `anchors`; and `CommentManager.list`
  gains a `proposal` filter so the proposals UI can fetch exactly its own
  threads in one call.
- **`_anchor`'s `NotImplementedError` scaffold is gone.** Every kind in
  `ANCHOR_KINDS` now has a real validator, so the "registered but not
  supported yet" branch and its `_SUPPORTED` tuple were dead code that looked
  like a safety net.
- **Surface:** `list_comments` takes `proposal` (tool schema + `GET
  /api/projects/{proj}/comments?proposal=`), and both `list_comments` and
  `add_comment` descriptions carry the hunk table in the words an agent has to
  act on, including *address the hunk through `resolution.hunk`, never
  `anchor.hunk`* and *reading a thread never regenerates a packet*.

## Files
- `agentcad/core/anchors.py` — `validate_proposal_hunk`, `_proposal_id`,
  `_proposal_store`, `stored_packet`, `packet_diffs`,
  `_resolve_proposal_hunk`, the `_RESOLVERS` entry, and a fourth trap in the
  module docstring (read the persisted packet, never rebuild one)
- `agentcad/core/comments.py` — evidence keys, the validator delegation, the
  `proposal` list filter, the removed `NotImplementedError` path, docstring
- `agentcad/core/tools_comments.py` — the `proposal` argument, `_HUNKS` in
  both read/write descriptions
- `agentcad/server/routes_comments.py` — the `proposal` query parameter
- `tests/test_comments_proposals.py` (new) — 25 cases in five sections
- `tests/test_comments.py`, `tests/test_comments_api.py` — the two assertions
  slice 4 moves (the "not supported yet" case is gone; `list_comments`'
  schema has one more key)
- `docs/agent-api.md` — the `proposal` argument and a "hunk threads re-map by
  header" paragraph

## Notes
- **Testing shape.** Sections 1–4 of the new module hand-write `packet.json`
  through `ProjectStore._atomic_write` on purpose: the contract under test is
  "whatever is on disk is what we read", and a hand-written packet is the only
  way to pin a *frozen* or a *rewritten* one without a merge and two geometry
  rebuilds. Section 5 is the one `slow` case that runs the real
  `PacketBuilder` — it pins that a real packet names its hunks the way the
  hand-written ones do, and that a regeneration after an unrelated change
  re-maps a thread by header (`moved`, `hunk == 0`).
- **The honesty assertions are explicit.** The module's service is built on a
  kernel client that raises on any request, so `service.kernel.calls == []`
  is asserted rather than assumed; one test monkeypatches
  `service.packets.packet` to explode and then lists comments; another asserts
  the packet's bytes, its `generated` stamp and `proposal.json` are unchanged
  after three listings.
- **Verification:** `uv run pytest tests/test_comments_proposals.py -q` →
  25 passed. `uv run pytest tests/test_comments.py tests/test_comments_api.py
  tests/test_anchors.py tests/test_comments_proposals.py -q` → 120 passed.
  `uv run pytest tests/test_packet.py tests/test_proposals.py -q` → 110
  passed: PRD-002 is a read-only dependency here and stays untouched.
- The UI half (a hover affordance on `div.diff-line` rows with
  `data-hunk >= 0`, re-applied after every `renderDetail()`) is slice 9.
