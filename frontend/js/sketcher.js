// 2D sketch editor overlay. A themed SVG canvas over the viewport: draw
// points / line chains / circles / arcs / splines / slots, apply constraints,
// drag geometry, and every mutation round-trips the whole spec through POST
// /api/sketch/solve (the first-party scipy solver) — solved coordinates
// re-render the canvas.
//
// **Emission lives on the server.** "Insert" asks the same route for
// `emit: "function"` and pastes the code it returns, so the GUI and an agent
// produce byte-identical build123d for the same spec (PRD-009 AC1). There is
// deliberately no snippet builder, no number formatter for code and no chain
// finder in this file: `agentcad/core/sketch_emit.py` owns all three, behind a
// closure gate the browser cannot skip.
//
// **Round-trip persistence lives on the server too** (FR10). "Insert" asks for
// `persist: <name>`, so the pasted code carries a marker, its whole spec as
// JSON and a hash over the code; opening the sketcher reads them back through
// POST /api/sketch/blocks. The hash is the only thing that can tell a hand
// edit from a fresh block, so it is computed in exactly one place — the same
// module that wrote it — and this file never re-implements it.
//
// Sketch plane is XY, y up (SVG y-axis is flipped via a scale(1,-1) group);
// units are mm, 1 SVG user unit == 1 mm, zoom via the wheel.

import { api } from "./api.js";
import { state, onKeys } from "./state.js";
import * as editor from "./editor.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const SNAP_PX = 10; // screen px within which a click reuses an existing point
const DRAG_PX = 3; // screen px of movement before a press becomes a drag
const PULSE_MS = 1800; // how long a DOF-chip highlight stays up
const FULL_TURN_DEG = 359.99; // an SVG arc command cannot express a full turn

let actions = null;
let host = null; // #sketcher
let btn = null; // #sketch-btn
let svg = null;
let worldG = null; // y-flipped group holding grid + entities
let previewG = null; // rubber-band + drag overlays (redrawn on pointermove)
let dofEl = null; // the DOF chip
let chipsEl = null;
let insertBtn = null;
let toolBtns = {}; // tool name -> button
let conBtns = {}; // constraint name -> button

let open = false;
let tool = "select"; // select | point | line | circle | arc | arc3 | arcTan | ellipse | spline | slot
let model = null; // {points, lines, circles, arcs, splines, slots, constraints}
let seq = null; // name counters
let selection = []; // [{kind, name}] kind: point|handle|line|circle|arc|ellipse|spline|slot
let chainPrev = null; // line/arc tools: previous point *ref* in the chain
let pending = null; // multi-click tool state: {kind, ...}
let cursor = null; // last pointer sketch coords (for previews)
let scale = 4; // px per mm
let solveSeq = 0;
// Generation counter for the round-trip block lookup. `/api/sketch/blocks`
// answers into a canvas the user may have started drawing on meanwhile, and
// `openBlock` calls `resetModel()` — so a slow response used to discard
// in-progress geometry with no undo. Every reset and every new lookup bumps
// it; a response from an older generation is dropped.
let blocksSeq = 0;
// `"<project>::<part>"` the current model belongs to. A sketch is part-scoped:
// switching parts (or projects) starts a new one rather than carrying the old
// geometry, plane and block name across. See `init`.
let sketchOwner = null;
// Generation counter for the reopened-face check (see `checkReopenedFace`).
let faceSeq = 0;
// The face this sketch lives on: {origin, x_dir, y_dir, normal, face_index,
// part} from the `sketch_plane` tool, plus the projected boundary edges. It
// rides along to the server on every solve, because the *emitter* needs it —
// sketch-on-face coordinates without their basis are arbitrary.
let plane = null;
// `local: true` marks a verdict computed in the browser (no residuals to
// solve), so the chip can tell "nothing to solve" from a server answer.
let solveState = { ok: true, dof: 0, local: true };
let drag = null; // active pointer drag — see startDrag()
let press = null; // pointerdown that has not yet become a click or a drag
// The round-trip block this sketch was opened from, and the read-only latch a
// **diverged** block opens under: the code is the source of truth for
// geometry, so until the user chooses (re-solve from the spec / keep the hand
// edit) nothing here may edit or emit. See `openBlock`.
let blockName = null;
let readOnly = false;
let bannerEl = null;
let highlight = { entities: new Set(), constraints: new Set() };
let highlightTimer = null;

// Canvas palette, read from CSS custom properties so the sketch follows the
// app's light/dark theme instead of hard-coding a dark ramp.
let palette = null;

// ------------------------------------------------------------------ setup

export function init(a) {
  actions = a;
  host = document.getElementById("sketcher");
  btn = document.getElementById("sketch-btn");
  if (!host || !btn) return;
  btn.addEventListener("click", () => (open ? close() : show()));
  onKeys(["mode", "selectedPart", "projectName"], () => {
    const partMode = state.mode === "part";
    btn.classList.toggle("hidden", !partMode);
    if (!partMode && open) close();
    // **The sketch belongs to the part it was drawn on.** Nothing used to
    // reset it: `model`, `plane`, `blockName`, `readOnly`, `selection` and the
    // banner all survived a part switch, so Insert appended part A's
    // `sketch_profile()` into part B's script — and if A was a sketch-on-face,
    // every solve still shipped A's face basis. Switching parts starts a new
    // sketch; the old one lives in whatever script it was inserted into.
    const key = `${state.projectName || ""}::${state.selectedPart || ""}`;
    if (key === sketchOwner) return;
    sketchOwner = key;
    resetModel();
    if (!bannerEl) return;          // pre-`buildUI`: nothing on screen yet
    hideBanner();
    applyReadOnly();
    render();
    renderStatus();
    if (open) refreshBlocks();
  });
  // The canvas palette comes from CSS custom properties, so a theme switch
  // has to invalidate it. theme.js flips `data-theme` on <html> and does not
  // publish a state key, so watch the attribute rather than edit that module.
  new MutationObserver(() => {
    palette = null;
    if (open) render();
  }).observe(document.documentElement, {
    attributes: true, attributeFilter: ["data-theme"],
  });
  document.addEventListener("keydown", onKey);
  resetModel();
  buildUI();
}

/** Open the sketcher on a part face, with its boundary edges as references.
 *
 *  The references enter the model as ordinary entities that happen to be
 *  **fixed and construction-marked**: fixed means they add no DOF and cannot
 *  be dragged, construction means the emitter never writes them as geometry.
 *  Both flags are the server's (`core/tools_sketch.reference_entities`) — the
 *  browser only carries them, because a browser-only flag would be a lie the
 *  emitter never sees. */
export function openOnFace(info) {
  resetModel();
  // Claim the current part for this sketch, so a `selectedPart` event that
  // arrives after the face pick cannot reset what was just opened.
  sketchOwner = `${state.projectName || ""}::${state.selectedPart || ""}`;
  plane = {
    origin: info.origin, x_dir: info.x_dir, y_dir: info.y_dir,
    normal: info.normal, face_index: info.face_index,
    part: info.part_id ? `build(p)` : undefined,
    // The face's own identity (area + normal + origin), so reopening this
    // sketch can *check* that the ordinal still points at the same face
    // instead of silently re-solving on a renumbered one. Measured on the
    // prototyping enclosure: `corner_r: 6.0` turns face 37 from a 5989 mm^2
    // base plate into a 51 mm^2 sliver. It rides in the persisted spec.
    face_id: info.face_id,
  };
  const ents = info.entities || {};
  for (const p of ents.points || []) model.points.push({ ...p });
  for (const l of ents.lines || []) model.lines.push({ ...l });
  for (const c of ents.circles || []) model.circles.push({ ...c });
  for (const a of ents.arcs || []) model.arcs.push({ ...a });
  const skipped = (info.refs || []).filter((r) => !r.constrainable).length;
  show();
  solveAndRender("full");
  if (skipped) {
    // A documented gap, said out loud: a spline or elliptical boundary edge
    // has no exact entity in the solver's vocabulary, so it is not offered as
    // a constraint target rather than approximated into one.
    actions.toast(`${skipped} boundary edge(s) are neither lines nor circles `
                  + "and were not projected — they cannot be constraint targets",
                  "info");
  }
}

function isConstruction(entity) {
  return !!(entity && entity.construction);
}

function resetModel() {
  model = {
    points: [], lines: [], circles: [], arcs: [], ellipses: [], splines: [],
    slots: [], constraints: [],
  };
  seq = { p: 0, l: 0, c: 0, a: 0, e: 0, sp: 0, sl: 0 };
  plane = null;
  blockName = null;
  readOnly = false;
  selection = [];
  chainPrev = null;
  pending = null;
  drag = null;
  press = null;
  clearHighlight();
  solveState = { ok: true, dof: 0, local: true };
  // Anything in flight belongs to the model that just went away.
  solveSeq++;
  blocksSeq++;
  faceSeq++;
}

function show() {
  if (state.mode !== "part") return;
  open = true;
  host.classList.remove("hidden");
  btn.classList.add("active");
  render();
  renderStatus();
  refreshBlocks();
}

function close() {
  open = false;
  host.classList.add("hidden");
  btn.classList.remove("active");
}

function colors() {
  if (palette) return palette;
  const cs = getComputedStyle(host);
  const v = (name, fallback) =>
    (cs.getPropertyValue(name) || "").trim() || fallback;
  palette = {
    grid: v("--sk-grid", "#232529"),
    axis: v("--sk-axis", "#33363d"),
    curve: v("--sk-curve", "#c9ced6"),
    point: v("--sk-point", "#8b919b"),
    fixed: v("--sk-fixed", "#d99a4e"),
    sel: v("--sk-sel", "#e8b06a"),
    ghost: v("--sk-ghost", "#d99a4e"),
    flag: v("--sk-flag", "#e0655c"),
    ring: v("--sk-ring", "#17181b"),
  };
  return palette;
}

// --------------------------------------------------------------------- UI

function skButton(label, title, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "sk-btn";
  b.textContent = label;
  b.title = title;
  b.addEventListener("click", onClick);
  return b;
}

