# PRD-013 Assembly v2 — design spec (structure, scale, richer joints, URDF)

- **PRD:** `docs/prd/pending/PRD-013-assembly-v2.md`
- **Branch:** `prd-013-assembly-v2`
- **Date:** 2026-08-19
- **Scope of this spec:** the PRD **MVP** — patterns (FR5–FR6), sub-assembly
  instancing with rigid placement + interface mating (FR1–FR4, tested to two
  nesting levels), slider + planar joints (FR10–FR11), `simplified_rep` +
  instanced rendering (FR7–FR8), `export_urdf` for fixed/revolute/prismatic
  (FR14 core). Phase 2/3 seams are **designed, not built** (§13).

This is a *large* PRD. The design keeps to the extension-point contract: new
tool packs (`tools_structure.py`, `tools_urdf.py`), a route pack
(`routes_structure.py`), a worker handler pack (`handlers/simplify.py`), the
existing connectors handler/resolver grown in place (a pack), and OCP-free
`core/` modules (`core/urdf.py`, expansion logic in `core/mates.py`). No edit to
`worker.py` / `tools.py` / `app.py` / `service.py` cores.

Everything below is grounded in current code (file:line). The load-bearing
seams:

- `AgentCADService._resolved_instances` — `agentcad/core/service.py:173-187`;
  consumers: `get_assembly` (519), `check_interference` (611),
  `export_assembly` (636), `specs.py:1069,1141`, `merge.py:640`,
  `checks.py:1707`, `tools_stackup.py:128`, `tools_vision.py:54`,
  `packet.py:1041`.
- `core/mates.py:29-92` `resolve()` — server seam that marshals to the worker.
- `agentcad/kernel/_mates_resolver.py` — `order_mates` (129-190),
  `resolve_mates` (211-322), `eval_connectors` (84-124), `_make_joint`
  (195-208). `VALID_TYPES = ("rigid","revolute","cylindrical")` (49).
- `handlers/connectors.py` (the pack) → the resolver.
- `InstanceSpec` — `agentcad/core/model.py:192-217`; `to_manifest` 204-217.
- `ProjectStore.set_instances` — `project.py:338-388`; `instances()` 320-336;
  `_read_manifest` 452-467; `open()` 104-116; `write_guard` 52-60, fired at
  186-187 / 396-397 / 422-423 only.
- `manifest_merge.py` — `_merge_assembly` 171-187, `_merge_entry` 222-247,
  `_write_path` 405-426, `config_problems` 573-671.
- LOD: `service.py:40-49` (constants), `mesh_info` 439-466 (443-465 tier
  probe), build request 967-970; worker `_write_lod_tiers` 407-438; ACM
  `kernel/acm.py`; mesh route `app.py:297-311` + by-key `routes_configs.py:198-220`.
- Inertia: `tools_analysis.py:10-43` → worker `_inertia`
  `handlers/analysis.py:152-166` → `inertia_tensor_g_mm2` **about the global
  origin** (note at :165).
- Pool affinity: `kernel/pool.py:56-66`; prefixed-namespace precedent
  `share_build.py:614`.

---

## 1. Manifest schema bump (Decision 1)

### 1.1 New fields (additive, old files load)

**Instance entry** (`assembly.instances[]`) gains three optional keys, joining
the existing `{id, part, position, rotation_deg, color?, mate?, config?}`:

```jsonc
{
  "id": "bolt", "part": "m6_bolt",
  "position": [40,0,0], "rotation_deg": [0,0,0],
  "pattern":  { "kind": "polar", "count": 8, "angle_step_deg": 45,
                "axis": [[0,0,0],[0,0,1]], "center": [0,0,0] },   // FR5
  "assembly": { "project": "engine", "version": null, "config": null }, // FR1
  "config":   "m"                                                  // PRD-012, unchanged
}
```

- `pattern`: `{kind: "linear"|"polar", count:int≥1, step_mm?, angle_step_deg?,
  axis?, center?}`. `axis` is `[[px,py,pz],[dx,dy,dz]]` (matches the connector
  `((point),(direction))` grammar, `_mates_resolver._to_axis`); defaults +X
  (linear) / +Z through origin (polar).
- `assembly`: `{project, version?, config?}`. `project` is a known project name
  or absolute path; `version` reserved for a PRD-001 tag (Phase 3 — MVP resolves
  the source's **current state** and warns when unpinned, per the PRD risk
  note); `config` reserved for PRD-012 config pinning (Phase 3).
- An instance carries **at most one of** `part`, `pattern`+`part`, or
  `assembly`. `pattern` decorates a part instance (repeat a part). `assembly`
  makes the instance a sub-assembly reference (no `part`). A `pattern` on an
  `assembly` instance is allowed (repeat a sub-assembly) and expands the same
  way. Validated in `ProjectStore.set_instances` (§5.1).

**Project entry** (`manifest["assembly"]`) gains two optional maps beside
`instances`:

```jsonc
"assembly": {
  "instances": [ ... ],
  "interface": { "mount": {"instance": "block", "connector": "crank_axis"} },  // FR3
  "couplings": { "gearA": {"a_instance":"gearA","a_connector":"axis",
                           "b_instance":"gearB","b_connector":"axis",
                           "ratio": 2.0} }                                     // FR12 (Phase 2 semantics)
}
```

