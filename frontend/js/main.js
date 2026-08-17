// Boot + orchestration: project loading, selection, mesh routing between
// the API and the viewport, WebSocket event stream, toolbar.

import { api, ApiError, clientId } from "./api.js";
import { state, setState, onKeys } from "./state.js";
import * as viewport from "./viewport.js";
import * as tree from "./tree.js";
import * as inspector from "./inspector.js";
import * as editor from "./editor.js";
import * as chat from "./chat.js";
import * as placement from "./placement.js";
import * as drawings from "./drawings.js";
import * as sketcher from "./sketcher.js";
import * as theme from "./theme.js";
import * as versions from "./versions.js";
import * as merge from "./merge.js";
import * as proposals from "./proposals.js";
import * as presence from "./presence.js";
import * as comments from "./comments.js";
import * as library from "./library.js";
import * as auth from "./auth.js";

const ID_RE = /^[a-z][a-z0-9_]{0,39}$/;
const BRANCH_RE = /^[a-z0-9][a-z0-9_/-]{0,63}$/;

const meshBuffers = new Map(); // partId -> {buffer, key, lod} from api.getMesh
let selectSeq = 0;
let lastFittedTarget = null; // part id or "__assembly__"
let localPatchUntil = 0; // suppress our own project_changed echo until this ts
let assemblyRefreshTimer = null;
let projectRefreshTimer = null;
let lockHolder = null; // current turn-lock holder for this project (or null)
let branchSwitchUntil = 0; // suppress the branch_changed echo of our own switch

// ------------------------------------------------------------------ actions

const actions = {
  selectPart,
  selectAssembly,
  addPart,
  deletePart,
  reloadMesh,
  refreshPartDetail,
  markPartState,
  patchInstanceTransform,
  toast,
  refreshProject,
  loadProject,
  // A thread anchored to a proposal hunk focuses by opening that proposal's
  // Files tab; comments.js reaches it through the same actions object every
  // other panel uses rather than importing proposals.js and closing a cycle.
  openProposal: (id, tab) => proposals.openTo(id, tab),
  handleWriteConflict,
};

// ------------------------------------------------------------------ project

async function refreshProjectsList() {
  try {
    const payload = await api.listProjects();
    setState({ projects: payload.projects || [] });
  } catch (err) {
    toast(`Could not list projects: ${err.message}`, "error");
  }
}

async function loadProject(name) {
  if (!confirmDiscardEdits(null)) return; // switching projects drops the editor
  let detail;
  try {
    detail = await api.getProject(name);
  } catch (err) {
    toast(`Could not open ${name}: ${err.message}`, "error");
    return;
  }
  meshBuffers.clear();
  viewport.clear();
  clearFaceSelection();
  lastFittedTarget = null;
  lockHolder = null; // lock state is per project; resync via lock_changed
  renderLockIndicator();
  setState({
    projectName: name,
    project: detail,
    assembly: null,
    part: null,
    selectedPart: null,
    selectedInstance: null,
    mode: "part",
    rebuilding: new Set(),
    partKinds: {},
    materials: null,
  });
  localStorage.setItem("agentcad.project", name);
  document.getElementById("project-name").textContent = name;
  updateEmptyState();
  loadMaterials(name);
  loadBranchState().then((available) => {
    if (available) merge.checkStaged(); // reopen a merge staged before the reload
  });
  proposals.refreshCount(); // hides its own button when there are no routes

  if (detail.parts.length) {
    await selectPart(detail.parts[0].id);
  } else if (detail.assembly.instances.length) {
    await selectAssembly(null);
  }
}

async function refreshProject() {
  if (!state.projectName) return;
  let detail;
  try {
    detail = await api.getProject(state.projectName);
  } catch {
    return;
  }
  setState({ project: detail });
  updateEmptyState();
  loadMaterials(state.projectName); // keep the material picker in sync after edits
  const stillThere = detail.parts.some((p) => p.id === state.selectedPart);
  if (state.mode === "part") {
    if (!stillThere) {
      if (detail.parts.length) await selectPart(detail.parts[0].id);
      else {
        setState({ selectedPart: null, part: null });
        viewport.clear();
        updateHUD();
      }
    } else if (state.selectedPart) {
      refreshPartDetail(state.selectedPart);
      reloadMesh(state.selectedPart);
    }
  } else {
    scheduleAssemblyRefresh();
  }
}

// Coalesce bursts of server-side project mutations (an agent creating or
// deleting several parts in a row) into one project refetch.
function scheduleProjectRefresh() {
  clearTimeout(projectRefreshTimer);
  projectRefreshTimer = setTimeout(() => refreshProject(), 300);
}

function partKnown(partId) {
  return !!(state.project && state.project.parts.some((p) => p.id === partId));
}

// ---------------------------------------------------------------- selection

// True when it is safe to navigate away from the current part's editor.
// Asks the user before discarding unsaved script edits; a cancelled dialog
// keeps the current selection.
function confirmDiscardEdits(nextPartId) {
  if (!state.selectedPart || nextPartId === state.selectedPart) return true;
  if (!state.part || !editor.isDirty()) return true;
  // The edited part no longer exists (deleted): nothing can be saved.
  if (!partKnown(state.selectedPart)) return true;
  return confirm("Discard unsaved script changes?");
}

async function selectPart(partId) {
  if (!confirmDiscardEdits(partId)) return;
  const seq = ++selectSeq;
  if (faceSel && faceSel.partId !== partId) clearFaceSelection();
  setState({ mode: "part", selectedPart: partId, selectedInstance: null });
  inspector.hideBanner();
  let detail = null;
  try {
    detail = await api.getPart(state.projectName, partId);
  } catch (err) {
    toast(`Could not load ${partId}: ${err.message}`, "error");
  }
  if (seq !== selectSeq) return;
  if (detail) {
    setState({ part: detail });
    learnPartKind(detail);
  }
  await reloadMesh(partId);
  if (seq !== selectSeq) return;
  if (lastFittedTarget !== partId && meshBuffers.has(partId)) {
    viewport.fit();
    lastFittedTarget = partId;
  }
  updateHUD();
}

async function selectAssembly(instanceId) {
  if (instanceId && state.project) {
    // Selecting an instance swaps its part into the inspector below, which
    // would silently drop unsaved edits to the current part's script.
    const next = state.project.assembly.instances.find((i) => i.id === instanceId);
    if (next && !confirmDiscardEdits(next.part)) return;
  }
  const entering = state.mode !== "assembly";
  const seq = ++selectSeq;
  clearFaceSelection();
  setState({ mode: "assembly", selectedInstance: instanceId });
  viewport.setSelectedInstance(instanceId);

  // an instance selection also loads its part into the inspector
  if (instanceId && state.project) {
    const inst = state.project.assembly.instances.find((i) => i.id === instanceId);
    if (inst && inst.part !== state.selectedPart) {
      setState({ selectedPart: inst.part });
      api.getPart(state.projectName, inst.part).then(
        (detail) => {
          if (state.selectedPart === inst.part) setState({ part: detail });
          learnPartKind(detail);
        },
        () => {}
      );
    }
  }

  if (entering || !state.assembly) {
    await loadAssembly();
    if (seq !== selectSeq) return;
  } else {
    renderAssemblyFromCache();
  }
  if (entering || lastFittedTarget !== "__assembly__") {
    viewport.fit();
    lastFittedTarget = "__assembly__";
  }
  updateHUD();
}

async function loadAssembly() {
  const proj = state.projectName;
  if (!proj) return;
  let asm;
  try {
    asm = await api.getAssembly(proj);
  } catch (err) {
    toast(`Assembly failed to load: ${err.message}`, "error");
    return;
  }
  if (proj !== state.projectName) return; // project switched mid-flight
  setState({ assembly: asm });
  const partIds = [...new Set(asm.instances.map((i) => i.part))];
  await Promise.all(
    partIds.map(async (pid) => {
      try {
        meshBuffers.set(pid, await api.getMesh(proj, pid));
      } catch {
        /* broken part: instance is skipped, error visible in sidebar/inspector */
      }
    })
  );
  if (proj !== state.projectName) return;
  renderAssemblyFromCache();
  updateHUD();
}

// Viewport geometry-cache key for a mesh entry. The lod qualifier keeps the
// coarse tier and the full-resolution mesh of the same build apart in the
// viewport's `${partId}:${key}` cache (same ACM1 format, different geometry).
function geomKey(entry) {
  return `${entry.key}:${entry.lod || "full"}`;
}

function renderAssemblyFromCache() {
  if (state.mode !== "assembly" || !state.assembly) return;
  const items = [];
  state.assembly.instances.forEach((inst, i) => {
    const entry = meshBuffers.get(inst.part);
    if (!entry) return;
    items.push({
      instanceId: inst.id,
      partId: inst.part,
      buffer: entry.buffer,
      key: geomKey(entry),
      position: inst.position,
      rotationDeg: inst.rotation_deg,
      color: tree.instanceColor(inst, i),
    });
  });
  viewport.showAssembly(items);
  viewport.setSelectedInstance(state.selectedInstance);
  updateGizmo();
}

