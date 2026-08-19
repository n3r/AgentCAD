// Three.js viewport: parses the ACM1 mesh binary, renders shaded geometry
// with subtle B-rep edge overlays, Z-up CAD orientation, orbit controls,
// fit-to-bbox, and raycast picking in assembly mode.

import * as THREE from "three";
import { OrbitControls } from "/vendor/OrbitControls.js";
import { TransformControls } from "/vendor/TransformControls.js";
import { groupInstances, instanceCounts } from "./instancing.js";

const DEG = Math.PI / 180;
const DEFAULT_PART_COLOR = "#98a2ad";
const SNAP_TRANSLATE_MM = 1;
const SNAP_ROTATE_DEG = 5;

let renderer = null;
let scene = null;
let camera = null;
let controls = null;
let contentGroup = null; // holds current part or assembly groups
let gridHelper = null;
let onPickCallback = null;

// geometry cache: `${partId}:${meshKey}` -> {geometry, edges, faceMap?}.
// main.js passes meshKey as `${cacheKey}:${lod}` so a coarse LOD tier and the
// full-resolution mesh of the same build never collide in the cache. faceMap
// is the optional triangle->B-rep-face Uint32Array (full-resolution mesh
// only), set lazily via setFaceMap; pick() maps hit triangles through it.
const geomCache = new Map();
const GEOM_CACHE_MAX = 32;

// what is currently displayed
let current = { mode: null, partId: null, key: null, items: [] };
let displayedKeys = new Set(); // geometry cache keys in the scene right now
let selectedInstanceId = null;

// transform gizmo (assembly mode). Created lazily; its helper lives in the
// scene root (not contentGroup, which is cleared on every re-render).
let gizmo = null;
let gizmoCallbacks = {}; // { onCommit(transform), onLive(transform) }
let gizmoInteracting = false; // a gizmo axis is being grabbed right now
let gizmoDragged = false; // the grab actually moved the object
let attachedInstanceId = null;

// 3D palette, swapped by theme.js (dark defaults). Grid size/divisions are
// remembered so a theme change can rebuild the grid in place.
let sceneTheme = {
  background: 0x17181b,
  gridMajor: 0x2c2f36,
  gridMinor: 0x22242a,
  edge: 0x0d0e10,
  diffAdded: 0x6fbf8f,
  diffRemoved: 0xe0655c,
};
let gridSize = 400;
let gridDivisions = 40;

// Per-frame subscribers (PRD-008's comment pins). They run inside the existing
// animation loop rather than in a timer of their own, because an HTML overlay
// that is projected with camera.project() must move on exactly the frames the
// camera does. Kept tiny and side-effect-only: this is not a place to fetch.
const frameHooks = new Set();
// The canvas box in CSS pixels, tracked by the ResizeObserver so projecting a
// world point costs no layout read.
let viewSize = { w: 1, h: 1 };
const _projectV = new THREE.Vector3();

const edgeMaterial = new THREE.LineBasicMaterial({
  color: sceneTheme.edge,
  transparent: true,
  opacity: 0.5,
});

// ------------------------------------------------------------------ ACM1

export function parseACM(buffer) {
  const dv = new DataView(buffer);
  const magic = String.fromCharCode(
    dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3)
  );
  if (magic !== "ACM1") throw new Error("not an ACM1 mesh buffer");
  const nv = dv.getUint32(4, true);
  const nt = dv.getUint32(8, true);
  const nep = dv.getUint32(12, true);
  const nel = dv.getUint32(16, true);
  let off = 20;
  const positions = new Float32Array(buffer, off, nv * 3); off += nv * 12;
  const normals = new Float32Array(buffer, off, nv * 3); off += nv * 12;
  const indices = new Uint32Array(buffer, off, nt * 3); off += nt * 12;
  const edgeLengths = new Uint32Array(buffer, off, nel); off += nel * 4;
  const edgePoints = new Float32Array(buffer, off, nep * 3);
  return { positions, normals, indices, edgeLengths, edgePoints, nv, nt };
}

