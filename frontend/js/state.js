// Tiny pub/sub store. Panels read `state` directly, mutate it only through
// setState, and subscribe to 'change' with the list of keys that changed.

export const state = {
  health: null,          // GET /api/health payload
  chatAvailable: false,
  projects: [],          // [{name, path, n_parts}]
  projectName: null,
  project: null,         // GET /api/projects/{name} payload
  assembly: null,        // GET /api/projects/{name}/assembly payload
  mode: "part",          // 'part' | 'assembly'
  selectedPart: null,    // part id
  selectedInstance: null,
  part: null,            // GET part detail for selectedPart
  rebuilding: new Set(), // part ids with a rebuild in flight
  connected: false,      // websocket state
  materials: null,       // GET /api/materials?project= payload {materials,caveat,...}
  partKinds: {},         // partId -> {kind, source} learned lazily from get_part
  gizmoMode: "translate",// assembly gizmo: "translate" | "rotate"
};

const listeners = new Map();

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}

export function emit(event, payload) {
  const set = listeners.get(event);
  if (!set) return;
  for (const fn of [...set]) {
    try {
      fn(payload);
    } catch (err) {
      console.error(`listener for '${event}' failed`, err);
    }
  }
}

export function setState(patch) {
  Object.assign(state, patch);
  emit("change", Object.keys(patch));
}

// Convenience for subscribers that only care about some keys.
export function onKeys(keys, fn) {
  return on("change", (changed) => {
    if (changed.some((k) => keys.includes(k))) fn(changed);
  });
}
