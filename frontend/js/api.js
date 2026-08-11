// All server communication. Same-origin only; errors carry the server's
// structured {type, message, details} payload.

export class ApiError extends Error {
  constructor(status, payload) {
    const err =
      payload && payload.error
        ? payload.error
        : { type: "http_error", message: `HTTP ${status}`, details: {} };
    super(err.message || `HTTP ${status}`);
    this.status = status;
    this.error = err;
  }
}

const enc = encodeURIComponent;

/** `?a=1&b=2` from a plain object, skipping null/undefined. Booleans go out
 *  lowercase because FastAPI's bool parser reads "true"/"false", not "True". */
function query(params) {
  const pairs = Object.entries(params || {})
    .filter(([, v]) => v != null)
    .map(([k, v]) => `${enc(k)}=${enc(typeof v === "boolean" ? String(v) : v)}`);
  return pairs.length ? `?${pairs.join("&")}` : "";
}

// ---- client identity -------------------------------------------------------
// Every request carries one identity, minted once per browser profile and kept
// in localStorage. Two TABS of one profile stay one client — which is what
// keeps the per-client branch checkout behaving as it does today — while two
// browsers, or a normal and an incognito window, are two clients. That is what
// makes presence visible and soft claims meaningful at all: before this, every
// browser in the world was literally the identity `browser`.
//
// It is NOT authentication. The header is self-asserted and the server says so
// in every tool description; a fresh id simply has no checkout row yet and
// lands on the default branch.
const CLIENT_ID_KEY = "agentcad.client_id";
const CLIENT_ID_RE = /^browser:[0-9a-f]{8}$/;

function mintClientId() {
  const bytes = new Uint8Array(4);
  if (globalThis.crypto && globalThis.crypto.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i++) bytes[i] = (Math.random() * 256) | 0;
  }
  return (
    "browser:" +
    [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("")
  );
}

/** This browser's identity, e.g. "browser:7f3a1b2c". */
export const clientId = (() => {
  let stored = null;
  try {
    stored = localStorage.getItem(CLIENT_ID_KEY);
  } catch {
    /* storage disabled (private mode, file://): a per-page id still works */
  }
  if (CLIENT_ID_RE.test(stored || "")) return stored;
  const minted = mintClientId();
  try {
    localStorage.setItem(CLIENT_ID_KEY, minted);
  } catch {
    /* ignore: the id lives for this page only */
  }
  return minted;
})();

async function request(method, path, body) {
  let res;
  const init = { method, headers: { "X-Agent-Id": clientId } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, {
      error: { type: "network_error", message: "server unreachable", details: {} },
    });
  }
  if (!res.ok) {
    let payload = null;
    try {
      payload = await res.json();
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, payload);
  }
  return res.json();
}

