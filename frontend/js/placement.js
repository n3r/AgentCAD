// Assembly placement panel: a floating card over the viewport that edits the
// selected instance's transform. It mirrors the on-canvas move/rotate gizmo —
// numeric x/y/z + rx/ry/rz, a Move/Rotate toggle, a Shift-to-snap hint, and
// the deliberate "no scale handle" explainer (scale is parametric). When the
// instance is positioned by a mate it shows a read-only note instead: the
// transform is derived, so the gizmo and fields would lie.

import { api } from "./api.js";
import { state, setState, onKeys } from "./state.js";
import * as viewport from "./viewport.js";
import { dofEditor, patternSpec, patternDraft } from "./placement_model.js";

let actions = null;
let panel = null;
let bodyEl = null;
let sweeping = false; // a motion sweep animation is playing

// Rebuild the panel structure only when identity/mate/mode changes; otherwise
// just refresh the numeric values (so typing / a live drag isn't clobbered).
let sig = null;
let inputs = {}; // axis key -> <input>

const AXES = [
  ["px", "X"], ["py", "Y"], ["pz", "Z"],
  ["rx", "X"], ["ry", "Y"], ["rz", "Z"],
];

export function init(a) {
  actions = a;
  panel = document.getElementById("placement");
  bodyEl = document.getElementById("placement-body");
  onKeys(["mode", "selectedInstance", "assembly", "gizmoMode"], render);
  render();
}

function currentInstance() {
  if (state.mode !== "assembly" || !state.selectedInstance) return null;
  const list = state.assembly && state.assembly.instances;
  if (!list) return null;
  return list.find((i) => i.id === state.selectedInstance) || null;
}

// The base id owning a resolved member: a pattern member is `<base>[i]`, a
// sub-assembly member `<base>/...`. A plain instance is its own base.
function baseIdOf(id) {
  const br = id.indexOf("[");
  if (br >= 0) return id.slice(0, br);
  const sl = id.indexOf("/");
  if (sl >= 0) return id.slice(0, sl);
  return id;
}

// The RAW (un-expanded) instance the current selection belongs to — where a
// pattern lives. Patterns/sub-assemblies are edited on this base, not on the
// derived member the tree selected.
function rawBaseInstance() {
  const inst = currentInstance();
  if (!inst) return null;
  const raw = (state.project && state.project.assembly.instances) || [];
  return raw.find((r) => r.id === baseIdOf(inst.id)) || null;
}

function render() {
  const inst = currentInstance();
  if (!inst) {
    panel.classList.add("hidden");
    sig = null;
    return;
  }
  panel.classList.remove("hidden");
  const mated = !!inst.mate;
  const base = rawBaseInstance();
  const derived = !!(base && base.id !== inst.id);
  const patSig = base && base.pattern
    ? `${base.pattern.kind}:${base.pattern.count}` : "";
  // `config` is in the signature because the picker below is rebuilt with the
  // panel: a binding changed elsewhere (the agent, another tab) has to move
  // this <select>, and nothing else in the panel would notice. `patSig` +
  // `derived` are here so adding/clearing a pattern rebuilds the card.
  const nextSig =
    `${inst.id}|${mated}|${mated ? "" : state.gizmoMode}|${inst.config || ""}` +
    `|${derived}|${patSig}`;
  if (nextSig !== sig) {
    sig = nextSig;
    build(inst, mated);
  }
  if (!mated && !derived) syncValues(inst);
}

