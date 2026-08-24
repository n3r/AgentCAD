# PRD-025 — Workspaces: Build · Test · Produce · Library · Market

- **Status:** pending
- **Phase:** v5 — daily-driver depth (frame); Produce/Market content matures in v6
- **Created:** 2026-08-09
- **Origin:** founder idea #1 (Aug 2026), engineering-reviewed; supported by competitive analysis
- **Depends on:** PRD-026 (shell implements the frame — hard) · content per tab: PRD-003/019/030 (Test), PRD-021/022 (Produce), PRD-011 (Library), PRD-031 (Market) — all soft/staged
- **Related:** PRD-016, PRD-027, PRD-029

> **Naming collision with PRD-005, ruled on there (design spec
> `docs/superpowers/specs/2026-08-24-multi-tenant-cloud-design.md`, "Scope
> rulings").** PRD-005's tenancy model claims `org → workspace → project` as
> a first-class, user-facing noun: the workspace switcher, the
> `X-Agentcad-Workspace` header, the `?workspace=` query fallback, the
> "Org members…"/"Agent tokens…" panels, and `docs/deployment.md`'s
> "Organisations, workspaces and roles" all use "workspace" for a tenant's
> sub-org — a real access-control boundary a person switches *into*. This
> PRD's title and this PRD's own text (G1's "five workspaces", FR1's
> "Workspace bar", `state.workspace`, `set_workspace`, the
> `workspace_changed` event) use the *same word* for something else
> entirely: a view/tab over one project, with no access-control meaning at
> all. The two are not the same concept wearing one name by coincidence —
> a PRD-005 workspace can *contain* several projects, each of which has all
> five of this PRD's tabs. **The ruling: PRD-005 keeps "workspace"; this PRD
> renames its tabs to a different word before it ships** (the shipped shell
> already has a `#workspace` DOM id and layout `localStorage` keys that
> predate both PRDs and are deliberately left alone — this collision is
> about a *future* user-facing label, not that existing internal slot name).
> Pick the replacement in this PRD's own design review (its "Naming" risk
> below already has an open slot for exactly this kind of choice) — a
> plausible option is dropping "workspace" for the tab bar entirely and
> calling it what it is ("phase", "tab", or the founder's per-tab nouns
> alone), but that choice is this PRD's to make, not PRD-005's.

## Problem & motivation

Today the workbench is one undifferentiated surface: modeling, analysis,
imports, exports, and chat all live in the same three panes. That is fine at
v0.1 scale and wrong for where the product is going: the engineering loop has
distinct *phases* — design it, prove it, make it, reuse it, share it — and
each phase needs different surfaces, different data in reach, and a
differently-primed agent. Cramming FEM results, DFM violations, quotes, and
a package library into the single inspector would bury all of them.

The founder's framing (idea #1) — explicit tabs for Build / Production /
Test / Library / Marketplace — matches how incumbent suites are actually
organized (Fusion's workspace switcher: Design/Manufacture/Simulation;
SolidWorks' CommandManager tabs; Onshape's tab types), but those are
document- or app-level splits. Ours can be better because the model is one
substrate: workspaces are *views over the same project*, switchable
instantly, with the agent following context. The founder's "swappable
modules" instinct for Production (3D-printing vs machining) is
architecturally exactly our pack system — a process profile activates its
DFM rule pack, cost model, and output pipeline (market_research.md, "The
workflow ring": quote+DFM-inside-CAD became table stakes via Siemens+Xometry;
per-process rules are stable and automatable).

## Users & jobs

- **Design engineer:** stay oriented — modeling in Build, checking margins
  in Test, sending to a printer in Produce — without hunting through menus.
- **Maker / non-expert:** a guided path: the tabs *are* the workflow; each
  tab's empty state teaches the next step.
- **Agent:** know the user's current phase; scope tools and skills
  (PRD-029) to it — in Produce, think in DFM violations and quotes; in
  Test, think in specs and margins.
- **Team lead:** glance at Test/Produce tabs to see whether a design is
  proven and manufacturable before review.

## Goals

- G1. One project, five workspaces over it: **Build** (model), **Test**
  (prove), **Produce** (make), **Library** (reuse), **Market** (share/find);
  switching is instant and stateless-safe (nothing is "open" per tab that
  can be lost).
- G2. Each workspace composes existing capabilities — no capability is
  *only* reachable through a workspace (tools/REST remain universal).
