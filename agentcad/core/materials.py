"""Materials v2: engineering property schema, curated builtin library, and
layered user-defined materials.

Designed as the drop-in replacement for agentcad/core/materials.py.

Schema v2
---------
``density_g_cm3`` is the only *required* numeric property (it is the only one
the kernel consumes today — mass_g = volume_mm3 * density_g_cm3 / 1000, see
agentcad/kernel/worker.py:_metrics). Everything else is optional reference
data for the agent and the Metrics pane:

    E_gpa              Young's modulus, GPa
    yield_mpa          0.2% offset tensile yield strength, MPa
    ultimate_mpa       ultimate tensile strength, MPa (see per-family notes:
                       bending/compressive basis for wood/concrete)
    elongation_pct     tensile elongation at break, %
    cte_um_m_k         coefficient of thermal expansion, um/(m*K), ~20-100C
    k_w_m_k            thermal conductivity, W/(m*K), room temperature
    max_service_temp_c approximate continuous service temperature, C
    cost_usd_kg        rough 2024-2026 bulk material cost, USD/kg (order of
                       magnitude only; regional/volume variation is huge)
    category           one of CATEGORIES
    notes              condition/temper/basis caveats, free text

NOTE ``ultimate_mpa >= yield_mpa`` is deliberately NOT enforced: ductile
polymers (e.g. Ultem) legitimately report break strength below yield.

Values are *typical room-temperature datasheet values to 2-3 significant
figures*, not design allowables. Source basis is cited per family in the
table comments (MatWeb typical / MMPDS-consistent handbook values for
metals, manufacturer datasheets for polymers/AM, EN/Eurocode characteristic
values for construction, FPL Wood Handbook for timber). Do not use for
certification.

Layering / precedence
---------------------
Three layers, highest wins, resolved per lookup:

    1. project   — "materials" object in project.json      (per-project)
    2. global    — ~/.agentcad/materials.json              (per-machine)
    3. builtin   — MATERIALS below                          (shipped)

An entry in a higher layer with an existing id REPLACES the whole lower
entry (no field merging — predictable, and validation always requires
density_g_cm3, so a half-specified override cannot silently inherit the
wrong density). New ids simply extend the catalog. Builtin ids can be
overridden but never deleted (a project that references "al6061" must keep
resolving). Each resolved Material carries provenance in ``source``.

File formats:
    project.json:            {..., "materials": {"<id>": {<entry>}, ...}}
    ~/.agentcad/materials.json:  {"schema_version": 1,
                                  "materials": {"<id>": {<entry>}, ...}}

Entry = the Material fields minus id/source; only density_g_cm3 required,
``label`` defaults to the id. Unknown keys are REJECTED (typo safety, same
philosophy as ToolRegistry._validate's "unexpected argument").
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

try:  # inside agentcad
    from .model import ValidationError
except ImportError:  # standalone (spike/tests)
    class ValidationError(Exception):
        def __init__(self, message: str, details: dict | None = None):
            super().__init__(message)
            self.message = message
            self.details = details or {}


CATEGORIES = ("metal", "polymer", "composite", "wood", "masonry")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

# Fields a user-supplied entry may set (everything except id/source).
_NUMERIC_FIELDS = (
    "density_g_cm3", "E_gpa", "yield_mpa", "ultimate_mpa", "elongation_pct",
    "cte_um_m_k", "k_w_m_k", "max_service_temp_c", "cost_usd_kg",
)
_ENTRY_FIELDS = _NUMERIC_FIELDS + ("label", "category", "notes")
# Only density must be > 0; the others must merely be >= 0 when present
# (CTE can be ~0 for invar-likes; keep it simple: non-negative).
_REQUIRED = ("density_g_cm3",)


@dataclass(frozen=True)
class Material:
    id: str
    label: str
    density_g_cm3: float
    category: str = "metal"
    E_gpa: float | None = None
    yield_mpa: float | None = None
    ultimate_mpa: float | None = None
    elongation_pct: float | None = None
    cte_um_m_k: float | None = None
    k_w_m_k: float | None = None
    max_service_temp_c: float | None = None
    cost_usd_kg: float | None = None
    notes: str | None = None
    source: str = "builtin"  # builtin | global | project (provenance)

    def to_payload(self) -> dict:
        """JSON-able dict for API/tool responses (drops None properties)."""
        out = {"id": self.id, "label": self.label, "category": self.category,
               "density_g_cm3": self.density_g_cm3, "source": self.source}
        for f in _NUMERIC_FIELDS[1:]:
            v = getattr(self, f)
            if v is not None:
                out[f] = v
        if self.notes:
            out["notes"] = self.notes
        return out


def _m(id, label, density, category, E=None, ys=None, uts=None, el=None,
       cte=None, k=None, tmax=None, cost=None, notes=None) -> Material:
    return Material(
        id=id, label=label, density_g_cm3=density, category=category,
        E_gpa=E, yield_mpa=ys, ultimate_mpa=uts, elongation_pct=el,
        cte_um_m_k=cte, k_w_m_k=k, max_service_temp_c=tmax,
        cost_usd_kg=cost, notes=notes, source="builtin",
    )


# ---------------------------------------------------------------------------
# Builtin library (30 materials). All 10 v1 ids are preserved unchanged so
# existing project.json files keep resolving: al6061, steel_a36,
# stainless_316, ti6al4v, inconel718, abs, pla, nylon_pa12, concrete,
# douglas_fir. v1 densities are kept identical (cache keys hash density, so
# changing them would silently invalidate every existing mesh cache).
# ---------------------------------------------------------------------------

MATERIALS: dict[str, Material] = {m.id: m for m in [

    # -- Aluminum alloys --------------------------------------------------
    # Source basis: MatWeb typical / MMPDS-consistent handbook values,
    # wrought sheet/plate at room temperature, temper as labeled.
    # AlSi10Mg: laser powder-bed fusion, as-built, EOS/SLM datasheet typical.
    _m("al6061", "Aluminum 6061-T6", 2.70, "metal",
       E=68.9, ys=276, uts=310, el=12, cte=23.6, k=167, tmax=150, cost=4.0),
    _m("al7075", "Aluminum 7075-T6", 2.81, "metal",
       E=71.7, ys=503, uts=572, el=11, cte=23.4, k=130, tmax=120, cost=6.5),
    _m("al2024", "Aluminum 2024-T3", 2.78, "metal",
       E=73.1, ys=345, uts=483, el=18, cte=23.2, k=121, tmax=120, cost=6.0,
       notes="Poor corrosion resistance bare; usually clad or coated."),
    _m("alsi10mg", "AlSi10Mg (LPBF, as-built)", 2.67, "metal",
       E=70, ys=270, uts=460, el=6, cte=21, k=120, tmax=150, cost=35,
       notes="Additive (laser powder-bed fusion), as-built, typical vendor "
             "datasheet; properties drop after stress relief. Cost = powder."),

    # -- Titanium ---------------------------------------------------------
    # Source basis: MatWeb typical, annealed bar (MMPDS-consistent).
    _m("ti6al4v", "Titanium Ti-6Al-4V (Grade 5)", 4.43, "metal",
       E=114, ys=880, uts=950, el=14, cte=8.6, k=6.7, tmax=350, cost=35),

    # -- Nickel superalloys ----------------------------------------------
    # Source basis: Special Metals datasheets / MatWeb typical.
    _m("inconel718", "Inconel 718 (aged)", 8.19, "metal",
       E=200, ys=1030, uts=1240, el=12, cte=13.0, k=11.4, tmax=650, cost=60,
       notes="AMS 5662 solution + aged, room temperature."),
    _m("inconel625", "Inconel 625 (annealed)", 8.44, "metal",
       E=208, ys=460, uts=880, el=45, cte=12.8, k=9.8, tmax=800, cost=55,
       notes="Oxidation-limited to ~980 C; strength falls off above ~650 C."),

    # -- Stainless steels -------------------------------------------------
    # Source basis: MatWeb typical, annealed sheet (17-4PH: H900 condition).
    _m("stainless_304", "Stainless 304 (annealed)", 8.00, "metal",
       E=193, ys=215, uts=505, el=40, cte=17.3, k=16.2, tmax=800, cost=4.0),
    _m("stainless_316", "Stainless 316 (annealed)", 8.00, "metal",
       E=193, ys=205, uts=515, el=40, cte=16.0, k=16.3, tmax=800, cost=5.0),
    _m("ss17_4ph", "Stainless 17-4PH (H900)", 7.75, "metal",
       E=196, ys=1170, uts=1310, el=10, cte=10.8, k=17.9, tmax=300, cost=8.0,
       notes="H900 peak-aged; service above ~315 C over-ages the temper."),

    # -- Low-alloy / high-strength steels --------------------------------
    # Source basis: MatWeb typical, condition as labeled. Heat treatment
    # dominates 4130/4340 strength; values are for the stated condition only.
    _m("steel_4130", "Chromoly 4130 (normalized)", 7.85, "metal",
       E=205, ys=435, uts=670, el=25, cte=12.2, k=42.6, tmax=450, cost=1.5),
    _m("steel_4340", "Alloy 4340 (normalized)", 7.85, "metal",
       E=205, ys=860, uts=1280, el=12, cte=12.3, k=44.5, tmax=450, cost=2.0,
       notes="Q&T tempers reach 1500-1900 MPa UTS; this row is normalized."),
    _m("maraging300", "Maraging 300 (aged)", 8.00, "metal",
       E=190, ys=1900, uts=2000, el=7, cte=10.1, k=21, tmax=400, cost=80,
       notes="18Ni-300 / 1.2709, solution + aged; common in AM tooling."),

    # -- Structural steels ------------------------------------------------
    # Source basis: ASTM minimums + MatWeb typical for physicals.
    _m("steel_a36", "Steel A36 (structural)", 7.85, "metal",
       E=200, ys=250, uts=450, el=20, cte=11.7, k=50, tmax=400, cost=0.8,
       notes="ys/uts are ASTM spec minimums (uts range 400-550)."),
    _m("steel_a992", "Steel A992 (W-shapes)", 7.85, "metal",
       E=200, ys=345, uts=450, el=21, cte=11.7, k=50, tmax=400, cost=0.9),

    # -- Copper alloys ----------------------------------------------------
    # Source basis: MatWeb typical / Copper Development Association,
    # temper as labeled.
    _m("copper_c110", "Copper C110 (annealed)", 8.94, "metal",
       E=115, ys=70, uts=220, el=45, cte=17.0, k=391, tmax=200, cost=10),
    _m("brass_c260", "Brass C260 (half-hard)", 8.53, "metal",
       E=110, ys=310, uts=420, el=25, cte=20, k=120, tmax=200, cost=8.0),
    _m("bronze_c932", "Bearing bronze C932 (SAE 660)", 8.93, "metal",
       E=100, ys=125, uts=240, el=20, cte=18.0, k=59, tmax=200, cost=12),

    # -- Polymers ---------------------------------------------------------
    # Source basis: manufacturer datasheet typical for molded resin
    # (Sabic/Victrex/Covestro) and printed material where labeled; printed
    # parts vary strongly with process/orientation — treat as indicative.
    _m("abs", "ABS", 1.04, "polymer",
       E=2.3, ys=40, uts=40, el=25, cte=90, k=0.17, tmax=80, cost=2.5),
    _m("pla", "PLA", 1.24, "polymer",
       E=3.5, ys=60, uts=60, el=6, cte=68, k=0.13, tmax=50, cost=2.5,
       notes="Low heat deflection (~55 C); creeps in warm environments."),
    _m("petg", "PETG", 1.27, "polymer",
       E=2.1, ys=50, uts=53, el=120, cte=68, k=0.20, tmax=70, cost=3.0),
    _m("nylon_pa12", "Nylon PA12 (SLS/MJF typical)", 1.01, "polymer",
       E=1.7, ys=45, uts=48, el=20, cte=110, k=0.23, tmax=100, cost=5.0,
       notes="Powder-bed printed typical; molded PA12 elongates far more."),
    _m("pc", "Polycarbonate", 1.20, "polymer",
       E=2.4, ys=62, uts=66, el=110, cte=68, k=0.20, tmax=115, cost=4.0),
    _m("peek", "PEEK", 1.30, "polymer",
       E=3.9, ys=100, uts=100, el=30, cte=47, k=0.25, tmax=250, cost=90),
    _m("ultem", "Ultem 1000 (PEI)", 1.27, "polymer",
       E=3.2, ys=110, uts=105, el=60, cte=56, k=0.22, tmax=170, cost=40,
       notes="Break strength below yield is real datasheet behavior."),

    # -- Composites (laminate-level approximations) ----------------------
    # Source basis: typical epoxy-prepreg laminate data (manufacturer /
    # CES-style typical), QUASI-ISOTROPIC layup, ~55-60% fiber volume.
    # Anisotropy is collapsed to in-plane values — through-thickness E and k
    # are far lower. Composites have no yield; yield_mpa left unset.
    _m("cfrp_qi", "CFRP quasi-iso laminate", 1.60, "composite",
       E=50, uts=600, el=1.5, cte=3.0, k=5.0, tmax=120, cost=40,
       notes="In-plane values; through-thickness k ~0.7 W/m-K. Epoxy Tg "
             "limits service temperature."),
    _m("gfrp_qi", "GFRP (E-glass) quasi-iso laminate", 1.85, "composite",
       E=18, uts=300, el=2.0, cte=12, k=0.35, tmax=120, cost=8.0,
       notes="In-plane values, quasi-isotropic epoxy laminate."),

    # -- Construction -----------------------------------------------------
    # Source basis: Eurocode characteristic values (EN 1992 / EN 14080) and
    # FPL Wood Handbook clear-wood values at 12% moisture content.
    # ultimate_mpa basis differs here: concrete = mean tensile fctm
    # (compressive fck in notes); timber = bending strength along grain.
    _m("concrete", "Concrete C30/37", 2.40, "masonry",
       E=33, uts=2.9, cte=10, k=1.8, tmax=250, cost=0.10,
       notes="ultimate = mean tensile fctm; compressive fck = 30 MPa "
             "(cylinder). Unreinforced — no meaningful tensile design use."),
    _m("glulam", "Glulam GL24h", 0.42, "wood",
       E=11.5, uts=24, cte=5, k=0.13, tmax=60, cost=1.5,
       notes="EN 14080: E parallel to grain (mean), ultimate = "
             "characteristic bending strength f_m,k."),
    _m("douglas_fir", "Douglas Fir (clear, 12% MC)", 0.53, "wood",
       E=13.4, uts=85, cte=4, k=0.12, tmax=60, cost=1.0,
       notes="FPL Wood Handbook clear-wood bending values along grain; "
             "graded lumber allowables are far lower."),
]}

DEFAULT_MATERIAL = "al6061"

GLOBAL_MATERIALS_PATH = Path.home() / ".agentcad" / "materials.json"


# ---------------------------------------------------------------------------
# Validation of user-defined entries
# ---------------------------------------------------------------------------

def validate_material_entry(material_id: str, entry: dict, source: str) -> Material:
    """Validate one user-supplied materials entry, return a Material.

    Rejects: bad id, non-dict entry, unknown keys, missing/non-positive
    density, negative numbers, unknown category, non-string label/notes.
    """
    if not _ID_RE.match(material_id or ""):
        raise ValidationError(
            f"invalid material id {material_id!r}",
            {"expected": "[a-z][a-z0-9_]{0,39}"},
        )
    if not isinstance(entry, dict):
        raise ValidationError(f"material {material_id!r}: entry must be an object")
    unknown = sorted(set(entry) - set(_ENTRY_FIELDS))
    if unknown:
        raise ValidationError(
            f"material {material_id!r}: unknown field(s): {', '.join(unknown)}",
            {"unknown": unknown, "known": sorted(_ENTRY_FIELDS)},
        )
    for key in _REQUIRED:
        if key not in entry:
            raise ValidationError(f"material {material_id!r}: {key} is required")
    values: dict = {}
    for key in _NUMERIC_FIELDS:
        if key not in entry or entry[key] is None:
            continue
        v = entry[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValidationError(f"material {material_id!r}: {key} must be a number")
        v = float(v)
        if key == "density_g_cm3":
            if not (0 < v <= 25):  # osmium is 22.6; anything above is a typo
                raise ValidationError(
                    f"material {material_id!r}: density_g_cm3 must be in (0, 25]"
                )
        elif v < 0:
            raise ValidationError(f"material {material_id!r}: {key} must be >= 0")
        values[key] = v
    category = entry.get("category", "metal")
    if category not in CATEGORIES:
        raise ValidationError(
            f"material {material_id!r}: unknown category {category!r}",
            {"known": list(CATEGORIES)},
        )
    label = entry.get("label", material_id)
    notes = entry.get("notes")
    if not isinstance(label, str) or (notes is not None and not isinstance(notes, str)):
        raise ValidationError(f"material {material_id!r}: label/notes must be strings")
    return Material(id=material_id, label=label, category=category,
                    notes=notes, source=source, **values)


def validate_materials_dict(materials: dict, source: str) -> dict[str, Material]:
    if not isinstance(materials, dict):
        raise ValidationError(f"{source} materials must be an object of id -> entry")
    return {
        mid: validate_material_entry(mid, entry, source)
        for mid, entry in materials.items()
    }


# ---------------------------------------------------------------------------
# Layered resolution
# ---------------------------------------------------------------------------

class MaterialLibrary:
    """Builtins + ~/.agentcad/materials.json + per-project overrides.

    The global file is re-read only when its mtime changes (cheap stat per
    lookup; the service calls resolve() on every rebuild). A corrupt or
    invalid global file degrades to builtins with a warning recorded in
    ``global_error`` rather than breaking every build.
    """

    def __init__(self, global_path: Path | str = GLOBAL_MATERIALS_PATH):
        self.global_path = Path(global_path)
        self._global_cache: dict[str, Material] = {}
        self._global_mtime: float | None = None
        self.global_error: str | None = None

    # -- layers -----------------------------------------------------------

    def _global_layer(self) -> dict[str, Material]:
        try:
            mtime = self.global_path.stat().st_mtime
        except OSError:
            self._global_cache, self._global_mtime = {}, None
            self.global_error = None
            return self._global_cache
        if mtime == self._global_mtime:
            return self._global_cache
        try:
            raw = json.loads(self.global_path.read_text(encoding="utf-8"))
            entries = raw.get("materials", {}) if isinstance(raw, dict) else None
            if entries is None:
                raise ValidationError("materials.json must be an object")
            self._global_cache = validate_materials_dict(entries, "global")
            self.global_error = None
        except (ValidationError, json.JSONDecodeError, OSError) as exc:
            self._global_cache = {}
            self.global_error = f"{self.global_path}: {exc}"
        self._global_mtime = mtime
        return self._global_cache

    # -- public API --------------------------------------------------------

    def effective(self, project_materials: dict | None = None) -> dict[str, Material]:
        """Full resolved catalog: builtin < global < project."""
        catalog = dict(MATERIALS)
        catalog.update(self._global_layer())
        if project_materials:
            catalog.update(validate_materials_dict(project_materials, "project"))
        return catalog

    def resolve(self, material_id: str,
                project_materials: dict | None = None) -> Material:
        catalog = self.effective(project_materials)
        material = catalog.get(material_id)
        if material is None:
            raise ValidationError(
                f"unknown material {material_id!r}",
                {"known": sorted(catalog)},
            )
        return material


# Backward-compatible module-level accessor (builtin layer only). Existing
# call sites in project.py/service.py migrate to MaterialLibrary.resolve();
# keep this for tests and for contexts with no project (e.g. templates).
def get_material(material_id: str) -> Material:
    material = MATERIALS.get(material_id)
    if material is None:
        raise ValidationError(
            f"unknown material {material_id!r}",
            {"known": sorted(MATERIALS)},
        )
    return material
