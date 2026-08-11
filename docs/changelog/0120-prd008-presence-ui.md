# 0120 — PRD-008 slice 9: avatars in context, claim chips, the override dialog, the inbox

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude (Opus 5)

## Summary
Presence and claims stop being a roster payload and become part of the
workspace: a dot or an "editing" chip on the part they are about, a chip above
the editor naming who has the file open, a conflict dialog with exactly one
Override button, a mentions inbox with an unread badge, and a comment
affordance on the diff rows PRD-002 stamped for it.

## Changes
- **`frontend/js/tree.js`** — `"presence"` added to this module's `onKeys`, and
  two indicators per part row rendered **from state** (the list is cleared and
  rebuilt on every relevant change, so an indicator poked in imperatively would
  survive exactly one render): an `editing` chip when somebody else holds the
  claim, otherwise a dot per other client whose focus names that part.
  `state.presence` is read directly rather than through `presence.js`, because
  that module already imports `INSTANCE_PALETTE` from here and closing the
  cycle for three lookups would be real fragility for no gain.
- **`frontend/js/presence.js`** — `handleClaim(ev)` merges one `claim_changed`
  into the roster's claims (`holder: null` deletes); `otherClaim(partId)`
  returns a claim held by *somebody else* only; `labelFor(id)` resolves an
  identity to its heartbeat label. The next heartbeat's response remains the
  authority — the event only makes the chip appear sooner.
- **`frontend/js/inspector.js`** — **claims are taken by editing.**
  `syncClaiming()` is the one rule in one place: we claim iff the editor buffer
  is dirty **or** a param write is queued/in flight, wired to
  `editor.onDirtyChange`, to `queueParam` and to the in-flight release. Viewing
  never claims, and the claim is dropped the moment editing stops — a claim
  nobody is using teaches people to click Override reflexively. Plus the
  "<label> is editing" chip above the editor, and a **one-shot** override retry
  in both write paths (`saveScript` and the `patchParams` chain): the arming
  route is single-use and 30 s, so a second 409 is a real refusal.
- **`frontend/js/main.js`**
  - `handleWriteConflict(err, partId)` — the only place the two kinds of 409
    are told apart. `details.overridable && details.claim` → the dialog. A
    plain **turn-lock** 409 (`details.holder`, no `overridable`) keeps today's
    banner and is offered **no** override, because nothing here could grant
    one. On Override it calls `api.overrideClaim` and tells the caller to retry
    once.
  - `#claim-modal` wiring (Escape, backdrop, Cancel/Override → one promise).
  - `case "claim_changed":` and `case "notification":`, the latter filtered on
    `ev.to === (state.clientId || clientId)` — the bus is a broadcast and the
    client filters, which is what PRD-005 will fix and what the docs say out
    loud.
  - **Lock-badge fix.** `renderLockIndicator` hid a holder called literally
    `"browser"`; since slice 6 our own identity is `browser:<8 hex>`, so the
    badge announced our own turn back to us. It now compares against
    `state.clientId || clientId` and shows the holder's presence **label**.
    (Flagged as a follow-up in changelog 0117; this is it.)
- **`frontend/js/comments.js`** — the notifications drawer: `#notif-btn` with
  an unread `.tb-badge` (hidden entirely when the server has no notification
  routes), a list newest-first, click-to-open-thread which marks that one read,
  "Mark all read", and an empty state that says plainly that identity is
  self-asserted and this inbox is visible to anyone on the machine. Plus
  `showThread(tid)`, which closes whatever modal is covering the inspector and
  expands one thread — the target for both the drawer and the diff chip.
- **`frontend/js/proposals.js`** — `decorateDiffs(pane)` called at the end of
  every Files render (`#prop-pane` is rebuilt on each tab click and on every
  `proposal_changed`, so a once-attached affordance would last one render), and
  `onKeys(["comments"])` re-renders the tab so a new thread shows on its hunk
  at once. `diffBlock` now also stamps `data-file` with the diff path: a
  `proposal_hunk` anchor names the *file* the packet keys its script diffs by,
  which `data-part` alone cannot spell. Existing threads render as a `💬 n`
  chip on the hunk header; the comment affordance is **one shared button moved
  between rows on hover** rather than one per line, because a large diff is
  thousands of rows. Rows before the first `@@` carry `data-hunk="-1"` and are
  deliberately offered nothing.
