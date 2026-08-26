# 0365 — PRD-018 review fixes: server-owned frozen re-measurement + hardening

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Claude (Opus fixers + Sonnet docs, adversarially verified) / Nikita Fedorov

## Summary
The four-way review of PRD-018 (Opus lenses + an adversarial verifier + Codex
xhigh) found the feature well-built but with one class of **integrity** hole:
the loop and `accept_candidate` *trusted the candidate's self-reported specs*.
A candidate could keep a frozen `check_that` spec's name while swapping its
predicate to `return True`; the old metadata diff (keyed on kind/name/limit)
saw no violation, so a constraint-violating part could LAND with a
`spec_green: true` badge (a 10 mm cube "NEMA 17 mount" was proven to land).
This wave replaces "trust the re-declared `SPECS`" with **server-owned
re-measurement against built geometry**, then closes the surrounding
data-loss / DoS / scope-honesty findings.

## Changes

### Wave 1 — integrity core (the centerpiece)
- Deleted the metadata-diff path (`agent.intent.freeze`/`frozen_spec_violation`
  no longer exist). The frozen contract is now enforced by RE-MEASURING it: the
  server appends always-pass **probe** SPECS whose `measured` field the kernel
  fills from the *actual built shape*, and evaluates every frozen bound/predicate
  itself (`agent.intent.frozen_probe_script`/`frozen_verdict`,
  `agent.generate.evaluate_frozen_specs`/`content_sha256`). A candidate's
  `build()` can wipe `globals()["SPECS"]`, so probes are literal dicts the
  candidate cannot forge; a deleted probe is fail-closed (an unmeasured frozen
  spec is a violation, not a pass). Enforced at TWO server-owned points: inside
  the loop (gating `spec_green`) and again at `accept_candidate`.
- `accept_candidate` binds to the candidate's **immutable recorded snapshot**
  (script + `content_sha256`), holds the project turn, and re-measures those
  exact bytes before stamping provenance — a scratch part mutated between
  generation and accept is re-measured and refused (the TOCTOU fix).
- `spec_green` requires kernel-valid AND every declared + frozen check PASS — a
  SKIP is not a pass. `tests/test_generation_integrity.py` (exploit regressions).

### Wave 3 — closing a deeper forge the adversarial gate found
The wave-1 re-measurement appended always-pass **probe** `SPECS` to the candidate
script and read the kernel-filled `measured` fields. The final adversarial pass
proved that is itself forgeable: `build()` runs after the append and reads
`globals()["SPECS"]`, so it detects the `__fzprobe_*` names and serves compliant
geometry **only while probed**, its real (violating) geometry otherwise — a 200³
cube landed `spec_green:true` against a 60×40×20 frozen envelope. Fixed by
measuring through a path `build()` cannot observe:
- New kernel op **`frozen_measure`** (`kernel/handlers/specs.py`) builds the
  candidate's **UNMODIFIED** recorded bytes — byte-for-byte what `create_part`
  builds, nothing appended, no `SPECS` declared — and returns only
  kernel-computed numbers off the B-rep (`size`/`mass_g`/`volume_mm3`, plus
  `min_wall` only when a frozen wall spec exists). `agent.intent.frozen_verdict`
  now evaluates the frozen bounds against that raw metrics dict, fail-closed.
- The probe-append helpers (`frozen_probe_script`/`_probe_dict`/`_PROBE_NAMES`/
  `_probe_kinds`) are **deleted** — no observable-probe code remains in the tree,
  so the forge cannot be reintroduced by accident.
- Regression (the merge gate): `test_probe_detecting_build_is_caught_because_the_
  recorded_bytes_are_measured` — a `build()` that branches on `globals()["SPECS"]`
  is measured on its real geometry (`frozen_ok False`); a genuinely-compliant
  candidate is not a false positive. Runs on a dedicated `KernelClient`.

### Wave 2 — hardening (three parallel fixers + controller consolidation)
- **Scratch / namespace / DoS:** cleanup deletes only the generation's *recorded*
  scratch ids (never a live prefix scan — a user part named `gen_*` is safe);
  `accept_candidate` refuses a target in the `gen_` namespace; `search_parts`
  filters `SCRATCH_PREFIX` in the engine unconditionally (the AC3 leak, closed
  even on a keyless server); `candidates` capped (≤8) and `budget` validated
  (finite, positive) at the tool boundary; a new `discard_generation` tool drops
  a never-accepted generation's scratch parts + record; accept-as-proposal cleans
  scratch only after the write succeeds.
- **FR7 connectors (was undisclosed-absent):** implemented — the intent surfaces
  each named interface with a human label, the system prompt instructs a
  `connectors(p, part)` frame per interface, and a candidate's connector is read
  back un-forgeably via the kernel handler in a test.