function buildGeometry(parsed) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(parsed.positions, 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(parsed.normals, 3));
  geometry.setIndex(new THREE.BufferAttribute(parsed.indices, 1));

  // Edge polylines -> line segment soup.
  let nSegments = 0;
  for (const len of parsed.edgeLengths) if (len > 1) nSegments += len - 1;
  const segs = new Float32Array(nSegments * 6);
  let p = 0; // index into edgePoints (points)
  let s = 0; // write offset into segs (floats)
  for (const len of parsed.edgeLengths) {
    for (let i = 0; i + 1 < len; i++) {
      const a = (p + i) * 3;
      const b = (p + i + 1) * 3;
      segs[s++] = parsed.edgePoints[a];
      segs[s++] = parsed.edgePoints[a + 1];
      segs[s++] = parsed.edgePoints[a + 2];
      segs[s++] = parsed.edgePoints[b];
      segs[s++] = parsed.edgePoints[b + 1];
      segs[s++] = parsed.edgePoints[b + 2];
    }
    p += len;
  }
  const edges = new THREE.BufferGeometry();
  edges.setAttribute("position", new THREE.BufferAttribute(segs, 3));
  return { geometry, edges, triangles: parsed.nt };
}

function getGeometry(partId, key, buffer) {
  const cacheKey = `${partId}:${key}`;
  let entry = geomCache.get(cacheKey);
  if (!entry) {
    entry = buildGeometry(parseACM(buffer));
    geomCache.set(cacheKey, entry);
    // Drop stale entries for the same part, then oldest overall — but never
    // geometry that is on screen right now.
    for (const k of [...geomCache.keys()]) {
      if (k !== cacheKey && !displayedKeys.has(k) && k.startsWith(`${partId}:`)) {
        disposeEntry(k);
      }
    }
    for (const k of [...geomCache.keys()]) {
      if (geomCache.size <= GEOM_CACHE_MAX) break;
      if (k !== cacheKey && !displayedKeys.has(k)) disposeEntry(k);
    }
  }
  return entry;
}

function disposeEntry(cacheKey) {
  const entry = geomCache.get(cacheKey);
  if (!entry) return;
  entry.geometry.dispose();
  entry.edges.dispose();
  geomCache.delete(cacheKey);
}

// ------------------------------------------------------------------ scene

export function init(container, { onPick } = {}) {
  onPickCallback = onPick || null;

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(container.clientWidth, container.clientHeight);
  container.appendChild(renderer.domElement);

  scene = new THREE.Scene();
  scene.background = new THREE.Color(sceneTheme.background);

  camera = new THREE.PerspectiveCamera(
    45,
    container.clientWidth / Math.max(1, container.clientHeight),
    0.1,
    50000
  );
  camera.up.set(0, 0, 1); // CAD convention: Z up
  camera.position.set(120, -120, 90);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;

  const hemi = new THREE.HemisphereLight(0xdbe4ee, 0x24211d, 1.1);
  hemi.position.set(0, 0, 1);
  scene.add(hemi);
  const key = new THREE.DirectionalLight(0xffffff, 1.5);
  key.position.set(250, -180, 320);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x8aa4c0, 0.35);
  fill.position.set(-200, 240, -120);
  scene.add(fill);

  setGrid(400, 40);

  contentGroup = new THREE.Group();
  scene.add(contentGroup);

  viewSize = { w: container.clientWidth, h: Math.max(1, container.clientHeight) };
  const ro = new ResizeObserver(() => {
    const w = container.clientWidth;
    const h = Math.max(1, container.clientHeight);
    viewSize = { w, h };
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  });
  ro.observe(container);

  // click-to-pick with a drag threshold so orbiting never selects
  let downPos = null;
  renderer.domElement.addEventListener("pointerdown", (e) => {
    downPos = [e.clientX, e.clientY];
  });
  renderer.domElement.addEventListener("pointerup", (e) => {
    const wasGizmo = gizmoInteracting;
    gizmoInteracting = false;
    if (!downPos) return;
    const moved = Math.hypot(e.clientX - downPos[0], e.clientY - downPos[1]);
    downPos = null;
    // A press on a gizmo axis is not a selection click, even if it didn't move.
    if (wasGizmo || moved > 4 || !onPickCallback) return;
    const hit = pick(e);
    onPickCallback(hit, e);
  });

  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
    for (const hook of frameHooks) {
      // A hook that throws would silently kill the render loop for the rest
      // of the session — the one place in this module where swallowing is the
      // safer failure.
      try {
        hook();
      } catch (err) {
        console.error("viewport frame hook failed", err);
      }
    }
  });
}

