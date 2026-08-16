# 0166 — 2026-08-16 — PRD-011 design spec + implementation plan; the repo gets a license

## Summary

The design round for PRD-011 (parts library & package registry), reframed by
the roadmap's marketplace resequencing (0165): registry-first, with the
git-hosted index and the seeded kernel-validated catalog as primary
deliverables. Also resolves the standing LICENSE blocker — **Apache-2.0** for
the repo and for the seeded catalog packages, a founder decision — because the
package format carries a required `license` field and the catalog needs a real
value at its first commit.

## Changes

- `docs/superpowers/specs/2026-08-16-parts-library-registry-design.md` — the
  design spec: 13 settled decisions, surfaces, data flow, testing strategy,
  risks, naming traps, and eight divergences from the PRD as written.
- `docs/superpowers/plans/2026-08-16-parts-library-registry.md` — the
  implementation plan: 14 vertical TDD slices, registry capability (format,
  cache, lockfile, git index, gate) before UI and the McMaster path.
- `LICENSE` — Apache License 2.0, verbatim from apache.org.
- `pyproject.toml` — `license = "Apache-2.0"` + `license-files`.
- `docs/prd/in-progress/PRD-011-parts-library-registry.md` — moved from
  `pending/` (in the resequencing commit, recorded here for the paper trail).

## Design decisions worth the read

- **The preset schema is frozen as the PRD-012 configuration schema**:
  `{name: {params: {...}, label?, description?}}`. The decisive argument is
  ambiguity, not extensibility — a flat `{name: {param: value}}` map is
  unreadable the day a part declares a parameter named `label`, and part
  scripts declare arbitrary names. PRD-012 FR1 must be amended (slice 14).
- **`tools_packages.py` keeps its name and the load-order trap is removed
  rather than dodged**: the pack registers no gate provider, ever — the
  publish gate gates a directory, not a merge, and materialized package parts
  are ordinary parts already covered by the PRD-004 `checks` gate. The
  `pac` < `pro` fact is documented in the module docstring and pinned by a
  test.
- **Materialize-on-use confirmed** on a stronger argument than the PRD's:
  copy-in is what makes geometry CI work on a bare checkout (the Action
  measures a working tree with no `~/.agentcad`). The provenance header
  carries no timestamp, no client id, no path — AC3 demands byte-identical
  re-materialization. `remove_package` touches zero script bytes.
- **The content id is a canonical file-tree digest**, not an archive hash —
  tar is not byte-stable across producers (the DXF lesson), and a tree digest
  is the same number from a directory, a git checkout, and a cache copy.
- **The gate's variant matrix is one-at-a-time + every declared preset,
  explicitly not the cross product** — mutually-constrained params would
  redden correct content; an author who wants a corner covered declares it as
  a preset.
- **`index.json` records `gate.build123d`**, the pinned kernel version the
  package was actually proved against — `min_agentcad` was the wrong
  compatibility key.

## Divergences from the PRD (recorded in the spec's final section)

Eight, including: FR2's "sha256 of the canonical archive" becomes the tree
digest (there is no archive); FR9's "builds at every parameter's min and max"
is vacuous on unbounded params, so the gate *requires* bounds of published
packages; FR9 does not check drawings (a package holds parts, not sheets —
PRD-031's parenthetical should drop it); cloud index and semantic search ship
as interfaces only; only the publish CLI ships (a publish *tool* that can only
write locally is one an agent cannot usefully call); `package_from_step`
scaffolds but does not auto-place connectors; the PRD's own MVP ordering is
superseded by the roadmap; presets get the frozen schema above.

## Founder decisions taken this round

- **Apache-2.0** for the repo (adoption over copyleft: the moat is the catalog
  and audience, not the code) and for catalog packages (they are code; CC is
  wrong for code by CC's own guidance).
- Catalog stays in-repo at `catalog/` (reversible; the git-index reduction
  makes moving cheap; the dogfood test keeps it honest).
- `@scope/` package naming deferred — widening the name regex later is
  additive and non-breaking.

## Files

- `docs/superpowers/specs/2026-08-16-parts-library-registry-design.md`
- `docs/superpowers/plans/2026-08-16-parts-library-registry.md`
- `LICENSE` · `pyproject.toml`
- `docs/changelog/0166-prd-011-design-and-license.md` — this entry

## Notes

Docs + licensing only; no production code. The plan's slice changelogs were
reserved as 0166–0179 before this entry claimed 0166; slices start at 0167.
Import sanity after the pyproject edit: `uv run python -c "import agentcad"`
ok. The last measured full run remains 2527 passed, 1 skipped (0163).