function build(inst, mated) {
  bodyEl.textContent = "";
  inputs = {};

  const title = document.createElement("div");
  title.className = "placement-title";
  const name = document.createElement("span");
  name.className = "placement-id";
  name.textContent = inst.id;
  const ref = document.createElement("span");
  ref.className = "placement-part";
  ref.textContent = inst.part;
  title.append(name, ref);
  bodyEl.appendChild(title);

  // BEFORE the mated early-return: a mate positions an instance, it does not
  // choose which configuration of the part is instanced, and the two questions
  // are independent. A mated instance can still be bound. A DERIVED member
  // (a pattern copy / sub-assembly internal) is not a raw instance, so binding
  // it would 404 — the config picker belongs to the base, not the member.
  const isDerived = !!(rawBaseInstance() && rawBaseInstance().id !== inst.id);
  if (!isDerived) {
    const config = configRow(inst);
    if (config) bodyEl.appendChild(config);
  }

  if (mated) {
    const note = document.createElement("div");
    note.className = "placement-mate";
    const m = inst.mate || {};
    const to = m.to_instance ? ` → ${m.to_instance}` : "";
    note.innerHTML =
      `<span class="placement-mate-tag">positioned by mate</span>` +
      `<div class="placement-mate-detail">${escapeHtml(
        (m.connector || "connector") + to
      )}</div>` +
      `<div class="placement-hint">Its transform is derived from the mate. ` +
      `Clear the mate to place it by hand.</div>`;
    bodyEl.appendChild(note);
    // A slider/planar mate exposes editable DOF fields (offset; u/v/spin),
    // committed via set_mate {dof} with clamp-on-out-of-range. A revolute keeps
    // the angle sweep it always had.
    const editor = dofEditor(inst.mate);
    if (editor && (editor.kind === "slider" || editor.kind === "planar")) {
      bodyEl.appendChild(dofFields(inst, editor));
    } else {
      bodyEl.appendChild(motionRow(inst));
    }
    return;
  }

  // A derived member (a pattern copy, or a sub-assembly's internal member): its
  // transform is not directly editable. Show what drives it, and — for a
  // pattern — the pattern editor bound to the owning base.
  const base = rawBaseInstance();
  if (base && base.id !== inst.id) {
    bodyEl.appendChild(derivedNote(base, inst));
    if (base.pattern) bodyEl.appendChild(patternEditor(base));
    return;
  }

  // Move / Rotate toggle — mirrors the g / r shortcuts.
  const seg = document.createElement("div");
  seg.className = "placement-seg";
  for (const [mode, label, key] of [
    ["translate", "Move", "G"],
    ["rotate", "Rotate", "R"],
  ]) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "placement-seg-btn";
    if (state.gizmoMode === mode) btn.classList.add("active");
    btn.innerHTML = `${label}<span class="placement-key">${key}</span>`;
    btn.addEventListener("click", () => setState({ gizmoMode: mode }));
    seg.appendChild(btn);
  }
  bodyEl.appendChild(seg);

  // Numeric transform grid.
  const grid = document.createElement("div");
  grid.className = "placement-grid";
  grid.appendChild(rowLabel("Position", "mm"));
  grid.appendChild(field("px"));
  grid.appendChild(field("py"));
  grid.appendChild(field("pz"));
  grid.appendChild(rowLabel("Rotation", "deg"));
  grid.appendChild(field("rx"));
  grid.appendChild(field("ry"));
  grid.appendChild(field("rz"));
  bodyEl.appendChild(grid);

  const hint = document.createElement("div");
  hint.className = "placement-hint";
  hint.textContent = "Drag the gizmo, or edit above. Hold Shift while dragging to snap (1 mm / 5°).";
  bodyEl.appendChild(hint);

  const scale = document.createElement("div");
  scale.className = "placement-scale";
  scale.title =
    "AgentCAD parts are parametric solids, not scaled meshes. There is no " +
    "scale handle by design — change a size by editing the part's parameters " +
    "so every dependent feature updates.";
  scale.innerHTML =
    `<span class="placement-scale-glyph" aria-hidden="true">⇲</span>` +
    `<span>No scale handle — resize via the part's <b>Parameters</b>.</span>`;
  bodyEl.appendChild(scale);

  // A plain part instance can grow a repeat pattern (a sub-assembly instance
  // can too, but sub-assemblies are added/edited from the tree, not here).
  if (inst.part) bodyEl.appendChild(patternEditor(inst));
}

// A short note for a derived (pattern / sub-assembly) member, mirroring the
// mate note: its pose comes from the base, not from a gizmo.
function derivedNote(base, member) {
  const note = document.createElement("div");
  note.className = "placement-mate";
  const kind = base.pattern ? `${base.pattern.kind} pattern`
    : base.assembly ? "sub-assembly" : "group";
  note.innerHTML =
    `<span class="placement-mate-tag">member of ${escapeHtml(base.id)}</span>` +
    `<div class="placement-mate-detail">${escapeHtml(kind)}</div>` +
    `<div class="placement-hint">${escapeHtml(member.id)}'s transform is ` +
    `derived. ${base.pattern ? "Edit the pattern below."
      : "Sub-assembly internals are read-only here."}</div>`;
  return note;
}

