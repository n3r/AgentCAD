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
│ AgentCAD  File Edit View Model Help   [project ▾]  ⌘K  [Fit] ☀ ● │  toolbar + menu bar
├──┬───────┬───────────────────────────────────┬───────────────────┤
│ ⋮│ Parts │                                   │ Parameters │ Code │
│  │ nozzle│                                   │            │      │
│  │flange●│           3D viewport             │   Metrics tabs    │  inspector
│  │Assembly        (HUD top-left)             │                   │
│  │nozzle_1                                   │                   │
├──┴───────┴───────────────────────────────────┴───────────────────┤
│ Agent  ▲                                                         │  chat dock
└──────────────────────────────────────────────────────────────────┘
```

Everything is live: parameter edits, script saves, agent tool calls, and
external MCP clients all publish the same WebSocket events, so every pane
updates no matter who made the change.

The frame itself is a shell: a **menu bar** inside the toolbar, a **⌘K
command palette**, first-party **dialogs** for everything that used to be a
browser `prompt()`/`confirm()`, and **resizable, collapsible panels**
(sidebar, inspector, chat dock) whose sizes are remembered. Details below,
and the full chord list is in [Keyboard shortcuts](#keyboard-shortcuts).

## Toolbar

**Menu bar** — `File · Edit · View · Model · Help`, immediately left of the
project switcher. Every row is the same **action** the palette and the
keyboard shortcuts use — there is exactly one registry, so a menu never
drifts from what ⌘K offers. A row a project state makes unusable (Export
with nothing selected, Delete branch with only one branch) stays visible but
**greyed** rather than vanishing, so the menu is always a true map of what
the app can do; a row's shortcut, if it has one, is printed beside it in the
platform's own spelling (`⌘K` on macOS, `Ctrl+K` elsewhere). Keyboard: `←`/`→`
moves between menus, `↑`/`↓` moves within one, `Enter` runs the highlighted
row, `Esc` closes.

**Command palette** (**⌘K** / **Ctrl+K**, or the palette button on the
toolbar) — one search box over everything the app can do: UI actions ("Fit
view", "Toggle theme", "New part…"), every tool the running server has
registered (the "Tools" section — a tool a pack does not load simply is not
there, the same rule that hides FEM tools without the `[fem]` extra), and
navigation targets (open a known project, jump to a part). Type to
fuzzy-filter; `↑`/`↓` move, `Enter` runs, `Esc` closes. An empty query shows
your most recent picks first, then the head of each section. Running a tool
with required arguments opens a small form generated from the tool's own
JSON Schema — string/enum/number/boolean fields, `project`/`part_id`
pre-filled from what's selected — the same schema an agent reads; a tool
with no required arguments (or once the form is filled) runs immediately.
The result shows as a **toast** when it is small and scalar, or opens a
non-modal **result panel** (pretty JSON, a Copy button) when it is not — a
geometry-changing tool needs neither, since the viewport already refreshes
from the same event every other client sees.

**Dialogs.** Every prompt/confirm in the app — new project, new part,
delete-with-blast-radius, and the rest — is a first-party dialog, not a
browser popup: themed, keyboard-first, and consistent everywhere. `Esc`
always cancels; `Enter` in a single-line field submits (a multi-line field
uses `⌘Enter`/`Ctrl+Enter` so plain Enter can still add a newline); a
destructive dialog (delete a part, delete a branch) opens focused on
**Cancel**, never on the button that does the damage, and names what it
would take with it (e.g. "also removes 1 assembly instance"). Focus is
trapped inside the open dialog and returns to whatever you had focused once
it closes. An agent can open specific dialogs too (`ui_open`, see
[agent-api.md](agent-api.md)) — those carry a persistent "opened by agent"
mark so it is never ambiguous who put a view in front of you.

**Panels.** The sidebar, the inspector, and the chat dock all resize: drag
the hairline on their edge, or grab it with `Tab` and nudge with the arrow
keys. Double-click a handle (or hit `Enter` while it's focused) to
collapse/restore the panel; the three panels also have dedicated toggles —
**⌘B**/**Ctrl+B** (sidebar), **⇧⌘B**/**Ctrl+Shift+B** (inspector),
**⌘J**/**Ctrl+J** (chat dock) — reachable from the View menu and the palette
too. Sizes and collapsed state are remembered per browser
(`localStorage`), and the inspector/sidebar auto-collapse once below
laptop-width windows so the viewport always keeps room to work in.

**The "?" cheat-sheet** — press **?** (Shift+/, outside a text field) for
the live shortcut map, grouped by area and generated from the same registry
every chord is bound through, so it can never go stale relative to what is
actually bound. None of the palette/menu/shortcut/panel-toggle chords fire
while a modal dialog is open — the dialog owns the keyboard until you
close it.

**Project switcher** — the button showing the current project name. The menu
lists every known project with its part count, plus:

- **New project…** — opens a dialog for a name matching
  `[a-z][a-z0-9_]{0,39}` and creates it under the projects directory.
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
to undo, and **Cmd+Y** / **Ctrl+Y** or **Shift+Cmd+Z** / **Shift+Ctrl+Z** to
redo — except inside the code editor or a text field, where the editor's own
text undo keeps working. The snapshots
themselves live in the per-project git history (durable, see below); the
undo/redo *stacks* are per-server-session, so after a restart one step back
remains available and redo starts empty.

**Fit** — reframes the camera on the current content, keeping the viewing
direction. Keyboard: **F**.

**Export menu** — two sections:

- *Part* (STEP / STL / 3MF / glTF / GLB): exports the **selected part** at
  origin. Disabled when no part is selected.
- *Assembly* (STEP / STL / 3MF / glTF / GLB, plus a second **STEP
  (structured)** row): exports all placed instances as one file. The plain
  STEP row writes today's single fused solid; **STEP (structured)** writes a
  real product tree instead — one product per unique part, one occurrence per
  instance, names and colors — the file a supplier can walk. Disabled when
  the project has no instances.

`USD` appears in both sections when the optional `agentcad[usd]` extra is
installed, and is simply absent otherwise. The menu is **schema-driven**: it
reads the export tools' format lists at start-up and adds any format the
static table does not already list, so a format added on the server appears
here with no frontend change — `usd` is the one that actually depends on
this today, since it exists only with the extra.

Exporting a part to **STEP** when that part carries GD&T (PMI, set through the
agent's `set_part_pmi`) first asks: *Include GD&T (AP242)*, checked by
default. Leave it on and the tolerances travel inside the STEP file; uncheck
it for plain geometry. Parts without PMI export straight away, exactly as
before. The toast then says what travelled — `PMI attached (2 dims, 1 datum,
1 FCF)`, or which entries could not be mapped.

Exports are written to `<project>/exports/<part>.<format>` (assembly:
`exports/assembly.<format>`; USD writes `.usda`); a toast shows the full path
and size. Nothing is downloaded through the browser — the file lands on disk
next to the project.

**Theme switcher** — the ☀/☾ button. Toggles between the dark (default) and
light themes; the whole UI switches, including the 3D scene and the code
editor. The choice is remembered (localStorage) and restored before first
paint on the next visit.

**Connection dot** — far right. Green means the WebSocket event stream is
connected; gray means the UI is reconnecting (it retries with backoff and
resyncs the project when the connection returns).

## Dashboard

The all-projects screen: a card grid, one card per project, that replaces the
bare switcher dropdown as the way in. It is what you see on **first run**, and
whenever the project the browser last remembered is no longer on the server.
Open it any time with **⌘⇧O**/**Ctrl+Shift+O**, from **File → All projects…**,
from the project menu's "All projects…", or from the ⌘K palette. It is a full
pane rather than a modal, so every shortcut keeps working while it is up;
`Esc` closes it when the keyboard is inside the pane (it takes focus when it
opens), and with no project open it does not close — there is nothing behind
it yet.

Each card carries:

- a **hero thumbnail** — the project's assembly rendered at its last build, or
  a placeholder when nothing has been built yet;
- the project name and `N parts · M instances`;
- **total mass**, or **—**;
- the **relative time** of the last change (`project.json`'s mtime);
- a red **`n failing`** badge when parts in it last failed to build.

Two honesty rules are worth knowing, because they are deliberate:

- **The mass is "—" whenever any part in the project is not currently built
  with metrics.** A partial sum is a number you would read as the project's
  mass and act on, so the dashboard refuses to print one. Build the project
  (or fix the failing part) and the number appears.
- **The dashboard never builds and never renders.** It reads each project's
  manifest and whatever build state the running server already holds. That is
  what keeps opening the app instant with twenty projects on disk — and it is
  why a card can show a thumbnail that is one edit out of date, or a state dot
  that only reflects builds this server has actually done.

Beside the project cards sit **New project** and **Open by path** cards, which
run the same dialogs the File menu does.

## Sidebar

### Parts

A tree, not a list. Each row carries a 24 px **thumbnail** (or a placeholder
glyph until the part is built), its label (hover for id and material),
`script`/`ref`/`cfg` badges, any claim/presence chips, a build-state dot, and a
**⋯** button that opens the same context menu a right-click does. Click a row
to select — the viewport, all three inspector tabs, and the HUD follow the
selection. Rows are focusable; Enter selects, ↑/↓/Home/End move, ←/→ collapse
and expand a folder, and Space toggles a row's membership in the selection.

Only the visible rows exist in the page. A 1 000-part project renders a few
dozen `<li>` and two spacers, so the tree stays responsive at a scale where a
plain list would not.

Build-state dot on the right of a row:

- **pulsing amber** — a rebuild for this part is in flight;
- **red** — the last rebuild failed (hover: "Last rebuild failed"); the
  details are in the inspector's error banner;
- **no dot** — built fine (or not built yet this session).

A part that declares [configurations](#configurations) also wears a small
**badge**: the configuration it is currently showing, or `cfg` at base (hover
for `N configurations · active: …`).

**Folders.** Parts can be filed into folders — `Chassis`, `Fasteners`,
`Chassis/Left side` — up to eight levels deep. A folder is **project
metadata, not a directory**: scripts stay flat in `parts/<id>.py`, which keeps
a project portable and its git diffs readable, and re-filing a part never
rebuilds it. Collapse state is remembered per project. Create one with **New
folder…** in the context menu (it appears empty until you move something into
it), or by typing a path into **Move to folder…**.

**Drag to organize.** Drag a row onto a folder to move it; drag onto the root
zone at the top to unfile it. If the dragged row is part of the current
selection, the whole selection travels — and lands as **one** undo step. An
assembly instance can be filed the same way.

**Tags.** Free-form lowercase labels (`fastener`, `printed`, `vendor.misumi`),
up to 32 per part, shared between you and any agent working on the project:
`search_parts {query: "tag:printed"}` is how an agent addresses "everything
tagged printed" as a stable group. Edit them with **Tags…** in the context
menu, or in bulk from the action bar.

**The filter box** is pinned at the top of the section; **/** focuses it from
anywhere outside a text field, and `Esc` in the box clears it and returns
focus to the tree. Type and the tree narrows live to matching rows with their
parent folders opened around them; the header reads "n of N". Clearing the box
restores the tree exactly as you left it, collapse state included.

The box speaks the same query language as the `search_parts` agent tool, so
what you type in the UI is what you would hand an agent:

| Type this | To find |
|---|---|
| `boss` | any part whose id, label, tag, material **or script text** contains "boss" |
| `tag:printed` | parts tagged `printed` (exact) |
| `-tag:draft` | everything *not* tagged `draft` |
| `state:error` | parts whose last build failed (`ok`, `error`, `unbuilt`) |
| `kind:package` | parts that came from an installed package (`script`, `reference`, `package`) |
| `material:al6061` | parts of that material (exact id) |
| `folder:Chassis` | that folder and everything under it |
| `"m5 boss"` | the phrase, not the two words |
| `state:error tag:printed boss` | all three at once — terms are ANDed |

Metadata-only queries are answered in the browser instantly; a query with free
text also asks the server (which is the half that can read script text) and
the two result sets are **unioned**, so a part found only by something inside
its script still shows up — badged `script`, with the matching snippet as its
tooltip. A query the grammar cannot parse shows the reason under the box
instead of blanking the list.

**Multi-selection** follows Finder: click selects one, **Cmd/Ctrl+click**
toggles a row, **Shift+click** extends from the last click over the *visible*
order (so a collapsed folder's hidden parts are not swept up), and Space
toggles from the keyboard.

**The context menu** (right-click, the **⋯** button, or the ContextMenu key)
acts on the whole selection and says how many rows it will touch: Rename…,
Tags…, Move to folder…, New folder…, Export…, Delete…. `Esc` closes it; ↑/↓
and Enter drive it from the keyboard.

**The bulk action bar** appears directly under the filter box as soon as more
than one row is selected: "N selected · Material · Tags · Folder · Export ·
Delete · ×". Four of its verbs — Tags, Folder, Export, Delete — are the same
implementation the context menu runs; **Material** is the one verb with no
row-level twin, because a single part's material is the inspector's picker. A bulk change
is **one undo step**, not one per part — press ⌘Z/Ctrl+Z once and all of it
comes back, and the toast names what it would undo ("bulk material ×6").

Bulk operations allow **partial success**: if some parts could not be changed
(an id that no longer exists, a part still used by an assembly instance), the
rest still land and a **results panel** opens listing every row with its
status and error. Deleting a part an assembly instance still uses is
**refused** — per part in a bulk, with the instances named — so clear those
instances or re-point them at another part first; the rest of the selection
still lands. (A **Material** change that lands but whose part then fails to
build is listed as "written, rebuild failed": the material *was* written, and
one undo takes it back.)

**＋** in the section header opens the **New part…** dialog (also **⌘N**/
**Ctrl+N**): an id (`[a-z][a-z0-9_]{0,39}`, validated live), a label, and a
material picker when the catalog is cached. The part starts from the default
template — a parametric rounded plate with four parameters — so there is
immediately something to see and edit. New parts default to Aluminum 6061.
Material is part of the manifest; change one part's from the **inspector's**
material picker (there is deliberately no Material row in the context menu —
the inspector *is* the per-part picker), several parts' at once from the bulk
bar's **Material** verb, or via the agent tools (`update_part_script` with
`material=`) and by editing `project.json`.

Deleting is in the context menu (single or bulk) rather than on the row: it
opens a danger-styled confirm naming exactly what it would remove — the parts
and their script files — and, when an assembly instance still references one,
saying so: the delete of that part is **refused** while the instance exists,
and the confirm names the instances you have to clear or re-point first.

### Assembly

Click the **Assembly** header to switch the viewport to assembly mode.
Below it, one row per instance: a color swatch, the instance id, and the
part it references. Clicking an instance selects it in the viewport *and*
loads its part into the inspector, so you can tune parameters while looking
at the whole machine.

Instances (position, rotation, color) are edited through the agent tools
(`set_assembly`) or by editing `project.json` directly — the v1 UI displays
and selects them but has no drag-to-place editor. Instances take **folders**
like parts do: drag one onto a folder row, or `PATCH` its `folder` — grouping
in the assembly tree is metadata and never moves geometry.

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

The **Agent** bar at the bottom expands into a chat dock (click the header,
or **⌘J**/**Ctrl+J**; the open/closed state and the dock's height are
remembered — see [Panels](#toolbar)).

**Without `ANTHROPIC_API_KEY`** the panel stays functional as a signpost: it
explains that chat needs the key set before launch and gives the exact
`claude mcp add agentcad …` command for driving AgentCAD from Claude Code
instead. Everything else in the app works normally.

**With the key** (set in the environment before `make run` /
`agentcad serve`) it becomes a full tool-using assistant with the same
109-tool surface external agents get ([agent-api.md](agent-api.md)):

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

### Skills

A **skill** is a markdown guide the agent loads on demand — the craft that
does not fit in one prompt: snap-fit strain limits, enclosure wall tables per
process, ISO 286 fits, the OCCT failure playbook. Sixteen ship with AgentCAD,
and a project can add its own. Full reference: [skills.md](skills.md).

**In chat.** When the agent loads one you see a chip in the dock —
`📘 snap-fits · core` — naming the skill and where it came from, or
`📎 snap-fits · snippets/lid.py` when it reads one file out of a skill rather
than the guide. If the agent loads more than its context budget allows, the
oldest chip gets struck through ("unloaded"): the guide was dropped to make
room, and the agent will re-read it if it needs it again. The chips are there
so you can see exactly what text entered the agent's context — and only this
chat lane's: another agent's session on the same project has its own.

**The Skills modal.** **Agent → Skills…** (also the toolbar button, or the
`#skills` link) lists everything loadable in the open project:

