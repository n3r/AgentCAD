# Roadmap

v0.1 delivered the vertical slice: script-as-model parts on a real OCCT B-rep
kernel, projects/assemblies, a browser UI, and the dual agent surface (MCP +
built-in chat). **v2** built out the engineering depth on top of that spine.
**v3** closed out that roadmap — the "Shipped" section below is the honest
record.

**v4+ points at a bigger goal: a world-class cloud (and open-source) CAD
system where humans and AI agents work as peers.** The plan below is the
conclusion of a full competitive analysis
([competitive-analysis.md](competitive-analysis.md), August 2026) covering
Onshape, the desktop incumbents, the AI-native startups, open-source CAD, and
the surrounding workflow tools. Features here are not copied from
competitors; each is either a table stake the evidence says we cannot skip,
or a differentiator our model-as-code + kernel-as-referee architecture makes
possible and incumbents structurally cannot follow.

## Shipped since v0.1

v2:

- **CAD import** — STEP/BREP as boolean-capable reference parts, STL as
  mesh-only (measure/display).
- **Assembly mates** — declarative rigid/revolute/cylindrical connectors,
  resolved to concrete transforms; `check_interference` already validated the
  other half.
- **2D drawings** — projected views with dimensions and hole callouts
  detected from geometry (SVG/DXF).
- **Geometric feedback tools** — cross-section, min wall thickness, projected
  area, full inertia tensor; optional linear-static FEM behind `agentcad[fem]`.
- **Materials with engineering properties** — 30 curated materials plus a
  layered user-override resolver.
- **Robustness** — the `safe_fillet`/`safe_shell`/`safe_bool` toolkit, a
  constraint sketch solver, `bd_warehouse` threads, and an Error Doctor.
- **Performance** — a parallel kernel-worker pool for multi-part rebuilds.

v3:

- **Typed parameters** — PARAMS `type: number|int|bool|enum|string` with
  choices/max_len, enforced in both layers, typed inspector controls.
- **Per-solid part semantics** — `SOLID_LABELS`, per-solid metrics, per-solid
  materials with correct mass roll-ups.
- **Sheet metal** — the `SheetPart` toolkit (fold/unfold from one spec, bend
  allowance) and the `flat_pattern` export with bend lines (SVG/DXF).
- **PMI / GD&T** — a validated tolerance model on parts (dims/datums/FCFs)
  rendered as drawing callouts, plus **tolerance stack-ups** (worst-case +
  RSS) along the assembly mate graph.
- **Motion from mates** — driven revolute/cylindrical DOF sweeps with
  moving-body interference checking and viewport animation.
- **Class-A surfacing** — `smooth_loft` + G0/G1/G2 `blend_surface` (plate
  filling) and a `curvature` analysis kind to verify continuity numerically.
- **FEM tiers** — modal (natural frequencies) and steady-state thermal
  behind the same `[fem]` extra.
- **GUI sketching & push/pull** — an interactive 2D sketcher over the
  constraint solver that emits build123d code, and face push/pull recorded
  as script edits (script stays the source of truth).
- **Vision feedback** — `render_view`: server-side shaded renders returned
  as real image content over MCP and chat.
- **Turn-locking & multi-agent sessions** — per-project editing turns
  enforced at the store choke point; concurrent chat sessions with scoped
  identities.
- **Undo / history** — every mutation snapshots into a per-project git repo;
  Cmd+Z and `project_restore` time travel.
- **Mesh streaming** — coarse LOD tiers for >150k-triangle parts with
  progressive viewport loading (ACM1 unchanged).
- **Sandboxed script execution** — macOS seatbelt confinement of kernel
  workers (writes only in project roots, no network).
- **Windows/Linux CI** — a three-OS GitHub Actions matrix over the full
  suite (the architecture was already portable).
- **Single-binary distribution** — a self-contained PyInstaller bundle
  (`make dist`, ~390 MB) with the executable re-launching itself as the
  kernel worker.

## The thesis for v4+

Three findings from the competitive analysis drive everything below:

1. **The unit of collaboration is the change, not the session.** Onshape
   built Google-Docs-for-CAD and still merges last-writer-wins with no
   reviewable conflicts, because binary database deltas can't be reviewed.
   Our model is code: branches, proposals, semantic diffs, real conflict
   resolution, and CI are all *possible* — and they are exactly the
   primitives a mixed human+agent team needs. We build GitHub-for-CAD,
   where half the committers are agents and the kernel referees every merge.
2. **The validation loop is the moat.** 2025–26 research (MUSE, Embodied
   CAD) and every incumbent's alpha-quality "AI companion" say the same
   thing: generation is cheap, *validated* engineering is the bottleneck.
   AgentCAD's structured-error, metrics-after-every-mutation contract is the
   winning architecture — v4+ extends it from "does it build" to "does it
   meet spec, pass review, and survive manufacturing rules."