// The pattern editor: linear/polar, count, spacing (mm) / angle (deg). Commits
// via set_pattern; "Clear" removes the pattern (the base reverts to one body).
function patternEditor(base) {
  const draft = patternDraft(base);
  const wrap = document.createElement("div");
  wrap.className = "placement-pattern";
  wrap.appendChild(rowLabel("Pattern", draft.active ? draft.kind : "off"));

  const row = document.createElement("div");
  row.className = "placement-pattern-row";

  const kindSel = document.createElement("select");
  kindSel.className = "cfg-select";
  kindSel.setAttribute("aria-label", "pattern kind");
  for (const k of ["linear", "polar"]) {
    const opt = document.createElement("option");
    opt.value = k;
    opt.textContent = k;
    if (k === draft.kind) opt.selected = true;
    kindSel.appendChild(opt);
  }

  const countInp = motionInput("pattern count", String(draft.count));
  countInp.min = "1";
  countInp.step = "1";
  const spaceInp = motionInput("pattern spacing", String(draft.spacing));

  const spaceUnit = () =>
    (kindSel.value === "polar" ? "deg" : "mm");
  const spaceLabel = document.createElement("span");
  spaceLabel.className = "placement-unit";
  spaceLabel.textContent = spaceUnit();
  kindSel.addEventListener("change", () => {
    spaceLabel.textContent = spaceUnit();
  });

  row.append(kindSel, countInp, spaceInp, spaceLabel);
  wrap.appendChild(row);

  const btns = document.createElement("div");
  btns.className = "placement-pattern-btns";
  const apply = document.createElement("button");
  apply.type = "button";
  apply.className = "placement-sweep-btn";
  apply.textContent = draft.active ? "Update" : "Add pattern";
  apply.addEventListener("click", () =>
    commitPattern(base.id,
      patternSpec(kindSel.value, countInp.value, spaceInp.value)));
  btns.appendChild(apply);
  if (draft.active) {
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "placement-pattern-clear";
    clear.textContent = "Clear";
    clear.addEventListener("click", () => commitPattern(base.id, null));
    btns.appendChild(clear);
  }
  wrap.appendChild(btns);
  return wrap;
}

async function commitPattern(baseId, pattern) {
  const proj = state.projectName;
  if (!proj) return;
  try {
    const res = await api.callTool("set_pattern",
      { project: proj, instance: baseId, pattern });
    if (res && res.error) throw new Error(res.error.message || "error");
  } catch (err) {
    actions.toast(`Could not set pattern: ${err.message}`, "error");
    return;
  }
  sig = null;                     // force a rebuild from the new resolved state
  actions.refreshProject();
  actions.toast(pattern ? `${baseId} × ${pattern.count}` : `${baseId} pattern cleared`);
}

// Editable DOF fields for a slider/planar mate. Each field carries its set_mate
// `dof` key; "Apply" sends them and surfaces a clamp warning honestly.
function dofFields(inst, editor) {
  const wrap = document.createElement("div");
  wrap.className = "placement-dof";
  wrap.appendChild(rowLabel(`${editor.kind} DOF`, ""));

  const row = document.createElement("div");
  row.className = "placement-dof-row";
  for (const f of editor.fields) {
    const cell = document.createElement("div");
    cell.className = "placement-dof-cell";
    const lab = document.createElement("span");
    lab.className = "placement-dof-label";
    lab.textContent = `${f.label} (${f.unit})`;
    const inp = motionInput(`${f.label} ${f.unit}`, String(f.value));
    inp.dataset.dof = f.key;
    cell.append(lab, inp);
    row.appendChild(cell);
  }
  wrap.appendChild(row);

  const apply = document.createElement("button");
  apply.type = "button";
  apply.className = "placement-sweep-btn";
  apply.textContent = "Apply";
  apply.addEventListener("click", () => commitDof(inst, wrap));
  wrap.appendChild(apply);
  return wrap;
}

