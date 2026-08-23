---
name: fem-workflow
description: Running and reading AgentCAD FEM — fem_static, fem_modal, fem_thermal — fixture and load faces, mesh size, safety factors, deflection limits, material_basis, and what not to trust.
triggers: [fem, stress, strain, deflection, modal, vibration, natural frequency, thermal, heat, conduction, load, fixture, boundary condition, safety factor, von mises, stiffness, mesh size, material basis, singularity, buckling]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: [fem]
---
# FEM workflow

Three solvers ship behind the `[fem]` extra: `fem_static` (linear elasticity),
`fem_modal` (frequencies), `fem_thermal` (steady-state conduction). Use this to
get a number you can defend — hand calc or solve, BCs on the faces you meant,
mesh sized to the thinnest wall, `material_basis` read before the stress. Do
**not** use FEM to size a fastener, to check a wall (`analyze_part
{kind: "wall"}` is one call), or when the failure mode is contact, bolt
preload, plasticity, buckling, fatigue, creep or convection: none exist here,
and a confident number from a model that cannot represent the failure mode is
worse than none.

## Hand calculation first, FEM to confirm

For a prismatic beam, plate or shaft with one clean load path, beam theory is
seconds of work and is the check on the solve (`I = b·h³/12`, `c = h/2` for a
`b×h` rectangle in bending):

| Case | Deflection | Peak bending stress |
|---|---|---|
| Cantilever, tip load F, span L | `δ = F·L³/(3·E·I)` | `σ = F·L·c/I` |
| Simply supported, centre F | `δ = F·L³/(48·E·I)` | `σ = F·L·c/(4I)` |

Expect FEM within ~10 % when `L/h ≥ 10`; below that Euler-Bernoulli ignores
shear and FEM is rightly softer. Solve when the geometry is not a beam, the
load path branches, you need *where* stress peaks, or a spec row must pin it.
Cross-check the solvers: `f ≈ 15.76/√δ` Hz, `δ` the static deflection in mm
under the mass carried.

## The tools, exactly as they are

```
fem_static (project, part_id, fixed_face, load_face, load_N=100, load_dir=[0,0,-1],
            E_mpa=None, nu=None, mesh_size_mm=3.0, temperature_c=20)
fem_modal  (project, part_id, n_modes=6, fixed_face=None, E_mpa=None, nu=None,
            temperature_c=20)
fem_thermal(project, part_id, hot_face, cold_face, t_hot_c, t_cold_c, k_w_m_k=None)
```

Returns: `max_disp_mm`/`max_von_mises_mpa`, `frequencies_hz`,
`t_min_c`/`t_max_c`/`flux_w` — each with `material_basis`. Faces are
`{"axis": "x"|"y"|"z", "side": "min"|"max"}`; `n_modes` is 1–24; `fem_thermal`
refuses a hot face equal to the cold face. `mesh_size_mm` exists on
**`fem_static` only** — modal and thermal always mesh at the kernel default
3.0 mm. `load_N` is a **total force** spread as uniform traction over the
matched face area; `load_dir` is normalised.

Units are mm-N-MPa-tonne-s, except `k_w_m_k` in **W/(m·K)** against mm
coordinates (the solver carries the 1e-3 factor — never convert k to W/(mm·K)).
No body load exists: for self-weight apply `F = mass_g/1000 × 9.81` N (from
`get_part` metrics) to the reacting face.

## Fixture and load faces — the bounding-box plane rule

`{axis, side}` is **not** a build123d selector and not one topological face: it
takes *every mesh facet within 1e-6 mm of the part's bounding-box extreme plane
on that axis*. Four consequences, all biting:

- **Everything on that plane is selected** — four bracket feet at `z=min` are
  all clamped (usually right), both flanges of a U-channel at `x=max` both
  loaded (usually wrong).
- **The proudest feature defines the plane** — a boss 0.5 mm above the face you
  meant, or a fillet rolling its edge away, moves the plane off it and it
  contributes zero facets.
- **An unmatched face fails differently per tool.** `load_face`, `hot_face`,
  `cold_face` and `fem_modal`'s `fixed_face` refuse with *"matched no mesh
  facets"*; **`fem_static` does not check its `fixed_face`**, so an unmatched
  clamp leaves the model unconstrained — a singular solve, not a refusal.
- **A curved face touches its plane in a line** — a cylinder loaded on `x=max`
  gets a sliver of area and a huge traction; load flats only.