- a **provenance badge** per row: `core` (shipped with AgentCAD),
  `project` (from this project's `skills/` directory), `overrides core` (a
  project skill shadowing a shipped one of the same name), plus
  `needs review`, `changed since trusted` and `invalid` states;
- an **enable** toggle that hides a skill from every agent without deleting the
  file;
- click a row to **preview** the exact text an agent would get, with its
  version, author, licence and its snippet/table files listed. **An
  unreviewed skill previews too** — you cannot decide about text you are not
  allowed to read — with a line above the body saying no agent can load it yet
  and a **Trust this skill** button right there.

**Trust — why a project skill starts greyed out.** A project skill is *agent
instructions*, and it may have arrived with a `git clone`, a pull or a package.
So AgentCAD will not let any **agent** read one until a human has: the modal
shows a banner — *"This project provides agent instructions — review them
before agents can load them"* — and each such row has **Review & trust**.

The order is the point: **read it, then trust it.** You always get the full
text of an unreviewed skill; the agent gets nothing — not even its own
description, which is prose the skill's author wrote for a model to read.
Approval is remembered **by content**, and by the content of the *whole*
skill: edit the guide, or add or change one of its snippets, and the row goes
back to "changed since trusted" and you approve it again. Only a person can
grant this — no agent, local or remote, can approve its own instructions, and
"a person" means a browser or signed-in session AgentCAD can name, not merely
a request that left the agent header off. The approval is stored outside the
project's version control, so it is never cloned or pushed anywhere.

