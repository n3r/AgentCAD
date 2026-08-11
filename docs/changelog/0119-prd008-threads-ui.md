# 0119 — PRD-008 slice 8: the Threads pane, face pins, editor gutter, param badges

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude (Opus 5)

## Summary
The human half of review threads. A fourth inspector tab lists every thread
with an anchor breadcrumb and one of **four** status chips; face threads get a
numbered pin projected over the canvas; `script_range` threads get a CodeMirror
gutter marker; `param` threads get a count badge on the parameter row; and one
composer popover starts a thread from any of those surfaces.

The whole panel is rendered **from `state.comments`**, which is re-read whenever
the server says something moved. That is not a stylistic choice: an anchor's
status is computed on every server read and never stored, so a panel that
patched itself locally would be a panel that lies about where a thread points.

## Changes
- **`frontend/js/comments.js` (new, ~700 lines).**
  - **The four states, drawn as four different things.** `ok` is quiet, `moved`
    is amber and shows both addresses (`bracket · L22–23 → L24–25`), `orphaned`
    is red, and `unverified` is a **dashed** neutral chip whose gloss reads
    "not checked — this is not the same as “fine”". Every non-`ok` row carries
    the server's `reason` and `hint` in the body and in the chip's tooltip,
    plus `confidence`, `margin` and the `against: {branch}` it resolved
    against.
  - **Click-to-focus reads `resolution.face_index` / `resolution.start`, never
    the stored ordinal or line.** Face → select the part, `highlightFace` the
    *resolved* ordinal, fit. `script_range` → Code tab + `editor.revealRange`.
    `param` → Params tab, scroll and flash `div.param[data-param]`. `instance`
    → select it. `proposal_hunk` → open that proposal's Files tab. An
    `orphaned` or `unverified` anchor is **not focusable**: the Show button is
    disabled and its `title` says which state and why.
  - **Pins** are `<button>`s in an absolutely-positioned `#pins` overlay — the
    `#facecard`/`#hud` pattern, no `CSS2DRenderer` vendored. Their world point
    is the centroid of the **resolved** face read off the geometry that is on
    screen (`viewport.faceCentroid`), falling back to the centroid the anchor
    recorded at creation only while the triangle→face sidecar is still loading.
    They are rebuilt when the thread set or the geometry changes and merely
    *repositioned* per frame; an anchor that is not `ok`/`moved` gets no pin at
    all, because there is nowhere honest to draw it.
  - **One composer for every surface**, a fixed popover (`#comment-pop`) rather
    than a panel section: the face card, the gutter, a param row and a
    proposal's diff row are not all reachable while the inspector is visible —
    the proposals modal covers it — and two composers would be two behaviours
    to keep in step. ⌘↵ posts, Escape and an outside click cancel.
  - Replies, resolve/reopen, an author-only delete on non-root comments,
    attachment chips (a missing file renders struck-through with "not on this
    branch", never as an error), and a filter bar carrying `open` / `resolved`
    / `all` counts plus a separate **orphaned** count — orphaned is an anchor's
    status, not a thread state, so it is deliberately not a fourth filter.
  - Everything user-controlled — bodies, ids, labels, reasons, hints, file
    paths — goes through `textContent`.
  - Unsent replies are kept in a `Map` keyed by thread id. The panel is rebuilt
    on **every** `comment_changed`, including somebody else's, so a half-typed
    reply would otherwise vanish under the person typing it. Verified in the
    browser by having an agent identity post while a draft was open.
- **`frontend/js/viewport.js`** — three additions, no change to any existing
  path: `onFrame(fn)` (subscribers run inside the existing animation loop, so
  an HTML overlay moves on exactly the frames the camera does),
  `projectPoint([x,y,z])` (`camera.project()` → CSS pixels, null behind the
  camera, using a size cached by the existing `ResizeObserver` so there is no
  per-frame layout read), and `faceCentroid(partId, faceIndex)` (the mean
  vertex of one B-rep face on the displayed geometry, built like
  `highlightFace`).
- **`frontend/js/editor.js`** — `gutters: ["CodeMirror-linenumbers",
  "agentcad-comments"]` (the linenumbers gutter has to be listed too, or
  declaring gutters at all drops it), `setCommentGutter(rows, onClick)` which
  clears and re-applies the whole gutter from state, `onCommentGutterClick`
  (clicking empty gutter space is how a `script_range` thread starts — the
  selection if it contains that line, else that one line), `selectionRange()`,
  `revealRange()`, and `onDirtyChange()` (slice 9 uses it).
- **`frontend/js/inspector.js`** — `threads` added to the `panes` map (it is
  snapshotted once at init, so an unregistered pane is never shown *or*
  hidden), plus **one decorator seam**: `setParamDecorator(fn)` is called at
  the end of every `render()` — after `buildParamControls` *and* after
  `syncParamValues` — so a badge survives both the full rebuild and the
  values-only sync, and `redecorateParams()` for when the badge data changed
  without the part changing.
- **`frontend/js/api.js`** — a `query()` helper and the review-thread surface:
  `listComments`, `addComment`, `getThread`, `resolveThread`, `reopenThread`,
  `editComment`, `deleteComment`, `threadAudit`, `listNotifications`,
  `markNotificationsRead`.
- **`frontend/js/main.js`** — `comments.init(actions)` after `inspector.init`;
  a `case "comment_changed":` (project-guarded, commented as the pointer it
  is); `comments.scheduleRefresh()` on `rebuild_finished`, **`rebuild_failed`**
  and `project_changed`; `comments.meshChanged()` after `setFaceMap`; a
  "💬 Comment" button on the existing `#facecard`; and
  `actions.openProposal`.
- **`frontend/js/proposals.js`** — `openTo(id, tab)` so a hunk thread can focus
  to its proposal's Files tab (the tab is set *after* `loadDetail`, which
  resets it to Overview on purpose).