So the selector work happens in `build(p)`: make the clamp face a bbox extreme,
flat and alone on its plane — reorient, leave that end unfilleted, add a flat
land — then confirm against `get_part` → `metrics.bbox`.

```python
from build123d import *

def build(p):
    with BuildPart() as part:
        Box(p.length, p.width, p.thickness)
        # Round the free end only; a fillet on the clamped -X end would leave
        # the BC selecting a sliver instead of the fixture.
        free_end = part.edges().filter_by(Axis.Z).group_by(Axis.X)[-1]
        fillet(free_end, radius=min(3.0, p.width / 4.0))
    clamp = part.part.faces().filter_by(Plane.YZ).sort_by(Axis.X)[0]
    assert clamp.area > 0.9 * p.width * p.thickness  # still a whole face
    return part.part
```

## Mesh size vs the thinnest wall

Get **2–3 quadratic (P2) tets through the thinnest bending wall**:
`mesh_size_mm ≈ t_min/2` to `t_min/3`, `t_min` from `analyze_part
{kind: "wall"}` and never guessed: 0.4–0.6 mm for a 1.2 mm FDM shell, 0.7–1.0
at 2 mm, 1.0–1.5 at 3 mm; the 3.0 default suits ≥ 6 mm. Elements scale as
`(1/h)³` — keep `n_tets` under a few hundred thousand or the 600 s budget and
the memory cap end the run first. Sizing is **global** (gmsh gets
`MeshSizeMax`, and `0.6 ×` it as min): no local refinement.

Convergence costs one call: run at `h`, then `h/2`. Displacement moving < 5 %
has converged; `max_von_mises_mpa` moving > 10 % is a singularity, not a
stress. A coarse mesh is *stiff*, under-predicting deflection and
over-predicting frequency — unconservative both ways — so with modal pinned at
3.0 mm read `frequencies_hz` on a small part as an upper bound, trusting mode
*ratios* over absolutes.

## Reading the result

**Stress.** Compare `max_von_mises_mpa` to `yield_mpa`, not `ultimate_mpa`; von
Mises is a ductile criterion, so brittle materials (ceramics, cast iron, filled
thermosets) want maximum principal stress. `n = yield / max_vm`; Shigley's
ranges are 1.25–2 for well-characterised material and controlled loads, 2–2.5
average, 2.5–4 for uncertain loads — then derate for creep (~25–50 % of
short-term yield under sustained thermoplastic load) and FDM across the layers
(~half datasheet, `fdm-design-rules`). Von Mises *above* yield is not a stress;
it is the model saying it is invalid.

**Deflection.** Judge `max_disp_mm` against the alignment tolerance the part
must hold; with none, L/250 for a non-precision bracket, L/360 (live) and L/240
(total) from the building code, L/1000 or tighter for a frame carrying a datum.

**Frequency.** Keep `frequencies_hz[0]` 1.5–2× above the highest excitation
(motor rev/s, step rate, blade pass); rotating gear wants 15–20 % separation
from any operating speed (API 617). Without `fixed_face` it is free-free
(rigid-body modes dropped, `note` counts them), valid only if the real
mounting is far softer than the part.

**Temperature.** `t_max_c`/`t_min_c` are pinned by your own faces, so read
`flux_w` — or `R = (t_hot − t_cold)/flux_w` K/W against a hand `R = L/(k·A)`,
L in **m**, A in **m²**.

## material_basis, the fallbacks, the temperature warning

Every result carries `material_basis`: per scalar consumed (`E_mpa`, `nu`,
`k_w_m_k`), the `value` plus `basis`, `source`, `T_c`, `interpolated`,
`clamped`, `table_range`, `unit` when read from the material;
`basis: "explicit"` when you passed it; `basis: "fallback_default"` for
`fem_static`'s historical **210000 MPa** E and **ν = 0.3**.

Read `basis` before the stress. `fallback_default` on `E_mpa` means a polymer
part solved as steel — deflection scales with 1/E, so a 100× error, not a
margin. It is rare (407 of 434 shipped materials carry `E_gpa`) but a
project-local one may not. On `nu` it is the *normal* case: only 227 of 434
carry `poisson_ratio`, `al6061` (the default material) not among them — 0.30
for aluminium's 0.33 moves bending deflection under 1 %, so note it and go.
`fem_modal` has **no** E fallback: missing `E_gpa` is a hard refusal there,
because frequency scales with √E.

