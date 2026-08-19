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
    // Hosted mode: a session that expired, was revoked, or belongs to an
    // account an admin just disabled. Announced from the ONE funnel every
    // call already passes through, so exactly one place knows — main.js
    // listens and swaps the workbench for the sign-in view. Local mode never
    // emits it, because local mode never answers 401.
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      window.dispatchEvent(new CustomEvent("agentcad:unauthenticated"));
    }
    throw new ApiError(res.status, payload);
  }
  return res.json();
}

export const api = {
  health: () => request("GET", "/api/health"),

  // ---- identity (hosted mode; 404 in local mode) ---------------------------
  session: () => request("GET", "/api/auth/session"),
  login: (handle, password) =>
    request("POST", "/api/auth/login", { handle, password }),
  logout: () => request("POST", "/api/auth/logout"),
  enrolInfo: (token) => request("GET", `/api/auth/enrol/${enc(token)}`),
  enrol: (token, password) =>
    request("POST", `/api/auth/enrol/${enc(token)}`, { password }),

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
  /** `params`: {config?, dim_table?} — the GET regenerates the sheet, so the
   *  configuration and the dimension table have to ride it too or the preview
   *  would show a base sheet the POST did not write. */
  drawingSvgUrl: (proj, id, params) =>
    `/api/projects/${enc(proj)}/parts/${enc(id)}/drawing.svg${query(params)}`,
  /** Same contract as `drawingSvgUrl`, for the PDF twin route (PRD-014 FR11). */
  drawingPdfUrl: (proj, id, params) =>
    `/api/projects/${enc(proj)}/parts/${enc(id)}/drawing.pdf${query(params)}`,

  // ---- configurations (PRD-012) ----
  // A configuration is a named parameter set declared on the part; `label` is
  // its display name and the name itself is lowercase. Every route below is a
  // registry passthrough, so a refusal is an ApiError (422/404/409) — a red
  // matrix ROW, by contrast, is payload (`ok: false`) at HTTP 200.
  /** `{parts: [{part_id, configs, active_config, diverged, diverged_params,
   *  referrers}]}` — the whole project, or one part. */
  listConfigs: (proj, id) =>
    id
      ? request("GET", `/api/projects/${enc(proj)}/parts/${enc(id)}/configs`)
      : request("GET", `/api/projects/${enc(proj)}/configs`),
  /** Load a configuration. Switching to a DIFFERENT one clears the part's
   *  explicit overrides unless `keepOverrides`; re-selecting the active one is
   *  a no-op for them. A null config is a 422 here — clearActiveConfig is the
   *  verb for "back to base". */
  setActiveConfig: (proj, id, config, keepOverrides) =>
    request("PUT", `/api/projects/${enc(proj)}/parts/${enc(id)}/active-config`, {
      config,
      keep_overrides: !!keepOverrides,
    }),
  clearActiveConfig: (proj, id) =>
    request("DELETE", `/api/projects/${enc(proj)}/parts/${enc(id)}/active-config`),
  /** body: {part_id?, configs?} — one part answers {part_id, configs: [rows]},
   *  the project answers {parts: [...]}; either may carry `warnings`. */
  buildConfigs: (proj, body) =>
    request("POST", `/api/projects/${enc(proj)}/configs/build`, body || {}),
  /** Bind an assembly instance to a configuration. `null` UNBINDS, so the key
   *  is sent either way — the route forwards on `"config" in body`, and an
   *  omitted key would read as "no argument at all". */
  setInstanceConfig: (proj, iid, config) =>
    request(
      "PATCH",
      `/api/projects/${enc(proj)}/assembly/instances/${enc(iid)}/config`,
      { config: config == null ? null : config }
    ),

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

  // ---- share links & the customizer (PRD-007; hosted mode, 404 in local) ----
  /** body: {project, part_id, scope?, ref?, customizer?, exports?,
   *  show_script?, expires_days?} → {url, pub_id} (the URL is shown once). */
  shareCreate: (body) => request("POST", "/api/share", body),
  shareList: (project) =>
    request("GET", `/api/share?project=${enc(project)}`),
  shareRevoke: (pubId) => request("DELETE", `/api/share/${enc(pubId)}`),

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

  // ---- package registry (the Library dialog) ----
  // Every failure a package operation can produce is a REFUSAL — an
  // unresolvable name, a tampered cache, a part_id already in the project —
  // so unlike the merge and proposal routes there is no {error} body at
  // HTTP 200 to check for here. 404/422/409 throw ApiError and that is all.
  /** `filters`: {query?, index?, keywords?, standards?, limit?}. `keywords`
   *  and `standards` go out as comma-separated lists. */
  searchPackages: (filters) =>
    request("GET", `/api/packages/search${query({
      ...filters,
      keywords: filters && filters.keywords ? filters.keywords.join(",") : null,
      standards: filters && filters.standards
        ? filters.standards.join(",") : null,
    })}`),
  listPackages: (proj) => request("GET", `/api/projects/${enc(proj)}/packages`),
  /** body: {name, version_req?, index?} */
  addPackage: (proj, body) =>
    request("POST", `/api/projects/${enc(proj)}/packages`, body),
  removePackage: (proj, name) =>
    request("DELETE", `/api/projects/${enc(proj)}/packages/${enc(name)}`),
  /** body: {part, part_id, preset?, params?} */
  usePackagePart: (proj, name, body) =>
    request("POST", `/api/projects/${enc(proj)}/packages/${enc(name)}/use`, body),
  /** A preview image URL, served straight out of the index (there is no copy
   *  in the project before the package is installed). */
  packagePreviewUrl: (name, version, path, index) =>
    `/api/packages/${enc(name)}/versions/${enc(version)}/preview${query({
      path, index })}`,

  // ---- public marketplace catalog (PRD-031a; anonymous, scope: public) ----
  // These read the pre-generated `index.json` digest and shipped assets over
  // the ANONYMOUS `/api/public/packages` surface — no session, no kernel except
  // the one customizer variant build. A private index never surfaces here (the
  // dual `scope: public` filter), and every miss is one name-free 404.
  /** The whole public catalog, latest version per package, in name order. */
  marketList: () => request("GET", "/api/public/packages"),
  /** `filters`: {q?, keyword?[], standard?[], license?, param?, param_min?,
   *  param_max?, limit?}. Refresh-free, deterministic, `why` per hit. */
  marketSearch: (filters) => {
    const f = filters || {};
    const qs = new URLSearchParams();
    if (f.q) qs.set("q", f.q);
    if (f.license) qs.set("license", f.license);
    for (const k of f.keyword || []) qs.append("keyword", k);
    for (const s of f.standard || []) qs.append("standard", s);
    if (f.param) qs.set("param", f.param);
    if (f.param_min != null) qs.set("param_min", f.param_min);
    if (f.param_max != null) qs.set("param_max", f.param_max);
    if (f.limit != null) qs.set("limit", f.limit);
    const s = qs.toString();
    return request("GET", `/api/public/packages/search${s ? `?${s}` : ""}`);
  },
  /** Listing summary + all version names. */
  marketPackage: (name) => request("GET", `/api/public/packages/${enc(name)}`),
  /** The full version `_document`: parts digest, previews, gate, license,
   *  disclosure, standards, signatures. */
  marketVersion: (name, version) =>
    request("GET", `/api/public/packages/${enc(name)}/versions/${enc(version)}`),
  /** The digest param list for one part — the slider spec, zero-kernel. */
  marketParams: (name, version, part) =>
    request(
      "GET",
      `/api/public/packages/${enc(name)}/versions/${enc(version)}/params/${enc(part)}`
    ),
  /** A shipped preview image URL, resolved inside the version directory. */
  marketPreviewUrl: (name, version, path) =>
    `/api/public/packages/${enc(name)}/versions/${enc(version)}/preview${query({ path })}`,
  /** The read-only part script, as text (not JSON) — hand-rolled like getMesh.
   *  Resolves the plain text; throws ApiError on a miss. */
  async marketScript(name, version, part) {
    const url =
      `/api/public/packages/${enc(name)}/versions/${enc(version)}/script/${enc(part)}`;
    let res;
    // A public, anonymous read: no per-profile identity header is sent (the
    // server ignores it on the public surface, and an anonymous browse must
    // not carry a browser fingerprint). Keeps the PRD-008 identity count tight.
    try {
      res = await fetch(url);
    } catch {
      throw new ApiError(0, {
        error: { type: "network_error", message: "server unreachable", details: {} },
      });
    }
    if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null));
    return res.text();
  },
  /** Build one bounded variant of a listing part (the ONE market kernel path).
   *  `params` is a plain object of slider values. Resolves {mesh_key, metrics,
   *  warnings, lods, cached}; 429/503/422 throw ApiError (the page degrades). */
  marketVariant: (name, version, part, params) =>
    request(
      "GET",
      `/api/public/packages/${enc(name)}/versions/${enc(version)}/parts/${enc(part)}/variant${query(params)}`
    ),
  /** The `.acm` bytes for a variant already built (never builds). Resolves
   *  {buffer, key}; throws ApiError on an absent/miss key. */
  async marketMesh(name, version, part, key) {
    const url =
      `/api/public/packages/${enc(name)}/versions/${enc(version)}/parts/${enc(part)}/mesh/${enc(key)}`;
    let res;
    // Public anonymous read — no browser identity attached (see marketScript).
    try {
      res = await fetch(url);
    } catch {
      throw new ApiError(0, {
        error: { type: "network_error", message: "server unreachable", details: {} },
      });
    }
    if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null));
    return { buffer: await res.arrayBuffer(), key: res.headers.get("X-Mesh-Key") || key };
  },
  /** A plain download URL for a variant export (a fixed set {step,stl,3mf}); a
   *  plain navigation lets the browser handle Content-Disposition. */
  marketDownloadUrl: (name, version, part, fmt, params) =>
    `/api/public/packages/${enc(name)}/versions/${enc(version)}/parts/${enc(part)}/download/${enc(fmt)}${query(params)}`,

  // ---- generic tool passthrough (used by import) ----
  callTool: (name, body) => request("POST", `/api/tools/${enc(name)}`, body),

  // ---- 2D sketch solve (constraint solver) ----
  /** Solve a constrained sketch. `opts` carries the optional keys the route
   *  whitelists: `initial` (the previous frame's solution — the warm start
   *  that selects the solution *branch*), `drag` ({point, x, y}, a weighted
   *  soft objective), `diagnostics` ("auto" | "full" | "cached") and `emit`
   *  ("function" | "buildline") for server-side code emission.
   *
   *  **This goes through the shared `request()` on purpose.** The drag path
   *  sends one of these per animation frame, and a reused HTTP connection is
   *  the difference between 0.7 ms and 12.6 ms per frame (design Decision
   *  9b). Nothing here may add `Connection: close`, a per-call
   *  `AbortController` teardown or `sendBeacon` — measured in a real browser,
   *  200 consecutive drag frames opened **zero** new TCP connections. */
  solveSketch: (entities, constraints, opts) =>
    request("POST", "/api/sketch/solve", { entities, constraints, ...opts }),

  /** The round-trip sketch blocks in a part script (FR10). Resolves
   *  `{blocks: [{name, status, spec, code, hash, computed_hash, start_line,
   *  end_line, message}], next_name}` — `status` is `ok` | `diverged` |
   *  `unverified`. The hash that decides between them is computed by the same
   *  module that wrote it, so the browser never re-implements it. */
  sketchBlocks: (script) =>
    request("POST", "/api/sketch/blocks", { script }),

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

  /** Fetch a built ACM1 mesh by its CONTENT KEY — the assembly's addressing
   *  (`get_assembly` publishes `mesh_key` per built instance), so two
   *  configurations of one part are two meshes rather than one racing entry.
   *  Resolves {buffer, key, lod}; this route NEVER builds, so a key with
   *  nothing on disk is a 404 and the instance is simply skipped. */
  async getMeshByKey(proj, key, lod) {
    let res;
    const url =
      `/api/projects/${enc(proj)}/meshes/${enc(key)}` +
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
    const servedKey = res.headers.get("X-Mesh-Key") || key;
    const servedLod = res.headers.get("X-Mesh-Lod") || "full";
    const buffer = await res.arrayBuffer();
    return { buffer, key: servedKey, lod: servedLod };
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
