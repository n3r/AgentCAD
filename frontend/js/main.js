// Boot + orchestration: project loading, selection, mesh routing between
// the API and the viewport, WebSocket event stream, toolbar.

import { api, ApiError } from "./api.js";
import { state, setState, onKeys } from "./state.js";
import * as viewport from "./viewport.js";
import * as tree from "./tree.js";
import * as inspector from "./inspector.js";
import * as chat from "./chat.js";

const ID_RE = /^[a-z][a-z0-9_]{0,39}$/;

const meshBuffers = new Map(); // partId -> {buffer, key}
let selectSeq = 0;
let lastFittedTarget = null; // part id or "__assembly__"
let assemblyRefreshTimer = null;

// ------------------------------------------------------------------ actions

const actions = {
  selectPart,
  selectAssembly,
  addPart,
  deletePart,
  reloadMesh,
  refreshPartDetail,
  markPartState,
  toast,
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
  let detail;
  try {
    detail = await api.getProject(name);
  } catch (err) {
    toast(`Could not open ${name}: ${err.message}`, "error");
    return;
  }
  meshBuffers.clear();
  viewport.clear();
  lastFittedTarget = null;
  setState({
    projectName: name,
    project: detail,
    assembly: null,
    part: null,
    selectedPart: null,
    selectedInstance: null,
    mode: "part",
    rebuilding: new Set(),
  });
  localStorage.setItem("agentcad.project", name);
  document.getElementById("project-name").textContent = name;
  updateEmptyState();

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

// ---------------------------------------------------------------- selection

async function selectPart(partId) {
  const seq = ++selectSeq;
  setState({ mode: "part", selectedPart: partId, selectedInstance: null });
  inspector.hideBanner();
  let detail = null;
  try {
    detail = await api.getPart(state.projectName, partId);
  } catch (err) {
    toast(`Could not load ${partId}: ${err.message}`, "error");
  }
  if (seq !== selectSeq) return;
  if (detail) setState({ part: detail });
  await reloadMesh(partId);
  if (seq !== selectSeq) return;
  if (lastFittedTarget !== partId && meshBuffers.has(partId)) {
    viewport.fit();
    lastFittedTarget = partId;
  }
  updateHUD();
}

async function selectAssembly(instanceId) {
  const entering = state.mode !== "assembly";
  const seq = ++selectSeq;
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
      key: entry.key,
      position: inst.position,
      rotationDeg: inst.rotation_deg,
      color: tree.instanceColor(inst, i),
    });
  });
  viewport.showAssembly(items);
  viewport.setSelectedInstance(state.selectedInstance);
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
      const entry = await api.getMesh(state.projectName, partId);
      if (state.selectedPart !== partId || state.mode !== "part") return;
      meshBuffers.set(partId, entry);
      viewport.showPart(partId, entry.buffer, entry.key);
    } catch (err) {
      if (err instanceof ApiError && err.status !== 0 && err.error) {
        markPartState(partId, "error");
      }
      // Broken build: show this part's last good mesh from the session,
      // or clear the stage so another part's geometry can't mislead.
      const lastGood = meshBuffers.get(partId);
      if (lastGood) {
        viewport.showPart(partId, lastGood.buffer, lastGood.key);
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

async function refreshPartDetail(partId) {
  if (state.selectedPart !== partId) return;
  try {
    const detail = await api.getPart(state.projectName, partId);
    if (state.selectedPart === partId) setState({ part: detail });
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
    if (hadConnection) refreshProject(); // resync after a drop
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
      reloadMesh(ev.part);
      if (state.mode === "assembly") scheduleAssemblyRefresh();
      return;
    }
    case "rebuild_failed": {
      if (ev.project !== state.projectName) return;
      state.rebuilding.delete(ev.part);
      setState({ rebuilding: state.rebuilding });
      markPartState(ev.part, "error");
      if (state.part && state.part.id === ev.part) {
        state.part.status = { state: "error", error: ev.error, warnings: [] };
        setState({ part: state.part });
        inspector.showBanner(ev.error);
      }
      return;
    }
    case "project_changed": {
      if (ev.project === state.projectName) refreshProject();
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
  if (state.rebuilding.size) {
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

function setupMenus() {
  const wraps = [...document.querySelectorAll(".menu-wrap")];
  document.addEventListener("click", (e) => {
    for (const wrap of wraps) {
      if (!wrap.contains(e.target)) {
        wrap.querySelector(".menu").classList.add("hidden");
      }
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      for (const wrap of wraps) wrap.querySelector(".menu").classList.add("hidden");
    }
  });
}

function setupProjectMenu() {
  const btn = document.getElementById("project-btn");
  const menu = document.getElementById("project-menu");
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!menu.classList.contains("hidden")) {
      menu.classList.add("hidden");
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
        menu.classList.add("hidden");
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
      menu.classList.add("hidden");
      newProjectPrompt();
    });
    menu.appendChild(add);
    const open = document.createElement("button");
    open.className = "menu-item";
    open.textContent = "Open by path…";
    open.addEventListener("click", () => {
      menu.classList.add("hidden");
      openProjectPrompt();
    });
    menu.appendChild(open);
    menu.classList.remove("hidden");
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

function setupExportMenu() {
  const btn = document.getElementById("export-btn");
  const menu = document.getElementById("export-menu");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!menu.classList.contains("hidden")) {
      menu.classList.add("hidden");
      return;
    }
    const hasPart = !!state.selectedPart;
    const hasInstances =
      state.project && state.project.assembly.instances.length > 0;
    for (const item of menu.querySelectorAll("[data-export]")) {
      const [kind] = item.dataset.export.split(":");
      item.disabled = kind === "part" ? !hasPart : !hasInstances;
    }
    menu.classList.remove("hidden");
  });
  menu.addEventListener("click", (e) => {
    const item = e.target.closest("[data-export]");
    if (!item || item.disabled) return;
    menu.classList.add("hidden");
    const [kind, format] = item.dataset.export.split(":");
    runExport(kind, format);
  });
}

function setupKeys() {
  document.addEventListener("keydown", (e) => {
    const target = e.target instanceof Element ? e.target : document.body;
    const inField = target.closest("input, textarea, .CodeMirror") != null;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
      // CodeMirror handles its own Cmd+S; catch it everywhere else
      if (!target.closest(".CodeMirror")) {
        e.preventDefault();
        if (state.part) inspector.saveIfDirty();
      }
      return;
    }
    if (inField || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "f" || e.key === "F") {
      viewport.fit();
    }
  });
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

// ----------------------------------------------------------------- widgets

function onPick(hit) {
  if (state.mode !== "assembly") return;
  if (hit && hit.instanceId) {
    selectAssembly(hit.instanceId);
  }
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

// -------------------------------------------------------------------- boot

async function boot() {
  viewport.init(document.getElementById("viewport"), { onPick });
  tree.init(actions);
  inspector.init(actions);
  chat.init(actions);
  setupMenus();
  setupProjectMenu();
  setupExportMenu();
  setupKeys();
  onKeys(["rebuilding", "connected"], renderIndicators);
  onKeys(["rebuilding", "part", "mode", "selectedPart", "selectedInstance"], updateHUD);
  connectWS();

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