/** Run `fn` after every rendered frame. Returns an unsubscribe function. */
export function onFrame(fn) {
  frameHooks.add(fn);
  return () => frameHooks.delete(fn);
}

/** Project a world point [x,y,z] to CSS pixels inside the viewport box, or
 *  null when it is behind the camera. The overlay host is a sibling of the
 *  canvas filling the same box, so these coordinates need no further offset —
 *  the #facecard/#hud pattern, with no CSS2DRenderer vendored. */
export function projectPoint(point) {
  if (!camera || !point) return null;
  const v = _projectV.set(point[0], point[1], point[2]).project(camera);
  if (!Number.isFinite(v.x) || !Number.isFinite(v.y) || v.z > 1) return null;
  return {
    x: (v.x * 0.5 + 0.5) * viewSize.w,
    y: (-v.y * 0.5 + 0.5) * viewSize.h,
  };
}

/** The mean vertex position of one B-rep face of the displayed part, in world
 *  space — where a pin for that face belongs. null when that part is not on
 *  stage or its triangle->face sidecar has not loaded yet — and null is the
 *  whole answer: the caller draws no pin at all until this returns one,
 *  because the only other position available is where the face used to be.
 *
 *  Read against the CURRENT geometry and the ordinal the server RESOLVED, not
 *  the one the anchor stored: after a rebuild the two need not be the same
 *  face, and a pin drawn on the stored ordinal is the mis-pin this feature
 *  exists to avoid. */
export function faceCentroid(partId, faceIndex) {
  if (faceIndex == null) return null;
  if (current.mode !== "part" || current.partId !== partId) return null;
  const entry = geomCache.get(`${partId}:${current.key}`);
  if (!entry || !entry.faceMap || !entry.geometry.index) return null;
  const idx = entry.geometry.index.array;
  const pos = entry.geometry.getAttribute("position").array;
  const map = entry.faceMap;
  let x = 0, y = 0, z = 0, n = 0;
  for (let t = 0; t < map.length; t++) {
    if (map[t] !== faceIndex) continue;
    for (let corner = 0; corner < 3; corner++) {
      const v = idx[t * 3 + corner] * 3;
      x += pos[v];
      y += pos[v + 1];
      z += pos[v + 2];
      n += 1;
    }
  }
  return n ? [x / n, y / n, z / n] : null;
}

function setGrid(size, divisions) {
  gridSize = size;
  gridDivisions = divisions;
  if (gridHelper) {
    scene.remove(gridHelper);
    gridHelper.geometry.dispose();
    gridHelper.material.dispose();
  }
  gridHelper = new THREE.GridHelper(size, divisions, sceneTheme.gridMajor, sceneTheme.gridMinor);
  gridHelper.rotation.x = Math.PI / 2; // lie in the XY plane (Z up)
  scene.add(gridHelper);
}

/** Swap the 3D palette: {background, gridMajor, gridMinor, edge}. Safe to
 *  call before init() — the colors apply when the scene is created. */
export function setTheme(colors) {
  sceneTheme = colors;
  edgeMaterial.color.set(colors.edge);
  for (const [kind, mesh] of diffOverlays) {
    mesh.material.color.set(diffColor(kind));
  }
  if (!scene) return;
  scene.background.set(colors.background);
  setGrid(gridSize, gridDivisions);
}

function pick(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1
  );
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(ndc, camera);
  const meshes = [];
  contentGroup.traverse((obj) => {
    if (obj.isMesh) meshes.push(obj);
  });
  const hits = raycaster.intersectObjects(meshes, false);
  if (!hits.length) return null;
  const hit = hits[0];
  const ud = hit.object.userData;
  // Instanced assembly mode: a hit carries the local `instanceId`; map it back
  // to the expanded assembly id via this mesh's id table (FR8). No B-rep face
  // map for a proxy/instanced mesh — a selected instance re-renders full-res.
  if (ud.instanced && hit.instanceId != null) {
    return {
      instanceId: ud.instanceIds[hit.instanceId] ?? null,
      partId: ud.partId ?? null,
      faceIndex: null,
    };
  }
  // Part mode: map the hit triangle to its B-rep face via the cache entry's
  // faceMap (set lazily from the mesh's .faces.u32 sidecar). null when the
  // map isn't loaded (LOD tier on stage, reference part, stale cache).
  let faceIndex = null;
  if (ud.geomKey && hit.faceIndex != null) {
    const entry = geomCache.get(ud.geomKey);
    if (entry && entry.faceMap && hit.faceIndex < entry.faceMap.length) {
      faceIndex = entry.faceMap[hit.faceIndex];
    }
  }
  return {
    instanceId: ud.instanceId ?? null,
    partId: ud.partId ?? null,
    faceIndex,
  };
}