**Teaching the system your own.** Save a markdown file at
`<project>/skills/<name>.md` (frontmatter with `name`, `description`,
`version`; the body is ordinary markdown) and it appears in the modal on the
next open — trust it once and every future agent session applies it. It is a
file in your project, so it branches, merges and restores with everything
else. For a scaffold and a checker:

```bash
.venv/bin/agentcad skill new frame-rules --project myproj
.venv/bin/agentcad skill lint ~/AgentCAD/projects/myproj/skills
```

### Generate

**Agent → Generate…** (needs `ANTHROPIC_API_KEY`, same as chat) opens a
different kind of agent entirely: instead of a conversation, you describe a
**part** and the system iterates — write, build, look, measure, check specs,
write again — on its own until the part is a valid solid that passes its
design specs, a time/iteration budget runs out, or it gives up. You watch it
converge; you do not drive it turn by turn.

**Describe the part.** Type what you want — dimensions, an interface ("mounts
a NEMA 17 motor"), a material, a mass budget — and attach a reference image
or a PDF datasheet if you have one (a photo of the space it has to fit,
a manufacturer's drawing). A named standard the system recognizes (a NEMA
frame today) is grounded from AgentCAD's own shipped data, never guessed —
the bolt circle and shaft clearance you get back are the real NEMA numbers,
cited, not the model's memory of them. You can ask for more than one
**candidate** (up to 4) so you have a choice at the end rather than one shot.

**Watch it converge.** While it runs you see a live lane per candidate: the
part's script being written, an automatic render after each change (the
system renders and measures for itself — you never have to ask it to "look"),
and the metrics/spec results as they land. This can take a couple of minutes;
the panel stays open and responsive while it works.

