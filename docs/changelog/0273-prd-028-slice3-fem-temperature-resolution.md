# 0273 — PRD-028 slice 3: FEM temperature resolution and `material_basis`

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
The three FEM tools now take their elastic and thermal scalars **from the part
material**, evaluated at a temperature, and say so in the result. Resolution is
entirely service-side: the kernel is untouched and every kernel request carries
exactly the scalar keys (`E_mpa`, `nu`, `k_w_m_k`) it carried before. A property
that ships a `table` is linearly interpolated, clamped to its end row outside
the table's span, and a clamped read adds a `temperature_out_of_table_range`
warning so nobody mistakes a clamp for an extrapolation (spec §7, FR4/G3/AC3).

## Changes
- `core/tools_analysis.py` gains a module-level, importable
  `resolve_property(service, project, material_id, key, T_c)` →
  `{value, basis, source, T_c, interpolated, clamped, table_range, unit}` or
  `None` when the material does not carry the key. It reads
  `service.materials.resolve(project, id)` when the materials-v2 pack is active
  and falls back to `materials.get_material` otherwise (the old in-`register`
  `_material` helper, hoisted to module scope — nothing else imported it).
- `fem_thermal`: with no `k_w_m_k` argument, k is the material's conductivity at
  `T_eval = (t_hot_c + t_cold_c) / 2` — the one defensible single temperature
  for a linear steady-state conduction model. An explicit `k_w_m_k` bypasses the
  material as before and is recorded as `{value, basis: "explicit"}`.
- `fem_static`: `E_mpa` and `nu` now default to `None` and resolve from the
  material at the new `temperature_c` argument (default 20). The historical
  fallbacks are kept and *recorded*: no E → 210000 MPa, no `poisson_ratio` →
  0.3, each as `{value, basis: "fallback_default"}`.
- `fem_modal`: same `temperature_c` + ν resolution; E keeps its hard refusal
  (`material 'x' has no Young's modulus; pass E_mpa`) because a modal frequency
  scales with √E — a silent steel default would be a wrong answer, not a rough
  one.
- Every FEM result gains `material_basis` (one entry per scalar the solver
  consumed) and, per clamped entry, an appended `warnings` string:
  `temperature_out_of_table_range: k_w_m_k evaluated at 400.0 C, table covers
  20.0..300.0 C; end value used`. The solver's own warnings are preserved (the
  list is extended, or created when the kernel returned none).
- `core/specs.py::_youngs_mpa` reads E through the same `resolve_property` at
  20 °C. `_fem_material_key` is unchanged for a point-only material (`al6061`
  still keys `E68900`) — asserted by a test, because that memo guards cached FEM
  evidence.
- `server/routes_analysis.py`: `temperature_c` added to the `fem` and
  `fem/modal` body whitelists, so the new argument is reachable over HTTP.
- Tool descriptions and `docs/agent-api.md`'s three FEM rows document
  `temperature_c`, the material defaults, `material_basis` and the warning.

## Files
- `agentcad/core/tools_analysis.py` — `resolve_property`, `_quietly`, `_in`,
  `_clamp_warning`, `_decorate`, `CLAMP_WARNING`; the three FEM tools' argument
  defaults, schemas and descriptions.
- `agentcad/core/specs.py` — `_youngs_mpa` goes through `resolve_property`.
- `agentcad/server/routes_analysis.py` — `temperature_c` in two whitelists.
- `tests/test_fem_material_resolution.py` — new: 23 fake-kernel tests plus 2
  `importorskip` real-solver tests.
- `docs/agent-api.md` — FEM rows + a `material_basis` paragraph.

## Notes
- **Registering the FEM tools without the extra.** `tools_analysis.register`
  reads `kernel.handlers.fem.fem_available()` at registration time, so the test
  fixture `monkeypatch.setattr`s that function true **before** `build_registry`
  and then swaps `service.kernel` for a recorder. Parts are created while the
  real kernel is still installed (a part record only exists once it has built).
- **`fem_static` must not gain a refusal.** It never looked a material up
  before this slice, so an *unresolvable* material id (a
  `set_project_materials` that dropped one) reads as "no such property" there
  and keeps the historical defaults — that is what `_quietly` is for.
  `fem_modal`/`fem_thermal` already resolved the material and still raise.
- The `E_mpa` basis entry reports the number the solver consumed: the property's
  GPa value ×1000 with `unit: "MPa"`, while `table_range` stays in °C.
- `al6061` carries no `poisson_ratio` in the landed library today, so
  `fem_static` on it sends ν = 0.3 with `basis: "fallback_default"`. The test
  asserts the *rule* against the resolved card rather than the literal, so a
  curated ν does not turn into a red.

## Verification
- `.venv/bin/python -m pytest -q tests/test_fem_material_resolution.py
  tests/test_materials.py tests/test_specs.py tests/test_specs_api.py
  tests/test_specs_gate.py tests/test_specs_kernel.py tests/test_specs_toolkit.py
  tests/test_analysis.py` → **313 passed, 9 skipped**.
- `.venv/bin/python -m pytest -q -n 4 --dist loadscope tests/test_prd003*.py
  tests/test_specs*.py tests/test_checks*.py` → **491 passed, 2 skipped**.
- The two real-solver tests skip here (`agentcad[fem]` is not installed).