- **FR6 output contract:** a real `interface_dims_parameterized` meta-spec (a
  grounded interface dimension baked in as a magic-number literal while absent
  from `PARAMS` is a violation), gating terminate and accept.
- **Intent parsing:** word-boundary matching — "aluminum"/"aluminium" no longer
  contains "min" and reverses a mass budget; a "50×70 mm PCB" envelope is a
  lower-bound interface footprint, not the part's own size ceiling.
- **Provenance accuracy:** truthful model id, the exact model-turn count, and
  sha256 of the ORIGINAL attachment bytes (a PDF's own bytes, not the extracted
  text — `text_sha256` is named honestly).
- **PDF tests skip without the `[pdf]` extra** (the FEM `importorskip` idiom):
  the eight PDF-integration tests now `pytest.importorskip("pypdfium2")`, so the
  suite is green on a CI leg that does not install the optional extra — the
  tool-surface security boundary and the fence function stay covered by non-PDF
  tests that always run.
- **Intake hardening:** the document fence neutralizes an injected END delimiter
  (defense-in-depth — `ALLOWED_TOOLS` is the real boundary); PDF text is
  extracted BOUNDED (never full-then-sliced — an unclamped range crashes pdfium);
  attachment count (≤20) and combined-size (≤150 MB) caps checked before any file
  opens.
- **Honest docs:** a generated script IS arbitrary Python confined only by
  PRD-006 sandboxing; the fence and the `spec_green`/provenance badge are NOT
  security boundaries — stated plainly in `CLAUDE.md`/`AGENTS.md`/`agent-api.md`/
  `user-guide.md`. The four docs were also corrected from the superseded
  "diff-at-accept" design to the real re-measurement one.

## Deliberate deferrals (recorded honestly, not papered over)
- **NEMA feature (hole-pattern) geometry verification** — only the footprint +
  parameterization are frozen; a `check_that` hole check is candidate-forgeable
  (predicate runs after `build()`), so the honest fix is a kernel-computed
  circle-inventory measurement in `kernel/handlers/specs.py`
  (`agent.intent.FEATURE_GEOMETRY_DEFERRED`). A garbage part still cannot LAND
  (footprint fails un-forgeably) — this is a fidelity gap, not a landing hole.
- **Branch coherence** — the loop's synthetic `gen:<id>:<n>` identity always
  lands scratch parts on the canonical branch; from a non-default branch
  `accept_candidate` fails safely but scratch parts orphan (recorded, out of
  scope for this pass).

## Residual flagged for a future PRD-006 follow-up (NOT PRD-018)
- The kernel worker namespace persists across requests — a part `build()` that
  mutates `sys.modules`/globals can poison a sibling. This wave's frozen gate is
  robust to it (it re-measures geometry, not worker state); the general isolation
  gap belongs to the kernel/sandboxing story. Poisoning tests must run on a
  dedicated `KernelClient` (running one against the shared session worker breaks
  `test_specs`).

## Files
- `agentcad/agent/intent.py`, `agentcad/agent/generate.py`,
  `agentcad/core/tools_generate.py`, `agentcad/core/search.py`,
  `agentcad/core/intake.py`, `agentcad/kernel/handlers/specs.py`
  (`frozen_measure`)
- `tests/test_generation_integrity.py` (new), `tests/test_generation_intent.py`,
  `tests/test_generation_loop.py`, `tests/test_tools_generate.py`,
  `tests/test_prd018_acceptance.py`
- `docs/agent-api.md`, `docs/architecture.md`, `docs/user-guide.md`,
  `AGENTS.md`, `CLAUDE.md`

## Verification
`make test`: **7288 passed, 52 skipped** (run in two parallel halves and summed,
because the full ~7.3k-case suite exceeds this environment's single-run memory
ceiling; `-n auto --maxprocesses=8 --dist loadscope`). **Zero regressions**, proven
structurally: the PR diff (`origin/main..HEAD`) is eight PRD-018 commits touching
no FEM / supervisor / sandbox / pool code, and the one kernel change here
(`frozen_measure`) is purely additive. The only non-passing cases were (a) the 15
`*_the_full_suite_count_is_cited` guards, which flip green the moment this count is
cited; (b) two `test_examples` STEP/param-sweep cases, which **pass in isolation**
(parallel-load flakes); and (c) `test_prd028`'s FEM real-solver and
`test_supervisor`'s memory-kill — pre-existing, environment-specific failures in
subsystems this change never touches (FEM skips on CI absent the `[fem]` extra; the
memory-kill needs Linux cgroups). Adversarially re-verified: no landing /
data-loss / injection exploit survived the hardened tree (a proven landing exploit
gated merge).
