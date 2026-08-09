// Three.js viewport: parses the ACM1 mesh binary, renders shaded geometry
// with subtle B-rep edge overlays, Z-up CAD orientation, orbit controls,
// fit-to-bbox, and raycast picking in assembly mode.

import * as THREE from "three";
import { OrbitControls } from "/vendor/OrbitControls.js";
import { TransformControls } from "/vendor/TransformControls.js";

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

// geometry cache: `${partId}:${meshKey}` -> {geometry, edges}. main.js passes
// meshKey as `${cacheKey}:${lod}` so a coarse LOD tier and the full-resolution
// mesh of the same build never collide in the cache.
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

const edgeMaterial = new THREE.LineBasicMaterial({
  color: 0x0d0e10,
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
  scene.background = new THREE.Color(0x17181b);

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

  const ro = new ResizeObserver(() => {
    const w = container.clientWidth;
    const h = Math.max(1, container.clientHeight);
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
    onPickCallback(hit);
  });

  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
  });
}

function setGrid(size, divisions) {
  if (gridHelper) {
    scene.remove(gridHelper);
    gridHelper.geometry.dispose();
    gridHelper.material.dispose();
  }
  gridHelper = new THREE.GridHelper(size, divisions, 0x2c2f36, 0x22242a);
  gridHelper.rotation.x = Math.PI / 2; // lie in the XY plane (Z up)
  scene.add(gridHelper);
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
  const ud = hits[0].object.userData;
  return { instanceId: ud.instanceId ?? null, partId: ud.partId ?? null };
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
  mesh.userData = { partId, instanceId: null };
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

export function setSelectedInstance(instanceId) {
  selectedInstanceId = instanceId;
  applySelection();
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