function buildUI() {
  host.textContent = "";

  const bar = document.createElement("div");
  bar.className = "sk-bar";

  for (const [name, label, title] of [
    ["select", "Select", "Select entities (shift-click adds); drag a point or an arc end to solve"],
    ["point", "Point", "Click to place points"],
    ["line", "Line", "Click-click line chains; Esc ends the chain"],
    ["circle", "Circle", "Press at the center, drag the radius"],
    ["arc", "Arc", "Click the center, then the start, then the end"],
    ["arc3", "Arc3", "Click the start, then the end, then a point on the arc"],
    ["arcTan", "ArcT", "Continue the chain with an arc tangent to the last segment"],
    ["ellipse", "Ellipse", "Click the center, then the end of the major axis, then a point for the minor"],
    ["spline", "Spline", "Click through points; Esc ends the spline"],
    ["slot", "Slot", "Click both cap centers, then give the width"],
  ]) {
    const b = skButton(label, title, () => setTool(name));
    toolBtns[name] = b;
    bar.appendChild(b);
  }
  bar.appendChild(sep());

  for (const [name, label, title] of [
    ["fixed", "Fix", "Fix / unfix the selected point (first point is auto-fixed)"],
    ["coincident", "Coin", "Make two selected points coincident"],
    ["distance", "Dist", "Distance between two selected points"],
    ["horizontal", "H", "Make the selected line horizontal"],
    ["vertical", "V", "Make the selected line vertical"],
    ["parallel", "Par", "Make two selected lines parallel"],
    ["perpendicular", "Perp", "Make two selected lines perpendicular"],
    ["radius", "Rad", "Set the selected circle's or arc's radius"],
    ["tangent", "Tan", "Tangency between the two selected curves"],
    ["symmetric", "Sym", "Mirror two selected points about the selected line"],
    ["equal", "Eq", "Equal length (two lines) or equal radius (two curves)"],
    ["concentric", "Conc", "Share a center between the two selected curves"],
  ]) {
    const b = skButton(label, title, () => applyConstraint(name));
    conBtns[name] = b;
    bar.appendChild(b);
  }
  bar.appendChild(sep());

  bar.appendChild(skButton("Delete", "Delete the selection (Del)", deleteSelection));
  bar.appendChild(skButton("Clear", "Clear the whole sketch", () => {
    // `resetModel` drops the read-only latch with the model it belonged to;
    // the banner has to go with them. Clearing the canvas never edits the
    // script, so a diverged block is still on disk, and reopening finds it.
    resetModel();
    hideBanner();
    applyReadOnly();
    render();
    renderStatus();
  }));

  dofEl = document.createElement("button");
  dofEl.type = "button";
  dofEl.className = "sk-dof";
  dofEl.addEventListener("click", onDofClick);
  bar.appendChild(dofEl);

  const spacer = document.createElement("span");
  spacer.className = "sk-spacer";
  bar.appendChild(spacer);

  insertBtn = skButton("Insert → script", "Emit build123d for the solved sketch and append it to the editor", insertSnippet);
  insertBtn.classList.add("sk-primary");
  bar.appendChild(insertBtn);
  bar.appendChild(skButton("✕", "Close the sketcher", close));

  svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "sk-canvas");
  svg.addEventListener("pointerdown", onPointerDown);
  svg.addEventListener("pointermove", onPointerMove);
  svg.addEventListener("pointerup", onPointerUp);
  svg.addEventListener("pointercancel", onPointerUp);
  svg.addEventListener("wheel", onWheel, { passive: false });

  chipsEl = document.createElement("div");
  chipsEl.className = "sk-chips";

  // The round-trip banner (FR10). It sits between the toolbar and the canvas
  // because a divergence has to be read *before* the geometry below it is
  // trusted, and it is the only surface that can say "this code was edited by
  // hand" — slice 10 left it with nowhere to live.
  bannerEl = document.createElement("div");
  bannerEl.className = "sk-banner hidden";

  host.append(bar, bannerEl, svg, chipsEl);
  setTool("line");

  new ResizeObserver(() => open && render()).observe(svg);
}

function sep() {
  const s = document.createElement("span");
  s.className = "sk-sep";
  return s;
}

// Tools that build one continuous chain. Switching *between* them keeps the
// chain alive — "line, then a tangent arc, then a line" is the whole point of
// the tangent-arc tool, and it has nothing to be tangent to if picking it up
// drops the chain.
const CHAIN_TOOLS = ["line", "arc3", "arcTan"];

function setTool(name) {
  if (!(CHAIN_TOOLS.includes(name) && CHAIN_TOOLS.includes(tool))) {
    chainPrev = null;
  }
  tool = name;
  pending = null;
  for (const [n, b] of Object.entries(toolBtns)) {
    b.classList.toggle("active", n === name);
  }
  renderPreview();
}

/** Display-only number formatting (prompts and constraint chips).
 *  Emission formats its own literals server-side, at 9 decimals behind a
 *  closure gate — never reuse this for code. */
function fmtVal(v) {
  const x = Math.round(v * 1e4) / 1e4;
  return String(Object.is(x, -0) ? 0 : x);
}

// -------------------------------------------------------------- selection

function isSelected(kind, name) {
  return selection.some((s) => s.kind === kind && s.name === name);
}

function toggleSelect(kind, name, additive) {
  if (!additive) {
    const already = selection.length === 1 && isSelected(kind, name);
    selection = already ? [] : [{ kind, name }];
  } else if (isSelected(kind, name)) {
    selection = selection.filter((s) => !(s.kind === kind && s.name === name));
  } else {
    selection.push({ kind, name });
  }
  render();
  renderStatus();
}

function selectedOf(...kinds) {
  return selection.filter((s) => kinds.includes(s.kind)).map((s) => s.name);
}

// ------------------------------------------------------- entities & refs

function point(name) {
  return model.points.find((p) => p.name === name);
}

function arcOf(name) {
  return model.arcs.find((a) => a.name === name);
}

function curveOf(name) {
  return model.circles.find((c) => c.name === name) || arcOf(name);
}

function ellipseOf(name) {
  return model.ellipses.find((e) => e.name === name);
}

/** A point on an ellipse at eccentric anomaly `deg` — the solver's own
 *  parametrization, and build123d's (measured to 8.9e-16 mm). */
function ellipsePoint(e, ctr, deg) {
  const t = (deg * Math.PI) / 180;
  const phi = (e.rotation * Math.PI) / 180;
  const lx = e.a * Math.cos(t);
  const ly = e.b * Math.sin(t);
  return { x: ctr.x + lx * Math.cos(phi) - ly * Math.sin(phi),
           y: ctr.y + lx * Math.sin(phi) + ly * Math.cos(phi) };
}

/** `p3` -> the point; `a1.start` / `a1.end` -> the arc's virtual handle.
 *  Returns `{x, y}` or null. Arc handles are *derived*, never stored: the
 *  solver owns the arc's centre/radius/angles and the endpoints follow. */
function refCoords(ref) {
  if (!ref) return null;
  const dot = ref.indexOf(".");
  if (dot < 0) {
    const p = point(ref);
    return p ? { x: p.x, y: p.y } : null;
  }
  const base = ref.slice(0, dot);
  const which = ref.slice(dot + 1);
  const ell = ellipseOf(base);
  if (ell) {
    const ec = point(ell.center);
    if (!ec) return null;
    if (which === "center") return { x: ec.x, y: ec.y };
    if (which === "major") return ellipsePoint(ell, ec, 0);
    if (which === "minor") return ellipsePoint(ell, ec, 90);
    if (!ell.bounded || (which !== "start" && which !== "end")) return null;
    return ellipsePoint(ell, ec,
                        which === "start" ? ell.start_deg : ell.end_deg);
  }
  const arc = arcOf(base);
  if (!arc || (which !== "start" && which !== "end")) return null;
  const c = point(arc.center);
  if (!c) return null;
  const t = ((which === "start" ? arc.start_deg : arc.end_deg) * Math.PI) / 180;
  return { x: c.x + arc.r * Math.cos(t), y: c.y + arc.r * Math.sin(t) };
}

/** Every point-like ref a click can snap to: real points and arc handles. */
function allRefs() {
  const refs = model.points.map((p) => ({ ref: p.name, kind: "point" }));
  for (const a of model.arcs) {
    refs.push({ ref: `${a.name}.start`, kind: "handle" },
              { ref: `${a.name}.end`, kind: "handle" });
  }
  for (const e of model.ellipses) {
    // `.major` / `.minor` are the ends of the two semi-axes: ordinary point
    // handles, which is how the existing vocabulary pins an ellipse's size
    // and orientation without a single new constraint type.
    refs.push({ ref: `${e.name}.major`, kind: "handle" },
              { ref: `${e.name}.minor`, kind: "handle" });
    if (e.bounded) {
      refs.push({ ref: `${e.name}.start`, kind: "handle" },
                { ref: `${e.name}.end`, kind: "handle" });
    }
  }
  return refs;
}

function nearRef(x, y) {
  const tol = SNAP_PX / scale;
  let best = null;
  let bestD = tol;
  for (const { ref } of allRefs()) {
    const c = refCoords(ref);
    if (!c) continue;
    const d = Math.hypot(c.x - x, c.y - y);
    if (d <= bestD) {
      best = ref;
      bestD = d;
    }
  }
  return best;
}

// ------------------------------------------------------------ model edits

function addPoint(x, y) {
  const name = `p${++seq.p}`;
  // The very first point anchors the sketch (removes 2 rigid-body DOF).
  const fixed = model.points.length === 0;
  model.points.push({ name, x, y, fixed });
  return name;
}

/** A chain joint: `ref` may be a real point or an arc's virtual handle, and a
 *  handle is joined with a `coincident` rather than by sharing a point — the
 *  arc owns its endpoint, so that is the only way to pin it. */
function joinRef(ref, x, y) {
  if (!ref) return addPoint(x, y);
  if (ref.includes(".")) {
    const p = addPoint(x, y);
    model.constraints.push({ type: "coincident", p: ref, q: p });
    return p;
  }
  return ref;
}

function addLine(p1, p2) {
  const dup = model.lines.some(
    (l) => (l.p1 === p1 && l.p2 === p2) || (l.p1 === p2 && l.p2 === p1));
  if (dup || p1 === p2) return null;
  const name = `ln${++seq.l}`;
  model.lines.push({ name, p1, p2 });
  return name;
}

function addArc(centerRef, r, startDeg, endDeg) {
  const name = `a${++seq.a}`;
  model.arcs.push({ name, center: centerRef, r, start_deg: startDeg,
                    end_deg: endDeg });
  return name;
}

function addEllipse(centerRef, a, b, rotation) {
  const name = `e${++seq.e}`;
  model.ellipses.push({ name, center: centerRef, a, b, rotation,
                        bounded: false });
  return name;
}

function mutated() {
  render();
  solveAndRender();
}

/** Every entity name a constraint points at (`kind`/`type` are not refs). */
const REF_KEYS = ["p", "q", "at", "ln", "l1", "l2", "c", "c1", "c2",
                  "a", "b", "about"];

function constraintRefs(con) {
  const out = [];
  for (const k of REF_KEYS) {
    if (typeof con[k] === "string") out.push(con[k]);
  }
  return out;
}

function baseName(ref) {
  const dot = ref.indexOf(".");
  return dot < 0 ? ref : ref.slice(0, dot);
}

