# 0095 — PRD-003 second-review fixes: no pending gate, every skip red, keys that cover their inputs

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Nikita Fedorov

## Summary
A second independent review of the executable-specs feature (after the round in
changelog 0094) returned CHANGES-REQUIRED with nine reproduced findings. Every
one of them was a way for the mandatory `specs` gate to approve or merge state
it never measured, or a branch-shared cache that omitted a real evaluation
input. This fixes all of them, each with a regression test written first and
demonstrated failing against the pre-fix behavior.

## Changes
- **X5 — a `specs.py` that will not read or declare is a RED check row.** A
  declaration failure landed only in `report["errors"]`, and both
  `report_status` and the gate are computed from the *check rows* alone — so a
  project spec file that raised while it executed left the report green and the
  merge unblocked. `_project_block` now returns a synthetic `declaration` check
  (`project:specs`, status `error`, message naming the file and the cause) for
  both a failed `spec_declare` and an unreadable file. `SpecRunner.project_script`
  no longer swallows the `OSError`: "there is no `specs.py`" and "there is one
  and we could not read it" were the same answer, which is the quietest way to
  lose a declared spec. `read_project_specs` and `declarations` name it too
  (new `_read_error`, `_declaration_row`).
- **X6 — `declares_specs` fails closed on a syntax error.** It returned `False`
  for any script that would not `ast.parse`, so a part that visibly binds
  `SPECS` above a malformed `def build` was classified spec-less and skipped by
  the gate entirely — the declared check never became red, and
  `proposal_merge(allow_invalid=True)` could then waive the separate kernel
  validation and merge. With no AST there is a line-anchored text scan
  (`_SPECS_TEXT_RE`) for the three binding forms; the part is evaluated, its
  build fails, and the `script_error` is a red row.
- **X7 — every skip is red in the GATE, not just `mesh_only`.** `_gate_row` now
  converts *any* skip on a declared check into a `fail` naming its reason
  (`fem_extra_missing`, `mesh_only`, `unsupported_scope`, `no_instances`, and
  whatever is added next), keeping `details.reason`, `details.hint` and
  `details.skipped_in_report`. The finding's scenario: a proposal declaring only
  `check_fem_static`, reviewed on a machine without the `[fem]` extra, passed
  the gate with zero structural measurement. Reports are unchanged — they keep
  the honest skip and its hint.
- **X8 — the specs gate never returns `pending`.** `ProposalManager.merge`
  blocks a gate whose state is `fail` and nothing else, so the supposedly
  retry-only `pending` (a source head that moved mid-evaluation) was
  merge-*permissive*: external git can advance a branch regardless of the turn
  lock. `_gate_wording` maps a moved head to `state: "fail"` with a summary
  saying *source moved during evaluation, retry*. The verdict is still not
  memoized, so the retry re-measures. `evaluate_specs` still reports
  `status: "pending"` / `available: false` at the verdict layer, and `pending`
  stays defined in PRD-002 for other providers. `proposals.py` untouched.
- **X1 — an incomplete hand-written declaration is structural residue, not a
  500.** `is_declaration` checked `spec`/`kind`/`scope` only, so
  `SPECS = [{"spec": 1, "kind": "mass", "scope": "part"}]` was accepted and then
  read unguarded by the kernel's `_record` and the service's `_record`/`_residue`
  — `KeyError("name")` in the *server* process, i.e. a 500 for the whole
  tool/route. New `toolkit.specs.declaration_problem` validates the full emitted
  shape and names the offending key; `is_declaration` is that test without the
  reason; the kernel's `_declarations` puts the reason in its `contract_error`.
  Both `_record`s and `_evaluate` now use `.get` with defaults so a future
  format drift degrades instead of raising.
- **X3 — the project-spec sidecar key covers the mate graph and PMI.**
  `check_stackup` walks the mate chain and sums each contributing part's PMI
  dims; neither lives in a script, params or a transform, so neither moved the
  key. Loosening a tolerance reused the verdict measured against the tight one.
  `_project_key` now hashes the raw mate dicts and the `pmi` subtree of every
  referenced part.
- **X4 — the cached `fem_static` row is keyed by E.** The row rides the part
  sidecar, whose key covers the material *density*, while `_eval_fem` hands the
  solver `E_mpa` and displacement scales with 1/E. The cache slot is now
  `"<index>|<material key>"` (new `_fem_material_key`). E is the whole list:
  the solver's Poisson ratio is its own constant and this layer never sends one.
