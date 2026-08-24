# PRD-033 — Guarded geometry & OCCT stewardship

- **Status:** pending
- **Phase:** v6 — moats
- **Created:** 2026-08-24
- **Origin:** founder idea (Aug 2026), prompted by OCCT#1496
- **Depends on:** — (nothing hard; the harnesses it reuses are shipped)
- **Related:** PRD-024 (the bench: the D5 rule, `fix_005`, the
  `score.json`/`run.json` split), PRD-004 (`agentcad check`: budget,
  determinism, report-honest verdicts), PRD-018 (a refusal is a third
  state its loop must handle), PRD-006 (confinement — only if Phase 3
  ships)

## Problem & motivation

Our kernel answers wrongly without saying so, and we cannot ship a fix.

The failure is measured. `BRepAlgoAPI_Common` (build123d's `&`) is
unreliable when both operands carry G1-tangent face junctions — any swept
solid with a filleted centre line. `IsDone()` is `True`, nothing raises,
and back comes an empty shape, a whole operand, or a negative volume. The
trigger is **operand distinctness**, not serialization: two spatially
coincident but *independently constructed* copies of the bench's coolant
elbow (`fix_005`, 21 711.685 mm³) intersect to nothing, while
same-object booleans take a shortcut inside OCCT and answer correctly —
the "STEP round trip is the cause" framing was retracted in changelog
`0309`. A positive volume from such a pair is order-dependent and not
provably trustworthy either. Seven healing recipes were probed and all
fail (`agentcad/kernel/handlers/_bop.py`), so it went upstream as
[OCCT#1496](https://github.com/Open-Cascade-SAS/OCCT/issues/1496) — which
proves an issue was *accepted*, and nothing more.

Silent-zero laundering is not only a boolean problem. `_bop.py` removed
one `max(v, 0.0)` clamp; the identical clamp is still live at
`handlers/diff.py:62`, where an impossible negative difference volume
becomes a clean `0.0` in a review packet. And the robustness helpers
report downgrades as **warning strings the caller may drop**:
`toolkit/facemod.py:77` prints `safe_bool`'s warning to stderr and
continues, and shipped skill snippets discard it outright
(`skills/brackets-and-mounts/snippets/nema17_bracket.py:86`,
`skills/snap-fits/snippets/cantilever_lid.py:118`) — so `safe_shell`'s
"~20% thin on curved faces" fallback and `safe_fillet`'s achieved-radius
downgrade (the `fix_002` class) can reach a user unremarked. Where a
guard does exist it works, in three vocabularies and with no registry:
`_bop.checked_common_volume` has exactly two callers
(`kernel/worker.py`, `kernel/handlers/bench.py`) and three flag readers
(`bench/scoring.py`, `core/specs.py`, `core/checks.py`), and nothing
stops the next consumer from skipping it.

The cost is real — `fix_005`'s task file zeroes `geometry`, the bench's
primary shape subscore, because the boolean is degenerate for *every*
candidate — and a fix ships on someone else's calendar: we consume
`cadquery-ocp-novtk 7.9.3.1.1` (with its `cadquery-ocp-proxy` shim)
pinned by hash in `uv.lock`. Strategically the two obvious moves are
closed: writing a kernel is a documented mistake (Fornjot abandoned,
CADmium archived; the gap matrix scores it **skip — their mistake, not
ours**) and buying one is a wall ("Parasolid is a hard wall (closed
spec…)") — so the open move is to *guard* the kernel we share and
*steward* it upstream (market_research.md, "Gap matrix"; "Native CAD
import"). We found no open-source CAD project doing this systematically,
which is a survey result, not a proof of vacancy; and Ondsel's **>75% of
FreeCAD users would pay — for reliability, not features** measures
*application* reliability, not kernel-op correctness — that correctness
is part of what they mean is our inference, stated as one.

## Users & jobs

- **Maintainers:** know which answers are trustworthy; reproduce a defect
  on demand instead of from memory.
- **The built-in agent and MCP clients:** never a confident wrong number
  — a refusal that names what to do next.
- **Reviewers and geometry CI:** rows that say "indeterminate" where the
  kernel could not answer, not "clean".
- **PRD-024's bench and PRD-018's loop:** an `error` subscore that is
  excluded, and a third state that does not thrash the generation loop.
- **Upstream OCCT/OCP and their ecosystem:** reproducible reports and, at
  Phase 2, real contributions.

## Goals

- G1. No *guarded first-party* answer is silently wrong: every guarded op
  returns a measurement that passed its invariants, a structured
  degradation, or a refusal — with a stated false-negative envelope
  (a plausible-positive wrong volume is still undetected; `_bop.py` names
  three residual blind spots and this PRD does not close them).
- G2. Known defects are a corpus with an expectation matrix, reproducible
  on demand, loudly wrong when reality moves.
- G3. Machine-found anomalies are triaged into an honest classification,
  and our upstream work is real contribution rather than filed noise.
- G4. If a defect with a product symptom outlives upstream's patience, we
  can build and ship a patched kernel — a capability entered on a written
  trigger, not built speculatively.
- G5. A per-release **kernel health** report that is reproducible offline.

## Non-goals

- **A geometry kernel of our own** — the graveyard and the gap matrix;
  this PRD makes OCCT ours to *guard and fix*, never to replace.
- **Parasolid / ACIS / CGM** — closed spec and royalty licensing,
  incompatible with an Apache-2.0 self-hostable product.
- **Kernel portability.** The model *language* is build123d and part
  scripts import OCP directly; genuine portability would need a
  kernel-neutral model IR — out of scope, not claimed. The worker seam
  and the OCP-import discipline stay clean as hygiene; that is all.
- **An F-rep/implicit kernel as the primary representation** — everything
  downstream speaks B-rep; implicit is a complement at most.
- **Forking build123d** — the pin is deliberate and the suite is its
  compat harness; a fork buys nothing the OCCT patch queue does not.
- **Guarantees over user-script geometry.** A part script calling
  build123d or OCP directly is outside the guarantee — the guard covers
  first-party call sites only, permanently.
- **Publishing wheels for third-party consumption** — our wheels, if they
  ever exist, serve our runtime. This PRD contains no step toward it.
- **New robustness algorithms** — the `safe_*` helpers keep their
  algorithms; only their *reporting* changes, callers unbroken (FR4).

## Experience

**The maintainer loop.** A check or bench row reports a refusal instead
of a number; `agentcad kernel-health` names the op and its corpus entry
if one exists. If none does, a Phase-2 campaign reproduces it into a
candidate anomaly, classifies it, and — if it survives triage — files it
with a reproducer. If a product symptom outlives upstream, FR12's trigger
fires and a patched wheel ships.

**The author's and the agent's path is unchanged.** No new tool, no new
dialog. Two differences: an answer the guard cannot vouch for arrives as
a refusal naming a recommended next action ("re-run with the parts
separated", "the boolean is degenerate for this pair — measure by mass
instead"), and a degraded helper (a fillet that fell back, a shell that
approximated) now shows up in the build result and in `agentcad check`
rather than in a warning string the script threw away.

## Functional requirements

### The guarded layer — MVP

- FR1. `agentcad/kernel/guarded/` defines one contract: a guarded op
  returns a **measurement that passed its declared invariants**, a
  **structured degradation** (op, requested vs achieved, reason), or a
  **refusal** — never a number it cannot vouch for. Every refusal names a
  recommended next action. The governing precedent is the bench's D5:
  "`error` is the harness failing to measure; `not_applicable` is
  declared by `task.json` (weight 0) and never by the run. A candidate
  that is absent, broken, mesh-only or wrong measures **zero**." The
  guarantee covers **first-party** call sites only.
- FR2. Guarded ops are reachable only through a **registry** in that
  module. An equality test asserts every registered op declares its
  degradation vocabulary, and an AST lint asserts no first-party module
  outside the registry calls a raw wrapped op. It ships covering
  `checked_common_volume`'s two callers (`kernel/worker.py`,
  `kernel/handlers/bench.py`), its three flag readers
  (`bench/scoring.py`, `core/specs.py`, `core/checks.py`), and the
  `safe_*` consumers `toolkit/features.py`, `toolkit/facemod.py`,
  `toolkit/sheetmetal.py`, `toolkit/surfacing.py`.
- FR3. Invariants that cost less than the measurement: **sign** — whose
  named target is `handlers/diff.py:62`'s surviving `max(volume, 0.0)`
  clamp, which must become a refusal; **containment** (an intersection
  may not exceed either operand — `handlers/bench.py`'s clamp promoted to
  a rule); **conservation** (`geom_diff`: `vol(new) − vol(old) = added −
  removed` within tolerance); **AABB** necessary conditions plus the
  existing octant-crop recheck; **validity** (`is_valid`, solid count,
  the solids-sum volume rule).
- FR4. Degradations travel **out of band**. The build worker collects
  degradation records and returns them with the build result; the
  documented tuple signatures of `safe_fillet`/`safe_shell`/`safe_bool`
  (`docs/part-authoring.md:154-162`, public API) stay byte-for-byte, so
  the shipped snippets that discard `_warn` keep working and their
  degradations still surface — in build results, `detected`, and
  `agentcad check` rows. If any signature must change, that is a surface
  change and carries a migration AC over `part-authoring.md` and the
  snippet call sites.
- FR5. A defect **corpus** at `tests/corpus/kernel_defects/<id>/` with an
  **expectation matrix** keyed by *(kernel build fingerprint, platform,
  execution mode)* rather than a scalar status: a `patched-locally` entry
  expects "reproduces on the upstream wheel, does not on ours". The
  enduring assertion across every cell is **"never returns the known
  silent-wrong result"** — a refusal and a correct answer both satisfy
  it, the either/or shape `tests/test_kernel.py:183` already pins. Each
  entry declares which CI leg gates it. Test zero is the #1496 pair.
- FR6. **Budgets, both directions.** A false refusal is a product defect:
  the bundled examples and the catalog parts must produce zero refusals,
  and the bench's 25 references must still score 1.0. And the guard's
  runtime cost is measured — `pairwise_interference` over the `asm_*`
  references before and after — with `handlers/motion.py`'s per-sample
  multiplication measured **before** any change to
  `_bop.SUSPECT_OVERLAP_FRACTION` (0.5 today; the elbow pair shifted
  40 mm sits at 0.44, one shift from the cliff). Both numbers are
  published in the health report.

### Fuzzing & upstream stewardship — Phase 2

- FR7. A campaign splits **plan** from **outcome**: the case manifest,
  generation and minimization steps are byte-reproducible from `(seed,
  generator version, corpus version, case count)`; observed outcomes —
  including reproduce-counts — live in a sibling, explicitly
  non-deterministic document (the bench's `score.json`/`run.json` split).
  Deterministic campaigns are fixed-case-count; wall-clock exploratory
  campaigns are a separate mode and never claim reproducibility.
- FR8. **Probes and metamorphic relations**, not "oracles": FR3's
  invariants plus independently-constructed-but-coincident operands,
  reversed operand order, repeated evaluation in one process, fuzzy vs
  plain, whole vs cropped — and OCCT's own `BOPAlgo_ArgumentAnalyzer` and
  `BRepAlgoAPI_Check`, both verified present in this build. A
  disagreement is a **candidate anomaly** for triage, classified as
  *candidate anomaly* → *invariant violation* → *independently confirmed
  defect* → *unresolved*; never automatically a defect.
- FR9. **Process history is part of the reproducer.** Each case is
  journaled *before* it executes (a segfault must not lose it), and a
  finding persists its warm-up prefix, its worker mapping — `pool._pick`
  routes by `hash(affinity) % size`, and Python's string hash is
  per-process salted — and its serial/parallel mode
  (`toolkit/boolean.py:37` sets `SetRunParallel(True)`). Both are pinned
  in campaigns. Reproduction is offered cold-process **and** as a
  stateful sequence, with a stated statistical threshold.
- FR10. Minimization shrinks parameters and op sequences to a minimal
  reproducer and is itself part of the reproducible plan.
- FR11. The upstream programme is graded. A **report** needs a minimal
  reproduction, the version, the measured correct answer, the negative
  results and a non-reproducing control (#1496's shape). A **patch**
  claim needs a current-branch C++/DRAW reproducer, OCCT-native
  diagnostics and CLA readiness — none of which #1496 established. The
  concrete near-term contribution is smaller and lands in **OCP, not
  OCCT**: `BOPAlgo_Options` exposes `HasErrors`/`HasWarnings`/`GetReport`
  in this build while `BRepAlgoAPI_Common`'s bound MRO does not surface
  them (measured on 7.9.3.1.1) — binding them narrows the very
  no-error-channel blind spot this PRD is about.

### Owning the build — Phase 3, entry-triggered

- FR12. **Written entry trigger:** a validated patch for a defect with a
  product symptom that upstream has not landed within N weeks (N declared
  when the phase opens). Gate zero is a measured Linux spike — wall time,
  peak RAM, cache design, and the `7.9.3.1.1 → (OCCT tag, OCP revision)`
  mapping verified — and a failed spike is a legitimate stop.
- FR13. The pipeline is a **pinned reuse/fork of CadQuery's
  `ocp-build-system`**, which already produces the exact
  `cadquery-ocp-novtk` / `cadquery-ocp-proxy` wheels we consume for all
  three platforms — never a from-scratch build. `novtk` is the payload
  and `proxy` the small shim `build123d`, `ocpsvg` and `ocp-gordon` all
  depend on, so `cadquery-ocp-proxy==7.9.3.1.1` must stay resolvable.
- FR14. The swap is named honestly: a second lockfile
  (`uv-ourwheel.lock`) or a `[tool.uv.sources]` manifest edit — `uv sync
  --locked` cannot be index-overridden past pinned hashes. It is
  exercised from an **empty package cache**.
- FR15. **Parity, without byte-identity.** Two compilations of the same
  C++ do not promise identical doubles (and `SetRunParallel(True)` is
  live), so the bar is same-platform semantic tolerance per artifact
  class: declared numeric tolerances on the mesh sidecars a determinism
  run actually compares (`checks._MESH_ARTIFACTS` — `.acm`,
  `.faces.u32`) and on the metric scalars; SVG byte-identity is a
  **stretch row with a stated flake budget**, not a gate. A full build
  fingerprint (toolchain, flags, source digest, patch series) is recorded
  with every wheel.
- FR16. Wheel engineering is named, not implied: manylinux policy +
  `auditwheel`, macOS deployment target + `delocate`, the ABI matrix, an
  SBOM, a clean-container install test, and the PyInstaller onedir bundle
  tested on our wheel too. **LGPL:** a digest is not source — we mirror
  the pinned tarball itself plus the patch series and build recipe. The
  OCCT exception removes the combined-work relink obligation; it does
  **not** waive publishing our modifications to the Library.
  Counsel-approved compliance is release-blocking for any public
  artifact, and "CI distributes nothing" holds only while artifacts are
  neither public nor retained.
- FR17. The **patch queue** (`build/kernel/patches/`) ships empty; every
  patch carries `upstream:`, `reason:` (product symptom + corpus id) and
  `review-by:`. At five patches the queue **freezes** to product-symptom
  defects until one retires. A patch that misses its `review-by` date
  forces a recorded exit decision: land upstream, drop it, or accept a
  permanent fork with its upgrade cost written down.

### Governance

- FR18. The health report separates a **reproducible local section**
  (corpus expectation matrix, guard budgets from FR6, build fingerprints,
  patch queue) from a **timestamped upstream-status snapshot** written by
  a scheduled job with defined failure behaviour — the report is never
  network-dependent at release time. It carries a one-line WATCH note on
  alternative kernels (truck, Fornjot's successors): no cadence, no bar,
  no commitment.
- FR19. **Fuzzer brake:** two releases of scheduled campaigns with zero
  independently confirmed novel defects stops the schedule; the fuzzer
  survives as an on-demand reproducer.

## Agent surface

**No new tools, routes or events**; the registered tool count is
unchanged. Two additive changes to *content*: build results gain
degradation records (FR4), and some answers arrive as refusals. A refusal
is a **third state**, not a failure, and agents must tell them apart —
which is why FR1 requires a recommended next action and FR6 caps false
refusals at zero over shipped examples and catalog parts. The fuzzer and
(if it ever exists) the wheel pipeline are maintainer CLIs.

## Technical approach

- **`guarded/` is kernel-internal**, beside `handlers/` under
  `agentcad/kernel/`, so pack discovery (`worker._load_handler_packs`
  iterates `handlers/`'s modules) never sees it and it is never a tool.
  `_bop.checked_common_volume` moves in behaviour-identical; its
  constants become declared policy at the same values.
- **The registry is the choke point.** Equality test plus AST lint is
  what converts "the consumers happen to do the right thing" into an
  invariant; without the lint the registry is documentation. The
  **out-of-band channel** rides the existing worker→service build result
  (the path `warnings` already takes out of `build_shape`), which is why
  FR4 adds reporting without touching toolkit signatures.
- **Fuzzer placement:** a package `agentcad/fuzz/` registered the way
  `add_bench_parser(sub)` registers `bench` in `agentcad/cli.py`, reusing
  that module's `_finite_arg`/`--budget` discipline and, like
  `agentcad/bench/`, importing neither OCP nor build123d — every op goes
  through the worker, so a segfault costs one worker and one journaled
  case.
- **Sandbox interplay is Phase 3 only.** Outside hosted posture
  `sandbox_linux._read_roots` returns `["/"]`; in hosted posture a
  missing path is *dropped*, and a `landlock_root` entry is a lost grant
  that does **not** clear the `active` claim (`kernel/client.py:50-60`).
  The real question is whether a hosted worker still reads its OCP
  libraries — AC10.
- **Nothing else moves:** no manifest, storage, route, service or
  frontend change; `docs/kernel-health.md` is generated, not a UI.

## MVP & phasing

- **MVP — the guarded layer and the corpus (FR1–FR6).** Quality work on
  shipped code: one contract, a registry with a lint, the invariants
  (including the live `diff.py:62` clamp), out-of-band degradations, the
  corpus with #1496 as test zero, and both budgets.
- **Phase 2 — fuzzing and stewardship (FR7–FR11, FR19).** Campaigns,
  triage classification, the reproduction-fidelity work, and the OCP
  bindings contribution.
- **Phase 3 — owning the build (FR12–FR17)**, entered only on FR12's
  trigger and only after its spike passes.
- FR18 grows with each phase.

## Acceptance criteria

- AC1. The registry equality test and the AST lint pass, and a fabricated
  first-party call to a raw wrapped op fails the suite (new work).
- AC2. The #1496 pair, through the guard: `iou` raises and the bench's
  `geometry` subscore is `error` and excluded (**regression-lock** on
  shipped behaviour); `pairwise_interference` carries the pair with
  `degenerate: true` so it counts un-clean (**regression-lock**);
  `agentcad check` renders the "indeterminate … counted as interfering"
  row (**regression-lock**); and a negative `geom_diff` volume becomes a
  refusal instead of `handlers/diff.py:62`'s `0.0` (**new work**).
- AC3. `safe_fillet`/`safe_shell`/`safe_bool` signatures are unchanged
  (the `part-authoring.md` snippets and the two shipped skill snippets
  that discard `_warn` still run), **and** a fallback taken inside them
  appears in the build result and in an `agentcad check` row (new work).
- AC4. Zero refusals across the bundled examples and the bundled catalog
  parts, and the bench's 25 references still total 1.0 (new work).
- AC5. The guard's runtime cost over the `asm_*` references is measured
  and inside a declared bound, with the motion per-sample figure recorded
  (measurement, published in the report).
- AC6. The corpus expectation matrix is enforced per *(fingerprint,
  platform, mode)*: every cell asserts "never the known silent-wrong
  result", a cell whose expectation no longer matches reality fails its
  declared CI leg, and the either/or precedent is preserved (new work).
- AC7. Phase 2 — the same `(seed, generator, corpus, case count)` tuple
  yields a byte-identical case manifest and minimization trace, with
  observed outcomes excluded from the comparison by construction.
- AC8. Phase 2 — the positive control (cold rediscovery of the #1496
  class) is stated for what it is: **a test of harness plumbing, not of
  discovery power**. Confirmed novel defects are a reported outcome in
  the health page and never a gate.
- AC9. Phase 3 — from an empty package cache, `uv-ourwheel.lock` resolves
  to our wheel with `cadquery-ocp-proxy==7.9.3.1.1` still satisfiable;
  every `from OCP…` import in the first-party tree resolves against it
  (91 import sites over 32 OCP submodules, in `agentcad/kernel/` **and**
  `agentcad/toolkit/` — the toolkit binds OCCT directly too); the full
  suite is green (count cited); and FR15's per-class tolerances hold with
  the SVG row reported as a stretch.
- AC10. Phase 3 — on Linux with `posture=hosted` and our wheel,
  confinement reports `active` with **no `landlock_root` failure naming
  an OCP library path** (a dropped root would be a silent read loss, not
  a downgrade to `off`).
- AC11. No model-facing tool, route or event is added (registry test);
  the build-result degradation records are additive and documented.

## Risks & open questions

- **False refusals.** A guard that refuses too often is worse than the
  bug: agents cannot distinguish refusal from failure and PRD-018's loop
  thrashes. Mitigation: FR1's recommended action, AC4's zero-refusal
  budget over shipped content, and a refusal count in every health
  report.
- **Runtime cost.** Rechecks are booleans, and `handlers/motion.py`
  multiplies them per sweep sample. AC5 measures before anything moves;
  an unaffordable guard is narrowed, not shipped slow.
- **Coverage theatre.** The layer narrows the hole, it does not close
  it: a plausible-positive wrong volume stays undetected, 0.5 is a
  coverage cliff, and the recheck runs on the same kernel. `_bop.py`'s
  three blind spots are repeated verbatim in the health report.
- **Scope creep into OCCT maintenance.** Phase 3 exists only behind
  FR12's trigger, the queue freezes at five, and a missed `review-by`
  forces an exit decision (FR17) — a permanent upgrade-blocking patch
  must be a decision, never a drift.
- **Fuzzer noise.** OCCT's own order-dependence means a finding may not
  reproduce; hence classification (FR8), reproduction fidelity (FR9), the
  rule that campaign findings never gate CI, and FR19's brake.
- **Phase-3 feasibility is unmeasured.** Build wall time, RAM, cache
  design and the version mapping are unknown until the spike; a failed
  spike stops the phase and the guarded layer still stands alone. *Open
  question:* what N (weeks of upstream silence) makes the trigger fair?
- **LGPL.** *Open question, release-blocking.* Mirroring the tarball plus
  patches plus recipe is the intended compliance shape, but the written
  offer, the modification notice, and the treatment of retained CI
  artifacts need counsel before any public artifact.
- **Upstream patch acceptance is unproven.** *Open question.* #1496 shows
  they take reports; the CLA and merge path for a patch are untested. The
  OCP bindings contribution (FR11) is the cheap way to find out.
- **Guarantee boundary.** Docs must repeat the first-party scope
  wherever the guarantee is stated, or it will be read as universal.
- **Bench interaction.** Restoring `fix_005`'s `geometry` weight once the
  guard makes that measurement possible is a **bench-v2** change, after
  bench-v1 results are published — not a silent rubric edit (PRD-024's
  rotation policy).
- **Docs debt noted, not fixed here:** `CLAUDE.md`'s trap list still
  carries the retracted STEP framing for the degenerate-boolean bug; a
  follow-up should align it with changelog `0309`.

## Competitive references

The commercial answer to kernel fragility is to buy a better kernel —
Onshape, NX and SolidWorks ride Parasolid — and that door is closed to an
open stack ("Parasolid is a hard wall (closed spec…)"; royalty licensing
is hostile to agent fleets). The AI-native rivals wrote their own engine,
scored **skip (their mistake, not ours)** alongside the kernel graveyard
(market_research.md, "Native CAD import"; "Gap matrix"). FreeCAD,
chili3d, CadQuery and build123d all ship OCCT and inherit its answers,
and we found no systematic guard layer among them — a survey result, not
a proof of vacancy.

So we take the remaining move: guard the shared kernel at our own call
sites, keep a corpus of what it gets wrong, steward fixes upstream where
they also help everyone else on OCCT, and keep the ability to ship a
patched build behind a trigger rather than as an ambition.