- G3. Produce is process-profile-driven: choosing FDM vs CNC-3ax vs sheet
  vs IM swaps the checks, outputs, and costs shown (the "swappable
  modules"), backed by PRD-021 packs and PRD-022 connectors.
- G4. The agent is workspace-aware: current workspace + selection ride into
  agent context; workspace-relevant skills auto-load (PRD-029).
- G5. Workspaces ship progressively behind capability presence — a tab
  whose backing PRDs aren't installed shows an honest empty state, not a
  broken surface (same philosophy as FEM tools absent without the extra).

## Non-goals

- Separate documents or data models per tab (Onshape-style tab-as-document)
  — one project substrate, always.
- A CAM workspace — toolpaths stay out (see roadmap non-goals); Produce
  hands off via drawings, flat patterns, sliced 3MF, and quotes.
- Per-workspace permissions (a v6+ enterprise concern, not in this PRD).
- Mobile layouts (desktop browser first).

## Experience

A workspace bar sits at the top of the workbench (under the toolbar, or as
the leftmost rail — resolved in PRD-026's shell design): **Build · Test ·
Produce · Library · Market**, keyboard-switchable (`1–5` when not typing),
deep-linkable (`/p/<project>/test`), with per-tab badge chips (Test: failing
specs count; Produce: DFM violations for the active profile).

**Build** is today's workbench (tree, viewport, inspector, sketcher,
push/pull, chat dock) — plus PRD-016/026/027 improvements as they land.

**Test** re-centers the same viewport on evidence: a left rail of *checks* —
specs (PRD-003) with pass/fail, analysis results (wall, section, inertia,
curvature), FEM cases, motion sweeps and dynamics runs (PRD-030), studies
(PRD-019) — each row expandable to its result, each re-runnable. The
viewport overlays the selected check (wall-thickness heat spot, section
plane, mode shape animation). The agent dock's placeholder changes: "ask
about margins, loads, or what to test next."

**Produce** opens with a per-part process assignment table (part → profile:
FDM/SLA/SLS/CNC-3ax/sheet/IM/"COTS — purchased"), then, for the active
profile: DFM findings with viewport locations (PRD-021), cost estimate,
required outputs (STEP AP242, drawings, flat patterns, sliced 3MF via
PRD-022) with freshness state (stale if geometry changed since last
generation), and quote actions. Switching profile swaps all four panels —
the founder's swappable module, made of packs.

**Library** is the personal/org shelf (PRD-011): installed packages, own
saved reusable parts, search; drag a fastener into the assembly and it
arrives mate-ready. **Market** is the public hub (PRD-031): browse, inspect
(customizer preview via PRD-007), add-to-library.

**Agent path.** `get_workspace {project}` / part of context envelope: the
chat agent receives `{workspace, process_profile?, selection}` on each turn;
`set_workspace` exists so an agent can *take* the user somewhere ("I found
three DFM violations — switching you to Produce" — always announced, never
silent). Workspace context selects default skills (PRD-029).

## Functional requirements

- FR1. Workspace bar with the five tabs; switch < 100 ms perceived (no
  reload; panes mount/unmount over live state); active workspace persisted
  per project (localStorage) and restored.
- FR2. Deep links `/p/<project>/<workspace>` resolve on load; unknown
  workspace falls back to Build.
- FR3. Tabs render capability-aware states: fully functional when backing
  PRDs are present; a designed empty state (what this tab will do + what to
  install/enable) otherwise. No dead controls.
- FR4. Test tab: unified checks rail listing specs, analyses, FEM, motion;
  each check row shows last-run state (pass/fail/stale/never), re-run
  action, and result detail; viewport overlay per check kind for at least
  wall, section, and modal shapes at MVP.
- FR5. Produce tab: per-part process profile stored in the manifest
  (additive key, e.g. `parts.<id>.process`); profile switch swaps DFM
  findings, cost, outputs, and quote panels; outputs show staleness against
  the current geometry hash.
- FR6. Badges: Test shows failing-spec count (live via existing WS events);
  Produce shows active-profile violation count; badges appear only when the
  backing capability is present.
- FR7. Selection and camera are shared across workspaces (switching to
  Test keeps the selected part; overlays apply to it).
- FR8. Agent context envelope carries `{workspace, process_profile,
  selection}`; `set_workspace {project, workspace}` tool exists and emits a
  `workspace_changed` event the UI follows.
- FR9. Keyboard: `1–5` switches workspaces (guarded against text inputs);
  the shortcut map lives in PRD-026's shortcut system.
- FR10. All five tabs work identically self-hosted and in cloud mode
  (PRD-005); Market in local-only mode points at the public marketplace
  read-only (PRD-031 details the offline story).

## Agent surface

New tools: `get_workspace {project}` · `set_workspace {project, workspace}`
· `set_part_process {project, part_id, process}` (Produce assignment;
validates against installed profile packs). New event: `workspace_changed
{project, workspace, client}`. Context: the chat engine's system context
gains the workspace envelope (documented in agent-api.md); MCP clients can
read the same via `get_workspace`.

## Technical approach

- **Frontend:** a workspace router in the existing vanilla-ES-module app —
  a top-level state field (`state.workspace`), a bar component, and per-tab
  layout composition of *existing* panels (tree/viewport/inspector/chat are
  already modular enough to re-arrange; Test/Produce rails are new
  modules). No bundler change. Rendering rides the existing store/actions
  wiring.
- **Manifest:** one additive key per part (`process`), round-tripped by the
  schema-tolerant store; nothing else persisted server-side (active
  workspace is client state; the agent envelope reads it from the request
  context like client identity does).
- **Route/tool packs:** `tools_workspace.py` for the three tools;
  no kernel involvement.
- **Capability detection:** tabs query the registry (`GET /api/tools`) for
  their backing tools (e.g. `check_dfm` present ⇒ Produce is live) — the
  same only-if-runnable philosophy the FEM pack established.
- **Staleness:** Produce outputs compare their recorded content hash
  against the part's current cache key (already computed per rebuild).

## MVP & phasing

- **MVP (with 026):** the bar + router + deep links; Build = current
  workbench; Test with specs/analysis/FEM rows over existing tools; Library
  as read-only view of installed packages (011 MVP); Produce and Market as
  designed empty states. Agent envelope + `get/set_workspace`.
- **Phase 2 (021/022 land):** Produce live — profiles, DFM findings, cost,
  outputs freshness, first quote connector.
- **Phase 3 (031 lands):** Market live; Library gains publish flow.
- **Phase 4:** overlays for every check kind; badge tuning; workspace-aware
  skill auto-loading (029).

## Acceptance criteria

- AC1. Browser session: switch through all five tabs on the rocketry
  example — Build fully functional, Test lists and re-runs a spec + wall
  check with overlay, Library lists the fastener package, Produce/Market
  show their designed empty states (pre-021/031) — zero console errors.
- AC2. Deep link straight into `/p/rocketry/test` lands on Test with state
  restored (test via headless browser).
- AC3. `set_part_process` assigns FDM to the enclosure; with PRD-021
  installed the Produce tab shows its violations; without it, the tool is
  absent from the registry (capability rule, test both ways).
- AC4. Agent asked "what's failing?" while the user is in Test answers from
  spec/check state without the user naming the project or workspace
  (envelope test).
- AC5. `set_workspace` from chat switches the visible tab live via the WS
  event (browser-verified).
- AC6. Full suite green; tab switching does not interrupt an in-flight
  rebuild (event-driven panes only re-render).

## Risks & open questions

- **IA risk — five tabs may be one too many at first:** Market could start
  as a section inside Library. Decide in design review with real users;
  the router cost is identical either way.
- **Naming:** "Produce" vs founder's "Production" vs "Make" — pick in
  design; this PRD uses Produce for the verb-parallel set (Build/Test/
  Produce), and the roadmap keeps the founder's intent either way. **A
  second, higher-stakes naming decision belongs in the same review**: the
  callout at the top of this document records that PRD-005 has claimed
  "workspace" for its org/workspace/project tenancy model, so this PRD's
  own use of "workspace" for its five tabs (title included) must be renamed
  before ship — ruled in PRD-005's design spec, decided here.
- **Test-tab unification** must not fork analysis state: the checks rail
  reads the same stores tools write, or freshness will lie. The staleness
  model (cache-key comparison) needs a test per check kind.
- **Agent-initiated navigation** can feel like losing control — always
  announced in chat, never during user pointer activity, and instantly
  reversible (browser back).

## Competitive references

Fusion's Design/Manufacture/Simulation workspaces prove the mental model;
Onshape's tab sprawl shows the failure mode of tabs-as-documents;
MakerWorld/Printables show Library/Market as the consumer loop
(market_research.md, "Open-source CAD" and "The workflow ring"). We differ:
one model substrate under all tabs, process profiles as open packs instead
of monolithic in-house CAM/sim studios, and an agent that follows — and can
lead — the workspace context.
