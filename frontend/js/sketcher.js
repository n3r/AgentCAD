// 2D sketch editor overlay (MVP). A dark SVG canvas over the viewport:
// draw points / line chains / circles, apply constraints, and every mutation
// round-trips the whole spec through POST /api/sketch/solve (the first-party
// scipy solver) — solved coordinates re-render the canvas. "Insert" appends a
// build123d sketch_profile() snippet to the code editor; the user wires it
// into build(p) themselves (stated in a toast).
//
// Sketch plane is XY, y up (SVG y-axis is flipped via a scale(1,-1) group);
// units are mm, 1 SVG user unit == 1 mm, zoom via the wheel.

import { api } from "./api.js";
import { state, onKeys } from "./state.js";
import * as editor from "./editor.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const SNAP_PX = 10; // screen px within which a click reuses an existing point
const COINCIDE_EPS = 1e-6; // endpoint coincidence -> closed loop (mm)

let actions = null;
let host = null; // #sketcher
let btn = null; // #sketch-btn
let svg = null;
let worldG = null; // y-flipped group holding grid + entities
let previewG = null; // rubber-band overlays (redrawn on pointermove)
let statusEl = null;
let chipsEl = null;
let insertBtn = null;
let toolBtns = {}; // tool name -> button
let conBtns = {}; // constraint name -> button

let open = false;
let tool = "select"; // select | point | line | circle
let model = null; // {points, lines, circles, constraints}
let seq = null; // name counters
let selection = []; // [{kind, name}] kind: point|line|circle
let chainPrev = null; // line tool: previous point name in the chain
let pending = null; // circle tool drag: {center, created, r}
let cursor = null; // last pointer sketch coords (for previews)
let scale = 4; // px per mm
let solveSeq = 0;
let solveState = { ok: true, dof: 0, local: true };

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
  });
  document.addEventListener("keydown", onKey);
  resetModel();
  buildUI();
}

function resetModel() {
  model = { points: [], lines: [], circles: [], constraints: [] };
  seq = { p: 0, l: 0, c: 0 };
  selection = [];
  chainPrev = null;
  pending = null;
  solveState = { ok: true, dof: 0, local: true };
}

function show() {
  if (state.mode !== "part") return;
  open = true;
  host.classList.remove("hidden");
  btn.classList.add("active");
  render();
  renderStatus();
}

function close() {
  open = false;
  host.classList.add("hidden");
  btn.classList.remove("active");
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
    ["select", "Select", "Select entities (shift-click adds)"],
    ["point", "Point", "Click to place points"],
    ["line", "Line", "Click-click line chains; Esc ends the chain"],
    ["circle", "Circle", "Press at the center, drag the radius"],
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
    ["radius", "Rad", "Set the selected circle's radius"],
  ]) {
    const b = skButton(label, title, () => applyConstraint(name));
    conBtns[name] = b;
    bar.appendChild(b);
  }
  bar.appendChild(sep());

  bar.appendChild(skButton("Delete", "Delete the selection (Del)", deleteSelection));
  bar.appendChild(skButton("Clear", "Clear the whole sketch", () => {
    resetModel();
    render();
    renderStatus();
  }));

  statusEl = document.createElement("span");
  statusEl.className = "sk-status";
  bar.appendChild(statusEl);

  const spacer = document.createElement("span");
  spacer.className = "sk-spacer";
  bar.appendChild(spacer);

  insertBtn = skButton("Insert → script", "Append a build123d sketch_profile() snippet to the editor", insertSnippet);
  insertBtn.classList.add("sk-primary");
  bar.appendChild(insertBtn);
  bar.appendChild(skButton("✕", "Close the sketcher", close));

  svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "sk-canvas");
  svg.addEventListener("pointerdown", onPointerDown);
  svg.addEventListener("pointermove", onPointerMove);
  svg.addEventListener("pointerup", onPointerUp);
  svg.addEventListener("wheel", onWheel, { passive: false });

  chipsEl = document.createElement("div");
  chipsEl.className = "sk-chips";

  host.append(bar, svg, chipsEl);
  setTool("line");

  new ResizeObserver(() => open && render()).observe(svg);
}

function sep() {
  const s = document.createElement("span");
  s.className = "sk-sep";
  return s;
}

