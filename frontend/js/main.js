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
import * as configs from "./configs.js";
import * as market from "./market.js";
import * as materials from "./materials.js";
import * as auth from "./auth.js";
import { setupShare } from "./share-links.js";
// The server's identifier rules, spelled once (`frontend/js/patterns.js`) and
// fed to every dialog field's `pattern` through `bare()`.
import { ID_RE, BRANCH_RE, bare } from "./patterns.js";
// PRD-026 shell. `actions` is the ACTION REGISTRY — the panel DI object that
// used to own that name is `panelApi` below.
import * as actions from "./shell/actions.js";
import * as dialogs from "./shell/dialogs.js";
import * as shortcuts from "./shell/shortcuts.js";
import * as menu from "./shell/menu.js";
import * as layout from "./shell/layout.js";
import * as palette from "./shell/palette.js";
import * as events from "./shell/events.js";
import { toast, init as initToasts } from "./shell/toast.js";


const meshBuffers = new Map(); // partId -> {buffer, key, lod} from api.getMesh
// Assembly geometry is CONTENT-addressed, not part-addressed: two instances of
// one part bound to different configurations are two different meshes, and
// `partId -> mesh` cannot hold both. `get_assembly` publishes a `mesh_key` for
// every built instance and this map is keyed by exactly that.
const instanceMeshes = new Map(); // mesh_key -> {buffer, key, lod}
let selectSeq = 0;
let lastFittedTarget = null; // part id or "__assembly__"
let localPatchUntil = 0; // suppress our own project_changed echo until this ts
let assemblyRefreshTimer = null;
let projectRefreshTimer = null;
let lockHolder = null; // current turn-lock holder for this project (or null)
let branchSwitchUntil = 0; // suppress the branch_changed echo of our own switch

// ----------------------------------------------------------------- panel API
// The dependency object every panel module receives. It was called `actions`
// until PRD-026; the name now belongs to the action registry (shell/actions.js)
// and this is the panels' API. Panels keep their own parameter names, so the
// rename is confined to this file.