**The gallery.** When every candidate reaches a stopping point, you get a
gallery: one card per candidate with its render, mass, bounding box, its
typed PARAMS table, and a row of chips — one per design spec, green or red —
so you can see *which* constraint a candidate missed without reading a spec
report. A candidate that ran out of budget before converging says so plainly:
**"budget exhausted — best so far, N checks failing"** — never a fake
success. Nothing here has touched your project yet; every candidate lives on
a scratch id you cannot otherwise see or select in the tree until you accept
one.

**Accept.** Pick the candidate you want and click **Accept** — it becomes a
real part at whatever id you choose (or a generated one), the losing
candidates are discarded, and the part's Parameters pane grows a small
**generated** badge (model, iteration count, when, by whom) so an
agent-generated part is never mistaken for hand-authored history you forgot
about — except for that one badge, it behaves exactly like any other script
part: edit it, branch it, export it, delete it.

**The honesty this feature demands, read before you trust a green.**
"Spec green" means the kernel accepted the geometry and every design spec
the script currently declares passed — it is a statement about the
constraints that got written down, not a certificate that the part is the
*right* shape. **The loop can pass its own metrics and still miss the
shape** — review the candidate the way you would review any script an agent
handed you: look at the render, check the PARAMS make sense, and only then
trust it. A `budget_exhausted` result is not a failure hidden from you; it is
the system telling you honestly that it ran out of time before converging,
with its best attempt on the table for you to judge or discard.

**What "spec green" and "reference data" do and do not protect you from.** A
generated part is a Python script, and it **is arbitrary Python** — the same
as a script you or anyone else wrote by hand. Generation does not sandbox it
any more tightly than any other part in your project; the isolation that
exists comes from the server's general script-execution confinement, not from
anything specific to this feature. Attaching a reference image or PDF is safe
in the ordinary sense (its text is fenced off from the model as data, never
followed as an instruction), but that fence is a **prompt-level safeguard,
not a security wall** — treat it the way you'd treat a spam filter, not a
lock. And the green "spec_green" badge and the **generated** provenance tag
are not a certificate that the part is safe or correct; they mean the server
re-measured the geometry against the constraints it could check, nothing
more. Review an accepted generated part's script the same way you would
review a script from any other agent before you trust it with anything that
matters.

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

**Importing a whole assembly (the import preview).** Pick a `.step`/`.stp`
with the Import button and, when the file holds more than one occurrence, an
**Import preview** dialog opens instead of the old part-id prompt: one row per
unique product with a swatch in its authored color and a `×N` occurrence
badge, under a summary line (*"14 products, 41 occurrences"*). You get three
choices —

- **Import N parts** — the structured landing: one reference part per unique
  product (8 occurrences of one screw are **one** part), one placed assembly
  instance per occurrence, with the names, the composed transforms and the
  colors the file carried. The optional *Part id prefix* field prefixes every
  generated id, which is how you keep two revisions of a vendor assembly apart
  in one project; colliding ids are suffixed `_2`, `_3`, … and the import says
  so.
- **Import flat instead** — today's behavior: the whole file as one reference
  part, with the part-id prompt. For a genuinely monolithic file.
- **Cancel**.

A single-occurrence file, a non-STEP file, or a preview that fails goes
straight to the old prompt with no error shown — nothing changed for those.
Asking the agent instead (`import_cad_file`) makes the same call, and there
the structured/flat decision is automatic unless you say which you want. Deep
trees flatten to one instance level for now (the transforms are composed, so
everything lands where the file says).

**What travels between tools — and what does not.** Every import and export
says so in its result, and the rules are short enough to state here:

| | Survives in |
|---|---|
| Exact solid geometry (B-rep) | **STEP** only — everything else is a triangle mesh of it |
| Tolerances / GD&T (PMI) | **STEP AP242, on the way out only.** We do not read PMI back out of someone else's file yet |
| Colors, names | **3MF**, **glTF/GLB**, **USD**; a structured STEP carries per-instance colors too |
| Metadata (title, designer, part number, creation date) | **3MF only.** glTF and USD carry no model metadata — just a generator/creator breadcrumb and the up-axis declaration |
| Parameters, the script, sketch constraints, configurations | **Nothing.** No neutral format carries parametric intent — this is a property of the formats, not a to-do |

