# 0324 — PRD-029 slice 3: the cheat-sheet's toolkit sections become ten core skills; `part_template` shrinks

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
FR9/G3: the nine toolkit sections of `core/templates.py::CHEATSHEET`
(29 339 chars, carried in every `part_template` reply) move verbatim into
ten core skills loaded on demand; the cheat-sheet keeps the contract and the
build123d basics (4 573 chars) and `part_template` adds the skill index as a
pointer. The shipped library is now sixteen skills.

## Changes
- `agentcad/skills/`: `robust-parametrics` (ROBUSTNESS TOOLKIT + "Parametric
  guards"), `selectors-and-occt-failures` (selectors, common failure modes,
  an "OCCT failure playbook" incl. the degenerate-boolean fail-closed rule of
  0308), `patterns`, `holes`, `threads-and-fasteners` (`requires:
  [threads]`), `ribs-bosses-draft`, `sketch-solver`, `sheet-metal`,
  `design-specs` (+ "Spec-first workflow"), `assemblies-and-mates`. Bodies
  6 600–9 800 chars, 15–20 triggers each, `## Sources` last; signature
  listings that are not Python sit in bare fences so every ```python fence
  parses.
- `core/templates.py`: `CHEATSHEET` ends after BUILD123D IDIOMS (selectors,
  failure modes, the algebra note and the surfacing pointer stay — the
  latter is the only pointer to `toolkit.surfacing`) with a closing paragraph
  naming the skills; `(see DESIGN SPECS below)` → `(load_skill
  design-specs)`.
- `core/service.py::part_template` → `{template, cheatsheet, skills:
  [{name, description}], hint}` (an `AppError` while indexing yields `[]`,
  so the one call an agent makes first can never fail on a broken skill
  dir); `core/tools.py` and `agent/mcp_server.py` describe the tool/server
  accordingly.
- `tests/test_holes.py`: the "cheat-sheet names every hole-record key" guard
  reads `SkillLibrary().load("holes")["content"]`, all 24 keys preserved;
  `tests/test_tools.py` asserts `skills` lists ≥ 10 names;
  `tests/test_part_template_compat.py` (new, 5): keys, `CONTRACT` present,
  `len(cheatsheet) < 7000`, every listed name is a core index name, the
  promoted headings are gone from the cheat-sheet.

## Files
- `agentcad/skills/{robust-parametrics,selectors-and-occt-failures,patterns,holes,threads-and-fasteners,ribs-bosses-draft,sketch-solver,sheet-metal,design-specs,assemblies-and-mates}/SKILL.md` — new
- `agentcad/core/templates.py`, `agentcad/core/service.py`, `agentcad/core/tools.py`, `agentcad/agent/mcp_server.py`
- `tests/test_holes.py`, `tests/test_tools.py`, `tests/test_part_template_compat.py`

## Notes
Docs that still describe the old cheat-sheet (`docs/agent-api.md`
`part_template` row and worked loop, `docs/part-authoring.md` "Cheat-sheet"
section, `AGENTS.md` pointers, two comments in `toolkit/holes.py`) are
updated in the docs slice. `make test` on the combined tree of slices 1–4 —
5646 passed, 51 skipped, 12 failed, 1 error in 852 s (nine are the
changelog count-guard tests reading this entry's own not-yet-filled count,
`test_prd028_acceptance::test_ac6_real_solver` is the known local `[fem]`
timeout that skips on CI, and `test_supervisor`'s memory-cap kill plus
`test_server::test_project_and_part_flow` were timeouts under the load of
concurrent slice agents — both re-run green in isolation, 2 passed in 13 s).