/** Attach the triangle->B-rep-face map (Uint32Array, one entry per triangle)
 *  to a cached geometry. `key` is the same geometry key showPart received. */
export function setFaceMap(partId, key, faceMap) {
  const entry = geomCache.get(`${partId}:${key}`);
  if (entry) entry.faceMap = faceMap;
}

function makeMaterial(color) {
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color(color),
    metalness: 0.1,
    roughness: 0.8,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
  });
}

function clearContent() {
  // The gizmo points at a group we are about to destroy — release it first so
  // it never renders against a detached object (main.js re-attaches after the
  // new content is built).
  detachGizmoInternal();
  clearFaceHighlight(); // overlay indexes into geometry we may drop
  clearDiffOverlay(); // a diff volume describes the part that is leaving
  for (const child of [...contentGroup.children]) {
    contentGroup.remove(child);
    child.traverse((obj) => {
      if (obj.isMesh && obj.material) obj.material.dispose();
    });
  }
  current = { mode: null, partId: null, key: null, items: [] };
  displayedKeys = new Set();
}

export function clear() {
  clearContent();
}

export function hasContent() {
  return contentGroup.children.length > 0;
}

/** Number of triangles currently displayed. */
export function triangleCount() {
  let n = 0;
  contentGroup.traverse((obj) => {
    if (obj.isMesh && obj.geometry.index) n += obj.geometry.index.count / 3;
  });
  return n;
}

// ------------------------------------------------------------- rendering

/** Single-part mode: the part at the origin. Returns true if the scene
 *  content actually changed (caller decides whether to fit). */
export function showPart(partId, buffer, key, color = DEFAULT_PART_COLOR) {
  if (current.mode === "part" && current.partId === partId && current.key === key) {
    return false;
  }
  const entry = getGeometry(partId, key, buffer);
  clearContent();
  const group = new THREE.Group();
  const mesh = new THREE.Mesh(entry.geometry, makeMaterial(color));
  mesh.userData = { partId, instanceId: null, geomKey: `${partId}:${key}` };
  group.add(mesh);
  group.add(new THREE.LineSegments(entry.edges, edgeMaterial));
  contentGroup.add(group);
  current = { mode: "part", partId, key, items: [] };
  displayedKeys = new Set([`${partId}:${key}`]);
  fitGridToContent();
  return true;
}

/** Assembly mode. items: [{instanceId, partId, buffer, key, position,
 *  rotationDeg, color}] — rotation applied as intrinsic XYZ Euler. */
export function showAssembly(items) {
  clearContent();
  displayedKeys = new Set(items.map((i) => `${i.partId}:${i.key}`));
  for (const item of items) {
    const entry = getGeometry(item.partId, item.key, item.buffer);
    const group = new THREE.Group();
    const mesh = new THREE.Mesh(entry.geometry, makeMaterial(item.color));
    mesh.userData = { partId: item.partId, instanceId: item.instanceId };
    group.add(mesh);
    group.add(new THREE.LineSegments(entry.edges, edgeMaterial));
    const [rx, ry, rz] = item.rotationDeg || [0, 0, 0];
    group.rotation.set(rx * DEG, ry * DEG, rz * DEG, "XYZ");
    const [x, y, z] = item.position || [0, 0, 0];
    group.position.set(x, y, z);
    group.userData.instanceId = item.instanceId; // gizmo target lookup
    contentGroup.add(group);
  }
  current = { mode: "assembly", partId: null, key: null, items };
  applySelection();
  fitGridToContent();
}