async function commitDof(inst, wrap) {
  const proj = state.projectName;
  const m = inst.mate;
  if (!proj || !m) return;
  const dof = {};
  for (const inp of wrap.querySelectorAll("input[data-dof]")) {
    const v = parseFloat(inp.value);
    if (Number.isFinite(v)) dof[inp.dataset.dof] = v;
  }
  let res;
  try {
    res = await api.callTool("set_mate", {
      project: proj, instance: inst.id, connector: m.connector,
      to_instance: m.to_instance, to_connector: m.to_connector, dof });
    if (res && res.error) throw new Error(res.error.message || "error");
  } catch (err) {
    actions.toast(`Could not set DOF: ${err.message}`, "error");
    return;
  }
  const clamps = (res.warnings || []).filter((w) => w.kind === "dof_clamped");
  actions.refreshProject();
  if (clamps.length) {
    const c = clamps[0];
    actions.toast(`Clamped ${c.dof} to ${c.clamped} (asked ${c.requested})`,
      "error");
  } else {
    actions.toast(`${inst.id} DOF updated`);
  }
}

// The instance's configuration binding. The declared family comes from
// `state.project.parts` — get_project carries `configs` per part — so opening
// the panel costs no request. Returns null for a part with no family, which is
// what keeps the panel identical to what it was before configurations existed.
function configRow(inst) {
  const parts = (state.project && state.project.parts) || [];
  const entry = parts.find((p) => p.id === inst.part);
  const declared = (entry && entry.configs) || {};
  const names = Object.keys(declared);
  if (!names.length) return null;

  const wrap = document.createElement("div");
  wrap.className = "placement-config";

  const label = document.createElement("span");
  label.className = "placement-config-label";
  label.textContent = "Config";
  wrap.appendChild(label);

  const select = document.createElement("select");
  select.className = "cfg-select";
  select.setAttribute("aria-label", `Configuration for instance ${inst.id}`);
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "— unbound —";
  none.title =
    "Unbound: the instance follows the part's live working state, including " +
    "its active configuration and any parameter overrides.";
  if (!inst.config) none.selected = true;
  select.appendChild(none);
  for (const name of names) {
    const cfg = typeof declared[name] === "object" ? declared[name] : {};
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = cfg.label || name;
    if (name === inst.config) opt.selected = true;
    select.appendChild(opt);
  }
  select.addEventListener("change", () =>
    setInstanceConfig(inst.id, select.value || null)
  );
  wrap.appendChild(select);
  return wrap;
}

async function setInstanceConfig(instanceId, config) {
  const proj = state.projectName;
  if (!proj) return;
  const inst = currentInstance();
  const partId = inst ? inst.part : null;
  const write = () => api.setInstanceConfig(proj, instanceId, config);
  try {
    try {
      await write();
    } catch (err) {
      // The same single-use override every other write in the app offers.
      // `set_instance_config` takes no `write_scope` today — a claim is a
      // *part* claim and this is a whole-manifest write — so this is defence
      // in depth rather than a path that fires now; handleWriteConflict
      // answers false for anything that is not an overridable 409, and the
      // error rethrows unchanged.
      if (!(await actions.handleWriteConflict(err, partId))) throw err;
      await write();
    }
  } catch (err) {
    actions.toast(`Could not bind ${instanceId}: ${err.message}`, "error");
    sig = null;              // the <select> is showing a value the server refused
    render();
    return;
  }
  // The bound instance is built from the configuration's PURE resolution, so
  // its mesh key moved: the assembly (and the sidebar's `part@config`) have to
  // be re-read, which is exactly what refreshProject does in assembly mode.
  actions.refreshProject();
  actions.toast(
    config ? `${instanceId} → ${config}` : `${instanceId} unbound`
  );
}

// Motion: drive the mate's DOF through an angle range and watch the sweep on
// stage. v1 driver UI — angle only, fixed sample count.
function motionRow(inst) {
  const wrap = document.createElement("div");
  wrap.className = "placement-motion";
  wrap.appendChild(rowLabel("Motion", "deg"));

  const row = document.createElement("div");
  row.className = "placement-motion-row";
  const from = motionInput("sweep from angle", "0");
  const to = motionInput("sweep to angle", "90");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "placement-sweep-btn";
  btn.textContent = "Sweep";
  btn.disabled = sweeping;
  btn.addEventListener("click", () => runSweep(inst, from, to));
  row.append(from, to, btn);
  wrap.appendChild(row);
  return wrap;
}

// The panel may rebuild mid-sweep (selection change), so re-enable whatever
// sweep button is live in the DOM rather than a possibly detached one.
function setSweepEnabled(on) {
  sweeping = !on;
  const btn = bodyEl && bodyEl.querySelector(".placement-sweep-btn");
  if (btn) btn.disabled = !on;
}

