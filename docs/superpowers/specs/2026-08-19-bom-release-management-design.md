# PRD-015 BOM & release management — design spec

Grounded in a full seam map (manifest/`_resolved_instances`/`mates`, metrics
cache, `materials.cost_usd_kg`, `SpecRunner.evaluate_specs`, `CheckRunner`
gates, `branches.tag`/`tags.json` referrers, `proposals`/`proposal_review`,
`checks.py` tag-capable ephemeral service, export paths, `manifest_merge`).
This records the decisions + rejected alternatives; the slice plan is the
sibling `docs/superpowers/plans/2026-08-19-bom-release-management.md`.

## Scope — full, all deps met

Hard deps (PRD-001 tags, PRD-002 proposals, PRD-003 specs) and soft deps
(PRD-004 checks, PRD-011 packages, PRD-012 configs, PRD-013 patterns/sub-
assemblies, PRD-014 drawings) are **all completed on main**, so the whole PRD is
buildable — MVP (FR1-5) + revisions/releases (FR6-9, FR12) + bundles/
reproducibility (FR10-11). This also lands **PRD-014's deferred FR4/FR5**
(assembly balloons + on-sheet BOM) by giving `get_bom` to the drawing path.

## Corrections to the PRD's technical approach (from the seam map)

- The BOM builder cannot be "no kernel calls" *via the existing enumeration
  path* — `mates.expand` issues `kernel.request("resolve_assembly")` for polar
  patterns and sub-assemblies (transform composition). **We enumerate with a new
  count-only traversal (Decision 1), so the BOM genuinely makes zero kernel
  calls.**
- The bundle/`get_bom {ref}` "same staged-worktree mechanism PRD-001's merge
  validation uses" is **wrong**: `branches.tree_of`/`pinned` are branch-only and
  `SpecRunner._pinned` raises on a tag. The tag-capable mechanism is
  `checks.py`'s `_ephemeral_service` + worktree materialization. **We reuse that
  (Decision 5).**
- The approval tool is **`proposal_review`**, not `approve_proposal`.
- Materials **already** carry `cost_usd_kg` — FR3's fallback needs no schema
  change.

## 1. The count-only BOM enumeration (Decision 1 — FR1)

A new pure function `bom.count_leaves(service, proj) -> list[LeafCount]` walks the
manifest structurally, **without composing transforms** (so no kernel):

- A native part instance → one leaf `(proj, part, config)` × 1.
- A **pattern** instance (`inst.pattern`) → `(proj, part, config)` × `count`
  (linear and polar alike — count is `count`, placement irrelevant).
- A **sub-assembly** instance (`inst.assembly` with `origin_project`) → recurse
  into the *source* project's instances, multiplying multiplicity through each
  level; ids/keys carry the origin project so a screw in project B counts as B's
  part. Cross-project cycle guard mirrors `mates._expand_subassembly`.

`get_bom` groups leaves by `(origin_project, part, config)`, sums `qty`, and
emits one line per group. `structure: "flat"` returns the rolled-up lines;
`structure: "indented"` returns the tree with level numbers (walk without the
grouping collapse, carry a `level`). Both agree on totals (AC2).

Rejected: reuse `_resolved_instances`/`expand`. It composes world transforms via
the kernel — wasteful and violates the zero-kernel contract when the BOM needs
only counts.

## 2. Line fields, cost, and provenance (Decision 2 — FR2/FR3)

Per line (FR2): `item` (stable 1-based ordinal, sorted by a deterministic key —
`(origin_project, part_id, config)`), `part_id`, `part_number`, `label`,
`config`, `material`, `unit_mass_g`, `unit_cost_usd`, `ext_cost_usd`, `qty`,
`source`, plus `cost_source` (`manual|material_estimate|none`) and a
`mass_source` (`built|stale|unbuilt`).

- **Mass**: read `service._status` / `_config_status` **directly** (never
  `_ensure_built` — Decision keeps zero-kernel), like `get_project` does. Detect
  staleness by recomputing `service._cache_key_for` (pure hash) and comparing to
  the memoized `cache_key`. `mass_source: unbuilt` (no memo — includes the
  post-restart case, documented) or `stale` (memo present, key mismatch) drives a
  `warnings` entry naming the parts; the BOM still renders with the last-known or
  a blank mass, never blocks.
- **Cost**: `unit_cost_usd` from the part's manifest `bom` field if set
  (`cost_source: manual`); else `unit_mass_g × material.cost_usd_kg / 1000` when
  the material carries a cost (`cost_source: material_estimate`); else `none`.
  `ext_cost_usd = unit_cost_usd × qty` only when a unit cost exists.
- **Part number / source**: from the part's manifest `bom` field
  (`set_bom_fields`); a **package part** (PRD-011) with no override inherits
  `part_number`/`url` from its package's `provenance.vendor` — parse the
  `# agentcad:package` header off the script (`provenance.parse`), resolve
  `index`/`package`, read that `package.json`'s `provenance.vendor`. A
  **reference** part shows its import `source`.

## 3. `bom` manifest field + its tool (Decision 3 — FR3, storage)

