"""Built-in material table. Density drives mass metrics."""

from __future__ import annotations

from dataclasses import dataclass

from .model import ValidationError


@dataclass(frozen=True)
class Material:
    id: str
    label: str
    density_g_cm3: float


MATERIALS: dict[str, Material] = {
    m.id: m
    for m in [
        Material("al6061", "Aluminum 6061", 2.70),
        Material("steel_a36", "Steel A36", 7.85),
        Material("stainless_316", "Stainless 316", 8.00),
        Material("ti6al4v", "Titanium Ti-6Al-4V", 4.43),
        Material("inconel718", "Inconel 718", 8.19),
        Material("abs", "ABS", 1.04),
        Material("pla", "PLA", 1.24),
        Material("nylon_pa12", "Nylon PA12", 1.01),
        Material("concrete", "Concrete", 2.40),
        Material("douglas_fir", "Douglas Fir", 0.53),
    ]
}

DEFAULT_MATERIAL = "al6061"


def get_material(material_id: str) -> Material:
    material = MATERIALS.get(material_id)
    if material is None:
        raise ValidationError(
            f"unknown material {material_id!r}",
            {"known": sorted(MATERIALS)},
        )
    return material