function setTool(name) {
  tool = name;
  chainPrev = null;
  pending = null;
  for (const [n, b] of Object.entries(toolBtns)) {
    b.classList.toggle("active", n === name);
  }
  renderPreview();
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

function selectedOf(kind) {
  return selection.filter((s) => s.kind === kind).map((s) => s.name);
}

// ------------------------------------------------------------ model edits

function point(name) {
  return model.points.find((p) => p.name === name);
}

function addPoint(x, y) {
  const name = `p${++seq.p}`;
  // The very first point anchors the sketch (removes 2 rigid-body DOF).
  const fixed = model.points.length === 0;
  model.points.push({ name, x, y, fixed });
  return name;
}

function nearPoint(x, y) {
  const tol = SNAP_PX / scale;
  let best = null;
  let bestD = tol;
  for (const p of model.points) {
    const d = Math.hypot(p.x - x, p.y - y);
    if (d <= bestD) {
      best = p.name;
      bestD = d;
    }
  }
  return best;
}

function mutated() {
  render();
  solveAndRender();
}

function deleteSelection() {
  if (!selection.length) return;
  const pts = new Set(selectedOf("point"));
  const lns = new Set(selectedOf("line"));
  const crc = new Set(selectedOf("circle"));
  // cascade: a removed point takes its lines and centered circles with it
  for (const l of model.lines) {
    if (pts.has(l.p1) || pts.has(l.p2)) lns.add(l.name);
  }
  for (const c of model.circles) {
    if (pts.has(c.center)) crc.add(c.name);
  }
  model.points = model.points.filter((p) => !pts.has(p.name));
  model.lines = model.lines.filter((l) => !lns.has(l.name));
  model.circles = model.circles.filter((c) => !crc.has(c.name));
  model.constraints = model.constraints.filter((con) => {
    for (const key of ["p", "q", "at"]) if (pts.has(con[key])) return false;
    for (const key of ["ln", "l1", "l2"]) if (lns.has(con[key])) return false;
    for (const key of ["c", "c1", "c2"]) if (crc.has(con[key])) return false;
    return true;
  });
  selection = [];
  chainPrev = null;
  mutated();
}

// ------------------------------------------------------------ constraints

function applyConstraint(name) {
  const pts = selectedOf("point");
  const lns = selectedOf("line");
  const crc = selectedOf("circle");
  if (name === "fixed" && pts.length === 1) {
    const p = point(pts[0]);
    p.fixed = !p.fixed;
    mutated();
    return;
  }
  if (name === "coincident" && pts.length === 2) {
    model.constraints.push({ type: "coincident", p: pts[0], q: pts[1] });
  } else if (name === "distance" && pts.length === 2) {
    const a = point(pts[0]);
    const b = point(pts[1]);
    const cur = Math.hypot(a.x - b.x, a.y - b.y);
    const d = parseFloat(prompt("Distance (mm):", fmtNum(cur)));
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
  } else if (name === "radius" && crc.length === 1) {
    const c = model.circles.find((x) => x.name === crc[0]);
    const r = parseFloat(prompt("Radius (mm):", fmtNum(c.r)));
    if (!Number.isFinite(r) || r <= 0) return;
    model.constraints.push({ type: "radius", c: crc[0], r });
  } else {
    return; // enablement should prevent this; ignore quietly
  }
  mutated();
}

function updateConstraintButtons() {
  const nPts = selectedOf("point").length;
  const nLns = selectedOf("line").length;
  const nCrc = selectedOf("circle").length;
  const only = (p, l, c) =>
    nPts === p && nLns === l && nCrc === c && selection.length === p + l + c;
  const enable = {
    fixed: only(1, 0, 0),
    coincident: only(2, 0, 0),
    distance: only(2, 0, 0),
    horizontal: only(0, 1, 0),
    vertical: only(0, 1, 0),
    parallel: only(0, 2, 0),
    perpendicular: only(0, 2, 0),
    radius: only(0, 0, 1),
  };
  for (const [name, b] of Object.entries(conBtns)) b.disabled = !enable[name];
}

function constraintLabel(con) {
  switch (con.type) {
    case "coincident": return `coin ${con.p}=${con.q}`;
    case "distance": return `dist ${con.p}–${con.q} = ${fmtNum(con.d)}`;
    case "horizontal": return `H ${con.ln}`;
    case "vertical": return `V ${con.ln}`;
    case "parallel": return `par ${con.l1},${con.l2}`;
    case "perpendicular": return `perp ${con.l1},${con.l2}`;
    case "radius": return `rad ${con.c} = ${fmtNum(con.r)}`;
    default: return con.type;
  }
}

function renderChips() {
  chipsEl.textContent = "";
  model.constraints.forEach((con, i) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "sk-chip";
    chip.title = "Click to remove this constraint";
    chip.textContent = `${constraintLabel(con)} ×`;
    chip.addEventListener("click", () => {
      model.constraints.splice(i, 1);
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
  const { x, y } = toSketch(e);
  if (tool === "point") {
    if (!nearPoint(x, y)) {
      addPoint(x, y);
      mutated();
    }
    return;
  }
  if (tool === "line") {
    let name = nearPoint(x, y);
    if (name && name === chainPrev) {
      chainPrev = null; // clicking the previous point ends the chain
      renderPreview();
      return;
    }
    if (!name) name = addPoint(x, y);
    if (chainPrev && chainPrev !== name) {
      const dup = model.lines.some(
        (l) =>
          (l.p1 === chainPrev && l.p2 === name) ||
          (l.p1 === name && l.p2 === chainPrev)
      );
      if (!dup) model.lines.push({ name: `ln${++seq.l}`, p1: chainPrev, p2: name });
    }
    chainPrev = name;
    mutated();
    return;
  }
  if (tool === "circle") {
    let center = nearPoint(x, y);
    const created = !center;
    if (!center) center = addPoint(x, y);
    pending = { center, created, r: 0 };
    svg.setPointerCapture(e.pointerId);
    renderPreview();
    return;
  }
  // select tool: empty-canvas click clears (entity handlers stopPropagation)
  if (!e.shiftKey) {
    selection = [];
    render();
    renderStatus();
  }
}

function onPointerMove(e) {
  cursor = toSketch(e);
  if (pending) {
    const c = point(pending.center);
    pending.r = Math.hypot(cursor.x - c.x, cursor.y - c.y);
  }
  if (pending || (tool === "line" && chainPrev)) renderPreview();
}

function onPointerUp() {
  if (!pending) return;
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
    if (chainPrev || pending) {
      chainPrev = null;
      pending = null;
      renderPreview();
    } else if (selection.length) {
      selection = [];
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

// ------------------------------------------------------------------ solve

function entitiesSpec() {
  return {
    points: model.points.map((p) => ({
      name: p.name, x: p.x, y: p.y, fixed: !!p.fixed,
    })),
    lines: model.lines.map((l) => ({ name: l.name, p1: l.p1, p2: l.p2 })),
    circles: model.circles.map((c) => ({
      name: c.name, center: c.center, r: c.r,
    })),
  };
}

function freeParamCount() {
  return (
    model.points.filter((p) => !p.fixed).length * 2 + model.circles.length
  );
}

async function solveAndRender() {
  const free = freeParamCount();
  if (!model.constraints.length || free === 0) {
    // Nothing to solve: no residuals (or no free geometry). The sketch is
    // trivially consistent; dof is just the free parameter count.
    solveState = { ok: true, dof: free, local: true };
    renderStatus();
    return;
  }
  const my = ++solveSeq;
  let res;
  try {
    res = await api.solveSketch(entitiesSpec(), model.constraints);
  } catch (err) {
    if (my !== solveSeq) return;
    solveState = { ok: false, msg: err.message };
    renderStatus();
    return;
  }
  if (my !== solveSeq) return;
  if (res.error) {
    const d = res.error.details || {};
    solveState = {
      ok: false,
      maxResidual: d.max_residual,
      dof: d.dof,
      msg: res.error.message,
    };
  } else {
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
    solveState = { ok: true, dof: res.dof };
    render();
  }
  renderStatus();
}

function renderStatus() {
  updateConstraintButtons();
  renderChips();
  insertBtn.disabled = !solveState.ok || (!model.lines.length && !model.circles.length);
  statusEl.classList.toggle("err", !solveState.ok);
  if (solveState.ok) {
    statusEl.textContent = `solved · dof ${solveState.dof}`;
  } else if (solveState.maxResidual != null) {
    statusEl.textContent =
      `unsolved · max residual ${Number(solveState.maxResidual).toExponential(1)}` +
      (solveState.dof != null ? ` · dof ${solveState.dof}` : "");
  } else {
    statusEl.textContent = `unsolved · ${solveState.msg || "solver error"}`;
  }
}

// ----------------------------------------------------------------- render

function el(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function render() {
  if (!svg) return;
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
      stroke: i === 0 ? "#33363d" : "#232529", "stroke-width": 1 / scale,
    }));
    grid.appendChild(el("line", {
      x1: -n * step, y1: v, x2: n * step, y2: v,
      stroke: i === 0 ? "#33363d" : "#232529", "stroke-width": 1 / scale,
    }));
  }
  worldG.appendChild(grid);

  // lines (fat invisible hit target + visible stroke)
  for (const l of model.lines) {
    const a = point(l.p1);
    const b = point(l.p2);
    if (!a || !b) continue;
    const sel = isSelected("line", l.name);
    const hit = el("line", {
      x1: a.x, y1: a.y, x2: b.x, y2: b.y,
      stroke: "rgba(0,0,0,0)", "stroke-width": SNAP_PX / scale,
    });
    hit.style.cursor = "pointer";
    hit.addEventListener("pointerdown", (e) => {
      if (tool !== "select") return;
      e.stopPropagation();
      toggleSelect("line", l.name, e.shiftKey);
    });
    const vis = el("line", {
      x1: a.x, y1: a.y, x2: b.x, y2: b.y,
      stroke: sel ? "#e8b06a" : "#c9ced6",
      "stroke-width": (sel ? 2.4 : 1.6) / scale,
      "pointer-events": "none",
    });
    worldG.append(hit, vis);
  }

  // circles
  for (const c of model.circles) {
    const ctr = point(c.center);
    if (!ctr) continue;
    const sel = isSelected("circle", c.name);
    const hit = el("circle", {
      cx: ctr.x, cy: ctr.y, r: c.r,
      fill: "none", stroke: "rgba(0,0,0,0)", "stroke-width": SNAP_PX / scale,
    });
    hit.style.cursor = "pointer";
    hit.addEventListener("pointerdown", (e) => {
      if (tool !== "select") return;
      e.stopPropagation();
      toggleSelect("circle", c.name, e.shiftKey);
    });
    const vis = el("circle", {
      cx: ctr.x, cy: ctr.y, r: c.r,
      fill: "none", stroke: sel ? "#e8b06a" : "#c9ced6",
      "stroke-width": (sel ? 2.4 : 1.6) / scale,
      "pointer-events": "none",
    });
    worldG.append(hit, vis);
  }

  // points on top
  for (const p of model.points) {
    const sel = isSelected("point", p.name);
    const dot = el("circle", {
      cx: p.x, cy: p.y, r: (sel ? 5 : 3.6) / scale,
      fill: sel ? "#e8b06a" : p.fixed ? "#d99a4e" : "#8b919b",
      stroke: p.fixed ? "#e8b06a" : "#17181b",
      "stroke-width": 1 / scale,
    });
    dot.style.cursor = "pointer";
    dot.addEventListener("pointerdown", (e) => {
      if (tool !== "select") return;
      e.stopPropagation();
      toggleSelect("point", p.name, e.shiftKey);
    });
    worldG.appendChild(dot);
  }

  previewG = el("g", { "pointer-events": "none" });
  worldG.appendChild(previewG);
  renderPreview();
}

function renderPreview() {
  if (!previewG) return;
  previewG.textContent = "";
  if (tool === "line" && chainPrev && cursor) {
    const a = point(chainPrev);
    if (a) {
      previewG.appendChild(el("line", {
        x1: a.x, y1: a.y, x2: cursor.x, y2: cursor.y,
        stroke: "#d99a4e", "stroke-width": 1.2 / scale,
        "stroke-dasharray": `${4 / scale} ${3 / scale}`,
      }));
    }
  }
  if (pending) {
    const c = point(pending.center);
    if (c && pending.r > 0) {
      previewG.appendChild(el("circle", {
        cx: c.x, cy: c.y, r: pending.r,
        fill: "none", stroke: "#d99a4e", "stroke-width": 1.2 / scale,
        "stroke-dasharray": `${4 / scale} ${3 / scale}`,
      }));
    }
  }
}

// ---------------------------------------------------------------- insert

function fmtNum(v) {
  let x = Math.round(v * 1e6) / 1e6;
  if (Object.is(x, -0)) x = 0;
  let s = String(x);
  if (!s.includes(".") && !s.includes("e")) s += ".0";
  return s;
}

/** Group the lines into maximal chains: [{pts: [names...], closed}]. */
function findChains() {
  const unused = new Set(model.lines.map((l) => l.name));
  const byName = new Map(model.lines.map((l) => [l.name, l]));
  const incident = new Map(); // point -> [line names]
  for (const l of model.lines) {
    for (const p of [l.p1, l.p2]) {
      if (!incident.has(p)) incident.set(p, []);
      incident.get(p).push(l.name);
    }
  }
  const takeNext = (pt, exclude) => {
    for (const ln of incident.get(pt) || []) {
      if (ln !== exclude && unused.has(ln)) return ln;
    }
    return null;
  };
  const chains = [];
  for (const start of model.lines) {
    if (!unused.has(start.name)) continue;
    unused.delete(start.name);
    const pts = [start.p1, start.p2];
    // extend forward from the tail
    let last = start.name;
    for (;;) {
      const tail = pts[pts.length - 1];
      const ln = takeNext(tail, last);
      if (!ln) break;
      unused.delete(ln);
      const l = byName.get(ln);
      pts.push(l.p1 === tail ? l.p2 : l.p1);
      last = ln;
      if (pts[pts.length - 1] === pts[0]) break; // closed by shared point
    }
    // extend backward from the head (open chains only)
    if (pts[pts.length - 1] !== pts[0]) {
      let first = start.name;
      for (;;) {
        const head = pts[0];
        const ln = takeNext(head, first);
        if (!ln) break;
        unused.delete(ln);
        const l = byName.get(ln);
        pts.unshift(l.p1 === head ? l.p2 : l.p1);
        first = ln;
        if (pts[0] === pts[pts.length - 1]) break;
      }
    }
    let closed = pts.length > 3 && pts[0] === pts[pts.length - 1];
    if (closed) pts.pop(); // the closing point is re-emitted by the snippet
    if (!closed && pts.length > 2) {
      const a = point(pts[0]);
      const b = point(pts[pts.length - 1]);
      if (a && b && Math.hypot(a.x - b.x, a.y - b.y) < COINCIDE_EPS) {
        pts.pop(); // coincident-by-coords endpoints: same closed loop
        closed = true;
      }
    }
    chains.push({ pts, closed });
  }
  return chains;
}

function buildSnippet() {
  const chains = findChains();
  const body = [];
  if (chains.length) {
    body.push("        with BuildLine():");
    for (const chain of chains) {
      const coords = chain.pts.map((n) => {
        const p = point(n);
        return `(${fmtNum(p.x)}, ${fmtNum(p.y)})`;
      });
      if (chain.closed) coords.push(coords[0]); // close the loop
      body.push(`            Polyline(${coords.join(", ")})`);
    }
    if (chains.some((c) => c.closed)) {
      body.push("        make_face()");
    }
  }
  for (const c of model.circles) {
    const ctr = point(c.center);
    body.push(`        with Locations((${fmtNum(ctr.x)}, ${fmtNum(ctr.y)})):`);
    body.push(`            Circle(radius=${fmtNum(c.r)})`);
  }
  return [
    "",
    "",
    "# --- agentcad sketch (auto-generated) ---",
    "def sketch_profile():",
    "    with BuildSketch(Plane.XY) as _sk:",
    ...body,
    "    return _sk.sketch",
    "",
  ].join("\n");
}

function insertSnippet() {
  if (!model.lines.length && !model.circles.length) return;
  if (!editor.insertText(buildSnippet())) {
    actions.toast("Open a script part first — the sketch inserts into its code", "error");
    return;
  }
  actions.toast("sketch inserted — call sketch_profile() from build(p)");
}
