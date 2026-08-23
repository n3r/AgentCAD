// PRD-027 FR7 — the virtualized tree's window arithmetic, on its own.
//
// The tree renders a 1 000-row project as one `<ul role="tree">` holding a few
// dozen `<li>`s and two spacer elements. The whole of "which rows" and "how
// tall are the spacers" is this one function, and it is a separate module for
// two reasons: it is the half a node test can prove (a 10 000-row tree renders
// a ≤ 60-row window, and the spacers plus the rendered rows add up to EXACTLY
// the scroll height — a scrollbar that lies about the document height is worse
// than no virtualization at all), and keeping it out of `tree.js` means the
// DOM half never grows arithmetic of its own.
//
// Fixed row height, deliberately: every tree row is 28 px (a 24 px thumbnail
// plus padding), so the window is division rather than a measured-offset table.
// A variable-height tree would need one, and would need a resize observer to
// keep it honest; nothing in this PRD's row design asks for that.
//
// Pure: no DOM, no imports. The export is called `window` because that is what
// it computes (the window of rows in view), and inside THIS module the name
// shadows the browser global harmlessly — nothing here touches it.
//
// **Import it namespaced**: `import * as virtual from "./virtual_model.js"`,
// then `virtual.window({...})`. A named `import { window } from …` shadows the
// browser's `window` for the WHOLE importing module, so `window.innerHeight`
// or `window.addEventListener` in that file would silently call into this one
// and fail in a way that reads like a DOM bug. `tree.js` and anything else
// that needs both must use the namespace form.

/** A finite number, or `fallback`. Every input arrives from a DOM property
 *  (`scrollTop`, `clientHeight`) that can be `undefined` for one frame while
 *  an element is being attached, and `NaN` propagating into `padTop` is a
 *  blank list nobody can debug. */
function num(value, fallback) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** Which rows to render, and how tall the two spacers are.
 *
 *  `{scrollTop, viewportHeight, rowHeight, total, overscan = 8}` →
 *  `{start, end, padTop, padBottom}` with `end` EXCLUSIVE, so the rendered
 *  slice is `rows.slice(start, end)` and the invariant is
 *  `padTop + (end - start) * rowHeight + padBottom === total * rowHeight`.
 *
 *  `overscan` rows are rendered above and below the viewport so a fast scroll
 *  shows rows rather than a gap for the frame before the next `rAF`.
 *
 *  Degenerate inputs answer an EMPTY window rather than guessing: no rows, a
 *  row height of zero (the stylesheet has not loaded), or no arguments at all.
 *  A `scrollTop` past the end is CLAMPED to the last row, so the momentum
 *  overscroll a trackpad produces still renders the end of the list instead of
 *  an empty window with a huge `padTop`.
 */
export function window(opts) {
  const o = opts || {};
  const rowHeight = num(o.rowHeight, 0);
  const total = Math.max(0, Math.floor(num(o.total, 0)));
  if (!(rowHeight > 0) || total === 0) {
    return {start: 0, end: 0, padTop: 0, padBottom: 0};
  }
  const overscan = Math.max(0, Math.floor(num(o.overscan, 8)));
  const scrollTop = Math.max(0, num(o.scrollTop, 0));
  const viewportHeight = Math.max(0, num(o.viewportHeight, 0));
  // `+ 1`: a viewport 21.4 rows tall shows 22 whole rows plus a partial one at
  // whichever end the scroll offset lands on.
  const visible = Math.ceil(viewportHeight / rowHeight) + 1;
  const first = Math.min(Math.floor(scrollTop / rowHeight), total - 1);
  const start = Math.max(0, first - overscan);
  const end = Math.min(total, first + visible + overscan);
  return {start, end,
          padTop: start * rowHeight,
          padBottom: (total - end) * rowHeight};
}

// Test seam — the node round-trip imports this and nothing else.
export const __virtualModel__ = {window};