- **X10 — non-finite limits are rejected, twice.** Every ordered comparison
  against NaN is false, so `check_mass(max_g=float("nan"))` reported *pass*
  without measuring anything. `toolkit._number` rejects `nan`/`inf` where the
  argument is read; the kernel's `_bounded`/`_eval_bbox`/`_eval_wall` re-check
  through the new `_limit` (a hand-written dict never passes through a
  constructor), and the service checks project-tier and FEM limits with
  `_non_finite_limit`.
- **X11 — portability markers.** The kernel-backed, sidecar-writing and
  route-driving tests in `tests/test_specs.py` (section 2) and
  `tests/test_specs_api.py` carried only `slow`; `make test-portability` selects
  `portability`, so a Windows-specific failure in atomic sidecar replacement or
  `specs.py` write/delete would not have been caught there. 42 tests gained the
  marker; the pure-logic section 1 deliberately did not. The other four spec
  test files already carry it module-wide.

## Files
- `agentcad/toolkit/specs.py` — `declaration_problem`, `is_declaration` over the
  full shape, `math.isfinite` in `_number`.
- `agentcad/kernel/handlers/specs.py` — `_limit` (finite bounds) used by
  `_bounded`/`_eval_bbox`/`_eval_wall`, `declaration_problem` in
  `_declarations`, `.get`-guarded `_record`/`_evaluate`.
- `agentcad/core/specs.py` — `_SPECS_TEXT_RE` + fail-closed `declares_specs`,
  guarded `_record`, `_non_finite_limit`, `_read_error`, `_declaration_row`,
  raising `project_script` and its three callers, `_project_block` declaration
  rows, `_project_key` over mates + PMI, `_fem_material_key` and the keyed FEM
  slot, `_gate_row` over every skip, `_gate_wording` pending → fail, docstrings
  (module header, `gate_provider` state table).
- `agentcad/core/tools_specs.py` — `run_specs` description: three gate
  divergences, no `pending`.
- `tests/test_specs_toolkit.py` — 3 new tests (non-finite arguments, full-shape
  `is_declaration`, `declaration_problem` naming the key).
- `tests/test_specs_kernel.py` — 2 new tests (incomplete hand-written entry is a
  `contract_error` from both methods and the worker survives; a NaN limit is an
  error record).
- `tests/test_specs.py` — 9 new tests (fail-closed presence scan, broken and
  unreadable `specs.py`, absent `specs.py` unchanged, incomplete and complete
  hand-written declarations, the `_residue` degradation path, PMI-only
  re-evaluation, the mate-graph key, the FEM key over a stubbed and a real
  solver); `portability` on section 2.
- `tests/test_specs_gate.py` — 4 new tests (broken `specs.py` red and named,
  syntax-broken script red not skipped, `fem_extra_missing` red, unsupported
  scope red), 2 rewritten (a skip is data in a report and red in the gate; a
  moved head blocks the merge and the retry lands).
- `tests/test_specs_api.py` — `portability` on the kernel/route tests.
- `AGENTS.md`, `docs/agent-api.md`, `docs/part-authoring.md`,
  `docs/superpowers/specs/2026-08-10-executable-design-specs-design.md` —
  as-built notes for X1, X3–X8, X10.

## Notes
- **Why a report and a gate disagree about skips.** A report is read by an
  engineer, who is better served by the named reason and the hint; a gate
  decides a merge, where "declared but not measured" is the entire hole it
  exists to close. `details.skipped_in_report` marks the rows that diverge so
  the proposals UI can still explain itself.
- **Why the verdict layer keeps `pending`.** `evaluate_specs` reports what it
  knows: no verdict exists for that head. Turning that into `fail` is a
  *gate* policy, forced by PRD-002's merge rule, and it belongs in the one
  function that turns policy into words.
- **The syntax-error heuristic is deliberately loose.** A line-anchored `SPECS`
  binding inside a triple-quoted string or a function body will match, because
  there is no AST to tell them apart. The cost of that false positive is one
  error row on a script that already fails its build; the cost of the false
  negative was a declared spec the gate never measured.
- Old sidecars keep their `tiers.fem` rows under the pre-fix `"<index>"` slot.
  They are simply never read again — one extra solve per part, once.