function deleteSelection() {
  if (readOnly || !selection.length) return;
  const gone = new Set();
  for (const s of selection) {
    if (s.kind === "handle") gone.add(baseName(s.name)); // deleting an end
    else gone.add(s.name);                               // deletes the arc
  }
  // Cascade: an entity whose own reference is gone goes too. Iterate until
  // the set stops growing — a slot's centre can take the slot, whose arcs a
  // line may in turn have been built on.
  for (;;) {
    const before = gone.size;
    for (const c of model.circles) if (gone.has(c.center)) gone.add(c.name);
    for (const a of model.arcs) if (gone.has(a.center)) gone.add(a.name);
    for (const e of model.ellipses) if (gone.has(e.center)) gone.add(e.name);
    for (const l of model.lines) {
      if (gone.has(baseName(l.p1)) || gone.has(baseName(l.p2))) gone.add(l.name);
    }
    for (const sl of model.slots) {
      if (gone.has(sl.c1) || gone.has(sl.c2)) gone.add(sl.name);
    }
    for (const sp of model.splines) {
      const left = sp.points.filter((n) => !gone.has(n));
      if (left.length < 2) gone.add(sp.name);
    }
    if (gone.size === before) break;
  }
  model.points = model.points.filter((p) => !gone.has(p.name));
  model.lines = model.lines.filter((l) => !gone.has(l.name));
  model.circles = model.circles.filter((c) => !gone.has(c.name));
  model.arcs = model.arcs.filter((a) => !gone.has(a.name));
  model.ellipses = model.ellipses.filter((e) => !gone.has(e.name));
  model.slots = model.slots.filter((s) => !gone.has(s.name));
  model.splines = model.splines.filter((s) => !gone.has(s.name));
  for (const sp of model.splines) {
    sp.points = sp.points.filter((n) => !gone.has(n));
  }
  // A deleted slot takes its whole compiled group: constraints may name
  // `slot1.arc_a`, which stops existing the moment the slot does.
  model.constraints = model.constraints.filter(
    (con) => !constraintRefs(con).some((r) => gone.has(baseName(r))));
  selection = [];
  chainPrev = null;
  clearHighlight();
  mutated();
}

// ------------------------------------------------------------ constraints

function applyConstraint(name) {
  if (readOnly) return;
  const pts = selectedOf("point", "handle");
  const lns = selectedOf("line");
  const crv = selectedOf("circle", "arc");
  const ell = selectedOf("ellipse");
  // `tangent` and `concentric` take any curve; `radius` and `equal_radius`
  // have to know the difference, because an ellipse has two radii and neither
  // is "the" one.
  const anyCrv = [...crv, ...ell];
  if (name === "fixed" && selectedOf("point").length === 1) {
    const p = point(selectedOf("point")[0]);
    p.fixed = !p.fixed;
    mutated();
    return;
  }
  if (name === "coincident" && pts.length === 2) {
    model.constraints.push({ type: "coincident", p: pts[0], q: pts[1] });
  } else if (name === "distance" && pts.length === 2) {
    const a = refCoords(pts[0]);
    const b = refCoords(pts[1]);
    const d = parseFloat(prompt("Distance (mm):",
                                fmtVal(Math.hypot(a.x - b.x, a.y - b.y))));
    if (!Number.isFinite(d) || d < 0) return;
    model.constraints.push({ type: "distance", p: pts[0], q: pts[1], d });
  } else if (name === "horizontal" && lns.length === 1) {
    model.constraints.push({ type: "horizontal", ln: lns[0] });
  } else if (name === "vertical" && lns.length === 1) {
    model.constraints.push({ type: "vertical", ln: lns[0] });
  } else if (name === "parallel" && lns.length === 2) {
    model.constraints.push({ type: "parallel", l1: lns[0], l2: lns[1] });
  } else if (name === "perpendicular" && lns.length === 2) {
    model.constraints.push({ type: "perpendicular", l1: lns[0], l2: lns[1] });
  } else if (name === "radius" && crv.length === 1) {
    const c = curveOf(crv[0]);
    const r = parseFloat(prompt("Radius (mm):", fmtVal(c.r)));
    if (!Number.isFinite(r) || r <= 0) return;
    model.constraints.push({ type: "radius", c: crv[0], r });
  } else if (name === "radius" && ell.length === 1) {
    // Two semi-axes, two constraints on the two scalar handles `e1.a`/`e1.b`.
    // Either prompt may be cancelled or left blank to pin only the other one.
    const e = ellipseOf(ell[0]);
    const a = parseFloat(prompt("Semi-axis a (mm), blank to leave free:",
                                fmtVal(e.a)));
    const b = parseFloat(prompt("Semi-axis b (mm), blank to leave free:",
                                fmtVal(e.b)));
    if (!Number.isFinite(a) && !Number.isFinite(b)) return;
    if (Number.isFinite(a) && a > 0) {
      model.constraints.push({ type: "radius", c: `${ell[0]}.a`, r: a });
    }
    if (Number.isFinite(b) && b > 0) {
      model.constraints.push({ type: "radius", c: `${ell[0]}.b`, r: b });
    }
  } else if (name === "tangent" && lns.length + anyCrv.length === 2
             && anyCrv.length && ell.length < 2) {
    // One front door over the solver's dispatch table: it works out which of
    // the two is the line. Two ellipses are refused — that tangency needs an
    // auxiliary anomaly on each curve and was not measured (slice 11).
    model.constraints.push({ type: "tangent", a: lns[0] || anyCrv[0],
                             b: anyCrv[anyCrv.length - 1] });
  } else if (name === "symmetric" && pts.length === 2 && lns.length === 1) {
    model.constraints.push({ type: "symmetric", a: pts[0], b: pts[1],
                             about: lns[0] });
  } else if (name === "equal" && lns.length === 2) {
    model.constraints.push({ type: "equal_length", l1: lns[0], l2: lns[1] });
  } else if (name === "equal" && crv.length === 2) {
    model.constraints.push({ type: "equal_radius", c1: crv[0], c2: crv[1] });
  } else if (name === "concentric" && anyCrv.length === 2) {
    model.constraints.push({ type: "concentric", a: anyCrv[0], b: anyCrv[1] });
  } else {
    return; // enablement should prevent this; ignore quietly
  }
  mutated();
}

function updateConstraintButtons() {
  const nPts = selectedOf("point").length;
  const nHnd = selectedOf("handle").length;
  const nLns = selectedOf("line").length;
  const nCrv = selectedOf("circle", "arc").length;
  const nEll = selectedOf("ellipse").length;
  const nAll = nPts + nHnd + nLns + nCrv + nEll;
  const only = (p, l, c, e = 0) => nPts + nHnd === p && nLns === l
    && nCrv === c && nEll === e && selection.length === nAll;
  const enable = {
    fixed: only(1, 0, 0) && nPts === 1,
    coincident: only(2, 0, 0),
    distance: only(2, 0, 0),
    horizontal: only(0, 1, 0),
    vertical: only(0, 1, 0),
    parallel: only(0, 2, 0),
    perpendicular: only(0, 2, 0),
    // one circle/arc (one radius), or one ellipse (two semi-axes)
    radius: only(0, 0, 1) || only(0, 0, 0, 1),
    // line+curve or curve+curve; two lines are never tangent, and two
    // ellipses are out of scope (slice 11's spike measured neither)
    tangent: only(0, 1, 1) || only(0, 0, 2) || only(0, 1, 0, 1)
      || only(0, 0, 1, 1),
    symmetric: only(2, 1, 0),
    // `equal` stays circles/arcs: "equal" between an ellipse and a circle
    // would have to guess which semi-axis was meant
    equal: only(0, 2, 0) || only(0, 0, 2),
    concentric: only(0, 0, 2) || only(0, 0, 1, 1) || only(0, 0, 0, 2),
  };
  for (const [name, b] of Object.entries(conBtns)) {
    // A diverged block opens read-only: nothing may edit the sketch until the
    // user has chosen between the spec and their hand edit.
    b.disabled = readOnly || !enable[name];
  }
}

function constraintLabel(con) {
  switch (con.type) {
    case "coincident": return `coin ${con.p}=${con.q}`;
    case "distance": return `dist ${con.p}–${con.q} = ${fmtVal(con.d)}`;
    case "horizontal": return `H ${con.ln}`;
    case "vertical": return `V ${con.ln}`;
    case "parallel": return `par ${con.l1},${con.l2}`;
    case "perpendicular": return `perp ${con.l1},${con.l2}`;
    case "radius": return `rad ${con.c} = ${fmtVal(con.r)}`;
    case "tangent": return `tan ${con.a},${con.b}`;
    case "symmetric": return `sym ${con.a},${con.b} ⟂ ${con.about}`;
    case "equal_length": return `eq len ${con.l1},${con.l2}`;
    case "equal_radius": return `eq rad ${con.c1},${con.c2}`;
    case "concentric": return `conc ${con.a},${con.b}`;
    default: return con.type;
  }
}

function renderChips() {
  chipsEl.textContent = "";
  model.constraints.forEach((con, i) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "sk-chip";
    if (highlight.constraints.has(i)) chip.classList.add("flagged");
    // Read-only (a diverged block) is a latch on **every** surface that could
    // edit, and this one spliced `model.constraints` and re-solved. Every
    // other mutating entry point checks it; so does this.
    chip.disabled = readOnly;
    chip.title = readOnly
      ? "This sketch is open read-only: its code was hand-edited, so pick "
        + "'Re-solve from the spec' or 'Discard the spec' first"
      : "Click to remove this constraint";
    chip.textContent = `${constraintLabel(con)} ×`;
    chip.addEventListener("click", () => {
      if (readOnly) return;
      model.constraints.splice(i, 1);
      clearHighlight();
      mutated();
    });
    chipsEl.appendChild(chip);
  });
}

// ---------------------------------------------------------------- pointer

function toSketch(e) {
  const rect = svg.getBoundingClientRect();
  return {
    x: (e.clientX - rect.left - rect.width / 2) / scale,
    y: -(e.clientY - rect.top - rect.height / 2) / scale,
  };
}

function onPointerDown(e) {
  if (e.button !== 0) return;
  // Read-only (a diverged block): look and select, never draw.
  if (readOnly && tool !== "select") return;
  const { x, y } = toSketch(e);
  if (tool === "point") {
    if (!nearRef(x, y)) {
      addPoint(x, y);
      mutated();
    }
    return;
  }
  if (tool === "line") return lineClick(x, y);
  if (tool === "arc") return arcCenterClick(x, y);
  if (tool === "arc3") return arcThreePointClick(x, y);
  if (tool === "arcTan") return arcTangentClick(x, y);
  if (tool === "ellipse") return ellipseClick(x, y);
  if (tool === "spline") return splineClick(x, y);
  if (tool === "slot") return slotClick(x, y);
  if (tool === "circle") {
    let center = nearRef(x, y);
    const created = !center || center.includes(".");
    if (created) center = addPoint(x, y);
    pending = { kind: "circle", center, created, r: 0 };
    svg.setPointerCapture(e.pointerId);
    renderPreview();
    return;
  }
  // select tool: empty-canvas click clears (entity handlers stopPropagation)
  if (!e.shiftKey) {
    selection = [];
    clearHighlight();
    render();
    renderStatus();
  }
}

