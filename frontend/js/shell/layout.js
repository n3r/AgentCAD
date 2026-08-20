// PRD-026 shell — the layout manager's DOM half: three resizable/collapsible
// regions (`#sidebar`, `#inspector`, `#chat-dock`), a `.resize-handle` per
// boundary, pointer drag + keyboard nudge, and per-workspace persistence
// through `layout_model`. Registers the three toggle actions so they are in
// the palette, the menus and the shortcut table for free (spec §5).

import {
  LIMITS, clamp, maxFor, deserialize, serialize, toggle as toggleModel,
  responsiveDefaults, key as storageKey,
} from "./layout_model.js";

const DIM = { sidebar: "width", inspector: "width", chat: "height" };
const ORIENTATION = { sidebar: "vertical", inspector: "vertical", chat: "horizontal" };
// v0.1's chat-dock collapse key, migrated into the new per-workspace layout
// state on first read and then left alone (spec §5).
const LEGACY_CHAT_KEY = "agentcad.chat.open";

let workspace = "default";
let state = null; // {sidebar:{size,collapsed}, inspector:{...}, chat:{...}}
let elements = {}; // panel -> DOM element
let handles = {}; // panel -> handle element

function viewportSize() {
  return {
    width: typeof window === "undefined" ? 0 : window.innerWidth,
    height: typeof window === "undefined" ? 0 : window.innerHeight,
  };
}

function persist() {
  localStorage.setItem(storageKey(workspace), JSON.stringify(serialize(state)));
}

// Sets the module-level `state` (rather than returning it) so it can call
// `persist()` — which reads `state` via closure — the moment a migration
// actually changes something, not just fold the migrated value into a local
// that would die with the tab.
function loadState() {
  const raw = localStorage.getItem(storageKey(workspace));
  state = deserialize(raw, viewportSize());
  // One-time migration: a stored `agentcad.chat.open` wins over the new
  // key's default (chat closed) but never over an ALREADY-migrated new key
  // — `raw` being present means migration already happened.
  if (raw == null) {
    const legacy = localStorage.getItem(LEGACY_CHAT_KEY);
    if (legacy != null) {
      state = { ...state, chat: { ...state.chat, collapsed: legacy !== "1" } };
      // Durably land the migration NOW: the next line deletes the only
      // other place this preference lived, and nothing else in `init()`
      // is guaranteed to persist (a reload that never touches a panel must
      // not silently lose it).
      persist();
    }
  }
  localStorage.removeItem(LEGACY_CHAT_KEY);
}

function effectiveCollapsed(panel) {
  if (state[panel].collapsed) return true;
  const resp = responsiveDefaults(viewportSize().width);
  if (panel === "inspector") return resp.inspectorCollapsed;
  if (panel === "sidebar") return resp.sidebarCollapsed;
  return false;
}

function applyPanel(panel) {
  const el = elements[panel];
  if (!el) return;
  const collapsed = effectiveCollapsed(panel);
  el.classList.toggle("collapsed", collapsed);
  const dim = DIM[panel];
  // Cleared (not zeroed) when collapsed so the CSS `.collapsed` rule owns the
  // size — an inline style always outranks a class rule, and leaving a
  // stale pixel value there would fight it.
  el.style[dim] = collapsed ? "" : `${state[panel].size}px`;
  const handle = handles[panel];
  if (handle) {
    handle.setAttribute("aria-valuenow", String(state[panel].size));
    handle.setAttribute("aria-expanded", String(!collapsed));
    // Chat's ceiling is a fraction of viewport height, so it moves on
    // resize; sidebar/inspector's are fixed, but re-setting them here too
    // costs nothing and keeps this the one place aria-valuemax is written.
    handle.setAttribute("aria-valuemax", String(maxFor(panel, viewportSize())));
  }
}

function applyAll() {
  for (const panel of Object.keys(LIMITS)) applyPanel(panel);
}

/** Flip one panel's collapsed state and persist it. Exported so `chat.js`'s
 *  own collapse button can call `layout.toggle("chat")` instead of owning a
 *  second copy of the state. */
export function toggle(panel) {
  state = toggleModel(state, panel);
  applyPanel(panel);
  persist();
}

