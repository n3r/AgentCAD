# PRD-029 — Agent skills & knowledge packs

- **Status:** in-progress (branch `prd-029-agent-skills`, 2026-08-23)
- **Phase:** v5 — daily-driver depth (early; multiplies every later agent feature)
- **Created:** 2026-08-09
- **Origin:** founder idea #4 (Aug 2026), engineering-reviewed
- **Depends on:** — (extends the chat/MCP context machinery)
- **Related:** PRD-018 (generation quality lever), PRD-025 (workspace-scoped loading), PRD-031 (community skill distribution), PRD-024 (skills measured by the bench)

## Problem & motivation

Agents drive AgentCAD well when the task is generic ("make this wall
thicker") and stumble where *craft knowledge* matters: how to design a
snap-fit that survives assembly, when to use a revolve instead of a loft,
what clearances a NEMA 17 mount needs, which OCCT operations are fragile
and how to sequence them. Today that knowledge lives in exactly one place —
the `part_template` cheat-sheet — a single static blob that cannot grow
per-domain without bloating every context.

Founder idea #4 names the fix: skills — curated, loadable guides that teach
an agent how to build specific kinds of parts and run specific workflows.
The pattern is proven in the coding world (Claude Code's skill system:
scoped instructions loaded on demand) and absent in CAD: no competitor
ships domain modeling knowledge as a first-class, versioned, loadable
artifact (market_research.md, "AI-native CAD": Zookeeper leans on doc
retrieval; incumbent copilots are help-chat over manuals). Skills also
compound three other bets: generation (PRD-018) gets domain playbooks,
workspaces (PRD-025) get phase-appropriate context, and the marketplace
(PRD-031) gets a second content type the community can contribute — with
quality measurable by the bench (PRD-024).

## Users & jobs

- **The built-in chat agent / any MCP agent:** load the right playbook at
  the right moment instead of rediscovering craft per session.
- **Engineer:** teach the system once ("here's how WE design test-stand
  frames") and have every future agent session apply it.
- **Community author:** package expertise (a sheet-metal design guide, a
  3DP tolerancing guide) as a distributable, versioned artifact.
- **The product itself:** ship core craft (enclosures, brackets, fits &
  clearances, DFM-per-process, GD&T basics) as maintained content, not
  prompt folklore.

## Goals

- G1. A skill format: markdown instructions + optional structured extras
  (parameter tables, snippet library, checklists, reference geometry
  scripts), with frontmatter (name, description, triggers, version,
  license, provenance) — human-readable, agent-loadable, diffable.
- G2. Deterministic loading mechanics: skills are discovered by
  description/trigger match and loaded *on demand* into agent context
  (never all at once); the active-skill set is visible and auditable in
  the session.
- G3. A core library shipped with the product: authoring craft
  (robust-parametrics patterns, selector recipes, OCCT failure playbook —
  promoted out of the cheat-sheet), part-type playbooks (enclosures,
  brackets/mounts, flanges, snap-fits, threads & fasteners usage), fits &
  clearances tables (ISO 286 basics, printed-part clearances), process
  guides (FDM/SLA/CNC design rules aligned with PRD-021 packs), workflow
  guides (spec-first design, review etiquette for proposals).
- G4. Project- and org-level skills: a `skills/` directory in a project
  (and org store in cloud mode) that agents load with the same mechanics
  — "how we do it here."
- G5. Scoped auto-suggestion: workspace (PRD-025), selection, and task
  phrasing inform which skills are offered/loaded; the agent (or user)
  confirms; MCP clients get the same discovery via tools.
- G6. Measurable value: bench tasks (PRD-024) run with and without
  relevant skills; a skill that doesn't move scores is content debt.

## Non-goals

- Fine-tuning or model training — skills are context, not weights.
- Arbitrary executable plugins — a skill's only executable payload is
  ordinary part-script snippets that run in the same sandboxed kernel
  path as any script (no new execution surface).
- Automatic skill *synthesis* from history (a later idea; manual + curated
  first).
- Replacing docs — skills are agent-operational, docs are human-first;
  they may share source.

## Experience

**Agent path.** The chat system prompt carries a compact index (name +
one-line description) of available skills. When a task matches ("build a
snap-fit lid"), the agent calls `load_skill {name: "snap-fits"}` and
receives the full content; the loaded set is echoed in the session and
capped (context budget); `list_skills {query?}` supports search. MCP
agents use the same two tools — Claude Code driving AgentCAD benefits
identically.

**Human path.** A Skills panel (settings or Library workspace): list of
available skills with provenance badges (core / org / project /
marketplace), enable/disable, version, and a preview. "Teach" flow: save a
markdown file into `project/skills/`; it appears in the index immediately.
In chat, a small chip shows which skills the agent has loaded this turn —
transparency, and a prompt-injection surface the user can inspect.

**Author path.** `agentcad skill new <name>` scaffolds frontmatter +
sections; `agentcad skill lint` validates (frontmatter completeness,
length budgets, snippet syntax — snippets must parse as Python);
publishing to the marketplace rides PRD-031 (signed, versioned, reviewed).

## Functional requirements

- FR1. Skill file format: `SKILL.md` with YAML frontmatter {name (slug),
  description (≤200 chars, the retrieval surface), triggers (optional
  keywords), version, license, author, requires (product capabilities —
  e.g. needs sheet-metal toolkit)}; body sections free-form markdown;
  optional sibling files (snippets/*.py, tables/*.json) referenced
  relatively.
- FR2. Discovery layers with precedence: core (shipped) < org (cloud) <
  project (`<project>/skills/`); same-name overrides higher layer,
  visible as such.
- FR3. `list_skills {query?}` (index + relevance) and `load_skill {name}`
  (full content, size-capped with structured truncation) registered for
  chat and MCP alike; loading is logged in the session transcript and
  surfaced in the UI chip.
- FR4. Context budget: at most N skills / K tokens loaded concurrently
  (config); the agent must unload (or the runtime evicts LRU) beyond
  budget — deterministic, logged.
- FR5. The chat engine's system context includes the compact index and
  the workspace envelope (PRD-025) so suggestion is context-aware;
  `requires` gates listing to capabilities actually present (FEM-needing
  skills hidden without the extra — the house capability rule).
- FR6. Core library at launch: ≥12 skills covering G3's list; each
  reviewed by the bench protocol (FR8) before shipping.
- FR7. Safety: skills are data; the UI labels non-core skills' provenance;
  project skills arriving via clone/marketplace show a first-load consent
  ("this project provides agent instructions — review?") mirroring
  editor-workspace trust prompts; skill content is subject to the same
  prompt-injection hygiene as any retrieved context (no tool-permission
  escalation via skill text — permissions live in the runtime, PRD-005).
- FR8. Bench integration: `agentcad bench --with-skill <name>` compares
  task scores with/without; CI publishes the delta per core skill
  (PRD-024).
- FR9. Cheat-sheet migration: `part_template` remains (compatibility) but
  its content is refactored into core skills; the template tool's payload
  shrinks to the contract + a pointer index.

## Agent surface

New tools: `list_skills {query?}` · `load_skill {name}` (· `unload_skill
{name}` if budget management is explicit — decide in design). New event:
`skill_loaded {session, name, layer}` (UI chip + audit). Chat system
context: skill index + loaded-set echo.

## Technical approach

- **Core:** `agentcad/skills/` in the package (markdown + assets), a small
  loader in `agentcad/core/skills.py` (index build, layering, budget,
  lint) — no kernel involvement; tool pack `tools_skills.py`; chat engine
  (`agent/chat.py`) gains the index injection + load logging (a seam, not
  a fork: the tool executor already stamps identity/context).
- **Lint:** frontmatter schema, size budgets, python-parse for snippets
  (`ast.parse`), link checking — shared by CLI, CI, and marketplace gate.
- **UI:** Skills panel + chat chips on existing panel primitives (PRD-026
  when available; minimal styling otherwise).
- **Storage:** project skills are plain files under `<project>/skills/`
  (git-tracked with the project — they version, branch, and merge like
  everything else, PRD-001 for free).

## MVP & phasing

- **MVP:** format + loader + two tools + chat index injection + 6 core
  skills (robust parametrics, selectors & OCCT failures, enclosures,
  brackets/mounts, fits & clearances, FDM design rules) + project-layer
  skills + lint.
- **Phase 2:** org layer (with 005), Skills panel + chips, workspace-aware
  suggestion (025), 12+ core skills, bench deltas in CI (024).
- **Phase 3:** marketplace distribution + review gate (031), snippet
  libraries with parameterized insertion, skill analytics (which skills
  correlate with green outcomes).

## Acceptance criteria

- AC1. With the snap-fit skill present, the built-in agent asked for "a
  snap-fit lid for the prototyping enclosure" loads the skill (event
  logged, chip shown) and the produced part builds green (scripted
  end-to-end test; content assertions kept loose).
- AC2. `list_skills {query: "sheet"}` ranks the sheet-metal skill first;
  `load_skill` returns capped content with intact sections (unit tests).
- AC3. A project-layer skill overrides a core skill of the same name and
  is labeled as project-provenance in the UI (test + browser check).
- AC4. A skill declaring `requires: [fem]` is absent from the index
  without the extra (capability rule test).
- AC5. Bench delta report runs for one core skill and prints
  with/without scores (harness smoke test; meaningful deltas tracked
  once 024 lands fully).
- AC6. Skill files round-trip through branch/merge like any project file
  (test on top of PRD-001 once landed; file-level until then).
- AC7. Full suite green; `part_template` still returns a valid contract
  payload (compatibility test).

## Risks & open questions

- **Prompt-injection via shared skills** is the marketplace's hardest
  edge: consent-on-first-load + provenance labels + runtime-held
  permissions are the mitigations; red-team tasks belong in the 024
  suite.
- **Skill sprawl/duplication:** the index's ranking and a curation policy
  for core; marketplace duplicates resolved by ratings/verified shelf
  (031).
- **Context budget tuning** (N/K) needs empirical calibration against
  real sessions; start conservative, log evictions.
- **Overlap with docs:** decide per artifact whether the skill embeds or
  links; avoid maintaining the same table twice (single-source where
  possible, e.g. fits tables as JSON used by both).

## Competitive references

Claude Code's skills prove the mechanism; Zookeeper's doc-retrieval and
incumbent help-copilots show the unstructured alternative
(market_research.md, "AI-native CAD"). Nobody ships versioned, loadable,
community-distributable CAD craft. We differ: skills are project-versioned
files in the same git substrate as the model, gated by the same capability
rules as tools, and their value is *measured* — the bench decides what
counts as knowledge.