/** Instanced assembly mode (PRD-013 FR8): ONE geometry upload per (part,
 *  rep-tier) group, N transforms via THREE.InstancedMesh. Used for the
 *  "Simplified" rep-mode and at scale (1000s of members). Picking maps a hit's
 *  `instanceId` back to the expanded assembly id via the per-mesh id table, so
 *  the click-select contract is preserved. Selection/gizmo editing stays in the
 *  per-mesh `showAssembly` path (Full mode); this path is for display + pick.
 *  items: [{instanceId, partId, buffer, key, position, rotationDeg, color}]. */
export function showAssemblyInstanced(items) {
  clearContent();
  const groups = groupInstances(items);
  displayedKeys = new Set(groups.map((g) => `${g.partId}:${g.key}`));
  const dummy = new THREE.Object3D();
  groups.forEach((group) => {
    const first = group.members[0];
    const entry = getGeometry(group.partId, group.key, first.buffer);
    const material = makeMaterial(DEFAULT_PART_COLOR);
    const mesh = new THREE.InstancedMesh(
      entry.geometry, material, group.members.length);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    group.members.forEach((m, li) => {
      const [rx, ry, rz] = m.rotationDeg || [0, 0, 0];
      dummy.rotation.set(rx * DEG, ry * DEG, rz * DEG, "XYZ");
      const [x, y, z] = m.position || [0, 0, 0];
      dummy.position.set(x, y, z);
      dummy.updateMatrix();
      mesh.setMatrixAt(li, dummy.matrix);
      if (m.color) mesh.setColorAt(li, new THREE.Color(m.color));
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    // The id table: local instanceId -> expanded assembly id.
    mesh.userData = {
      instanced: true,
      partId: group.partId,
      instanceIds: group.members.map((m) => m.instanceId),
    };
    contentGroup.add(mesh);
  });
  current = { mode: "assembly", partId: null, key: null, items };
  fitGridToContent();
}

/** HUD counts for the current assembly items: total instances + distinct
 *  geometry uploads. */
export function assemblyCounts(items) {
  return instanceCounts(items || current.items || []);
}

export function setSelectedInstance(instanceId) {
  selectedInstanceId = instanceId;
  applySelection();
}

// -------------------------------------------------------- face highlight

// Least-invasive overlay: a separate non-indexed mesh in the scene root
// holding copies of just the picked face's triangles, tinted accent amber.
// Cleared on null, on re-render (clearContent), and on part switches.
let faceHighlightMesh = null;

function clearFaceHighlight() {
  if (!faceHighlightMesh) return;
  scene.remove(faceHighlightMesh);
  faceHighlightMesh.geometry.dispose();
  faceHighlightMesh.material.dispose();
  faceHighlightMesh = null;
}

/** Tint one B-rep face of the displayed part (part mode only). Pass a null
 *  faceIndex to clear. No-op when the part/map isn't on stage. */
export function highlightFace(partId, faceIndex) {
  clearFaceHighlight();
  if (faceIndex == null) return;
  if (current.mode !== "part" || current.partId !== partId) return;
  const entry = geomCache.get(`${partId}:${current.key}`);
  if (!entry || !entry.faceMap || !entry.geometry.index) return;
  const idx = entry.geometry.index.array;
  const pos = entry.geometry.getAttribute("position").array;
  const map = entry.faceMap;
  const verts = [];
  for (let t = 0; t < map.length; t++) {
    if (map[t] !== faceIndex) continue;
    for (let corner = 0; corner < 3; corner++) {
      const v = idx[t * 3 + corner] * 3;
      verts.push(pos[v], pos[v + 1], pos[v + 2]);
    }
  }
  if (!verts.length) return;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(verts), 3));
  const mat = new THREE.MeshBasicMaterial({
    color: 0xe8b06a,
    transparent: true,
    opacity: 0.35,
    depthWrite: false,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
  });
  faceHighlightMesh = new THREE.Mesh(geo, mat);
  faceHighlightMesh.renderOrder = 2;
  scene.add(faceHighlightMesh);
}

// ---------------------------------------------------- proposal diff overlay