- **`frontend/index.html`** — the `threads` tab with a count badge, the
  `#pane-threads` pane, the `#pins` overlay inside `#viewport`, and
  `#comment-pop`.
- **`frontend/css/app.css`** — the threads block: chips, rows, pins, gutter
  markers, param badges and the composer, all in both themes.

## Files
- `frontend/js/comments.js` — new
- `frontend/js/viewport.js` — `onFrame`, `projectPoint`, `faceCentroid`
- `frontend/js/editor.js` — the comment gutter, selection/reveal, dirty hook
- `frontend/js/inspector.js` — `threads` pane, the param decorator seam
- `frontend/js/api.js` — `query()` + the comment/notification routes
- `frontend/js/main.js` — wiring, the `comment_changed` case, the face button
- `frontend/js/proposals.js` — `openTo`
- `frontend/js/state.js` — `comments`, `notifications`
- `frontend/index.html`, `frontend/css/app.css`

## Verification
`make test-fast` → **1072 passed, 1 skipped**; `make test` → **1371 passed,
1 skipped in 25:50**, byte-identical to the slice-7 baseline (no Python
changed, so this only proves the server side is untouched). `node --check`
clean on every changed module.

**Real browser** (headless Chrome via Playwright with SwiftShader, a scratch
server on port 60964 and a scratch projects dir — the user's 8630 was never
touched). Screenshots in the session scratchpad under `shots/`:

| shot | what it shows |
|---|---|
| `01-threads-empty` | the empty pane and its filter bar |
| `02-facecard`, `03-composer` | `planar · 3345.5 mm²` and the composer pre-filled with `bracket · face 2` |
| `04-face-thread-pin` | the thread at `ok` and pin 1 at `translate(479px, 409.7px)` |
| `05-pin-click-expanded` | clicking the pin expands the thread and highlights the face |
| `06-gutter-composer`, `07-gutter-marker` | a `script_range` thread from the gutter (`bracket · L22–23`), marker `●` on line 22 |
| `08-ac3-moved` | **AC3**: two lines inserted above → the row reads `bracket · L22–23 → L24–25`, chip `moved`, `reason: snippet_found_verbatim`, and the gutter marker is on line **24** and now `st-moved` |
| `09-param-badge` | the `boss_d` row badge reading `1`, the other three still empty |
| `10-reply`, `11-resolved`, `12-reopened` | reply, then `Open 2 / Resolved 1`, then `Open 3 / Resolved 0` |
| `13-orphaned` | **AC2's second half**: unchecking `boss` cuts the face away → `orphaned`, `reason: no_candidate`, **0 pins**, Show disabled with "Not focusable: the anchor is orphaned (no_candidate)" |
| `14-unverified` | a deliberately broken script → `unverified` / `part_not_built`, dashed chip |
| `15-focus-param` | Show on a param thread → Params tab, `boss_d` flashed |
| `16-light-threads`, `17-light-gutter` | all four chips in the light theme |

**Console: 0 page errors, 0 console errors, 0 responses ≥ 400** on the slice-8
run. (The `drive8b` run logs one 502 — Chrome's network line for the
*deliberately* broken build that produces the `unverified` state.)

## Notes
- **A matcher characteristic worth knowing, found here, not introduced here.**
  Unchecking `boss` orphaned the *plate's top face* as well as the boss's own
  faces. The top face still exists; what changed is the shape's bounding box
  (z max 22 → 10), which moves that face's `bbox_uvw` by 0.55 — well past
  `UVW_DIST = 0.15`. So **any parameter change that alters the bounding box
  orphans every anchor on the part**, not only anchors on geometry that went
  away. That is the safe direction (orphan, never mis-pin) and the UI reports
  it honestly, but it is a real ceiling on AC2 and it belongs in slice 11's
  documentation rather than in a loosened tolerance.
- Related: a *curved* face (a cylinder's side) orphans on any script edit at
  all, because a closed cylinder's area-weighted normal cancels to ~0 and the
  `NORMAL_DOT` filter then admits no candidate. Also honest, also a documented
  limit rather than a bug to fix by widening a constant.
- The face pin has no depth test — it is HTML over a canvas — so a pin on a
  face that is currently behind other geometry still draws. `projectPoint`
  hides it only when it is behind the *camera*. A depth-tested pin would need
  a read of the depth buffer per pin per frame, which is not worth it at this
  scale.
- `main.js` now re-reads threads on `rebuild_failed` too. Without it a failed
  rebuild left every face anchor showing `ok` while the server had already
  moved them to `unverified` — the exact confusion the four states exist to
  prevent. Caught in the browser, not in a test.