export const api = {
  health: () => request("GET", "/api/health"),

  listProjects: () => request("GET", "/api/projects"),
  createProject: (name) => request("POST", "/api/projects", { name }),
  openProject: (path) => request("POST", "/api/projects/open", { path }),
  getProject: (proj) => request("GET", `/api/projects/${enc(proj)}`),

  createPart: (proj, id, label) =>
    request("POST", `/api/projects/${enc(proj)}/parts`, { id, label }),
  getPart: (proj, id) =>
    request("GET", `/api/projects/${enc(proj)}/parts/${enc(id)}`),
  updatePart: (proj, id, body) =>
    request("PUT", `/api/projects/${enc(proj)}/parts/${enc(id)}`, body),
  patchParams: (proj, id, values) =>
    request("PATCH", `/api/projects/${enc(proj)}/parts/${enc(id)}/params`, values),
  deletePart: (proj, id) =>
    request("DELETE", `/api/projects/${enc(proj)}/parts/${enc(id)}`),
  exportPart: (proj, id, format) =>
    request("POST", `/api/projects/${enc(proj)}/parts/${enc(id)}/export`, { format }),

  getAssembly: (proj) => request("GET", `/api/projects/${enc(proj)}/assembly`),
  exportAssembly: (proj, format) =>
    request("POST", `/api/projects/${enc(proj)}/export`, { format }),

  /** Set one instance's transform/color. 409 (ConflictError) if mate-driven. */
  patchInstance: (proj, id, body) =>
    request("PATCH", `/api/projects/${enc(proj)}/assembly/instances/${enc(id)}`, body),

  /** Undo/redo the last project mutation. 409 (ConflictError) when empty. */
  undo: (proj) => request("POST", `/api/projects/${enc(proj)}/undo`),
  redo: (proj) => request("POST", `/api/projects/${enc(proj)}/redo`),

  // ---- materials v2 ----
  listMaterials: (proj) =>
    request("GET", `/api/materials${proj ? `?project=${enc(proj)}` : ""}`),

  // ---- analysis (tier 1) ----
  analyzePart: (proj, id, body) =>
    request("POST", `/api/projects/${enc(proj)}/parts/${enc(id)}/analyze`, body),

  // ---- drawings ----
  generateDrawing: (proj, id, body) =>
    request("POST", `/api/projects/${enc(proj)}/parts/${enc(id)}/drawing`, body),
  drawingSvgUrl: (proj, id) =>
    `/api/projects/${enc(proj)}/parts/${enc(id)}/drawing.svg`,

  // ---- design specs ----
  // The inspector chips need none of these: `specs` rides the part payload
  // that getPart/patchParams already return. They exist for the Phase-2
  // requirement panel and for driving the feature by hand from the console.
  // Everything about a check — fail, skip, a broken predicate — is payload, so
  // a red project is an ordinary 200; only 404/422/409 throw.
  listSpecs: (proj, partId) =>
    request(
      "GET",
      `/api/projects/${enc(proj)}/specs${partId ? `?part_id=${enc(partId)}` : ""}`
    ),
  /** body: {part_id?, ref?} — ref evaluates another BRANCH without switching. */
  runSpecs: (proj, body) =>
    request("POST", `/api/projects/${enc(proj)}/specs/run`, body || {}),
  getProjectSpecs: (proj) =>
    request("GET", `/api/projects/${enc(proj)}/specs/file`),
  setProjectSpecs: (proj, script) =>
    request("PUT", `/api/projects/${enc(proj)}/specs/file`, { script }),

  // ---- project history (undo) ----
  projectHistory: (proj) =>
    request("GET", `/api/projects/${enc(proj)}/history`),
  projectRestore: (proj, commit) =>
    request("POST", `/api/projects/${enc(proj)}/restore`, { commit }),

  // ---- branches / versions / merge ----
  // Like the tool passthrough, these routes answer HTTP 200 with an
  // {"error": …} body for `merge_conflict`, so callers must check res.error in
  // addition to catching ApiError (404/409/422 still throw).
  listBranches: (proj) => request("GET", `/api/projects/${enc(proj)}/branches`),
  createBranch: (proj, name, from) =>
    request("POST", `/api/projects/${enc(proj)}/branches`, { name, from }),
  switchBranch: (proj, name) =>
    request("POST", `/api/projects/${enc(proj)}/branches/switch`, { name }),
  deleteBranch: (proj, name) =>
    request("DELETE", `/api/projects/${enc(proj)}/branches/${enc(name)}`),

  listVersions: (proj) => request("GET", `/api/projects/${enc(proj)}/versions`),
  createVersion: (proj, name, message) =>
    request("POST", `/api/projects/${enc(proj)}/versions`, { name, message }),

  mergeStatus: (proj) => request("GET", `/api/projects/${enc(proj)}/merge`),
  /** body: {source, target?, allow_invalid?} */
  mergeBranch: (proj, body) =>
    request("POST", `/api/projects/${enc(proj)}/merge`, body),
  resolveMerge: (proj, choices) =>
    request("POST", `/api/projects/${enc(proj)}/merge/resolve`, { choices }),
  abortMerge: (proj) => request("POST", `/api/projects/${enc(proj)}/merge/abort`),

  // ---- change proposals ----
  // Same dual error contract as the merge routes above: POST …/merge answers
  // HTTP 200 with an {"error": {"type": "merge_conflict"}} body, so callers
  // must check res.error in addition to catching ApiError. A blocked kernel
  // validation is a 422 ApiError carrying details.validation (retryable with
  // allow_invalid); 404/409 still throw too.
  listProposals: (proj, state) =>
    request(
      "GET",
      `/api/projects/${enc(proj)}/proposals${state ? `?state=${enc(state)}` : ""}`
    ),
  /** body: {source, target?, title, description?, draft?} */
  createProposal: (proj, body) =>
    request("POST", `/api/projects/${enc(proj)}/proposals`, body),
  getProposal: (proj, id) =>
    request("GET", `/api/projects/${enc(proj)}/proposals/${enc(id)}`),
  /** body: {title?, description?, state?} */
  updateProposal: (proj, id, body) =>
    request("PATCH", `/api/projects/${enc(proj)}/proposals/${enc(id)}`, body),
  /** The review packet. Generated lazily on first view (seconds, cold) and
   *  regenerated by this same GET when either branch head moved. */
  getPacket: (proj, id, regenerate) =>
    request(
      "GET",
      `/api/projects/${enc(proj)}/proposals/${enc(id)}/packet` +
        (regenerate ? "?regenerate=1" : "")
    ),
  reviewProposal: (proj, id, verdict, summary) =>
    request("POST", `/api/projects/${enc(proj)}/proposals/${enc(id)}/review`, {
      verdict,
      summary,
    }),
  mergeProposal: (proj, id, allowInvalid) =>
    request("POST", `/api/projects/${enc(proj)}/proposals/${enc(id)}/merge`, {
      allow_invalid: !!allowInvalid,
    }),

  /** Fetch an ACM1 diff mesh by the URL the packet published
   *  (parts[].geom_diff.added_mesh / removed_mesh). Hand-rolled like getMesh:
   *  the body is binary, not JSON. Resolves {buffer}; throws ApiError. */
  async getDiffMesh(url) {
    let res;
    try {
      res = await fetch(url, { headers: { "X-Agent-Id": clientId } });
    } catch {
      throw new ApiError(0, {
        error: { type: "network_error", message: "server unreachable", details: {} },
      });
    }
    if (!res.ok) {
      let payload = null;
      try {
        payload = await res.json();
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, payload);
    }
    return { buffer: await res.arrayBuffer() };
  },

  // ---- generic tool passthrough (used by import) ----
  callTool: (name, body) => request("POST", `/api/tools/${enc(name)}`, body),

  // ---- 2D sketch solve (constraint solver) ----
  solveSketch: (entities, constraints) =>
    request("POST", "/api/sketch/solve", { entities, constraints }),

  /** Raw-body upload of an imported CAD file. Resolves {source, size_bytes};
   *  throws ApiError on rejection (too large, bad extension, empty). */
  async uploadImport(proj, filename, arrayBuffer) {
    let res;
    const url = `/api/projects/${enc(proj)}/imports?filename=${enc(filename)}`;
    try {
      res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Agent-Id": clientId,
        },
        body: arrayBuffer,
      });
    } catch {
      throw new ApiError(0, {
        error: { type: "network_error", message: "server unreachable", details: {} },
      });
    }
    if (!res.ok) {
      let payload = null;
      try {
        payload = await res.json();
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, payload);
    }
    return res.json();
  },

  // ---- presence ----
  // The heartbeat's RESPONSE carries the whole roster, so a client that misses
  // every presence_changed event still converges within one beat. An over-rate
  // beat is a 200 with {throttled: true}, never an error.
  /** body: {part_id?, surface?, label?, claim?, leave?} */
  heartbeat: (proj, body) =>
    request("POST", `/api/projects/${enc(proj)}/presence`, body || {}),
  presence: (proj) => request("GET", `/api/projects/${enc(proj)}/presence`),

  /** Arm a single-use, 30-second claim override for this identity and one
   *  part, then retry the write. It has to be out of band: the two part-write
   *  routes live in app.py, which PRD-008 may not edit, so the override cannot
   *  ride on the write itself. Arming publishes claim_changed. */
  overrideClaim: (proj, part) =>
    request("POST", `/api/projects/${enc(proj)}/claims/override`, { part }),

  // ---- review threads ----
  // Every mutation returns the post-state thread, and `comment_changed` is a
  // POINTER carrying no body — so the UI re-reads with listComments rather
  // than patching locally. That is not laziness: `resolution` (ok / moved /
  // orphaned / unverified) is computed on the server on EVERY read and is
  // never stored, so a stale list is a list that lies about where a thread
  // points.
  /** params: {part_id?, state?, kind?, branch?, proposal?, anchor_status?,
   *  resolve_anchors?} -> {threads, counts: {open, resolved, orphaned}} */
  listComments: (proj, params) =>
    request("GET", `/api/projects/${enc(proj)}/comments${query(params)}`),
  /** body: {anchor|thread, body, attachments?} — exactly one of anchor/thread. */
  addComment: (proj, body) =>
    request("POST", `/api/projects/${enc(proj)}/comments`, body),
  getThread: (proj, tid) =>
    request("GET", `/api/projects/${enc(proj)}/comments/${enc(tid)}`),
  resolveThread: (proj, tid) =>
    request("POST", `/api/projects/${enc(proj)}/comments/${enc(tid)}/resolve`),
  reopenThread: (proj, tid) =>
    request("POST", `/api/projects/${enc(proj)}/comments/${enc(tid)}/reopen`),
  /** Author-only, server-enforced; the root comment can never be deleted. */
  editComment: (proj, tid, cid, body) =>
    request(
      "PATCH",
      `/api/projects/${enc(proj)}/comments/${enc(tid)}/comments/${enc(cid)}`,
      { body }
    ),
  deleteComment: (proj, tid, cid) =>
    request(
      "DELETE",
      `/api/projects/${enc(proj)}/comments/${enc(tid)}/comments/${enc(cid)}`
    ),
  threadAudit: (proj, tid) =>
    request("GET", `/api/projects/${enc(proj)}/comments/${enc(tid)}/audit`),

  // The inbox answers for the identity of the REQUEST — never for an identity
  // passed as an argument — so these take no `to`.
  listNotifications: (proj, unread) =>
    request(
      "GET",
      `/api/projects/${enc(proj)}/notifications${query({ unread })}`
    ),
  /** Omitting `ids` marks every unread one. */
  markNotificationsRead: (proj, ids) =>
    request(
      "POST",
      `/api/projects/${enc(proj)}/notifications/read`,
      ids ? { ids } : {}
    ),

  chat: (project, message) => request("POST", "/api/chat", { project, message }),
  chatHistory: (project) =>
    request("GET", `/api/chat/history?project=${enc(project)}`),

  /** Fetch the ACM1 binary mesh. Resolves {buffer, key, lod}; throws ApiError
   *  (502 with the build error) when the part's script is broken.
   *  Pass lod ("lod1") to request a coarse preview tier — the server falls
   *  back to the full mesh when no tier exists, and `lod` reports which
   *  resolution actually arrived ("lod1" or "full"). */
  async getMesh(proj, id, lod) {
    let res;
    const url =
      `/api/projects/${enc(proj)}/parts/${enc(id)}/mesh` +
      (lod ? `?lod=${enc(lod)}` : "");
    try {
      res = await fetch(url, { headers: { "X-Agent-Id": clientId } });
    } catch {
      throw new ApiError(0, {
        error: { type: "network_error", message: "server unreachable", details: {} },
      });
    }
    if (!res.ok) {
      let payload = null;
      try {
        payload = await res.json();
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, payload);
    }
    const key = res.headers.get("X-Mesh-Key") || "";
    const servedLod = res.headers.get("X-Mesh-Lod") || "full";
    const buffer = await res.arrayBuffer();
    return { buffer, key, lod: servedLod };
  },

  /** Fetch the mesh's triangle->B-rep-face sidecar (one u32 per triangle of
   *  the FULL-resolution mesh). Resolves {buffer, key}; throws ApiError 404
   *  when no sidecar exists (stale cache / reference part). */
  async getMeshFaces(proj, id) {
    let res;
    try {
      res = await fetch(
        `/api/projects/${enc(proj)}/parts/${enc(id)}/mesh/faces`,
        { headers: { "X-Agent-Id": clientId } }
      );
    } catch {
      throw new ApiError(0, {
        error: { type: "network_error", message: "server unreachable", details: {} },
      });
    }
    if (!res.ok) {
      let payload = null;
      try {
        payload = await res.json();
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, payload);
    }
    const key = res.headers.get("X-Mesh-Key") || "";
    const buffer = await res.arrayBuffer();
    return { buffer, key };
  },
};