function scheduleAssemblyRefresh() {
  clearTimeout(assemblyRefreshTimer);
  assemblyRefreshTimer = setTimeout(() => {
    if (state.mode === "assembly") loadAssembly();
  }, 400);
}

// --------------------------------------------------------------------- mesh

async function reloadMesh(partId) {
  if (!state.projectName) return;
  if (state.mode === "part") {
    if (state.selectedPart !== partId) return;
    try {
      // Progressive load: ask for the coarse tier first. Small parts have no
      // tier — the server serves the full mesh in this same response
      // (lod === "full") and no second request is made.
      const entry = await api.getMesh(state.projectName, partId, "lod1");
      if (state.selectedPart !== partId || state.mode !== "part") return;
      meshBuffers.set(partId, entry);
      viewport.showPart(partId, entry.buffer, geomKey(entry));
      // A rebuild produced new geometry: any picked face index is stale.
      if (faceSel && (faceSel.partId !== partId || faceSel.key !== entry.key)) {
        clearFaceSelection();
      }
      if (entry.lod === "lod1") upgradeMeshToFull(partId);
      else loadFaceMap(partId, entry.key);
    } catch (err) {
      if (err instanceof ApiError && err.status !== 0 && err.error) {
        markPartState(partId, "error");
      }
      // Broken build: show this part's last good mesh from the session,
      // or clear the stage so another part's geometry can't mislead.
      const lastGood = meshBuffers.get(partId);
      if (lastGood) {
        viewport.showPart(partId, lastGood.buffer, geomKey(lastGood));
      } else {
        viewport.clear();
      }
    }
  } else {
    const used = state.assembly &&
      state.assembly.instances.some((i) => i.part === partId);
    if (!used) return;
    try {
      meshBuffers.set(partId, await api.getMesh(state.projectName, partId));
    } catch {
      return;
    }
    renderAssemblyFromCache();
  }
  updateHUD();
}

// The coarse tier is already on screen; fetch the full-resolution mesh in the
// background and swap it in. Guarded like selectPart's async loads: bail if
// the selection, mode, or project changed while the fetch was in flight — a
// fresh reloadMesh will run its own upgrade then.
async function upgradeMeshToFull(partId) {
  const seq = selectSeq;
  const proj = state.projectName;
  let entry;
  try {
    entry = await api.getMesh(proj, partId);
  } catch {
    return; // keep the coarse tier; the next rebuild event retries
  }
  if (seq !== selectSeq || proj !== state.projectName) return;
  if (state.mode !== "part" || state.selectedPart !== partId) return;
  meshBuffers.set(partId, entry);
  viewport.showPart(partId, entry.buffer, geomKey(entry));
  loadFaceMap(partId, entry.key);
  updateHUD();
}

// Fetch the triangle->B-rep-face sidecar for the FULL-resolution mesh and
// hand it to the viewport's geometry cache, enabling face picking. 404 (a
// stale pre-sidecar cache entry or a reference part) just leaves face
// picking off for that part.
async function loadFaceMap(partId, meshKey) {
  let res;
  try {
    res = await api.getMeshFaces(state.projectName, partId);
  } catch {
    return;
  }
  if (res.key !== meshKey) return; // a rebuild landed mid-flight
  viewport.setFaceMap(partId, `${meshKey}:full`, new Uint32Array(res.buffer));
  // Face pins can now read the resolved face off the geometry on screen
  // instead of the centroid the anchor recorded at creation.
  comments.meshChanged();
}

async function refreshPartDetail(partId) {
  if (state.selectedPart !== partId) return;
  try {
    const detail = await api.getPart(state.projectName, partId);
    if (state.selectedPart === partId) setState({ part: detail });
    learnPartKind(detail);
  } catch {
    /* keep the current detail */
  }
}

function markPartState(partId, st) {
  if (!state.project) return;
  const entry = state.project.parts.find((p) => p.id === partId);
  if (entry && entry.state !== st) {
    entry.state = st;
    setState({ project: state.project });
  }
}

// ----------------------------------------------------------- part CRUD

async function addPart() {
  if (!state.projectName) {
    toast("Open a project first", "error");
    return;
  }
  let id = prompt("New part id ([a-z][a-z0-9_]{0,39}):");
  if (!id) return;
  id = id.trim();
  if (!ID_RE.test(id)) {
    toast(`Invalid part id ${JSON.stringify(id)}`, "error");
    return;
  }
  try {
    await api.createPart(state.projectName, id);
  } catch (err) {
    toast(`Create failed: ${err.message}`, "error");
    return;
  }
  await refreshProject();
  await selectPart(id);
}

async function deletePart(partId) {
  if (!confirm(`Delete part “${partId}”? This removes its script file.`)) return;
  try {
    await api.deletePart(state.projectName, partId);
  } catch (err) {
    toast(`Delete failed: ${err.message}`, "error");
    return;
  }
  meshBuffers.delete(partId);
  toast(`Deleted ${partId}`);
  await refreshProject();
}

// ------------------------------------------------------------------ export

async function runExport(kind, format) {
  if (!state.projectName) return;
  try {
    let result;
    if (kind === "part") {
      if (!state.selectedPart) {
        toast("Select a part to export", "error");
        return;
      }
      result = await api.exportPart(state.projectName, state.selectedPart, format);
    } else {
      result = await api.exportAssembly(state.projectName, format);
    }
    const kb = (result.size_bytes / 1024).toFixed(1);
    toast(`Exported ${result.path} (${kb} KB)`);
  } catch (err) {
    const detail = err instanceof ApiError ? err.error.message : String(err);
    toast(`Export failed: ${detail}`, "error");
  }
}

// ----------------------------------------------------------- materials v2

async function loadMaterials(name) {
  try {
    const payload = await api.listMaterials(name);
    if (state.projectName === name) setState({ materials: payload });
  } catch {
    /* materials pane degrades to the bare id if the catalog can't load */
  }
}

// Reference-vs-script isn't in the project list payload; learn it lazily from
// part detail so the sidebar can badge imports without an extra fetch storm.
function learnPartKind(detail) {
  if (!detail || !detail.id) return;
  const prev = state.partKinds[detail.id];
  const next = { kind: detail.kind || "script", source: detail.source || null };
  if (!prev || prev.kind !== next.kind || prev.source !== next.source) {
    setState({ partKinds: { ...state.partKinds, [detail.id]: next } });
  }
}

// ----------------------------------------------------- instance transform

async function patchInstanceTransform(instanceId, patch) {
  if (!state.projectName || !instanceId) return;
  try {
    localPatchUntil = Date.now() + 700; // ignore our own project_changed echo
    const asm = await api.patchInstance(state.projectName, instanceId, patch);
    setState({ assembly: asm });
    // keep the project's raw instances roughly in sync (sidebar, mate flags)
    if (state.project && state.project.assembly) {
      const src = asm.instances.find((i) => i.id === instanceId);
      const dst = state.project.assembly.instances.find((i) => i.id === instanceId);
      if (src && dst) {
        dst.position = src.position;
        dst.rotation_deg = src.rotation_deg;
      }
    }
    renderAssemblyFromCache();
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      toast("Positioned by a mate — clear its mate to move it by hand.", "error");
    } else {
      toast(`Move failed: ${err.message}`, "error");
    }
    scheduleAssemblyRefresh(); // resync panel + gizmo to the server truth
  }
}

// Attach/detach the on-canvas move/rotate gizmo for the current selection.
function updateGizmo() {
  if (state.mode !== "assembly" || !state.selectedInstance) {
    viewport.setGizmo(null);
    return;
  }
  const inst =
    state.assembly &&
    state.assembly.instances.find((i) => i.id === state.selectedInstance);
  // Mate-driven (or not yet loaded): the placement panel shows the note; no gizmo.
  if (!inst || inst.mate) {
    viewport.setGizmo(null);
    return;
  }
  viewport.setGizmo(state.selectedInstance, {
    mode: state.gizmoMode,
    onLive: (t) => placement.updateLive(t),
    onCommit: (t) =>
      patchInstanceTransform(state.selectedInstance, {
        position: t.position,
        rotation_deg: t.rotationDeg,
      }),
  });
}

// ---------------------------------------------------------------- import

function deriveImportId(filename) {
  let id = (filename || "part")
    .replace(/\.[^.]*$/, "") // drop extension
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!/^[a-z]/.test(id)) id = `ref_${id}`;
  return id.slice(0, 40) || "reference";
}

