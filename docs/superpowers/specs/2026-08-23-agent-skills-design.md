# PRD-029 Agent skills & knowledge packs — design spec

Grounded in a full read of the seams this touches: `agent/chat.py` (380 LOC —
`SYSTEM_PROMPT` is a module constant passed straight to `messages.create`,
tools run in an executor under `locks.set_client_id("chat"|"chat:<session>")`,
the only per-session state is `_history[(project, session)]`, progress is four
bus event types); `core/tools.py` (`ToolRegistry`, `build_registry`, the
`tools_*.py` pack loader, `part_template` → `service.part_template()` →
`{template, cheatsheet}`); `core/templates.py` (the 30 KB `CHEATSHEET`: the
contract + build123d idioms, then nine toolkit sections); `agent/mcp_server.py`
(an HTTP proxy over `GET /api/tools` + `POST /api/tools/{name}` — MCP has no
session, only `X-Agent-Id`); `bench/runner.py` (`run_task` builds a
`ChatEngine` over the derived service's registry; "chat.py may not be edited"
was the *bench's* constraint, not a repo rule); `core/proposals.actor_kind`
(browser = human, `chat`/`mcp` = agent); `config.py` (`load_config()` dict);
`frontend/js/chat.js` (tool chips keyed on `chat_tool_call`/`chat_tool_result`)
and the PRD-026 shell (`materials.js` is the adopted-modal model: `init`,
`open`, an `actions.register` row, a toolbar button, a `#hash`).

This spec records the decisions and the rejected alternatives; the slice plan
is the sibling `docs/superpowers/plans/2026-08-23-agent-skills.md`. The
orchestrator ran the brainstorm autonomously (standing process for roadmap
PRDs); every ruling below is recorded with its reason so a reviewer can
overturn it by name.

## Scope (what this PRD builds now)

The PRD's **MVP in full plus the Phase-2 items that live in this repo**:

- **Build now:** FR1 format + a strict frontmatter subset, FR2 core < project
  layering with visible overrides, FR3 `list_skills`/`load_skill` for chat and
  MCP with transcript/bus logging and the chat chip, FR4 a deterministic
  per-session budget with LRU eviction, FR5 the compact index in the chat
  system context (+ `requires` capability gating), FR6 a core library of
  **≥ 12 skills**, FR7 provenance labels + first-load consent for project
  skills, FR8 `bench run --skills` and the with/without comparison through
  `bench report --baseline`, FR9 the cheat-sheet migration, `agentcad skill
  new|lint`, the Skills modal on the PRD-026 shell.
- **Defer (recorded, not silently dropped):**
  - the **org layer** — it needs PRD-005's org store; the layering code takes
    an ordered list of layers, so an org layer is one more entry, and the
    precedence `core < org < project` is written into `LAYER_ORDER` now with
    `org` unpopulated;
  - **workspace-aware suggestion** (PRD-025 envelope) — there is no
    workspace model yet; the index is already task-phrasing-aware through
    `list_skills {query}` and the agent's own reading of the index;
  - **CI-published per-skill bench deltas** (FR8's second half) — the bench's
    secret job never runs on PRs and the delta needs an API key; the harness
    smoke test (AC5) proves the path, CI wiring is a one-line follow-up in
    `bench.yml` once the task roster is skill-tagged;
  - **marketplace distribution** (Phase 3, PRD-031) — the lint is the gate it
    will run; no index/registry changes here;
  - **snippet parameterised insertion and skill analytics** (Phase 3).

Non-goals unchanged from the PRD: no model training, no executable plugins
(a snippet is an ordinary part script and runs only where any script runs —
through `create_part`/`update_part` under the kernel's confinement), no
automatic synthesis, no doc replacement.

## 1. The skill format (Decision 1 — FR1)

A skill is a **directory** `<name>/` holding `SKILL.md` and optional
siblings; a bare `<name>.md` is accepted for the one-file teach flow. The
directory wins when both exist (reported by the lint as
`duplicate_skill_file`).

```
skills/
  snap-fits/
    SKILL.md
    snippets/cantilever_lid.py      # a complete part script; must build green
    tables/material_strain.json     # data the body refers to
  our-frame-rules.md                # the flat form: frontmatter + body, no siblings
```

`SKILL.md` = frontmatter block + markdown body:

```markdown
---
name: snap-fits
description: Cantilever and annular snap-fit design — deflection, strain, lengths, ratios, FDM/injection rules.
triggers: [snap, snap-fit, cantilever, clip, latch, lid]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---
# Snap-fits
...
```

**Frontmatter is a strict flat subset of YAML, parsed by our own 60-line
reader, not PyYAML.** Keys are `[a-z_]+`; values are a scalar (string, quoted
or bare) or a list (`[a, b]` inline or `- a` block lines). Every value is a
*string*; nothing is coerced (`version: 1.0` stays `"1.0"`, `no` stays
`"no"`). Unknown keys are a lint warning, not an error (forward-compatible).
*Why:* PyYAML is only a transitive dependency today; YAML's implicit typing
(`1.10` → `1.1`, `on` → `True`) is exactly the class of silent corruption a
diffable, human-edited format must not have; and the marketplace gate needs a
parser whose whole behaviour fits on one screen.

Required keys: `name` (slug `[a-z][a-z0-9-]{1,47}`, **must equal the
directory/file name**), `description` (1–200 chars — the retrieval surface),
`version` (`\d+\.\d+\.\d+`). Optional: `triggers` (list of keywords, each
≤ 32 chars, ≤ 24 entries), `license`, `author`, `requires` (list of
capability names, see §4). `license` and `author` are required for a
**core** skill (the library lint profile) and warned-absent for a project
skill (the user profile) — the materials `library`/`user` precedent.

Body: free-form markdown. Sections are `##` headings (the truncation unit,
§3). Python code fences (```` ```python ````) must `ast.parse`; sibling
`snippets/*.py` must parse **and** build green in the kernel (the core-library
test builds every one); `tables/*.json` must parse (through a size-capped
reader — the packages `_json` precedent). Relative links in the body must
resolve inside the skill directory. Size: body ≤ `max_skill_chars`
(default 24 000) is a lint **warning** for a project skill and an **error**
for a core skill — a core skill that needs truncating at load is content
debt, not a runtime problem.

## 2. Layers, discovery, precedence (Decision 2 — FR2, G4)

`agentcad/core/skills.py` — `SkillLibrary`, OCP-free, no kernel involvement.

- **Core layer:** `agentcad/skills/<name>/SKILL.md`, shipped in the wheel
  (hatchling includes every file under `agentcad/`; a packaging test opens a
  core skill through `importlib.resources`-style path resolution from the
  installed package, not the repo).
- **Project layer:** `<project>/skills/` — plain files under the project
  root, so they are git-tracked, branched, merged and restored by PRD-001
  with no new code (AC6 is a test on top of `branches`).
- **Org layer:** `LAYER_ORDER = ("core", "org", "project")`, unpopulated.

`index(project=None)` rescans the layers on every call (one `listdir` per
layer plus one stat per skill; parsed frontmatter is memoised per
`(path, mtime_ns, size)` so a 30-skill index costs microseconds). No
watcher, no cache invalidation bug class. Same-name in a higher layer
**shadows** the lower one: the entry carries `layer: "project"` and
`overrides: "core"`; the shadowed entry is not listed (AC3).

Ranking for `list_skills {query}` is deterministic and runs over **token
sets**, not substrings. Tokens are lowercase `[a-z0-9]+` of length ≥ 3 (if
every token of a query is shorter, they are all kept, so `m8` still searches).
Score = 100 when the query *is* the name; else 60 when a token equals one of
the name's hyphen-parts or a token of ≥ 4 characters appears in the name; 40
per trigger whose own token set meets the query's; 10 per description token
the query also has. Ties break by layer precedence (project first) then name.
A query with no hits returns the full index unranked with `matched: false` so
the agent still sees what exists. Without a query the index is in name order.

*Why token sets:* substring scoring made every short word a match. `"make a
snap fit lid"` contains `"a"`, `"a"` is inside `clamp`, `safe_fillet` and a
dozen other triggers, and over the shipped library the wrong skill won a query
whose subject was named in it. The pinned cases are `"make a snap fit lid"` →
`snap-fits`, `"a bracket for a NEMA 17 motor"` → `brackets-and-mounts`, and
AC2's `"sheet"` → `sheet-metal`, which is unchanged.

## 3. Loading, capping, the budget (Decision 3 — FR3, FR4)

`load(name, project=None, asset=None)` returns the full `SKILL.md` body
capped at `max_skill_chars`. **Truncation is structural:** whole `##`
sections are kept in order until the next would cross the cap; the result
carries `truncated: true` and `omitted_sections: [...]` (headings only) so
the agent knows what it did not get and the remaining bytes are never cut
mid-fence. The first section (the preamble before any `##`) is always kept.
`assets` lists sibling files (`{path, bytes}`); `load_skill {name,
asset: "snippets/x.py"}` returns one asset verbatim (capped at the same
limit, `truncated` honest). *Why not inline every asset:* a snippet library
is the one thing that can legitimately be larger than the prose.

**The budget is the chat engine's, not the tool's.** MCP agents own their
context; the built-in chat does not, so `ChatEngine` keeps
`_skills_loaded[(project, session)]: OrderedDict[key → {tool_use_id, chars,
layer, name, asset}]`. After a successful `load_skill` call the engine records
the load, then evicts LRU entries until `len ≤ max_loaded` (default 4) and
`Σ chars ≤ max_loaded_chars` (default 40 000).

**What is counted, and what an entry is** (both corrected after the first
implementation shipped):

- `chars` is the length of the **`tool_result` the transcript holds** — the
  whole serialized payload — not `result["chars"]`. The content is a fraction
  of it: `provenance`, `assets` and above all `omitted_sections` ride along,
  and a probe measured 768 kB of omitted headings against a 24 000-char body.
  Counting the content alone is a budget that bounds nothing.
- An **asset read is an entry**, keyed `"{name}#{asset}"`, with its own
  `tool_use_id` and its own eviction. It is separate from the body — reading a
  snippet neither refreshes nor displaces the guide — and it is absent from
  the "Loaded this session:" line, because that line is a claim the model acts
  on and one file out of a guide is not that guide. Before this,
  `load_skill {asset: "SKILL.md"}` was unbudgeted and unevictable.
- `SkillBudget` normalizes `max_skill_chars` down to `max_loaded_chars` on
  construction. The engine never evicts the load it is answering (that would
  answer a load with an unload, and loop), so an unclamped truncation cap left
  one skill loaded above the bound forever — a config that looked *stricter*
  silently exceeded the budget.

**Eviction is real context reclamation:** the evicted entry's earlier
`tool_result` content block in `_history` is rewritten in place to
`[skill <name> unloaded to free context budget — call load_skill again if
you need it]` (or `[asset <asset> of skill <name> unloaded …]`). A **re-load**
of an already-loaded key refreshes its position, does not double-count, and
rewrites the copy it supersedes to `[skill <name> was loaded again later in
this conversation — the current copy is below]` — *silently*, publishing no
`skill_unloaded`, because the skill is loaded by the newer block. Only the
newest `tool_use_id` is remembered, so the older block is reclaimed there or
never: before this, two full copies stayed in the transcript while the budget
counted one, and a later eviction stubbed only the newest.

Every eviction publishes `skill_unloaded {project, session, name, asset,
reason: "budget"}` and the loaded skills are listed in the system context's
"Loaded this session" line, so the state is visible and auditable.
*Why rewrite history rather than just forget:* "unloaded" that leaves the
tokens in the transcript is a lie the next turn pays for; replacing one
tool_result's content keeps the Messages API pairing intact and is
idempotent. *Why no `unload_skill` tool:* the PRD leaves it to design; LRU
is deterministic and the agent gains nothing by managing it by hand (YAGNI —
one fewer tool in every tool list).

Budget values come from `config.py` (`skills.max_loaded`,
`skills.max_loaded_chars`, `skills.max_skill_chars`) with the defaults above
and env overrides `AGENTCAD_SKILLS_MAX_LOADED` / `_MAX_LOADED_CHARS` /
`_MAX_SKILL_CHARS` — the `get_kernel_pool_size` precedent; the engine takes
a `SkillBudget` object so tests pin values.

## 4. Capabilities (Decision 4 — FR5's gate, AC4)

`requires` names capabilities from a **closed set** the library can probe:
`fem` (`kernel.handlers.fem.fem_available()`, the same probe
`tools_analysis` uses), `threads` (`bd_warehouse` importable), `sheetmetal`,
`sketch`, `holes`, `specs` (always present — named so a skill can declare
what it is about and a future split of the toolkit has a hook). A skill
requiring a capability that is **absent** is not in the index and
`load_skill` refuses it with `skill_unavailable` naming the capability. A
skill requiring an **unknown** capability is refused the same way (fail
closed — a typo must not leak a skill past the gate) and the lint flags it
as `unknown_capability` (error).

## 5. The tools and the chat seam (Decision 5 — FR3, FR5)

Tool pack `core/tools_skills.py` (loads at `ski` — after `run_checks`,
`sheetmetal`; it reads nothing from its neighbours and registers no gate
provider). Two tools, no more:

- `list_skills {project?, query?}` →
  `{skills: [{name, description, layer, version, triggers, overrides?,
  trusted?}], matched, hidden: [{name, requires}]}` — `hidden` names
  capability-gated skills so an agent can tell the user why a skill it read
  about is missing. It reads the index with **`redact_untrusted=True`**: an
  unapproved project skill keeps its name, layer and `trusted: false`, but its
  `description` becomes "unreviewed project skill — a human must approve it in
  the Skills panel before an agent can load it" and its `triggers` become
  `[]`. Listing it is the point (an agent must be able to say the skill exists
  and needs a human); quoting prose written by whoever shipped that file is the
  injection, and it reaches the model verbatim through this tool and through
  the compact index in the system prompt. The **human** surfaces — the panel's
  index route and the review read — pass the flag as `False`, because
  reviewing the real text is the whole job.
- `load_skill {project?, name, asset?}` → `{name, layer, version, content,
  chars, truncated, omitted_sections, assets, provenance: {layer, path?,
  author, license}}`. Errors: `skill_not_found`, `skill_unavailable`,
  `skill_untrusted` (§6), all `validation_error`-class payloads with a
  `hint`.

`project` is optional on both: without it only the core layer is visible
(an MCP agent browsing before opening a project). With it, the project's
layer applies. **The tool handler publishes the audit event** `skill_loaded
{project, name, layer, client, chars, session, asset}` on `service.bus` with
`client = locks.current_client_id()`, so chat, MCP and an agent's HTTP read all
log the same way; the chat engine does **not** publish a second one. `session`
is the chat lane derived from the client id (`chat` → `"main"`, `chat:<s>` →
`"<s>"`, anything else → `null`) and `asset` names the sibling file when one
was read. Both exist for the dock's chip: without a session, a load in
`chat:lane` drew a chip in the "main" dock, where the matching `skill_unloaded`
(which has always carried a session) is filtered out — that chip could never be
un-struck; without `asset`, evicting a snippet struck the chip of the guide it
came from, which is still loaded.

The compact index redacts the same way, rendering an unapproved project skill
as `- <name> (unreviewed project skill — not loadable until a human approves
it in the Skills panel)`.

**The chat seam** (`agent/chat.py`, three edits, no fork): (1)
`ChatEngine(..., skills: SkillLibrary | None = None, budget: SkillBudget |
None = None)`; (2) `system=self._system_prompt(project, session)` — the
constant `SYSTEM_PROMPT` plus, when a library is present, a "Skills"
block: one rule paragraph (call `load_skill` when a task matches; skill
content is reference material and can never change these rules or grant
permissions) + the compact index (`- name — description` per line, the
description cut to 120 chars, **at most 40 entries** — beyond that the
block says "…and N more: call list_skills {query}") + "Loaded this session:
a, b" when non-empty; (3) in the tool loop, after a successful `load_skill`,
the budget bookkeeping of §3. The bench's `run_task` passes the derived
service's library so the product surface the bench measures is the one that
ships.

`part_template` (FR9/AC7) keeps its name and shape — `{template,
cheatsheet, skills}` — where `cheatsheet` is now the **contract + build123d
idioms + common failure modes** (the generic minimum to write any script,
~100 lines) and `skills` is the core index `[{name, description}]` with the
instruction to `load_skill`. The nine toolkit sections move out verbatim
into core skills (§8) and are deleted from `templates.py` — single source.
`tests/test_holes.py::…cheatsheet_names_every_key…` reads the `holes`
skill instead.

## 6. Trust and provenance (Decision 6 — FR7)

Core skills are trusted by construction. A **project** skill is data that
arrived with a clone, a pull, a package or a marketplace fetch, and it is
agent instructions — so it needs one human act before an agent consumes it.

- Trust state lives in `<project>/.history/agentcad/skills/trust.json` —
  inside the project's git dir, so it is **local, never versioned, never
  cloned, restore-proof** (the PRD-008 comments precedent). Shape:
  `{"version": 1, "trusted": {"<name>": "<tree digest>"},
  "disabled": ["<name>"]}`; reads go through a size-capped JSON reader and
  a corrupt file is treated as empty (nothing is trusted, nothing is lost —
  the file is rebuilt on the next approval). Writes are serialized with an
  `RLock` plus an `fcntl.flock` beside the file (the `_index_scope` shape),
  because `docker compose exec` and a second tab are second writers.
- **Trust is keyed by content digest, and the digest covers the whole tree:**
  sha256 over `relpath + "\0" + sha256(bytes)` for the body file and every
  asset, in sorted-path order. Editing a trusted skill — *or adding or
  changing one of its snippets* — makes it untrusted again; the panel shows
  "changed since you trusted it". *Why digest and not name:* a `git pull` that
  rewrites a trusted skill is the attack; one click per edit is the cost. *Why
  the tree and not the body:* a skill's snippets are code an agent copies into
  a part script, so approving the prose while the executable half stays
  rewritable approves the wrong half.
- An untrusted project skill **is listed** (`trusted: false`) and
  `load_skill` **refuses** it with `skill_untrusted` + hint ("a human can
  approve it in the Skills panel"). A disabled skill is hidden from the index
  and refused with `skill_not_found`-class `skill_disabled`.
- **Granting trust is a route, not a tool:** `POST
  /projects/{p}/skills/{name}/trust` (and `/untrust`, `PATCH …/enabled`),
  refused with 403 unless the caller is a human **named explicitly**:
  `actor_kind(client) == "human"` *and* the id is `browser:<non-empty>` or
  `user:<non-empty>`. `actor_kind` alone is not sufficient — `server/app.py`
  turns a request with no `X-Agent-Id` header into the bare id `browser`, and
  `actor_kind("browser")` is `"human"`, so an agent could approve its own
  instructions by *dropping a header*. `browser`, `chat`, `chat:<s>`, `mcp`,
  `agent:*` and `local` are all refused, and the gate runs before the name
  check so a non-human learns nothing about which skills exist. No agent
  surface can approve agent instructions. *Why not a human-gated tool:* a tool
  in the registry is in every agent's tool list, and "refused for you" is
  noise; the runtime holds the permission (the PRD's own rule).
- **A human may read an untrusted skill.** `GET /projects/{p}/skills/{name}`
  from a human client reads `SkillLibrary.load(..., enforce_trust=False)`
  **directly** — no registry call, no `skill_loaded`, no engine bookkeeping —
  because reviewing a skill is what trusting it is for, and a panel that
  refuses to show the text it is asking you to approve is a consent dialog
  with the body blanked out. `enforce_trust=False` skips exactly one check:
  disabled, invalid, capability-gated and unknown are refused as always. The
  same URL from any other client still goes through `load_skill` and is still
  refused with `skill_untrusted`. *Why no audit event:* a person reading a
  file is not an agent loading instructions, and the chip and the transcript
  bookkeeping both key off that distinction.
- The system-context rule paragraph and each `load_skill` result's
  `provenance` make the injection surface inspectable; the chat chip shows
  the layer (§7). Hosted mode: all skill tools/routes are member-only
  (default deny — no anonymous entry added).

## 7. Browser UI (Decision 7 — human path, AC1/AC3)

On the PRD-026 shell, following `materials.js` exactly:

- **Skills modal** (`frontend/js/skills.js`, `#skills-modal`, action
  `agent.skills` "Skills…" in the *Agent* group, toolbar button, `#skills`
  hash): a list of the project's effective index with a provenance badge
  (`core` / `project`, plus "overrides core" and "needs review" / "changed"
  states), version, enable toggle, and a preview pane showing `content`
  (rendered as plain text in a `<pre>` — never innerHTML) with the
  `assets` list. An **untrusted** skill previews too (the human read of §6),
  with a line above the body saying no agent can load it yet and a **Trust
  this skill** button beside it. Project skills needing review show **Review
  & trust** → the trust route; the modal opens with a one-line consent banner
  when any untrusted project skill exists ("This project provides agent
  instructions — review them before agents can load them"). "Teach" is a
  hint line with the path (`<project>/skills/<name>.md`) — no in-browser
  editor (the file lands in git like any part; the editor is PRD-026's
  script editor territory).
- **Chat chip:** `chat.js` handles `skill_loaded` with a `client` of
  `chat`/`chat:<session>` **whose lane is this dock's** → a `.skill-chip`
  "📘 snap-fits · project" (or "📎 snap-fits · snippets/lid.py" for an asset
  read) in the dock, distinct from tool chips; `skill_unloaded` → the chip
  with the matching `name` *and* `asset` gets a struck "unloaded" state. The
  lane filter is `skills_model.sessionOf(ev.client)`, the browser's copy of
  `tools_skills.chat_session`. Transparency for the user; an inspectable
  prompt-injection surface.

Route pack `server/routes_skills.py`: `GET /projects/{p}/skills` (index with
trust/disabled state — the modal's one fetch, read **unredacted** from
`service.skills.index(p)` because this is the human surface), `GET
/projects/{p}/skills/{name}` (`load` payload; `?asset=`; the human read of §6
for a human client, `load_skill` through the registry for anyone else), the
three human-only writes of §6. Refusals from the library are `AppError`s → 404/422
through the house envelope.

## 8. The core library (Decision 8 — FR6, G3, FR9)

Fourteen skills at launch, all under `agentcad/skills/`, all passing the
`library` lint profile, every code fence parsing and every snippet building
green (one parametrised test each):

Promoted from the cheat-sheet — the eight toolkit sections (`ROBUSTNESS
TOOLKIT`, `HOLE WIZARD`, `SHEET METAL`, `DESIGN SPECS`, `CONSTRAINT SKETCH
SOLVER`, `THREADS & FASTENERS`, `RIBS, BOSSES & DRAFT`, `CONNECTORS & MATES`)
are **deleted** from `templates.py`, single source. The **selectors** block
and `Common failure modes` are the deliberate exception: they are **copied**,
not moved, because they are the generic minimum for writing *any* part script
and the sheet is what an agent reads first
(`tests/test_part_template_compat.py` pins both halves). The skills:
`robust-parametrics` (ROBUSTNESS TOOLKIT + parametric-guard patterns),
`selectors-and-occt-failures` (selectors in depth, common failure modes, the
degenerate-boolean/fillet/shell playbook),
`patterns`, `holes`, `threads-and-fasteners` (`requires: [threads]`),
`ribs-bosses-draft`, `sketch-solver`, `sheet-metal`, `design-specs` (+ the
spec-first workflow guide), `assemblies-and-mates`.

Authored new: `enclosures` (shell/lid/lip/bosses, wall tables per process;
snippet), `snap-fits` (cantilever/annular design rules, strain table;
snippet — AC1's skill), `brackets-and-mounts` (L-bracket/gusset/NEMA 17
clearances; `tables/nema.json`; snippet), `fits-and-clearances` (ISO 286
basics, printed-part clearances; `tables/iso286.json`),
`fdm-design-rules` (FDM process rules aligned with PRD-021's pack shape).
Plus `fem-workflow` (`requires: [fem]`) — the capability-gated skill AC4
needs in the shipped library, not only in a fixture. That is 16; the floor
is 12.

Each authored skill is ≤ 12 700 chars (about half the 24 000 cap — room for
growth without truncation; the largest shipped is `fem-workflow` at 12 655), cites its sources in a final `## Sources` section, and
contains no aggregator names (the materials rule). Bench review of each
core skill (the PRD's "reviewed by the bench protocol before shipping") is
deferred with the CI delta (Scope) — the enforceable ship gate here is the
lint + green snippets + the scripted AC1 run.

## 9. Bench integration (Decision 9 — FR8, AC5)

`agentcad bench run --skills all|none|<name>[,<name>]` (default `all`)
restricts the derived service's library: `none` passes `skills=None` to the
engine (no index) and an empty `only` set to the task service, a list keeps
only those names on both — a skill outside the selection is refused as
`skill_not_found` with a hint naming `bench --skills` (the library's own
`only` semantics; one condition, one name — ruling recorded at Slice 5). `run.json` records `skills: {"mode": ..., "names": [...]}`;
`score.json` stays byte-identical (the selection is provenance, not a
score). The with/without comparison is `bench report --baseline <without>
<with>` — the existing command, which already prints per-task deltas — so
AC5's smoke test runs one scripted-client task twice and asserts both score
lines and the delta line. `--baseline` takes a baseline *document* (numbers),
so the without-run's report is converted with the one filter `docs/bench.md`
shows. No new report command (YAGNI).

## 10. CLI (Decision 10 — author path)

`agentcad skill new <name> (--project <p> | --dir <path>)` writes a
scaffold (`<name>/SKILL.md` with every frontmatter key, section headings,
a `snippets/` README line); refuses to overwrite. `agentcad skill lint
[<path> …] [--core] [--profile library|user]` prints one row per finding
(`path: level code: message`) and exits 1 on any error; `--core` lints the
shipped library with the `library` profile — the test suite runs the same
function, so CI is the gate. Shared lint function = the marketplace gate's
future entry point (`skills.lint_skill(path, profile)`).

## 11. Events, errors, docs (Decision 11)

Events: `skill_loaded {project, name, layer, client, chars, session, asset}`,
`skill_unloaded {project, session, name, asset, reason}`. Neither is
`project_changed` (no manifest moves); trust edits are local state and emit
`skills_changed {project}` so an open modal refreshes. A **human's** review
read publishes nothing at all (§6).

Errors are `ValidationError` subclasses with stable `type`s:
`skill_not_found`, `skill_unavailable`, `skill_untrusted`, `skill_disabled`,
`skill_invalid` (a project skill whose frontmatter fails the lint is listed
with `invalid: true` + the first problem and refused on load — a broken
file must be visible, not silent).

Docs: `docs/skills.md` (format, layers, trust, budget, authoring, the lint,
bench use), `docs/agent-api.md` (the two tools + events + the
`part_template` change), `docs/user-guide.md` (the modal), `docs/bench.md`
(`--skills`), `AGENTS.md`/`CLAUDE.md` traps, a changelog entry per commit.

## 11b. Acceptance, criterion by criterion

- **AC1** — `tests/test_skills_acceptance.py::test_ac1_snap_fit_lid_loads_the_skill_and_builds_green`:
  a scripted Anthropic client (the bench's `CLIENT_FACTORY` pattern) answers
  "a snap-fit lid for the prototyping enclosure" with `load_skill
  {name: "snap-fits"}` then `create_part` with the skill's own snippet; the
  test asserts a `skill_loaded` event on the bus with `layer: core`, the
  "Loaded this session" line in the next request's system prompt, and the
  part's `ok: true` with positive volume. The chip is a `chat.js` handler
  over the same event (browser-checked in the final AC pass).
- **AC2** — `list_skills {query: "sheet"}` ranks `sheet-metal` first, and so
  do the sentence-shaped queries a real agent sends (`"make a snap fit lid"` →
  `snap-fits`, `"a bracket for a NEMA 17 motor"` → `brackets-and-mounts`);
  `load_skill` on a fixture whose body exceeds the cap returns
  `truncated: true`, every kept section byte-intact, `omitted_sections`
  naming the rest.
- **AC3** — a project skill named `enclosures` shadows the core one:
  `layer: project`, `overrides: core`, the core entry absent; the route
  payload the modal renders carries the same fields (browser-checked).
- **AC4** — `fem-workflow` (`requires: [fem]`) is absent from the index when
  `fem_available()` is monkeypatched false and `load_skill` refuses it with
  `skill_unavailable`; an unknown capability behaves the same.
- **AC5** — `tests/test_bench_skills.py`, and the acceptance file *runs* the
  same scenario rather than describing it: one scripted task twice, `--skills
  none` and `--skills snap-fits`, through the real CLI; `bench report
  --baseline` prints both scores and the delta line and exits 1 on a
  regression; `run.json` carries the selection, `score.json` does not.
- **AC6** — `tests/test_skills_branching.py`: a project skill written on a
  branch is absent after switching back and present again on return
  (PRD-001 `branches`), and a merge carries it over.
- **AC7** — `part_template` still returns `template` + a `cheatsheet`
  containing the CONTRACT block, plus the `skills` index; the full suite is
  green with the count cited in the changelog.

## 12. Pack boundaries (Decision 12)

New files: `core/skills.py`, `core/skills_lint.py`, `core/tools_skills.py`,
`server/routes_skills.py`, `agentcad/skills/**`, `frontend/js/skills.js`,
`docs/skills.md`. Edited cores, each minimally: `agent/chat.py` (the three
seam edits), `core/service.py` (`self.skills = SkillLibrary(store)` +
`part_template` payload), `core/templates.py` (sections removed),
`server/app.py` (engine construction passes the library), `cli.py` (the
`skill` subcommand), `bench/cli.py` + `runner.py` (`--skills`), `config.py`
(budget getters), `frontend/js/chat.js`, `main.js`, `index.html`, `app.css`.

## 13. Approaches considered and rejected (summary)

- **Inject every skill into the system prompt** — the cheat-sheet problem at
  16× the size; rejected by the PRD's G2.
- **Retrieval (embedding) ranking** — non-deterministic, a new dependency,
  and a 16–40 entry index does not need it; a keyword score the tests can
  pin is enough (G2 "deterministic").
- **PyYAML frontmatter** — implicit typing corrupts versions and booleans;
  a strict subset is safer and dependency-free (§1).
- **`unload_skill` tool** — LRU with history rewrite gives the same guarantee
  with one fewer tool (§3).
- **Trust as a tool gated on `actor_kind`** — the permission must not be in
  the agent's tool list at all; a route is the runtime holding it (§6).
- **Trust keyed by name** — defeated by a pull that rewrites the file (§6).
- **Trust file inside the project tree** — cloned along with the skill,
  which is the thing it is meant to gate; `.history/agentcad/` is local (§6).
- **A new `bench skill-delta` command** — `report --baseline` already prints
  deltas (§9).
- **Org layer as a stub directory** — an unpopulated layer name in the order
  tuple is the whole seam PRD-005 needs (§2).