`interface` is a map `name → {instance, connector}`. `couplings` is a map keyed
by the driven instance id (matches `clear_coupling {a_instance}`). Couplings are
**declared** in MVP (schema + validation) but only *resolved* in Phase 2 — MVP
stores them and `export_urdf` ignores them (fixed+warning). This lets the schema
and merge behaviour land once.

### 1.2 Old-file-loads guarantee

`_read_manifest` (`project.py:452-467`) already `setdefault`s `assembly` to
`{"instances": []}` and never requires `interface`/`couplings`. `InstanceSpec`
is built with `.get(...)` tolerance (`project.py:320-336`) and `to_manifest`
omits falsy optionals (`model.py:204-217`). A v1 file therefore loads unchanged;
the new keys are simply absent. **No migration, no schema-version gate**
(`SCHEMA_VERSION` stays 2; it is written-and-`setdefault`ed but never branched
on — same discipline as PRD-012, AGENTS "zero cost when unused"). AC8 (byte
identity for a project with no v2 structure) is graded against this.

### 1.3 How `manifest_merge` treats each new key

Grounded in `manifest_merge.py`:

| New key | Merge behaviour | Code change? |
|---|---|---|
| `instances[].pattern` | Instance entry is keyed by `id` (`_by_id`), each field a **whole value** (`_merge_entry`, `subdicts=()`, `entry_dicts={}` at 179-182). `pattern` merges whole-value per instance. Two branches editing the same pattern's `count` differently → one conflict on the `pattern` sub-object. | **None** |
| `instances[].assembly` | Same — whole-value per instance. | **None** |
| `assembly.interface` | Currently `_merge_assembly` (183-184) merges every non-`instances` sub-key **atomically (whole map)**. The PRD wants key-wise. | **Small** (§1.4) |
| `assembly.couplings` | Same as interface. | **Small** (§1.4) |

### 1.4 The one merge-code change: per-name interface/couplings

Extend `_merge_assembly` so `interface` and `couplings` merge **per name**, each
entry **atomic** — exactly the `_ENTRY_DICTS` pattern already used for
`materials`/`packages` (an interface export or a coupling is a small
content-determined record; merging one side's `instance` with another's
`connector` is a record nobody authored, so per-name-atomic is correct):

- Add `_ASSEMBLY_ENTRY_DICTS = ("interface", "couplings")`. In `_merge_assembly`,
  route those sub-keys through `_merge_entry_dict((("assembly", sub)), ...)`
  (the existing atomic-per-name helper) instead of `_merge_atomic`.
- In `_write_path` (405-426), add a branch for
  `segs[1] in _ASSEMBLY_ENTRY_DICTS and len(segs) == 3` →
  `_write_slot(assembly.setdefault(segs[1], {}), segs[2], value, present)`.
  Without it a resolution of `assembly.interface.<name>` would fall through to
  the final `_write_slot(manifest, ".".join(segs), …)` and write a bogus flat
  key — the exact failure the module's docstring warns about.

Two branches adding **different** interface names (or couplings) then merge
clean (FR12-style), and a delete/modify of the **same** name conflicts on that
whole record.

### 1.5 Post-merge referential integrity (a `config_problems` sibling)