async function handleImportFile(file) {
  if (!file) return;
  if (!state.projectName) {
    toast("Open a project first", "error");
    return;
  }
  let upload;
  try {
    const buf = await file.arrayBuffer();
    upload = await api.uploadImport(state.projectName, file.name, buf);
  } catch (err) {
    const detail = err instanceof ApiError ? err.error.message : String(err);
    toast(`Upload failed: ${detail}`, "error");
    return;
  }
  let id = prompt(
    `Imported “${upload.source}”. Part id ([a-z][a-z0-9_]{0,39}):`,
    deriveImportId(file.name)
  );
  if (!id) return; // file stays in imports/; user can retry with the same name
  id = id.trim();
  if (!ID_RE.test(id)) {
    toast(`Invalid part id ${JSON.stringify(id)}`, "error");
    return;
  }
  let res;
  try {
    res = await api.callTool("import_cad_file", {
      project: state.projectName,
      source: upload.source,
      part_id: id,
      label: id,
    });
  } catch (err) {
    const detail = err instanceof ApiError ? err.error.message : String(err);
    toast(`Import failed: ${detail}`, "error");
    return;
  }
  // The tool passthrough returns {error:...} at HTTP 200 on tool failure
  // (e.g. a duplicate part id or an unreadable file).
  if (res && res.error) {
    toast(`Import failed: ${res.error.message || "error"}`, "error");
    return;
  }
  if (res && res.part) learnPartKind(res.part);
  await refreshProject();
  await selectPart(id);
  const imp = (res && res.imported) || {};
  const bits = [];
  if (imp.mesh_only) bits.push("mesh-only");
  if (imp.n_solids != null) {
    bits.push(`${imp.n_solids} solid${imp.n_solids === 1 ? "" : "s"}`);
  }
  toast(`Imported ${id}${bits.length ? ` (${bits.join(", ")})` : ""}`);
}

function setupLibrary() {
  const btn = document.getElementById("library-btn");
  if (!btn) return;
  btn.addEventListener("click", () => library.open());
}

function setupImport() {
  const btn = document.getElementById("import-btn");
  const input = document.getElementById("import-input");
  if (!btn || !input) return;
  btn.addEventListener("click", () => {
    if (!state.projectName) {
      toast("Open a project first", "error");
      return;
    }
    input.value = "";
    input.click();
  });
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (file) handleImportFile(file);
    input.value = "";
  });
}

// --------------------------------------------------------------- websocket

let ws = null;
let wsBackoff = 500;
let hadConnection = false;

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => {
    wsBackoff = 500;
    setState({ connected: true });
    if (hadConnection) {
      // Terminal events (rebuild_finished/failed, chat_done) may have been
      // published while the socket was down: drop stale in-flight markers,
      // then resync from the server.
      setState({ rebuilding: new Set() });
      chat.resetSending();
      refreshProject();
      loadBranchState();
      proposals.refreshCount();
    }
    hadConnection = true;
  };
  ws.onmessage = (e) => {
    let ev;
    try {
      ev = JSON.parse(e.data);
    } catch {
      return;
    }
    handleEvent(ev);
  };
  ws.onclose = () => {
    setState({ connected: false });
    setTimeout(connectWS, wsBackoff);
    wsBackoff = Math.min(wsBackoff * 2, 8000);
  };
  ws.onerror = () => {
    try {
      ws.close();
    } catch {
      /* already closed */
    }
  };
}

function handleEvent(ev) {
  switch (ev.type) {
    case "ping":
      return;
    case "rebuild_started": {
      if (ev.project !== state.projectName) return;
      state.rebuilding.add(ev.part);
      setState({ rebuilding: state.rebuilding });
      // A rebuild for a part we don't know about: created behind our back
      // (e.g. by an agent) — pull the project list back in sync.
      if (!partKnown(ev.part)) scheduleProjectRefresh();
      return;
    }
    case "rebuild_finished": {
      if (ev.project !== state.projectName) return;
      state.rebuilding.delete(ev.part);
      setState({ rebuilding: state.rebuilding });
      markPartState(ev.part, "ok");
      if (state.part && state.part.id === ev.part && ev.metrics) {
        state.part.metrics = ev.metrics;
        state.part.status = {
          state: "ok",
          error: null,
          warnings: state.part.status ? state.part.status.warnings : [],
        };
        setState({ part: state.part });
      }
      // The event carries metrics but not params/script: after an
      // agent-driven set_params/update_part the inspector would show stale
      // values, so refetch the full detail for the selected part.
      if (state.selectedPart === ev.part) refreshPartDetail(ev.part);
      if (!partKnown(ev.part)) scheduleProjectRefresh();
      reloadMesh(ev.part);
      if (state.mode === "assembly") scheduleAssemblyRefresh();
      // New geometry means new face ordinals and possibly a new script: an
      // anchor's status is computed on every read, so the only way to keep
      // the pins and chips honest is to re-read them here.
      comments.scheduleRefresh();
      return;
    }
    case "rebuild_failed": {
      if (ev.project !== state.projectName) return;
      state.rebuilding.delete(ev.part);
      setState({ rebuilding: state.rebuilding });
      markPartState(ev.part, "error");
      if (state.part && state.part.id === ev.part) {
        state.part.status = { state: "error", error: ev.error, warnings: [] };
        state.part.specs = null;   // stale green chips beside a red banner
        setState({ part: state.part });
        inspector.showBanner(ev.error);
      }
      if (!partKnown(ev.part)) scheduleProjectRefresh();
      // A failed rebuild moves anchors too, and in the honest direction: there
      // is now no mesh at the part's current parameters, so its face anchors
      // are `unverified`. Not re-reading here would leave them showing `ok`,
      // which is the one thing the four states exist to prevent.
      comments.scheduleRefresh();
      return;
    }
    case "project_changed": {
      // Covers part create/delete/update as well as assembly edits made
      // server-side; debounced so agent-driven bursts refetch once. Skip the
      // echo of our own in-flight gizmo/transform commit so the reload can't
      // detach the gizmo mid-interaction (local state already reflects it).
      if (ev.project !== state.projectName) return;
      if (Date.now() < localPatchUntil) return;
      scheduleProjectRefresh();
      comments.scheduleRefresh(); // a removed part orphans its threads' anchors
      return;
    }
    case "branch_changed": {
      if (ev.project !== state.projectName) return;
      if (ev.client !== state.clientId) {
        // Another client moved: our cached list (and its meta) is stale, but
        // our own working tree did not change.
        setState({ branches: null });
        return;
      }
      setState({ branch: ev.branch });
      setBranchLabel(ev.branch);
      // Our own switch already reset the context; a switch made elsewhere
      // under this identity (a second tab, the chat agent) has not.
      if (Date.now() < branchSwitchUntil) return;
      reloadBranchContext();
      return;
    }
    case "merge_completed": {
      if (ev.project !== state.projectName) return;
      const where = `${ev.source} into ${ev.target}`;
      if (!ev.validation) {
        toast(`Fast-forwarded ${ev.target} to ${ev.source}`);
      } else if (ev.validation.ok === false) {
        toast(`Merged ${where} with validation failures`, "error");
      } else {
        toast(`Merged ${where}`);
      }
      refreshProject();
      return;
    }
    case "proposal_changed": {
      if (ev.project !== state.projectName) return;
      proposals.handleEvent(ev);
      return;
    }
    case "lock_changed": {
      if (ev.project !== state.projectName) return;
      // The turn lock is per branch: a lock taken on another branch says
      // nothing about ours, and must not light the badge here.
      if (ev.branch && state.branch && ev.branch !== state.branch) return;
      lockHolder = ev.holder || null;
      renderLockIndicator();
      return;
    }
    case "comment_changed": {
      // A POINTER, deliberately: {thread, state, action, part} and no body, so
      // no comment text is fanned out to every connected client. The panel,
      // the pins, the gutter and the badges all re-read from list_comments.
      if (ev.project !== state.projectName) return;
      comments.handleEvent(ev);
      return;
    }
    case "claim_changed": {
      // Who holds a part, not a change to the model — so it never snapshots
      // history and never refreshes geometry. The tree chip and the editor
      // chip both render from the roster this merges into.
      if (ev.project !== state.projectName) return;
      presence.handleClaim(ev);
      return;
    }
    case "notification": {
      // The bus is a BROADCAST: every /ws client gets every notification and
      // filters on `to` here. That is honest on a single-node, unauthenticated,
      // 127.0.0.1-only server — per-principal delivery is PRD-005 — and it is
      // said out loud in the docs rather than implied.
      if (ev.to !== (state.clientId || clientId)) return;
      comments.notified(ev);
      return;
    }
    case "presence_changed": {
      // An optimization, never the mechanism: the 15-second heartbeat's own
      // response carries the same roster, so missing this event costs
      // latency and nothing else.
      if (ev.project !== state.projectName) return;
      presence.handleEvent(ev);
      return;
    }
    case "chat_delta":
    case "chat_tool_call":
    case "chat_tool_result":
    case "chat_done":
      chat.handleEvent(ev);
      return;
    default:
      return;
  }
}

// ------------------------------------------------------------------- HUD

