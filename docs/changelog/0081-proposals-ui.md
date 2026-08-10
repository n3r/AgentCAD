# 0081 — the proposals UI: list, detail, diffs and the geometry overlay

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
Slice 5 of PRD-002: the browser half. A toolbar entry with an open-count badge
opens a wide master/detail modal — the proposal list on the left, and on the
right the header, the review/merge actions and five tabs over the generated
review packet (Overview, Files, Geometry, Checks, Audit). `viewport.js` gains
the one additive overlay pair the design spec allowed, so a proposal's
kernel-computed added/removed volumes draw translucent over the real part in
the real viewport. This lands the browser halves of **AC1** (an agent proposes,
a human reviews and merges without touching a terminal) and **AC3** (the drilled
hole shows as a red removed-volume overlay). No Python changed.

## Changes
- **New module `frontend/js/proposals.js`** (`init/open/isOpen/handleEvent/
  refreshCount`), wired like `versions.js`/`merge.js`: close button, backdrop
  click, Escape, a module `busy` flag guarding double-submit, a `loadSeq`
  counter dropping out-of-order responses, and every node built with
  `createElement` + `textContent` — no `innerHTML` for data anywhere.
  - **List:** state-count filter chips (`all N`, then one per non-empty state),
    rows with a state dot, `#id`, title, `source → target`, the author with a
    human/agent badge, a relative age and the review count.
  - **Create:** an inline form (source/target selects seeded from
    `branch_list`, title, description, draft) instead of a chain of `prompt()`s;
    the target defaults to the project's **default** branch, not the current
    one, matching what the service does.
  - **Detail header:** state chip, `source → target (new → old)`, author,
    merge commit when merged, and the actions — Approve · Request changes ·
    Comment · Merge · Close/Reopen · Edit… · Regenerate packet. **Merge is
    disabled while a gate is red with the failing gate's summary in its
    `title`** — a hint, not the enforcement (the service refuses regardless).
  - **Overview:** description, the packet stamp, the summary line, and per
    changed part the metric-delta table (volume/mass/area with Δ and %, the
    per-axis centre-of-mass delta, both bbox sizes) plus the before/after render
    pair. The pair is frame-matched by the packet, so the two `<img>` elements
    are superimposed and **hover cross-fades old → new** — the cheapest possible
    proof that they share one camera.
  - **Files:** the unified diff as **plain DOM**, one `.diff-line` node per line
    (`.diff-add`/`.diff-del`/`.diff-hunk`/`.diff-ctx`/`.diff-meta`) inside an
    `overflow-x: auto` block, each carrying `data-part`/`data-hunk`/`data-line`
    — unused today, and exactly the anchors PRD-008 will hang threads off. Not
    a CodeMirror merge view: the merge addon is not vendored and the frontend
    is offline-only. Plus the PARAMS diff table.
  - **Geometry:** per part the removed/added mm³ and a "Show in viewport"
    action that fetches the ACM1 diff meshes, closes the modal, selects the
    part and overlays them.
  - **Checks:** the gate list with pass/fail/pending/skipped chips, the review
    list (with each review's `stale` flag), and — after a merge the kernel
    blocked — the validation report with a **"Merge anyway (allow_invalid)"**
    button that re-sends the merge with the override.
  - **Audit:** the append-only log as a table (seq, ts, actor + kind, action,
    details).
- **Degradation is rendered as data, never as an error** (FR8): a
  `build.<side>.ok === false` prints that side's script error above the metrics,
  `geom_diff.available === false` prints its reason (including
  `skipped: "mesh"`), `metrics.center_of_mass === null` reads `n/a (mesh)`, and
  the packet's `warnings`/`errors` arrays render as a Warnings section.
- **`frontend/js/viewport.js` — one additive pair**, `showDiffOverlay(partId,
  buffer, key, kind)` / `clearDiffOverlay(kind?)`, modelled exactly on
  `highlightFace`/`clearFaceHighlight`: a separate translucent mesh parented to
  the **scene root, not `contentGroup`**, with its own dispose path, keyed by
  kind so a repeat call is a no-op, dropped by `clearContent()` (so a part
  switch or a rebuild clears it) and recoloured by `setTheme`. `scene` and
  `contentGroup` stay module-private; nothing else in the file moved.
- **`frontend/js/theme.js`** — the two scene palettes gain `diffAdded` /
  `diffRemoved` (the `--ok` / `--err` token values, which THREE cannot read
  from CSS).
- **`frontend/js/merge.js`** — `reportBlock` is now **exported** so the Checks
  tab renders the same validation report as the merge modal instead of growing
  a second one that can drift. No behavior change.
- **`frontend/js/api.js`** — a `// ---- change proposals ----` section:
  `listProposals`, `createProposal`, `getProposal`, `updateProposal`,
  `getPacket`, `reviewProposal`, `mergeProposal`, and a hand-rolled
  `getDiffMesh(url)` that returns the raw ArrayBuffer for the URL the packet
  published. The dual error contract is commented as it is for merge:
  `merge_conflict` arrives at HTTP **200** in `res.error`, a blocked validation
  is a 422 `ApiError` carrying `details.validation`, everything else throws.
- **`frontend/js/main.js`** — `proposals.init(actions)`, `setupProposals()`, a
  `refreshCount()` on project load and on WS reconnect, and one case in
  `handleEvent()`'s switch:
  `case "proposal_changed"` → project guard → `proposals.handleEvent(ev)`.
- **`frontend/js/state.js`** — `proposals` (the list payload) and `proposal`
  (the open detail), mutated only through `setState`.
- **`frontend/index.html`** — a static `#proposals-btn` with a
  `#proposals-count` badge beside the branch switcher, the static
  `#proposals-modal.modal-overlay.hidden` (`.modal.wide`), and a `#diff-legend`
  strip inside `#viewport` (swatches + Clear) — the overlay's only chrome, and
  it must survive the modal closing.
- **`frontend/css/app.css`** — a `/* proposals modal */` block (`prop-*`,
  `diff-*`, `gate-*`, `.tb-badge`, `#diff-legend`) plus
  `.modal.wide { width: min(1100px, 100%); }`, using **only** existing tokens,
  so light mode needed no new variable.

## Files
- `frontend/js/proposals.js` — new: the whole modal
- `frontend/js/viewport.js` — `showDiffOverlay`/`clearDiffOverlay`, cleared by
  `clearContent`, recoloured by `setTheme`
- `frontend/js/theme.js` — `diffAdded`/`diffRemoved` in both scene palettes
- `frontend/js/merge.js` — `reportBlock` exported
- `frontend/js/api.js` — the proposals section + `getDiffMesh`
- `frontend/js/main.js` — init, toolbar wiring, badge refresh, WS case
- `frontend/js/state.js` — `proposals`, `proposal`
- `frontend/index.html` — toolbar button, modal, viewport legend
- `frontend/css/app.css` — the proposals block, `.modal.wide`, `.tb-badge`

## Verification
`make test-fast` → **565 passed, 1 skipped** (no Python changed; this only
proves the server side is untouched). `node --check` clean on every changed
module.

**Real browser session** (headless Chrome via Playwright, a scratch projects
dir, a two-part project, both proposals opened by the agent identity
`chat:main` and reviewed by `browser`) — screenshots in the session scratchpad
under `shots/`:

| shot | what it shows |
|---|---|
| `01-list` | the list with filter chips, state chips and author badges |
| `02-packet-spinner` | the cold-packet spinner ("the first view builds both sides…") |
| `03-overview`, `04-overview-crossfade` | metric deltas + the render pair, hovered |
| `05-files` | the plain-DOM unified diff (+4 −1) and the PARAMS table |
| `06-geometry` | `− 565.5 mm³ removed` / `+ 2,591.5 mm³ added` |
| `09-overlay` | the red removed hole and the green added shell over the real part, with the legend (**AC3's browser half**) |
| `10-approved`, `11-merged-frozen` | approve → merge → `merged`, packet `frozen`, review/regenerate actions gone |
| `12-degraded-overview`, `12b-degraded-geometry` | `new side does not build: ValueError: …` with the rest of the packet intact (**AC7**) |
| `13-blocked-checks` | the blocked kernel report + "Merge anyway (allow_invalid)" |
| `14-red-gate` | two red gates after Request changes; Merge disabled with the reason in its `title` |
| `15-light-theme`, `16-light-files` | the same surfaces in light mode |
| `17-reopen` | closing and reopening the modal restores the proposal being read |
| `18-create-form` | the inline create form (source/target/title/description/draft) |

Measured: the diff-line anchors read `['block', '0', '5']`
(`data-part`/`data-hunk`/`data-line`); the removed volume 565.5 mm³ matches
π·3²·20 = 565.49 for the ⌀6 through hole. **Console: 0 page errors, 0 JS
errors.** The one entry Chrome logs as an "error" is its own network log line
for the *intentional* `422` on `POST …/proposals/2/merge` — the kernel
validation gate refusing the merge, which the UI renders as the blocked report.

## Notes
- **Five tabs, not four.** The design spec named Overview/Files/Geometry/Checks;
  Audit is a fifth because FR14's append-only log has no other surface and the
  slice brief asked for it. Metrics and renders stay inside Overview as
  specified.
- **`merge.js`'s `reportBlock` export is a third additive change** to an
  existing file beyond the plan's "exactly two" (`render_acm(frame=)` and the
  viewport pair). The plan's Step 3 explicitly requires reusing that block
  rather than writing a second one, and reuse needs an export; it is additive
  and no existing caller changed.
- **A `merge_conflict` hands off to the existing conflict modal.** The
  proposals modal closes and `merge.checkStaged()` reopens PRD-001's conflict
  view on the staged merge, with a toast saying to merge the proposal again
  afterwards — the design spec's wording. Completing the merge from *inside*
  that modal would land it outside the proposal (the proposal would stay open);
  routing the conflict modal's "Complete merge" back through `proposal_merge`
  when a proposal is in play is a follow-up, not MVP.
- **The gate dot in the list is derived from the proposal's state**, not from
  its gates: `proposal_list` returns summaries without gates, and evaluating
  them per row would be one `proposal_get` per proposal.
- **Timestamps are naive UTC.** `proposals._now()` and the packet's `generated`
  both use `time.gmtime()` with no zone suffix, so `Date.parse` would read them
  as local time and something written a second ago would show as hours old. The
  UI tags them (`ago()` appends `Z`) rather than changing the stored format —
  but the honest fix is a zone-aware stamp server-side, and slice 6 should
  decide whether to make it one.
- **A merge the kernel blocks leaves PRD-001's staged merge behind**, so the
  next page load reopens the *merge* modal over everything (`merge.checkStaged`
  on project load). That is existing `merge_branch` behavior, not new here, but
  it is now reachable from the proposals flow: after "Merge anyway" is declined,
  the staged merge should be aborted or completed. Worth a line in the user
  guide in slice 6.
- No `uv sync` and no Python edits; the backend needed no gap filled.