// PRD-002's geometry diff: the added/removed volumes a proposal computes,
// drawn translucent OVER the part that is already on stage. Built exactly like
// the face highlight above — a separate mesh parented to the scene ROOT, not
// contentGroup, so it never joins the pick set, never inherits a material and
// has its own dispose path. clearContent() drops it, so a part switch or a
// rebuild clears it for free.
const diffOverlays = new Map(); // "added" | "removed" -> THREE.Mesh

function diffColor(kind) {
  return kind === "added"
    ? sceneTheme.diffAdded ?? 0x6fbf8f
    : sceneTheme.diffRemoved ?? 0xe0655c;
}

/** Drop every diff overlay (or just one `kind`). */
export function clearDiffOverlay(kind) {
  for (const [name, mesh] of [...diffOverlays]) {
    if (kind && name !== kind) continue;
    if (scene) scene.remove(mesh);
    mesh.geometry.dispose();
    mesh.material.dispose();
    diffOverlays.delete(name);
  }
}

/** Overlay one ACM1 diff volume on the displayed part. `kind` is "added"
 *  (green) or "removed" (red); `key` identifies the buffer so a repeat call
 *  with the same geometry is a no-op. Returns false when that part is not the
 *  one on stage (part mode only) — the caller selects it first. */
export function showDiffOverlay(partId, buffer, key, kind) {
  if (current.mode !== "part" || current.partId !== partId) return false;
  const stamp = `${partId}:${key}:${kind}`;
  const existing = diffOverlays.get(kind);
  if (existing && existing.userData.diffStamp === stamp) return true;
  clearDiffOverlay(kind);
  const parsed = parseACM(buffer);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(parsed.positions, 3));
  geo.setAttribute("normal", new THREE.BufferAttribute(parsed.normals, 3));
  geo.setIndex(new THREE.BufferAttribute(parsed.indices, 1));
  const mat = new THREE.MeshBasicMaterial({
    color: diffColor(kind),
    transparent: true,
    opacity: 0.45,
    depthWrite: false,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.userData = { diffStamp: stamp };
  mesh.renderOrder = 3;
  diffOverlays.set(kind, mesh);
  scene.add(mesh);
  return true;
}

function applySelection() {
  contentGroup.traverse((obj) => {
    if (!obj.isMesh) return;
    const selected =
      selectedInstanceId != null && obj.userData.instanceId === selectedInstanceId;
    obj.material.emissive.set(selected ? 0xd99a4e : 0x000000);
    obj.material.emissiveIntensity = selected ? 0.22 : 0;
  });
}

// ------------------------------------------------------------------- fit

function contentBox() {
  const box = new THREE.Box3();
  let any = false;
  contentGroup.traverse((obj) => {
    if (obj.isMesh) {
      obj.updateWorldMatrix(true, false);
      if (!obj.geometry.boundingBox) obj.geometry.computeBoundingBox();
      const b = obj.geometry.boundingBox.clone();
      b.applyMatrix4(obj.matrixWorld);
      box.union(b);
      any = true;
    }
  });
  return any ? box : null;
}

function fitGridToContent() {
  const box = contentBox();
  if (!box) return;
  const span = Math.max(
    box.max.x - box.min.x,
    box.max.y - box.min.y,
    Math.abs(box.max.x), Math.abs(box.min.x),
    Math.abs(box.max.y), Math.abs(box.min.y),
    40
  );
  // grid covers ~3x the content span with cells in a 1/2/5 decade
  const target = span * 3;
  const decade = Math.pow(10, Math.floor(Math.log10(target / 10)));
  let cell = decade;
  for (const m of [1, 2, 5, 10]) {
    if (decade * m * 10 >= target) { cell = decade * m; break; }
  }
  const size = cell * 10 * 2;
  setGrid(size, Math.round(size / cell));
}

export function fit() {
  const box = contentBox();
  if (!box) return;
  const center = box.getCenter(new THREE.Vector3());
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const radius = Math.max(sphere.radius, 1);
  const dist = (radius / Math.sin((camera.fov * DEG) / 2)) * 1.15;

  // keep the current viewing direction
  const dir = camera.position.clone().sub(controls.target);
  if (dir.lengthSq() < 1e-6) dir.set(1, -1, 0.8);
  dir.normalize();

  controls.target.copy(center);
  camera.position.copy(center).addScaledVector(dir, dist);
  camera.near = Math.max(dist / 1000, 0.01);
  camera.far = dist * 100;
  camera.updateProjectionMatrix();
  controls.update();
}

// ---------------------------------------------------------------- gizmo

function ensureGizmo() {
  if (gizmo) return gizmo;
  gizmo = new TransformControls(camera, renderer.domElement);
  gizmo.setSize(0.9);
  gizmo.setSpace("local");
  scene.add(gizmo.getHelper()); // r169+ API: the helper is a separate object
  // Orbiting must not fight a drag.
  gizmo.addEventListener("dragging-changed", (e) => {
    controls.enabled = !e.value;
  });
  gizmo.addEventListener("mouseDown", () => {
    gizmoInteracting = true;
    gizmoDragged = false;
  });
  gizmo.addEventListener("objectChange", () => {
    gizmoDragged = true;
    if (gizmoCallbacks.onLive) gizmoCallbacks.onLive(readTransform(gizmo.object));
  });
  gizmo.addEventListener("mouseUp", () => {
    // A press without motion is not an edit — don't write back the same pose.
    if (gizmoDragged && gizmoCallbacks.onCommit) {
      gizmoCallbacks.onCommit(readTransform(gizmo.object));
    }
    gizmoDragged = false;
  });
  return gizmo;
}

/** Object transform -> {position:[x,y,z], rotationDeg:[rx,ry,rz]} in the same
 *  intrinsic-XYZ-degrees convention the assembly PATCH expects. */
function readTransform(obj) {
  const p = obj.position;
  const e = obj.rotation; // Euler, order "XYZ" (set when the group was built)
  return {
    position: [p.x, p.y, p.z],
    rotationDeg: [e.x / DEG, e.y / DEG, e.z / DEG],
  };
}

function groupForInstance(instanceId) {
  for (const child of contentGroup.children) {
    if (child.userData && child.userData.instanceId === instanceId) return child;
  }
  return null;
}

/** Directly set one assembly instance group's transform (used by the motion
 *  sweep animation). position [x,y,z] mm; rotationDeg intrinsic XYZ Euler
 *  degrees — the same convention showAssembly applies. Returns false when the
 *  instance has no group on stage (e.g. the assembly was re-rendered). */
export function setInstanceTransform(instanceId, position, rotationDeg) {
  const group = groupForInstance(instanceId);
  if (!group) return false;
  const [rx, ry, rz] = rotationDeg || [0, 0, 0];
  group.rotation.set(rx * DEG, ry * DEG, rz * DEG, "XYZ");
  const [x, y, z] = position || [0, 0, 0];
  group.position.set(x, y, z);
  return true;
}

function detachGizmoInternal() {
  if (gizmo) gizmo.detach();
  attachedInstanceId = null;
  gizmoInteracting = false;
  gizmoDragged = false;
}

/** Attach the move/rotate gizmo to the group for `instanceId`. Pass a null id
 *  (or an id with no group on stage) to detach. opts: {mode, snap, onCommit,
 *  onLive}. Returns true when a gizmo is attached. */
export function setGizmo(instanceId, opts = {}) {
  if (!instanceId) {
    if (gizmo) gizmo.detach();
    attachedInstanceId = null;
    gizmoCallbacks = {};
    return false;
  }
  const group = groupForInstance(instanceId);
  if (!group) {
    if (gizmo) gizmo.detach();
    attachedInstanceId = null;
    return false;
  }
  ensureGizmo();
  gizmoCallbacks = { onCommit: opts.onCommit, onLive: opts.onLive };
  gizmo.setMode(opts.mode === "rotate" ? "rotate" : "translate");
  setGizmoSnap(!!opts.snap);
  gizmo.attach(group);
  attachedInstanceId = instanceId;
  return true;
}

export function setGizmoMode(mode) {
  if (gizmo && attachedInstanceId) gizmo.setMode(mode === "rotate" ? "rotate" : "translate");
}

export function setGizmoSnap(on) {
  if (!gizmo) return;
  gizmo.setTranslationSnap(on ? SNAP_TRANSLATE_MM : null);
  gizmo.setRotationSnap(on ? SNAP_ROTATE_DEG * DEG : null);
}

export function hasGizmo() {
  return !!(gizmo && attachedInstanceId);
}