function updateHUD() {
  const hud = document.getElementById("hud");
  if (!state.projectName || (!state.selectedPart && state.mode === "part")) {
    hud.classList.add("hidden");
    return;
  }
  hud.classList.remove("hidden");
  const tris = viewport.triangleCount().toLocaleString("en-US");
  let name, mode;
  if (state.mode === "assembly") {
    mode = "assembly";
    name = state.selectedInstance || state.projectName;
  } else {
    mode = "part";
    name = state.selectedPart || "";
  }
  let stateText = "ok";
  let stateClass = "";
  // In part mode only this part's rebuild matters; any rebuilding part
  // affects the assembly view.
  const buildingHere =
    state.mode === "assembly"
      ? state.rebuilding.size > 0
      : state.rebuilding.has(state.selectedPart);
  if (buildingHere) {
    stateText = "building…";
    stateClass = "building";
  } else if (
    state.mode === "part" &&
    state.part &&
    state.part.status &&
    state.part.status.state === "error"
  ) {
    stateText = "error";
    stateClass = "error";
  }
  hud.innerHTML = "";
  const nameEl = document.createElement("span");
  nameEl.className = "hud-name";
  nameEl.textContent = name;
  const metaEl = document.createElement("span");
  metaEl.textContent = `${mode} · ${tris} tris`;
  const stateEl = document.createElement("span");
  stateEl.className = `hud-state ${stateClass}`;
  stateEl.textContent = stateText;
  hud.append(nameEl, metaEl, stateEl);
}

function updateEmptyState() {
  const el = document.getElementById("viewport-empty");
  el.innerHTML = "";
  let message = null;
  let buttonLabel = null;
  let onClick = null;
  if (!state.projectName) {
    message = "No project open. Create one to start modeling.";
    buttonLabel = "New project…";
    onClick = newProjectPrompt;
  } else if (state.project && !state.project.parts.length) {
    message = `“${state.projectName}” has no parts yet.`;
    buttonLabel = "New part…";
    onClick = addPart;
  }
  if (!message) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  const p = document.createElement("div");
  p.textContent = message;
  const btn = document.createElement("button");
  btn.className = "tb-btn";
  btn.textContent = buttonLabel;
  btn.addEventListener("click", onClick);
  el.append(p, btn);
}

// ---------------------------------------------------------------- toolbar

// Single place that shows/hides a dropdown so the trigger button's
// aria-expanded always mirrors the menu state.
function setMenuHidden(menu, hidden) {
  menu.classList.toggle("hidden", hidden);
  const wrap = menu.closest(".menu-wrap");
  const btn = wrap && wrap.querySelector("button[aria-haspopup]");
  if (btn) btn.setAttribute("aria-expanded", hidden ? "false" : "true");
}

function setupMenus() {
  const wraps = [...document.querySelectorAll(".menu-wrap")];
  document.addEventListener("click", (e) => {
    for (const wrap of wraps) {
      if (!wrap.contains(e.target)) {
        setMenuHidden(wrap.querySelector(".menu"), true);
      }
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      for (const wrap of wraps) setMenuHidden(wrap.querySelector(".menu"), true);
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const open = wraps
      .map((w) => w.querySelector(".menu"))
      .find((m) => !m.classList.contains("hidden"));
    if (!open) return;
    const items = [...open.querySelectorAll(".menu-item")].filter((i) => !i.disabled);
    if (!items.length) return;
    e.preventDefault();
    const idx = items.indexOf(document.activeElement);
    const next =
      e.key === "ArrowDown"
        ? items[(idx + 1) % items.length]
        : items[(idx - 1 + items.length) % items.length];
    next.focus();
  });
}

function setupProjectMenu() {
  const btn = document.getElementById("project-btn");
  const menu = document.getElementById("project-menu");
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!menu.classList.contains("hidden")) {
      setMenuHidden(menu, true);
      return;
    }
    await refreshProjectsList();
    menu.innerHTML = "";
    for (const proj of state.projects) {
      const item = document.createElement("button");
      item.className = "menu-item";
      if (proj.name === state.projectName) item.classList.add("active");
      const label = document.createElement("span");
      label.textContent = proj.name;
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = `${proj.n_parts} part${proj.n_parts === 1 ? "" : "s"}`;
      item.append(label, meta);
      item.addEventListener("click", () => {
        setMenuHidden(menu, true);
        if (proj.name !== state.projectName) loadProject(proj.name);
      });
      menu.appendChild(item);
    }
    if (state.projects.length) {
      const sep = document.createElement("div");
      sep.className = "menu-sep";
      menu.appendChild(sep);
    }
    const add = document.createElement("button");
    add.className = "menu-item";
    add.textContent = "New project…";
    add.addEventListener("click", () => {
      setMenuHidden(menu, true);
      newProjectPrompt();
    });
    menu.appendChild(add);
    const open = document.createElement("button");
    open.className = "menu-item";
    open.textContent = "Open by path…";
    open.addEventListener("click", () => {
      setMenuHidden(menu, true);
      openProjectPrompt();
    });
    menu.appendChild(open);
    setMenuHidden(menu, false);
  });
}

async function newProjectPrompt() {
  let name = prompt("Project name ([a-z][a-z0-9_]{0,39}):");
  if (!name) return;
  name = name.trim();
  if (!ID_RE.test(name)) {
    toast(`Invalid project name ${JSON.stringify(name)}`, "error");
    return;
  }
  try {
    await api.createProject(name);
  } catch (err) {
    toast(`Create failed: ${err.message}`, "error");
    return;
  }
  await refreshProjectsList();
  await loadProject(name);
}

async function openProjectPrompt() {
  const path = prompt("Absolute path to a project directory:");
  if (!path) return;
  let detail;
  try {
    detail = await api.openProject(path);
  } catch (err) {
    toast(`Open failed: ${err.message}`, "error");
    return;
  }
  await refreshProjectsList();
  await loadProject(detail.name);
}

// ------------------------------------------------------------------ branches
// The switcher is a static .menu-wrap (setupMenus() snapshots them at boot).
// It stays hidden when the server has no versioning routes — git missing means
// the tool pack registered nothing and the project has no branches at all.

async function loadBranchState() {
  const btn = document.getElementById("branch-btn");
  const proj = state.projectName;
  if (!proj) {
    btn.classList.add("hidden");
    return false;
  }
  let payload;
  try {
    payload = await api.listBranches(proj);
  } catch {
    btn.classList.add("hidden");
    setState({ branch: null, branches: null, clientId: null });
    return false;
  }
  if (proj !== state.projectName) return false;
  setState({
    branches: payload.branches || [],
    branch: payload.current,
    clientId: payload.you,
  });
  setBranchLabel(payload.current);
  btn.classList.remove("hidden");
  return true;
}

function setBranchLabel(name) {
  const label = document.getElementById("branch-name");
  const btn = document.getElementById("branch-btn");
  if (!label || !btn) return;
  label.textContent = name || "—";
  btn.title = name ? `Branch ${name} — click to switch, merge or tag` : "Current branch";
}

// The proposals button sits beside the branch switcher and shares its
// precondition: no git means no branches, no proposal routes and no button
// (proposals.refreshCount hides it when the list route 404s).
function setupProposals() {
  const btn = document.getElementById("proposals-btn");
  if (btn) btn.addEventListener("click", () => proposals.open());
}

function setupBranchMenu() {
  const btn = document.getElementById("branch-btn");
  const menu = document.getElementById("branch-menu");
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!menu.classList.contains("hidden")) {
      setMenuHidden(menu, true);
      return;
    }
    await loadBranchState();
    menu.innerHTML = "";
    for (const branch of state.branches || []) {
      const item = document.createElement("button");
      item.className = "menu-item";
      if (branch.is_current) item.classList.add("active");
      const label = document.createElement("span");
      label.textContent = branch.is_default ? `${branch.name} (default)` : branch.name;
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = versions.relTime(branch.ts);
      item.title = branch.message || "";
      item.append(label, meta);
      item.addEventListener("click", () => {
        setMenuHidden(menu, true);
        if (!branch.is_current) switchToBranch(branch.name);
      });
      // FR1's delete, per row. Only where the server would allow it anyway —
      // the default branch and the one you are on are refused — and never
      // nested inside the switch button (a button inside a button).
      if (branch.is_default || branch.is_current) {
        menu.appendChild(item);
        continue;
      }
      const row = document.createElement("div");
      row.className = "menu-row";
      const del = document.createElement("button");
      del.type = "button";
      del.className = "menu-del";
      del.textContent = "×";
      del.title = `Delete branch ${branch.name}`;
      del.setAttribute("aria-label", `Delete branch ${branch.name}`);
      del.addEventListener("click", (ev) => {
        ev.stopPropagation();
        setMenuHidden(menu, true);
        deleteBranch(branch.name);
      });
      row.append(item, del);
      menu.appendChild(row);
    }
    const sep = document.createElement("div");
    sep.className = "menu-sep";
    menu.appendChild(sep);
    for (const [label, run] of [
      ["New branch…", newBranchPrompt],
      ["Merge into…", () => merge.openPicker()],
      ["Versions…", () => versions.open()],
    ]) {
      const item = document.createElement("button");
      item.className = "menu-item";
      item.textContent = label;
      item.addEventListener("click", () => {
        setMenuHidden(menu, true);
        run();
      });
      menu.appendChild(item);
    }
    setMenuHidden(menu, false);
  });
}