3. **The window is open but closing.** Onshape Labs has promised permissioned
   agents and a FeatureScript MCP server; Autodesk ships official MCP
   servers. Their versions are bolted onto unreviewable models. Ours is
   native — but only valuable if the cloud/collaboration substrate exists.
   Hence the ordering: collaboration core first, daily-driver depth second,
   generative/manufacturing moats third.

Each feature below states: **What** · **Why now** (evidence — see
[competitive-analysis.md](competitive-analysis.md)) · **Agent-native angle**
(what makes our version different from copying) · **MVP** · **Done when**.

---

## v4 — The collaborative core

*Goal: the "cloud CAD" claim becomes true, and the change-based human+agent
workflow ships before incumbents' bolted-on agents normalize. Everything in
v4 composes: branches carry proposals, proposals run CI, CI runs
design-tests, review threads anchor to the diff, and all of it works
identically self-hosted or hosted.*

### 4.1 · Branching project history

**What.** Upgrade the per-project git history from linear undo to real
version control: named branches, named immutable versions (tags), merge with
conflict detection, and a history graph UI. Script conflicts surface as
standard git conflicts; `project.json` gets a structure-aware merge driver
(parts/instances/materials merged key-wise, not line-wise). New tools:
`branch_create/list/switch`, `version_tag`, `merge_branch`.

**Why now.** Onshape's versions/workspaces are its backbone — but its merge
is per-tab, three-strategy, last-writer-wins, no cherry-pick. Named immutable
versions are also the prerequisite for release management (5.7) and for any
review workflow.

**Agent-native angle.** Agents get cheap isolated workspaces (branch = agent
sandbox), and merges are *validated*: the kernel rebuilds the merge result
and blocks on regression (broken builds, new interference) — a merge gate no
CAD has.

**MVP.** Branch/tag/merge on the existing `.history/` repo; manifest merge
driver; conflict payloads as structured errors; branch switcher in the
toolbar; linear undo unchanged on every branch.

**Done when.** Two branches editing different parts merge clean; same-part
edits produce a surfaced conflict an agent can resolve via tools; tagged
versions are immutably restorable; suite covers cross-branch cache reuse.

### 4.2 · Change proposals with geometric diff (CAD pull requests)

**What.** A proposal = source branch + target + title/description +
auto-generated review packet: per-part script diffs, PARAMS diffs, metric
deltas (volume/mass/CoM/bbox), assembly deltas, before/after renders from
matched camera poses, and a 3D visual diff (added volume green / removed red,
computed by booleans between old and new builds). Approve/request-changes/
merge in the UI; full audit trail of who (human or agent) did what.

**Why now.** This is the single largest open space the analysis found:
nobody ships reviewable CAD changes — Onshape can't (binary deltas), CoLab
raised $72M bolting review onto files *outside* CAD. It is also the core
trust mechanism for agent work: agents propose, humans decide.

**Agent-native angle.** Agents author proposals with self-written summaries
and evidence (renders, metrics); other agents can be reviewers. The review
packet is exactly the structured data an LLM needs to judge a change.

**MVP.** Proposal object on top of 4.1 branches; packet generation in the
service (renders via `render_view`, metric deltas from cached metrics;
geometric diff kind in the analysis handler pack); proposal list + detail
view in the UI; merge = 4.1 merge with the validation gate.

**Done when.** An agent branch → proposal → human review → merge round-trip
works end-to-end in the browser with zero terminal use; the packet renders
for the rocketry example in <10 s; every proposal action is attributed in
the audit log.

### 4.3 · Design specs as executable tests

**What.** A first-class spec layer: `SPECS` in part scripts and a project
`specs.py` — assertions over built geometry and assemblies with the same
contract as PARAMS (`check_wall(min=2.5)`, `check_mass(max_g=120)`,
`check_clearance(a, b, min_mm=0.5)`, `check_interference_free()`,
`check_stackup(axis="z", within=(-0.2, 0.2))`, `check_fem_static(max_vm_mpa=…)`,
arbitrary Python predicates). Specs run on rebuild (warnings) and gate
proposals (4.4). Each spec can carry a `requirement` string/URL, giving
requirements→geometry traceability.

**Why now.** Requirements↔CAD traceability lost its champion (Valispace
absorbed into Altium); engineering budgets (mass/cost/clearance) live in
spreadsheets that silently diverge. And agents need machine-checkable intent:
"keep it under 120 g and 2.5 mm walls" as *code*, not chat history.

**Agent-native angle.** TDD for hardware: humans (or agents) write the spec,
agents iterate geometry until green — the kernel referees intent, not just
validity. Specs are the objective function for 6.2's optimization studies.

**MVP.** Spec contract + worker evaluation reusing existing analysis
handlers; `run_specs` tool; spec status chips in the inspector; failures as
structured errors with per-check details.

**Done when.** The rocketry example ships specs (chamber mass budget, nozzle
wall min, flange bolt-circle clearance); breaking one fails `run_specs` with
the failing check named; a proposal that violates a spec shows a red gate.

### 4.4 · Geometry CI

