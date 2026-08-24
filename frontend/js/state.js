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
  repMode: "full",       // assembly display: "full" | "simplified" (PRD-013 FR8)
  branch: null,          // this client's checked-out branch (null = no branching)
  branches: null,        // branch_list payload's branches[], null when stale
  clientId: null,        // branch_list's `you` — our identity in branch events
  versions: null,        // list_versions payload's versions[]
  merge: null,           // staged merge summary from merge_status, or null
  proposals: null,       // proposal_list payload {proposals, counts}, or null
  proposal: null,        // proposal_get payload for the open detail, or null
  presence: null,        // last heartbeat payload {you, clients, claims, ttl_s}
  comments: null,        // list_comments payload {threads, counts}, or null
  notifications: null,   // list_notifications payload {notifications, unread}
  // PRD-027 navigation. The multi-selection lives BESIDE the scalars above,
  // never instead of them: `selectedPart`/`selectedInstance` stay "the
  // primary" that the inspector, the viewport, comments and presence read, and
  // none of those modules learns about this set. A plain click sets both; a
  // Cmd/Shift click grows the set and leaves the primary where it was.
  selection: new Set(),  // part ids in the sidebar's multi-selection
  selectionAnchor: null, // the row a Shift-range measures from
  treeFilter: "",        // the sidebar filter box's raw query text
  dashboardOpen: false,  // the all-projects pane is covering the workspace
  // PRD-005 slice 8 — role affordances. `identityOrgs`/`identityOrg` are the
  // whoami-extended session's org list and resolved current org (null/empty
  // outside hosted-with-tenancy); `canEdit` is the shell-wide affordance flag
  // `shell/actions.js`'s `context()` reads (`main.js`'s `updateCanEdit`
  // computes it from `whoami.roles[projectName]`, defaulting OPEN whenever a
  // role is unknown — this gates affordances, not writes).
  identityOrgs: [],
  identityOrg: null,
  identityRoles: {},     // whoami's {project: role} in the resolved workspace
  canEdit: true,
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