async function switchToBranch(name) {
  if (!state.projectName || name === state.branch) return;
  if (!confirmDiscardEdits(null)) return; // another branch's scripts differ
  branchSwitchUntil = Date.now() + 1500;
  try {
    await api.switchBranch(state.projectName, name);
  } catch (err) {
    branchSwitchUntil = 0;
    toast(`Switch failed: ${err.message}`, "error");
    return;
  }
  setState({ branch: name });
  setBranchLabel(name);
  await reloadBranchContext();
  toast(`Switched to ${name}`);
}

// The same context reset loadProject() performs: the branch's scripts, params
// and assembly are different authored state, so every cached mesh and fitted
// camera target is stale.
async function reloadBranchContext() {
  const proj = state.projectName;
  if (!proj) return;
  meshBuffers.clear();
  viewport.clear();
  clearFaceSelection();
  lastFittedTarget = null;
  let detail;
  try {
    detail = await api.getProject(proj);
  } catch (err) {
    toast(`Could not reload ${proj}: ${err.message}`, "error");
    return;
  }
  if (proj !== state.projectName) return;
  setState({ project: detail, rebuilding: new Set(), partKinds: {} });
  updateEmptyState();
  loadMaterials(proj);
  if (state.mode === "assembly") {
    await loadAssembly();
    viewport.fit();
    lastFittedTarget = "__assembly__";
    return;
  }
  const keep = detail.parts.some((p) => p.id === state.selectedPart)
    ? state.selectedPart
    : detail.parts.length
      ? detail.parts[0].id
      : null;
  if (keep) {
    await selectPart(keep);
  } else {
    setState({ selectedPart: null, part: null });
    updateHUD();
  }
}

async function deleteBranch(name) {
  if (!state.projectName) return;
  // Same confirm affordance every other destructive action here uses
  // (restore a version, discard a staged merge).
  if (
    !confirm(
      `Delete branch “${name}” and its working tree?\n\n` +
        "Versions (tags) made on it survive. This cannot be undone."
    )
  ) {
    return;
  }
  try {
    await api.deleteBranch(state.projectName, name);
  } catch (err) {
    // in use / current / default all come back as ordinary REST errors
    toast(`Delete failed: ${err.message}`, "error");
    await loadBranchState();
    return;
  }
  toast(`Deleted ${name}`);
  await loadBranchState();
}

async function newBranchPrompt() {
  if (!state.projectName) {
    toast("Open a project first", "error");
    return;
  }
  let name = prompt("New branch name ([a-z0-9][a-z0-9_/-]{0,63}):");
  if (!name) return;
  name = name.trim();
  if (!BRANCH_RE.test(name)) {
    toast(`Invalid branch name ${JSON.stringify(name)}`, "error");
    return;
  }
  try {
    await api.createBranch(state.projectName, name);
  } catch (err) {
    toast(`Create failed: ${err.message}`, "error");
    return;
  }
  toast(`Created ${name}`);
  await switchToBranch(name); // creating does not switch you server-side
}

function setupExportMenu() {
  const btn = document.getElementById("export-btn");
  const menu = document.getElementById("export-menu");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!menu.classList.contains("hidden")) {
      setMenuHidden(menu, true);
      return;
    }
    const hasPart = !!state.selectedPart;
    const hasInstances =
      state.project && state.project.assembly.instances.length > 0;
    for (const item of menu.querySelectorAll("[data-export]")) {
      const [kind] = item.dataset.export.split(":");
      item.disabled = kind === "part" ? !hasPart : !hasInstances;
    }
    // Drawings are script-part only (the server rejects references).
    const selInfo = state.selectedPart && state.partKinds[state.selectedPart];
    const selKind = selInfo
      ? selInfo.kind
      : state.part && state.part.id === state.selectedPart
        ? state.part.kind
        : "script";
    const canDraw = hasPart && selKind !== "reference";
    for (const item of menu.querySelectorAll("[data-drawing]")) {
      item.disabled = !canDraw;
    }
    setMenuHidden(menu, false);
  });
  menu.addEventListener("click", (e) => {
    const drawItem = e.target.closest("[data-drawing]");
    if (drawItem && !drawItem.disabled) {
      setMenuHidden(menu, true);
      if (!state.selectedPart) return;
      if (drawItem.dataset.drawing === "svg") {
        drawings.previewSvg(state.projectName, state.selectedPart);
      } else {
        drawings.saveDxf(state.projectName, state.selectedPart);
      }
      return;
    }
    const item = e.target.closest("[data-export]");
    if (!item || item.disabled) return;
    setMenuHidden(menu, true);
    const [kind, format] = item.dataset.export.split(":");
    runExport(kind, format);
  });
}

// ------------------------------------------------------------------- undo
// Project-level undo/redo, backed by the server's git history through the
// undo cursor (POST /undo, /redo — two-stack semantics: each real mutation
// is one step; redo clears on any new edit). The restore publishes
// project_changed, so the view refreshes through the debounced WS path.

let undoInFlight = false; // one restore at a time; ignore key/button spam

async function stepHistory(verb) {
  if (!state.projectName) {
    toast("Open a project first", "error");
    return;
  }
  if (undoInFlight) return;
  undoInFlight = true;
  try {
    let res;
    try {
      res = await (verb === "undo"
        ? api.undo(state.projectName)
        : api.redo(state.projectName));
    } catch (err) {
      // 409 = empty stack; anything else is a real failure.
      const msg = err.error && err.error.message ? err.error.message : err.message;
      const empty = /nothing to (undo|redo)/i.test(msg || "");
      toast(empty ? `Nothing to ${verb}` : `${verb} failed: ${msg}`,
            empty ? "info" : "error");
      return;
    }
    if (res.error) {
      toast(`${verb} failed: ${res.error.message || "error"}`, "error");
      return;
    }
    const label = res.undone || res.redone || "last change";
    toast(`${verb === "undo" ? "Undid" : "Redid"}: ${label}`);
  } finally {
    undoInFlight = false;
  }
}

function undoLastChange() {
  return stepHistory("undo");
}

function redoLastChange() {
  return stepHistory("redo");
}

function setupUndo() {
  const undoBtn = document.getElementById("undo-btn");
  const redoBtn = document.getElementById("redo-btn");
  if (undoBtn) undoBtn.addEventListener("click", undoLastChange);
  if (redoBtn) redoBtn.addEventListener("click", redoLastChange);
}

// Bare-key shortcuts (f/g/r) must not act behind an open dialog.
function modalOpen() {
  return document.querySelector(".modal-overlay:not(.hidden)") != null;
}

function setupKeys() {
  document.addEventListener("keydown", (e) => {
    const target = e.target instanceof Element ? e.target : document.body;
    const inField = target.closest("input, textarea, .CodeMirror") != null;
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "z") {
      // In a text field leave the browser's/CodeMirror's native text undo
      // alone; elsewhere Cmd/Ctrl+Z is project undo, Shift+Cmd/Ctrl+Z redo.
      if (inField) return;
      e.preventDefault();
      if (e.shiftKey) redoLastChange();
      else undoLastChange();
      return;
    }
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "y") {
      // Ctrl+Y — the Windows/Linux redo convention.
      if (inField) return;
      e.preventDefault();
      redoLastChange();
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
      // CodeMirror handles its own Cmd+S; catch it everywhere else
      if (!target.closest(".CodeMirror")) {
        e.preventDefault();
        if (state.part) inspector.saveIfDirty();
      }
      return;
    }
    if (inField || modalOpen() || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "f") {
      viewport.fit();
      return;
    }
    // Gizmo mode — only meaningful with an editable instance selected.
    if ((k === "g" || k === "r") && state.mode === "assembly" && state.selectedInstance) {
      setState({ gizmoMode: k === "g" ? "translate" : "rotate" });
    }
  });
  // Shift-to-snap while dragging the gizmo (1 mm / 5°) — the placement card
  // advertises it, so it must actually be wired.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Shift") viewport.setGizmoSnap(true);
  });
  document.addEventListener("keyup", (e) => {
    if (e.key === "Shift") viewport.setGizmoSnap(false);
  });
  window.addEventListener("blur", () => viewport.setGizmoSnap(false));
  document.getElementById("fit-btn").addEventListener("click", () => viewport.fit());
}

// ------------------------------------------------------------------ toasts

