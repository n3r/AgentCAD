# AgentCAD User Guide

A surface-by-surface tour of the AgentCAD workbench for an engineer opening
the app for the first time. What AgentCAD *is* and why is in the
[README](../README.md); how it works inside is in
[architecture.md](architecture.md); the part-script language is in
[part-authoring.md](part-authoring.md). This document covers what you see
and click.

## Starting the app

```bash
make setup     # one time: uv sync (build123d/OCCT is ~2 GB of wheels)
make run       # server + browser at http://127.0.0.1:8630
```

`make run` is `uv run agentcad open` — it starts the server and opens the
UI after a second. The other entry points:

| Command | Effect |
|---|---|
| `agentcad serve` | Start the server; no browser (`make serve` adds `--no-open` explicitly). |
| `agentcad open` | Serve **and** open `http://127.0.0.1:<port>` in the default browser. |
| `agentcad new <name>` | Create an empty project on disk without starting the server. |
| `agentcad export <project> <part> --format step\|stl\|3mf [--config NAME] [-o OUT]` | Headless one-shot export. `--config` exports one declared [configuration](#configurations) to `exports/<part>_<config>.<format>`. |
| `agentcad check [--project P] [--ref R]` | Certify a whole project headlessly — rebuild, assembly, specs, drawings — writing a JSON report and a markdown summary and answering with an exit code (`0` green, `1` red, `2` no verdict). See [geometry-ci.md](geometry-ci.md). |
| `agentcad mcp` | The MCP stdio server for external agents — see [agent-api.md](agent-api.md). |
| `make app` | Build `dist/AgentCAD.app`, a macOS wrapper that runs `agentcad open` (logs to `~/Library/Logs/AgentCAD.log`). |

`serve` and `open` accept `--port N`, `--projects-dir P`, and `--no-open`.
The default port is **8630**, persisted in `~/.agentcad/config.json`; a
`--port` flag wins for that run without changing the config. Projects
default to `~/AgentCAD/projects/`.

First launch is slower than the rest: the kernel worker imports
build123d/OCCT once (~3 s, up to 180 s allowed) before the first build.
After that, rebuilds are typically 10–100 ms.

The five bundled examples (`rocketry`, `construction`, `prototyping`,
`fasteners`, `engine`) are registered automatically from the repo's
`examples/` directory and appear in the project switcher alongside your own
projects.

## The workbench

```
┌──────────────────────────────────────────────────────────────────┐
│ AgentCAD  [project ▾]     (Rebuilding…)  [Fit] [Export ▾] ☀  ●  │  toolbar
├──────────┬───────────────────────────────────┬───────────────────┤
│ Parts    │                                   │ Parameters │ Code │
│  nozzle  │                                   │            │      │
│  flange ●│           3D viewport             │   Metrics tabs    │  inspector
│ Assembly │        (HUD top-left)             │                   │
│  nozzle_1│                                   │                   │
├──────────┴───────────────────────────────────┴───────────────────┤
│ Agent  ▲                                                         │  chat dock
└──────────────────────────────────────────────────────────────────┘
```

Everything is live: parameter edits, script saves, agent tool calls, and
external MCP clients all publish the same WebSocket events, so every pane
updates no matter who made the change.

## Toolbar

**Project switcher** — the button showing the current project name. The menu
lists every known project with its part count, plus:

- **New project…** — prompts for a name matching `[a-z][a-z0-9_]{0,39}` and
  creates it under the projects directory.
- **Open by path…** — registers an existing project directory (one
  containing `project.json`) by absolute path, e.g. a checkout somewhere
  else on disk.

The last opened project is remembered (localStorage) and reopened on the
next visit.

**Branch switcher** — the button next to it, showing the branch you are on
(`master` for a project that has never branched). It appears only when the
server has `git` on its PATH; without git the app behaves exactly as it did
before branching. The menu lists every branch with the relative time of its
last change, marks the current one and the default, and ends with **New
branch…**, **Merge into…** and **Versions…**. Details in
[Branches, versions and merges](#branches-versions-and-merges) below.

**Proposals** — the button after the branch switcher, with a badge counting
the open proposals in this project. A proposal is a *CAD pull request*: a
branch packaged as a reviewable change with an argument and kernel-computed
evidence. Like the branch switcher, it appears only when the server has `git`.
Details in [Change proposals](#change-proposals) below.

**Rebuild indicator** — a spinner labeled `Rebuilding <part>…` (or
`Rebuilding N parts…`) appears while any rebuild is in flight, driven by
`rebuild_started`/`rebuild_finished` events — including rebuilds an agent
triggered.

**Undo / Redo** (↩ / ↪) — step backwards and forwards through the project's
mutation history: parameter changes, instance moves, script saves, part
add/delete, material, mate, and PMI edits. The history is **shared with the
agent** — one Cmd+Z can revert a change the chat agent (or an MCP client)
just made. A toast names what was undone. Keyboard: **Cmd+Z** / **Ctrl+Z**
and **Shift+Cmd+Z** / **Ctrl+Y** — except inside the code editor or a text
field, where the editor's own text undo keeps working. The snapshots
themselves live in the per-project git history (durable, see below); the
undo/redo *stacks* are per-server-session, so after a restart one step back
remains available and redo starts empty.

**Fit** — reframes the camera on the current content, keeping the viewing
direction. Keyboard: **F**.

**Export menu** — two sections:

- *Part* (STEP / STL / 3MF): exports the **selected part** at origin.
  Disabled when no part is selected.
- *Assembly* (STEP / STL): exports all placed instances as one file.
  Disabled when the project has no instances.

Exports are written to `<project>/exports/<part>.<format>` (assembly:
`exports/assembly.<format>`); a toast shows the full path and size. Nothing
is downloaded through the browser — the file lands on disk next to the
project.

**Theme switcher** — the ☀/☾ button. Toggles between the dark (default) and
light themes; the whole UI switches, including the 3D scene and the code
editor. The choice is remembered (localStorage) and restored before first
paint on the next visit.

**Connection dot** — far right. Green means the WebSocket event stream is
connected; gray means the UI is reconnecting (it retries with backoff and
resyncs the project when the connection returns).

## Sidebar

### Parts

One row per part, showing its label (hover for id and material). Click to
select — the viewport, all three inspector tabs, and the HUD follow the
selection. Rows are focusable; Enter selects too.

Build-state dot on the right of a row:

- **pulsing amber** — a rebuild for this part is in flight;
- **red** — the last rebuild failed (hover: "Last rebuild failed"); the
  details are in the inspector's error banner;
- **no dot** — built fine (or not built yet this session).

A part that declares [configurations](#configurations) also wears a small
**badge**: the configuration it is currently showing, or `cfg` at base (hover
for `N configurations · active: …`).

**＋** in the section header creates a part: you are prompted for an id
(`[a-z][a-z0-9_]{0,39}`), and the part starts from the default template — a
parametric rounded plate with four parameters — so there is immediately
something to see and edit. New parts default to Aluminum 6061; material is
part of the manifest and can be changed via the agent tools
(`update_part_script` with `material=`) or by editing `project.json`.

**×** appears on hover and deletes the part *and its script file* after a
confirm. Deletion is refused (conflict) while any assembly instance still
references the part — remove the instances first.

### Assembly

Click the **Assembly** header to switch the viewport to assembly mode.
Below it, one row per instance: a color swatch, the instance id, and the
part it references. Clicking an instance selects it in the viewport *and*
loads its part into the inspector, so you can tune parameters while looking
at the whole machine.

Instances (position, rotation, color) are edited through the agent tools
(`set_assembly`) or by editing `project.json` directly — the v1 UI displays
and selects them but has no drag-to-place editor.

An instance of a part that declares [configurations](#configurations) shows
`part@config` instead of the bare part name, and the placement card gains a
**configuration picker**: choose which size of that part this instance *is*.
The binding is per instance, so two instances of one part can be two sizes on
stage at once. Leave it on the part's live state to keep today's behaviour —
that instance then follows whatever the part is currently showing.

### Patterns, sub-assemblies, joints and URDF (v2 structure)

The sidebar groups structure so a big assembly stays legible:

- **Patterns.** A repeat pattern — a bolt circle, a row of standoffs — is one
  sidebar row with a `×N` badge; click the disclosure triangle to expand its
  members. Declare one on the placement card's **Pattern** section (linear or
  polar, a count, and a spacing in mm or an angle in degrees) or with the
  `set_pattern` agent verb. The pattern is a single manifest change, but mass,
  interference and the geometry all recount to N members from it.
- **Sub-assemblies (assemblies of assemblies).** Instance one project inside
  another: it appears as one read-only row naming its source (with an **open**
  affordance to jump to the source project), and its parts flatten into the
  parent under `<unit>/<member>` ids. The source is resolved **read-only** —
  opening a parent never edits or rebuilds the child's authored state. A source
  chooses which of its connectors are matable from outside via its **interface**
  (`set_assembly_interface`); only exported connectors are reachable, and a
  cross-project cycle is refused with the offending path.
- **Slider and planar joints.** Beside rigid/revolute/cylindrical mates, a part
  can declare `slider` (one linear DOF) and `planar` (u/v slide + spin)
  connectors. A mated slider/planar instance shows editable **DOF fields** on the
  placement card. Drive a value past the connector's declared range and it is
  **clamped** to the limit with a warning — the DOF never throws.
- **Scale rendering.** In assembly mode a **Full / Simplified** toggle (bottom
  right) switches between exact per-instance meshes and one convex-hull proxy per
  part, instanced for thousands of bodies; a HUD shows instance and geometry
  counts. The proxy is display-only — mass and interference always measure the
  real solid. (An **Explode** slider sits beside the toggle but is a disabled
  preview of a later phase.)
- **URDF export.** `export_urdf` writes a robot description plus one mesh per
  link under `exports/urdf/<name>/`: rigid mates become `fixed` joints, revolute
  `revolute` (with limits), slider `prismatic`; link inertia is shifted to each
  part's centre of mass. Planar/cylindrical/ball joints and couplings degrade to
  `fixed` with a named warning in the result — nothing is dropped silently.

## Viewport

CAD orientation: **Z up**, millimeters, a ground grid in the XY plane that
rescales itself to the content. Rendering is shaded geometry plus B-rep edge
overlays, so the display reflects real face/edge structure, not just a
triangle soup.

Mouse (standard Three.js OrbitControls):

| Input | Action |
|---|---|
| Left-drag | Orbit |
| Right-drag | Pan |
| Scroll / middle-drag | Zoom |
| **F** or the Fit button | Fit view to content |

Two display modes:

- **Single-part mode** (a part is selected in the sidebar): that part alone,
  at the origin, in neutral gray.
- **Assembly mode** (Assembly header or an instance is selected): every
  instance placed with its `position`/`rotation_deg` transform and its
  color. **Click a body to select its instance** — the sidebar row
  highlights and the selection gets an amber tint. A small drag threshold
  keeps orbiting from registering as a click. Picking is assembly-only;
  in part mode clicks just orbit.

The **HUD** (top-left) shows the current part or instance name, the mode,
the on-screen triangle count, and a state word: `ok`, `building…`, or
`error`.

If a part's script is broken, the viewport keeps showing that part's **last
good geometry** from the session rather than going blank — the red dot,
HUD state, and error banner tell you the mesh on screen is stale.

## Inspector

Four tabs on the right: **Parameters**, **Code**, **Metrics** and
**Threads** (review comments — see [Review threads and
presence](#review-threads-and-presence)). The error banner (below the tabs)
belongs to all of them.

### Parameters

Controls are generated from the script's `PARAMS` spec:

- a **slider** when the spec has both `min` and `max` (with a sensibly
  rounded step), plus a **number field** always; the unit and description
  from the spec are shown alongside.
- Edits are debounced (250 ms) and patched live — release a slider and the
  rebuild is usually done before your eyes get there. Values are persisted
  into `project.json` as parameter overrides; the script's defaults are
  untouched.
- Values outside `[min, max]` are **clamped, not rejected** — the rebuild
  succeeds and a `⚠ param <name> clamped to max <value>`-style warning
  appears under the controls.
- A part with no `PARAMS` shows a note telling you to define one in the
  script.

**The configuration bar.** A part that declares a family (see
[Configurations](#configurations)) gets a bar above its parameters:

- a **switcher** listing `base` plus every declared configuration by its
  display label — pick one and the parameters, the metrics and the viewport
  all become that configuration's;
- **provenance marks** on the parameter rows: a left rule says a value came
  from the active configuration, a second, stronger one says you have typed
  over it. A row can be both (the family declares it, you changed it), and the
  mark you see is the value actually in effect;
- the **divergence chip** — `M — modified`, hover for which parameters moved —
  which appears the moment you edit a parameter on top of an active
  configuration, so nobody ships an undeclared parameter set unknowingly.
  **Reset to M**
  beside it removes every override in one step;
- a **Matrix** button opening the family table (below).

A part with no configurations shows none of this: no bar, no marks, no badge.

**Design-spec chips.** Under the parameter warnings, one chip per design spec
the script declares (`SPECS` — see
[part-authoring.md](part-authoring.md#design-specs-specs)), coloured by status:
green **pass**, red **fail**, red **error** (the check itself broke, e.g. a
predicate that raised), grey **skip** (it could not be measured here — a
deferred assembly check, a missing `[fem]` extra). Hover a chip for the
measurement: `2 mm vs min 2.5 mm · ENG-014 · min wall 2 mm is below the 2.5 mm
minimum`. They update live on every rebuild, so dragging the `wall` slider
below its minimum turns the chip red as the geometry lands — **a failing spec
never blocks the edit**; it is information until a proposal tries to merge.
A red strip adds one summary line (`1 failing, 1 errored of 6 design specs`);
a part that declares no specs shows nothing at all (no header, no empty note).

Project-scope specs (clearances, interference, stack-ups) live in the
project's root `specs.py`. There is no panel for them yet — edit the file
directly, or through `set_project_specs`, and read the verdicts with
`run_specs`; a proposal shows them in its `specs` gate.

### Code

The part's script in a CodeMirror editor (Python highlighting, line
numbers, Tab indents 4 spaces). The footer shows the dirty state
(`unsaved changes — ⌘S to save` / `saved`), and **Save & Rebuild**
(**Cmd+S** / **Ctrl+S**, from anywhere in the app) writes the script and
triggers a rebuild — the button reads `Rebuilding…` until it returns.

The failure loop is the core workflow:

1. Save a broken script — the script **is** persisted (your edit is never
   thrown away), but the rebuild fails.
2. The **error banner** opens with the error type in the title
   (`script_error`, `contract_error`, `kernel_error`, `timeout`,
   `kernel_crash`) and the traceback with the failing line number in the
   body. The sidebar dot turns red; the last good geometry stays on screen.
3. Fix the line, Cmd+S again. On success the banner clears, the mesh
   refreshes, and a `Saved — rebuild ok` toast confirms. If you changed
   `PARAMS`, the Parameters tab rebuilds its controls from the new spec.

**×** dismisses the banner; it will not resurrect for the same error, only
for a new one. Switching parts clears it.

**When the sandbox or a resource cap is what stopped you**, the banner is the
same banner and reads the same way — that is deliberate. A script that opens a
socket, writes outside the project, forks past the process cap or asks for more
memory than the worker may have fails as an ordinary `script_error`, with the
traceback and the failing line, plus a hint naming the refusal in words
("network access is blocked in the kernel sandbox…"). Your script is saved and
the last good geometry stays on screen, exactly as for a typo. If the worker
was *killed* instead — a runaway allocation is the one case that does that —
the title is `kernel_crash`, the details say `memory_cap` and which mechanism
answered, and they carry what the request had cost; the worker restarts by
itself and the next rebuild works. Running out of *processes* never kills it:
that one comes back as the ordinary `script_error` above. A project that has filled its disk budget is refused *before* anything
is written, with the used and allowed megabytes in the message. None of this
needs configuring for local use; the operator-facing knobs are in
[deployment.md](deployment.md#confinement-and-quotas).

### Metrics

Real measurements from the B-rep, refreshed on every successful rebuild:

| Row | Meaning |
|---|---|
| Volume | Solid volume, mm³. |
| Mass | Volume × the part material's density (g, auto-shown as kg above 1000 g). |
| Area | Total surface area, mm². |
| Bounding box | Axis-aligned extents, X × Y × Z mm. |
| Center of mass | X, Y, Z in the part's own frame, mm. |
| Validity | OCCT's `is_valid` check on the shape — `invalid` geometry may still render but will misbehave downstream (booleans, exports). |
| Faces / Edges / Solids | B-rep topology counts. A "one part" that reports 2 solids is usually an accidental disjoint union. |

If the last rebuild failed, the table stays populated from the last good
build with a `stale — last rebuild failed` note at the top.

## The Agent panel

The **Agent** bar at the bottom expands into a chat dock (click the header;
the open/closed state is remembered).

**Without `ANTHROPIC_API_KEY`** the panel stays functional as a signpost: it
explains that chat needs the key set before launch and gives the exact
`claude mcp add agentcad …` command for driving AgentCAD from Claude Code
instead. Everything else in the app works normally.

**With the key** (set in the environment before `make run` /
`agentcad serve`) it becomes a full tool-using assistant with the same
85-tool surface external agents get ([agent-api.md](agent-api.md)):

- The hint in the header reads `agent works on <project>` — each chat is
  scoped to the currently open project, with a separate in-memory history
  per project (cleared when the server restarts).
- Responses stream in live. Every tool call renders as a **chip** showing
  the tool name and a status that flips from `running…` to `ok`/`error`;
  click a chip to expand the JSON arguments it was called with.
- Because the agent uses the same service you do, you watch its work
  happen: rebuild spinners, mesh updates, new sidebar rows, metric changes
  — all live while the turn runs. A turn is capped at 30 tool calls.
- One turn at a time: while the agent works, Send is disabled and the
  input's placeholder reads `Agent is working…`.

Good first prompts: "make the nozzle wall 4 mm and tell me the new mass",
"create a mounting bracket for a NEMA 17 motor", "run an interference check
on the assembly".

## The v2 capabilities

AgentCAD v2 adds imports, richer materials, assembly mates, 2D drawings, and
geometric analysis. **How you reach them today:** through the Agent panel
(built-in chat), an external MCP client such as Claude Code, or the REST API
directly. These are backend capabilities on the shared service — every change
still flows through the same WebSocket, so the viewport, tree, and Metrics
tab update live as the agent works, exactly as they do for a parameter edit.

The on-canvas controls shipped with them: a transform gizmo on a selected
instance (G/R switch modes; hold Shift to snap 1 mm / 5°), a numeric
transform panel, a material dropdown, the Import button, the in-app drawing
preview, analysis actions in the Metrics tab, the 2D sketcher, and face
push/pull — everything below works both from the UI and through the agent.

**Import existing CAD.** Upload a `.step`/`.stp`/`.brep`/`.stl` (≤100 MB) to
the project's `imports/` directory (`POST /api/projects/<proj>/imports?filename=…`
with the file as the raw body), then ask the agent to *import* it — it becomes
a **reference part**: no script, but it shows in the tree, renders, measures,
and (STEP/BREP) can be booleaned and placed in assemblies. STL is mesh-only
(display/measure; excluded from interference checks). Its `get_part` shows
`kind: reference` and the `source` file instead of a script.

**Materials with properties.** The builtin catalog is now 30 engineering
materials, each with density plus optional modulus, yield/ultimate strength,
CTE, conductivity, service temperature, and cost. Ask the agent to "list
materials" or "set this part to Ti-6Al-4V"; add your own alloys per-project or
machine-wide (`~/.agentcad/materials.json`). Values are typical datasheet
figures, **not design allowables** — the tool says so on every call. Density
drives the Mass row in the Metrics tab as before.

**Assembly mates.** If a part script declares connectors (see
[part-authoring.md](part-authoring.md#declaring-connectors-for-mates)), you
can constrain one instance to another (rigid / revolute / cylindrical) instead
of typing transforms — "mate the bracket's seat to the plate's hole1 at 30°".
The service resolves the mate to a concrete pose, so the instance moves in the
viewport like any other. A mate is authoritative: a mated instance can't be
posed by hand until you clear its mate.

**Motion from mates.** A mate's free DOF can be driven, not just held. Select a
mated instance and the placement card shows a compact *Motion* row: enter a
from/to angle and press **Sweep** to watch the instance swing through its range
in the viewport; the assembly snaps back to its real pose when the animation
ends, and a toast reports either "Motion clear through range" or the first
angle at which something collides. Agents get the same via the `sweep_motion`
tool (angle in degrees for revolute/cylindrical mates, offset in mm for the
cylindrical slide), which re-resolves the mate graph at each sampled value and
boolean-checks every part pair — use it to prove a mechanism clears its housing
before committing to a design. Imported STL instances cannot join the boolean
check and are listed under `skipped_mesh`, exactly as in `check_interference`.

**2D drawings.** Ask for a drawing of a (script) part and AgentCAD projects
front/top/right/iso views with overall dimensions and hole callouts detected
from the geometry, writing `exports/<part>_drawing.svg` (or `.dxf`). A
server-rendered SVG preview is available at
`GET /api/projects/<proj>/parts/<part>/drawing.svg`. For a part with
[configurations](#configurations) the drawing panel adds a **dim table**
checkbox: the sheet then carries a tabulated **dimension table** in its right
column — one row per configuration, columns for the configured parameters plus
the overall X/Y/Z, every number measured from that configuration's own built
geometry (not echoed back from the parameters you typed). A drawing made while
a configuration is active is that configuration's, and saves as
`<part>_<config>_drawing.svg`.

**Geometric analysis.** Ask the agent to measure a cross-section area, the
minimum wall thickness (optionally against a requirement), the projected
silhouette area, or the full inertia tensor. Linear-static FEM is available
only if the optional `agentcad[fem]` extra is installed (otherwise the tool
and its route are absent).

**Sketching & push/pull.** The ✏️ Sketch button (part mode) opens a 2D
sketch editor over the viewport. Draw **points, lines, circles, arcs** (centre,
3-point, or tangent to the chain you are drawing), **ellipses, splines and
slots**; apply constraints (distance, horizontal/vertical,
parallel/perpendicular, radius, coincident, fix, **tangent, symmetric, equal,
concentric**), and watch the constraint solver keep the sketch consistent live.

- **The DOF chip** (top right of the toolbar) reads `fully constrained`,
  `3 DOF`, `over-constrained (n)` (amber — redundant but consistent, which is
  not an error) or `conflicting (n)` (red). Click it to highlight what it
  names: the entities that can still move, or every member of the dependent
  set. The tooltip says plainly that the reported set is *a* dependent set,
  not necessarily the unique culprit — the later constraint is the one blamed.
- **Drag to solve.** With the Select tool, drag any point, arc handle or
  centre: the sketch re-solves every frame, warm-started from the previous
  one, so the profile deforms continuously instead of flipping to a mirrored
  solution. The dragged handle follows the cursor immediately (a ring), and a
  dotted hairline appears when the constraints are holding the geometry back —
  dragging a fully constrained sketch *should* move nothing.
- **Sketch on a face.** Click a planar face and press **Sketch on face**: the
  sketcher opens in that face's plane with the face's own boundary edges
  ghosted as reference geometry you can constrain to (they are fixed, so they
  add no DOF and are never emitted). The inserted code carries the plane's
  basis and the face reference, with the caveat that face indices can be
  renumbered by a topology-changing parameter edit — and reopening a saved
  sketch-on-face **checks** it: if the ordinal now points at a different face
  (a different area or normal), a toast says so with both measurements. It is
  never repaired for you, because which face you meant is not guessable.
- **Insert → script** appends a `sketch_profile()` build123d function to the
  code editor; call it from `build(p)` and save. The block it writes also
  carries the sketch's **constraint spec**, so reopening the sketcher on that
  part loads the sketch back, constraints and all (several blocks in one
  script are offered as a list). Each insert writes a **new** block —
  `sketch_profile`, then `sketch_profile2`, … — and the toast names the
  function it just wrote, because two blocks of one name would define the same
  function twice. Nothing you wrote is ever removed for you: delete a
  superseded block yourself when you have pointed `build(p)` at the new one.
- **A sketch belongs to its part.** Switching to another part (or another
  project) starts a new sketch: the canvas, the plane and the block it came
  from are cleared, so Insert can never append one part's profile into
  another's script. Insert first if you want to keep what you drew.
- **The divergence banner.** If you hand-edit the emitted code, reopening
  shows a red banner: the code no longer matches the saved spec. The sketch
  opens **read-only** — every tool, every constraint button and every
  constraint chip is disabled — with two explicit choices: *Re-solve from the
  spec* (edit the constraints again; inserting writes a new block and leaves
  your edit in place) or *Discard the spec* (keep your code exactly as it is and
  drop the constraints). Nothing is ever silently overwritten.

Clicking a face of a part also opens a small face card (area, normal) with a
push/pull distance — applying it records the edit *in the script* as a
visible, editable `push_face(...)` wrapper, so direct manipulation never
bypasses the code. Alt+click clears the selection.

**Drilling standard holes from the face card.** The same card carries a *Hole*
section: pick a family (clearance, tapped, counterbore, countersink, or a plain
drilled diameter), a standard (ISO or ASME), a size, a fit and an optional
blind depth, type one or more positions as `u, v` pairs in the face's own
plane (`20, 10; -20, 10` drills two), and press **Drill**. The size list is the
standards table itself — the same numbers `hole_standards` answers with and the
same numbers the geometry uses — so the picker cannot offer a size that does
not exist. Applying appends a visible `holes.clearance(...)` (or `.tapped`,
`.counterbore`, …) call to the script and rebuilds; the hole's *intent* travels
with it, so the drawing callout reads `4× M3×0.5 - 6H ↧8` rather than `⌀2.5`.

One caveat travels in the generated code as a comment, because it cannot be
engineered away: the picked face is written into the script as its **plane**
(origin and basis), not as its index. If a parameter change later moves that
face, the script keeps drilling at the plane you picked — re-pick the face if
the geometry moves.

**Huge meshes.** Heavy parts (over ~150k triangles) appear almost instantly
as a coarse preview while the full-resolution mesh streams in behind it;
small parts load in a single request exactly as before.

**Undo & project history.** Every change you or an agent makes — scripts,
parameters, assembly, mates, materials, PMI — is snapshotted into a
per-project git history (`.history/` inside the project folder; derived
`.cache/` and `exports/` are never tracked). Press Cmd/Ctrl+Z or the toolbar
Undo button to roll back to the previous state; agents can jump to any
snapshot with `project_restore`. Restores are themselves recorded as new
snapshots, so history is linear and redo is just restoring the commit you
were on before undoing. Requires git on the server's PATH; without it
AgentCAD works normally, only history is disabled.

**Working alongside agents.** When several agents (or an agent and you) edit
one project at once, an agent can take the editing turn with `acquire_turn`.
While the turn is held, everyone else's changes — including edits made from
the browser UI — are rejected with a clear "project is locked by \<holder\>"
message until the turn is released or its lock expires (default 120 s). The
toolbar shows a lock chip naming the holder whenever an agent holds the turn.
Reads are never blocked, and with no lock held everything behaves exactly as
before. The chat dock is pinned to the default session: when another agent
holds its own chat session on your project, the dock shows a one-line notice
("another agent session is active: …") instead of mixing its stream into
yours.

**Seeing the model.** Agents can now look at what they build: the `render_view`
tool rasterizes the built mesh to a shaded PNG entirely server-side (no GPU),
either a single part or the whole placed assembly with instance colors. Views
match the drawing pack (iso, front, top, right). The image is written to
`exports/renders/` and returned as real image content over MCP and in the
built-in chat, so a vision-capable model can check proportions, hole placement,
and assembly layout instead of reasoning from numbers alone. The same render is
available over HTTP via `POST /api/projects/<proj>/render`.

## Branches, versions and merges

A project is a real git repository (`<project>/.history/`), and the toolbar
exposes it as branches you can work on, versions you can name and return to,
and merges that the kernel validates before they land. Everything here is
equally available to agents (see
[agent-api.md](agent-api.md#branches-versions-and-merges)) — a human can pick
up an agent's branch, and vice versa.

**The branch switcher.** The menu lists each branch with its last-change time
(hover for the commit subject); the current one is highlighted and the default
is labelled. Picking a branch switches **you only**: branches are per client,
so the browser, the chat agent, and an MCP client can each sit on a different
branch of one project at the same time, each with its own editing turn and
undo stack. Switching is instant — every branch keeps a materialized working
tree, so nothing is checked out and nothing rebuilds — and the viewport, tree
and inspector reload from the branch you moved to. Unsaved editor changes
prompt first, as everywhere else.

- **New branch…** prompts for a name matching `[a-z0-9][a-z0-9_/-]{0,63}`,
  forks it from the branch you are on, and switches you to it.
- **×** on a branch row deletes that branch and its working tree, after a
  confirm. It appears only where the server would allow it — never on the
  default branch or the one you are on — and a branch someone else has checked
  out comes back as an error toast. Versions (tags) made on the branch survive
  it, and its working tree is committed before removal, so nothing uncommitted
  is silently thrown away.

**Versions… (the versions dialog).** A version is an immutable named state —
"the revision we sent to the machine shop" — stored as an annotated git tag.
The dialog lists them newest-first with the message, author, relative date and
short commit, and gives each a **Restore** action (it restores that state onto
your current branch as one undoable step). **Tag current state…** prompts for
a name (`[a-z0-9][a-z0-9._/-]{0,63}`, so `v1.2` works). Versions cannot be
moved or deleted, and they outlive the branch they were made on.

**Merge into… (the merge modal).** Pick the source branch (*theirs*, what you
merge from) and the target (*ours*, what you merge into — your current branch
by default), then **Merge**. Three outcomes:

1. **Clean.** The modal shows the post-merge report: the merge commit and its
   two parents, how many conflicts were resolved, which parts rebuilt, and the
   interference result. The project reloads live (a `merge_completed` event
   reaches every open client).
2. **Conflicts.** The modal becomes the conflict view: a left rail listing
   each conflicted part script or manifest key, and a right pane showing
   either the conflict-marked script (read-only CodeMirror, with the
   `<<<<<<< ours / ||||||| base / >>>>>>> theirs` sections labelled by branch)
   or a base/ours/theirs value table for a manifest key. Per conflict: **Use
   ours (target)**, **Use theirs (source)**, **Use base**, or **Edit…** to
   author the merged text by hand and **Save edit**. Each pick posts
   immediately, so partial resolution is real — the footer counts "N of M
   resolved" and **Complete merge** enables at zero outstanding — unless the
   merge was staged by a *proposal*, which holds it: the footer then says
   "held by proposal:N" and the button reads **Complete in the proposal**,
   because completing it here would land the change without re-checking that
   proposal's gates. **Abort
   merge** throws the staged merge away. Until the merge completes, *nothing*
   on either branch has changed: the merge is staged, and reloading the page
   (or restarting the server) reopens the conflict view where you left it.
3. **Blocked by validation.** Before a merge lands, the kernel rebuilds the
   merged state: changed parts build, mates re-resolve, and interference is
   re-checked. If the result would break a build, strand an assembly instance,
   or **introduce** a new interfering pair, the merge is refused and the same
   report is shown as blocked, naming the failing part or the overlapping
   pair. Fix the source branch and merge again, or use **Land anyway
   (allow_invalid)** — the failures are then recorded in the merge commit
   message.

Only *newly introduced* interference blocks: a project that already overlaps
stays mergeable. Merges of very large assemblies skip the pair check (above 40
instances) rather than spending minutes on it.

**What merges how.** Part scripts merge as text, like any Python file, so two
people editing different functions of one script merge cleanly and only real
overlaps conflict. `project.json` never merges line-wise — it is re-merged
key by key (per part, per parameter, per instance, per material, per PMI
section), so "A rewrote the flange script while B changed its bolt diameter"
lands both. A merge is one entry in the project history with both parents:
`git log --graph` in the project directory shows exactly what happened, and
Undo (Cmd+Z) after a merge takes the target branch back to its pre-merge
state.

**Working outside the app.** The project stays a plain git repository — clone
it, `git log`, `git diff master..flange-weld`, or check out a version tag with
your own tools. Derived data (`.cache/`, `exports/`) is never committed.

## Change proposals

A merge lands a branch. A **proposal** is the decision point in front of it:
"here is what I did, here is why, and here is what the geometry actually did —
approve it or push back." It is the surface a human supervises an agent
through, and the same object an agent uses to hand work over
([agent-api.md](agent-api.md#change-proposals)). Open it with the **Proposals**
toolbar button; the badge counts what is open.

**The list** (left). Filter chips across the top — `all N`, then one per state
in use (`open`, `approved`, `changes_requested`, `merged`, `closed`). Each row
shows a state dot, `#id`, the title, `source → target`, the author with a
**human/agent badge**, the age and the review count. **New proposal…** opens an
inline form: source branch, target (defaulting to the project's *default*
branch, not the one you are on — a proposal is read by other clients), title,
description and a *draft* checkbox. Write the description for the reviewer: the
packet says what changed, you say why it is right.

**The header** (right). State chip, `source → target (new → old)`, the author,
the merge commit once merged, and the actions: **Approve**, **Request
changes**, **Comment**, **Merge**, **Close**/**Reopen**, **Edit…** and
**Regenerate packet**. Merge is disabled while a gate is red, with the failing
gate's reason in its tooltip — a hint only; the server refuses regardless, so
nothing lands because a button was enabled.

**The five tabs** are the review packet, generated on first view (the spinner
says so; a cold packet builds both sides) and re-served until either branch
moves.

- **Overview** — the description, when the packet was generated, and per
  changed part a metric-delta table (volume, mass, area with Δ and %, the
  per-axis centre-of-mass shift, both bounding boxes) plus the **before/after
  render pair**. The pair shares one camera frame, so the two images are
  superimposed: **hover to cross-fade old → new**.
- **Files** — the unified script diff, line by line with hunk headers, plus a
  PARAMS diff table (parameters added, removed, and each changed value as
  old → new).
- **Geometry** — per part the kernel-computed **removed** and **added** mm³,
  with **Show in viewport**: it closes the modal, selects the part and overlays
  the diff solids on the real geometry — translucent **red for removed**,
  **green for added** — with a legend in the viewport corner and a **Clear**
  action. The overlay is drawn over the target build and disappears on a part
  switch or a rebuild.
- **Checks** — the merge gates (state, approvals, kernel validation, the
  **design specs** of the source branch, and the **geometry CI** verdict posted
  to this proposal) with pass/fail/pending/skipped chips, the reviews with
  their verdicts, and — after a merge the kernel blocked — the full validation
  report with a **Merge anyway (allow_invalid)** button.
- **Audit** — the append-only log: sequence, timestamp, actor and whether it
  was a human or an agent, action, details. It cannot be edited from anywhere.

**Failures are shown as evidence, not as errors.** A side that does not build
prints its script error above that part's metrics with the rest of the packet
intact; an impossible geometric diff prints its reason (an imported STL part
reports `skipped: mesh`); a mesh part reports `n/a (mesh)` for centre of mass,
because a bbox centre is not a mass property. That is the packet working
correctly — "the new side does not build" is the most useful review comment
there is.

**Approvals.** By default a proposal needs **one approval that is not the
author's**. Approving your own proposal never satisfies that, which is what
makes the flow meaningful when the author is an agent. **Merge anyway
(allow_invalid)** overrides the *kernel validation* gate only — never the
approvals policy and never the design-spec gate — and is recorded in the audit
log and the merge commit message.

**The design-spec gate is fail-closed.** It evaluates the source branch's
declared specs, and it is red when any of them fails, errors, *or could not be
evaluated at all* — an unmeasured spec is not evidence of green. The gate's
summary names the failing checks (or, when nothing could be measured, tells you
to run `run_specs` on that branch). A proposal that edits `specs.py` rather
than the geometry is flagged there too, since the packet's part rows cannot
show it.

**The geometry-CI gate is opt-in, then fail-closed.** Nobody has to run CI on a
proposal — until someone does. Run `agentcad check --proposal <id>` (or
`--auto-proposal` on the source branch, or `run_checks {proposal}`) and the
whole-project verdict is attached to the proposal permanently: the report, who
posted it, and the commit it measured. From then on the **checks** chip is
green only while that report is *complete*, *green* and certifies the source
branch's **current** head. A red report, a run whose budget ran out, an
unreadable one, or one that certifies a commit the branch has since moved past
are all a red chip that blocks the merge, with a summary telling you to re-run
and post again. There is deliberately no "pending" middle state: a merge is
blocked by a red gate and nothing else, so a soft state would let a stale green
wave through commits nobody measured. Before any report is posted the chip is
simply *skipped* and blocks nothing. See
[geometry-ci.md](geometry-ci.md).

**Conflicts.** If the merge conflicts, the proposals modal hands off to the
usual conflict view on a staged merge; resolve it there. That merge is **held
by the proposal**: resolving the last conflict records your choices but lands
nothing, and the conflict view says so — its Complete button reads *Complete in
the proposal*. Go back to the proposal and merge it again. That is not
ceremony: landing the merge from the conflict view would skip the gates
entirely, so a proposal someone set back to *changes requested* while you were
resolving would merge anyway. Merging the proposal re-checks the gates first
and then finishes the staged merge, keeping the override it was staged with.
Aborting the staged merge instead leaves the proposal exactly where it was.

**If you decline "Merge anyway", finish the staged merge.** A merge the kernel
blocks leaves the staged merge in place (ordinary `merge_branch` behaviour), so
the next page load reopens the *merge* modal over everything. Either complete
it there or **Abort merge**, then fix the source branch and merge the proposal
again.

Proposals are workflow metadata, not model state: they live in
`<project>/.history/agentcad/proposals/`, outside every working tree, so they
are the same on every branch and **Undo / Restore never rewinds them**.

## Review threads and presence

Feedback that points at something. A **thread** is a comment plus replies,
anchored to a part, a face, a parameter, a script line range, an assembly
instance or a proposal diff hunk, with a state of *open* or *resolved* — and an
agent sees exactly the same threads you do, as a work queue it can list, answer
with a render attached, and resolve.

### The Threads tab

A fourth inspector tab beside Parameters, Code and Metrics. It lists the
project's threads with a breadcrumb of what each one points at, filters for
**open / resolved / all** plus a separate **orphaned** count, a composer,
replies, and Resolve / Reopen. **Show** jumps to the thing: a face selects the
part, highlights the face and fits the camera; a line range opens the Code tab
at those lines; a param scrolls the Parameters pane and flashes the row; an
instance selects it in the tree; a hunk opens that proposal's Files tab.

Every row carries a **status chip**, and the four states are four different
facts:

| Chip | Means |
|---|---|
| `ok` | still points at what it pointed at |
| `moved` (amber) | re-matched at a **new** address, shown as `bracket · L22–23 → L24–25` |
| `orphaned` (red) | the target is gone, or nothing cleared the tolerance |
| `unverified` (dashed) | **not checked** — the part is not built, git is absent, the packet is frozen |

`unverified` is never a synonym for "fine". An `orphaned` or `unverified`
thread is still a thread — readable, listable, resolvable — but **Show** is
disabled and says why, because there is nowhere honest to jump to. The status
is recomputed on every read; the anchor you created never changes underneath
you.

### Pointing at things

- **A face.** Click a face in the viewport and press **Comment** on the face
  card. Open face threads then draw a numbered **pin** over the model, which
  follows the face as parameters change — and disappears when the anchor stops
  being `ok`/`moved`, rather than floating over the wrong place. A pin is only
  ever drawn once the browser has located the face on the geometry *currently
  on screen*; in the moment between a rebuild and that data arriving there is
  no pin at all, because the only other position available is where the face
  used to be, and a pin you cannot tell apart from a located one is worse than
  no pin.
- **Script lines.** Select lines in the Code editor and comment; the thread
  gets a marker in the editor **gutter**, which moves when you insert lines
  above it. The snippet is re-found by its text *and* the lines around it: if
  the same line exists twice and the commented one is deleted, the thread does
  not silently jump to the other copy — it falls back to a real diff of the
  script, and orphans if that cannot place it either.
- **A parameter.** A count **badge** on the parameter row.
- **A proposal's diff hunk.** Hover a diff line in a proposal's Files tab; the
  hunk header then carries a count chip.

A face anchor survives a parameter tweak when the face stays where it is
relative to the shape's bounds, and honestly says `orphaned` otherwise —
including for faces that still exist but moved within the bounds, and for
closed curved faces (a cylinder's side), which orphan on any edit. **Orphan
rather than guess**: a comment pointing at nothing is recoverable, a comment
pointing at the wrong face is not — so the matcher refuses a match it cannot
support, including one that only looks certain because no other face was left
to compare it with. Two things were measured, and they are different numbers.
Across a **parameter change** (2 693 faces) about half resolve, the rest
orphan, and **2 pointed at the wrong face**. Across a **deleted feature** (327
faces that no longer exist) 99% orphan and **4 re-pinned onto the surface that
was underneath** — all four a square pad on a square plate, where the face left
behind has the same shape, the same place and nearly the same size as the one
you deleted. So a pin can survive onto the wrong face after you delete
something: rare, not impossible, and worth a glance before you act on it.

One ceiling is worth knowing because it looks like a bug and is not: when no
other face on the part is even a candidate — a lone face at that orientation
and position, which is the common case for a boss top or a pocket floor — the
match rests on size alone, so an edit that moves that face's **share of the
part's surface area** by more than about 30% orphans the thread. Widening a
boss a little keeps the pin; nearly doubling it does not. That is deliberate:
the same "only candidate left" reasoning is what used to move a comment onto
the face *underneath* one that had been cut away.

### Who else is here

The toolbar shows an **avatar** per other client in the project — a browser
tab, a chat session, an MCP agent — with its label and what it is looking at,
refreshed every 15 seconds and dropped 45 seconds after a client goes quiet.
Part rows in the tree show a dot for someone looking at that part.

**Claims.** When somebody is *editing* a part (a dirty editor buffer, or a
parameter being changed) that part shows an **"<name> is editing"** chip, and
your write to it comes back as a conflict dialog naming them, with an
**Override** button. Override arms one single-use 30-second override and
retries your save; the override is announced to everyone, so taking a part is
on the record. It really is single-use: it is spent by that retry whether or
not the other person was still holding the part when it landed, so if they
pick the part back up a moment later your next save asks you again rather than
taking it silently. Claims are per part — somebody editing `bracket` never blocks
your work on `nozzle` — they last 90 seconds, they are dropped the moment
editing stops, and they apply **between people only**: an agent is never
blocked by your open editor. The project-wide **turn lock** is the other
mechanism and it still decides first: while an agent holds the turn, every
write fails exactly as it always did, and no override is offered.

### Mentions and the inbox

`@` an identity in a comment — `@chat:main`, `@browser:1a2b3c4d` — and it lands
in that client's **inbox**, the toolbar button with the unread badge. Clicking
a row opens the thread and marks it read. `@todo` and `@nobody` are not
identities: they stay plain text and deliver nothing.

### Upgrading: your browser gets a new identity once

Presence needs each browser to *have* an identity, so this release mints one
per browser profile (`browser:<8 hex>`, kept in `localStorage`) where every
browser previously sent the single shared id `browser`. On first load after
upgrading, an existing browser is therefore a **new client** to the server, and
two things follow, both one-time and neither destructive:

- **Your per-client branch checkout is gone.** Branch checkouts are keyed by
  client id, so a new id has no row and lands on the project's default branch.
  Re-check-out the branch you were on; nothing on any branch was touched.
- **A turn you were holding is held by your old identity.** You cannot release
  a turn you no longer claim to be, so wait out its TTL (two minutes by
  default) or restart the server, and take the turn again.

Comments, history and attributions written under the old id keep it: `browser`
stays a valid identity to read, mention and filter by.

### What this is honestly not

Identity here is **self-asserted**. A client id is a header, not a login, so
attribution, mentions, presence and claims are coordination and bookkeeping —
not authentication and not access control. Presence is **ephemeral**: it lives
in the server's memory and is gone on restart. And every notification is
broadcast to every connected client and filtered in the browser, so on this
single-user, 127.0.0.1-only server your inbox is visible to anyone already on
the machine. Real principals arrive with PRD-005.

Three gaps are deliberate rather than pending bugs: an **assembly instance**
anchor has no create affordance in the UI (agents and REST can make one, and
the panel focuses it correctly); comment **attachments** have no browser file
picker (an agent attaches a render from `exports/`, and the panel renders the
chip); and **Cmd+Z is always the shared undo** — `scope: "mine"`, which takes
back only your own last edit, is an API-level capability with no toolbar
gesture, because a human taking back the agent's edit with Cmd+Z is the
interaction the button exists for.

Threads are workflow metadata, not model state: they live in
`<project>/.history/agentcad/comments/`, outside every working tree, so every
branch sees the same list and **Undo / Restore never rewinds them**.

## Configurations

Most real parts are a family, not a part: an enclosure in S/M/L, a bracket in
left and right, a flange with three bolt counts. A **configuration** is a
named, validated set of parameter values on one part — and it is ordinary,
reviewable data in `project.json`, not a hidden mode.

**Declaring a family is done through the tools or the chat**, not through a
dialog: "give the flange three sizes — small at ⌀100, medium at ⌀140, large at
⌀200" (`set_part_configs`). Names are lowercase (`s`, `m`, `l`); the display
name you see in the switcher is each configuration's `label`. The browser is
where you *use* a family, and everything it does is described above: the
switcher and the divergence chip in the Inspector, the badge in the sidebar,
the picker in the placement card.

**Switching loads the configuration.** Picking `M` shows M's parameters, M's
metrics and M's geometry — and it *clears* any parameters you had typed over,
because a half-loaded configuration with invisible leftovers is the failure
mode this feature exists to prevent. Type over a value afterwards and the chip
says `M — modified`; **Reset to M** takes you back. (Re-picking the
configuration that is already active changes nothing, so you cannot lose an
edit to a stray click.)

**Two rules that differ on purpose, and both are deliberate.** A parameter you
drag past its limit is *clamped* with a warning — that is a live edit, and
stopping you mid-drag would be worse. A parameter in a **declared**
configuration that is out of range is **refused**: a family is a published
thing, and a size nobody can build should not be sitting in the manifest
looking legitimate. The refusal names every problem in the map at once, and
nothing is written until the whole map is valid.

**The Matrix** (the button in the configuration bar) builds the whole family
in one go and shows a row per configuration: mass, volume, bounding box, and
the design-spec chips when the part declares any. A member that fails to build
is a red row **in place** — you still see the rest of the family, which is the
point of asking about all of them at once. Members with identical parameters
cost one build, and a second look is served from the cache.

**Configurations reach everything downstream.** An export made while one
is active writes `<part>_<config>.step`; a drawing writes
`<part>_<config>_drawing.svg` and can carry the family's dimension table; an
assembly instance can be *bound* to a configuration, so two sizes of one part
stand on the stage with two masses and two meshes; `agentcad check` builds
every configuration of every configured part, so a change that breaks only
size XL is caught before merge. And a configuration an instance is using
cannot simply be deleted — the removal is refused, naming the instances that
would have been left pointing at nothing.

**A per-configuration file is the configuration *as declared*.** It is
resolved purely (defaults, then the configuration), so parameters you have
typed over on top of it — the "modified" chip — are deliberately not in
`flange_l.step` or `flange_l_drawing.svg`; a file named after a configuration
that quietly contained someone's unsaved slider drag would be the worse
surprise. Return the part to base if what you want exported is the working
state. While the part is modified the browser says so, in the export toast and
in the drawing preview's title.

Agents get the same surface as one call each — `set_part_configs`,
`list_configs`, `build_configs`, `set_active_config`, `set_instance_config` —
documented in [agent-api.md](agent-api.md#configurations). A part that
declares no configurations is completely unaffected: same manifest, same
caches, same UI.

## The Parts library

The **Library** button on the toolbar opens the parts library: search a
catalog of versioned, kernel-validated packages and drop a real part into
your project.

**What you see.** Type into the search field (`cap screw`, `bearing`, `2020`,
`nema`) and the hit list fills from every configured index, each hit with its
rendered preview thumbnail, its version, its index and a `why` saying which
part of it matched. Pick one and the detail pane shows the preview, the
**disclosure badge** (`human`, `agent` or `hybrid` — who authored the
geometry), the licence, the standards it claims, the declared parameter table
with each parameter's range and unit, and the connectors it ships. A preset
picker lists the configurations the package publishes — `m5x16`, `b608`,
`l40` — and **Add to project** installs the dependency and materialises the
part in one gesture.

**What you get.** An *ordinary part*. The script is copied into your project
under a short provenance header, so the project builds with no cache, no index
and no network — in CI, in a bare clone and in a proposal diff. Edit it,
branch it, review it, undo it: nothing about it is special. The Inspector
shows its parameters and its spec chips like any other part.

**The nine packages that ship with the app** — ISO 4762 cap screws, ISO 4014
hex bolts, ISO 7380 button heads, heat-set inserts, DIN 625 bearings, 2020 and
3030 T-slot extrusion, NEMA 17 and NEMA 23 motor outlines — answer with **no
network and no configuration**. The bearings, extrusions and motors are
*interface models*: the geometry a bracket has to fit and clear, at the
published dimensions, and each package's README names what is not modelled
(a bearing has no balls; do not read a mass off a motor).

**Fasteners have a `thread` parameter.** `cosmetic` (the default) draws the
shank at the thread root, so the screw drops into a tapped hole and
`check_interference` reports nothing. `real` cuts the actual helix and reaches
the nominal diameter, so it *overlaps* the tapped hole — which is what thread
engagement is, not a modelling error. Real threads cost time in proportion to
the number of turns.

> **Packages are code.** A package script runs in your kernel worker with your
> privileges, exactly like any part script you write. The publish gate proves
> that the geometry builds, that the specs pass and that the connectors mate;
> it proves nothing about intent. Install from indexes you trust. The dialog
> says so in its footer, and `docs/packages.md` has the whole trust model.

Adding your own index (a directory or a git repository) and publishing your
own packages is `docs/packages.md`; the short version is
`agentcad package validate ./pkg` until it is green, then
`agentcad publish ./pkg --index <name>`.

## Browsing the catalog (the Marketplace)

The **Market** button on the toolbar opens the marketplace — a full-page,
read-only view over the same seeded, kernel-validated catalog the Library
searches, but built for **browsing and customizing without an account**. On a
hosted instance you can even reach it before signing in (the URL is `/#market`):
browsing, customizing and downloads need no session; only *adding to a project*
does.

- **Browse.** A grid of listing cards — name, summary, license, a disclosure
  badge (`agent`/`human`), a `validated ✓` correctness badge, and a preview
  thumbnail. The search box filters by name, keyword, standard, license or
  parameter range, deterministically, as you type.
- **A listing.** Open a card for its metadata (license, standards, disclosure,
  the validated-gate detail and the `signatures` slot — `unsigned` today), a
  preview strip, a versions selector, the read-only script, and the **customizer**:
  a viewport with sliders. Drag `body_length` and the server rebuilds a bounded
  variant and shows the new mass and bounding box; the part's script runs only in
  our server-side kernel, never on your machine.
- **Download.** STEP, STL or 3MF of the variant you configured — a fixed set for
  every listing.
- **Add to library** (signed-in). Adds the package to one of your projects
  (`add_package`, pinning the public catalog index) and materialises the part
  (`use_part`); the lockfile pins the exact version so it rebuilds byte-identically
  forever. Agents do the same in one call with `market_install`.

The catalog is **seeded and read-only** in this release — the customizer is
PRD-007's exact containment (rate-shaped, concurrency-capped, param-validated),
so a busy instance may briefly show "the customizer is busy" and degrade to
view-only; it recovers on its own. Open publishing, remix and an economy are a
later phase.

> **Packages are still code.** Adding a listing to your project installs a script
> that runs in your kernel with your privileges; the validated badge is a
> **correctness** gate, not a security boundary, and the listing says so.

## Working with the bundled examples

Pick them from the project switcher:

- **rocketry** — a liquid-engine thrust chamber: `nozzle` (Inconel 718),
  `injector_plate`, `flange`, assembled into one stack.
- **construction** — a steel truss gusset node with bolt patterns.
- **prototyping** — a snap-fit electronics enclosure.

A five-minute tour: open **rocketry**, select `nozzle`, drag
`expansion_ratio` and watch the bell and the mass respond; open the Code
tab to see how the profile is a revolved sketch; click **Assembly** and
pick bodies; Export → Assembly → STEP.

**rocketry also ships design specs**, so it is the fastest way to see them
work: the nozzle carries a wall minimum and a mass budget, the flange a
bolt-circle ligament check, and the project's `specs.py` the assembly gaps.
Select `nozzle` and drag `wall` from 3 mm down to 2 mm — the `wall_min` chip
goes red with the measured value in its tooltip, and the geometry still
rebuilds. Drag it back and it goes green. `injector_plate` declares nothing,
so it shows no chips at all.

The examples are working projects, not demos behind glass — they live in
the repo's `examples/` directory and are registered by path, so parameter
tweaks and script edits **write into your checkout**. `git checkout -- examples/`
restores pristine state. Their parameter sweeps are also part of the test
suite, so the shipped defaults always build.

## Signing in (a hosted instance)

Everything above describes AgentCAD on your own machine, where there is no
sign-in at all. An operator can also run one **hosted** instance that a team
shares — `docs/deployment.md` is the operator's guide; this is what changes for
you as a user.

- **You are invited, never self-registered.** An admin runs
  `agentcad admin user add <you>` and sends you a **one-time enrolment link**.
  Open it in a browser, choose a password (8 characters minimum), and you are
  signed in. The link works once; a second visit is a 404, so ask for a fresh
  one rather than reusing an old mail.
- **The identity chip**, top right, shows who the server thinks you are and
  signs you out. Your session survives a browser restart and the server being
  restarted, and it expires after 14 idle days (30 at the outside).
- **Attribution becomes your name.** Lock chips, the presence roster, comment
  authors, proposal actors and history entries read `user:<handle>` instead of
  `browser:7f3a1b2c` — and per-part claims now actually protect you from other
  *people*, while an agent with a token is never blocked by a human's claim and
  cannot take one.
- **Everyone shares one project space.** There are two roles: `member` (read
  and write every project) and `admin` (that, plus managing users and tokens).
  There is deliberately no per-project permission — see the trust note below.
- **Anonymous visitors can see the public parts catalog and nothing else**:
  the package list, per-version metadata and the shipped preview images. No
  project, no part, no geometry, and nothing that runs the kernel.
- **For agents and CI**, an admin mints a bearer token
  (`agentcad admin token add ci`). Point an MCP client at the instance with
  `AGENTCAD_URL` and `AGENTCAD_TOKEN`; revoking the token cuts it off on the
  next call.

> **Trust.** An account on a hosted instance can execute arbitrary Python on
> the server — a part script *is* Python, and worker confinement does not exist
> on Linux yet. So accounts are for people you would give a shell to,
> registration is closed, and `member` versus `admin` is not a wall between
> colleagues. That is a statement of what the software does today, not a
> caveat to skim.

## Sharing a part (a hosted instance)

On a hosted instance you can turn a part into an **unlisted link** that anyone
can open in a browser — no account, no install. A logged-out visitor sees the
model, its metrics and your attribution; a *customizer* link also gives them
sliders that rebuild real geometry within the bounds your PARAMS declare, and a
STEP/STL/3MF of *their* variant.

- **Publish.** Select a part and click **Share…** in the toolbar. In the dialog:
  choose the **version** (a tag by default, so the link never drifts — leaving
  it on "current state" tags a new version for you); turn the **customizer** on
  or off; tick the **download** formats to allow (or none for view-only); choose
  whether the **script** is visible; and optionally an **expiry** in days
  (default: never, until you revoke). Click **Create link** — the URL is shown
  **once**, so copy it then.
- **The URL is the capability.** Anyone with the link can view (and, on a
  customizer link, customize and download). There is no other login. Treat it
  like a shared document link: unguessable, but not secret to those you send it
  to. The page footer says as much to your visitors.
- **Embed it.** `…/embed/<token>` is an iframe-embeddable version of the same
  page — drop it into a forum post or a docs page and the model orbits inline.
- **Watch and revoke.** The dialog's **Active links** list shows each link's
  coarse view/rebuild/download counts and a **Revoke** button. Revocation is
  immediate; a revoked, expired or unknown link all answer the same "no such
  link", so the URL is never an oracle for what you have published.
- **Your work is safe from visitors.** A link pins a *copy* of the script at the
  chosen version, built in an isolated space; editing your working part never
  changes what a live link serves, and no visitor action can touch your project,
  its history or its cache. A visitor supplies slider *values*, never code — the
  same validation the editor uses clamps a number out of range and refuses a bad
  type. A busy link degrades to view-only with a plain "try again shortly"
  rather than an error.

Publishing is also an agent tool (`share_create` / `share_list` /
`share_revoke`) — an agent can drop a share URL into chat or a proposal. See
`docs/agent-api.md`.

## Where files live

| Path | Contents |
|---|---|
| `~/AgentCAD/projects/<name>/` | Your projects (override with `--projects-dir`). |
| `<project>/project.json` | Manifest (schema v2): parts (script or `reference`), a project `materials` section, parameter overrides, and assembly instances with optional `mate` specs. Human-readable, atomically written, diff-friendly; v1 files still load. |
| `<project>/parts/<id>.py` | One plain build123d script per part — the model itself. Edit with anything; the UI picks up saves via its own editor, agents via tools. (Reference parts have no script here.) |
| `<project>/imports/` | Uploaded reference CAD (STEP/BREP/STL) backing `reference` parts. |
| `<project>/.cache/` | Derived data (ACM1 meshes + metrics JSON keyed by content hash). Safe to delete; rebuilt on demand — and **shared by every branch**, since the keys are content hashes. |
| `<project>/.history/` | The project's git repository (snapshots, branches, tags). `git log`/`diff`/`clone` work on it directly. Inside it: `trees/<branch>/` — one working tree per non-default branch — and `agentcad/` — sidecar state (default branch, per-client checkouts, version referrers, any staged merge, and `proposals/`). None of it is ever committed. |
| `<project>/.history/agentcad/proposals/<id>/` | One change proposal: `proposal.json`, the append-only `audit.jsonl`, the generated `packet.json` and its render PNGs / diff meshes. Shared by every branch and never rewound by a restore. `policy.json` beside them holds the project's merge policy (`approvals_required`, `self_approve`). |
| `<project>/.history/agentcad/comments/` | Review threads: `<id>/thread.json` plus its append-only `<id>/audit.jsonl`, an `index.json` that can be rebuilt from the directories, a persisted `next_id`, and one `notifications.jsonl` per project. Branch-free like proposals, and never rewound by a restore. |
| `<project>/exports/` | STEP/STL/3MF part & assembly exports, plus `<part>_drawing.svg`/`.dxf` drawings, from the Export menu, agent tools, or `agentcad export`. |
| `examples/` (repo) | The bundled example projects, registered at startup. |
| `~/.agentcad/config.json` | The persisted port (`AGENTCAD_CONFIG` overrides the path). |
| `~/.agentcad/state/auth/*.json` | **Hosted mode only.** Accounts, enrolments, sessions and tokens — four atomically-written `0600` documents (`AGENTCAD_STATE_DIR` overrides the directory; in the container it is `/data/state`). Passwords are scrypt digests and session/token secrets are stored only as SHA-256 digests, so the files hold nothing that can be replayed — but back them up as a secret anyway. Never inside a project, and unaffected by `--projects-dir`. |
| `~/Library/Logs/AgentCAD.log` | Output of the `AgentCAD.app` wrapper. |

The Anthropic API key is read from the environment only — it is never
written to any of these files.

## Keyboard shortcuts

| Key | Action |
|---|---|
| **F** | Fit view (when not typing in a field). |
| **Cmd+Z** / **Ctrl+Z** | Undo the last project change (any client's). In the code editor / a text field it stays the editor's own text undo. |
| **Shift+Cmd+Z** / **Ctrl+Y** | Redo the last undone change. |
| **Cmd+S** / **Ctrl+S** | Save & Rebuild the current part's script — works from anywhere, not just the editor. |
| **Esc** | Close an open toolbar menu. |
| **Enter** | Select the focused sidebar row (rows are Tab-reachable). |

## Troubleshooting

**`kernel request 'build' exceeded 120s; worker restarted`** — the script
hung or is pathologically slow (accidental huge loop, an OCCT operation
that never converges). The worker was killed and respawned automatically;
the app is fine. Fix the script and save again. Budgets: 60 s for ordinary
kernel requests, 120 s for builds, 300 s for interference checks and
assembly exports, 180 s for the first-start import.

**Red "rebuild failed" banner** — not a malfunction; it is the kernel
refereeing your script. Read the title for the class of failure
(`script_error`: your Python raised; `contract_error`: PARAMS/`build(p)`
contract violated; `timeout`; `kernel_crash`: the worker died — see
`stderr_tail` in the details) and the body for the traceback and failing
line. Your script is saved, the previous good geometry stays on screen, and
metrics are marked stale until a rebuild succeeds. Common OCCT failure
modes and fixes are listed in
[part-authoring.md](part-authoring.md).

**Port already in use** — startup fails with an "address already in use"
error when something else owns 8630 (often a previous AgentCAD still
running). Start with `agentcad serve --port 8631` for one run, or edit the
`port` value in `~/.agentcad/config.json` to move permanently.

**"Server unreachable — is `agentcad serve` running?"** / gray connection
dot — the browser tab lost the server. If the server is running, the UI
reconnects by itself (backoff up to 8 s) and resyncs the project; if you
stopped it, restart and reload.

**Agent panel says "unavailable"** — no `ANTHROPIC_API_KEY` in the server's
environment. Export the key and restart the server, or skip the built-in
chat entirely and use the MCP route from Claude Code — same tools, no key
stored by AgentCAD either way.

**`warning: could not open example <name>` at startup** — that example's
`project.json` failed validation (e.g. a locally edited manifest). The
server starts anyway without it; restore the file to get it back.

**Part won't delete** — it is referenced by assembly instances (the error
lists them). Remove those instances first — ask the agent, or edit
`project.json`'s `assembly.instances` — then delete the part.

**FEM says it needs the extra (HTTP 501)** — linear-static FEM ships as the
optional `agentcad[fem]` dependency group (gmsh + scikit-fem + meshio).
Install it with `uv sync --extra fem` (or `pip install 'agentcad[fem]'`) and
restart the server; the `fem_static` tool and `.../fem` route appear only
then. All other analysis (section, wall, inertia, projected area) needs no
extra.

**Import rejected** — uploads must be `.step`/`.stp`/`.brep`/`.stl` and ≤100
MB; anything else returns a validation error. STL comes in mesh-only: it
renders and measures but is skipped by interference checks and cannot be
booleaned.