That last row is why the project is the source of truth and an export is a
compiled artifact of one version: send a supplier the STEP, keep the model.

**USD needs its extra, and not every platform has it.** `usd` shows up in the
Export menu only when `agentcad[usd]` is installed (`uv sync --extra usd`).
The upstream `usd-core` wheel exists for **macOS**, **x86-64 Linux** and
**Windows** — there is **no linux-aarch64 wheel**, so on an ARM Linux box the
extra installs nothing, `usd` never appears, and everything else works
normally.

**Per-release manual interop checks.** Two things a test cannot assert,
verified by hand once per release and recorded in the release notes:

1. a part exported with `Include GD&T` opens in **FreeCAD's** AP242 viewer
   with its dimensions, datum and feature control frames visible;
2. a 3MF opens in **PrusaSlicer** (or the Bambu/Orca lineage) at the right
   scale, with the object names and per-solid colors intact;
3. an assembly's GLB export, opened through the **vendored Three.js loader**
   (not just glTF-validated), actually renders with the right instance colors
   and poses against the live viewport — a toast reporting export success is
   not this check; it only proves the file was written.

Everything else about those two files — the AP242 schema, the millimetre
declaration, the metadata, the colors, the PMI round trip through our own
reader — is covered by the suite.

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
front/top/right/iso views onto a real drawing **sheet** — frame, title block,
overall dimensions and hole callouts detected from the geometry — writing
`exports/<part>_drawing.svg` (or `.pdf`, `.dxf`). A server-rendered preview is
available at `GET /api/projects/<proj>/parts/<part>/drawing.svg` and the PDF
twin `…/drawing.pdf`.

*Sheet format and the title block.* The drawing modal's header has a sheet
picker — nine formats, ISO `A4`–`A0` and ASME `A`–`D`, all landscape, default
`A3` — and view checkboxes (top/front/right/iso); views auto-scale to a
standard ratio (2:1, 1:2, 1:5, …) chosen to fit, printed in the title block
along with part label, material, mass, units, and a version identity (a tag
name or short commit hash, filled in once you have history — "-" before
that). The company/author/project-code/approved-by/notes fields that fill the
rest of the title block are edited **once** per project via "Drawing
fields…" (agents: `set_drawing_fields`/`get_drawing_fields`) rather than
per drawing.

*Sections and details.* The "Section…" control cuts the part on a plane
(XY/XZ/YZ + offset) and draws a hatched, labeled `A-A` view with
cutting-plane arrows on the parent view — real cross-sections, not a flat
projection. (A detail-view control — circle a region, get a magnified `A
(2:1)` view — is agent/API-only for now; the tool supports it, the UI control
is a follow-up.)

*Hole tables.* Checking hole callouts on isn't automatic — ask for a **hole
table** and the right-hand column lists tag/X/Y/designation per hole, reading
real thread/counterbore/countersink designations when the part was drilled
through the hole toolkit, or plain detected diameters otherwise.

