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
| `agentcad export <project> <part> --format step\|stl\|3mf [-o OUT]` | Headless one-shot export. |
| `agentcad mcp` | The MCP stdio server for external agents — see [agent-api.md](agent-api.md). |
| `make app` | Build `dist/AgentCAD.app`, a macOS wrapper that runs `agentcad open` (logs to `~/Library/Logs/AgentCAD.log`). |

`serve` and `open` accept `--port N`, `--projects-dir P`, and `--no-open`.
The default port is **8630**, persisted in `~/.agentcad/config.json`; a
`--port` flag wins for that run without changing the config. Projects
default to `~/AgentCAD/projects/`.

First launch is slower than the rest: the kernel worker imports
build123d/OCCT once (~3 s, up to 180 s allowed) before the first build.
After that, rebuilds are typically 10–100 ms.

The three bundled examples (`rocketry`, `construction`, `prototyping`) are
registered automatically from the repo's `examples/` directory and appear in
the project switcher alongside your own projects.

## The workbench

```
┌──────────────────────────────────────────────────────────────────┐
│ AgentCAD  [project ▾]        (Rebuilding…)  [Fit] [Export ▾]  ●  │  toolbar
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

**Rebuild indicator** — a spinner labeled `Rebuilding <part>…` (or
`Rebuilding N parts…`) appears while any rebuild is in flight, driven by
`rebuild_started`/`rebuild_finished` events — including rebuilds an agent
triggered.

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

Three tabs on the right: **Parameters**, **Code**, **Metrics**. The error
banner (below the tabs) belongs to all three.

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
25-tool surface external agents get ([agent-api.md](agent-api.md)):

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

> **On-canvas controls are the next wave.** The browser UI's own widgets are
> still the v1 set described above (viewport, parts/assembly tree, the
> Parameters / Code / Metrics inspector, the Export menu). Dedicated
> direct-manipulation surfaces for the features below — a transform gizmo on a
> selected instance, a numeric transform panel, a material dropdown, an
> Import button, an in-app drawing preview, analysis buttons in the Metrics
> tab — are not in this build; drive these features via the agent or the API
> for now.

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
`GET /api/projects/<proj>/parts/<part>/drawing.svg`.

**Geometric analysis.** Ask the agent to measure a cross-section area, the
minimum wall thickness (optionally against a requirement), the projected
silhouette area, or the full inertia tensor. Linear-static FEM is available
only if the optional `agentcad[fem]` extra is installed (otherwise the tool
and its route are absent).

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

The examples are working projects, not demos behind glass — they live in
the repo's `examples/` directory and are registered by path, so parameter
tweaks and script edits **write into your checkout**. `git checkout -- examples/`
restores pristine state. Their parameter sweeps are also part of the test
suite, so the shipped defaults always build.

## Where files live

| Path | Contents |
|---|---|
| `~/AgentCAD/projects/<name>/` | Your projects (override with `--projects-dir`). |
| `<project>/project.json` | Manifest (schema v2): parts (script or `reference`), a project `materials` section, parameter overrides, and assembly instances with optional `mate` specs. Human-readable, atomically written, diff-friendly; v1 files still load. |
| `<project>/parts/<id>.py` | One plain build123d script per part — the model itself. Edit with anything; the UI picks up saves via its own editor, agents via tools. (Reference parts have no script here.) |
| `<project>/imports/` | Uploaded reference CAD (STEP/BREP/STL) backing `reference` parts. |
| `<project>/.cache/` | Derived data (ACM1 meshes + metrics JSON keyed by content hash). Safe to delete; rebuilt on demand. |
| `<project>/exports/` | STEP/STL/3MF part & assembly exports, plus `<part>_drawing.svg`/`.dxf` drawings, from the Export menu, agent tools, or `agentcad export`. |
| `examples/` (repo) | The bundled example projects, registered at startup. |
| `~/.agentcad/config.json` | The persisted port (`AGENTCAD_CONFIG` overrides the path). |
| `~/Library/Logs/AgentCAD.log` | Output of the `AgentCAD.app` wrapper. |

The Anthropic API key is read from the environment only — it is never
written to any of these files.

## Keyboard shortcuts

| Key | Action |
|---|---|
| **F** | Fit view (when not typing in a field). |
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