function lineClick(x, y) {
  let ref = nearRef(x, y);
  if (ref && ref === chainPrev) {
    chainPrev = null; // clicking the previous point ends the chain
    renderPreview();
    return;
  }
  if (!ref) ref = addPoint(x, y);
  if (chainPrev && chainPrev !== ref) addLine(chainPrev, ref);
  chainPrev = ref;
  mutated();
}

// ---- arcs ----------------------------------------------------------------

function angleDeg(cx, cy, x, y) {
  return (Math.atan2(y - cy, x - cx) * 180) / Math.PI;
}

/** Unwrap `end` so the sweep from `start` runs the way the cursor went. */
function sweepTo(startDeg, endDeg, ccw) {
  let d = endDeg - startDeg;
  while (d <= 0) d += 360;
  while (d > 360) d -= 360;
  return startDeg + (ccw ? d : d - 360);
}

function arcCenterClick(x, y) {
  if (!pending || pending.kind !== "arcC") {
    let center = nearRef(x, y);
    if (!center || center.includes(".")) center = addPoint(x, y);
    pending = { kind: "arcC", center };
    renderPreview();
    return;
  }
  const c = refCoords(pending.center);
  if (pending.start_deg === undefined) {
    pending.r = Math.max(0.25, Math.hypot(x - c.x, y - c.y));
    pending.start_deg = angleDeg(c.x, c.y, x, y);
    renderPreview();
    return;
  }
  const end = sweepTo(pending.start_deg, angleDeg(c.x, c.y, x, y),
                      pending.ccw !== false);
  addArc(pending.center, pending.r, pending.start_deg, end);
  pending = null;
  chainPrev = null;
  mutated();
}

/** Centre, then the end of the major axis (which fixes `a` and the rotation),
 *  then any point whose distance across that axis fixes `b`. Three clicks, the
 *  same shape as the `Arc` tool. */
function ellipseClick(x, y) {
  if (!pending || pending.kind !== "ellipse") {
    let center = nearRef(x, y);
    if (!center || center.includes(".")) center = addPoint(x, y);
    pending = { kind: "ellipse", center };
    renderPreview();
    return;
  }
  const c = refCoords(pending.center);
  if (pending.a === undefined) {
    pending.a = Math.max(0.25, Math.hypot(x - c.x, y - c.y));
    pending.rotation = angleDeg(c.x, c.y, x, y);
    renderPreview();
    return;
  }
  const b = minorFrom(pending, c, x, y);
  addEllipse(pending.center, pending.a, b, pending.rotation);
  pending = null;
  chainPrev = null;
  mutated();
}

/** The semi-minor axis a cursor at (x, y) implies: its distance from the
 *  major axis, clamped so a degenerate ellipse can never be authored. */
function minorFrom(p, c, x, y) {
  const phi = (p.rotation * Math.PI) / 180;
  const dx = x - c.x;
  const dy = y - c.y;
  return Math.max(0.25, Math.abs(-dx * Math.sin(phi) + dy * Math.cos(phi)));
}

function arcThreePointClick(x, y) {
  if (!pending || pending.kind !== "arc3") {
    const ref = nearRef(x, y) || null;
    const at = ref ? refCoords(ref) : { x, y };
    pending = { kind: "arc3", startRef: ref, start: at };
    renderPreview();
    return;
  }
  if (!pending.end) {
    const ref = nearRef(x, y) || null;
    pending.endRef = ref;
    pending.end = ref ? refCoords(ref) : { x, y };
    renderPreview();
    return;
  }
  const arc = circumArc(pending.start, { x, y }, pending.end);
  const { startRef, endRef } = pending;
  pending = null;
  if (!arc) {
    actions.toast("those three points are collinear — no arc through them",
                  "error");
    renderPreview();
    return;
  }
  const center = addPoint(arc.cx, arc.cy);
  const name = addArc(center, arc.r, arc.start_deg, arc.end_deg);
  // Pin the ends onto whatever the user clicked: the arc owns its endpoints,
  // so joining a chain means constraining its handle, not sharing a point.
  if (startRef) {
    model.constraints.push({ type: "coincident", p: `${name}.start`, q: startRef });
  }
  if (endRef) {
    model.constraints.push({ type: "coincident", p: `${name}.end`, q: endRef });
  }
  chainPrev = `${name}.end`;
  mutated();
}

/** Centre, radius and unwrapped sweep of the arc start->through->end. */
function circumArc(a, m, b) {
  const d = 2 * (a.x * (m.y - b.y) + m.x * (b.y - a.y) + b.x * (a.y - m.y));
  if (Math.abs(d) < 1e-9) return null;
  const sa = a.x * a.x + a.y * a.y;
  const sm = m.x * m.x + m.y * m.y;
  const sb = b.x * b.x + b.y * b.y;
  const cx = (sa * (m.y - b.y) + sm * (b.y - a.y) + sb * (a.y - m.y)) / d;
  const cy = (sa * (b.x - m.x) + sm * (a.x - b.x) + sb * (m.x - a.x)) / d;
  const r = Math.hypot(a.x - cx, a.y - cy);
  const t0 = angleDeg(cx, cy, a.x, a.y);
  const tm = angleDeg(cx, cy, m.x, m.y);
  const t1 = angleDeg(cx, cy, b.x, b.y);
  // the sweep must pass through the middle point, so pick the direction that
  // contains it — this is what stops a 3-point arc taking the long way round
  const ccwMid = norm360(tm - t0);
  const ccwEnd = norm360(t1 - t0);
  const ccw = ccwMid < ccwEnd;
  return { cx, cy, r, start_deg: t0, end_deg: sweepTo(t0, t1, ccw) };
}

function norm360(d) {
  let x = d % 360;
  if (x < 0) x += 360;
  return x;
}

function arcTangentClick(x, y) {
  if (!chainPrev) {
    actions.toast("draw a line or an arc first — a tangent arc continues a chain",
                  "error");
    return;
  }
  const start = refCoords(chainPrev);
  const dir = chainDirection(chainPrev);
  if (!start || !dir) {
    actions.toast("no tangent direction at that point", "error");
    return;
  }
  // centre = start + n·d, with n ⟂ the incoming tangent and
  // d = |sq|² / (2·(sq·n)) — the circle through `start` and the cursor that
  // leaves `start` along `dir`.
  const n = { x: -dir.y, y: dir.x };
  const q = { x: x - start.x, y: y - start.y };
  const denom = 2 * (q.x * n.x + q.y * n.y);
  if (Math.abs(denom) < 1e-9) {
    actions.toast("that point is straight ahead — a tangent arc would be a line",
                  "error");
    return;
  }
  const d = (q.x * q.x + q.y * q.y) / denom;
  const cx = start.x + n.x * d;
  const cy = start.y + n.y * d;
  const r = Math.abs(d);
  const t0 = angleDeg(cx, cy, start.x, start.y);
  const t1 = angleDeg(cx, cy, x, y);
  // d > 0 puts the centre on the +n side, which is a CCW sweep
  const center = addPoint(cx, cy);
  const name = addArc(center, r, t0, sweepTo(t0, t1, d > 0));
  model.constraints.push({ type: "coincident", p: `${name}.start`, q: chainPrev });
  const owner = chainOwner(chainPrev);
  if (owner) model.constraints.push({ type: "tangent", a: owner, b: name });
  chainPrev = `${name}.end`;
  mutated();
}

/** The unit direction the chain arrives at `ref` travelling forward. */
function chainDirection(ref) {
  const at = refCoords(ref);
  if (!at) return null;
  const arcEnd = ref.includes(".") ? arcOf(baseName(ref)) : null;
  if (arcEnd) {
    const which = ref.slice(ref.indexOf(".") + 1);
    const t = ((which === "start" ? arcEnd.start_deg : arcEnd.end_deg)
               * Math.PI) / 180;
    const ccw = arcEnd.end_deg >= arcEnd.start_deg ? 1 : -1;
    return { x: -Math.sin(t) * ccw, y: Math.cos(t) * ccw };
  }
  // the most recently declared line that ends at this point
  for (let i = model.lines.length - 1; i >= 0; i--) {
    const l = model.lines[i];
    const other = l.p2 === ref ? l.p1 : l.p1 === ref ? l.p2 : null;
    if (!other) continue;
    const o = refCoords(other);
    if (!o) continue;
    const dx = at.x - o.x;
    const dy = at.y - o.y;
    const n = Math.hypot(dx, dy) || 1;
    return { x: dx / n, y: dy / n };
  }
  return null;
}

/** The curve a chain ref belongs to, for the tangency constraint. */
function chainOwner(ref) {
  if (ref.includes(".")) return baseName(ref);
  for (let i = model.lines.length - 1; i >= 0; i--) {
    const l = model.lines[i];
    if (l.p1 === ref || l.p2 === ref) return l.name;
  }
  return null;
}

// ---- splines and slots ---------------------------------------------------

function splineClick(x, y) {
  let ref = nearRef(x, y);
  if (ref && ref.includes(".")) ref = joinRef(ref, x, y);
  if (!ref) ref = addPoint(x, y);
  if (!pending || pending.kind !== "spline") {
    pending = { kind: "spline", points: [ref] };
    render();
    return;
  }
  if (pending.points[pending.points.length - 1] === ref) {
    finishSpline();
    return;
  }
  pending.points.push(ref);
  if (pending.points.length === 2) {
    pending.name = `sp${++seq.sp}`;
    model.splines.push({ name: pending.name, points: [...pending.points] });
  } else {
    const sp = model.splines.find((s) => s.name === pending.name);
    sp.points = [...pending.points];
  }
  mutated();
}

function finishSpline() {
  if (pending && pending.kind === "spline" && pending.points.length < 2) {
    // one lonely click: no spline, but keep the point the user placed
    pending = null;
    render();
    return;
  }
  pending = null;
  renderPreview();
}

function slotClick(x, y) {
  let ref = nearRef(x, y);
  if (!ref || ref.includes(".")) ref = addPoint(x, y);
  if (!pending || pending.kind !== "slot") {
    pending = { kind: "slot", c1: ref };
    renderPreview();
    return;
  }
  if (ref === pending.c1) return;
  const c1 = refCoords(pending.c1);
  const c2 = refCoords(ref);
  const len = Math.hypot(c2.x - c1.x, c2.y - c1.y);
  const width = parseFloat(prompt("Slot width (mm):",
                                  fmtVal(Math.max(2, len * 0.4))));
  const c1Ref = pending.c1;
  pending = null;
  if (!Number.isFinite(width) || width <= 0) {
    render();
    return;
  }
  model.slots.push({ name: `sl${++seq.sl}`, c1: c1Ref, c2: ref, width });
  mutated();
}

// ---- drag ----------------------------------------------------------------