*Config tables.* For a part with [configurations](#configurations) the
drawing panel offers a **dim table** checkbox (one row per configuration:
configured parameters plus overall X/Y/Z, every number measured from that
configuration's own built geometry) or, further along the family workflow,
**tabulate** — dims on the view get lettered (A, B, C…) and a boxed table
lists every configuration's value for each letter plus its mass, so one sheet
documents the whole family. The two share the sheet's one table column
(tabulate wins if you ask for both). A drawing made while a configuration is
active is that configuration's, and saves as `<part>_<config>_drawing.<ext>`.

*PDF, and the determinism guarantee.* Every format but DXF renders through
one shared layout, so SVG and the "Download PDF" export always show the same
sheet. Regenerating a drawing at the same project state produces
**byte-identical** SVG and PDF every time — no timestamps, no random ids —
which is what makes a drawing diffable in a change proposal and checkable in
CI: a changed drawing means the geometry moved, never that the renderer's
formatting drifted.

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
raise the discard-edits dialog first, as everywhere else.

- **New branch…** opens a dialog for a name matching
  `[a-z0-9][a-z0-9_/-]{0,63}`, forks it from the branch you are on, and
  switches you to it.
- **×** on a branch row opens a danger confirm naming the branch. Deleting
  from the switcher's own **Delete branch…** row instead opens a picker
  (any branch but the current and the default one) before the same confirm.
  It appears only where the server would allow it — never on the
  default branch or the one you are on — and a branch someone else has checked
  out comes back as an error toast. Versions (tags) made on the branch survive
  it, and its working tree is committed before removal, so nothing uncommitted
  is silently thrown away.

**Versions… (the versions dialog).** A version is an immutable named state —
"the revision we sent to the machine shop" — stored as an annotated git tag.
The dialog lists them newest-first with the message, author, relative date and
short commit, and gives each a **Restore** action (it restores that state onto
your current branch as one undoable step; the confirm names the tag whose
state you're about to restore). **Tag current state…** opens a form for a
name (`[a-z0-9][a-z0-9._/-]{0,63}`, so `v1.2` works) and a message. Versions
cannot be moved or deleted, and they outlive the branch they were made on.

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
`<part>_<config>_drawing.svg` and can carry the family's dimension table (or,
with `tabulate`, a letter-variable table with per-config mass — see
[2D drawings](#the-v2-capabilities)); an
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

## Bill of materials

The **BOM** toolbar button opens the assembly's bill of materials — the table
that answers "what do I actually need to buy or send to the shop," derived
from the model on demand rather than maintained beside it.

**One row per part, quantities rolled up.** A screw pattern of eight, used
twice in a bolted-on bracket sub-assembly, is one row with `qty: 16` — you
never hand-count instances. The **flat / indented** selector switches between
that rolled-up view and a per-occurrence tree (indented by assembly level);
totals at the footer (mass, cost) are identical either way. Each row shows
item number, quantity, part number, name, config, material, unit mass, unit
cost, extended cost and source.

**Part numbers, costs and sourcing are edited in the model, not in a
spreadsheet.** `Part #`, `Unit cost` and `Source` are inline-editable cells —
type a value and it commits on blur/Enter, written to the part like any other
manifest field (and undoable the same way). There is no verb yet to *clear* a
manual cost once set — leave the field as-is if you want to fall back to the
estimate again; a later release adds that.

**Cost honesty.** A unit cost you never set is not blank — if the part's
material carries a `cost_usd_kg`, the BOM estimates it (`mass × cost/kg`) and
tags the cell **(est)** so nobody downstream reads a guess as a quote. No
material cost and no manual price shows **(none)**. The same discipline
applies to mass: a part that hasn't been built in this server session shows
**(unbuilt)** rather than triggering a rebuild just to answer a BOM query, and
one whose script or parameters changed since its last build shows **(stale)**
(the table still shows the last-known value). A banner above the table names
how many rows are affected. **These tags survive into the CSV/JSON export as
their own `cost_source`/`mass_source` columns** — the honesty travels with the
file, not just the screen.

**Export** writes `exports/bom.csv` or `exports/bom.json` next to the project
(the same download-to-disk convention as the Export menu); CSV opens cleanly
in Sheets/Excel, quoting commas and quotes correctly, with a `TOTAL` row.

Agents get the same three calls — `get_bom`, `export_bom`, `set_bom_fields` —
documented in
[agent-api.md](agent-api.md#bom-and-releases).

## Releases

The **Releases** toolbar button opens the revision panel: draft a release,
watch its gate, get it approved, and finalize it into an immutable, tagged,
reproducible snapshot — Onshape's "PDM built in" pitch, built on the git
substrate the app already has rather than a vault.

**Cutting a release.** You must be on a branch other than the project's
default (make one with **New branch…** first, same as any other change).
**Cut release…** prompts for release notes and allocates the next revision
letter (`A`, `B`, `C`, …) — this opens a `release`-kind change proposal behind
the scenes and evaluates its **gate**: design specs (PRD-003) and geometry CI
(PRD-004) green, the working tree clean, sub-assembly references pinned and
drawings regenerable (the last two are soft checks in v1 — named, not yet
blocking). A green gate moves the row to **in review**; a red one leaves it
**draft** with every failing check listed right there, so you know exactly
what to fix before cutting again.

**Approving is reviewing the proposal — there is no separate "approve
release" button.** Each row's **Review proposal** action opens the same
Proposals modal as any other change: read the evidence, **Approve**. A
release, like any proposal, cannot be approved by its own author.

**Finalize** appears once a row is **in review**; it refuses (with a clear
error) until the proposal actually carries an approval. Finalizing tags the
approved state as `release/<rev>` (an immutable, protected version — it can't
be deleted or moved out from under the release), marks the row **released**
with a 🔒, supersedes the previous release, and builds the bundle in the
background: STEP for every part and the assembly, drawings, the BOM at that
exact revision, flat patterns for sheet-metal parts, and a README summarizing
the gate report — all written under `exports/releases/<rev>/` plus a zip
beside it.

**A released row is visibly locked.** There is no edit path back into it —
the append-only record and the tag's own immutability both refuse a write.
To evolve a released design, branch off its tag (`branch_create` from
`release/<rev>`) and cut the next revision from there when it's ready.

**Reproducibility, honestly qualified.** Re-running the bundle at the same
tag produces byte-identical drawings, BOM files, flat patterns and README on
every rebuild. STEP is the one exception: two non-geometry fields (a write
timestamp, and an OCCT session counter on assembly exports) are normalized
before comparing, and the bundle's own README says so rather than claiming a
perfect byte match it can't deliver.

Agents get the same five calls — `release_start`, `release_finalize`,
`release_bundle`, `list_releases`, `get_release` — documented in
[agent-api.md](agent-api.md#bom-and-releases).

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

## Materials browser

The **Materials** button on the toolbar (next to Market) opens the materials
database in a modal over the workbench — unlike Market it never navigates
away, so it can also open in **assign mode** from the inspector's material
block (its **Browse…** button, under the material dropdown) without losing
your place. The `#materials` URL hash opens it at boot too.

- **Tree.** Category → subcategory on the left, each with a material count;
  click one to filter the table. Counts are read once per open from the whole
  catalog, so they don't shift as you narrow other filters.
- **Filters.** Min/max density, min E, min yield, min max-service-temperature,
  max cost, a row of process chips (`cnc`, `weld`, `fdm`, `sheet`, …) and a
  basis select (`typical`/`minimum`/`characteristic`). Numeric fields debounce
  250 ms; the count next to Close updates with every query.
- **Table.** id/label, category/subcategory, condition, density, E, yield,
  max service temperature and cost — click a column header to sort (numbers
  first, missing values always last). Click a row to open its full record in
  the detail pane on the right.
- **Compare.** Check up to four rows' pin boxes; the **Compare** button (it
  shows the count) swaps the table for a side-by-side column view of every
  cited property, ranges rendered `lo–hi` and a missing value as `—`.
- **Detail.** Label, condition, standards, every property with its value/range
  and unit, a **basis badge** (`typical`/`minimum`/`characteristic`, or
  `uncited` when the property carries no source), the source text, a
  temperature table when a property has one, the process block as chips, and
  `links` as outbound references (MMPDS, manufacturer datasheets — never
  mirrored). In assign mode a **Use for…** button writes the selected material
  straight to the part you opened it from, through the exact same call the
  inspector's material dropdown makes.

The same room-temperature-typical, not-a-design-allowable caveat from the
Inspector's material block applies here too — it is shown on every detail
record.

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
- **Single sign-on is an additional door, not a replacement — reached by URL,
  not yet a button on this page.** If your operator has configured OIDC,
  visiting `/api/auth/oidc/login` signs you in with your identity provider
  and lands you back in the app; there is no discovery of it from the sign-in
  form yet, so ask your operator whether it is configured before you go
  looking. It still ends at the same session, and it cannot register a
  brand-new account by itself — it signs in a local handle you (or your
  operator) already created, exactly like the enrolment link above.
  **Passkeys** have the same kind of server-side support — reached the
  ceremony exists, but there is no sign-in-page affordance to trigger it
  from a browser yet, so password (or SSO) is the way in today. Passkeys
  need their operator's server to have installed the optional `[cloud]`
  extra; **OIDC does not** — it runs on a plain install, with no extra
  required. See `docs/deployment.md` for what your operator has to
  configure first.
- **The identity chip**, top right, shows who the server thinks you are and
  signs you out. Your session survives a browser restart and the server being
  restarted, and it expires after 14 idle days (30 at the outside).
- **Attribution becomes your name.** Lock chips, the presence roster, comment
  authors, proposal actors and history entries read `user:<handle>` instead of
  `browser:7f3a1b2c` — and per-part claims now actually protect you from other
  *people*, while an agent with a token is never blocked by a human's claim and
  cannot take one.
- **Everyone shares one project space — unless your operator has turned on
  organizations.** On a plain instance there are still just two roles:
  `member` (read and write every project) and `admin` (that, plus managing
  users and tokens), with no per-project permission. Once your operator has
  created at least one organization, projects live under
  `org / workspace / project` and per-project roles apply instead — see
  "Organizations, workspaces and roles" below. Which kind of instance you are
  on is not something you have to guess: the workspace chip described there
  only appears when it applies.
- **Anonymous visitors can see the public parts catalog and nothing else**:
  the package list, per-version metadata and the shipped preview images. No
  project, no part, no geometry, and nothing that runs the kernel.
- **For agents and CI**, an admin mints a bearer token
  (`agentcad admin token add ci`). Point an MCP client at the instance with
  `AGENTCAD_URL` and `AGENTCAD_TOKEN`; revoking the token cuts it off on the
  next call. Inside an organization, an **org admin** can instead mint a
  token scoped to just that org's projects — see below.

> **Trust.** An account on a hosted instance can execute arbitrary Python on
> the server — a part script *is* Python. So accounts are for people you would
> give a shell to, and registration is closed. Organizations, workspaces and
> per-project roles (below) decide who may **call the API** to build, edit or
> read a project — they are not a filesystem wall around what a script that
> IS running can touch: a member authorized to build one project still runs
> as the server user with every other project on the instance readable and
> writable underneath them, including another organization's. That is a
> statement of what the software does today, not a caveat to skim.

## Organizations, workspaces and roles (a hosted instance)

Skip this section on a plain hosted instance — it only applies once your
operator has created at least one **organization**. You will know: a
**workspace chip** appears in the toolbar reading `org/workspace`, and the
Model menu grows three new rows: "Switch workspace…", "Org members…" and
"Agent tokens…".

- **The workspace chip and switcher.** Click the chip (or use "Switch
  workspace…" from the Model menu, or the command palette) to see every
  organization and workspace you belong to and jump between them. Switching
  reloads the page — a different workspace can have entirely different
  projects, so this is a deliberate re-boot rather than a hot-swap. Your
  choice is remembered in the browser for next time.
- **Roles**, weakest to strongest: **viewer** (read only), **commenter**
  (also opens and reviews threads and proposals), **editor** (also changes
  geometry), **admin** (also decides who holds which role). An organization
  gives you a *default* role across every project in it; an admin may
  additionally raise or lower your role on one specific project without
  touching the default everywhere else.
- **What a viewer actually sees.** The 3D view, the parameter list, the
  script, metrics, drawings, exports, BOMs and releases are all there to
  read — a viewer can still export a STEP file or render a drawing, because
  those are derived from geometry that is already visible to them. What
  disappears: parameter inputs and the script editor go read-only, and
  branch create/delete, undo/redo and part deletion are hidden from the
  toolbar and the command palette. **Threads and proposals are read-only to
  a viewer**, not fully usable: they can see every thread and every
  proposal, including its diff and packet (that is a `view`-floor read),
  but opening or resolving a thread and creating, updating or reviewing a
  proposal all need `comment` or above — the same floor "commenter" names in
  the role list above. Attempting a write anyway (e.g. through the agent
  chat) comes back as a plain refusal naming the role you need and the role
  you actually have, never a silent no-op.
- **Members and tokens**, from the Model menu ("Org members…" / "Agent
  tokens…") — visible to everyone in the org, with the write controls shown
  only to an org admin:
  - The **members panel** lists everyone's org-default role, and — for an
    admin — a form to grant or revoke a *per-project* role override for
    any member or agent token (name it as `user:handle`, `agent:name`, or
    just a bare handle).
  - The **tokens panel** lists the scoped agent tokens minted in this org
    (name, role, which projects, expiry, live/revoked) and, for an admin, a
    form to mint a new one: name it, pick a workspace, list the project(s)
    it should reach, its role and an optional expiry. **The secret is shown
    exactly once**, in a copy-and-dismiss box right after minting — there is
    nowhere in the product to retrieve it again, only to revoke it and mint
    a fresh one. Revocation takes effect on the token's very next request.
  - A scoped token cannot itself mint or revoke a token: that needs a
    signed-in *person* holding **org-level** admin, by construction — an
    agent has no organization membership of its own, only whatever projects
    it was explicitly granted. It *can* grant or revoke a role on a project
    it was itself minted `admin` on — that only needs **project-level**
    admin, which an explicit grant gives a token exactly as it gives a
    person.

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

## Working offline: git sync with a hosted instance

Every project on a hosted instance is also an ordinary git repository you
can clone, edit fully offline (with your own local `agentcad serve`), and
push back. You need the `agentcad` CLI installed and a bearer token from
your admin (`agentcad admin token add <you>`, or a scoped token an org admin
mints for you — see above).

```bash
agentcad login https://cad.example.com --token acad_xxxxxxxx_…
agentcad clone https://cad.example.com/git/acme/main/widget.git
cd widget
# edit, run `agentcad serve` locally, build offline with your own kernel —
# this is a real project directory: scripts, manifest and history all present
agentcad push          # send your branches and tags
agentcad pull          # bring down anyone else's, merging when needed
agentcad status --fetch
```

`login` stores the token once, at `~/.agentcad/sync.json`, permissioned so
only you can read it, and never asks you to put it in a URL or a git config
file — every `push`/`pull`/`clone` reaches for it automatically through a
git credential helper. `clone` takes the same `<org>/<workspace>/<project>`
URL the workspace switcher's org/workspace names compose into.

**The conflict story.** `push` never forces and never deletes — if the
server has commits you do not, it refuses **inside git's own transaction**,
atomically, with a message git prints back to you verbatim:

```
remote: agentcad: refs/heads/main diverged - pull and merge, never force
```

The fix is always `agentcad pull`: it fetches, fast-forwards whatever it
can, and for a branch that has genuinely diverged runs the same
conflict-aware merge the browser's own merge UI uses — you get named
conflicts to resolve, never a silent overwrite of somebody's work and never
a reset of yours. Two more refusals you may see, both permanent (there is no
"force" that gets around them, by design):

```
remote: agentcad: refusing to delete refs/heads/old - deletes are refused on the hosted copy
remote: agentcad: refs/tags/v1 already exists - tags are immutable
```

Release tags (see [Releases](#releases)) never move or
disappear once pushed — that immutability is what makes a release
reproducible. A push that *builds* into something broken is not refused
either way: the server checks it out and the next open rebuilds it exactly
like any other change, showing the failure where every build failure shows.

**What syncs, and what never does.** Scripts, the manifest, and the whole
commit history travel with every clone and push. **`.cache/` and `exports/`
never do** — they hold rebuilt meshes and generated files that are cheap to
regenerate locally and expensive to carry over the wire; a fresh clone
simply rebuilds them from the script. `imports/` (reference CAD you brought
in) does sync, because there is no script that could regenerate it.

`agentcad mcp --remote <url> --token …` points an MCP client (Claude Code
included) at a hosted instance instead of your own machine, so an agent can
work against the shared project directly rather than through a local clone.

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
| `<project>/exports/` | STEP/STL/3MF part & assembly exports, plus `<part>_drawing.svg`/`.pdf`/`.dxf` drawings, from the Export menu, agent tools, or `agentcad export`. |
| `examples/` (repo) | The bundled example projects, registered at startup. |
| `~/.agentcad/config.json` | The persisted port (`AGENTCAD_CONFIG` overrides the path). |
| `~/.agentcad/sync.json` | **Hosted git sync only.** `agentcad login`'s token store, `0600` from the first byte, never inside a project (`AGENTCAD_SYNC_CONFIG` overrides the path). Read by the git credential helper; never written into a URL or a git config file. |
| `~/.agentcad/state/auth/*.json` | **Hosted mode only.** Accounts, enrolments, sessions, tokens, and (once your operator sets up SSO) the OIDC provider config — five atomically-written `0600` documents, plus `orgs.json` beside them once your operator has created an organization (`AGENTCAD_STATE_DIR` overrides the directory; in the container it is `/data/state`). Passwords are scrypt digests and session/token secrets are stored only as SHA-256 digests, so the files hold nothing that can be replayed — but back them up as a secret anyway. Never inside a project, and unaffected by `--projects-dir`. |
| `~/.agentcad/state/audit/*.db` | **Hosted mode, once an organization exists.** One SQLite database per organization plus one instance-wide `_instance.db` for sign-ins and account administration — never back these up with a plain file copy; see `docs/deployment.md`'s "Audit" section for the safe command. |
| `~/Library/Logs/AgentCAD.log` | Output of the `AgentCAD.app` wrapper. |

The Anthropic API key is read from the environment only — it is never
written to any of these files.

## Keyboard shortcuts

Generated from the same shortcut registry the "?" cheat-sheet reads, so this
table and the live overlay can never disagree. Every chord below is
suppressed while a modal dialog is open — the dialog owns `Esc`/`Enter`
until it closes — and a bare key (`F`, `G`, `R`, `?`) never fires while
you're typing in a field.

| Key (macOS / other) | Action |
|---|---|
| **F** | Fit view to content. |
| **G** | Switch the assembly gizmo to Move (an instance must be selected). |
| **R** | Switch the assembly gizmo to Rotate (an instance must be selected). |
| **Cmd+Z** / **Ctrl+Z** | Undo the last project change (any client's). In the code editor / a text field it stays the editor's own text undo. |
| **Cmd+Y** / **Ctrl+Y**, or **Shift+Cmd+Z** / **Shift+Ctrl+Z** | Redo the last undone change. |
| **Cmd+S** / **Ctrl+S** | Save & Rebuild the current part's script — works from anywhere, not just the editor; defers to the code editor's own binding while a field, not this shortcut, has focus. |
| **Cmd+N** / **Ctrl+N** | New part… (a project must be open). |
| **Cmd+K** / **Ctrl+K** | Open the command palette. |
| **Cmd+Shift+O** / **Ctrl+Shift+O** | Open the all-projects dashboard (also the first screen when no project has been opened in this browser). `Esc` closes it while the keyboard is inside the pane — it takes focus when it opens, so press `Esc` there; after clicking away into the toolbar, click back into the pane first or use its **Close** button. With no project open it does not close: there is nothing behind it yet. |
| **/** | Focus the sidebar's part filter (a project must be open). `Esc` in the box clears it and returns focus to the tree. |
| **Cmd+B** / **Ctrl+B** | Toggle the sidebar. |
| **Shift+Cmd+B** / **Ctrl+Shift+B** | Toggle the inspector. |
| **Cmd+J** / **Ctrl+J** | Toggle the chat dock. |
| **?** | Open the keyboard shortcuts cheat-sheet. |
| **Esc** | Cancel the topmost open dialog/menu/palette; while sketching, cancels the pending entity, then the selection, then closes the sketch. |
| **Enter** | Submit the focused dialog's single-line field, run the highlighted palette row, or select the focused sidebar row (rows are Tab-reachable). |
| **Delete** *(while sketching)* | Delete the selected sketch entities. |

Resizable panels (sidebar, inspector, chat dock) additionally respond to
`Tab` to focus their drag handle, the arrow keys to nudge it 16 px at a
time, and `Enter`/double-click to collapse or restore — see
[Panels](#toolbar).

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