**What.** A headless check runner: for a branch/proposal, rebuild every part
(all configs once 5.4 lands), run specs, interference, drawing regeneration,
and optional FEM smoke checks; emit a machine-readable + human-readable
report; post status to the proposal. Ships as `agentcad check` (CLI) and a
GitHub Action so repo-hosted projects get CI on push.

**Why now.** "CI for CAD" exists nowhere — incumbents regenerate nothing
deterministically; our determinism guarantee (same script + params ⇒
identical geometry) makes it trivial and trustworthy. It also makes the
open-source distribution channel (projects on GitHub) first-class.

**Agent-native angle.** The CI report is the agent's feedback loop at
change-scale rather than rebuild-scale; a red check is a structured task an
agent can pick up and fix autonomously.

**MVP.** `agentcad check [--project P] [--ref branch]` over the service in
headless mode; JSON + markdown report; proposal status integration; a
published GitHub Action wrapping it.

**Done when.** CI runs green on all three bundled examples in the repo's own
GitHub Actions; introducing an interference into the construction example
turns the proposal gate red with the offending pair named.

### 4.5 · Multi-tenant cloud service

**What.** The server grows a deployment mode with real identity and
isolation: OIDC/passkey auth, users/orgs/workspaces, per-project roles
(view/comment/edit/admin), HTTPS, project storage namespaced per tenant,
kernel pool scheduling with per-tenant fairness, and audit logs keyed by the
existing client-identity plumbing (`X-Agent-Id` becomes authenticated
principals: `user:nikita`, `agent:chat:main`, `agent:mcp:claude`).
Local-first stays sacred: a project is a git repo; `agentcad push/pull`
syncs laptop ↔ cloud; the same open-source binary self-hosts the whole thing
(docker compose).

**Why now.** Everything in the goal statement ("cloud CAD") and every
sharing/collab feature depends on it. Onshape's model proves demand; its
cloud-only architecture is also its top complaint (no offline). Local-first
+ sync is the structural answer — and air-gapped operation matters to the
exact A&D startups PTC is courting.

**Agent-native angle.** Agents are principals, not headers: scoped tokens,
per-agent permissions (e.g. propose-but-not-merge), quotas, and a complete
who-did-what trail — the governance story Onshape Labs is only promising.

**MVP.** Auth + orgs + roles; tenant-scoped project stores; push/pull sync;
docker deployment; the browser UI aware of identity (avatars on presence,
lock chips naming real principals). Signed/notarized desktop builds ride the
same release pipeline.

**Done when.** Two users in one org collaborate on one project from two
machines against a hosted instance; a third without access is 403'd; the
laptop clone works offline and syncs back; the whole stack deploys from the
public repo with one compose file.

### 4.6 · Cross-platform sandboxing and resource quotas

**What.** Extend the macOS seatbelt confinement to Linux (Landlock +
seccomp, or bubblewrap/gVisor containers per kernel worker) and Windows
(AppContainer), and add resource governance: CPU-seconds, memory caps,
wall-clock budgets, disk quotas, and no-network defaults per worker, with
per-tenant metering surfaced through `/api/health` and the audit log.

