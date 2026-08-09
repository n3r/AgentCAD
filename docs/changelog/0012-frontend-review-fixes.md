# 0012 — Apply confirmed review findings (frontend)

- **Commit:** fd4277a
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Frontend correctness, accessibility, and resync fixes from a code review pass:
keeps the UI consistent with agent-driven server-side changes, recovers stuck
chat/rebuild state, guards unsaved edits, and reaches WCAG AA plus keyboard/ARIA
access.

## Changes
- **Agent-driven sync** (`main.js`): `scheduleProjectRefresh` coalesces bursts of
  server mutations (an agent creating/deleting several parts) into one refetch;
  `project_changed`, and `rebuild_started/finished/failed` for parts the client
  doesn't yet know (`partKnown`), now trigger the debounced refresh. On
  `rebuild_finished` for the selected part it refetches full detail so the
  inspector doesn't show stale params/script (the event carries only metrics).
- **Reconnect recovery** (`main.js`): on WebSocket reopen after a drop, stale
  in-flight markers are cleared (`rebuilding` reset, `chat.resetSending()`) before
  resyncing, since terminal events published while the socket was down are gone.
- **Chat state** (`chat.js`): `chat_done` always releases the composer even for a
  project navigated away from; switching projects unlocks a stuck input; new
  `resetSending()` for reconnect. Tool-result payloads (pre-serialized JSON string,
  truncated to 2000 chars) are rendered verbatim via `textContent` — never
  `innerHTML` — with a truncation marker.
- **Unsaved edits** (`main.js`): `confirmDiscardEdits` prompts before discarding
  dirty editor content when switching parts, projects, or selecting an assembly
  instance that swaps in another part; a deleted part needs no prompt.
- **Param snap-back guard** (`inspector.js`): a reference-counted `inflight` map
  keeps `syncParamValues` from snapping an input back to the server's stale value
  between the debounce flush and the PATCH response.
- **HUD** (`main.js`): in part mode the "building…" state reflects only the
  selected part; assembly mode reflects any rebuilding part.
- **Accessibility**: `--faint` is now reserved for decorative/disabled use and
  meaningful text moved to `--dim` for WCAG AA contrast (`app.css`); menu triggers
  get `aria-expanded` kept in sync via `setMenuHidden`, arrow-key navigation
  between menu items; delete buttons are `type="button"` with `aria-label`, stay
  Tab-reachable (opacity instead of `display:none`) with a focus-visible outline,
  and Enter on a button no longer also selects the row (`tree.js`).

## Files
- `frontend/js/main.js` — debounced project refresh, reconnect reset, unsaved-edit guard, per-part HUD, aria-expanded menu handling + arrow-key nav
- `frontend/js/chat.js` — sending-state recovery, safe tool-result rendering
- `frontend/js/inspector.js` — in-flight param snap-back guard
- `frontend/js/tree.js` — keyboard/ARIA fixes for delete buttons and rows
- `frontend/index.html` — `aria-expanded` on menu trigger buttons
- `frontend/css/app.css` — WCAG AA contrast pass; focus-visible delete button

## Notes
Pairs with the backend review fixes in commit 3e92168 (changelog 0011) — several
items here (tool-result truncation, project_changed on CRUD) consume contract
changes made there.
