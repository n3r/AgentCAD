// Assembly placement panel: a floating card over the viewport that edits the
// selected instance's transform. It mirrors the on-canvas move/rotate gizmo —
// numeric x/y/z + rx/ry/rz, a Move/Rotate toggle, a Shift-to-snap hint, and
// the deliberate "no scale handle" explainer (scale is parametric). When the
// instance is positioned by a mate it shows a read-only note instead: the
// transform is derived, so the gizmo and fields would lie.

import { state, setState, onKeys } from "./state.js";

let actions = null;
let panel = null;
let bodyEl = null;

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

function render() {
  const inst = currentInstance();
  if (!inst) {
    panel.classList.add("hidden");
    sig = null;
    return;
  }
  panel.classList.remove("hidden");
  const mated = !!inst.mate;
  const nextSig = `${inst.id}|${mated}|${mated ? "" : state.gizmoMode}`;
  if (nextSig !== sig) {
    sig = nextSig;
    build(inst, mated);
  }
  if (!mated) syncValues(inst);
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
