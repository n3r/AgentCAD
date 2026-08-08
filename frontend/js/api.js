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

  chat: (project, message) => request("POST", "/api/chat", { project, message }),
  chatHistory: (project) =>
    request("GET", `/api/chat/history?project=${enc(project)}`),

  /** Fetch the ACM1 binary mesh. Resolves {buffer, key}; throws ApiError
   *  (502 with the build error) when the part's script is broken. */
  async getMesh(proj, id) {
    let res;
    try {
      res = await fetch(`/api/projects/${enc(proj)}/parts/${enc(id)}/mesh`);
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