function toast(message, kind = "info") {
  const host = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = `toast ${kind === "error" ? "error" : ""}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 8000 : 4000);
}

// ---------------------------------------------------- face pick / push-pull

// Part-mode face selection: clicking a face highlights it and opens a small
// push/pull card (distance + Apply -> the push_pull tool). Alt+click or an
// empty-space click clears. The selection is keyed to the mesh cache key, so
// a rebuild (new key => new face indexing) drops it.
let faceSel = null; // {partId, faceIndex, key, info|null}

function clearFaceSelection() {
  faceSel = null;
  viewport.highlightFace(null, null);
  renderFaceCard();
}

async function selectFace(partId, faceIndex) {
  const entry = meshBuffers.get(partId);
  if (!entry) return;
  faceSel = { partId, faceIndex, key: entry.key, info: null };
  viewport.highlightFace(partId, faceIndex);
  renderFaceCard();
  let res;
  try {
    res = await api.callTool("face_info", {
      project: state.projectName,
      part_id: partId,
      face_index: faceIndex,
    });
  } catch {
    return; // card stays in its loading state; Apply still validates server-side
  }
  if (!faceSel || faceSel.partId !== partId || faceSel.faceIndex !== faceIndex) return;
  if (!res.error) {
    faceSel.info = res;
    renderFaceCard();
  }
}

async function openSketchOnFace(button) {
  if (!faceSel) return;
  const { partId, faceIndex } = faceSel;
  // **The plane belongs to the part that asked for it.** `sketch_plane` is a
  // kernel round trip, and nothing rechecked the selection when it landed: pick
  // a face on part A, switch to part B before the kernel answers, and A's face
  // basis and projected references opened in the sketcher *over B* — and Insert
  // then wrote geometry expressed in A's plane into B's script (review 2, C14).
  const project = state.projectName;
  button.disabled = true;
  let res = null;
  try {
    // `face_info` gives a normal and a centre — a plane, but no basis. Without
    // a deterministic in-plane X axis every coordinate the sketch emits is
    // arbitrary, so the plane comes from `sketch_plane`, which also projects
    // the face's own boundary edges into that basis.
    res = await api.callTool("sketch_plane", {
      project,
      part_id: partId,
      face_index: faceIndex,
    });
  } catch (err) {
    toast(`Sketch on face failed — ${err.message}`, "error");
    button.disabled = false;
    return;
  }
  button.disabled = false;
  const stale = state.projectName !== project || state.selectedPart !== partId
    || !faceSel || faceSel.partId !== partId || faceSel.faceIndex !== faceIndex;
  if (stale) return;              // the user moved on; this plane is not theirs
  if (res.error) {
    toast(`Sketch on face failed — ${res.error.message}`, "error");
    return;
  }
  sketcher.openOnFace({ ...res, part_id: partId, project, owner_part: partId });
  toast(`Sketching on face ${faceIndex} · ${res.refs.length} reference edge(s)`);
}

function renderFaceCard() {
  const card = document.getElementById("facecard");
  const body = document.getElementById("facecard-body");
  if (!card || !body) return;
  if (!faceSel || state.mode !== "part") {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  body.textContent = "";

  const title = document.createElement("div");
  title.className = "placement-title";
  const name = document.createElement("span");
  name.className = "placement-id";
  name.textContent = `Face ${faceSel.faceIndex}`;
  const ref = document.createElement("span");
  ref.className = "placement-part";
  ref.textContent = faceSel.partId;
  title.append(name, ref);
  body.appendChild(title);

  const info = faceSel.info;
  const meta = document.createElement("div");
  meta.className = "facecard-meta";
  if (!info) {
    meta.textContent = "inspecting…";
  } else if (info.planar) {
    meta.textContent =
      `planar · ${info.area_mm2.toFixed(1)} mm² · ` +
      `n [${info.normal.map((v) => v.toFixed(2)).join(", ")}]`;
  } else {
    meta.textContent = `not planar · ${info.area_mm2.toFixed(1)} mm²`;
  }
  body.appendChild(meta);

  const planar = !!(info && info.planar);
  const row = document.createElement("div");
  row.className = "facecard-row";
  const input = document.createElement("input");
  input.type = "number";
  input.step = "any";
  input.className = "placement-num";
  input.value = "5";
  input.setAttribute("aria-label", "push/pull distance in millimetres");
  input.disabled = !planar;
  const apply = document.createElement("button");
  apply.type = "button";
  apply.className = "tb-btn";
  apply.textContent = "Push/Pull";
  apply.disabled = !planar;
  apply.title = planar
    ? "Positive distance adds material along the outward normal; negative cuts in"
    : "Push/pull needs a planar face";
  apply.addEventListener("click", () => applyPushPull(input, apply));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && planar) {
      e.preventDefault();
      applyPushPull(input, apply);
    }
  });
  row.append(input, apply);
  body.appendChild(row);

  const sketch = document.createElement("button");
  sketch.type = "button";
  sketch.className = "tb-btn";
  sketch.textContent = "Sketch on face";
  sketch.disabled = !planar;
  sketch.title = planar
    ? "Open the sketcher on this face's plane, with the face's own boundary "
      + "edges projected in as fixed references"
    : "A sketch needs a planar face";
  sketch.addEventListener("click", () => openSketchOnFace(sketch));
  row.appendChild(sketch);

  const comment = document.createElement("button");
  comment.type = "button";
  comment.className = "tb-btn facecard-comment";
  comment.textContent = "💬 Comment";
  comment.title =
    "Open a review thread anchored to this face. The anchor records the " +
    "face's signature now; its status is recomputed on every read.";
  comment.addEventListener("click", (e) => {
    comments.openComposer(
      { kind: "face", part_id: faceSel.partId, face_index: faceSel.faceIndex },
      {
        label: `${faceSel.partId} · face ${faceSel.faceIndex}`,
        at: { x: e.clientX, y: e.clientY },
      }
    );
  });
  row.appendChild(comment);

  renderHoleControls(body, planar);

  const hint = document.createElement("div");
  hint.className = "placement-hint";
  hint.textContent = planar
    ? "mm · appends a marked block to the part script and rebuilds."
    : "Only planar faces can be pushed/pulled. Alt+click clears.";
  body.appendChild(hint);
}

async function applyPushPull(input, applyBtn) {
  if (!faceSel) return;
  const distance = parseFloat(input.value);
  if (!Number.isFinite(distance) || distance === 0) {
    toast("Enter a nonzero distance in mm", "error");
    return;
  }
  const { partId, faceIndex } = faceSel;
  applyBtn.disabled = true;
  applyBtn.textContent = "Applying…";
  let res;
  try {
    res = await api.callTool("push_pull", {
      project: state.projectName,
      part_id: partId,
      face_index: faceIndex,
      distance_mm: distance,
    });
  } catch (err) {
    applyBtn.disabled = false;
    applyBtn.textContent = "Push/Pull";
    toast(`Push/pull failed: ${err.message}`, "error");
    return;
  }
  // Tool failures come back as {error} at HTTP 200; rebuild failures as ok:false.
  if (res.error) {
    applyBtn.disabled = false;
    applyBtn.textContent = "Push/Pull";
    toast(`Push/pull failed: ${res.error.message || "error"}`, "error");
    return;
  }
  if (res.ok === false) {
    applyBtn.disabled = false;
    applyBtn.textContent = "Push/Pull";
    const msg = (res.error && res.error.message) || "rebuild failed";
    toast(`Push/pull rebuilt with an error: ${msg}`, "error");
    return;
  }
  toast(`Pushed face ${faceIndex} by ${distance} mm`);
  // The rebuild's WS events refresh the mesh; the old face index is stale now.
  clearFaceSelection();
}

// ------------------------------------------- hole on face (PRD-010 FR14)

// The families `add_holes` accepts. `fit` is a *clearance* concept: a tapped
// hole's diameter is the tap drill and a `drilled` one is a millimetre the
// user typed, so neither of those has a fit to offer.
const HOLE_FAMILIES = [
  { id: "clearance", label: "Clearance", fit: true },
  { id: "tapped", label: "Tapped", fit: false },
  { id: "counterbore", label: "Counterbore", fit: true },
  { id: "countersink", label: "Countersink", fit: true },
  { id: "drilled", label: "Drilled", fit: false },
];

// standard -> the `hole_standards` answer, or null while one is in flight.
// **The sizes are never a list in this file.** They are the rows the geometry
// will look up, so the picker cannot offer an M4.5 the tables do not have.
const holeTables = new Map();

// The diameter a `drilled` hole starts at, in mm. A number, not a designation:
// the sizes the pickers offer are the tables' rows and nothing else.
const HOLE_DRILL_DEFAULT_MM = "6";

// Kept outside the card so a re-render (face_info landing, a highlight change)
// does not throw away half-typed input.
//
// **It is ONE part's form, though.** A depth or a set of points typed against
// part A is a statement about A's face, and it used to be carried silently on
// to part B — the next Drill applied it there. So it is reset when the
// selection moves.
//
// The scope is `"<project>::<part>"`, not the part id, on PRD-009's precedent
// (`sketch_plane` and `/api/sketch/blocks` record exactly that string). A part
// id alone is not an identity across projects: `loadProject` KEEPS the current
// selection when the incoming project has a part of the same name, and every
// project made from the template has a `part1`, so switching projects between
// two `part1`s left the id untouched, this comparison early-returned, and one
// project's coordinates and blind depth were still loaded against the other's
// face.
const HOLE_FORM_DEFAULTS = Object.freeze({
  family: "clearance", std: "iso", size: "", fit: "",
  depth: "", points: "0, 0",
});
const holeForm = { ...HOLE_FORM_DEFAULTS };
let holeFormScope = null; // the "<project>::<part>" the numbers were typed for

function holeFormScopeOf() {
  return `${state.projectName || ""}::${state.selectedPart || ""}`;
}

function syncHoleFormPart() {
  // Same part of the same project: keep what the user typed. `setState`
  // re-announces a key whether or not its value moved, so this has to
  // compare, not just fire.
  const scope = holeFormScopeOf();
  if (scope === holeFormScope) return;
  holeFormScope = scope;
  Object.assign(holeForm, HOLE_FORM_DEFAULTS);
}

function holeStandardsFor(std) {
  if (holeTables.has(std)) return holeTables.get(std);
  holeTables.set(std, null); // in flight — one request per standard, ever
  api
    .callTool("hole_standards", { std })
    .then((res) => {
      if (res && !res.error) {
        holeTables.set(std, res);
        renderFaceCard();
      } else {
        holeTables.delete(std);
      }
    })
    .catch(() => holeTables.delete(std));
  return null;
}

/** `[[u, v], …]` from "20, 10; -20, 10", or null when it is not that. */
function parseHolePoints(text) {
  const out = [];
  for (const chunk of String(text).split(/[;\n]/)) {
    const trimmed = chunk.trim();
    if (!trimmed) continue;
    const parts = trimmed.split(",");
    if (parts.length !== 2) return null;
    const pair = parts.map((piece) => Number.parseFloat(piece.trim()));
    if (!pair.every((value) => Number.isFinite(value))) return null;
    out.push(pair);
  }
  return out.length ? out : null;
}

function holeSelect(label, options, value, onChange) {
  const wrap = document.createElement("label");
  wrap.className = "facecard-field";
  const caption = document.createElement("span");
  caption.textContent = label;
  const select = document.createElement("select");
  select.className = "param-select";
  select.setAttribute("aria-label", label);
  for (const option of options) {
    const el = document.createElement("option");
    el.value = option.value;
    el.textContent = option.label;
    if (option.value === value) el.selected = true;
    select.appendChild(el);
  }
  select.addEventListener("change", () => onChange(select.value));
  wrap.append(caption, select);
  return { wrap, select };
}

function renderHoleControls(body, planar) {
  const section = document.createElement("div");
  section.className = "facecard-holes";
  const heading = document.createElement("div");
  heading.className = "facecard-section";
  heading.textContent = "Hole";
  section.appendChild(heading);

  const family = HOLE_FAMILIES.find((entry) => entry.id === holeForm.family)
    || HOLE_FAMILIES[0];
  const tables = holeStandardsFor(holeForm.std);
  const drilled = family.id === "drilled";
  // A `drilled` hole has no table row — its "size" is a diameter in mm — so
  // it is the one family whose size control is a number, not a picker.
  const sizes = drilled ? [] : ((tables && tables.sizes && tables.sizes[family.id]) || []);
  // …and the two kinds of "size" are not interchangeable: a designation is not
  // a number of millimetres, so neither control may be handed the other's
  // value. This normalizes whatever is in the form to what THIS control means.
  if (drilled) {
    if (!(Number.parseFloat(holeForm.size) > 0)) {
      holeForm.size = HOLE_DRILL_DEFAULT_MM;
    }
  } else if (sizes.length && !sizes.includes(holeForm.size)) {
    // The table's first row, deliberately: preferring a particular thread here
    // would put a size literal in this file, and the rule the picker keeps is
    // that every size it offers came from `hole_standards`.
    holeForm.size = sizes[0];
  }
  const fitNames = (tables && tables.fits) || {};
  const fitOptions = ["fine", "medium", "coarse"]
    .filter((name) => fitNames[name])
    .map((name) => ({ value: fitNames[name], label: fitNames[name] }));
  if (fitOptions.length && !fitOptions.some((o) => o.value === holeForm.fit)) {
    holeForm.fit = fitNames.medium;
  }

  const grid = document.createElement("div");
  grid.className = "facecard-grid";
  const controls = [];

  controls.push(holeSelect(
    "family",
    HOLE_FAMILIES.map((entry) => ({ value: entry.id, label: entry.label })),
    family.id,
    (value) => {
      // Crossing the `drilled` boundary changes what a size IS, so the old
      // value is not one the new control can hold. The render above
      // re-defaults it; clearing here is what keeps a stale diameter out of
      // the picker while a size list is still in flight.
      if ((value === "drilled") !== drilled) holeForm.size = "";
      holeForm.family = value;
      renderFaceCard();
    }
  ));
  controls.push(holeSelect(
    "std",
    [{ value: "iso", label: "ISO" }, { value: "ansi", label: "ASME" }],
    holeForm.std,
    (value) => { holeForm.std = value; holeForm.size = ""; renderFaceCard(); }
  ));

  if (drilled) {
    const wrap = document.createElement("label");
    wrap.className = "facecard-field";
    const caption = document.createElement("span");
    caption.textContent = "⌀ mm";
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.min = "0";
    input.className = "placement-num";
    input.value = holeForm.size;
    input.setAttribute("aria-label", "hole diameter in millimetres");
    input.addEventListener("input", () => { holeForm.size = input.value; });
    wrap.append(caption, input);
    controls.push({ wrap, select: input });
  } else {
    controls.push(holeSelect(
      "size",
      sizes.length
        ? sizes.map((size) => ({ value: size, label: size }))
        : [{ value: "", label: "loading…" }],
      holeForm.size,
      (value) => { holeForm.size = value; }
    ));
  }

  if (family.fit) {
    controls.push(holeSelect(
      "fit",
      fitOptions.length ? fitOptions : [{ value: "", label: "loading…" }],
      holeForm.fit,
      (value) => { holeForm.fit = value; }
    ));
  }

  const depthWrap = document.createElement("label");
  depthWrap.className = "facecard-field";
  const depthCaption = document.createElement("span");
  depthCaption.textContent = "depth";
  const depth = document.createElement("input");
  depth.type = "number";
  depth.step = "any";
  depth.min = "0";
  depth.className = "placement-num";
  depth.placeholder = "thru";
  depth.value = holeForm.depth;
  depth.setAttribute("aria-label", "blind depth in millimetres, blank for through");
  depth.addEventListener("input", () => { holeForm.depth = depth.value; });
  depthWrap.append(depthCaption, depth);
  controls.push({ wrap: depthWrap, select: depth });

  for (const control of controls) {
    control.select.disabled = !planar;
    grid.appendChild(control.wrap);
  }
  section.appendChild(grid);

  const posWrap = document.createElement("label");
  posWrap.className = "facecard-field facecard-field-wide";
  const posCaption = document.createElement("span");
  posCaption.textContent = "at (u, v)";
  const points = document.createElement("input");
  points.type = "text";
  points.className = "param-text";
  points.value = holeForm.points;
  points.placeholder = "20, 10; -20, 10";
  points.disabled = !planar;
  points.title =
    "Positions in the picked face's own plane coordinates, ';'-separated. "
    + "(0, 0) is the plane origin `sketch_plane` reports for this face.";
  points.setAttribute("aria-label", "hole positions in face plane coordinates");
  points.addEventListener("input", () => { holeForm.points = points.value; });
  posWrap.append(posCaption, points);
  section.appendChild(posWrap);

  const row = document.createElement("div");
  row.className = "facecard-row";
  const apply = document.createElement("button");
  apply.type = "button";
  apply.className = "tb-btn";
  apply.textContent = "Drill";
  apply.disabled = !planar;
  apply.title = planar
    ? "Appends a holes.* call to the script using this face's own plane basis, "
      + "then rebuilds"
    : "A hole needs a planar face";
  apply.addEventListener("click", () => applyAddHoles(apply));
  points.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    // A disabled button never fires `click`, so the attribute IS the button
    // path's guard and Enter has to consult it. That covers both halves: a
    // non-planar face (disabled at render) and a drill already in flight
    // (`applyAddHoles` disables it for the round trip) — two `add_holes`
    // calls appending to the same script clobber each other.
    if (apply.disabled) return;
    applyAddHoles(apply);
  });
  row.appendChild(apply);
  section.appendChild(row);
  body.appendChild(section);
}

async function applyAddHoles(button) {
  if (!faceSel) return;
  const points = parseHolePoints(holeForm.points);
  if (!points) {
    toast("Positions are 'u, v' pairs separated by ';' — e.g. 20, 10; -20, 10",
      "error");
    return;
  }
  const family = HOLE_FAMILIES.find((entry) => entry.id === holeForm.family)
    || HOLE_FAMILIES[0];
  if (!holeForm.size) {
    toast("Pick a size first", "error");
    return;
  }
  const args = {
    project: state.projectName,
    part_id: faceSel.partId,
    // The picked ordinal is resolved to a literal plane basis SERVER-side, at
    // this instant — the ordinal itself never reaches the script.
    face_index: faceSel.faceIndex,
    points,
    family: family.id,
    size: String(holeForm.size),
    std: holeForm.std,
  };
  if (family.fit && holeForm.fit) args.fit = holeForm.fit;
  const depth = Number.parseFloat(holeForm.depth);
  if (holeForm.depth !== "" && Number.isFinite(depth)) args.depth = depth;

  // The same staleness rule sketch-on-face carries: this is a round trip, and
  // the answer belongs to the part that asked for it.
  const project = state.projectName;
  const faceIndex = faceSel.faceIndex;
  button.disabled = true;
  button.textContent = "Drilling…";
  let res;
  try {
    res = await api.callTool("add_holes", args);
  } catch (err) {
    button.disabled = false;
    button.textContent = "Drill";
    toast(`Drill failed: ${err.message}`, "error");
    return;
  }
  button.disabled = false;
  button.textContent = "Drill";
  if (res.error) {
    toast(`Drill failed: ${res.error.message || "error"}`, "error");
    return;
  }
  if (res.ok === false) {
    const msg = (res.error && res.error.message) || "rebuild failed";
    toast(`Drill rebuilt with an error: ${msg}`, "error");
    return;
  }
  if (state.projectName !== project) return; // the user moved on; not their part
  const label = family.id === "drilled" ? `⌀${args.size}` : args.size;
  toast(`Drilled ${points.length} × ${label} ${family.id} on face ${faceIndex}`);
  // New geometry means new face ordinals — the same rule push/pull follows.
  clearFaceSelection();
}

// ----------------------------------------------------------------- widgets

function onPick(hit, event) {
  if (state.mode === "assembly") {
    if (hit && hit.instanceId) {
      selectAssembly(hit.instanceId);
    }
    return;
  }
  // part mode: face picking
  if ((event && event.altKey) || !hit || hit.faceIndex == null) {
    clearFaceSelection();
    return;
  }
  selectFace(hit.partId, hit.faceIndex);
}

function renderIndicators() {
  const indicator = document.getElementById("rebuild-indicator");
  const label = document.getElementById("rebuild-label");
  if (state.rebuilding.size) {
    indicator.classList.remove("hidden");
    const names = [...state.rebuilding];
    label.textContent =
      names.length === 1 ? `Rebuilding ${names[0]}…` : `Rebuilding ${names.length} parts…`;
  } else {
    indicator.classList.add("hidden");
  }
  document
    .getElementById("conn-dot")
    .classList.toggle("connected", state.connected);
  document.getElementById("conn-dot").title = state.connected
    ? "Connected"
    : "Reconnecting…";
}

let lockEl = null; // lazily created toolbar chip (no markup/CSS changes)

function renderLockIndicator() {
  if (!lockEl) {
    const connDot = document.getElementById("conn-dot");
    if (!connDot || !connDot.parentNode) return;
    lockEl = document.createElement("span");
    lockEl.id = "lock-indicator";
    lockEl.style.cssText =
      "display:none;align-items:center;gap:4px;font-size:12px;" +
      "opacity:0.85;margin-right:8px;white-space:nowrap;";
    connDot.parentNode.insertBefore(lockEl, connDot);
  }
  // Compare against OUR identity, not the literal string "browser". Until
  // PRD-008 slice 6 every browser in the world was the identity `browser`, so
  // that constant was our name; now it is `browser:<8 hex>` and the badge
  // would have announced our own turn back to us. `state.clientId` is the
  // server's echo (branch_list's `you`) and is null without git, so the minted
  // id from api.js is the fallback.
  const me = state.clientId || clientId;
  if (lockHolder && lockHolder !== me) {
    const label = presence.labelFor(lockHolder);
    lockEl.textContent = `🔒 ${label}`;
    lockEl.title =
      `${lockHolder} holds the editing turn — ` +
      "changes by others are rejected until release or expiry";
    lockEl.style.display = "inline-flex";
  } else {
    lockEl.style.display = "none";
  }
}

// ------------------------------------------------------- claim conflicts
// A 409 arrives in two flavours and they must not be confused:
//
//   * the PROJECT TURN LOCK — `details.holder`, no `overridable`. Today's
//     message, and deliberately NO override button: a turn is asked for and
//     released explicitly, and there is nothing here that could grant one.
//   * a per-part SOFT CLAIM — `details.claim` and `details.overridable: true`.
//     Somebody else has this part open. Offer exactly one button, because a
//     claim exists to stop a silent clobber, not to be a permission system.

let claimResolve = null;

function setupClaimDialog() {
  const overlay = document.getElementById("claim-modal");
  const done = (ok) => {
    overlay.classList.add("hidden");
    const resolve = claimResolve;
    claimResolve = null;
    if (resolve) resolve(ok);
  };
  document.getElementById("claim-cancel").addEventListener("click", () => done(false));
  document.getElementById("claim-override").addEventListener("click", () => done(true));
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) done(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && claimResolve) done(false);
  });
}

function askOverride(claim, partId) {
  const overlay = document.getElementById("claim-modal");
  const body = document.getElementById("claim-body");
  const label = presence.labelFor(claim.holder);
  document.getElementById("claim-title").textContent = `${label} is editing ${partId}`;
  body.textContent = "";
  const line = document.createElement("div");
  line.textContent =
    `${label} (${claim.holder}) has ${partId} open. Overriding takes the ` +
    "part, lands your change, and tells them it happened — their unsaved " +
    "edits are still theirs, and the next save wins.";
  const meta = document.createElement("div");
  meta.className = "claim-meta";
  meta.textContent =
    `holder ${claim.holder} · ${claim.holder_kind} · claim expires on its own`;
  body.append(line, meta);
  overlay.classList.remove("hidden");
  return new Promise((resolve) => {
    claimResolve = resolve;
  });
}

/** Offer the override for a refused part write. Resolves true when the caller
 *  should retry the write ONCE — the arming route is single-use and 30 s. */
async function handleWriteConflict(err, partId) {
  if (!(err instanceof ApiError) || err.status !== 409 || !partId) return false;
  const details = (err.error && err.error.details) || {};
  if (!details.overridable || !details.claim) return false; // a turn lock: no override
  const wants = await askOverride(details.claim, partId);
  if (!wants) return false;
  try {
    await api.overrideClaim(state.projectName, partId);
  } catch (e) {
    toast(`Override failed: ${e.message}`, "error");
    return false;
  }
  return true;
}

// -------------------------------------------------------------------- boot

/** Swap the workbench for the sign-in view.
 *
 *  Reached at boot when nobody is signed in, and again if a session dies
 *  mid-session (`agentcad:unauthenticated`, dispatched from the single
 *  `request()` funnel in api.js). Never reached in local mode: there is no
 *  401 to hear and `/api/auth/session` answers 404.
 */
function showSignIn() {
  for (const id of ["toolbar", "workspace"]) {
    document.getElementById(id)?.classList.add("hidden");
  }
  // A full reload rather than re-running boot(): every panel's init() is
  // written to run once (setupMenus snapshots .menu-wrap, comments.js
  // registers an inspector decorator), so re-booting in place would be a
  // second, subtly different app.
  auth.renderSignIn(document.getElementById("auth-view"),
                    () => location.reload());
}

async function boot() {
  theme.init(); // before viewport.init so the scene is born with the stored palette

  // Identity first: in hosted mode there is nothing to render until we know
  // who is asking, and every panel below would 401 on its first call.
  let identity = null;
  try {
    identity = await auth.session();
  } catch {
    identity = { mode: "local", principal: null };   // offline: fall through
  }
  if (identity === null) {
    showSignIn();
    return;
  }
  window.addEventListener("agentcad:unauthenticated", showSignIn, { once: true });
  viewport.init(document.getElementById("viewport"), { onPick });
  tree.init(actions);
  inspector.init(actions);
  chat.init(actions);
  placement.init(actions);
  drawings.init(actions);
  sketcher.init(actions);
  versions.init(actions);
  merge.init(actions);
  proposals.init(actions);
  library.init(actions);
  presence.init();
  // After inspector.init: comments.js registers inspector's param decorator
  // and subscribes to `part` behind it, so a badge is applied to rows the
  // inspector has already built.
  comments.init(actions);
  setupMenus();
  setupProjectMenu();
  setupBranchMenu();
  setupProposals();
  setupClaimDialog();
  setupExportMenu();
  setupImport();
  setupLibrary();
  setupUndo();
  setupKeys();
  onKeys(["rebuilding", "connected"], renderIndicators);
  onKeys(["rebuilding", "part", "mode", "selectedPart", "selectedInstance"], updateHUD);
  onKeys(["selectedPart", "projectName"], syncHoleFormPart);
  onKeys(["gizmoMode"], () => viewport.setGizmoMode(state.gizmoMode));
  connectWS();

  auth.renderChip(document.getElementById("auth-chip"), identity,
                  () => location.reload());

  try {
    const health = await api.health();
    setState({ health, chatAvailable: !!health.chat_available });
  } catch {
    toast("Server unreachable — is `agentcad serve` running?", "error");
  }

  await refreshProjectsList();
  const stored = localStorage.getItem("agentcad.project");
  const initial =
    state.projects.find((p) => p.name === stored) || state.projects[0];
  if (initial) {
    await loadProject(initial.name);
  } else {
    updateEmptyState();
  }
}

boot();