A property with a **table** (27 materials for `E_gpa`, 45 for `k_w_m_k`) is
interpolated at the evaluation temperature and **clamped** to its end row
outside the span, never extrapolated, appending to `warnings`:
`temperature_out_of_table_range: k_w_m_k evaluated at 400.0 C, table covers
20.0..300.0 C; end value used`. `fem_static`/`fem_modal` evaluate at
`temperature_c` (default 20); `fem_thermal` evaluates k once at the **mean**
of `t_hot_c` and `t_cold_c` — weakest when ΔT is large and k moves. Where a
table reaches zero stiffness (`steel_a36` carries the EN 1993-1-2 fire curve) a
high `temperature_c` is refused by name.

## Do not trust

1. **Stress at a sharp re-entrant corner** — no converged value exists; refine
   and it grows without bound. Read on a fillet there, or a wall-thickness
   away, or use a Roark concentration factor.
2. **Stress at the clamp** — the result's own `note` says so. `{axis, side}`
   fixes all three DOF on every node in the plane: no bolt compliance, no slip,
   so the part is over-stiffened and a stress ring manufactured. A real joint
   is softer and deflects more.
3. **A point load** — `load_N` is uniform traction over the whole matched face,
   so bearing under a pin or screw head is not in it; nor is **contact** of any
   kind, and a multi-solid part meshes as disjoint bodies with no coupling
   (clamp one, load another: the load path does not exist).
4. **Nonlinearity and anisotropy** — linear, isotropic and small-deflection
   only (past ~half a plate's thickness the answer is optimistic): no
   plasticity, no **buckling** (a slender strut can fail far below the reported
   von Mises), `E_perp_gpa` never read.
5. **`fem_thermal` as a thermal design tool** — conduction between two held
   temperatures: no convection, radiation or generation. It gives a conduction
   resistance, never "how hot does this get".

## Pinning a result in design-specs

`check_fem_static` turns a solve into a standing budget the `check` gate
re-measures; declaring one needs no `[fem]`.

```python
from agentcad.toolkit.specs import check_fem_static

SPECS = [
    check_fem_static(
        {"axis": "x", "side": "min"},  # clamped end
        {"axis": "x", "side": "max"},  # loaded end
        50.0,                          # load_N, total
        max_disp_mm=0.8,               # L/250 on a 200 mm arm
        max_vm_mpa=184.0,              # al6061 yield 276 / 1.5
        requirement="STR-004",
    ),
]
```

Four things the row cannot say:

- **`load_dir` is fixed at `[0, 0, -1]`** — orient the part until the load case
  you care about is −Z.
- **The gate sends E at 20 °C and nothing else** — no `nu` (kernel 0.3), no
  `mesh_size_mm` (kernel 3.0); pin limits from a run with those defaults.
- **One of `max_vm_mpa` / `max_disp_mm` is required**, or it never fails.
- **Tier 3, run-only**: one request, 600 s, memoised on the part cache key plus
  E; without the extra it skips as `fem_extra_missing`, so a green `check` with
  no FEM is not evidence (`design-specs`).

## Checklist

- [ ] Hand calc done first; expected `max_disp_mm` written down.
- [ ] `analyze_part {kind: "wall"}` run; `mesh_size_mm ≤ t_min/2`.
- [ ] `metrics.bbox` read; both BC faces flat, on a bbox extreme, alone.
- [ ] `max_disp_mm` within ~10 % of the hand calc; at `h/2` displacement moved
      < 5 % and stress < 10 %.
- [ ] `material_basis` clean: no unintended `fallback_default`, no unexplained
      `temperature_out_of_table_range`.
- [ ] Peak stress read away from clamps and corners, against `yield_mpa` with a
      stated factor; buckling, fatigue and creep judged outside FEM, and the
      result pinned by `check_fem_static` with a requirement id.

## Sources

- Budynas & Nisbett, *Shigley's Mechanical Engineering Design*, 11th ed.,
  McGraw-Hill, 2020 — safety factors, beam formulas, von Mises theory.
- Young, Budynas & Sadegh, *Roark's Formulas for Stress and Strain*, 9th ed.,
  McGraw-Hill, 2020 — closed-form cases, stress-concentration factors.
- NAFEMS, *How To Do Linear Static Analysis with Finite Elements*; *What Is A
  Singularity?* — convergence practice, the re-entrant corner.
- EN 1993-1-2 Table 3.1 — the E(T) factors behind the steel tables.
- International Building Code 2021, Table 1604.3 (deflection limits); API 617
  (frequency separation); ASM Handbook Vol. 1 and 2 (material cards).