function startDrag(ref, e) {
  if (readOnly) return;
  // Snapshot before the first frame: on any solver error the drag ends and
  // the sketch reverts here. A drag that leaves a divergent frame on screen
  // is how a sketch gets corrupted.
  drag = {
    ref,
    cursor: toSketch(e),
    snapshot: JSON.parse(JSON.stringify(model)),
    inFlight: false,
    rafId: null,
    frames: 0,
  };
  svg.setPointerCapture(e.pointerId);
  renderPreview();
}

function scheduleDragFrame() {
  if (!drag || drag.rafId) return;
  drag.rafId = requestAnimationFrame(() => {
    if (!drag) return;
    drag.rafId = null;
    // The predicted handle is painted on THIS frame, before any round trip
    // (measured: the round trip cannot make a display frame — slice 10's
    // browser spike). The solved geometry follows when the response lands.
    renderPreview();
    if (!drag.inFlight) sendDragFrame();
  });
}

function sendDragFrame() {
  const my = ++solveSeq;
  const { ref, cursor: c } = drag;
  drag.inFlight = true;
  drag.frames++;
  // `initial` is the PREVIOUS FRAME's solution, never the cursor: seeding the
  // dragged point at the cursor is what flips the mirror branch when the
  // cursor crosses it. `diagnostics` is left at its default so the drag frame
  // serves the cached block instead of paying for the greedy pass.
  api.solveSketch(entitiesSpec(), model.constraints,
                  solveOpts({ initial: seedFromModel(),
                              drag: { point: ref, x: c.x, y: c.y } }))
    .then((res) => {
      if (my !== solveSeq || !drag) return;
      const error = errorOf(res, null);
      if (error) {
        endDrag(error);
        return;
      }
      applySolution(res);
      solveState = fromResult(res);
      render();
      renderStatus();
    })
    .catch((err) => {
      if (my !== solveSeq) return;
      endDrag(errorOf(null, err));
    })
    .finally(() => {
      if (drag) drag.inFlight = false;
    });
}

function endDrag(err) {
  if (!drag) return;
  if (drag.rafId) cancelAnimationFrame(drag.rafId);
  const moved = drag.frames > 0;
  const snapshot = drag.snapshot;
  drag = null;
  if (err) {
    model = snapshot;
    solveSeq++; // discard anything still in flight
    render();
    solveAndRender();
    actions.toast(`drag reverted — ${err.message}`, "error");
    return;
  }
  render();
  // One final non-drag solve with full diagnostics, so the chip and any
  // conflicts describe the settled geometry rather than a cached block.
  if (moved) solveAndRender("full");
  else renderStatus();
}

function onPointerMove(e) {
  cursor = toSketch(e);
  if (drag) {
    drag.cursor = cursor;
    scheduleDragFrame();
    return;
  }
  if (press && !press.started) {
    if (e.buttons === 0) {
      // The button came up somewhere we never heard about. A press that
      // outlived its pointer is not a drag.
      press = null;
      return;
    }
    const dx = (e.clientX - press.clientX);
    const dy = (e.clientY - press.clientY);
    if (Math.hypot(dx, dy) > DRAG_PX) {
      press.started = true;
      startDrag(press.ref, e);
      return;
    }
  }
  if (pending && pending.kind === "circle") {
    const c = refCoords(pending.center);
    pending.r = Math.hypot(cursor.x - c.x, cursor.y - c.y);
  }
  if (pending || (chainPrev && (tool === "line" || tool === "arcTan"))) {
    renderPreview();
  }
}

function onPointerUp(e) {
  if (drag) {
    endDrag(null);
    press = null;
    return;
  }
  if (press) {
    const { kind, ref, shift } = press;
    press = null;
    toggleSelect(kind, ref, shift);
    return;
  }
  if (!pending || pending.kind !== "circle") return;
  const { center, created, r } = pending;
  pending = null;
  if (r < 0.5) {
    // a click without a drag: no circle; drop a center point we just made
    if (created) {
      model.points = model.points.filter((p) => p.name !== center);
      render();
    }
    renderPreview();
    return;
  }
  model.circles.push({ name: `c${++seq.c}`, center, r });
  mutated();
}

function onWheel(e) {
  if (!open) return;
  e.preventDefault();
  scale = Math.min(40, Math.max(0.5, scale * Math.exp(-e.deltaY * 0.0015)));
  render();
}

function onKey(e) {
  if (!open) return;
  const target = e.target instanceof Element ? e.target : document.body;
  if (target.closest("input, textarea, .CodeMirror")) return;
  if (e.key === "Escape") {
    if (drag) {
      endDrag(null);
    } else if (pending && pending.kind === "spline") {
      finishSpline();
    } else if (chainPrev || pending) {
      chainPrev = null;
      pending = null;
      render();
    } else if (selection.length || highlight.entities.size) {
      selection = [];
      clearHighlight();
      render();
      renderStatus();
    } else {
      close();
    }
    e.stopPropagation();
    return;
  }
  if (e.key === "Delete" || e.key === "Backspace") {
    e.preventDefault();
    deleteSelection();
  }
}

// ------------------------------------------------- round-trip blocks (FR10)

/** Name prefixes the GUI allocates, so a spec read back out of a script keeps
 *  numbering where it left off instead of colliding with itself. Projected
 *  references (`ref0`, `ref0_a`) match none of them, deliberately. */
const NAME_SEQ = [
  ["p", "points", /^p(\d+)$/], ["l", "lines", /^ln(\d+)$/],
  ["c", "circles", /^c(\d+)$/], ["a", "arcs", /^a(\d+)$/],
  ["e", "ellipses", /^e(\d+)$/], ["sp", "splines", /^sp(\d+)$/],
  ["sl", "slots", /^sl(\d+)$/],
];

function countersFor(m) {
  const out = { p: 0, l: 0, c: 0, a: 0, e: 0, sp: 0, sl: 0 };
  for (const [key, kind, re] of NAME_SEQ) {
    for (const entity of m[kind] || []) {
      const hit = re.exec(entity.name);
      if (hit) out[key] = Math.max(out[key], Number(hit[1]));
    }
  }
  return out;
}

function hasEntities() {
  return [model.points, model.lines, model.circles, model.arcs,
          model.ellipses, model.splines, model.slots].some((a) => a.length);
}

/** A persisted spec back into the on-screen model — the inverse of
 *  `entitiesSpec()`. `construction` and `plane` ride through untouched: a
 *  reference that re-parsed as real geometry would emit the part's own
 *  boundary back into it, and a sketch-on-face without its basis is a set of
 *  coordinates in a plane nobody recorded. */
function specToModel(spec) {
  const ents = (spec && spec.entities) || {};
  model.points = (ents.points || []).map((p) => ({ ...p, fixed: !!p.fixed }));
  model.lines = (ents.lines || []).map((l) => ({ ...l }));
  model.circles = (ents.circles || []).map((c) => ({ ...c }));
  model.arcs = (ents.arcs || []).map((a) => ({ ...a }));
  model.ellipses = (ents.ellipses || []).map((e) => ({
    ...e, rotation: e.rotation || 0,
    bounded: e.start_deg !== undefined && e.start_deg !== null,
  }));
  model.splines = (ents.splines || []).map(
    (s) => ({ name: s.name, points: [...s.points] }));
  model.slots = (ents.slots || []).map((s) => ({ ...s }));
  model.constraints = ((spec && spec.constraints) || []).map((c) => ({ ...c }));
  plane = (spec && spec.plane) || null;
  seq = countersFor(model);
}

/** Look for round-trip blocks in the open part's script and offer them.
 *
 *  Never clobbers: a canvas with work on it is left alone. */
async function refreshBlocks() {
  if (hasEntities()) return;
  let script = "";
  try {
    script = editor.getScript() || "";
  } catch (err) {
    script = "";
  }
  if (!script.includes("agentcad-sketch-spec")
      && !script.includes("agentcad sketch \"")) {
    hideBanner();
    return;
  }
  const my = ++blocksSeq;
  let res = null;
  try {
    res = await api.sketchBlocks(script);
  } catch (err) {
    return;                       // an unreachable server is not a divergence
  }
  // The entry guard, re-checked on arrival: a lookup that started against an
  // empty canvas must not `resetModel()` over geometry the user drew while it
  // was in flight — there is no undo for that. A newer generation (a part
  // switch, a second open) supersedes this answer entirely.
  if (my !== blocksSeq || hasEntities()) return;
  const blocks = (res && res.blocks) || [];
  if (!blocks.length) {
    hideBanner();
    return;
  }
  if (blocks.length === 1) {
    openBlock(blocks[0]);
    return;
  }
  showPicker(blocks);
}

/** Open one block. A diverged one opens **read-only**: the code is the source
 *  of truth for geometry, and the user picks explicitly between the spec and
 *  their hand edit. Nothing is overwritten either way.
 *
 *  A block whose spec will not parse loads nothing, so there is nothing to
 *  protect and the latch stays off — locking the canvas over whatever the user
 *  had drawn would punish them for someone else's corrupt comment. */
function openBlock(block) {
  if (block.spec) {
    resetModel();
    specToModel(block.spec);
    blockName = block.name;
  }
  readOnly = !!block.spec && block.status !== "ok";
  applyReadOnly();
  showBanner(block);
  if (block.spec) {
    render();
    solveAndRender("full");
    checkReopenedFace();
  } else {
    render();
    renderStatus();
  }
}

/** Is the face this sketch was drawn on still at the ordinal it recorded?
 *
 *  Face indices are mesh-order ordinals and a topology-changing parameter edit
 *  renumbers them, so a reopened sketch-on-face can re-solve on the *old*
 *  basis, emit "on face 37" naming a different face, and still report `ok`
 *  because its hash matches — the block is intact, the face under it is not.
 *  The server compares the recorded identity with the face that is there now
 *  and reports `ok` / `moved` / `unchecked`; a mismatch is **surfaced, never
 *  repaired**, because which face the user meant is not something this can
 *  guess. A sketch saved before the identity was recorded comes back
 *  `unchecked`, which is honest and silent. */
async function checkReopenedFace() {
  const p = plane;
  const project = state.projectName;
  const part = state.selectedPart;
  if (!p || !p.face_id || p.face_index === undefined || p.face_index === null) {
    return;
  }
  if (!project || !part) return;
  const my = ++faceSeq;
  let res = null;
  try {
    res = await api.callTool("sketch_plane", {
      project, part_id: part, face_index: p.face_index, expect: p.face_id,
    });
  } catch (err) {
    return;              // an unreachable server is not a moved face
  }
  // `plane !== p` means a different sketch is on screen now.
  if (my !== faceSeq || plane !== p || !res || res.error) return;
  const check = res.face_check;
  if (!check || check.status !== "moved") return;
  actions.toast(check.message, "error");
}