function setSize(panel, size) {
  state = { ...state, [panel]: { ...state[panel], size: clamp(panel, size, viewportSize()) } };
  applyPanel(panel);
}

// ------------------------------------------------------------- resize handle

const NUDGE = 16;

function makeHandle(panel) {
  const el = document.createElement("div");
  el.className = "resize-handle";
  el.setAttribute("role", "separator");
  el.setAttribute("aria-orientation", ORIENTATION[panel]);
  el.setAttribute("tabindex", "0");
  const lim = LIMITS[panel];
  el.setAttribute("aria-valuemin", String(lim.min));
  el.setAttribute("aria-valuemax", String(maxFor(panel, viewportSize())));
  el.dataset.panel = panel;
  if (elements[panel] && elements[panel].id) {
    el.setAttribute("aria-controls", elements[panel].id);
  }

  let dragging = false;
  let startPos = 0;
  let startSize = 0;
  const axis = panel === "chat" ? "clientY" : "clientX";
  // Sidebar grows to the RIGHT as the pointer moves right; inspector grows
  // to the LEFT (it sits on the right edge); chat grows UPWARD.
  const sign = panel === "inspector" ? -1 : panel === "chat" ? -1 : 1;

  el.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    dragging = true;
    startPos = e[axis];
    startSize = state[panel].size;
    try { el.setPointerCapture(e.pointerId); } catch { /* best effort */ }
    el.classList.add("dragging");
    e.preventDefault();
  });
  el.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const delta = (e[axis] - startPos) * sign;
    setSize(panel, startSize + delta);
  });
  const endDrag = (e) => {
    if (!dragging) return;
    dragging = false;
    el.classList.remove("dragging");
    try { el.releasePointerCapture(e.pointerId); } catch { /* already gone */ }
    persist();
  };
  el.addEventListener("pointerup", endDrag);
  el.addEventListener("pointercancel", endDrag);

  el.addEventListener("dblclick", () => toggle(panel));
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      toggle(panel);
      return;
    }
    const growKey = panel === "chat" ? "ArrowUp" : (panel === "inspector" ? "ArrowLeft" : "ArrowRight");
    const shrinkKey = panel === "chat" ? "ArrowDown" : (panel === "inspector" ? "ArrowRight" : "ArrowLeft");
    if (e.key === growKey) {
      e.preventDefault();
      setSize(panel, state[panel].size + NUDGE);
      persist();
    } else if (e.key === shrinkKey) {
      e.preventDefault();
      setSize(panel, state[panel].size - NUDGE);
      persist();
    }
  });
  return el;
}

function insertHandles() {
  const sidebarHandle = makeHandle("sidebar");
  elements.sidebar.after(sidebarHandle);
  const inspectorHandle = makeHandle("inspector");
  elements.inspector.before(inspectorHandle);
  const chatHandle = makeHandle("chat");
  elements.chat.before(chatHandle);
  handles = { sidebar: sidebarHandle, inspector: inspectorHandle, chat: chatHandle };
}

// ------------------------------------------------------------------ actions

function registerActions(actions) {
  if (!actions) return;
  const A = (spec) => actions.register(spec);
  A({ id: "view.sidebar.toggle", title: "Toggle sidebar", group: "View",
      menu: "view/30", shortcut: "Mod+B", run: () => toggle("sidebar") });
  A({ id: "view.inspector.toggle", title: "Toggle inspector", group: "View",
      menu: "view/31", shortcut: "Shift+Mod+B", run: () => toggle("inspector") });
  A({ id: "view.chat.toggle", title: "Toggle chat dock", group: "View",
      menu: "view/32", shortcut: "Mod+J", run: () => toggle("chat") });
}

// -------------------------------------------------------------------- init

export function init(deps) {
  const d = deps || {};
  workspace = d.workspace || "default";
  elements = {
    sidebar: document.getElementById("sidebar"),
    inspector: document.getElementById("inspector"),
    chat: document.getElementById("chat-dock"),
  };
  loadState();
  insertHandles();
  applyAll();
  registerActions(d.actions);
  if (typeof window !== "undefined") {
    window.addEventListener("resize", applyAll);
  }
  return state;
}

export const __layoutManager__ = { init, toggle };
