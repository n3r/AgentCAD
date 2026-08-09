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

async function request(method, path, body) {
  let res;
  const init = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
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
        headers: { "Content-Type": "application/octet-stream" },
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
      res = await fetch(url);
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
      res = await fetch(`/api/projects/${enc(proj)}/parts/${enc(id)}/mesh/faces`);
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
