# 0322 — PRD-029 slice 1+4: skill format, library, lint, CLI, config; six authored core skills

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
The foundation of agent skills (PRD-029 FR1, FR2, FR4's config, FR5's
capability gate, FR7's trust state, the author path) plus the first six
authored core skills. `agentcad/core/skills.py` is an OCP-free library over
two layers — the shipped `agentcad/skills/` and each project's `skills/` —
with a strict frontmatter reader, deterministic search, structural
truncation under a hard cap, digest-keyed trust for project skills, and a
config-driven budget; `skills_lint.py` is the shared lint behind
`agentcad skill lint` and the core-library test.

## Changes
- `core/skills.py`: `parse_frontmatter` — a flat YAML subset (scalars,
  inline/block lists, quoted strings; every value a string, no coercion;
  duplicate/malformed keys raise `SkillFormatError`); `SkillMeta`,
  `SkillRecord` (`name`, `layer`, `path`, `dir`, `body`, `digest`,
  `invalid`), `SkillBudget` (+ `from_config()`); `SkillLibrary(store,
  core_dir=, budget=, only=, capabilities=)` with `records` (layering —
  a project skill shadows the core one by name and is flagged
  `overrides: "core"`; invalid project skills still shadow, listed with
  `invalid`), `index` (layer → `only` → enabled → capability), `hidden`,
  `search` (100 exact name / 60 name substring / 40 per trigger / 10 per
  description token; ties by layer then name; no hit → full index,
  `matched: false`), `resolve`, `load` (trust check for project skills;
  `content`, `chars`, `truncated`, `omitted_sections`, `assets`,
  `provenance`; `asset=` reads one sibling file with absolute/`..`/symlink
  escapes refused), `compact_index` (≤ 40 `- name — description` lines +
  "…and N more"), trust state (`trust_state`/`trust`/`untrust`/
  `set_enabled`) in `<project>/.history/agentcad/skills/trust.json` —
  local, never cloned, atomic write, corrupt → empty; `split_sections` /
  `truncate_sections` keep whole `##` sections and **guarantee
  `max_chars + 4`**: an over-long preamble is cut at a line boundary, an
  open fence is closed with its own marker, and `omitted_sections` leads
  with `(preamble cut)`. Capabilities are a closed set (`fem` via
  `fem_available()`, `threads` via `bd_warehouse`, `sheetmetal`, `sketch`,
  `holes`, `specs`); an unknown `requires` entry hides the skill. File reads
  are capped at 1 MiB and decoded strictly; anything else is `skill_invalid`,
  never a 500. Directory walks are bounded and symlink-safe.
- `core/skills_lint.py`: `Finding`, `lint_skill(path, profile)` with the
  `library`/`user` profiles (license/author/body-too-long are errors for
  `library`, warnings for `user`), codes `missing_skill_md`, `frontmatter`,
  `missing_key:*`, `name_mismatch`, `bad_name`, `bad_version`,
  `bad_description`, `bad_triggers`, `unknown_capability`, `unknown_key`,
  `empty_body`, `body_too_long`, `code_fence_syntax` (every ```python fence
  must `ast.parse`), `snippet_syntax`, `table_json` (size-capped),
  `broken_link`, `duplicate_skill_file`, `stray_file`; `lint_dir`,
  `lint_paths`, `has_errors`, `scaffold`.
- `config.py`: `get_skills_budget()` — env
  (`AGENTCAD_SKILLS_MAX_LOADED`/`_MAX_LOADED_CHARS`/`_MAX_SKILL_CHARS`) >
  config `skills` block > defaults 4 / 40 000 / 24 000; never writes.
- `cli.py`: `agentcad skill new <name> (--project P | --dir D)` (refuses to
  overwrite; exit 2 on usage) and `agentcad skill lint [<path>…] [--core]
  [--profile library|user] [--json]` (exit 0 clean / 1 errors / 2 usage; rows
  `path: level code: message`).
- `core/service.py`: `self.skills = SkillLibrary(self.store)`.
- `agentcad/skills/`: `README.md` plus six authored core skills —
  `enclosures` (walls per process, shell order, lip/groove, bosses,
  standoffs, vents; snippet `two_part_enclosure.py`, body + lid),
  `snap-fits` (cantilever/annular design, strain table
  `tables/material_strain.json`, the tapered-beam 1.64× correction; snippet
  `cantilever_lid.py` derives the undercut from the allowable strain),
  `brackets-and-mounts` (L/U/Z, gussets, bolt patterns, `tables/nema.json`
  NEMA 8–23; snippet `nema17_bracket.py`), `fits-and-clearances` (ISO 286
  IT6–IT11 + H/g/h/k/p deviations for 3–50 mm in `tables/iso286.json`,
  printed clearances, inserts, bearing seats; snippet `pin_and_bore.py`),
  `fdm-design-rules` (walls, overhangs, bridging, hole compensation,
  elephant foot, orientation, a pre-print checklist as `analyze_part`/SPECS
  calls; snippet `printable_bracket.py` with a teardrop bore), `fem-workflow`
  (`requires: [fem]`; the real `fem_*` tool surface, fixtures/loads by
  bounding-box plane, mesh vs wall, result reading, the fallback material).
  Every skill lints clean under `library`, every fence parses, every snippet
  builds green.
- Tests: `test_skills_library.py` (65), `test_skills_lint.py` (33),
  `test_skills_cli.py` (9), `test_skills_core_library.py` (parametrised over
  the shipped library: lint, fences, snippet builds, ≥ 12 entries);
  `test_packages_cli.py` updates the subparser-metavar literal that pins the
  command list.

## Files
- `agentcad/core/skills.py`, `agentcad/core/skills_lint.py` — new
- `agentcad/skills/README.md`, `agentcad/skills/{enclosures,snap-fits,brackets-and-mounts,fits-and-clearances,fdm-design-rules,fem-workflow}/` — new
- `agentcad/config.py`, `agentcad/cli.py`, `agentcad/core/service.py` — the seams above
- `tests/test_skills_library.py`, `tests/test_skills_lint.py`, `tests/test_skills_cli.py`, `tests/test_skills_core_library.py` — new
- `tests/test_packages_cli.py` — metavar literal

## Notes
Rulings: a disabled skill resolves to `skill_disabled` (not
`skill_not_found`); asset refusals reuse `skill_not_found` with
`details.asset`; `key:` with no items is an empty string; trust/enable work on
any layer but `is_trusted` is always true for core; `only` makes a
non-selected skill `skill_not_found` with a hint. The snap-fits author found
the brief's two deflection formulas were the same formula — the skill states
the constant-section one and the 1.64× taper factor with sources.
`make test` on the combined tree of slices 1–4 — 5646 passed, 51 skipped, 12 failed, 1 error in 852 s (nine are the
changelog count-guard tests reading this entry's own not-yet-filled count,
`test_prd028_acceptance::test_ac6_real_solver` is the known local `[fem]`
timeout that skips on CI, and `test_supervisor`'s memory-cap kill plus
`test_server::test_project_and_part_flow` were timeouts under the load of
concurrent slice agents — both re-run green in isolation, 2 passed in 13 s).
