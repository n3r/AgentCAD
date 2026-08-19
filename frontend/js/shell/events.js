// PRD-026 shell — browser → server UX telemetry (`POST /api/ui/events`).
//
// Three facts about this module, each of them deliberate:
//
//   * It is FIRE-AND-FORGET. Nothing in the UI ever waits for it and nothing
//     ever fails because of it: a rejected promise is swallowed. Telemetry
//     that can break the thing it is measuring is worse than no telemetry.
//   * It filters to the server's allow-list BEFORE posting (`type` ∈ the three
//     known types, payload keys ∈ `view`/`action`/`tool`, strings ≤ 80 chars).
//     The route answers 422 on anything else, and a shell that posts a 422 in
//     the console on every dialog open is a shell people learn to ignore.
//   * `emit` takes BOTH shapes — `emit("palette_executed", {action})` and
//     `emit({type: "dialog_opened", view})` — because `dialogs.setEmitter(fn)`
//     calls its emitter with one event object, and the whole point of the seam
//     is that `dialogs.setEmitter(events.emit)` is the entire wiring.
//
// Node-importable: no top-level DOM, and with no `init()` every `emit` is a
// silent no-op (which is what a test importing this module wants).

const TYPES = new Set(["dialog_opened", "dialog_submitted", "palette_executed"]);
const KEYS = ["view", "action", "tool"];
const MAX_LEN = 80;

let post = null;

/** Point the client at the API. `api.postUiEvent(body)` is the only thing it
 *  uses, so a test can hand in a stub and read what would have been sent. */
export function init(deps) {
  const api = deps && deps.api;
  post = api && typeof api.postUiEvent === "function"
    ? (body) => api.postUiEvent(body)
    : null;
  return emit;
}

/** Build the body the route accepts, or `null` when there is nothing to say.
 *  Pure and exported so the allow-list is testable without a network. */
export function body(typeOrEvent, payload) {
  const event = typeOrEvent && typeof typeOrEvent === "object"
    ? typeOrEvent
    : { type: typeOrEvent, ...(payload || {}) };
  const type = event && event.type;
  if (typeof type !== "string" || !TYPES.has(type)) return null;
  const out = { type };
  for (const key of KEYS) {
    const value = event[key];
    if (typeof value !== "string" || !value) continue;
    out[key] = value.length > MAX_LEN ? value.slice(0, MAX_LEN) : value;
  }
  return out;
}

/** Publish one UX event. Never throws, never returns anything worth awaiting. */
export function emit(typeOrEvent, payload) {
  const out = body(typeOrEvent, payload);
  if (!out || !post) return;
  try {
    const sent = post(out);
    if (sent && typeof sent.catch === "function") sent.catch(() => {});
  } catch {
    /* the UI is not the place to report that telemetry failed */
  }
}

/** Test seam: forget the API again. */
export function reset() {
  post = null;
}

export const __events__ = { body, emit, init, reset, TYPES, KEYS, MAX_LEN };
