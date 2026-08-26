# PRD-036 — Production planning & routing

- **Status:** pending
- **Phase:** v6 — manufacturing (the Produce mode's entry surface)
- **Created:** 2026-08-25
- **Origin:** founder direction from the Aug-2026 UX study ("different
  parts can be done very differently — print some, CNC some, just order
  screws") + founder idea #1b
- **Depends on:** PRD-025 (mode frame; owns the `parts.<id>.process`
  manifest key) · PRD-015 (BOM — completed) — soft: PRD-021 (DFM/cost
  packs), PRD-022 (connectors), PRD-037 (print studio), PRD-011
  (supplier metadata — completed)
- **Related:** PRD-013, PRD-014, PRD-023

## Problem & motivation

An item is never manufactured one way. The founder's example is exact:
a gearbox is two printed housings, two machined shafts, and twenty-four
ordered screws. Today the product can *export* everything (STEP/3MF/
drawings/BOM) but has no surface that answers the production question:
**for each part, how will it be made, what does that cost, what is
missing, and what do I hand to whom?** PRD-021 (rules/costs) and PRD-022
(connectors) build the per-process machinery; PRD-025 sketches the
Produce mode around a process table. This PRD is that table grown into
the actual planning workflow the UX study validated: a per-part routing
board with roll-ups, honest readiness states, and one handoff artifact
per route. It also records the CNC scope decision the study surfaced
(below) so the roadmap's non-goal and the founder's ask stop silently
contradicting each other.

## Users & jobs

- **Maker / engineer:** route every part (print / machine / order /
  provided), see total cost and lead time, and get each route's output
  bundle without hunting through export menus.
- **Purchaser:** the order route produces a supplier-grouped order list
  they can actually send.
- **Machine-shop counterpart:** the CNC route hands them a complete,
  standards-grade package (STEP AP242 + PMI, drawing, stock note) — not
  a mesh and a prayer.
- **Agent:** read and set routings, explain trade-offs ("printing the
  gear saves $40 but fails the DFM bending rule"), and prepare bundles.

## Goals

- G1. A production plan per assembly: every BOM line carries a route
  (`print` / `cnc` / `order` / `provided`), a cost and lead estimate,
  and a readiness state; totals roll up live.
- G2. Each route has one artifact contract: print → a sliced job
  (PRD-037); cnc → a handoff pack (AP242+PMI, drawing, stock/setup
  sheet); order → a supplier order line; provided → nothing owed.
- G3. Readiness is honest and staleness-aware: outputs record the
  geometry hash they were generated from (the PRD-025 freshness model);
  a changed part flips its route to "regenerate".
- G4. Costs deepen with what is installed: catalog/package prices for
  `order` (PRD-011 supplier metadata), print estimates from PRD-037,
  DFM-pack cost models from PRD-021 — with a visible "estimate basis"
  so a heuristic never masquerades as a quote (PRD-022 brings quotes).
- G5. The plan is data, not a view: exportable (CSV/JSON beside the
  PRD-015 BOM), diffable in proposals, and readable by agents.

## Non-goals

- In-house CAM/toolpathing — the roadmap non-goal stands for this PRD:
  the CNC route is DFM + handoff + quote, not G-code. **Recorded
  founder request (Aug 25 2026):** the UX study's founder direction
  included "a program for a CNC machine"; the PRD-030 precedent (a
  non-goal retired by explicit founder decision) applies, but that
  retirement has not happened. If it does, bounded 2.5D post-processing
  (facing/drilling/contour on prismatic parts) becomes a *new* PRD; it
  is deliberately not smuggled in here.
- Scheduling, shop-floor tracking, MES — out of scope entirely.
- Marketplace "have it made for me" flows — PRD-022/031 territory.

## Experience

Produce mode opens on the plan: one row per rolled-up BOM line — part,
qty, route selector, material, unit cost × qty, lead, readiness chip
(`ready` / `regenerate` / `blocked: 2 DFM findings` / `no supplier`).
Totals foot the table. Switching a row's route swaps its panel: print
shows the PRD-037 job summary and opens the print studio; cnc shows the
DFM findings (PRD-021, when installed), stock suggestion, and "generate
handoff pack"; order shows supplier/part-number/price (from package
metadata) with quantity math; provided just records who supplies it.
"Prepare all" regenerates every stale artifact and reports what it
could not do and why. The agent can run the same loop conversationally,
and a proposal that changes geometry shows, in review, which routes it
staled (PRD-002 packet line).

## Functional requirements

- FR1. Routing persists on the PRD-025 manifest key (`parts.<id>.
  process`), extended additively with route-specific fields
  (`supplier`, `stock`, `job_ref`); absence of a route renders
  "unrouted", counted in the plan header.
- FR2. The plan table derives rows from the PRD-015 BOM roll-up (same
  zero-kernel path), joined with routes, estimates, and readiness; it
  never triggers builds to render.
- FR3. Route artifacts and their hashes: `cnc` handoff pack = AP242+PMI
  export (PRD-017) + drawing (PRD-014) + a generated stock/setup sheet;
  `print` delegates to PRD-037's job object; `order` needs no artifact
  but validates supplier metadata presence. Every artifact records its
  source geometry hash; mismatch → `regenerate`.
- FR4. Cost/lead: `order` from package/catalog metadata; `print` from
  PRD-037 estimates; `cnc` from PRD-021 cost models when installed,
  else "no estimate" (never a made-up number). Each estimate carries
  its basis label; totals mark themselves incomplete when any row
  lacks one.
- FR5. Readiness aggregates: DFM findings (PRD-021) and study verdicts
  (PRD-035) surface as `blocked` chips with links; blocked rows are
  advisory, never locks.
- FR6. Export: `production_plan.csv`/`.json` beside the BOM exports;
  stable column set; excluded from byte-determinism claims exactly as
  DXF is (timestamps live in the export envelope, not the plan).
- FR7. Tools: `get_production_plan {project}` · `set_part_route
  {project, part_id, route, fields}` · `prepare_route {project,
  part_id}` (regenerates that route's artifact) — post-state returns;
  `prepare` under a budget reports partial completion honestly.
- FR8. Proposals: the PRD-002 review packet lists routes staled by the
  change (computed from hashes, zero kernel calls).

## Agent surface

The three tools above; structured errors (`route_unsupported`,
`supplier_metadata_missing`, `artifact_stale`, `dfm_pack_absent`).
`get_production_plan` returns rows with basis-labeled estimates so an
agent never quotes a heuristic as a price.

## Technical approach

Service: `core/production.py` (plan derivation, hashing, artifact
registry) + `tools_production.py` + a Produce-mode routes pack. Reuses:
BOM roll-up (PRD-015), exports (014/017), freshness-by-cache-key
(PRD-025's model), supplier metadata from package manifests (PRD-011 —
extend the catalog schema additively where missing). No kernel changes;
`prepare_route` orchestrates existing export tools.

## MVP & phasing

- **MVP:** plan table over BOM + routes + `order` route complete
  (supplier lines, order export) + `cnc` handoff pack from existing
  exports + readiness hashes + the three tools. Costs only where
  metadata exists.
- **Phase 2 (with 037):** print route live end-to-end.
- **Phase 3 (with 021/022):** DFM blocked-chips, cost models, real
  quotes; proposal staleness lines.

## Acceptance criteria

- AC1. Browser: the gearbox example routes screws→order (supplier line
  appears with price math), shafts→cnc, housings→print; totals foot;
  changing a housing's thickness flips exactly its row to
  `regenerate` — live.
- AC2. `prepare_route` on the cnc shaft produces the pack (AP242 +
  drawing + setup sheet) whose recorded hash matches; re-running with
  no change is a no-op that says so.
- AC3. Ordering export produces a supplier-grouped CSV that matches
  the table; JSON round-trips.
- AC4. Without PRD-021 installed, cnc rows show "no estimate" and no
  DFM chip — and the tool registry proves the absence (both ways).
- AC5. A proposal changing routed geometry lists the staled routes in
  its packet without kernel calls (counter-asserted).
- AC6. Full suite green; plan rendering triggers zero builds (asserted
  like the thumbnail rule).

## Risks & open questions

- **Estimate credibility:** the honest-basis label must be designed so
  users don't read heuristics as quotes — review with real users.
- **Supplier metadata coverage** in the current catalog is partial; the
  MVP must degrade to "no supplier on file" without shame.
- **Where does per-config routing live** (a config may change material
  and thus route)? Likely `configs.<name>.process` additively — decide
  in design with PRD-012's merge rules in the room.
- **CNC scope pressure** (see Non-goals): if the founder retires the
  CAM non-goal, scope the successor PRD then — this one is complete
  without it.

## Competitive references

Fusion's Manufacture workspace assumes you *are* the machinist; Xometry-
style DFM+quote embeds (market_research.md, "The workflow ring") assume
you outsource everything. Real projects mix routes per part — no
incumbent shows one plan across print/machine/order with honest
readiness. That mixed-route board, backed by kernel-validated artifacts
and readable by agents, is the differentiator.