const BANNER_TEXT = {
  ok: (b) => `editing sketch “${b.name}” from the script — its spec and the `
    + "code are in sync",
  diverged: (b) => `sketch “${b.name}”: the emitted code was edited by hand, `
    + "so it no longer matches the saved spec. The code is the source of "
    + "truth for geometry — nothing here has been overwritten.",
  unverified: (b) => `sketch “${b.name}”: ${b.message}`,
};

function showBanner(block) {
  bannerEl.textContent = "";
  bannerEl.className = `sk-banner ${block.status === "ok" ? "ok"
    : block.status === "diverged" ? "err" : "warn"}`;
  const msg = document.createElement("span");
  msg.className = "sk-banner-msg";
  msg.textContent = (BANNER_TEXT[block.status] || BANNER_TEXT.unverified)(block);
  bannerEl.appendChild(msg);
  if (block.status === "ok") {
    bannerEl.appendChild(bannerButton("✕", "Dismiss", hideBanner));
    return;
  }
  if (block.spec) {
    // The design's two explicit choices. Neither writes to the script: "Insert
    // → script" still does that, and it appends a *new* block rather than
    // rewriting the one the user edited.
    bannerEl.appendChild(bannerButton(
      "Re-solve from the spec",
      "Edit the saved constraint spec. Inserting writes a new block; the "
      + "hand-edited one stays in the script until you remove it.",
      () => {
        readOnly = false;
        applyReadOnly();
        hideBanner();
        actions.toast(`editing the saved spec of “${block.name}” — Insert `
                      + "writes a new block, so your hand edit is still there",
                      "info");
      }));
    bannerEl.appendChild(bannerButton(
      "Discard the spec",
      "Keep the hand-edited code exactly as it is and drop its constraints.",
      () => {
        resetModel();
        hideBanner();
        render();
        renderStatus();
        actions.toast(`kept the hand-edited code of “${block.name}”; its `
                      + "constraints are gone", "info");
      }));
    return;
  }
  bannerEl.appendChild(bannerButton("Dismiss", "Leave the code alone", () => {
    readOnly = false;                 // nothing was loaded; nothing to latch
    applyReadOnly();
    hideBanner();
  }));
}

function showPicker(blocks) {
  bannerEl.textContent = "";
  bannerEl.className = "sk-banner";
  const msg = document.createElement("span");
  msg.className = "sk-banner-msg";
  msg.textContent = `${blocks.length} saved sketches in this script:`;
  bannerEl.appendChild(msg);
  for (const block of blocks) {
    const label = block.status === "ok" ? block.name
      : `${block.name} (${block.status})`;
    bannerEl.appendChild(bannerButton(
      label, block.message || `Open sketch “${block.name}”`,
      () => openBlock(block)));
  }
  bannerEl.appendChild(bannerButton("✕", "Dismiss", hideBanner));
}

function bannerButton(label, title, onClick) {
  const b = skButton(label, title, onClick);
  b.classList.add("sk-banner-btn");
  return b;
}

function hideBanner() {
  bannerEl.className = "sk-banner hidden";
  bannerEl.textContent = "";
}

/** Read-only is a latch on every surface that could edit or emit. */
function applyReadOnly() {
  host.classList.toggle("sk-locked", readOnly);
  if (readOnly) setTool("select");
  for (const [name, b] of Object.entries(toolBtns)) {
    b.disabled = readOnly && name !== "select";
  }
  renderStatus();          // `updateConstraintButtons` honours `readOnly`
}

// ------------------------------------------------------------------ solve

function entitiesSpec() {
  return {
    points: model.points.map((p) => ({
      name: p.name, x: p.x, y: p.y, fixed: !!p.fixed,
    })),
    lines: model.lines.map((l) => ({
      name: l.name, p1: l.p1, p2: l.p2,
      ...(l.construction ? { construction: true } : {}),
    })),
    circles: model.circles.map((c) => ({
      name: c.name, center: c.center, r: c.r,
      ...(c.fixed_r ? { fixed_r: true } : {}),
      ...(c.construction ? { construction: true } : {}),
    })),
    arcs: model.arcs.map((a) => ({
      name: a.name, center: a.center, r: a.r,
      start_deg: a.start_deg, end_deg: a.end_deg,
      ...(a.fixed ? { fixed: true } : {}),
      ...(a.construction ? { construction: true } : {}),
    })),
    ellipses: model.ellipses.map((e) => ({
      name: e.name, center: e.center, a: e.a, b: e.b, rotation: e.rotation,
      ...(e.bounded ? { start_deg: e.start_deg, end_deg: e.end_deg } : {}),
      ...(e.construction ? { construction: true } : {}),
    })),
    splines: model.splines.map((s) => ({ name: s.name, points: [...s.points] })),
    slots: model.slots.map((s) => ({
      name: s.name, c1: s.c1, c2: s.c2, width: s.width,
    })),
  };
}

/** The previous frame's solution, in `initial`'s shape. A slot is seeded by
 *  its radius alone — its caps are re-derived from the seeded centres — and
 *  the compiled sub-entities are deliberately absent. */
function seedFromModel() {
  const seed = {
    points: Object.fromEntries(model.points.map((p) => [p.name, { x: p.x, y: p.y }])),
  };
  if (model.circles.length) {
    seed.circles = Object.fromEntries(model.circles.map((c) => [c.name, { r: c.r }]));
  }
  if (model.arcs.length) {
    seed.arcs = Object.fromEntries(model.arcs.map((a) => [a.name, {
      r: a.r, start_deg: a.start_deg, end_deg: a.end_deg,
    }]));
  }
  if (model.ellipses.length) {
    // Same all-or-nothing rule as a slot: an ellipse owns a, b and rotation
    // (plus its bounds), and omitting any of them is not a smaller seed but
    // no seed at all — `initial_incomplete`, a cold start, and the branch
    // stability the drag path depends on gone with it.
    seed.ellipses = Object.fromEntries(model.ellipses.map((e) => [e.name, {
      a: e.a, b: e.b, rotation: e.rotation,
      ...(e.bounded ? { start_deg: e.start_deg, end_deg: e.end_deg } : {}),
    }]));
  }
  if (model.slots.length) {
    // A slot owns a radius parameter of its own, so leaving it out is not a
    // smaller seed — it is **no seed at all**: the solver reports
    // `initial_incomplete` and degrades the whole frame to a cold start,
    // which is exactly how a mirror flip gets back in through the side door.
    seed.slots = Object.fromEntries(
      model.slots.map((s) => [s.name, { r: s.width / 2 }]));
  }
  return seed;
}

function applySolution(res) {
  for (const p of model.points) {
    const s = res.points && res.points[p.name];
    if (s) {
      p.x = s.x;
      p.y = s.y;
    }
  }
  for (const c of model.circles) {
    const s = res.circles && res.circles[c.name];
    if (s) c.r = s.r;
  }
  for (const a of model.arcs) {
    const s = res.arcs && res.arcs[a.name];
    if (s) {
      a.r = s.r;
      a.start_deg = s.start_deg;
      a.end_deg = s.end_deg;
    }
  }
  for (const e of model.ellipses) {
    const s = res.ellipses && res.ellipses[e.name];
    if (s) {
      e.a = s.a;
      e.b = s.b;
      e.rotation = s.rotation;
      if (e.bounded) {
        e.start_deg = s.start_deg;
        e.end_deg = s.end_deg;
      }
    }
  }
}

/** A solve fails in two shapes and both must be handled: the route answers
 *  **200 with an `{error: {type, message, details}}` envelope** for a
 *  tool-level failure (an unsatisfiable constraint set, a refused emission),
 *  and `request()` throws an `ApiError` carrying the same `error` object for
 *  a transport or HTTP failure. Handling only the second is how a conflicting
 *  sketch silently keeps reading "fully constrained". */
function errorOf(res, err) {
  if (err) return err.error || { message: err.message, details: {} };
  return res && res.error ? res.error : null;
}

function failureState(error) {
  const d = error.details || {};
  const diag = d.diagnostics || {};
  return {
    ok: false,
    msg: error.message,
    maxResidual: d.max_residual,
    dof: d.dof,
    status: diag.status,
    free: diag.free_entities || [],
    redundant: diag.redundant || [],
    conflicting: diag.conflicting || [],
    complete: diag.analysis_complete !== false,
  };
}

function fromResult(res) {
  const d = res.diagnostics || {};
  return {
    ok: true,
    dof: res.dof,
    status: d.status,
    free: d.free_entities || [],
    redundant: d.redundant || [],
    conflicting: d.conflicting || [],
    complete: d.analysis_complete !== false,
    stale: res.diagnostics_source === "cached",
  };
}

function hasResiduals() {
  return model.constraints.length > 0 || model.slots.length > 0;
}

/** The DOF of a sketch with no residuals at all, counted the way the solver
 *  allocates parameters: 2 per free point, 1 per free radius, 3 per arc
 *  (r, start, end) — 0 for a wholly fixed reference arc — 3 per ellipse plus 2
 *  when it is bounded, and 1 for a slot's shared radius. */
function freeParamCount() {
  return (
    model.points.filter((p) => !p.fixed).length * 2
    + model.circles.filter((c) => !c.fixed_r).length
    + model.arcs.filter((a) => !a.fixed).length * 3
    + model.ellipses.reduce((n, e) => n + (e.bounded ? 5 : 3), 0)
    + model.slots.length
  );
}

/** The optional half of every solve request. The plane rides along on all of
 *  them: `insertSnippet` is the one that needs it (emission writes the basis
 *  into the script), and passing it everywhere keeps the three call sites from
 *  diverging. */
function solveOpts(extra) {
  const opts = { ...(extra || {}) };
  if (plane) opts.plane = plane;
  return Object.keys(opts).length ? opts : undefined;
}

async function solveAndRender(diagnostics) {
  const free = freeParamCount();
  if (!hasResiduals() || free === 0) {
    // Nothing to solve: no residuals (or no free geometry). The sketch is
    // trivially consistent; dof is just the free parameter count.
    solveState = { ok: true, dof: free, local: true, free: [],
                   redundant: [], conflicting: [], complete: true };
    renderStatus();
    return;
  }
  const my = ++solveSeq;
  let res = null;
  let thrown = null;
  try {
    res = await api.solveSketch(entitiesSpec(), model.constraints,
                                solveOpts(diagnostics ? { diagnostics } : null));
  } catch (err) {
    thrown = err;
  }
  if (my !== solveSeq) return;
  const error = errorOf(res, thrown);
  if (error) {
    solveState = failureState(error);
    render();
    renderStatus();
    return;
  }
  applySolution(res);
  solveState = fromResult(res);
  render();
  renderStatus();
}

// -------------------------------------------------------------- DOF chip

const DEPENDENT_NOTE =
  "The reported set is *a* dependent set, not necessarily the unique culprit: "
  + "removing any one member resolves the dependency. The member named is "
  + "chosen by declaration order — the later constraint is blamed.";