`manifest_merge` deliberately does not do cross-key referential checks; PRD-001's
`merge._integrity` + PRD-012's `config_problems` are the backstop. Add a small
pure `structure_problems(manifest)` (in `manifest_merge.py`, called from
`merge._integrity`'s site like `config_problems`/`package_problems`) reporting,
per the PRD-012 blocking/warning split:

- `dangling_interface` (**warning**): an `interface` export naming an instance or
  connector that the merged project no longer has → resolves to nothing but the
  project loads.
- `dangling_coupling` (**warning**): a coupling naming a missing instance.
- `pattern_conflict` is not a thing — a pattern is one instance field, covered by
  the ordinary instance merge.

Cross-project sub-assembly cycles are **not** a merge concern (the merge sees one
project's manifest); they are caught at resolution time (§3.3).

### 1.6 Proposal diff renders structure as reviewable text

This is a selling point (competitive refs). A pattern or sub-assembly change is
one instance-entry change in `project.json`. `packet.py`'s assembly delta
(`packet.py:1041` reads `get_assembly` at resolved transforms; the manifest text
diff comes from the two branch worktrees) shows:

- **Pattern edit** → the `pattern.count` field changes `6 → 8` on one instance
  line: a one-line, human-legible diff. Because expansion is a single downstream
  point (§2), the packet's *resolved* assembly (mass, interference, bbox)
  reflects 8 members automatically — the text says "count 8", the geometry
  evidence says "8 bodies".
- **Sub-assembly add** → a new instance line with an `assembly: {project: …}`
  field. The resolved side flattens it; the text side names the source project.

No new packet machinery is required — the delta already diffs instance entries
by id and reads resolved transforms through the ordinary service path.

---

## 2. Pattern expansion — the single expansion point (Decision 2)

### 2.1 Where expansion happens

A new pure-server function `mates.expand(service, proj, instances) →
(flat_items, warnings)` produces the **flattened concrete instance list**:
patterns expanded to N members, sub-assemblies (§3) resolved to their members.
It is OCP-free (graph work + cross-project reads + it delegates all transform
*geometry* to the kernel — §2.3).

`_resolved_instances` becomes: **expand, then mate-resolve.**

```
def _resolved_instances(proj, timeout_s=None):
    instances = store.instances(proj)
    if not any(i.mate or i.pattern or i.assembly for i in instances):
        return instances                      # byte-identical v1 fast path (AC8)
    flat, warnings = mates.expand(self, proj, instances)
    return mates.resolve(self, proj, flat, timeout_s=timeout_s)  # mate pass on the flat list
```

- The trigger (`service.py:181`) grows from "any mate" to "any mate **or**
  pattern **or** assembly". A flat single-level project with no patterns/
  sub-assemblies/mates still short-circuits → **byte-identical** to today (AC8).
- **Every** consumer already routes through `_resolved_instances` (the 10 call
  sites above), so mass roll-ups, interference pairs, export, stackup, specs,
  checks and the packet all see N members from **one** expansion — never N
  places that each re-expand.
- The one consumer that does *not* use `_resolved_instances` is `sweep_motion`
  (`tools_motion.py:48` reads raw `store.instances`, because the kernel
  re-resolves the mate graph per sample). It calls `mates.expand` (only the
  expansion step, not the mate pass) and feeds the flat items to `motion_sweep`
  — same single expansion implementation, second entry point. (§5.4)

### 2.2 Determinism and the replace-not-add invariant

- Member ids: `<id>[0] … <id>[count-1]` (FR5). Deterministic, index-ordered.
- Each member **inherits** `part`, `color`, `config`, and the base's `mate`
  template.
- **Expansion replaces the base instance with its members** — the base id never
  appears in the flat list, and a member is never itself re-expanded. This is
  the invariant that prevents the sharpest double-count/under-count risk: mass =
  Σ members, and the base is not also counted. Stated as a test (§ plan slice 1):
  `len(flat) == Σ count` for a project of patterns, and the base ids are absent.
- Polar `center` and per-member frames follow PRD-010's measured convention
  (`patterns.polar` skips no index; a member's `center` is the rigid image of one
  reference point — AGENTS PRD-010 gotchas) so a correct polar pattern is not
  reported broken.

### 2.3 Transform composition stays in ONE rotation convention

Rotations are intrinsic-XYZ Euler degrees **everywhere** (AGENTS hard gotcha).
To avoid a second Euler implementation server-side, **the geometry of expansion
is composed in the kernel via build123d `Location`** — the same authority the
mate resolver already uses. `mates.expand` builds a flat *item* list carrying,
per member, the pattern operator to apply (`{kind, index, step/angle, axis,
center}`); the worker composes `member_world = pattern_op(index) ∘
base_placement` with `Location` and returns concrete `position`/`rotation_deg`,
exactly as `resolve_mates` already returns transforms.

- The worker expands **without building shapes** unless a connector/mate is
  involved: a 1 000-instance unmated bolt pattern composes 1 000 `Location`s (µs
  each) and builds the *mesh* once through the normal cache path. No 1 000
  builds.
- Concretely: extend the connectors handler to a `resolve_assembly` method (or
  grow `resolve_mates`' payload) that accepts `pattern` operators and
  sub-assembly member items alongside mates, and returns the full flat transform
  map. Linear patterns are pure world translation; polar patterns rotate the
  member about `axis` through `center` by `index·angle_step` — both via
  `Location`, so orientation stays in the build123d convention that
  `service._apply_transform` is round-trip-tested against.

### 2.4 Polar re-aim and the off-axis fallback (PRD risk item)

"Polar patterns re-aim the mate per member" (FR5): member i's world pose is the
polar rotation `R_i` (about the pattern axis) applied to the base's resolved
pose, so a bolt keeps pointing radially. This is always a well-defined **rigid
image**.

The PRD's caveat: re-aiming is only *the mate's intent* when the base's mate
anchor lies on the pattern axis. When the base instance **is mated** and its
anchor connector is **off** the pattern axis, we do **not** re-solve the mate per
member (there is only one anchor connector); we apply the rigid polar image and
emit a warning `pattern_polar_offaxis {instance}` in the resolved-assembly
warnings. When the base is unmated, or the anchor is on-axis, no warning. This
matches "off-axis cases fall back to rigid transforms with a warning — document
it."

---

## 3. Sub-assembly resolution (Decision 3)

### 3.1 Depth-first resolution of the source, then rigid placement

For an instance `{assembly: {project, …}, position/rotation_deg or mate}`,
`mates.expand` (server, OCP-free) does:

1. **Open the source read-only.** `name = service.store.open(path_or_name)`
   (`project.py:104-116`) registers the external project; it installs **no**
   write hook. Only read accessors are used thereafter (`manifest`,
   `instances`, `get_part`, `read_script`, `cache_dir`, `mesh_info`).
2. **Recurse** — resolve the source's own patterns + sub-assemblies + internal
   mates (a depth-first `_resolved_instances`-equivalent on the source), yielding
   the source's members as concrete items in the **source's local frame**.
3. **Interface** (§3.2) — expose only the source's exported connectors.
4. **Rigid placement** — each source member's world = `parent_placement ∘
   member_local`, where `parent_placement` is the parent instance's explicit
   transform, or its mate-resolved transform when the parent instance is mated
   (via its interface connector, §3.2). Member ids are namespaced
   `<parent_id>/<member_local_id>`, so two nesting levels read
   `stand/engine/piston[3]` (FR4, ≥2 levels). Placement composition is done in
   the kernel `Location` pass (§2.3), one convention.

### 3.2 Interface: only exported connectors are matable from outside

- The source declares `assembly.interface {name → {instance, connector}}` (FR3),
  set via `set_assembly_interface`.
- When the parent instance mates the sub-assembly (`mate.connector` /
  `mate.to_connector` naming an **interface name**), `expand` resolves that name
  to the internal `(instance, connector)`, computes that connector's frame in the
  sub-assembly's local coordinates (the named member's connector, transformed by
  that member's internal placement), and presents it as a **rigid** connector on
  the sub-assembly unit — fitting the existing "moving side must be rigid" rule
  (`_mates_resolver.py:261-266`).
- A mate that names a connector **not** in the source's `interface` →
  `ValidationError` with `details.interface` (FR3, agent surface). Internal,
  non-exported connectors are unreachable from outside by construction.

### 3.3 Cross-project cycle detection

`expand` threads a **project-ref stack** through the recursion. Before opening a
source, if its resolved identity (absolute project path) is already on the
stack, raise `ValidationError("assembly cycle: A -> B -> A", {"cycle": [...]})`
(FR2, `details.cycle`) — mirroring the intra-project mate cycle payload
(`_mates_resolver.order_mates:174-180`). Detection is a pure server-side walk
over `assembly` refs (reading each source manifest's instances); no geometry, no
kernel. A→B→A and deeper cycles are named by their path.

### 3.4 The cross-project read-only safety property (sharpest invariant)

**`write_guard` never fires against a source, and it is structurally
guaranteed** — not a runtime check that could regress:

- `write_guard` is invoked **only** inside `ProjectStore.write_script`
  (`project.py:186-187`), `save_manifest` (396-397), and `imports_dir(write=True)`
  (422-423). Resolution calls **none** of these on a source — only read
  accessors, which `project.py` documents as unguarded (`cache_dir` 400-405,
  `manifest` 392-393, read-path `imports_dir` "Reads stay unguarded" 419).
- The guard is keyed by the `proj` argument of a *mutation*; a source that is
  only read is never that argument. So it is unreachable, not merely
  not-triggered.
- Precedent for opening a project read-only and neutralizing write seams:
  `checks.py:826-837` opens a linked worktree behind an ephemeral service with
  `bus.on_publish=None` and a nulled `branch_resolver`. Sub-assembly resolution
  needs even less — it never opens a *worktree*, only reads authored + cached
  state — but the design **asserts** (a test, plan slice 2) that resolving a
  sub-assembly issues zero `save_manifest`/`write_script` calls against the
  source (spy on the store). The one write that *can* happen is a **derived,
  content-addressed** `.cache/<key>.acm` when a source part is built for its mesh
  — identical to any read-triggered build (`get_metrics` on that source), never
  authored state, and never through `write_guard`. Stated explicitly so "opened
  read-only" is not over-claimed.

### 3.5 Kernel affinity includes the source project

Source-part builds route with `affinity=f"src:{source_name}:{part_id}"`
(the prefixed-namespace precedent, `share_build.py:614`), so a sub-assembly's
parts land on warm workers keyed by source and reuse across repeated
sub-assembly instances. `hash(affinity)` is `PYTHONHASHSEED`-randomised
(`pool._pick:58`), so this is a warmth optimization, never a correctness
assumption — the content-addressed cache is the correctness layer.

---

## 4. `simplified_rep` — proxy meshes as a new ACM tier (Decision 4)

### 4.1 A new tier alongside LOD, not a re-tessellation

`simplified_rep` is a **new build kind**, not a coarser LOD tolerance:

- `convex` — the convex hull of the full tessellation's vertices via
  `scipy.spatial.ConvexHull` (scipy>=1.14 already a dep, `pyproject.toml:22`;
  first `ConvexHull` use). One tiny mesh per part (dozens of triangles),
  deterministic, and cheap to instance.
- `decimated` — a coarse re-tessellation (large deflection) preserving rough
  shape. Secondary; used when a hull is a poor proxy (thin/hollow parts).

MVP ships `convex` as the default `simplified_rep` mode. `decimated` is a mode
flag on the same handler.

### 4.2 Handler + caching + serving

- New worker pack `handlers/simplify.py` exposing a `simplify_rep` method:
  input `{script, params, mode}`; it builds the shape (through the toolbox
  `build_shape`), tessellates, computes the hull/decimation, packs an ACM1
  buffer (`kernel/acm.py:pack`), and `atomic_write`s the sidecar
  `<key>.simplified.acm` (tier suffix `simplified`, which matches
  `_LOD_SUFFIX_RE = ^[a-z][a-z0-9_]{0,15}$`, `service.py:49`).
- **Why a separate handler, not `_write_lod_tiers`:** `_write_lod_tiers`
  (`worker.py:407-438`) lives in the `worker.py` **core** we may not edit, and a
  hull is not a tolerance re-tessellation. So the tier is produced by its own
  kernel call, **lazily**: `mesh_info(proj, part, lod="simplified")` probes
  `<key>.simplified.acm` (the existing tier-probe path, `service.py:462-465`);
  on a miss the service issues one `simplify_rep` call keyed by the part's
  content cache key, then serves the sidecar. Cached content-addressed, so it is
  computed **once per distinct (part, config)** — a 1 000-bolt pattern is one
  hull.
- Serving is unchanged: `GET …/meshes/{key}?lod=simplified` (by-key route
  `routes_configs.py:198-220`, `_LOD_RE` gate) and
  `GET …/parts/{id}/mesh?lod=simplified` (`app.py:297-311`, `X-Mesh-Lod`
  header). No route edit needed — the suffix already flows through.

`simplified_rep` composes *with* `lod1`: a part can have full, `lod1` (auto,
>150k tris) and `simplified` tiers side by side; the client picks per rep-mode.

### 4.3 Instanced-render path uses the tier

The viewport requests the `simplified` tier for repeated parts in "Simplified"
rep-mode (§7) and uploads **one geometry per (part, tier)**, N transforms via
`THREE.InstancedMesh` (three 0.185.1 has it, `frontend/vendor/VERSIONS.txt:1`).
The exact-geometry `full`/`lod1` path is retained for a **selected** instance.

---

## 5. Joint additions — slider + planar (Decision 5)

### 5.1 Connector declarations (`connectors(p, part)`)

`eval_connectors` (`_mates_resolver.py:84-124`) grows two `VALID_TYPES`:

- `slider` — `{type:"slider", axis:((pt),(dir)), linear_range:(a,b)}`. DOF:
  `offset_mm`. Resolves to build123d `LinearJoint(label, shape, axis,
  linear_range=…)`.
- `planar` — `{type:"planar", location:((origin),(rot)) | plane, normal?,
  u_range:(a,b), v_range:(a,b), spin?:bool}`. DOF: `{u_mm, v_mm, spin_deg}`.
  build123d has **no** PlanarJoint, so planar resolves by **composition**
  (§5.3), not a native joint.

Existing scripts are untouched — `slider`/`planar` are new opt-in types;
rigid/revolute/cylindrical unchanged. `eval_connectors` validation grows the
two type branches (axis/range coercion via existing `_to_axis`/`_to_location`).

### 5.2 `set_mate` grows a `dof` object; shorthand stays

`set_mate` (`tools_mates.py:46-66`) schema adds `dof` (object). Mapping in
`tools_mates._set_instance_mate` / the tool body (`tools_mates.py:25-41`):

| Driver | Stored `mate.params` |
|---|---|
| `angle_deg` (shorthand) | `{angle}` — unchanged |
| `offset_mm` (shorthand) | `{position}` — unchanged |
| `dof: {offset_mm}` (slider) | `{position}` |
| `dof: {u_mm, v_mm, spin_deg}` (planar) | `{u, v, spin}` |
| `dof: {rx_deg, ry_deg, rz_deg}` (ball, Phase 2) | `{rx, ry, rz}` |

Today's `angle_deg`/`offset_mm` remain as shorthand (FR11). The stored mate keeps
the existing `{connector, to_instance, to_connector, params}` shape — only the
`params` vocabulary grows, so the manifest and merge are unaffected.

### 5.3 Resolution + clamping

- `_make_joint` (`_mates_resolver.py:195-208`) grows a `slider` branch
  (`LinearJoint`). The `connect_to` dispatch (278-306) grows `slider`
  (`connect_to(child, position=…)`) and `planar`.
- **Planar** is resolved without a b3d joint: place a RigidJoint at the plane
  frame, `connect_to`, then post-multiply the child `Location` by
  `translate(u along plane x_dir, v along plane y_dir) ∘ rotate(spin about
  normal)`. All in the kernel `Location` convention.
- **Clamping (PARAMS semantics, FR11):** the resolver clamps each DOF value to
  the connector's declared range **before** calling `connect_to`, and records a
  `dof_clamped {instance, dof, requested, clamped}` warning. This is a
  **behaviour change** from today, where an out-of-range value is passed to
  build123d which **raises** (caught at `_mates_resolver.py:309-312`). Clamp is
  the PRD-mandated semantics (AC4: 80 mm on a (0,50) slider → 50 + warning). The
  divergence: any existing test asserting a *raise* on an out-of-range
  revolute/cylindrical angle would now see a clamp+warning. Mitigation: existing
  mate tests drive in-range values (verified during slice 3); the change is
  applied to all DOF joints uniformly for consistency. Flagged in §14.

### 5.4 `sweep_motion` over the new DOFs

`sweep_motion` (`tools_motion.py:19-115`) already drives one DOF as `angle`
(revolute) or `position` (cylindrical) via `motion_sweep`. It grows: (a) it feeds
`mates.expand`-flattened items to `motion_sweep` (so a swept assembly that
contains a pattern sweeps N members), and (b) the driven `param` grows to cover
slider `position` and planar `u`/`v`/`spin` (one driven DOF at a time — the
single-value contract). Coupled re-resolution (FR12) is Phase 2.

---

## 6. URDF export (Decision 6)

### 6.1 Pure server-side XML, OCP-free

`core/urdf.py` (no OCP, like `core/checks.py`) builds the robot description from
**metrics + the resolved graph** — never geometry from inside the server.
`export_urdf` (in `tools_urdf.py`) writes `exports/urdf/<name>/`:
`robot.urdf` + one mesh per link (STL default, `mesh_format` param).

### 6.2 Data sources

- Resolved graph + transforms: `_resolved_instances` (flattened, expanded).
- Per-link mass: `get_metrics(part).mass_g` → kg (`/1000`).
- Per-link inertia: `analyze_part(kind="inertia").inertia_tensor_g_mm2` →
  kg·m² (`× 1e-9`: g→kg ×1e-3, mm²→m² ×1e-6).
- **Parallel-axis shift (correctness):** the returned tensor is about the
  **global origin** (`handlers/analysis.py:165`), but URDF `<inertial>` wants the
  tensor about the link's **COM** (the `<inertial><origin>` is the COM, and the
  `<inertia>` is expressed about it). `core/urdf.py` applies
  `I_com = I_origin − m·(‖c‖²·E₃ − c·cᵀ)` (c = COM from the same analyze call),
  pure numpy. Skipping this ships a positive-inertia-but-wrong tensor for any
  off-origin part; AC6's "positive-definite / masses within 0.1%" check exercises
  it.
- Connector axes/frames for joint `<axis>` and `<origin>`: a kernel connector-
  frame query (the existing `connectors` inspection handler grown to return the
  connector's resolved `axis`/`location`, or reuse the frames already computed in
  the resolve pass).

### 6.3 Joint / element mapping table

Forest → URDF tree rooted at `world`. One mate edge → one joint (parent =
`to_instance`, child = the mated instance). Joint `<origin>` = child frame
relative to parent at the zero-DOF pose; `<axis>` = the connector axis in the
parent frame.

| Mate / instance | URDF (MVP) | URDF (Phase 2) |
|---|---|---|
| instance | `<link>` (mass + COM-shifted inertia + one mesh) | — |
| rigid mate | `fixed` | — |
| revolute mate | `revolute` w/ `<limit>` from `range`; `continuous` when unbounded | — |
| slider mate | `prismatic` w/ `<limit>` from `linear_range` | — |
| **unmated instance** | `fixed` child of `world` **+ warning** (FR15) | — |
| planar mate | `fixed` + warning (MVP) | `planar` |
| cylindrical mate | `fixed` + warning (MVP) | `prismatic` + `revolute` via a massless intermediate link |
| ball mate | — (Phase 2 joint) | `fixed` + warning |
| coupling | — | `<mimic multiplier=ratio>` |

MVP emits **fixed/revolute/prismatic** cleanly (FR14 core); planar/cylindrical/
ball/couplings degrade to `fixed` with a **named warning in the result** (never
silent — AGENTS "URDF/ball warnings in post-state, never silent"). Return:
`{path, links, joints, warnings}` (FR15).

### 6.4 AC6 golden-parse without a new dependency

There is **no** Python URDF parser in the deps, and the PRD forbids adding one.
So AC6's machine-checked half is a **hand-rolled `validate_urdf`** in
`core/urdf.py` (the house style — cf. `checks.validate_report`,
`checks.validate_record`): parse with stdlib `xml.etree.ElementTree`
(well-formedness), then structural asserts — every `<link>` has an `<inertial>`
with positive mass and a symmetric positive-definite inertia; every `<joint>`
references existing parent/child links and a valid type; the joint graph is a
connected tree rooted at `world` with no cycles; any `<mimic>` references an
existing joint. A golden `robot.urdf` fixture (the rocketry stack) is asserted
byte-stable. The "loads in urdf-viz / `check_urdf` parses" half is
**evidence-graded** (manual screenshot) — AC6 explicitly names urdf-viz as
manual. Masses-match-`get_metrics`-within-0.1% is machine-checked.

---

## 7. Frontend (Decision 8 + UI)

Grounded in `tree.js:151-199` (instance list), `viewport.js:424-444`
(`showAssembly`, one Mesh/instance, `userData.instanceId`), `viewport.js:315-346`
(`pick`, reads `userData.instanceId`), `placement.js:63-146`/`229-315` (placement
+ sweep), `main.js:251-301` (mesh fetch by key + `showAssembly`).

- **Tree (`tree.js`).** `renderInstances` grows grouped rows: a pattern is **one
  row** with a `×N` badge, expandable to its members (read from the flattened
  `get_assembly` view); a sub-assembly node expands to its resolved contents
  **read-only**, with a "open source project" affordance. Selection still flows
  through `actions.selectAssembly(id)`; a member id is `<base>[i]` /
  `<parent>/<child>`.
- **Viewport (`viewport.js`).** A `THREE.InstancedMesh` path for repeated
  (part, rep-tier): one geometry upload, N instance matrices. **Instanced
  picking:** `pick()` must read `hit.instanceId` (InstancedMesh) and map it back
  to the expanded id via a per-InstancedMesh id table, in addition to today's
  `userData.instanceId` for singletons — the existing click-select contract must
  not regress (PRD risk). A rep-mode toggle (Full / Simplified) selects the tier
  fetched; a HUD shows instance + triangle counts. An **Explode** slider is a
  Phase-2 stub in MVP (the `explode_assembly` tool is Phase 2) — the slider is
  wired but disabled with a tooltip until Phase 2 (keeps the UI seam).
- **Placement (`placement.js`).** A **pattern editor** section (linear/polar,
  count, spacing/angle) on the placement card, committing via a new
  `set_pattern` verb. Per-joint **DOF fields** (slider offset; planar u/v/spin)
  replacing the read-only mate note for slider/planar mates, committing via
  `set_mate {dof}`.

### 7.1 The 1k-instance scale AC (AC3) is evidence-graded

The Chrome extension has been unavailable for many sessions. Per the 005a/007/031a
precedent, AC3 (≥30 fps orbiting 1 000 instances) is graded as **evidence**:
the machine-checkable parts are kernel-side (a generated 1 000-instance synthetic
project resolves to exactly 1 000 members; the `simplified` tier is produced; the
InstancedMesh id-mapping is unit-tested in node against the existing
`__roundTrip__`-style export). The **fps number** is graded honestly from a
manual browser session + HUD screenshot when the extension is available, and
marked *evidence-graded, extension-gated* in the acceptance table — not a green
CI gate.

---

## 8. Pack naming + load order (Decision 7)

| New unit | Kind | Sorts at | Load-order consequence |
|---|---|---|---|
| `handlers/simplify.py` | worker handler pack | merged at worker startup (`_load_handler_packs`) | independent; adds `simplify_rep` |
| `handlers/connectors.py` (+ `_mates_resolver.py`) | existing pack, grown | — | slider/planar/expand; **coordinate with PRD-006** (§ constraints) |
| `tools_structure.py` | tool pack | after `tools_stackup`/`tools_specs`/`tools_solids`, **before** `tools_undo`/`tools_versioning` | **must not** read `service.branches` / `store.write_guard` in `register()` — versioning installs them later. Reads happen lazily in methods (§8.1). Has **no gate**, so the `tools_proposals` `gate_providers=[]` reset is irrelevant. |
| `tools_urdf.py` | tool pack | after `tools_undo`, **before** `tools_versioning` | same lazy-read rule; export reads at call time. |
| `routes_structure.py` | route pack | mounted under `/api` (the `routes_assembly2.py` name is taken by PRD-011/012) | whitelist body keys (never `**body`); raise house errors → 404/422/409. |

### 8.1 Registration-time reads — the PRD-011/004 lesson

`tools_structure`/`tools_urdf` sort **before** `tools_versioning`, so at
`register()` time `service.branches` and the installed `write_guard` do **not**
exist yet (versioning's `install_write_guard`, `tools_versioning.py:36-69`).
Both packs therefore read cross-pack seams (`service.branches`,
`service.packages`, `store.write_guard`) **lazily inside methods**, guarded by
`getattr(service, …, None)` — the exact pattern `tools_holes`/`tools_configs`
follow. Neither pack appends to `gate_providers` (they add no merge gate), so the
`tools_proposals` unconditional reset is a non-issue. The store's own writes
(`set_instances`, `set_assembly_interface`) go through `ProjectStore` under
`manifest_scope`, so history snapshots ride the existing `project_changed`
publish (no per-call hook).

---

## 9. Agent surface (net changes)

- **Changed:** `set_assembly` (instance schema per FR1/FR5), `set_mate` (`+dof`),
  `sweep_motion` (drives new DOFs; sweeps expanded members),
  `check_interference` (unchanged in MVP; broad-phase is Phase 2, §13),
  `get_assembly` (tree + flattened views; recursive `total_mass_g`).
- **New (MVP):** `set_pattern {project, instance, pattern|null}` (a focused verb
  so a pattern edit is one call, and to keep `set_assembly` a full-replace),
  `set_assembly_interface {project, exports}`,
  `add_subassembly` / setting an instance's `assembly` via `set_assembly`,
  `export_urdf {project, name?, mesh_format?}`.
- **New (Phase 2, designed not built):** `set_coupling`, `clear_coupling`,
  `explode_assembly`.
- Error details: `validation_error` with `details.cycle` (FR2),
  `details.interface` (FR3); URDF/degraded-joint warnings in post-state.
- Events: structure changes ride the existing `project_changed`.

---

## 10. `get_assembly` — tree vs flattened (the count guarantee)

`get_assembly` returns both:

- **Tree view** (from raw `store.instances`): patterns as one node (`×N`),
  sub-assemblies as one node — for the sidebar and reviewable diffs.
- **Flattened view** (from `_resolved_instances`): every member with its
  `mesh_key`, resolved transform, and per-member mass; `total_mass_g` = Σ over
  the flattened set (recursive through sub-assemblies).

The mass roll-up, world bbox, and every `mesh_key` come from the **flattened**
set — so a pattern contributes N× mass and a sub-assembly contributes its members
(FR6/FR4), from the one expansion. The tree view never feeds a rollup; the
flattened view never feeds the sidebar structure. This split is where
under/over-counting is prevented, and it is asserted by AC1/AC2 tests.

---

## 11. Approaches considered (and rejected)

1. **Expansion in each consumer** vs **one expansion point.** Rejected
   per-consumer expansion: N places re-expanding is exactly the double-count trap
   and drifts (the packet would count differently from `get_assembly`). Chosen:
   `_resolved_instances` (+ `mates.expand` for `sweep_motion`).
2. **Server-side rotation math** vs **kernel `Location` composition.** Rejected a
   second server-side Euler implementation (the "rotations in one convention"
   gotcha). Chosen: compose in the kernel via build123d `Location`, shapes built
   only when connectors are needed.
3. **`simplified_rep` as a coarser `lod` tolerance** vs **a distinct hull/decimate
   build kind.** A coarse tolerance still tessellates every concavity/hole; a
   convex hull is dozens of triangles and a far better instancing proxy. Chosen:
   a distinct `handlers/simplify.py` tier (`convex` default), lazily produced,
   content-cached.
4. **Native build123d PlanarJoint** — does not exist; chosen: compose the planar
   DOF as a `Location` post-multiply on a rigid frame.
5. **Add a URDF-parser dependency for AC6** vs **hand-rolled `validate_urdf`.**
   PRD forbids a new dep; chosen: stdlib XML + structural asserts (house style),
   urdf-viz as evidence.

---

## 12. Testing strategy (parallel-safe)

- Pattern determinism + replace-not-add invariant (unit, no kernel).
- Two-level sub-assembly fixture (engine→stand→cell): internal mates hold, mass
  roll-up == hand sum (AC1); interface mate; **read-only spy** asserts zero
  `save_manifest`/`write_script` against the source (§3.4).
- Cross-project cycle → `validation_error` with `details.cycle`.
- Polar bolt-circle `count:8→6` updates mass, interference candidates, tree
  (AC2).
- slider clamp 80→50 + warning; `sweep_motion` `first_collision` vs an
  obstructing fixture (AC4). planar u/v/spin resolution unit test.
- `simplify_rep` produces a `<key>.simplified.acm` served through
  `mesh_info(lod="simplified")`; hull triangle count « full; content-cached once
  per key. InstancedMesh id-mapping in node.
- URDF golden-file (rocketry stack) + `validate_urdf`; masses within 0.1%
  (AC6-machine); parallel-axis shift correctness on an off-origin part.
- AC8: a v1 flat project is byte-identical (`.acm` sha golden, PRD-010/012
  precedent); all existing mate/motion/stackup tests unchanged.
- FEM-style `importorskip` is not needed here; keep runs targeted (machine is
  contended by the parallel PRD-006 checkout).

---

## 13. MVP vs Phase 2/3 (with justification)

**MVP (this spec builds):** FR1–FR8, FR10–FR11, FR14-core, AC1–AC4, AC6(core),
AC8. Rationale: this is the coherent "structure + scale-render + robotics-handoff"
slice that makes AgentCAD an assembly system; each piece is independently
testable and rides one expansion point.

**Phase 2 (seams designed, not built):**
- `explode_assembly` (FR13) — the UI slider seam and `order` (leaves-first) are
  reserved; the tool is not implemented. The frontend slider is present but
  disabled.
- `set_coupling`/`clear_coupling` + coupled `sweep_motion` (FR12) — the
  `assembly.couplings` schema + merge + validation land in MVP (so the format is
  stable); resolution and `<mimic>` are Phase 2.
- Ball/gear joints (FR10 remainder).
- Interference broad-phase (FR9) — see §13.1.
- URDF cylindrical decomposition + planar + `<mimic>`.

**Phase 3:** config-pinned sub-assembly refs (with PRD-012), packaged sources
(with PRD-011), per-instance DOF overrides. The `assembly.{version, config}`
fields are reserved in the schema now so no later migration is needed.

### 13.1 Interference broad-phase (FR9) — kept in Phase 2, with reasoning

The task invited folding FR9 into MVP for the 1k-scale AC. **AC3 is a *rendering*
criterion** (simplified mode, ≥30 fps orbit) — it does **not** run interference.
Interference at 1k already has a per-pair AABB skip + solid-AABB crop
(`worker.py:678-694`) — correct, just O(N²) in the cheap test. A true broad-phase
(sort-and-sweep / spatial hash) is a real win but is not on any MVP AC, so it
stays Phase 2 per the PRD split. Noted rather than folded.

---

## 14. Divergences from the PRD as written

1. **Clamping is a behaviour change** for existing revolute/cylindrical DOFs
   (raise → clamp+warning), applied uniformly for FR11 consistency. Existing
   tests use in-range values; flagged for verification in slice 3. (§5.3)
2. **`assembly.interface`/`couplings` merge needs a small code change** to be
   key-wise; the PRD says "all merged key-wise by PRD-001's driver" but the
   driver merges non-`instances` assembly sub-keys **atomically** today. §1.4
   makes it key-wise (per-name atomic). Instance-level `pattern`/`assembly` need
   **no** change (already whole-value per id).
3. **`set_pattern` is added** as a focused verb (not in the PRD agent surface),
   because `set_assembly` is a full-replace; editing one pattern's `count`
   otherwise means resending the whole list. Small, orthogonal, keeps the
   one-manifest-change-per-edit property (FR6).
4. **Explode slider is a disabled UI stub in MVP** (the tool is Phase 2), so the
   toolbar seam exists without shipping FR13.
5. **Couplings schema lands in MVP, resolution in Phase 2** — so the manifest
   format + merge stabilize once rather than churning.
6. **`export_urdf` inertia requires a parallel-axis shift** the PRD does not
   mention (the tensor is origin-referenced); without it URDF inertia is wrong
   for off-origin links. Treated as a correctness requirement, not an option.

---

## 15. The sharpest risk

**A sub-assembly resolution that accidentally writes to / rebuilds a source's
authored state**, or **pattern expansion double-/under-counting**. Both are
mitigated structurally: (a) `write_guard` is unreachable on a read-only source
(§3.4) and a store-spy test asserts zero authored writes; (b) expansion
**replaces** the base and is the **single** point every consumer reads, asserted
by `len(flat)==Σcount` + base-absent tests and by AC1/AC2 mass/interference
counts. These are the first tests written (plan slices 1–2).
