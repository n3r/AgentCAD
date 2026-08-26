# PRD-025 — Modes: Design · Testing · Production · Marketplace

- **Status:** pending
- **Phase:** v5 — daily-driver depth (frame); Production/Marketplace content matures in v6
- **Created:** 2026-08-09
- **Origin:** founder idea #1 (Aug 2026), engineering-reviewed; direction validated by the Aug-2026 UX study (see the third callout below)
- **Depends on:** PRD-026 (shell implements the frame — completed) · content per tab: PRD-003/035/019/030 (Testing), PRD-021/022/036/037 (Production), PRD-031/031c + PRD-011 (Marketplace, Library folded in) — all soft/staged
- **Related:** PRD-016, PRD-027, PRD-029, PRD-034 (the Design tab's chassis)


> **Naming (settled 2026-08-24, PRD-005 close-out).** This concept was
> originally "workspaces". PRD-005's tenancy took that word for its
> `org → workspace → project` hierarchy (shipped, user-facing), and the
> PRD-026 shell already uses `workspace` internally as the per-tab
> layout-memory localStorage key. To avoid a three-way collision, these
> Build · Test · Produce · Library · Market tabs are **modes**. The tenancy
> "workspace" and the shell's internal layout key are unchanged; only this
> UI concept was renamed.

> **Naming collision with PRD-005, ruled on there (design spec
> `docs/superpowers/specs/2026-08-24-multi-tenant-cloud-design.md`, "Scope
> rulings").** PRD-005's tenancy model claims `org → mode → project` as
> a first-class, user-facing noun: the mode switcher, the
> `X-Agentcad-Mode` header, the `?mode=` query fallback, the
> "Org members…"/"Agent tokens…" panels, and `docs/deployment.md`'s
> "Organisations, modes and roles" all use "mode" for a tenant's
> sub-org — a real access-control boundary a person switches *into*. This
> PRD's title and this PRD's own text (G1's "five modes", FR1's
> "Mode bar", `state.mode`, `set_mode`, the
> `mode_changed` event) use the *same word* for something else
> entirely: a view/tab over one project, with no access-control meaning at
> all. The two are not the same concept wearing one name by coincidence —
> a PRD-005 mode can *contain* several projects, each of which has all
> five of this PRD's tabs. **The ruling: PRD-005 keeps "mode"; this PRD
> renames its tabs to a different word before it ships** (the shipped shell
> already has a `#mode` DOM id and layout `localStorage` keys that
> predate both PRDs and are deliberately left alone — this collision is
> about a *future* user-facing label, not that existing internal slot name).
> Pick the replacement in this PRD's own design review (its "Naming" risk
> below already has an open slot for exactly this kind of choice) — a
> plausible option is dropping "mode" for the tab bar entirely and
> calling it what it is ("phase", "tab", or the founder's per-tab nouns
> alone), but that choice is this PRD's to make, not PRD-005's.

> **Direction validated (2026-08-25, UX study).** A four-round interactive
> mockup study (internal artifacts) tested this PRD's IA against novel
> alternatives; the founder rejected every novel paradigm and validated a
> "familiar workstation": desktop-CAD conventions (SolidWorks/Fusion/
> FreeCAD/Bambu Studio) rendered in the product's existing visual language,
> then hardened by an adversarial two-reviewer pass. Resolutions folded
> into this PRD: **(1)** the tab set is **four, in the founder's own
> nouns — Design · Testing · Production · Marketplace** — with Library
> folded into Marketplace as its "My Library" tab plus insert flows (this
> settles the "five tabs may be one too many" risk in exactly the
> direction it anticipated, inverted); **(2)** the tab-bar *container*
> word is still owed its rename per the PRD-005 ruling above — "phase" and
> plain "tab" lead; the per-tab nouns themselves are settled; **(3)** the
> Design tab's chassis is specified by PRD-034 (feature tree + timeline +
> properties + script drawer, three-way selection sync) and per-tab
> content by PRD-035 (Testing), PRD-036/037 (Production), PRD-031a/b/c
> (Marketplace); **(4)** two review-derived invariants bind every tab:
> *state honesty* — derived results (checks, studies, slices, outputs)
> are keyed to input hashes and render an explicit out-of-date state,
> never a stale number as current (FR5's freshness model, generalized) —
> and *affordance honesty* — FR3's "no dead controls" extended: any
> not-yet-wired control is visually distinct and says so, never styled
> identically to a live one.

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
substrate: modes are *views over the same project*, switchable
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

- G1. One project, four tabs over it: **Design** (model), **Testing**
  (prove), **Production** (make), **Marketplace** (share/find, with
  Library as its "My Library" side); switching is instant and
  stateless-safe (nothing is "open" per tab that can be lost).
- G2. Each mode composes existing capabilities — no capability is
  *only* reachable through a mode (tools/REST remain universal).
- G3. Produce is process-profile-driven: choosing FDM vs CNC-3ax vs sheet
  vs IM swaps the checks, outputs, and costs shown (the "swappable
  modules"), backed by PRD-021 packs and PRD-022 connectors.
- G4. The agent is mode-aware: current mode + selection ride into
  agent context; mode-relevant skills auto-load (PRD-029).
- G5. Modes ship progressively behind capability presence — a tab
  whose backing PRDs aren't installed shows an honest empty state, not a
  broken surface (same philosophy as FEM tools absent without the extra).

## Non-goals

- Separate documents or data models per tab (Onshape-style tab-as-document)
  — one project substrate, always.
- A CAM mode — toolpaths stay out (see roadmap non-goals); Produce
  hands off via drawings, flat patterns, sliced 3MF, and quotes.
- Per-mode permissions (a v6+ enterprise concern, not in this PRD).
- Mobile layouts (desktop browser first).

## Experience

A tab bar sits at the top of the workbench (under the toolbar, or as
the leftmost rail — resolved in PRD-026's shell design): **Design ·
Testing · Production · Marketplace**, keyboard-switchable (`1–4` when not
typing), deep-linkable (`/p/<project>/testing`), with per-tab badge chips
(Testing: failing specs count; Production: DFM violations for the active
profile).

**Design** is today's workbench (tree, viewport, inspector, sketcher,
push/pull, chat dock) — grown into the familiar chassis PRD-034 specifies
(feature tree + history timeline + properties + script drawer, three-way
selection sync), plus PRD-016/027 improvements as they land.

**Testing** re-centers the same viewport on evidence: a left rail of
*checks* — specs (PRD-003) with pass/fail, analysis results (wall,
section, inertia, curvature), simulation studies (PRD-035: persisted
setups, hash-keyed results, staleness), motion sweeps and dynamics runs
(PRD-030), optimization studies (PRD-019) — each row expandable to its
result, each re-runnable. The viewport overlays the selected check
(wall-thickness heat spot, section plane, mode shape animation, study
colormaps). The agent dock's placeholder changes: "ask about margins,
loads, or what to test next."

**Production** opens with the routing plan (PRD-036: part → route —
print/CNC/order/provided — with costs, leads and readiness), then, per
route: DFM findings with viewport locations (PRD-021), cost estimate,
required outputs (STEP AP242, drawings, flat patterns, sliced jobs via
PRD-037/022) with freshness state (stale if geometry changed since last
generation), and quote actions. Switching a route swaps its panels — the
founder's swappable module, made of packs.

**Marketplace** is the public hub (PRD-031a/b/c: browse, galleries,
comments/ratings, remix, customizer preview via PRD-007) with **My
Library** as its personal shelf (PRD-011): installed packages, own saved
reusable parts, collections, search; drag a fastener into the assembly
and it arrives mate-ready.

**Agent path.** `get_mode {project}` / part of context envelope: the
chat agent receives `{mode, process_profile?, selection}` on each turn;
`set_mode` exists so an agent can *take* the user somewhere ("I found
three DFM violations — switching you to Produce" — always announced, never
silent). Mode context selects default skills (PRD-029).

## Functional requirements

- FR1. Tab bar with the four tabs; switch < 100 ms perceived (no
  reload; panes mount/unmount over live state); active tab persisted
  per project (localStorage) and restored.
- FR2. Deep links `/p/<project>/<tab>` resolve on load; unknown
  tab falls back to Design.
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
- FR7. Selection and camera are shared across modes (switching to
  Test keeps the selected part; overlays apply to it).
- FR8. Agent context envelope carries `{mode, process_profile,
  selection}`; `set_mode {project, mode}` tool exists and emits a
  `mode_changed` event the UI follows.
- FR9. Keyboard: `1–4` switches tabs (guarded against text inputs);
  the shortcut map lives in PRD-026's shortcut system.
- FR10. All four tabs work identically self-hosted and in cloud
  deployments (PRD-005); Marketplace in local-only deployments points at
  the public marketplace read-only (PRD-031 details the offline story).

## Agent surface

New tools: `get_mode {project}` · `set_mode {project, mode}`
· `set_part_process {project, part_id, process}` (Produce assignment;
validates against installed profile packs). New event: `mode_changed
{project, mode, client}`. Context: the chat engine's system context
gains the mode envelope (documented in agent-api.md); MCP clients can
read the same via `get_mode`.

## Technical approach

- **Frontend:** a mode router in the existing vanilla-ES-module app —
  a top-level state field (`state.mode`), a bar component, and per-tab
  layout composition of *existing* panels (tree/viewport/inspector/chat are
  already modular enough to re-arrange; Test/Produce rails are new
  modules). No bundler change. Rendering rides the existing store/actions
  wiring.
- **Manifest:** one additive key per part (`process`), round-tripped by the
  schema-tolerant store; nothing else persisted server-side (active
  mode is client state; the agent envelope reads it from the request
  context like client identity does).
- **Route/tool packs:** `tools_mode.py` for the three tools;
  no kernel involvement.
- **Capability detection:** tabs query the registry (`GET /api/tools`) for
  their backing tools (e.g. `check_dfm` present ⇒ Produce is live) — the
  same only-if-runnable philosophy the FEM pack established.
- **Staleness:** Produce outputs compare their recorded content hash
  against the part's current cache key (already computed per rebuild).

## MVP & phasing

- **MVP:** the bar + router + deep links; Design = current workbench;
  Testing with specs/analysis/FEM rows over existing tools; Marketplace's
  My Library tab as read-only view of installed packages (011); Production
  and the public Marketplace as designed empty states. Agent envelope +
  `get/set_mode`.
- **Phase 2 (021/022/036/037 land):** Production live — routing plan,
  DFM findings, cost, outputs freshness, first quote connector.
- **Phase 3 (031b/c land):** public Marketplace live; My Library gains
  the publish flow.
- **Phase 4:** overlays for every check kind; badge tuning; mode-aware
  skill auto-loading (029).

## Acceptance criteria

- AC1. Browser session: switch through all four tabs on the rocketry
  example — Design fully functional, Testing lists and re-runs a spec +
  wall check with overlay, Marketplace's My Library tab lists the
  fastener package, Production and the public Marketplace show their
  designed empty states (pre-021/031) — zero console errors.
- AC2. Deep link straight into `/p/rocketry/testing` lands on Testing
  with state restored (test via headless browser).
- AC3. `set_part_process` assigns FDM to the enclosure; with PRD-021
  installed the Production tab shows its violations; without it, the tool
  is absent from the registry (capability rule, test both ways).
- AC4. Agent asked "what's failing?" while the user is in Testing answers
  from spec/check state without the user naming the project or tab
  (envelope test).
- AC5. `set_mode` from chat switches the visible tab live via the WS
  event (browser-verified).
- AC6. Full suite green; tab switching does not interrupt an in-flight
  rebuild (event-driven panes only re-render).

## Risks & open questions

- **IA risk — RESOLVED (2026-08-25):** the tab count question closed in
  the UX study: four tabs, Library folded into Marketplace as "My
  Library" — founder-validated on an interactive mockup, the inverse of
  the originally suspected merge direction.
- **Naming — half resolved:** the per-tab nouns are settled in the
  founder's own words (Design · Testing · Production · Marketplace; the
  UX study's mockups shipped those labels). Still open, and owed before
  ship per the PRD-005 ruling recorded above: the tab-bar *container*
  word (this PRD provisionally says "tab"; "phase" is the alternative) —
  decided in this PRD's design review, including whether `get/set_mode`
  and `mode_changed` follow the rename.
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
lead — the mode context.