const panelApi = {
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
  // Files tab; comments.js reaches it through the same panelApi object every
  // other panel uses rather than importing proposals.js and closing a cycle.
  openProposal: (id, tab) => proposals.openTo(id, tab),
  handleWriteConflict,
  // Materials modal (PRD-028 slice 5): routed through `actions` both ways —
  // inspector.js's Browse… button opens it, its "Use for part" button writes
  // back through `inspector.setPartMaterial` — the same "don't import each
  // other, go through actions" idiom `openProposal` above uses.
  openMaterials: (opts) => materials.open(opts),
  assignMaterial: (partId, id) => inspector.setPartMaterial(partId, id),
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
  if (!(await confirmDiscardEdits(null))) return; // switching projects drops the editor
  let detail;
  try {
    detail = await api.getProject(name);
  } catch (err) {
    toast(`Could not open ${name}: ${err.message}`, "error");
    return;
  }
  meshBuffers.clear();
  instanceMeshes.clear();
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
  const materialsMoved = JSON.stringify((state.project || {}).materials || null)
    !== JSON.stringify(detail.materials || null);
  setState({ project: detail });
  updateEmptyState();
  // Keep the material picker in sync after edits — but only when the
  // project's materials map actually moved: the full catalog is ~0.5 MB for
  // 434 cards and `project_changed` fires after every write.
  if (materialsMoved || !state.materials) loadMaterials(state.projectName);
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
// PRD-026 FR2: the native confirm became a dialog, so this is a PROMISE and every
// caller awaits it. The three fast paths above still answer synchronously
// (an `async` function returning `true` resolves on the microtask queue), so
// the common "nothing is dirty" case still costs no frame.
async function confirmDiscardEdits(nextPartId) {
  if (!state.selectedPart || nextPartId === state.selectedPart) return true;
  if (!state.part || !editor.isDirty()) return true;
  // The edited part no longer exists (deleted): nothing can be saved.
  if (!partKnown(state.selectedPart)) return true;
  return dialogs.confirm({
    view: "discard-edits",
    title: "Discard unsaved script changes?",
    body: `${state.selectedPart} has edits that were never saved. Leaving `
      + "this part throws them away.",
    danger: true,
    confirmLabel: "Discard",
    cancelLabel: "Keep editing",
  });
}

async function selectPart(partId) {
  if (!(await confirmDiscardEdits(partId))) return;
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
    if (next && !(await confirmDiscardEdits(next.part))) return;
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
  const wanted = [...new Set(asm.instances.map((i) => i.mesh_key).filter(Boolean))];
  // Content-addressed, so a key we already hold is the same bytes: only the
  // keys that are new to this session are fetched, and the unconditional
  // per-refresh refetch of every part goes away with it.
  await Promise.all(
    wanted
      .filter((key) => !instanceMeshes.has(key))
      .map(async (key) => {
        try {
          instanceMeshes.set(key, await api.getMeshByKey(proj, key));
        } catch {
          /* not built / broken part: the instance is skipped, and the error is
             already visible in the sidebar and the inspector */
        }
      })
  );
  if (proj !== state.projectName) return;
  // Keep the map bounded: a session that walks a family would otherwise hold
  // every configuration it ever rendered.
  const live = new Set(wanted);
  for (const key of [...instanceMeshes.keys()]) {
    if (!live.has(key)) instanceMeshes.delete(key);
  }
  renderAssemblyFromCache();
  updateHUD();
}

// Viewport geometry-cache key for a mesh entry. The lod qualifier keeps the
// coarse tier and the full-resolution mesh of the same build apart in the
// viewport's `${partId}:${key}` cache (same ACM1 format, different geometry).
function geomKey(entry) {
  return `${entry.key}:${entry.lod || "full"}`;
}

// Simplified proxy meshes, keyed by partId (one convex hull per part, config-
// agnostic — a display proxy). Lazily fetched through the per-part route, which
// produces the tier on a miss (PRD-013 FR7).
const simplifiedMeshes = new Map(); // partId -> {buffer, key}

function assemblyItems(useSimplified) {
  const items = [];
  state.assembly.instances.forEach((inst, i) => {
    const proxy = useSimplified ? simplifiedMeshes.get(inst.part) : null;
    const entry = proxy || (inst.mesh_key ? instanceMeshes.get(inst.mesh_key) : null);
    if (!entry) return;
    items.push({
      instanceId: inst.id,
      partId: inst.part,
      buffer: entry.buffer,
      key: proxy ? `${inst.part}:simplified` : geomKey(entry),
      position: inst.position,
      rotationDeg: inst.rotation_deg,
      color: tree.instanceColor(inst, i),
    });
  });
  return items;
}

function renderAssemblyFromCache() {
  if (state.mode !== "assembly" || !state.assembly) return;
  if (state.repMode === "simplified") {
    // Instanced proxy render: one geometry per part, N transforms. Selection
    // still resolves (pick maps instanceId back), but per-instance gizmo
    // editing lives in Full mode.
    const items = assemblyItems(true);
    viewport.showAssemblyInstanced(items);
    viewport.setSelectedInstance(state.selectedInstance);
    updateHUD();
    return;
  }
  const items = assemblyItems(false);
  viewport.showAssembly(items);
  viewport.setSelectedInstance(state.selectedInstance);
  updateGizmo();
}

/** Fetch the simplified proxy for every distinct part in the current assembly,
 *  producing the tier lazily via the per-part route, then re-render. */
async function loadSimplifiedProxies() {
  const proj = state.projectName;
  if (!proj || !state.assembly) return;
  const parts = [...new Set(state.assembly.instances.map((i) => i.part).filter(Boolean))];
  await Promise.all(
    parts
      .filter((p) => !simplifiedMeshes.has(p))
      .map(async (part) => {
        try {
          simplifiedMeshes.set(part, await api.getMesh(proj, part, "simplified"));
        } catch {
          /* no proxy (reference part / broken): that instance is skipped */
        }
      })
  );
  if (proj !== state.projectName) return;
  renderAssemblyFromCache();
}

async function setRepMode(mode) {
  if (mode !== "full" && mode !== "simplified") return;
  setState({ repMode: mode });
  updateRepModeToggle();
  if (mode === "simplified") {
    await loadSimplifiedProxies();
  } else {
    renderAssemblyFromCache();
  }
}

function updateRepModeToggle() {
  const box = document.getElementById("repmode");
  if (!box) return;
  box.classList.toggle("hidden", state.mode !== "assembly");
  for (const btn of box.querySelectorAll(".repmode-btn")) {
    btn.classList.toggle("active", btn.dataset.rep === state.repMode);
  }
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
    // A part-addressed refetch cannot answer "which mesh does THIS instance
    // show now" — a rebuild moves the part's key, and a bound instance's key
    // did not move at all. `get_assembly` is the only thing that knows, so the
    // refresh goes through it (debounced: a burst of rebuilds costs one call).
    scheduleAssemblyRefresh();
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

/** The material field, but ONLY when the catalog is already in hand.
 *
 *  `loadMaterials` fetches it per project and parks it in `state.materials`;
 *  offering a free-text material box when it has not arrived would invite a
 *  typo that `updatePart` then refuses, so the field is simply absent and the
 *  inspector's picker (which owns this decision) stays the one place to set it.
 */
function materialField() {
  const catalog = (state.materials && state.materials.materials) || [];
  if (!catalog.length) return null;
  return {
    name: "material",
    label: "Material",
    type: "select",
    value: "",
    options: [{ value: "", label: "— none —" },
              // `m.label` is the display name the inspector's picker shows —
              // the same field, so the two lists read alike.
              ...catalog.map((m) => ({ value: m.id, label: m.label || m.id }))],
    help: "Optional; the inspector can change it later.",
  };
}

async function addPart(args) {
  if (!state.projectName) {
    toast("Open a project first", "error");
    return;
  }
  const prefill = args || {};
  const values = await dialogs.form({
    view: "new-part",
    title: "New part",
    width: "narrow",
    fields: [
      { name: "id", label: "Part id", value: prefill.id || "", required: true,
        pattern: bare(ID_RE),
        patternMessage: "lowercase letters, digits, _; max 40",
        help: "Used for the script file name and every reference to the part." },
      { name: "label", label: "Label", value: prefill.label || "",
        placeholder: "Optional display name" },
      ...[materialField()].filter(Boolean),
    ],
    buttons: [{ id: "cancel", label: "Cancel" },
              { id: "create", label: "Create", kind: "primary", submits: true }],
  });
  if (!values) return;
  const id = String(values.id).trim();
  try {
    await api.createPart(state.projectName, id, values.label || undefined);
  } catch (err) {
    toast(`Create failed: ${err.message}`, "error");
    return;
  }
  if (values.material) {
    // A second call because `POST /parts` takes id+label only. A refusal here
    // must not read as "the part was not created" — it was.
    try {
      await api.updatePart(state.projectName, id, { material: values.material });
    } catch (err) {
      toast(`${id} created, but the material did not stick: ${err.message}`,
            "error");
    }
  }
  await refreshProject();
  await selectPart(id);
}

async function deletePart(partId) {
  if (!partId) return;
  const instances = ((state.project && state.project.assembly
    && state.project.assembly.instances) || []).filter((i) => i.part === partId);
  const ok = await dialogs.confirm({
    view: "delete-part",
    title: `Delete part “${partId}”?`,
    body: `Deletes ${partId} and its script file.`,
    // The blast radius, named before the click and not discovered after it:
    // deleting a part takes every assembly instance of it with it.
    note: instances.length
      ? `Also removes ${instances.length} assembly instance`
        + `${instances.length === 1 ? "" : "s"}: `
        + instances.map((i) => i.id).join(", ")
      : undefined,
    danger: true,
    confirmLabel: "Delete part",
  });
  if (!ok) return;
  try {
    await api.deletePart(state.projectName, partId);
  } catch (err) {
    toast(`Delete failed: ${err.message}`, "error");
    return;
  }
  meshBuffers.delete(partId);
  // instanceMeshes is keyed by CONTENT, not by part, so a part id cannot
  // select its entries: drop the lot. The next loadAssembly refetches exactly
  // the keys the surviving instances name, and nothing else.
  instanceMeshes.clear();
  toast(`Deleted ${partId}`);
  await refreshProject();
}

// ------------------------------------------------------------------ export

/** A compact " · …" toast suffix from an export/import result's `fidelity`
 *  and `warnings` (PRD-017 deliverables 3–4). PMI presence/opt-out is always
 *  worth a word (it is the whole point of the toggle below); everything else
 *  — a skipped PMI entry, an import warning — only shows up when there is
 *  something to say, so the happy path stays one short line. */
function resultSuffix(result) {
  const bits = [];
  const attached = result && result.pmi_attached;
  if (attached) {
    const total = (attached.dims || 0) + (attached.datums || 0) + (attached.fcf || 0);
    if (total) {
      bits.push(
        `PMI attached (${attached.dims || 0} dim${attached.dims === 1 ? "" : "s"}, `
        + `${attached.datums || 0} datum${attached.datums === 1 ? "" : "s"}, `
        + `${attached.fcf || 0} FCF${attached.fcf === 1 ? "" : "s"})`
      );
    }
  }
  const fid = (result && result.fidelity) || {};
  if (fid.pmi === "opted_out") bits.push("PMI opted out");
  const skipped = Array.isArray(fid.pmi_skipped) ? fid.pmi_skipped : [];
  if (skipped.length) {
    bits.push(`PMI: ${skipped.length} entr${skipped.length === 1 ? "y" : "ies"} skipped`);
  }
  // An assembly export's own skip list (an unbuilt/broken instance) — the
  // same "never silent" rule the PMI skip line above follows.
  const instancesSkipped = Array.isArray(fid.instances_skipped)
    ? fid.instances_skipped : [];
  if (instancesSkipped.length) {
    bits.push(`${instancesSkipped.length} `
      + `instance${instancesSkipped.length === 1 ? "" : "s"} skipped`);
  }
  const warnings = (result && result.warnings) || [];
  if (warnings.length) {
    bits.push(`${warnings.length} warning${warnings.length === 1 ? "" : "s"}`);
  }
  return bits.length ? ` · ${bits.join(" · ")}` : "";
}

/** STEP part export only: when the part has PMI stored, ask whether to
 *  attach it (default yes — the same default `export_part`'s wrapper
 *  applies server-side when `pmi` is omitted). Returns `true`/`false` to
 *  pass explicitly, or `null` when the user cancelled the export outright.
 *  A part with no PMI never sees a dialog — there is nothing to opt out of. */
async function promptIncludePmi(partId) {
  let info;
  try {
    info = await api.callTool("get_part_pmi", {
      project: state.projectName, part_id: partId,
    });
  } catch {
    return true; // couldn't check — the export still runs on the server's own default
  }
  const pmi = (info && !info.error && info.pmi) || {};
  const hasPmi = ["dims", "datums", "fcf"]
    .some((k) => Array.isArray(pmi[k]) && pmi[k].length);
  if (!hasPmi) return true;
  const values = await dialogs.form({
    view: "export-step-pmi",
    title: "Export STEP",
    width: "narrow",
    body: `${partId} has PMI (GD&T) attached.`,
    fields: [{
      name: "pmi", type: "checkbox", value: true,
      label: "Include GD&T (AP242)",
    }],
    buttons: [
      { id: "cancel", label: "Cancel" },
      { id: "export", label: "Export", kind: "primary", submits: true },
    ],
  });
  return values ? !!values.pmi : null;
}

async function runExport(kind, format, structured) {
  if (!state.projectName) return;
  try {
    let result;
    let diverged = false;
    if (kind === "part") {
      if (!state.selectedPart) {
        toast("Select a part to export", "error");
        return;
      }
      // With a configuration loaded, "export this part" means export the
      // CONFIGURATION — `flange_l.step`, not `flange.step` — resolved purely
      // (defaults < config), so parameters you typed over on top of it are
      // deliberately NOT in the file: a variant's identity never depends on
      // session state (Decision 3/8). Use the base export for the working
      // state. The core export route takes no `config`, so this goes through
      // the tool passthrough.
      const cfg = state.part && state.part.id === state.selectedPart
        ? state.part.active_config
        : null;
      // ...and when the working state IS diverged, say so, because the file
      // and the viewport genuinely disagree. The flag is already on the part
      // payload, so this costs no round trip.
      diverged = !!(cfg && state.part && state.part.status
        && state.part.status.diverged);
      let pmi;
      if (format === "step") {
        pmi = await promptIncludePmi(state.selectedPart);
        if (pmi === null) return; // cancelled at the PMI checkbox
      }
      // The REST export route (`api.exportPart`) forwards no `pmi` — only the
      // tool passthrough can opt out — so a STEP export always goes through
      // `callTool`, config or not; every other format keeps the plain route.
      result = cfg || pmi !== undefined
        ? await api.callTool("export_part", {
            project: state.projectName,
            part_id: state.selectedPart,
            format,
            config: cfg || undefined,
            pmi,
          })
        : await api.exportPart(state.projectName, state.selectedPart, format);
      // The passthrough answers {error} at HTTP 200 on a tool failure.
      if (result && result.error) {
        toast(`Export failed: ${result.error.message || "error"}`, "error");
        return;
      }
    } else if (structured) {
      // The plain assembly export route takes no `structured` flag — same
      // reason the STEP-part path above needs `callTool` for `pmi`.
      result = await api.callTool("export_assembly", {
        project: state.projectName, format, structured: true,
      });
      // The passthrough answers {error} at HTTP 200 on a tool failure.
      if (result && result.error) {
        toast(`Export failed: ${result.error.message || "error"}`, "error");
        return;
      }
    } else {
      result = await api.exportAssembly(state.projectName, format);
    }
    const kb = (result.size_bytes / 1024).toFixed(1);
    const note = diverged
      ? " — the configuration as declared; your edits are not in it"
      : "";
    toast(`Exported ${result.path} (${kb} KB)${note}${resultSuffix(result)}`);
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

// The kernel pack's own list (`tools_import.STRUCTURED_EXTS`) — only these
// carry a product tree worth previewing; STL/BREP go straight to the
// unchanged flat prompt.
const STRUCTURED_IMPORT_EXTS = new Set([".step", ".stp"]);

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

/** Today's single-part landing, unchanged: prompt for a part id, then
 *  `import_cad_file`. `opts` carries `{structured: false}` only when the
 *  preview dialog's "Import flat instead" forced it — the natural path (no
 *  preview, or a single-product file) sends no `structured` key at all, so
 *  the tool's own auto-detect (a no-op for these files) decides exactly as
 *  it did before this slice. */
async function importFlat(upload, originalName, opts) {
  const answer = await dialogs.prompt({
    view: "import-part-id",
    title: "Name the imported part",
    body: `Imported “${upload.source}”.`,
    label: "Part id",
    value: deriveImportId(originalName),
    pattern: bare(ID_RE),
    patternMessage: "lowercase letters, digits, _; max 40",
    okLabel: "Import",
  });
  // Cancelled: the file stays in imports/ and the user can retry with the
  // same name.
  if (answer == null) return;
  const id = answer.trim();
  let res;
  try {
    res = await api.callTool("import_cad_file", {
      project: state.projectName,
      source: upload.source,
      part_id: id,
      label: id,
      ...(opts || {}),
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
  toast(`Imported ${id}${bits.length ? ` (${bits.join(", ")})` : ""}${resultSuffix(res)}`);
}

/** The preview dialog's product list: one `.row` per unique product (the
 *  tree.js sidebar styling, reused rather than reinvented), a swatch in its
 *  authored colour and a `×N` occurrence badge — "14 products, 41
 *  occurrences" is the summary line above it; this is the "names +
 *  per-product occurrence counts" the design spec asks for. */
function renderImportProductTree(preview) {
  const counts = new Map();
  for (const occ of preview.occurrences || []) {
    counts.set(occ.product_index, (counts.get(occ.product_index) || 0) + 1);
  }
  const wrap = document.createElement("div");
  wrap.className = "import-tree";
  for (const product of preview.products || []) {
    const row = document.createElement("div");
    row.className = "row import-tree-row";
    const swatch = document.createElement("span");
    swatch.className = "row-swatch";
    swatch.style.background = product.color || "#8f9aa6";
    row.appendChild(swatch);
    const label = document.createElement("span");
    label.className = "row-label";
    label.textContent = product.name;
    row.appendChild(label);
    const n = counts.get(product.index) || 0;
    const badge = document.createElement("span");
    badge.className = "row-badge";
    badge.textContent = `×${n}`;
    badge.title = `${n} occurrence${n === 1 ? "" : "s"} of ${product.name}`;
    row.appendChild(badge);
    wrap.appendChild(row);
  }
  return wrap;
}

/** PRD-017 FR8-FR9's dialog: a multi-product STEP file's product tree, a
 *  prefix field, and the choice between landing it structured (N reference
 *  parts + N instances) or flat (today's one-part prompt, unchanged). */
async function openImportPreview(upload, preview, originalName) {
  const wrap = document.createElement("div");
  const nProducts = preview.counts.products;
  const nOcc = preview.counts.occurrences;
  const summary = document.createElement("p");
  summary.className = "dlg-text";
  summary.textContent =
    `${nProducts} product${nProducts === 1 ? "" : "s"}, `
    + `${nOcc} occurrence${nOcc === 1 ? "" : "s"}`;
  wrap.append(summary, renderImportProductTree(preview));

  const res = await dialogs.open({
    view: "import-preview",
    title: "Import preview",
    width: "wide",
    body: wrap,
    fields: [{
      name: "prefix",
      label: "Part id prefix (optional)",
      value: "",
      placeholder: "e.g. vendor",
      pattern: "[a-z0-9_]*",
      patternMessage: "lowercase letters, digits, _ only",
      help: "Prepended to every generated part id.",
    }],
    buttons: [
      { id: "cancel", label: "Cancel" },
      { id: "flat", label: "Import flat instead" },
      {
        id: "structured", kind: "primary", submits: true,
        label: `Import ${nProducts} part${nProducts === 1 ? "" : "s"}`,
      },
    ],
  });
  if (res.button === "flat") {
    await importFlat(upload, originalName, { structured: false });
    return;
  }
  if (!res.ok) return; // cancelled, or dismissed by clicking outside

  const prefix = String(res.values.prefix || "").trim();
  let result;
  try {
    result = await api.callTool("import_cad_file", {
      project: state.projectName,
      source: upload.source,
      structured: true,
      ...(prefix ? { prefix } : {}),
    });
  } catch (err) {
    const detail = err instanceof ApiError ? err.error.message : String(err);
    toast(`Import failed: ${detail}`, "error");
    return;
  }
  if (result && result.error) {
    toast(`Import failed: ${result.error.message || "error"}`, "error");
    return;
  }
  await refreshProject();
  if ((result.instances || []).length) await selectAssembly(null);
  const nParts = (result.parts || []).length;
  const nInstances = (result.instances || []).length;
  toast(
    `Imported ${nParts} part${nParts === 1 ? "" : "s"}, `
    + `${nInstances} instance${nInstances === 1 ? "" : "s"}${resultSuffix(result)}`
  );
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

  const ext = (upload.source.match(/\.[^.]*$/) || [""])[0].toLowerCase();
  if (STRUCTURED_IMPORT_EXTS.has(ext)) {
    let preview = null;
    try {
      preview = await api.previewImport(state.projectName, upload.source);
    } catch {
      // Non-STEP-shaped upload (422/404) or an unreadable file (502): the
      // preview is a convenience, never the import — fall through to today's
      // prompt with no error shown (the plan is explicit: no error dialog).
      preview = null;
    }
    // Gate on the server's name-aware auto-detect rule (PRD-017 review
    // fix): a re-imported multi-solid AgentCAD export must NOT bounce
    // through this dialog just because it has several occurrences, or its
    // primary button explodes it into N anonymous SOLID parts. Fall back to
    // the old occurrence-count rule ONLY when `structured_suggested` is
    // absent from the payload (an older server) — an explicit `false` means
    // straight to the flat prompt no matter the occurrence count.
    const suggestsStructured = preview && (
      preview.structured_suggested === true
      || (preview.structured_suggested === undefined
          && preview.counts && preview.counts.occurrences > 1)
    );
    if (suggestsStructured) {
      await openImportPreview(upload, preview, file.name);
      return;
    }
  }
  await importFlat(upload, file.name);
}

function setupLibrary() {
  const btn = document.getElementById("library-btn");
  if (!btn) return;
  btn.addEventListener("click", () => actions.run("model.library", null,
                                                  { source: "toolbar" }));
}

/** The file picker behind the Import button — an action's `run`, so the same
 *  verb is reachable from the toolbar, the menus and the palette. */
function openImportPicker() {
  const input = document.getElementById("import-input");
  if (!input) return;
  if (!state.projectName) {
    toast("Open a project first", "error");
    return;
  }
  input.value = "";
  input.click();
}

function setupImport() {
  const btn = document.getElementById("import-btn");
  const input = document.getElementById("import-input");
  if (!btn || !input) return;
  btn.addEventListener("click", () => actions.run("project.import-cad", null,
                                                  { source: "toolbar" }));
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
      // A config-tagged rebuild is a MATRIX or an instance build, not the
      // part's own: `state.rebuilding` holds bare part ids, so the first
      // configuration to finish would clear the part's dot and the inspector
      // would repaint another configuration's metrics. It belongs to the matrix.
      if (ev.config) {
        configs.onRebuildEvent(ev);
        return;
      }
      state.rebuilding.add(ev.part);
      setState({ rebuilding: state.rebuilding });
      // A rebuild for a part we don't know about: created behind our back
      // (e.g. by an agent) — pull the project list back in sync.
      if (!partKnown(ev.part)) scheduleProjectRefresh();
      return;
    }
    case "rebuild_finished": {
      if (ev.project !== state.projectName) return;
      if (ev.config) {
        configs.onRebuildEvent(ev);
        return;
      }
      state.rebuilding.delete(ev.part);
      setState({ rebuilding: state.rebuilding });
      markPartState(ev.part, "ok");
      if (state.part && state.part.id === ev.part && ev.metrics) {
        state.part.metrics = ev.metrics;
        // Spread: `status` also carries `diverged` / `diverged_params`, and a
        // rebuild says nothing about either. Replacing the object blinked the
        // config bar's divergence chip off on every rebuild event.
        state.part.status = {
          ...(state.part.status || {}),
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
      if (ev.config) {
        configs.onRebuildEvent(ev);
        return;
      }
      state.rebuilding.delete(ev.part);
      setState({ rebuilding: state.rebuilding });
      markPartState(ev.part, "error");
      if (state.part && state.part.id === ev.part) {
        state.part.status = {
          ...(state.part.status || {}),
          state: "error",
          error: ev.error,
          warnings: [],
        };
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
    // PRD-026: the agent's one way into this UI. `ui_open` is a BROADCAST —
    // the bus has no per-client routing — so every connected browser opens the
    // view, and each one shows the "opened by agent" attribution chip.
    // `openView` refuses (and toasts) a view this shell does not have; it
    // never guesses.
    case "ui_open":
      // Nobody awaits this: catch here, or a throwing opener is an unhandled
      // rejection with no user-visible trace (`palette.js`'s tool path made the
      // same argument).
      dialogs.openView(ev.view, ev.args || {}, { by: "agent" })
        .catch((err) => toast(
          `Agent could not open “${ev.view}”: ${err && err.message ? err.message : err}`,
          "error"));
      return;
    // Our own UX telemetry, echoed back to us by the bus. Handling any of
    // these would make `dialog_opened` open a dialog.
    case "dialog_opened":
    case "dialog_submitted":
    case "palette_executed":
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
  updateRepModeToggle();
  const tris = viewport.triangleCount().toLocaleString("en-US");
  let name, mode;
  if (state.mode === "assembly") {
    mode = "assembly";
    name = state.selectedInstance || state.projectName;
    const n = state.assembly ? state.assembly.instances.length : 0;
    const counts = viewport.assemblyCounts();
    mode = `assembly · ${n.toLocaleString("en-US")} instances`;
    if (state.repMode === "simplified") {
      mode += ` · ${counts.geometries.toLocaleString("en-US")} geom`;
    }
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
  // Wrapped, not passed: both openers now take an `args` prefill object and a
  // MouseEvent is not one.
  btn.addEventListener("click", () => onClick());
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

// PRD-026 slice 4: the outside-click/Esc/roving behaviour itself moved to
// `shell/menu.js`'s `attach()`, which queries `.menu-wrap` LIVE at event time
// (no more "snapshots them at boot" caveat — a wrap inserted after this call,
// like the menu bar's own generated ones, is handled too). This just attaches
// the three PRE-EXISTING static wraps (project/branch/export); `menu.js`
// attaches its own generated wraps itself.
function setupMenus() {
  for (const wrap of document.querySelectorAll(".menu-wrap")) menu.attach(wrap);
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

async function newProjectPrompt(args) {
  const answer = await dialogs.prompt({
    view: "new-project",
    title: "New project",
    label: "Project name",
    value: (args && args.name) || "",
    pattern: bare(ID_RE),
    patternMessage: "lowercase letters, digits, _; max 40",
    okLabel: "Create",
  });
  if (answer == null) return;
  const name = answer.trim();
  try {
    await api.createProject(name);
  } catch (err) {
    toast(`Create failed: ${err.message}`, "error");
    return;
  }
  await refreshProjectsList();
  await loadProject(name);
}

async function openProjectPrompt(args) {
  const path = await dialogs.prompt({
    view: "open-project",
    title: "Open a project by path",
    label: "Absolute path",
    value: (args && args.path) || "",
    placeholder: "/home/you/cad/bracket",
    help: "The directory that holds project.json.",
    okLabel: "Open",
  });
  if (path == null) return;
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
// The switcher is a static .menu-wrap, attached by setupMenus() via
// shell/menu.js's attach(). It stays hidden when the server has no
// versioning routes — git missing means
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
      ["New branch…", () => newBranchPrompt()],
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
  if (!(await confirmDiscardEdits(null))) return; // another branch's scripts differ
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
  instanceMeshes.clear();
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

/** The picker behind `model.branches.delete`: the branch menu's per-row `×`
 *  knows which branch it names, an action reached from the palette or a menu
 *  does not — and an argument-free delete would name the CURRENT branch, which
 *  the server always refuses. */
async function deleteBranchPrompt(args) {
  if (!state.projectName) return;
  const deletable = (state.branches || [])
    .filter((b) => !b.is_current && !b.is_default);
  if (!deletable.length) {
    toast("No other branch to delete", "error");
    return;
  }
  const preset = args && args.branch;
  if (deletable.some((b) => b.name === preset)) {
    await deleteBranch(preset);
    return;
  }
  const values = await dialogs.form({
    view: "delete-branch",
    title: "Delete a branch",
    body: "Deletes the branch and its working tree. Versions (tags) made on "
      + "it survive. This cannot be undone.",
    danger: true,
    fields: [{
      name: "branch",
      label: "Branch",
      type: "select",
      // The current branch and the default one are absent, not disabled: the
      // server refuses both, and a row that can only fail is not a choice.
      //
      // It opens UNCHOSEN, and the empty option is what makes that a state the
      // form can be in: `required` then keeps the danger button disabled until
      // somebody deliberately picks a branch, so an Enter still travelling
      // from the palette row that opened this cannot land on a valid form.
      // (`focusFirst` puts focus on Cancel too — belt and braces, because this
      // is the one dialog in the slice that deletes a working tree.)
      options: [{ value: "", label: "— choose a branch —" },
                ...deletable.map((b) => b.name)],
      value: "",
      required: true,
    }],
    buttons: [{ id: "cancel", label: "Cancel" },
              { id: "delete", label: "Delete branch", kind: "danger",
                submits: true }],
  });
  if (!values) return;
  await runDeleteBranch(values.branch);
}

/** The branch menu's per-row `×`: the branch is already named, so the dialog
 *  is the plain danger confirm every other destructive action here uses
 *  (restore a version, discard a staged merge). */
async function deleteBranch(name) {
  if (!state.projectName) return;
  const ok = await dialogs.confirm({
    view: "delete-branch",
    title: `Delete branch “${name}” and its working tree?`,
    body: "Versions (tags) made on it survive. This cannot be undone.",
    danger: true,
    confirmLabel: "Delete branch",
  });
  if (!ok) return;
  await runDeleteBranch(name);
}

async function runDeleteBranch(name) {
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

async function newBranchPrompt(args) {
  if (!state.projectName) {
    toast("Open a project first", "error");
    return;
  }
  const answer = await dialogs.prompt({
    view: "new-branch",
    title: "New branch",
    label: "Branch name",
    value: (args && args.name) || "",
    pattern: bare(BRANCH_RE),
    patternMessage: "lowercase letters, digits, _ - /; max 64",
    help: `Branches off ${state.branch || "the current branch"}.`,
    okLabel: "Create",
  });
  if (answer == null) return;
  const name = answer.trim();
  try {
    await api.createBranch(state.projectName, name);
  } catch (err) {
    toast(`Create failed: ${err.message}`, "error");
    return;
  }
  toast(`Created ${name}`);
  await switchToBranch(name); // creating does not switch you server-side
}

/** One toolbar Export▾ row for an already-graded `actions.list()` entry
 *  (`kind`/`format`/`enabled` all ride on the spec — see
 *  `registerExportAction`). Kept a plain button, same markup the old
 *  hand-written HTML used, so `app.css`'s `.menu-item`/`.meta` styling needs
 *  no changes. */
function exportMenuItem(spec) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "menu-item";
  // The trailing flag distinguishes the structured-STEP assembly row from
  // the plain STEP row — both share kind:"assembly", format:"step".
  btn.dataset.export = `${spec.kind}:${spec.format}:${spec.structured ? "1" : "0"}`;
  btn.disabled = !spec.enabled;
  const label = document.createElement("span");
  label.textContent = spec.label || String(spec.format).toUpperCase();
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = `.${spec.format}`;
  btn.append(label, meta);
  return btn;
}

/** PRD-017 deliverable 2: the toolbar Export▾ menu's Part/Assembly rows are
 *  built from `actions.list()` — the SAME rows the generated File menu
 *  shows — every time the dropdown opens, rather than a second hand-written
 *  copy in `index.html`. A format registered late (the dynamic sync above,
 *  or a concurrent pack landing after boot) appears here the next time the
 *  menu opens, with zero code here. */
function renderExportSection(container, kind) {
  container.textContent = "";
  const ctx = actions.context();
  for (const spec of actions.list(ctx)) {
    if (spec.kind !== kind || !spec.id.startsWith("project.export.")) continue;
    container.appendChild(exportMenuItem(spec));
  }
}

function setupExportMenu() {
  const btn = document.getElementById("export-btn");
  const menu = document.getElementById("export-menu");
  const partItems = document.getElementById("export-part-items");
  const assemblyItems = document.getElementById("export-assembly-items");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!menu.classList.contains("hidden")) {
      setMenuHidden(menu, true);
      return;
    }
    renderExportSection(partItems, "part");
    renderExportSection(assemblyItems, "assembly");
    // Drawings are script-part only (the server rejects references).
    const hasPart = !!state.selectedPart;
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
    const [kind, format, structuredFlag] = item.dataset.export.split(":");
    runExport(kind, format, structuredFlag === "1");
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

function setupRepMode() {
  const box = document.getElementById("repmode");
  if (!box) return;
  for (const btn of box.querySelectorAll(".repmode-btn")) {
    btn.addEventListener("click", () => actions.run(
      `view.repmode.${btn.dataset.rep}`, null, { source: "toolbar" }));
  }
  updateRepModeToggle();
}

function setupUndo() {
  const undoBtn = document.getElementById("undo-btn");
  const redoBtn = document.getElementById("redo-btn");
  const run = (id) => () => actions.run(id, null, { source: "toolbar" });
  if (undoBtn) undoBtn.addEventListener("click", run("edit.undo"));
  if (redoBtn) redoBtn.addEventListener("click", run("edit.redo"));
  document.getElementById("fit-btn")
    ?.addEventListener("click", run("view.fit"));
}

// Shift-to-snap while dragging the gizmo (1 mm / 5°). NOT a shortcut: it is a
// modifier held during a drag, not a chord that runs a verb, so it stays a
// plain listener rather than entering the shortcut table. The placement card
// advertises it, so it must actually be wired.
function setupGizmoSnapKeys() {
  document.addEventListener("keydown", (e) => {
    if (e.key === "Shift") viewport.setGizmoSnap(true);
  });
  document.addEventListener("keyup", (e) => {
    if (e.key === "Shift") viewport.setGizmoSnap(false);
  });
  window.addEventListener("blur", () => viewport.setGizmoSnap(false));
}

// The v0.1 key handler and `toast()` used to live here. Both are shell modules
// now: chords are declared on actions (see registerActions) and dispatched by
// `shell/shortcuts.js`, notices come from `shell/toast.js`.

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

// PRD-026: the hand-rolled `#claim-modal` (its own Escape listener, its own
// backdrop handler, one module-level resolve slot that a second 409 would have
// overwritten) is gone. The dialog primitive already had this promise shape —
// it was modelled on it — so the callers' contract is unchanged: `true` to
// retry the write, `false` to give up, and it never resolves twice.
function askOverride(claim, partId) {
  const label = presence.labelFor(claim.holder);
  return dialogs.open({
    view: "claim",
    title: `${label} is editing ${partId}`,
    width: "narrow",
    danger: true,
    body:
      `${label} (${claim.holder}) has ${partId} open. Overriding takes the ` +
      "part, lands your change, and tells them it happened — their unsaved " +
      "edits are still theirs, and the next save wins.",
    note: `holder ${claim.holder} · ${claim.holder_kind} · claim expires on its own`,
    buttons: [
      { id: "cancel", label: "Cancel" },
      { id: "override", label: "Override", kind: "danger", submits: true },
    ],
  }).then((res) => res.ok);
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

// --------------------------------------------------------------- the actions
// PRD-026 FR7's "single action registry": every verb the workbench performs is
// registered here ONCE and reached three ways — the toolbar button calls
// `actions.run(id)`, the shortcut table binds whatever `shortcut` declares,
// and slice 3/4's palette and menu bar read the same list. Registered late in
// `boot()` (after `shortcuts.init`) so a declared chord is bound as it lands.
//
// Nothing is invented here: every entry is a verb the v0.1 toolbar already
// had, and every chord is one `setupKeys` already honoured. Actions for
// behaviour that does not exist yet (`help.palette`, `view.sidebar.toggle`,
// `model.branches.delete`) belong to the slices that build it — a registry row
// with nothing behind it is a menu that lies.

const notInField = (ctx) => !ctx.inField;

/** Click a button another module owns. The verb stays that module's (it keeps
 *  its own listener and its own preconditions); the action makes it reachable
 *  from the keyboard, the menus and the palette without a second copy of the
 *  logic that would then drift. */
function clickButton(id) {
  const btn = document.getElementById(id);
  if (btn) btn.click();
}

function enterMarket() {
  // A full reload into `#market` — boot() re-enters in market mode with the
  // viewport singleton to itself (the "reload rather than re-run boot()"
  // discipline showSignIn() uses).
  window.location.hash = "#market";
  window.location.reload();
}

// Module scope (not local to registerActions()): `syncDynamicExportFormats`
// below registers more of these after boot, once the live tool schema is in
// hand, and needs the same eligibility predicate the static rows use.
const hasProject = (c) => !!c.projectName;

// The next free "File" menu rank for a format `syncDynamicExportFormats`
// discovers that the static EXPORTS table below does not already cover
// (usd, structured/3mf assembly export, …) — after file/40-53's static rows
// and clear of file/60 ("Share a part…") below.
let nextExportMenuRank = 65;

/** One `project.export.<kind>.<format>` action — the static EXPORTS loop and
 *  `syncDynamicExportFormats` both funnel through this so the two can never
 *  register the same id with different shapes. `kind`/`format` ride on the
 *  action spec itself (extra properties `actions.register` does not mind) so
 *  the toolbar Export▾ menu can rebuild its rows straight from
 *  `actions.list()` — see `renderExportSection` — instead of keeping a
 *  second, hand-authored copy of "what formats exist" in `index.html`. */
function registerExportAction(kind, format, menuRank) {
  const id = `project.export.${kind}.${format}`;
  if (actions.get(id)) return; // already covered by the static table
  actions.register({
    id, kind, format,
    title: `Export ${kind} as ${String(format).toUpperCase()}`,
    group: "Export", menu: menuRank, keywords: ["save", "download"],
    when: hasProject,
    // Both halves read `ctx`, never module state: a synthetic context (the
    // palette's, a test's) must grade the row the same way the live one does.
    enabled: (c) => (kind === "part" ? !!c.selectedPart : !!c.hasInstances),
    run: () => runExport(kind, format),
  });
}

/** PRD-017 deliverable 2: the formats the STATIC table below does not know
 *  about, discovered from the live tool schema (`GET /api/tools`, the same
 *  registry the palette already reads) and registered the same way — so
 *  `usd` (flagged), a concurrent slice's `3mf` on `export_assembly`, or a
 *  future format all appear with zero code here once the pack that adds them
 *  mutates the schema. Best-effort: an unreachable server at boot just means
 *  the menu shows the static formats until the next call succeeds. */
async function syncDynamicExportFormats() {
  let payload;
  try {
    payload = await api.listTools();
  } catch {
    return;
  }
  const byName = new Map(
    ((payload && payload.tools) || []).map((t) => [t.name, t])
  );
  for (const [kind, toolName] of
       [["part", "export_part"], ["assembly", "export_assembly"]]) {
    const tool = byName.get(toolName);
    const props = tool && tool.input_schema && tool.input_schema.properties;
    const enumValues = props && props.format && Array.isArray(props.format.enum)
      ? props.format.enum
      : [];
    for (const format of enumValues) {
      registerExportAction(kind, format, `file/${nextExportMenuRank++}`);
    }
  }
}

function registerActions() {
  const A = (spec) => actions.register(spec);
  const hasPart = (c) => !!c.selectedPart;
  const inAssembly = (c) => c.mode === "assembly";
  const onBranch = (c) => !!c.branch;

  // ------------------------------------------------------------------ File
  A({ id: "project.new", title: "New project…", group: "Project",
      menu: "file/10", keywords: ["create"], run: () => newProjectPrompt() });
  A({ id: "project.open-path", title: "Open by path…", group: "Project",
      menu: "file/11", run: () => openProjectPrompt() });
  A({ id: "part.new", title: "New part…", group: "Parts", menu: "file/20",
      // Spec ruling 3. `when` (not `enabled`) on purpose: with no project open
      // there is no part to create and the File row genuinely should not be
      // there, so the chord declines and the browser keeps ⌘N — the one case
      // where we are not taking a keystroke we cannot act on.
      shortcut: "Mod+N",
      keywords: ["create", "add"], when: hasProject, run: () => addPart() });
  A({ id: "project.import-cad", title: "Import CAD file…", group: "Project",
      menu: "file/30", keywords: ["step", "stl", "brep"], when: hasProject,
      run: () => openImportPicker() });
  const EXPORTS = [
    ["part", "step", "file/40"], ["part", "stl", "file/41"],
    ["part", "3mf", "file/42"], ["part", "gltf", "file/43"],
    ["part", "glb", "file/44"],
    ["assembly", "step", "file/50"], ["assembly", "stl", "file/51"],
    ["assembly", "gltf", "file/52"], ["assembly", "glb", "file/53"],
    // Was reachable only via the un-awaited dynamic schema sync below (it
    // renders last, and vanishes entirely if the boot fetch fails) — a
    // static row so it is there from first paint like every other format.
    ["assembly", "3mf", "file/54"],
  ];
  for (const [kind, format, menu] of EXPORTS) {
    registerExportAction(kind, format, menu);
  }
  // PRD-017's Experience section promises the human path can produce a
  // structured assembly STEP (product tree, not the fused compound) —
  // today only the agent path (`export_assembly {structured: true}`)
  // could reach it. Same STEP format as the row above but explicitly
  // flagged, mirroring the STEP-part `callTool` precedent (`promptIncludePmi`).
  A({
    id: "project.export.assembly.step_structured",
    kind: "assembly", format: "step", structured: true,
    label: "STEP (structured)",
    title: "Export assembly as structured STEP (product tree)",
    group: "Export", menu: "file/55",
    keywords: ["save", "download", "structured", "product tree", "step"],
    when: hasProject,
    enabled: (c) => !!c.hasInstances,
    run: () => runExport("assembly", "step", true),
  });
  A({ id: "project.share", title: "Share a part…", group: "Project",
      menu: "file/60",
      // Hosted mode only: share-links.js unhides the button once it knows the
      // app has a public origin, so the button's visibility IS the capability.
      when: () => {
        const btn = document.getElementById("share-btn");
        return !!btn && !btn.classList.contains("hidden");
      },
      run: () => clickButton("share-btn") });

  // ------------------------------------------------------------------ Edit
  A({ id: "edit.undo", title: "Undo", group: "Edit", menu: "edit/10",
      // In a text field the browser's/CodeMirror's own text undo is the right
      // answer — the binding declines and does not preventDefault.
      shortcut: { chord: "Mod+Z", when: notInField },
      when: hasProject, run: () => undoLastChange() });
  A({ id: "edit.redo", title: "Redo", group: "Edit", menu: "edit/11",
      shortcut: [{ chord: "Mod+Y", when: notInField },
                 { chord: "Mod+Shift+Z", when: notInField }],
      when: hasProject, run: () => redoLastChange() });
  A({ id: "part.save-script", title: "Save & rebuild", group: "Parts",
      menu: "edit/20",
      // CodeMirror binds its own Cmd+S; everywhere else this catches it.
      shortcut: { chord: "Mod+S", when: (c) => !c.inCodeMirror },
      // `enabled`, NOT `when`: dispatch consults `when` BEFORE it
      // `preventDefault`s, so a `when`-gated ⌘S with no part selected would
      // fall through to the browser's "Save page as…" — which v0.1 always
      // suppressed outside CodeMirror. The menu still greys the row out, and
      // the run body keeps v0.1's own `if (state.part)` guard.
      enabled: hasPart,
      run: () => { if (state.part) inspector.saveIfDirty(); } });
  A({ id: "part.delete", title: "Delete part…", group: "Parts",
      menu: "edit/30", danger: true, when: hasPart,
      run: () => deletePart(state.selectedPart) });

  // ------------------------------------------------------------------ View
  A({ id: "view.fit", title: "Fit view", group: "View", menu: "view/10",
      shortcut: "F", keywords: ["zoom", "frame"], run: () => viewport.fit() });
  A({ id: "view.gizmo.translate", title: "Move gizmo", group: "View",
      menu: "view/20", shortcut: "G",
      when: (c) => inAssembly(c) && !!c.selectedInstance,
      run: () => setState({ gizmoMode: "translate" }) });
  A({ id: "view.gizmo.rotate", title: "Rotate gizmo", group: "View",
      menu: "view/21", shortcut: "R",
      when: (c) => inAssembly(c) && !!c.selectedInstance,
      run: () => setState({ gizmoMode: "rotate" }) });
  // view/30-32 belong to the panel toggles registered by `shell/layout.js`
  // (sidebar / inspector / chat dock), so the representation modes start at
  // 40 and the theme toggle at 50 — three groups, three separators, and no
  // two rows sharing a rank.
  A({ id: "view.repmode.full", title: "Full geometry", group: "View",
      menu: "view/40", when: inAssembly, run: () => setRepMode("full") });
  A({ id: "view.repmode.simplified", title: "Simplified proxies",
      group: "View", menu: "view/41", when: inAssembly,
      run: () => setRepMode("simplified") });
  A({ id: "view.theme.toggle", title: "Toggle light/dark theme", group: "View",
      menu: "view/50", run: () => theme.toggle() });

  // ----------------------------------------------------------------- Model
  A({ id: "model.sketch", title: "2D sketch…", group: "Model", menu: "model/10",
      when: hasPart, run: () => clickButton("sketch-btn") });
  A({ id: "model.library", title: "Parts library…", group: "Model",
      menu: "model/20", keywords: ["package", "screw", "insert"],
      run: () => library.open() });
  A({ id: "model.market", title: "Marketplace…", group: "Model",
      menu: "model/21", run: () => enterMarket() });
  // PRD-028's materials browser was the one adopted modal with no action row:
  // reachable from the toolbar and from the palette's "Open: …" *view* row, but
  // absent from the Model menu, which the user guide calls "a true map of what
  // the app can do". `model/22` is the free slot between Marketplace and
  // Versions. Same `run` as the toolbar button, and `panelApi.openMaterials`
  // (PRD-028's inspector Browse… path) is untouched.
  A({ id: "model.materials", title: "Materials…", group: "Model",
      menu: "model/22", when: hasProject,
      keywords: ["material", "steel", "aluminium", "density"],
      run: () => materials.open() });
  A({ id: "model.versions", title: "Versions…", group: "Model",
      menu: "model/30", when: onBranch, run: () => versions.open() });
  A({ id: "model.branches.new", title: "New branch…", group: "Model",
      menu: "model/31", when: onBranch, run: () => newBranchPrompt() });
  // Slice 1 could not register this: an argument-free delete would name the
  // branch you are ON, which the server always refuses. Slice 2's picker is
  // what makes the row honest, so it is gated on there BEING another branch
  // (the current one and the default one are never deletable).
  A({ id: "model.branches.delete", title: "Delete branch…", group: "Model",
      menu: "model/32", danger: true,
      when: (c) => !!c.branch && c.hasOtherBranches,
      run: () => deleteBranchPrompt() });
  A({ id: "model.proposals", title: "Proposals…", group: "Model",
      menu: "model/33", when: onBranch, run: () => proposals.open() });
  A({ id: "model.configs", title: "Configurations…", group: "Model",
      menu: "model/40", when: hasPart,
      run: () => configs.open(state.selectedPart) });
  A({ id: "model.drawing", title: "Drawing preview…", group: "Model",
      menu: "model/41", when: hasPart,
      run: () => drawings.previewSvg(state.projectName, state.selectedPart) });

  // ------------------------------------------------------------------ Help
  // help/10 is the command palette (`shell/palette.js`) — the row people reach
  // for first, and the one that leads to everything else.
  A({ id: "help.shortcuts", title: "Keyboard shortcuts", group: "Help",
      menu: "help/11", shortcut: "?", keywords: ["keys", "cheat sheet"],
      run: () => openShortcutSheet() });

  // The sketcher is a modal MODE, not a set of bindings: its `onKey`
  // stopPropagation's before the shortcut table ever sees the event. The
  // cheat-sheet still has to tell the truth about it, so those rows are
  // declared data (spec §6).
  shortcuts.declare({ chord: "Escape", group: "While sketching",
                      title: "Cancel the pending entity, then the selection, "
                             + "then close the sketch" });
  shortcuts.declare({ chord: "Delete", group: "While sketching",
                      title: "Delete the selected sketch entities" });

  // FR3: the views this module owns. The claim dialog is deliberately NOT
  // registered — it is a response to a 409, and nothing should be able to
  // conjure one.
  //
  // A view is registered when opening it OUT OF CONTEXT is a coherent thing to
  // ask for. `discard-edits` (a navigation guard whose "Discard" discards
  // nothing on its own) and `import-part-id` (it names a file that has already
  // been uploaded) are dialogs with a `view:` and no registry row for exactly
  // that reason — a registry row with nothing behind it is a menu that lies.
  dialogs.register("shortcuts", () => openShortcutSheet(), {
    title: "Keyboard shortcuts",
    description: "Every chord this workbench binds, grouped by area",
  });
  dialogs.register("new-part", (args) => addPart(args), {
    title: "New part…",
    description: "Create a part in the current project",
    when: (c) => !!c.projectName,
  });
  dialogs.register("delete-part", (args) => deletePart(
    (args && args.part) || state.selectedPart), {
    title: "Delete part…",
    description: "Delete a part, its script and every assembly instance of it",
    // Destructive: an agent may not conjure the confirm that leads to it.
    agentOpenable: false,
    when: (c) => !!c.selectedPart,
  });
  dialogs.register("new-project", (args) => newProjectPrompt(args), {
    title: "New project…", description: "Create a project",
  });
  dialogs.register("open-project", (args) => openProjectPrompt(args), {
    title: "Open project by path…",
    description: "Open a project directory that is not in the list",
  });
  dialogs.register("new-branch", (args) => newBranchPrompt(args), {
    title: "New branch…", description: "Branch the current project",
    when: (c) => !!c.branch,
  });
  dialogs.register("delete-branch", (args) => deleteBranchPrompt(args), {
    title: "Delete branch…",
    description: "Delete a branch other than the current or default one",
    agentOpenable: false,
    when: (c) => !!c.branch && c.hasOtherBranches,
  });
}

/** The toolbar's palette affordance. Its label is the CHORD, so it is read
 *  from the shortcut table rather than hard-coded: `index.html` cannot know
 *  whether this browser renders `⌘K` or `Ctrl+K`, and a Windows user seeing a
 *  Command glyph learns the wrong key. If the chord ever moves, this follows. */
function setupPaletteButton() {
  const btn = document.getElementById("palette-btn");
  if (!btn) return;
  const row = shortcuts.list().find((r) => r.actionId === "help.palette");
  if (row) {
    btn.textContent = row.label;
    btn.title = `Command palette (${row.label})`;
    btn.setAttribute("aria-keyshortcuts",
                     row.chord.replace(/^Mod\+/, navigator.platform
                       && /Mac/i.test(navigator.platform) ? "Meta+" : "Control+"));
  }
  btn.addEventListener("click",
    () => actions.run("help.palette", null, { source: "toolbar" }));
}

/** The "?" cheat-sheet: `shortcuts.list()` grouped by area. Built as DOM (not
 *  an HTML string) so no shortcut title can ever be interpolated as markup. */
function openShortcutSheet() {
  const groups = new Map();
  for (const row of shortcuts.list()) {
    if (!groups.has(row.group)) groups.set(row.group, []);
    groups.get(row.group).push(row);
  }
  const wrap = document.createElement("div");
  wrap.className = "dlg-keys";
  for (const [group, rows] of groups) {
    const head = document.createElement("h3");
    head.className = "dlg-keys-group";
    head.textContent = group;
    wrap.appendChild(head);
    for (const row of rows) {
      const line = document.createElement("div");
      line.className = "dlg-keys-row";
      const chord = document.createElement("kbd");
      chord.textContent = row.label;
      const title = document.createElement("span");
      title.textContent = row.title;
      line.append(chord, title);
      wrap.appendChild(line);
    }
  }
  return dialogs.open({
    view: "shortcuts",
    title: "Keyboard shortcuts",
    body: wrap,
    buttons: [{ id: "ok", label: "Close", kind: "primary" }],
  });
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
  // written to run once (comments.js registers an inspector decorator,
  // layout.js inserts its resize handles), so re-booting in place would be a
  // second, subtly different app.
  auth.renderSignIn(document.getElementById("auth-view"),
                    () => location.reload());
}

async function boot() {
  // The shell first: toasts and dialogs are used by everything below (auth
  // failures included), and the dialog stack must own its Esc listener before
  // any panel installs one of its own.
  initToasts(document.getElementById("toasts"));
  dialogs.init(document.getElementById("dialog-host"));
  theme.init(); // before viewport.init so the scene is born with the stored palette

  // Identity first: in hosted mode there is nothing to render until we know
  // who is asking, and every panel below would 401 on its first call.
  let identity = null;
  try {
    identity = await auth.session();
  } catch {
    identity = { mode: "local", principal: null };   // offline: fall through
  }
  // The marketplace is entered on the `#market` hash BEFORE the auth gate, so a
  // logged-out hosted visitor can browse/customize/download the public catalog
  // (add-to-library still needs a session). It takes over the whole page and
  // does not init the workbench, so the viewport singleton is its alone.
  if (window.location.hash.startsWith("#market")) {
    market.enter(identity, panelApi);
    return;
  }
  if (identity === null) {
    showSignIn();
    return;
  }
  window.addEventListener("agentcad:unauthenticated", showSignIn, { once: true });
  // Before the panels and before registerActions(): the table subscribes to
  // `actions.onChange`, so every chord an action declares is bound as the
  // action lands — and a conflict throws from that registration.
  shortcuts.init({ actions, dialogs });
  registerActions();
  // PRD-017 deliverable 2: not awaited — the static EXPORTS rows already work,
  // and this only ADDS rows (usd, a concurrent slice's assembly 3mf, …) once
  // the live schema answers. `actions.register` fires `onChange` per row, so
  // the File menu (and, on its next open, the toolbar Export▾ menu) update on
  // their own the moment each one lands.
  syncDynamicExportFormats();
  // After registerActions() (so the first render has every File/Edit/View/
  // Model/Help row) and after shortcuts.init() (so the three toggle actions
  // layout.init() registers get their chords bound through the same
  // onChange subscription as everything else).
  menu.init({ actions, host: document.getElementById("menubar") });
  layout.init({ workspace: "default", actions });
  // The UX-events client, then the dialog stack's emitter: from here every
  // `dialog_opened`/`dialog_submitted` reaches `POST /api/ui/events`
  // fire-and-forget. `events.emit` accepts the one-object shape the dialog
  // stack calls it with, so this line IS the wiring.
  events.init({ api });
  dialogs.setEmitter(events.emit);
  // The same eligibility context the menu and the palette read, injected (not
  // imported: `actions.js` imports `dialogs.js`) so `openView` can refuse a
  // view whose own `when` is false instead of running its opener out of
  // context — `ui_open {view: "merge"}` on a clean branch would otherwise start
  // a NEW merge.
  dialogs.setContext(actions.context);
  // One `palette_executed` per palette run, emitted from the registry's run
  // listener so that a menu/toolbar/shortcut run of the same action is never
  // recorded as a palette one. Rows the palette runs WITHOUT the registry
  // (a tool, a view, a navigation target) emit it themselves.
  actions.onRun(({ id, source }) => {
    if (source === "palette") events.emit("palette_executed", { action: id });
  });
  // After registerActions(): the palette registers `help.palette`/`Mod+K`
  // itself, and a chord conflict must throw from that registration.
  palette.init({ actions, dialogs, shortcuts, api, state, toast, events,
                 loadProject, selectPart });
  viewport.init(document.getElementById("viewport"), { onPick });
  tree.init(panelApi);
  inspector.init(panelApi);
  chat.init(panelApi);
  placement.init(panelApi);
  drawings.init(panelApi);
  sketcher.init(panelApi);
  versions.init(panelApi);
  merge.init(panelApi);
  proposals.init(panelApi);
  library.init(panelApi);
  configs.init(panelApi);
  materials.init(panelApi);
  presence.init();
  // After inspector.init: comments.js registers inspector's param decorator
  // and subscribes to `part` behind it, so a badge is applied to rows the
  // inspector has already built.
  comments.init(panelApi);
  setupMenus();
  setupProjectMenu();
  setupBranchMenu();
  setupProposals();
  setupExportMenu();
  setupShare(identity);
  setupImport();
  setupLibrary();
  document.getElementById("market-btn")?.addEventListener("click",
    () => actions.run("model.market", null, { source: "toolbar" }));
  setupPaletteButton();
  // Unlike Market, the materials browser is a MODAL inside the workbench
  // (PRD-028 Decision 10's ruling) — no navigation, no reload, assign mode
  // needs the workbench alive underneath it. Routed through the registry like
  // every other toolbar button, so the Model menu, the palette and this click
  // are the same verb (`setupLibrary()`'s shape).
  document.getElementById("materials-btn")?.addEventListener("click",
    () => actions.run("model.materials", null, { source: "toolbar" }));
  setupUndo();
  setupGizmoSnapKeys();
  setupRepMode();
  onKeys(["rebuilding", "connected"], renderIndicators);
  onKeys(["rebuilding", "part", "mode", "selectedPart", "selectedInstance"], updateHUD);
  onKeys(["mode"], updateRepModeToggle);
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

  // The `#materials` hash opens the modal AFTER the workbench inits — unlike
  // `#market` above (a full-page takeover entered BEFORE the auth gate, which
  // needs no project), this just opens an overlay on top of the workbench
  // boot() just built, so it waits for that project load to settle first.
  if (window.location.hash.startsWith("#materials")) {
    materials.open();
  }
}

boot();