- **`frontend/js/api.js`** — `overrideClaim(proj, part)`.
- **`frontend/index.html`** — `#notif-btn`/`#notif-count`, `#claim-modal`,
  `#notifications-modal`, `#editor-claim`.
- **`frontend/css/app.css`** — row claim chip and presence dot, the editor
  chip, the dialog meta line, inbox rows, and the two diff-row affordances.

## Files
- `frontend/js/tree.js` — `"presence"` in `onKeys`, claim chip + presence dot
- `frontend/js/presence.js` — `handleClaim`, `otherClaim`, `labelFor`
- `frontend/js/inspector.js` — `syncClaiming`, the editor chip, override retry
- `frontend/js/main.js` — the conflict dialog, two event cases, the lock badge
- `frontend/js/comments.js` — the notifications drawer and `showThread`
- `frontend/js/proposals.js` — `decorateDiffs`, `data-file`
- `frontend/js/api.js` — `overrideClaim`
- `frontend/index.html`, `frontend/css/app.css`

## Verification
`make test-fast` → **1072 passed, 1 skipped**; `make test` → **1371 passed,
1 skipped in 25:50**, byte-identical to the slice-7 baseline (no Python
changed, so this only proves the server side is untouched). `node --check`
clean on every changed module.

**Real browser, two identities** — two Playwright contexts are two
`localStorage` profiles and therefore two `browser:<8 hex>` clients, which is
exactly the AC1/AC5 setup. Scratch server on port 60964, scratch projects dir;
the user's 8630 was never touched. Screenshots under `shots/`:

| shot | what it shows |
|---|---|
| `20-presence-A` | both toolbars carry both avatars: `A "Ada (you) — in the viewport on bracket"` and `G "Grace — …"` |
| `21-claim-chip-B` | Ada's buffer goes dirty → B's tree row shows `editing` ("Ada has bracket open…") and B's editor shows `Ada is editing` |
| `22-override-dialog` | B saves → **409** → "Ada is editing bracket", Cancel / Override, and `holder browser:46716fd2 · human` |
| `23-override-landed` | Override → arm → retry → `saved`, no banner |
| `24-turn-lock-no-override` | a turn taken by `someone-else` → the same save gets **no dialog**, just today's `ConflictError · project is locked by someone-else` |
| `25-notifications` | Ada's `@browser:9e492d1a` mention → B's Inbox badge `1` and the drawer row |
| `26-thread-from-inbox` | clicking it opens the Threads tab and the badge goes to `0`/hidden |
| `27-diff-composer`, `28-hunk-chip` | hover a `data-hunk="0"` row → 💬 → composer `#1 · parts/bracket.py hunk 0` → the chip `💬 1` on the hunk header |
| `29-hunk-thread-focused` | the chip survives an Overview↔Files round trip, and clicking it closes the modal and expands the thread |

Also measured: after A closes its window, B's avatar strip drops to one and
hides itself (`display: none`) — the strip is noise at one client.

**Console: 0 page errors.** Two `console.error` lines, both Chrome's own
network log for the two **intentional** 409s (the claim conflict and the
turn-lock conflict) — the same category changelog 0081 documented for the
proposals slice.

## Notes
- **The claim disappears as soon as B saves**, because the save cleans the
  buffer and the next heartbeat sends `claim: false`, which releases it and
  publishes `claim_changed {holder: null}`. That is the design ("viewing never
  claims"), not a dropped event, but it does mean the tree chip is short-lived
  by construction — it tracks *someone is typing*, not *someone owns this*.
- `publish_claim` at **arming** time still names the previous holder (the steal
  happens later, inside `claim_write`), so the honest chip update comes from
  the second `claim_changed` the guard publishes. Nothing was changed
  server-side for this; it is worth knowing when reading the event stream.
- **No backend change was needed for either slice.** Every gap the UI wanted
  was already answered by slices 1–7: the four-state `resolution`, the
  `proposal` filter on `list_comments`, `claims/override`, the identity-scoped
  inbox, and `data-part`/`data-hunk`/`data-line` on the diff rows.
- Not covered here, and left for slice 11: the assembly-`instance` anchor has
  no create affordance in the UI yet (it is creatable through the tool/REST
  surface and focuses correctly), and comment **attachments** have no browser
  picker — an agent attaches a render from `exports/` and the panel renders the
  chip.
