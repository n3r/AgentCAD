// PRD-026 shell — the layout manager's pure half: clamps, collapse
// semantics, persistence keys/serialisation. PURE: no DOM, runs in node
// (tests/test_frontend_shell.py).
//
// Three panels, one shape each: `{size, collapsed}`. `sidebar` and
// `inspector` clamp to a fixed pixel range; `chat` clamps to a range whose
// TOP end is a fraction of the viewport height (spec §5's "120-60vh"), which
// is why `clamp`/`deserialize` take a `viewport` ({height}) — a stale
// persisted size from a since-shrunk window must not wedge the dock off the
// bottom of the screen.

export const LIMITS = {
  sidebar: { min: 160, max: 480, def: 216 },
  inspector: { min: 240, max: 640, def: 326 },
  chat: { min: 120, maxFrac: 0.6, def: 264 },
};

const PANELS = Object.keys(LIMITS);

/** The panel's max size in `viewport` — a fixed pixel ceiling for
 *  sidebar/inspector, `maxFrac * viewport.height` for chat (never below its
 *  own `min`, so a tiny/zero-height viewport — a node test, an iframe mid-
 *  layout — cannot invert the range). */
export function maxFor(panel, viewport) {
  const lim = LIMITS[panel];
  if (!lim) return Infinity;
  if (lim.maxFrac != null) {
    const h = (viewport && Number(viewport.height)) || 0;
    return Math.max(lim.min, Math.round(h * lim.maxFrac));
  }
  return lim.max;
}

/** Clamp `size` into `panel`'s range for `viewport`. A non-finite `size`
 *  (`NaN`, `undefined`, a stray string) falls back to the panel's default
 *  BEFORE clamping, so garbage never survives as an out-of-range number and
 *  never survives as `NaN` either. */
export function clamp(panel, size, viewport) {
  const lim = LIMITS[panel];
  if (!lim) throw new Error(`unknown layout panel: ${panel}`);
  // `size == null` catches both `null` (what `NaN` becomes across a
  // JSON round trip — `JSON.stringify` has no way to spell it) and
  // `undefined` (a missing key), so both fall to the default exactly as a
  // literal in-memory `NaN` does.
  const n = size == null ? NaN : Number(size);
  const value = Number.isFinite(n) ? n : lim.def;
  const max = maxFor(panel, viewport);
  return Math.min(max, Math.max(lim.min, value));
}

function defaultPanelState(panel) {
  const lim = LIMITS[panel];
  // The chat dock starts closed today (v0.1's `agentcad.chat.open` default);
  // sidebar/inspector start open. Migrating `agentcad.chat.open` overrides
  // this default with the user's actual preference (layout.js).
  return { size: lim.def, collapsed: panel === "chat" };
}

export function defaultState() {
  const out = {};
  for (const panel of PANELS) out[panel] = defaultPanelState(panel);
  return out;
}

/** `{panel: {size, collapsed}}` -> a plain JSON-safe object. No clamping here
 *  — clamping happens on READ (`deserialize`), so a value that was valid when
 *  written but is now stale (a shrunk window) is fixed up rather than
 *  rejected. */
export function serialize(state) {
  const out = {};
  for (const panel of PANELS) {
    const p = (state && state[panel]) || {};
    out[panel] = { size: Number(p.size), collapsed: !!p.collapsed };
  }
  return out;
}

/** The inverse of `serialize`, tolerant of anything: a raw JSON string (parse
 *  failure -> defaults), `null`/`undefined` (-> defaults), a non-object
 *  (-> defaults), a missing panel (-> that panel's default), an unknown extra
 *  key (dropped), a non-finite/out-of-range `size` (clamped per `clamp`), a
 *  non-boolean `collapsed` (coerced). Nothing a hand-edited or truncated
 *  localStorage value can contain wedges a panel off-screen. */
export function deserialize(json, viewport) {
  let parsed = json;
  if (typeof json === "string") {
    try {
      parsed = JSON.parse(json);
    } catch {
      parsed = null;
    }
  }
  const out = defaultState();
  if (!parsed || typeof parsed !== "object") return out;
  for (const panel of PANELS) {
    const raw = parsed[panel];
    if (!raw || typeof raw !== "object") continue;
    out[panel] = {
      size: clamp(panel, raw.size, viewport),
      collapsed: !!raw.collapsed,
    };
  }
  return out;
}

/** Flip one panel's `collapsed` bit, returning a NEW state object (the
 *  caller decides whether/how to persist it — this is pure). */
export function toggle(state, panel) {
  if (!LIMITS[panel]) throw new Error(`unknown layout panel: ${panel}`);
  const cur = (state && state[panel]) || defaultPanelState(panel);
  return { ...state, [panel]: { ...cur, collapsed: !cur.collapsed } };
}

/** Below 1100px the inspector auto-collapses; below 800px the sidebar does
 *  too (spec §5). Never persisted — `layout.js` applies this on top of the
 *  stored state, not into it. */
export function responsiveDefaults(width) {
  const w = Number(width) || 0;
  return { inspectorCollapsed: w < 1100, sidebarCollapsed: w < 800 };
}

/** The localStorage key for one workspace's layout. */
export function key(workspace) {
  return `agentcad.layout.${workspace}`;
}

// Test seam — the node round-trip imports this and nothing else.
export const __layout__ = {
  LIMITS, maxFor, clamp, defaultState, serialize, deserialize, toggle,
  responsiveDefaults, key,
};