function chipState() {
  const s = solveState;
  const conflicting = s.conflicting || [];
  const redundant = s.redundant || [];
  if (!s.ok && !conflicting.length) {
    const extra = s.maxResidual != null
      ? ` (max residual ${Number(s.maxResidual).toExponential(1)})` : "";
    return { cls: "err", text: "unsolved", title: s.msg + extra };
  }
  if (conflicting.length) {
    return {
      cls: "err",
      text: `conflicting (${conflicting.length})`,
      title: `${conflicting.length} constraint(s) cannot be satisfied together. `
        + `${DEPENDENT_NOTE} Click to highlight the set.`,
    };
  }
  // **Branch on `status`, not on `redundant.length`.** The two agree by
  // construction on the server (the rank *is* the dependent-row count), and
  // reading only the blame set is how a sketch that reported
  // `over_constrained` with an empty set rendered as "7 DOF, still free:
  // b, c, d" over a pinned rectangle. If they ever disagree again, say so
  // rather than reporting the freedom the DOF number claims.
  if (s.status === "over_constrained" || redundant.length) {
    return {
      cls: "warn",
      text: redundant.length ? `over-constrained (${redundant.length})`
                             : "over-constrained",
      title: redundant.length
        ? `${redundant.length} constraint(s) add nothing — the sketch still `
          + `solves. ${DEPENDENT_NOTE} Click to highlight the set.`
        : "the sketch has more constraints than independent ones, and the "
          + "analysis named none of them — so the DOF count below is not "
          + "trustworthy. Please report this.",
    };
  }
  if (s.complete === false) {
    return { cls: "warn", text: `dof ${s.dof} · not analysed`,
             title: "The dependency analysis ran out of its time budget, so "
               + "redundant and conflicting constraints were not looked for. "
               + "This is 'we did not look', not 'nothing found'." };
  }
  if (s.dof > 0) {
    return {
      cls: "dof",
      text: `${s.dof} DOF`,
      title: (s.free && s.free.length
        ? `still free: ${s.free.join(", ")}. Click to highlight them.`
        : "the sketch can still move; add constraints to pin it down"),
    };
  }
  return { cls: "ok", text: "fully constrained",
           title: "every degree of freedom is removed" };
}

function renderStatus() {
  updateConstraintButtons();
  renderChips();
  const anyCurve = [...model.lines, ...model.circles, ...model.arcs,
                    ...model.ellipses, ...model.splines, ...model.slots]
    .some((e) => !isConstruction(e));
  insertBtn.disabled = readOnly || !solveState.ok || !anyCurve;
  const st = chipState();
  dofEl.className = `sk-dof ${st.cls}`;
  if (solveState.stale && drag) dofEl.classList.add("stale");
  dofEl.textContent = st.text;
  dofEl.title = st.title;
}

function onDofClick() {
  const s = solveState;
  const set = (s.conflicting || []).length ? s.conflicting
    : (s.redundant || []).length ? s.redundant : null;
  if (set) {
    highlightSet({ constraints: set.map((c) => c.index).filter((i) => i != null) });
    const compiled = set.filter((c) => c.index == null);
    if (compiled.length) {
      actions.toast(
        `${compiled.length} of them come from ${compiled.map((c) => c.origin).join(", ")}`,
        "info");
    }
    return;
  }
  if ((s.free || []).length) highlightSet({ entities: s.free });
}

function highlightSet({ entities = [], constraints = [] }) {
  highlight = { entities: new Set(entities), constraints: new Set(constraints) };
  if (highlightTimer) clearTimeout(highlightTimer);
  highlightTimer = setTimeout(() => {
    clearHighlight();
    render();
    renderStatus();
  }, PULSE_MS);
  render();
  renderStatus();
}

function clearHighlight() {
  if (highlightTimer) clearTimeout(highlightTimer);
  highlightTimer = null;
  highlight = { entities: new Set(), constraints: new Set() };
}

// ----------------------------------------------------------------- render