function motionInput(label, value) {
  const inp = document.createElement("input");
  inp.type = "number";
  inp.step = "any";
  inp.className = "placement-num";
  inp.value = value;
  inp.setAttribute("aria-label", label);
  return inp;
}

async function runSweep(inst, fromEl, toEl) {
  if (sweeping) return;
  const from = parseFloat(fromEl.value);
  const to = parseFloat(toEl.value);
  if (!Number.isFinite(from) || !Number.isFinite(to)) {
    actions.toast("Enter numeric from/to angles first", "error");
    return;
  }
  setSweepEnabled(false);
  let res;
  try {
    res = await api.callTool("sweep_motion", {
      project: state.projectName,
      instance: inst.id,
      angle_range: [from, to],
      samples: 16,
    });
  } catch (err) {
    setSweepEnabled(true);
    actions.toast(`Sweep failed: ${err.message}`, "error");
    return;
  }
  if (res.error) {
    setSweepEnabled(true);
    actions.toast(`Sweep failed: ${res.error.message || "error"}`, "error");
    return;
  }

  // Snapshot the real assembly transforms so the stage snaps back afterwards.
  const original = new Map();
  for (const i of (state.assembly && state.assembly.instances) || []) {
    original.set(i.id, { position: i.position, rotationDeg: i.rotation_deg });
  }
  for (const frame of res.frames || []) {
    for (const [id, t] of Object.entries(frame)) {
      viewport.setInstanceTransform(id, t.position, t.rotation_deg);
    }
    await new Promise((r) => setTimeout(r, 30));
  }
  for (const [id, t] of original) {
    viewport.setInstanceTransform(id, t.position, t.rotationDeg);
  }
  setSweepEnabled(true);
  if (res.clear) {
    actions.toast("Motion clear through range");
  } else {
    actions.toast(`Collision at ${res.first_collision}°`, "error");
  }
}

function rowLabel(text, unit) {
  const el = document.createElement("div");
  el.className = "placement-rowlabel";
  el.innerHTML = `${text}<span class="placement-unit">${unit}</span>`;
  return el;
}

function field(key) {
  const inp = document.createElement("input");
  inp.type = "number";
  inp.step = "any";
  inp.className = "placement-num";
  inp.setAttribute("aria-label", axisAria(key));
  inp.addEventListener("change", commit);
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      inp.blur();
    }
  });
  inputs[key] = inp;
  return inp;
}

function axisAria(key) {
  const kind = key[0] === "p" ? "position" : "rotation";
  return `${kind} ${key[1].toUpperCase()}`;
}

// Update the numeric fields from an instance (or a live gizmo transform),
// leaving whichever field the user is editing untouched.
function syncValues(inst) {
  const pos = inst.position || [0, 0, 0];
  const rot = inst.rotation_deg || [0, 0, 0];
  setVal("px", pos[0]); setVal("py", pos[1]); setVal("pz", pos[2]);
  setVal("rx", rot[0]); setVal("ry", rot[1]); setVal("rz", rot[2]);
}

// Called by main.js while the gizmo is being dragged.
export function updateLive(transform) {
  if (!transform) return;
  const p = transform.position, r = transform.rotationDeg;
  setVal("px", p[0]); setVal("py", p[1]); setVal("pz", p[2]);
  setVal("rx", r[0]); setVal("ry", r[1]); setVal("rz", r[2]);
}

function setVal(key, v) {
  const inp = inputs[key];
  if (!inp || document.activeElement === inp) return;
  inp.value = fmt(v);
}

function commit() {
  const inst = currentInstance();
  if (!inst || inst.mate) return;
  const position = [num("px"), num("py"), num("pz")];
  const rotation_deg = [num("rx"), num("ry"), num("rz")];
  if ([...position, ...rotation_deg].some((v) => !Number.isFinite(v))) return;
  actions.patchInstanceTransform(inst.id, { position, rotation_deg });
}

function num(key) {
  const inp = inputs[key];
  return inp ? parseFloat(inp.value) : NaN;
}

function fmt(v) {
  if (v == null || !Number.isFinite(v)) return "0";
  if (Object.is(v, -0) || Math.abs(v) < 5e-7) v = 0;
  return String(Math.round(v * 1e4) / 1e4);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