**Why now.** Promoted from v3 residual to hard prerequisite: a multi-tenant
cloud executes untrusted Python from strangers and their agents. Linux
confinement is the one that matters first (that's what the cloud runs on).

**Agent-native angle.** Quota metering is also the billing/fairness
substrate for agent fleets (6.3) — compute-metered, never seat-metered.

**MVP.** Landlock/seccomp profile with the same deny-by-default semantics as
the seatbelt profile; cgroup-based CPU/memory caps in the pool; kill-and-
respawn on breach reported as the existing structured `timeout`/`kernel_crash`
errors.

**Done when.** A malicious example script (network attempt, path escape,
fork bomb, memory balloon) is contained on Linux CI with the violation
reported cleanly; `/api/health` reports `sandbox: active` on all three OSes.

### 4.7 · Share links, embedded viewer, and customizer publishing

**What.** Read-only share links for projects/parts/versions rendering the
existing Three.js viewer (glTF under the hood) with metrics, drawings, and —
the differentiated half — a **customizer mode**: published parts expose
their typed PARAMS as sliders/dropdowns; visitors tweak within bounds, watch
the kernel rebuild, and download STEP/STL/3MF/drawings of *their* variant.
Embeddable iframe for forums/docs.

**Why now.** Sharing is the entry ticket to cloud CAD (Onshape free tier,
GrabCAD's dead Workbench left a vacuum), and slider-customizers are the
proven consumption mode for parametric models (Thingiverse Customizer,
MakerWorld's Parametric Model Maker) — but capped at meshes. Ours emit
B-rep engineering artifacts: STEP, flat patterns, toleranced drawings.

**Agent-native angle.** Every published script is agent-readable reuse
substrate, and the customizer is the zero-install top of funnel that feeds
the registry (5.3) and generation (6.1) with humans who never wrote code.

**MVP.** Signed share URLs with role=view; server-rendered viewer page;
param playground bounded by PARAMS min/max/choices with rebuild rate
limits (4.6 quotas); export gating per link settings.

**Done when.** A rocketry nozzle link opens for a logged-out visitor, the
expansion-ratio slider rebuilds live, and STEP-of-variant downloads; embeds
render on a third-party page.

### 4.8 · Anchored review threads and presence

**What.** Comments anchored to model entities — a part, a face (via the
existing face-index sidecar), a param, a script line range, an assembly
instance, or a proposal diff hunk — with threads, resolve state, mentions,
and notifications. Plus presence: who is looking at what, per-part soft
claims replacing the coarse project turn lock for humans (agents keep
explicit turns), and per-user undo in shared sessions.

**Why now.** Review-on-the-model is table stakes (Onshape's anchored
comments; CoLab's entire business is pinned feedback), and it is where
human intent enters the loop.

**Agent-native angle.** Comments are tool-visible: an agent reads the open
threads on its proposal, addresses each, replies with evidence (render,
metric delta), and marks resolved — review feedback becomes structured work
items, not lost chat.

**MVP.** Comment store + anchors + WS events; thread UI in inspector and
proposal views; `list_comments`/`reply_comment`/`resolve_comment` tools;
presence avatars from the WS channel; per-part claims at the store
choke point (same seam as turn locks).

**Done when.** A human comments on a face ("this boss needs a fillet"); an
agent lists it, edits the script, replies with a before/after render, and
resolves; a second browser sees all of it live.

---

## v5 — Daily-driver depth and the ecosystem

*Goal: an engineer's Tuesday. The features the evidence says users check for
before trusting a CAD as their primary tool — built code-first so every one
of them is also an agent capability — plus the ecosystem loop that compounds:
packages, configurations, real drawings, releases.*

### 5.1 · Sketcher v2

**What.** Complete the 2D story: arcs, splines, ellipses, slots, conics in
both the solver and the GUI sketcher; tangency/symmetry/equal constraints on
the new entities; drag-to-solve with warm starting (the reserved `initial`
hook); DOF and over/under-constraint diagnostics with rank reporting;
sketch-on-face (reference existing geometry); emitted code stays clean
build123d `BuildLine/BuildSketch`.

**Why now.** "A constraint sketcher without arcs reads as a toy" — every
incumbent and every OSS peer (FreeCAD, SolveSpace, Dune3D) has these. It is
the most-cited v3 residual and blocks real profiles (brackets, cams, ports).

**Agent-native angle.** The solver is already a tool (`solve_sketch`);
completing its vocabulary completes the *agent's* 2D vocabulary too — and
AutoConstrain-style assistance (suggesting constraints from rough geometry)
becomes a natural agent task on top.

**MVP.** Solver entities + constraints; sketcher UI for arc/spline drawing
and editing; warm-start API; diagnostics payload.

**Done when.** A slotted cam profile with tangent arcs solves, edits by
drag, and emits code that rebuilds identically; over-constraining reports
the conflicting set.

### 5.2 · Feature toolkit II (patterns, holes, sheet-metal v2)

**What.** The high-frequency feature vocabulary as toolkit helpers + UI
actions that emit script calls: linear/polar/mirror patterns (of features
and bodies); a hole wizard — clearance/tapped/counterbore/countersink from
ISO/ANSI tables, placed on faces, with machine-readable hole metadata
flowing to drawing callouts (5.6) and DFM checks (6.4); ribs, bosses, draft
helpers. Sheet-metal v2: bend relief, partial-width flanges, hems, and
corner treatments in `SheetPart`.

**Why now.** SolidWorks' Hole Wizard/Toolbox is daily-use muscle memory;
hole standards are the single highest-leverage gap for robotics/rocketry
users. Bend relief + partial flanges were the explicit sheet-metal v1
residual.

**Agent-native angle.** Standards tables become part of the agent's
vocabulary ("M5 clearance holes on a 40 mm bolt circle" is one call, not
trigonometry), and every helper returns the same honest warnings the
`safe_*` family does.

**MVP.** `patterns`, `holes` (with ISO/ANSI data), sheet-metal additions in
the toolkit; UI: hole placement on face-click, pattern dialog; CHEATSHEET
sections.

**Done when.** The construction example's bolt patterns rewrite to the
helpers with identical geometry; a tapped-hole callout appears on the
drawing with correct designation; a flanged bracket gets bend relief and
still round-trips fold/unfold.

### 5.3 · Standard parts and the package registry

**What.** "pip for parts": versioned part packages (script + typed PARAMS +
connectors + specs + docs) that projects declare as dependencies; an index
with semantic search; `add_package`/`use_part` tools; local cache;
publishing flow with mandatory kernel validation (builds at param extremes,
spec pass, connector checks). Seeded three ways: bd_warehouse (threads/
fasteners/gears) wrapped as packages; an agent-built curated COTS library
(ISO/DIN fasteners, bearings, extrusions, NEMA motors, COTS electronics
outlines); and a McMaster-STEP ingestion path that wraps a vendor model
into a placeable, mate-ready reference package for private use.

**Why now.** Standard content is assumed (Toolbox, Onshape Standard Content
— which users complain isn't extensible); McMaster is de-facto
infrastructure; and npm-for-parts is *unclaimed* (PartCAD sits at 483 stars
with its registry "in progress"). This is the ecosystem flywheel open source
can win.

**Agent-native angle.** The economics flip: a validated registry was
prohibitively labor-intensive to curate by hand — agents generate, test
(the kernel referees), repair, and document packages at scale. Typed PARAMS
+ connectors are the package interface contract.

**MVP.** Package format + lockfile in the manifest; local + git-hosted
indexes (cloud registry rides 4.5); fastener starter set with connectors;
publish CLI with the validation gate.

**Done when.** `add_package(iso4762)` + `use_part("M5x16")` mates a real
cap screw into the prototyping example via its connector; a corrupted
package fails publish validation with the failing check named.

### 5.4 · Configurations

**What.** Named parameter sets as first-class variants: a part/assembly
declares configurations (e.g. `S/M/L`, `left/right`), each a PARAMS
override set with its own identity — per-config metrics, BOM line, exports,
and drawing dimension tables. Config matrix builds in one call; the
customizer (4.7) can expose configs instead of raw params.

**Why now.** Configurations/design tables are a named top loss for anyone
leaving SolidWorks and a mature Onshape strength; every real product line
is a family, not a part.

**Agent-native angle.** A config sweep is just data (`build_configs` →
per-config metrics/spec results), so agents reason across the family —
"which sizes violate the mass budget?" — in one call; CI (4.4) builds all
configs on every proposal.

**MVP.** `configurations` in PARAMS/manifest; config switcher in inspector;
config-aware cache keys (the hash already includes params); per-config
export naming.

**Done when.** A three-size flange family builds as a matrix with per-config
mass in one tool call; the drawing shows a tabulated dimension table;
deleting a config referenced by an assembly instance is a conflict.

### 5.5 · Assembly v2 — structure, scale, richer joints

**What.** Sub-assemblies (a project can instance another project/assembly
with its own mate graph); instance patterns (bolt circles of screws, not N
manual instances); large-assembly semantics — kernel-side simplified
representations (convex/decimated proxies) with lightweight loading so 1k+
instances stay interactive on top of the existing LOD; richer joints:
slider, planar, ball, gear/rack couplings with limits; exploded views
derived from the mate graph (offsets along mate axes) for docs and the
viewer; URDF export (links/joints from parts/mates) for robotics toolchains.

**Why now.** Fusion's ~500-component ceiling is a documented churn driver;
FreeCAD 1.x ships richer joints; Onshape exports URDF and courts robotics.
Exploded views also feed auto-documentation (6.6).

**Agent-native angle.** Sub-assembly interfaces (exported connectors) let
agents compose systems the way software composes modules; URDF makes
AgentCAD outputs land directly in the sim stacks (Isaac, MuJoCo) agents
already drive.

**MVP.** Sub-assembly instancing + flattened resolve; instance patterns in
the manifest; `simplified_rep` build kind + viewport toggle; slider/planar
joints; URDF exporter; exploded-view offsets tool.

**Done when.** A 1,000-instance synthetic assembly orbits at interactive
rates with simplified reps; a two-level assembly (engine on test stand)
resolves mates through the boundary; the rocketry stack exports URDF that
loads in a standard viewer.

### 5.6 · Drawings v2 — the standards wrapper

**What.** Make the auto-generated drawings shop-submittable: sheet formats
and title blocks (ASME/ISO templates, project fields, revision block);
assembly drawings with BOM tables and balloons; section and detail views;
centerlines/center marks; hole tables fed by 5.2's hole metadata;
config dimension tables (5.4); PDF export alongside SVG/DXF; deterministic
regeneration per version so drawings are CI artifacts (4.4), not stale
files.

**Why now.** "Shops reject drawings that don't look standard." Incumbents'
decades of template depth is a moat, and their AI auto-drawing features
(SW2026, Solid Edge 2026) are raising the baseline — but none of them
*regenerate deterministically from the model on every change*. Ours do,
because the model is the only source of truth.

**Agent-native angle.** Drawings become compiled artifacts of the change
workflow: an agent's proposal carries the regenerated drawing diff, and the
AI-drawing-checker role (which Onshape is promising) is just another spec.

**MVP.** Sheet/title-block templating; assembly view + BOM/balloon
generation from the manifest; section views via the existing cross-section
handler; PDF backend.

**Done when.** The construction gusset produces an A3 ISO sheet with title
block, a sectioned view, balloons matching the BOM table, and a hole table
— byte-stable across two runs at the same version.

### 5.7 · BOM and release management

**What.** Structured BOMs as first-class data: part numbers, per-config
identity, materials, mass, quantity roll-ups across sub-assemblies, unit
cost fields, sourcing links (package metadata from 5.3 flows in);
CSV/JSON/Sheets export. On top: release management — Rev A/B revisions
with approval workflows riding proposals (4.2), immutable releases pinning
a version (4.1) with a generated bundle (STEP + drawings + BOM + flat
patterns + README).

**Why now.** "PDM built in" is the single biggest reason teams choose
Onshape; startups run BOMs in spreadsheets until an ECO disaster. We get
the outcome with zero vault infrastructure because versions are git tags
and approvals are proposals.

**Agent-native angle.** Releases become one agent task: "cut Rev B of the
test stand" — the agent assembles the bundle, writes the release notes from
history, and routes the approval to a human. Spec gates (4.3) make
"released implies green" enforceable.

**MVP.** BOM builder over manifest + packages; revision state machine on
proposals; release bundles as reproducible export jobs.

**Done when.** The rocketry project cuts a release: approval recorded,
bundle downloadable, BOM opens in Sheets with correct roll-ups; editing a
released version is impossible (new branch required).

### 5.8 · Workbench UX depth

**What.** The viewport/inspector maturity pass: measure tools (distance,
angle, radius, edge length), live section views in the viewport, curvature/
zebra overlays (the analysis data already exists), appearance/material
render modes, drag-to-place instances with mate snapping (magnetic-snap
style suggestions from connector compatibility), and **selection-aware
chat**: the current selection (part/face/instance/sketch entity) rides into
the agent's context so "fillet this edge" needs no ids.

**Why now.** Shapr3D proves adaptive UX sells; Solid Edge ships AI magnetic
snap; selection-aware copilots are the ergonomic baseline set by Zoo and
the incumbent assistants. Humans steer best by pointing.

**Agent-native angle.** Selection context is serialized as structured data
(face indices via the existing sidecar), so pointing composes with every
tool — the human's cursor becomes an argument to the agent's next call.

**MVP.** Measure + section + overlays; selection→chat context payload;
connector-snap suggestions when dragging instances.

**Done when.** "Make this wall 1 mm thicker" works with only a face
selected; section view slices the assembly live; zebra stripes render on
the surfacing example.

### 5.9 · Interop pack

**What.** Round out exchange: STEP AP242 export with PMI (the tolerance
model already exists — attach it); 3MF export with metadata/units/colors
(ISO 25422); glTF export (feeds 4.7 share links and web embeds); structured
assembly-STEP import (product tree → parts + instances, not one blob);
DXF/SVG already ship; USD export behind a flag for the digital-twin
ecosystem.

**Why now.** The format landscape settled in 2025–26: STEP authoritative,
3MF for print (slicers' native), glTF for web review, USD rising (Core Spec
1.0, ISO track). STL-only reads as dated; AP242 PMI is what makes our GD&T
survive the trip to suppliers.

**Agent-native angle.** Import fidelity is agent leverage: a structured
assembly import gives agents a real product tree to reason over and
re-parametrize piece by piece (with 6.5's assist).

**MVP.** AP242 PMI writer; 3MF metadata; glTF exporter; assembly-STEP
reader mapping to reference parts + instances.

**Done when.** A toleranced part's STEP re-imports into FreeCAD/NX viewers
with PMI visible; an imported multi-part STEP shows a real instance tree;
the share-link viewer streams glTF.

---

## v6 — Generative engineering and the manufacturing bridge

*Goal: compound the moats. Generation grounded in the kernel, optimization
grounded in specs, manufacturing grounded in open rules — each one both a
product feature and a data/ecosystem flywheel incumbents can't run.*

### 6.1 · Task-to-part generation (kernel-grounded)

**What.** The generation front door — prompt, sketch photo, PDF drawing, or
datasheet in; validated parametric part out. Not one-shot: a built-in
generation loop drafts a build123d script, builds it, *looks* at it
(render_view), reads metrics against the stated intent, and iterates until
the kernel and the specs (4.3) are green — then returns a part with typed
PARAMS, connectors, and a generated spec block. Multi-candidate generation
with a side-by-side picker. Datasheet grounding: "mount for NEMA 17" pulls
the bolt-circle numbers from the standard, not from vibes.

**Why now.** Every rival's front door is text-to-CAD (Zoo, Adam's 1M
models); benchmarks say one-shot fails on engineering criteria while
solver-grounded iteration works — which is our architecture. The startups
lack depth; the incumbents lack the loop. Nobody has generation that
terminates on *spec-green*.

**Agent-native angle.** Generation is not a separate model — it's the same
agent surface driving the same 39+ tools, so generated parts are ordinary
reviewable proposals (4.2), not magic blobs.

**MVP.** A generation orchestration mode in the chat agent (budgeted
iterate-until-green loop); prompt+image intake; candidate gallery UI;
generated-part provenance recorded in the manifest.

**Done when.** "A 2 mm wall enclosure for a 60×40 mm PCB with M3 bosses and
a snap lid" yields a buildable, spec-green, parametric part with sane PARAMS
in under 3 minutes; the eval harness (6.7) scores the loop above one-shot
baselines.

### 6.2 · Design studies and optimization

**What.** Studies as first-class jobs: parameter sweeps, DOE, and
scipy-driven optimization over typed PARAMS with objectives/constraints
drawn from metrics, specs (4.3), and FEM; Pareto fronts and study reports
(tables + renders + the winning candidate as a proposal). "Minimize mass
subject to first mode > 120 Hz and wall ≥ 2 mm" is one call.

**Why now.** Incumbent generative design is expensive black-box topology
optimization; our targets mostly need *sizing* — fast, explainable
optimization over parameters they already exposed. The kernel pool and
deterministic cache make sweeps cheap.

**Agent-native angle.** Agents set up studies from natural language, watch
convergence, and narrate trade-offs; study results feed back into the spec
layer ("tighten the mass budget — margin exists").

**MVP.** Study runner over the kernel pool (config-matrix machinery from
5.4); objective/constraint bindings to metrics/specs/FEM; study report
artifact; background execution via 6.3.

**Done when.** The nozzle study (mass vs. first-mode frequency across two
params) produces a Pareto front and a winning proposal, fully offline from
the chat transcript.

### 6.3 · Jobs and fleet orchestration

**What.** A background job system for everything long-running (CI, studies,
generation, release bundles, registry validation): queue, progress events
on the WS channel, cancellation, retries, per-principal quotas (4.6
metering), and fleet semantics — many agents with scoped roles
(drafter/reviewer/optimizer) coordinating through branches and proposals
rather than shared mutable state.

**Why now.** "Run 50 design agents overnight" is economically impossible on
per-seat incumbent licensing and technically impossible over COM — it is
the workload our architecture exists for, and the compute-metered business
model depends on it.

**Agent-native angle.** The coordination primitive is the proposal, so
fleet output is inherently reviewable; roles + permissions (4.5) keep a
drafter from merging its own work.

**MVP.** Job queue in the service (persisted, resumable); job tools
(`submit/status/cancel/list`); quota enforcement; UI job tray.

**Done when.** Ten parallel study jobs saturate the kernel pool fairly, a
canceled job stops cleanly, and an overnight fleet run leaves only
proposals + reports (no orphaned state).

### 6.4 · DFM rule packs and cost models

**What.** Manufacturability as executable, open rules: per-process packs
(CNC: internal-corner radius vs. tool, pocket depth ratios, tool access;
3DP per FDM/SLA/SLS: min wall/feature, overhangs, escape holes; sheet:
bend radius, hole-to-bend, relief; IM: wall uniformity, draft) run natively
by the kernel against built geometry, returning located violations like the
wall-thickness check does today. Parametric cost models (material volume +
process time proxies) per process. Rules are data (versioned YAML +
Python), community-extensible like materials.

**Why now.** Siemens paid ~$50M to put Xometry DFM inside NX; Protolabs
ships DFM with every quote — but always *after* upload, at the vendor's
portal. Design-time DFM is expected next, the rule content is stable and
published, and nobody owns an open ruleset ("ESLint for parts").

**Agent-native angle.** Violations are structured errors — the loop that
fixes a fillet today fixes an unmachinable pocket tomorrow. `check_dfm`
becomes a spec (4.3) and a CI gate (4.4): "this project stays 3-axis
machinable" is enforceable.

**MVP.** Rule engine in an analysis handler pack; CNC-3axis + FDM packs;
`check_dfm(process=…)` tool with located violations; cost estimate v1.

**Done when.** The prototyping enclosure reports its FDM violations (min
wall, overhang) with face locations; fixing them via agent turns the check
green; cost moves sensibly with volume/material.

### 6.5 · Manufacturing connectors — quotes, print pipeline, scan import, sim burst

**What.** The integrate-don't-build ring, one connector pack each:
**quotes** — Xometry/JLC3DP/PCBWay APIs behind one `get_quotes` tool
(upload STEP/3MF, return price/lead-time per process/vendor);
**print** — slicer CLI orchestration (PrusaSlicer/Bambu/Orca) producing
settings-embedded sliced 3MF artifacts; **scan/mesh→parametric assist** —
Backflip-class/SGS-1-class services (or local fitting for prismatic cases)
draft a script from an imported mesh for agent refinement; **sim burst** —
SimScale-class cloud solvers for fidelity beyond built-in FEM, with the
agent routing "which fidelity does this question need."

**Why now.** Each neighbor is already API-shaped and agentizing (SimScale
Engineering AI via API; slicer CLIs; quote APIs; Backflip GA). The analysis
verdict was unanimous: the credible manufacturing story is STEP + PMI +
drawings + DFM-checked quotes — connectors, not an in-house CAM/solver/
foundation model.

**Agent-native angle.** The whole outer loop compresses to one command:
design → `check_dfm` → fix → `get_quotes` → pick vendor → release bundle.
Hours of portal round-trips become an agent errand with a human decision at
the end.

**MVP.** Connector extension-point (API-key config per org); quotes for
CNC+3DP via one vendor + JLC; slicer pipeline for one slicer; mesh-assist
behind a flag.

**Done when.** The flange gets three real quotes in-app from a spec-green
release; a sliced 3MF prints without opening a slicer GUI; a scanned
bracket mesh yields an editable draft script.

### 6.6 · Auto-documentation

**What.** Docs as compiled artifacts: assembly instructions (step sequence
from the mate graph + exploded views (5.5) + per-step renders), project
READMEs with live renders and metrics, release notes written from history
between versions, BOM with sourcing links — regenerated per release (5.7),
agent-drafted, human-approved via the normal proposal flow.

**Why now.** Documentation is the most-hated, most-deferred engineering
chore and a pure agent sweet spot; nothing in the market generates assembly
docs from mate semantics because no one else's assemblies carry semantics
in reviewable form.

**Agent-native angle.** The agent has everything it needs in-context —
geometry, mates, history, specs — so docs are grounded, not hallucinated;
approval rides review (4.2/4.8).

**MVP.** Exploded-sequence heuristic from the mate forest; instruction
renderer (HTML/PDF) using render_view; release-notes generator over git
history.

**Done when.** The rocketry stack produces printable assembly instructions
(correct order: flange → injector → nozzle) and honest release notes for
its last three versions without hand-editing.

### 6.7 · AgentCAD-Bench — public agentic-CAD evals

**What.** An open benchmark suite for agent-driven CAD: tasks
(model-from-drawing, modify-to-spec, fix-the-broken-part, assemble-and-
clear, optimize-under-constraints) with kernel-scored ground truth
(geometry match, spec pass, interference, mass windows) — runnable against
AgentCAD's own agent and, via MCP, against others. Doubles as our
regression suite for 6.1's generation loop.

**Why now.** Nobody publishes agentic-CAD numbers — Zoo ships zero evals;
academic benchmarks (Text2CAD-Bench, MUSE) exist but no product reports
against them. The first credible, open, kernel-scored benchmark sets the
narrative and the quality bar simultaneously — classic open-source
leverage.

**Agent-native angle.** The benchmark is only possible because scoring is
mechanical: the kernel referees success, the same way it referees
everything else.

**MVP.** 25–40 tasks across the five categories with scoring harness
(`agentcad bench`); public leaderboard page; CI integration so our own
agent's score gates releases.

**Done when.** Published results for our agent plus at least two external
setups (e.g. Claude via MCP, a KCL-based baseline) with reproducible
harness runs; the score is part of release criteria.

---

## Deliberate non-goals (v4–v6)

Carried forward or newly decided — each with the reason:

- **Our own geometry kernel, CAD language, or B-rep foundation model** —
  the Fornjot/CADmium graveyard, the KCL/FeatureScript DSL tax, and $30M+
  data-poor training runs are documented mistakes. OCCT + Python +
  integrate-generation-models is the survivable path.
- **In-house CAM/toolpathing** — Fusion's decade-deep, safety-critical
  moat; our handoff is STEP AP242 + PMI + standards-correct drawings +
  DFM-checked quotes (6.4/6.5).
- **In-house high-fidelity solvers (contact/nonlinear FEM, CFD)** — burst
  to cloud solvers via connectors (6.5); built-in FEM stays the fast
  sanity tier.
- **Full kinematic/closed-chain solver** — richer joints and driven DOFs
  (5.5) yes; simultaneous multi-joint linkage solving remains out until
  demanded.
- **Same-file CRDT co-editing as the collaboration foundation** — per-part
  concurrency + proposals (4.1/4.2/4.8) deliver the value; live co-editing
  of one script is later polish.
- **Interactive Class-A sculpting UX** — the surfacing toolkit + curvature
  analysis stand; control-point sculpting is a product of its own.
- **Enterprise PLM ceremony, vault PDM, per-seat or metered-API pricing,
  public-documents free tiers, iframe app stores, VR concepting,
  implicit-modeling kernel, mesh-generation text-to-3D** — each is a
  competitor liability we inherit no obligation to repeat.

## Sequencing and dependencies

| Depends on → | 4.1 branches | 4.3 specs | 4.5 cloud | 4.6 sandbox |
|---|---|---|---|---|
| 4.2 proposals | ● | ○ (gates) | | |
| 4.4 CI | ● | ● | | ○ (headless) |
| 4.7 sharing | | | ● | ● (quotas) |
| 5.3 registry | | ● (validation) | ○ (hosted index) | |
| 5.7 releases | ● | ● | | |
| 6.1 generation | ○ (proposals out) | ● (termination) | | |
| 6.2 studies | | ● (objectives) | | ○ (quotas) |
| 6.3 fleets | ● | | ● (principals) | ● (metering) |
| 6.7 bench | | ● (scoring) | | |

● hard dependency · ○ soft (better with).

The order of the phases is the argument of the
[competitive analysis](competitive-analysis.md): the collaboration core is
the differentiated wedge and the trust substrate for everything agents do
(v4); daily-driver depth converts trials into primary-tool adoption (v5);
generation, manufacturing, and evals compound into moats only once both
exist (v6). Within each phase, features are vertical slices behind the
existing extension points (handler/tool/route packs and the service seams)
— the same discipline that shipped v2 and v3. Each feature gets its own
design spec and implementation plan (`docs/superpowers/specs|plans/`, via
the brainstorming → writing-plans process) when it is picked up; this
document fixes the *what* and the *why*, not the build steps.