Per-part BOM inputs live at `manifest parts[i]["bom"] = {part_number?,
unit_cost_usd?, supplier?, url?}` (a raw-dict field read directly, like
`get_project` reads state — no `PartRecord` dataclass change). `set_bom_fields
{project, part_id, part_number?, unit_cost_usd?, supplier?, url?, config?}`
validates (part_number a bounded string; `unit_cost_usd` a non-negative number;
url/supplier bounded strings; unknown keys refused) and writes through
`store.save_manifest`. Merge: add `"bom"` to `manifest_merge._PART_SUBDICTS`
(per-field merge, the `params` precedent) — two branches editing different BOM
fields of one part merge clean. No schema bump (`setdefault` tolerates absence).

`config?` on `set_bom_fields` — v1 stores a single per-part `bom` (config-
agnostic part_number is the common case); a per-config override map is a
documented follow-up (the `_PART_ENTRY_DICTS`/`configs` precedent if demanded).

## 4. Exports (Decision 4 — FR4)

`export_bom {project, format: csv|json, ref?}` writes `exports/bom.<ext>`:

- **CSV**: `csv.writer` with RFC-4180 quoting (Python's default `QUOTE_MINIMAL`
  quotes fields with commas/quotes/newlines and doubles embedded quotes — AC3), a
  header row, one row per line, a **totals row** (mass, cost). `cost_source`
  is its own column so a material estimate is never read as a quote (the PRD's
  cost-honesty risk). `\r\n` line terminator per RFC-4180, UTF-8.
- **JSON**: mirrors FR2 exactly — `{lines: [...], totals: {mass_g, cost_usd},
  warnings, generated_ref}` — sorted keys, `fmt`-free (JSON numbers).

## 5. Ref-pinned BOM + the tag-capable ephemeral service (Decision 5 — FR5, FR10-11)

`get_bom`/`export_bom` accept `ref?` (branch **or tag**) for a reproducible
after-the-fact BOM. Since the merge worktree helper (`branches.tree_of`) is
branch-only, we reuse **`checks.py`'s tag-capable path**: resolve the ref (branch
→ tag → commit), `git worktree add --detach`, spin an ephemeral
`AgentCADService` rooted there with `write_guard=None`/`branch_resolver=None`
(the non-negotiable nulls), compute the BOM against it, tear the worktree down in
a `finally`. To avoid a *third* copy of this mechanism (merge.py, checks.py, us),
**extract a small shared helper** `core/_worktree.py: materialized_service(service,
ref) -> contextmanager` from the checks.py logic, and have both the BOM ref path
and the release bundle use it. (checks.py keeps working; we lift the reusable
core and let checks.py call it, or leave checks.py as-is and have the helper be a
faithful sibling — decided in Slice 2 by whether lifting risks the checks
determinism tests.)

## 6. Release records + revision state machine (Decision 6 — FR6)

`manifest["releases"]` — a map `{<rev>: {name, rev, status, tag, proposal, notes,
approvals: [{principal, ts}], waiver?, gate, bundle?}}`. `rev` auto-sequences
`A, B, …` per project (next = highest existing + 1, or `A`). Status:
`draft → in_review → released → superseded`. Merge: add `"releases"` to
`manifest_merge._ENTRY_DICTS` (+ the `_write_path` set) — per-rev atomic entries
(a release record is a coherent unit like a material/package), so two branches
releasing different revs merge clean and a same-rev edit conflicts (matching
FR6's "append-only, never rewrites").

## 7. `release_start` + the gate via the proposal (Decision 7 — FR7/FR8)

`release_start {project, notes?, waive?}`:
1. Allocate the next `rev`, write a `draft` record.
2. Open a **PRD-002 proposal** with a new `kind: "release"` (Decision 9), source
   = the current release branch. Because the specs gate (`install_specs_gate`)
   and checks gate (`install_checks_gate`) are already in `service.gate_providers`
   and apply to **every** proposal, the gate is evaluated for free — `release_start`
   reads `proposal["gates"]` (specs green? checks green-when-configured?), adds
   its own release-specific checks (working state clean, sub-assembly refs
   version-pinned per PRD-013, drawings regenerable), and returns a **gate
   report** + the proposal id. A red gate leaves the release `draft` with each
   failing check named in `details.gate`.
3. `waive: {reason}` records an explicit waiver into the record + audit trail
   (FR8) so a red specs gate can proceed knowingly; the waiver survives into
   `get_release` and the bundle README. Silent override is impossible (a waiver
   is always a recorded object).

The record moves to `in_review` once the proposal is open.

Rejected: hand-roll `evaluate_specs`/`CheckRunner.run` inside `core/releases.py`.
The proposal already carries both gates; re-invoking them duplicates work and
drifts from the one source of truth reviewers see.

## 8. `release_finalize` + tag pin + immutability (Decision 8 — FR9/FR12)

On approval of the release proposal (`proposal_review` verdict `approve` by a
permitted principal) — auto-finalize, or the idempotent
`release_finalize {project, rev}`:
1. Create tag `release/<rev>` via `branches.tag` at the approved head.
2. **Register the referrer**: append `{release: rev}` to
   `tags.json[<tag>]["referrers"]` (the field is scaffolded-for-PRD-015, empty
   today) — a new write. FR5's "cannot delete/move" is already vacuous (no
   delete/move tool exists), so registration is the whole obligation.
