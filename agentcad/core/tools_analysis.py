"""Tool pack: geometric analysis (tier 1) and optional FEM (tier 2:
linear-static, modal, thermal — all gated on the agentcad[fem] extra).

Material resolution (PRD-028 FR4) is entirely **service side**: the kernel
request shapes are unchanged (the solver still takes the scalars ``E_mpa``,
``nu`` and ``k_w_m_k``), and this layer decides which numbers they carry, at
which temperature, and reports how it got them in ``material_basis``. A value
read off a property *table* outside that table's span is clamped to the end row
(:meth:`materials.Property.at`) and the result gains a
``temperature_out_of_table_range`` warning — a number the caller must not
mistake for an extrapolation.
"""

from __future__ import annotations

from .model import ValidationError
from .tools import Tool, schema

#: Prefix of the warning a clamped table evaluation adds to a FEM result. The
#: rest of the string names the property, the temperature and the table span.
CLAMP_WARNING = "temperature_out_of_table_range"


def _material(service, project: str, material_id: str):
    """Full material record. Uses the project-aware resolver when the
    materials-v2 pack is active, else the builtin-only accessor."""
    resolve = getattr(service.materials, "resolve", None)
    if callable(resolve):
        return resolve(project, material_id)
    from .materials import get_material

    return get_material(material_id)


def resolve_property(service, project: str, material_id: str, key: str,
                     T_c: float) -> dict | None:
    """One material property at a temperature, with its evidence.

    ``{value, basis, source, T_c, interpolated, clamped, table_range, unit}``,
    or ``None`` when the material simply does not carry ``key`` (the callers
    each have their own documented fallback for that). An *unknown material*
    still raises — that is a caller's mistake, not a missing datum.

    This is the one place `fem_static`/`fem_modal`/`fem_thermal` and
    `specs._youngs_mpa` read a property from, so the number a spec memo is
    keyed on is the number the solver consumed.
    """
    prop = _material(service, project, material_id).prop(key)
    if prop is None:
        return None
    value, interpolated, clamped = prop.at(float(T_c))
    if value is None:
        return None
    table = prop.table
    return {
        "value": float(value),
        "basis": prop.basis,
        "source": prop.source,
        "T_c": float(T_c),
        "interpolated": interpolated,
        "clamped": clamped,
        "table_range": ([float(table[0][0]), float(table[-1][0])]
                        if table else None),
        "unit": prop.unit,
    }


def _quietly(service, project: str, material_id: str, key: str,
             T_c: float) -> dict | None:
    """:func:`resolve_property`, but an unresolvable material reads as a
    missing property.

    ``fem_static`` never looked a material up before this slice (E and nu were
    hard-coded defaults), so a part whose material id no longer resolves — a
    ``set_project_materials`` that dropped an id, say — must keep running with
    those defaults rather than gain a brand-new refusal.
    """
    try:
        return resolve_property(service, project, material_id, key, T_c)
    except ValidationError:
        return None


def _in(entry: dict, factor: float, unit: str) -> dict:
    """The same evidence expressed in the unit the solver takes (GPa -> MPa)."""
    return {**entry, "value": entry["value"] * factor, "unit": unit}


def _clamp_warning(key: str, entry: dict) -> str:
    lo, hi = entry["table_range"]
    return (f"{CLAMP_WARNING}: {key} evaluated at {entry['T_c']} C, "
            f"table covers {lo}..{hi} C; end value used")


def _decorate(result, basis: dict):
    """Attach ``material_basis`` and any clamping warnings to a kernel result.

    Warnings are *appended* to whatever list the kernel already returned (and
    the list is created when it returned none), so a solver-side warning is
    never overwritten by a material one.
    """
    if not isinstance(result, dict):
        return result
    result["material_basis"] = basis
    warnings = [_clamp_warning(key, entry) for key, entry in basis.items()
                if entry.get("clamped")]
    if warnings:
        existing = result.get("warnings")
        result["warnings"] = (list(existing) if isinstance(existing, list)
                              else []) + warnings
    return result