function el(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

/** An SVG `A` path from centre/radius/angles. Inside the y-flipped world
 *  group a growing angle is a positive sweep, so sweep-flag follows its sign. */
function arcPathD(cx, cy, r, a0, a1) {
  const sweep = a1 - a0;
  const t0 = (a0 * Math.PI) / 180;
  const t1 = (a1 * Math.PI) / 180;
  const x0 = cx + r * Math.cos(t0);
  const y0 = cy + r * Math.sin(t0);
  if (Math.abs(sweep) >= FULL_TURN_DEG) {
    // one `A` cannot express a full turn; two halves can
    return `M ${x0} ${y0} A ${r} ${r} 0 1 1 ${cx - r * Math.cos(t0)} `
      + `${cy - r * Math.sin(t0)} A ${r} ${r} 0 1 1 ${x0} ${y0}`;
  }
  const x1 = cx + r * Math.cos(t1);
  const y1 = cy + r * Math.sin(t1);
  const large = Math.abs(sweep) > 180 ? 1 : 0;
  return `M ${x0} ${y0} A ${r} ${r} 0 ${large} ${sweep > 0 ? 1 : 0} ${x1} ${y1}`;
}

/** An SVG `A` path for an ellipse or elliptical arc. SVG's arc command takes
 *  the two radii and an x-axis rotation, so this is the same curve the solver
 *  and build123d use — no polyline approximation. The rotation is passed in
 *  the *world* frame; the y-flipped group handles the rest, exactly as the
 *  circular `arcPathD` lets the group decide the sweep's sense. */
function ellipsePathD(e, ctr) {
  const at = (deg) => ellipsePoint(e, ctr, deg);
  const rot = e.rotation;
  if (!e.bounded) {
    const p0 = at(0);
    const p1 = at(180);
    // one `A` cannot express a closed curve; two halves can
    return `M ${p0.x} ${p0.y} A ${e.a} ${e.b} ${rot} 1 1 ${p1.x} ${p1.y}`
      + ` A ${e.a} ${e.b} ${rot} 1 1 ${p0.x} ${p0.y} Z`;
  }
  const sweep = e.end_deg - e.start_deg;
  if (Math.abs(sweep) >= FULL_TURN_DEG) {
    const p0 = at(e.start_deg);
    const p1 = at(e.start_deg + 180);
    return `M ${p0.x} ${p0.y} A ${e.a} ${e.b} ${rot} 1 1 ${p1.x} ${p1.y}`
      + ` A ${e.a} ${e.b} ${rot} 1 1 ${p0.x} ${p0.y}`;
  }
  const p0 = at(e.start_deg);
  const p1 = at(e.end_deg);
  const large = Math.abs(sweep) > 180 ? 1 : 0;
  return `M ${p0.x} ${p0.y} A ${e.a} ${e.b} ${rot} ${large} `
    + `${sweep > 0 ? 1 : 0} ${p1.x} ${p1.y}`;
}

/** A Catmull-Rom preview of the interpolating spline the emitter will write.
 *  Display only — build123d's `Spline` owns the real end conditions. */
function splinePathD(pts) {
  if (pts.length < 2) return "";
  let d = `M ${pts[0].x} ${pts[0].y}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || p2;
    d += ` C ${p1.x + (p2.x - p0.x) / 6} ${p1.y + (p2.y - p0.y) / 6}`
      + ` ${p2.x - (p3.x - p1.x) / 6} ${p2.y - (p3.y - p1.y) / 6}`
      + ` ${p2.x} ${p2.y}`;
  }
  return d;
}

/** A slot's outline: two half-turn caps joined by two tangent sides. */
function slotPathD(c1, c2, r) {
  const dx = c2.x - c1.x;
  const dy = c2.y - c1.y;
  const len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len;
  const ny = dx / len;
  return `M ${c1.x + nx * r} ${c1.y + ny * r}`
    + ` L ${c2.x + nx * r} ${c2.y + ny * r}`
    + ` A ${r} ${r} 0 0 0 ${c2.x - nx * r} ${c2.y - ny * r}`
    + ` L ${c1.x - nx * r} ${c1.y - ny * r}`
    + ` A ${r} ${r} 0 0 0 ${c1.x + nx * r} ${c1.y + ny * r} Z`;
}

function entityOf(kind, name) {
  const bag = { line: model.lines, circle: model.circles, arc: model.arcs,
                ellipse: model.ellipses, spline: model.splines,
                slot: model.slots }[kind];
  return bag ? bag.find((e) => e.name === name) : null;
}

function strokeFor(kind, name) {
  const c = colors();
  if (highlight.entities.has(name)) return c.flag;
  if (isSelected(kind, name)) return c.sel;
  // Projected references and construction geometry are ghosted: they
  // constrain, they are not the profile, and the emitter does not write them.
  return isConstruction(entityOf(kind, name)) ? c.ghost : c.curve;
}

function hitHandler(kind, name) {
  return (e) => {
    if (tool !== "select") return;
    e.stopPropagation();
    toggleSelect(kind, name, e.shiftKey);
  };
}

/** A fat invisible hit target plus the visible stroke, the pair every curve
 *  in this canvas is drawn as. */
function curveNodes(shape, attrs, kind, name) {
  const sel = isSelected(kind, name) || highlight.entities.has(name);
  const hit = el(shape, { ...attrs, fill: "none", stroke: "rgba(0,0,0,0)",
                          "stroke-width": SNAP_PX / scale });
  hit.style.cursor = "pointer";
  hit.addEventListener("pointerdown", hitHandler(kind, name));
  const ghost = isConstruction(entityOf(kind, name));
  const vis = el(shape, {
    ...attrs, fill: "none", stroke: strokeFor(kind, name),
    "stroke-width": (sel ? 2.4 : ghost ? 1.1 : 1.6) / scale,
    "pointer-events": "none",
    ...(ghost ? { "stroke-dasharray": `${3 / scale} ${2.5 / scale}` } : {}),
  });
  if (highlight.entities.has(name)) vis.setAttribute("class", "sk-pulse");
  return [hit, vis];
}

function render() {
  if (!svg) return;
  const c = colors();
  const w = Math.max(1, svg.clientWidth) / scale;
  const h = Math.max(1, svg.clientHeight) / scale;
  svg.setAttribute("viewBox", `${-w / 2} ${-h / 2} ${w} ${h}`);
  svg.textContent = "";

  worldG = el("g", { transform: "scale(1,-1)" });
  svg.appendChild(worldG);

  // grid + axes
  const span = Math.max(w, h);
  const grid = el("g", {});
  const step = 10;
  const n = Math.ceil(span / 2 / step) + 1;
  for (let i = -n; i <= n; i++) {
    const v = i * step;
    grid.appendChild(el("line", {
      x1: v, y1: -n * step, x2: v, y2: n * step,
      stroke: i === 0 ? c.axis : c.grid, "stroke-width": 1 / scale,
    }));
    grid.appendChild(el("line", {
      x1: -n * step, y1: v, x2: n * step, y2: v,
      stroke: i === 0 ? c.axis : c.grid, "stroke-width": 1 / scale,
    }));
  }
  worldG.appendChild(grid);

  // slots first — they are a filled outline the rest sits on
  for (const sl of model.slots) {
    const a = refCoords(sl.c1);
    const b = refCoords(sl.c2);
    if (!a || !b) continue;
    worldG.append(...curveNodes("path", { d: slotPathD(a, b, sl.width / 2) },
                                "slot", sl.name));
  }

  for (const sp of model.splines) {
    const pts = sp.points.map(refCoords).filter(Boolean);
    if (pts.length < 2) continue;
    worldG.append(...curveNodes("path", { d: splinePathD(pts) },
                                "spline", sp.name));
  }

  for (const l of model.lines) {
    const a = refCoords(l.p1);
    const b = refCoords(l.p2);
    if (!a || !b) continue;
    worldG.append(...curveNodes("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y },
                                "line", l.name));
  }

  for (const cir of model.circles) {
    const ctr = refCoords(cir.center);
    if (!ctr) continue;
    worldG.append(...curveNodes("circle", { cx: ctr.x, cy: ctr.y, r: cir.r },
                                "circle", cir.name));
  }

  for (const a of model.arcs) {
    const ctr = refCoords(a.center);
    if (!ctr) continue;
    worldG.append(...curveNodes(
      "path", { d: arcPathD(ctr.x, ctr.y, a.r, a.start_deg, a.end_deg) },
      "arc", a.name));
  }

  for (const e of model.ellipses) {
    const ctr = refCoords(e.center);
    if (!ctr) continue;
    worldG.append(...curveNodes("path", { d: ellipsePathD(e, ctr) },
                                "ellipse", e.name));
  }

  // curve handles: the names the solver takes — an arc's `a1.start`/`a1.end`,
  // and an ellipse's `e1.major`/`e1.minor` (plus its bounds when it is an arc)
  const handleRefs = [];
  for (const a of model.arcs) {
    handleRefs.push(`${a.name}.start`, `${a.name}.end`);
  }
  for (const e of model.ellipses) {
    handleRefs.push(`${e.name}.major`, `${e.name}.minor`);
    if (e.bounded) handleRefs.push(`${e.name}.start`, `${e.name}.end`);
  }
  {
    for (const ref of handleRefs) {
      const at = refCoords(ref);
      if (!at) continue;
      const sel = isSelected("handle", ref);
      const s = 3.2 / scale;
      const box = el("rect", {
        x: at.x - s, y: at.y - s, width: s * 2, height: s * 2,
        fill: sel ? c.sel : c.ghost, stroke: c.ring, "stroke-width": 1 / scale,
      });
      box.style.cursor = "pointer";
      box.addEventListener("pointerdown", (e) => onEntityPointerDown(e, "handle", ref));
      worldG.appendChild(box);
    }
  }

  // points on top
  for (const p of model.points) {
    const sel = isSelected("point", p.name);
    const flagged = highlight.entities.has(p.name);
    const dot = el("circle", {
      cx: p.x, cy: p.y, r: (sel || flagged ? 5 : 3.6) / scale,
      fill: flagged ? c.flag : sel ? c.sel : p.fixed ? c.fixed : c.point,
      stroke: p.fixed ? c.sel : c.ring,
      "stroke-width": 1 / scale,
    });
    if (flagged) dot.setAttribute("class", "sk-pulse");
    dot.style.cursor = "pointer";
    dot.addEventListener("pointerdown", (e) => onEntityPointerDown(e, "point", p.name));
    worldG.appendChild(dot);
  }

  previewG = el("g", { "pointer-events": "none" });
  worldG.appendChild(previewG);
  renderPreview();
}

/** A press on a draggable ref: a click selects it, a movement drags it. */
function onEntityPointerDown(e, kind, ref) {
  if (tool !== "select" || e.button !== 0) return;
  e.stopPropagation();
  press = { kind, ref, shift: e.shiftKey, clientX: e.clientX,
            clientY: e.clientY, started: false };
  // Capture, like `startDrag` and the circle tool already do. Without it the
  // `pointerup` can land on anything the pointer happens to be over — another
  // element, the window chrome, a tab switch — and `press` stays armed, so the
  // *next* pointer movement (with no button held) becomes a drag that POSTs a
  // solve frame per animation frame.
  try {
    svg.setPointerCapture(e.pointerId);
  } catch (err) {
    /* a synthetic or already-released pointer: the buttons guard covers it */
  }
}

function dashed(shape, attrs) {
  return el(shape, {
    ...attrs, fill: "none", stroke: colors().ghost,
    "stroke-width": 1.2 / scale,
    "stroke-dasharray": `${4 / scale} ${3 / scale}`,
  });
}

function dashedLine(a, b) {
  return dashed("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y });
}

function renderPreview() {
  if (!previewG) return;
  const c = colors();
  previewG.textContent = "";

  // the drag: the predicted handle is at the cursor NOW, and a hairline shows
  // how far the constraints are holding the geometry back from it
  if (drag) {
    const solved = refCoords(drag.ref);
    const { x, y } = drag.cursor;
    if (solved) {
      previewG.appendChild(el("line", {
        x1: solved.x, y1: solved.y, x2: x, y2: y, stroke: c.ghost,
        "stroke-width": 0.8 / scale,
        "stroke-dasharray": `${2 / scale} ${2 / scale}`,
      }));
    }
    previewG.appendChild(el("circle", {
      cx: x, cy: y, r: 4.5 / scale, fill: "none", stroke: c.ghost,
      "stroke-width": 1.4 / scale,
    }));
    return;
  }

  if (chainPrev && cursor && (tool === "line" || tool === "arcTan")) {
    const a = refCoords(chainPrev);
    if (a) previewG.appendChild(dashedLine(a, cursor));
  }
  if (!pending || !cursor) return;
  if (pending.kind === "circle") {
    const ctr = refCoords(pending.center);
    if (ctr && pending.r > 0) {
      previewG.appendChild(
        dashed("circle", { cx: ctr.x, cy: ctr.y, r: pending.r }));
    }
  } else if (pending.kind === "arcC") {
    const ctr = refCoords(pending.center);
    if (!ctr) return;
    if (pending.start_deg === undefined) {
      previewG.appendChild(dashedLine(ctr, cursor));
    } else {
      const end = sweepTo(pending.start_deg,
                          angleDeg(ctr.x, ctr.y, cursor.x, cursor.y), true);
      previewG.appendChild(dashed("path", {
        d: arcPathD(ctr.x, ctr.y, pending.r, pending.start_deg, end) }));
    }
  } else if (pending.kind === "arc3") {
    if (!pending.end) {
      previewG.appendChild(dashedLine(pending.start, cursor));
    } else {
      const arc = circumArc(pending.start, cursor, pending.end);
      if (arc) {
        previewG.appendChild(dashed("path", {
          d: arcPathD(arc.cx, arc.cy, arc.r, arc.start_deg, arc.end_deg) }));
      }
    }
  } else if (pending.kind === "ellipse") {
    const ctr = refCoords(pending.center);
    if (!ctr) return;
    if (pending.a === undefined) {
      previewG.appendChild(dashedLine(ctr, cursor));
    } else {
      previewG.appendChild(dashed("path", {
        d: ellipsePathD({ a: pending.a, rotation: pending.rotation,
                          b: minorFrom(pending, ctr, cursor.x, cursor.y),
                          bounded: false }, ctr) }));
    }
  } else if (pending.kind === "spline") {
    const pts = pending.points.map(refCoords).filter(Boolean);
    previewG.appendChild(
      dashed("path", { d: splinePathD([...pts, cursor]) }));
  } else if (pending.kind === "slot") {
    const a = refCoords(pending.c1);
    if (a) previewG.appendChild(dashedLine(a, cursor));
  }
}

// ---------------------------------------------------------------- insert

async function insertSnippet() {
  // Construction geometry does not emit, so a sketch that is *only* projected
  // references has nothing to insert.
  const anyCurve = [...model.lines, ...model.circles, ...model.arcs,
                    ...model.ellipses, ...model.splines, ...model.slots]
    .some((e) => !isConstruction(e));
  if (!anyCurve || readOnly) return;
  insertBtn.disabled = true;
  // Same generation guard as every other round trip: two awaits stand between
  // here and `applySolution`, and a model that changed under them (a part
  // switch, a second Insert) must not have someone else's solution written
  // into it. `solveSeq` is the counter `solveAndRender` already uses, so an
  // insert also supersedes an in-flight background solve.
  const my = ++solveSeq;
  // FR10: the round-trip block's name must shadow nothing already in the
  // script — two blocks of one name define `sketch_<name>()` twice and the
  // second silently wins. The server owns the naming rule (it also counts
  // pre-FR10 `def sketch_*(` definitions), so ask it rather than guess.
  let name = "profile";
  try {
    const info = await api.sketchBlocks(editor.getScript() || "");
    name = info.next_name || name;
  } catch (err) {
    name = blockName || name;
  }
  if (my !== solveSeq) return renderStatus();   // the model moved on
  let res = null;
  let thrown = null;
  try {
    // One emitter for both layers: this is the same call an agent makes, so
    // the same spec yields byte-identical build123d either way (AC1).
    res = await api.solveSketch(entitiesSpec(), model.constraints,
                                solveOpts({ emit: "function",
                                            diagnostics: "full",
                                            persist: name }));
  } catch (err) {
    thrown = err;
  }
  if (my !== solveSeq) return renderStatus();   // do not paste a stale solve
  // An emission that would not rebuild comes back as a `validation_error`
  // naming the junction — surface it verbatim rather than pasting code the
  // kernel will refuse.
  const error = errorOf(res, thrown);
  if (error) {
    if (error.details && error.details.diagnostics) {
      solveState = failureState(error);
    }
    actions.toast(`sketch not inserted — ${error.message}`, "error");
    render();
    renderStatus();
    return;
  }
  applySolution(res);
  solveState = fromResult(res);
  render();
  renderStatus();
  if (!editor.insertText(res.emit.code)) {
    actions.toast("Open a script part first — the sketch inserts into its code",
                  "error");
    return;
  }
  // The sketch now belongs to the block just written; the block it came from
  // (if any) is left in the script untouched, because replacing a user's text
  // is exactly what FR10 says never to do silently.
  const previous = blockName;
  blockName = res.emit.persist || name;
  const stale = previous && previous !== blockName
    ? ` — the earlier block “${previous}” is still in the script, remove it if `
      + "this replaces it" : "";
  const warnings = (res.emit.warnings || []).map((w) => w.message);
  if (warnings.length) {
    actions.toast(`sketch inserted as sketch_${blockName}() with `
                  + `${warnings.length} warning(s): ${warnings.join("; ")}`,
                  "info");
  } else {
    actions.toast(`sketch inserted — call sketch_${blockName}() from `
                  + `build(p)${stale}`);
  }
}