3. Transition the record to `released`, record approvals `[{principal, ts}]` from
   the proposal reviews, mark the prior rev `superseded`.
4. Start the bundle job (Decision 10). Emit `release_changed {project, rev,
   status}`.

**Immutability (FR12)** is mostly structural: no write path can land on a tag's
tree (you can't `switch` to a tag — only `branch_create(from_ref=tag)`), so the
real surface is the **release record**: a `released`/`superseded` record is
append-only — any tool mutating it raises `ConflictError` (`conflict_error`)
directing to branch. `release_start`/`set_bom_fields` on a checked-out branch
whose head is a released tag's commit likewise refuse. This is a manifest-section
guard, not a new store-level `write_guard` (the store guard stays as-is).

## 9. Proposal `release` kind (Decision 9 — agent surface)

`ProposalManager.create` grows `kind: str = "change"`; `"release"` is carried
through `_summary` and the proposal object, and `routes_proposals`/the UI render
release chrome when `kind == "release"`. The review/approval path
(`proposal_review`) is unchanged — attribution (`locks.current_client_id()`)
comes free. This is the one edit to an existing PRD-002 module; done additively
(new optional param, default preserves every existing call).

## 10. The reproducible bundle (Decision 10 — FR10/FR11)

The bundle job, in the ephemeral service materialized at `release/<rev>`
(Decision 5), writes `exports/releases/<rev>/`:
- STEP per part + assembly (`export_part`/`export_assembly`).
- Drawings (PRD-014 PDF+SVG) via `generate_drawing(..., version={ref: rev, date:
  <tag date>})` — the **`version` override** pins the title block so the drawing
  is byte-stable at the tag (FR11, the seam PRD-014 built for exactly this).
- `bom.csv` + `bom.json` at the tag (Decision 4).
- Flat patterns for every sheet-metal part (`flat_pattern`).
- `README.md` — release name, notes, gate report, waiver if any, artifact
  manifest.
- `artifacts.json` — every file with sha256.

Files are produced in the throwaway worktree's `exports/` and **copied out** to
the real project's `exports/releases/<rev>/` before teardown. **Reproducibility
(FR11)**: two runs at the same tag yield identical `artifacts.json` hashes for
drawings/BOM/flat-patterns/README (all deterministic — PRD-014 determinism +
`fmt` + sorted BOM); STEP is compared with its timestamp header normalized, and
the README explicitly lists STEP as the one normalized-comparison class. A zip
sits beside the directory.

## 11. Frontend (Decision 11 — Experience)

- A **BOM view** on the Assembly node (`tree.js` + a new `bom.js`): the table
  (item/qty/part_number/name/config/material/unit_mass/unit_cost/ext_cost/
  source), footer totals, CSV/JSON export buttons, inline edits for the input
  fields via a new `api.patchBom` (the `patchParams` pattern → `set_bom_fields`).
- A **Releases panel** (extend `versions.js` or a new `releases.js`): rev rows
  with status chips, a "Cut release…" flow showing the gate report and opening
  the proposal, reusing `proposals.js`'s approve UI (gated on `kind==="release"`).
  A released row is visibly locked; "start Rev C" branches.
- Browser-verified if the extension is available, else evidence-graded (AC1's
  browser half), machine halves tested.

## 12. Pack boundaries (Decision 12)

New: `core/bom.py`, `core/releases.py`, `core/_worktree.py` (shared helper),
`tools_bom.py`, `routes_bom.py`, `tools_releases.py`, `routes_releases.py`,
`frontend/js/bom.js` (+ `releases.js`/`versions.js` edit), `api.js` `patchBom`.
Edited additively: `manifest_merge.py` (`_ENTRY_DICTS`+`_PART_SUBDICTS`),
`proposals.py` (`kind`), `tools_proposals.py`/`routes_proposals.py` (carry
`kind`). Cores (`service.py`/`tools.py`/`app.py`/`worker.py`) untouched.
Gate-provider load order: any release gate provider loads after `tools_proposals`
(which resets `gate_providers = []`).

## 13. Approaches considered and rejected (summary)

- **Enumerate via `_resolved_instances`** — rejected; composes transforms via the
  kernel. A count-only structural walk is zero-kernel and all the BOM needs.
- **Branch-worktree (merge.py) for ref-pinned BOM/bundle** — rejected; it refuses
  tags. Reuse checks.py's tag-capable ephemeral service.
- **Hand-roll the specs/CI gate in `core/releases.py`** — rejected; the proposal
  already carries both gates. Read `proposal["gates"]`.
- **A store-level `write_guard` for released immutability** — rejected as the
  primary mechanism; no write path reaches a tag's tree, so a release-record
  append-only check is the real (and sufficient) guard.
- **Per-config BOM field map now** — deferred; a single per-part `bom` covers the
  common case, with the `configs` merge precedent ready if demanded.