def register(registry, service) -> None:
    def analyze_part(project: str, part_id: str, kind: str = "inertia",
                     plane: str = "XY", axis: str = "Z",
                     min_required: float | None = None) -> dict:
        record = service.store.get_part(project, part_id)
        if record.kind != "script":
            raise ValidationError("analysis is supported for script parts only")
        script = service.store.read_script(project, part_id)
        density = service.material_density(project, record.material)
        return service.kernel.request("analyze", {
            "script": script, "params": record.effective_params, "kind": kind,
            "plane": plane, "axis": axis, "min_required": min_required,
            "density_g_cm3": density,
        }, timeout_s=120.0)

    registry.register(Tool(
        "analyze_part",
        "Geometric analysis of a part. kind=section (cross-section area on a "
        "plane), wall (min wall thickness, optionally vs min_required), "
        "inertia (mass-properties tensor), projected_area (silhouette area "
        "along an axis).",
        schema(
            {
                "project": {"type": "string"},
                "part_id": {"type": "string"},
                "kind": {"type": "string", "description": "section|wall|inertia|projected_area|curvature"},
                "plane": {"type": "string", "description": "section plane: XY|XZ|YZ"},
                "axis": {"type": "string", "description": "projected_area axis: X|Y|Z"},
                "min_required": {"type": "number", "description": "wall: min acceptable mm"},
            },
            ["project", "part_id", "kind"],
        ),
        analyze_part,
    ))

    # FEM: register only when the optional extra is importable, so agents never
    # see a tool that cannot run.
    from ..kernel.handlers.fem import fem_available

    if fem_available():
        def _elastic(project: str, material_id: str, E_mpa: float | None,
                     nu: float | None, temperature_c: float,
                     fallback_E: float | None) -> tuple[float, float, dict]:
            """``(E_mpa, nu, material_basis)`` for the two structural solvers.

            ``fallback_E`` is ``fem_static``'s historical 210000; ``fem_modal``
            passes ``None`` and keeps its hard refusal instead.
            """
            basis: dict = {}
            if E_mpa is None:
                entry = _quietly(service, project, material_id, "E_gpa",
                                 temperature_c)
                if entry is None:
                    if fallback_E is None:
                        raise ValidationError(
                            f"material {material_id!r} has no Young's "
                            "modulus; pass E_mpa"
                        )
                    E_mpa = fallback_E
                    basis["E_mpa"] = {"value": E_mpa,
                                      "basis": "fallback_default"}
                else:
                    entry = _in(entry, 1000.0, "MPa")   # GPa -> MPa
                    E_mpa = entry["value"]
                    basis["E_mpa"] = entry
            else:
                basis["E_mpa"] = {"value": E_mpa, "basis": "explicit"}

            if nu is None:
                entry = _quietly(service, project, material_id,
                                 "poisson_ratio", temperature_c)
                if entry is None:
                    nu = 0.3
                    basis["nu"] = {"value": nu, "basis": "fallback_default"}
                else:
                    nu = entry["value"]
                    basis["nu"] = entry
            else:
                basis["nu"] = {"value": nu, "basis": "explicit"}
            return E_mpa, nu, basis

        def fem_static(project: str, part_id: str, fixed_face: dict,
                       load_face: dict, load_N: float = 100.0,
                       load_dir: list | None = None,
                       E_mpa: float | None = None, nu: float | None = None,
                       mesh_size_mm: float = 3.0,
                       temperature_c: float = 20.0) -> dict:
            record = service.store.get_part(project, part_id)
            script = service.store.read_script(project, part_id)
            E_mpa, nu, basis = _elastic(project, record.material, E_mpa, nu,
                                        temperature_c, 210000.0)
            result = service.kernel.request("fem_static", {
                "script": script, "params": record.effective_params,
                "fixed_face": fixed_face, "load_face": load_face,
                "load_N": load_N, "load_dir": load_dir or [0, 0, -1],
                "E_mpa": E_mpa, "nu": nu, "mesh_size_mm": mesh_size_mm,
            }, timeout_s=600.0)
            return _decorate(result, basis)

        registry.register(Tool(
            "fem_static",
            "Linear-static FEM: clamp one axis-aligned face, load another, "
            "return max displacement and max von Mises. fixed_face/load_face = "
            "{axis: x|y|z, side: min|max}. E and nu default from the part "
            "material at temperature_c (a property table is interpolated, and "
            "clamped to its end value outside its range with a "
            "temperature_out_of_table_range warning); a material with no E "
            "falls back to 210000 MPa and no Poisson ratio to 0.3. The result "
            "records every one of those choices in material_basis.",
            schema(
                {
                    "project": {"type": "string"},
                    "part_id": {"type": "string"},
                    "fixed_face": {"type": "object"},
                    "load_face": {"type": "object"},
                    "load_N": {"type": "number"},
                    "load_dir": {"type": "array"},
                    "E_mpa": {"type": "number", "description": "override; default: the material's E at temperature_c"},
                    "nu": {"type": "number", "description": "override; default: the material's poisson_ratio, else 0.3"},
                    "mesh_size_mm": {"type": "number"},
                    "temperature_c": {"type": "number", "description": "evaluation temperature for material properties (default 20)"},
                },
                ["project", "part_id", "fixed_face", "load_face"],
            ),
            fem_static,
        ))

        def fem_modal(project: str, part_id: str, n_modes: int = 6,
                      fixed_face: dict | None = None, E_mpa: float | None = None,
                      nu: float | None = None,
                      temperature_c: float = 20.0) -> dict:
            if not 1 <= int(n_modes) <= 24:
                raise ValidationError("n_modes must be between 1 and 24")
            record = service.store.get_part(project, part_id)
            script = service.store.read_script(project, part_id)
            # Mass needs the real density — always the part material's.
            density = service.material_density(project, record.material)
            # No fallback modulus: a modal frequency scales with sqrt(E), so a
            # silent steel default would be a wrong answer, not a rough one.
            E_mpa, nu, basis = _elastic(project, record.material, E_mpa, nu,
                                        temperature_c, None)
            args = {
                "script": script, "params": record.effective_params,
                "n_modes": int(n_modes), "E_mpa": E_mpa,
                "nu": nu, "density_g_cm3": density,
            }
            if fixed_face is not None:
                args["fixed_face"] = fixed_face
            return _decorate(
                service.kernel.request("fem_modal", args, timeout_s=600.0),
                basis)

        registry.register(Tool(
            "fem_modal",
            "Modal FEM: natural frequencies (Hz) of a part, consistent-mass "
            "eigensolve with the part material's density (E and nu default "
            "from the material too, at temperature_c: a property table is "
            "interpolated, and clamped to its end value outside its range "
            "with a temperature_out_of_table_range warning; material_basis "
            "records what was used). Clamp an optional axis-aligned face "
            "({axis: x|y|z, side: min|max}); without one the free-free "
            "rigid-body modes are omitted from the result.",
            schema(
                {
                    "project": {"type": "string"},
                    "part_id": {"type": "string"},
                    "n_modes": {"type": "integer", "description": "modes to return (1..24, default 6)"},
                    "fixed_face": {"type": "object"},
                    "E_mpa": {"type": "number", "description": "override; default: the material's E at temperature_c"},
                    "nu": {"type": "number", "description": "override; default: the material's poisson_ratio, else 0.3"},
                    "temperature_c": {"type": "number", "description": "evaluation temperature for material properties (default 20)"},
                },
                ["project", "part_id"],
            ),
            fem_modal,
        ))

        def fem_thermal(project: str, part_id: str, hot_face: dict,
                        cold_face: dict, t_hot_c: float, t_cold_c: float,
                        k_w_m_k: float | None = None) -> dict:
            record = service.store.get_part(project, part_id)
            script = service.store.read_script(project, part_id)
            if k_w_m_k is None:
                # The mean of the two fixed temperatures is the one defensible
                # single temperature for a linear steady-state conduction model
                # (the solver takes one scalar k).
                t_eval = (float(t_hot_c) + float(t_cold_c)) / 2.0
                entry = resolve_property(service, project, record.material,
                                         "k_w_m_k", t_eval)
                if entry is None:
                    raise ValidationError(
                        f"material {record.material!r} has no thermal "
                        "conductivity; pass k_w_m_k"
                    )
                k_w_m_k = entry["value"]
                basis = {"k_w_m_k": entry}
            else:
                basis = {"k_w_m_k": {"value": k_w_m_k, "basis": "explicit"}}
            result = service.kernel.request("fem_thermal", {
                "script": script, "params": record.effective_params,
                "hot_face": hot_face, "cold_face": cold_face,
                "t_hot_c": t_hot_c, "t_cold_c": t_cold_c,
                "k_w_m_k": k_w_m_k,
            }, timeout_s=600.0)
            return _decorate(result, basis)

        registry.register(Tool(
            "fem_thermal",
            "Thermal FEM: steady-state conduction with fixed temperatures on "
            "two axis-aligned faces ({axis: x|y|z, side: min|max}). Returns "
            "t_min/t_max (C) and the total heat flow through the hot face in "
            "W. k defaults from the part material's conductivity, evaluated at "
            "the mean of t_hot_c and t_cold_c (a k(T) table is interpolated, "
            "and clamped to its end value outside its range with a "
            "temperature_out_of_table_range warning); material_basis records "
            "what was used.",
            schema(
                {
                    "project": {"type": "string"},
                    "part_id": {"type": "string"},
                    "hot_face": {"type": "object"},
                    "cold_face": {"type": "object"},
                    "t_hot_c": {"type": "number"},
                    "t_cold_c": {"type": "number"},
                    "k_w_m_k": {"type": "number", "description": "thermal conductivity W/(m*K); default: the material's, at (t_hot_c + t_cold_c) / 2"},
                },
                ["project", "part_id", "hot_face", "cold_face",
                 "t_hot_c", "t_cold_c"],
            ),
            fem_thermal,
        ))
