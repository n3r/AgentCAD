"""One deterministic colour mapping for every interop writer (PRD-017 §5).

Four consumers — glTF/GLB (slice 4), 3MF and structured STEP (slice 5), USD
(slice 7) — need "what colour is this thing?" answered the same way, so the
answer lives here once:

    explicit instance/solid colour  >  the material category map  >  #98a2ad

``#98a2ad`` is the viewport's own default, so an untouched part exports the
colour the browser already shows it in. The category map is **closed** over
``materials.CATEGORIES`` (and, for metals, over
``materials.SUBCATEGORIES["metal"]``) and asserted so in the tests: a new
category in the library without a colour here would otherwise silently fall
back to neutral.

``srgb_to_linear`` lives beside the map because glTF's ``baseColorFactor`` and
USD's ``displayColor`` are **linear**, while everything we store (manifest
colours, the viewport, 3MF) is sRGB. Storing one as the other is the classic
silent-darkening bug — the same trap the importer hits from the other side
(``Quantity_Color.Values(Quantity_TOC_sRGB)``).

OCP-free by construction: this is server-process code (a probe in
``tests/test_interop_gltf.py`` proves it in a fresh interpreter).
"""

from __future__ import annotations

import re

from .materials import CATEGORIES, SUBCATEGORIES, get_material

#: What a thing with no colour and no known material is: the viewport default.
DEFAULT_COLOR = "#98a2ad"

#: Closed over ``materials.CATEGORIES``.
CATEGORY_COLORS: dict[str, str] = {
    "metal": "#b0b6bd",      # generic silver-gray; refined per subcategory
    "polymer": "#eae7e0",    # off-white
    "composite": "#3f434a",  # carbon-dark
    "wood": "#c8a06a",       # tan
    "masonry": "#9a9a94",    # gray
    "ceramic": "#efe9df",    # porcelain off-white
    "other": DEFAULT_COLOR,  # neutral
}

#: Closed over ``materials.SUBCATEGORIES["metal"]``. A silver-gray family, with
#: the two metals nobody would accept as gray (copper, and brassy zinc) given
#: their own hue — the map is meant to be recognisable in a viewer, not uniform.
METAL_SUBCATEGORY_COLORS: dict[str, str] = {
    "steel": "#8d949c",
    "stainless": "#b6bcc2",
    "tool_steel": "#767c84",
    "cast_iron": "#5f6469",
    "aluminum": "#c9ccd1",
    "titanium": "#a7a9ad",
    "copper": "#b87333",
    "nickel": "#b4b1a4",
    "magnesium": "#a9a9a0",
    "zinc": "#adb5bd",
    "other_metal": "#b0b6bd",
}

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex(value) -> str | None:
    """``"#ABC"``/``"#aabbcc"`` → ``"#aabbcc"``; anything else → ``None``.

    Never raises: a malformed author-set colour must degrade to the category
    map, not fail an export.
    """
    if not isinstance(value, str) or not _HEX_RE.match(value.strip()):
        return None
    text = value.strip().lower()
    if len(text) == 4:
        return "#" + "".join(c * 2 for c in text[1:])
    return text


def _material_id(record, solid_material=None) -> str | None:
    if isinstance(solid_material, str) and solid_material:
        return solid_material
    if record is None:
        return None
    material = (record.get("material") if isinstance(record, dict)
                else getattr(record, "material", None))
    return material if isinstance(material, str) and material else None


def category_of(material_id: str | None) -> str | None:
    """``("aluminum_6061") -> "metal"``. Unknown ids answer ``None``.

    Reads the **builtin** library layer (``materials.get_material``): a colour
    is cosmetic, and threading a project-materials layer through four writers
    to tint a project-local override differently is not worth the coupling. An
    unknown id is never an error here — it is a neutral part.
    """
    if not material_id:
        return None
    try:
        return get_material(material_id).category
    except Exception:       # ValidationError, or a library that failed to load
        return None


def subcategory_of(material_id: str | None) -> str | None:
    if not material_id:
        return None
    try:
        return get_material(material_id).subcategory
    except Exception:
        return None


def color_for(record, instance=None, solid_material=None) -> str:
    """The ``#rrggbb`` an exporter should paint this thing.

    *record* is a ``PartRecord`` (or a manifest-shaped dict); *instance* an
    ``InstanceSpec`` (or a ``get_assembly`` entry) whose author-set ``color``
    wins outright; *solid_material* the material id of one solid of a
    multi-material part (3MF/slice 5), which overrides the part's own material
    for the category lookup.
    """
    explicit = normalize_hex(
        instance.get("color") if isinstance(instance, dict)
        else getattr(instance, "color", None)
    )
    if explicit:
        return explicit
    material_id = _material_id(record, solid_material)
    category = category_of(material_id)
    if category == "metal":
        subcategory = subcategory_of(material_id)
        if subcategory in METAL_SUBCATEGORY_COLORS:
            return METAL_SUBCATEGORY_COLORS[subcategory]
    return CATEGORY_COLORS.get(category or "", DEFAULT_COLOR)


def category_for(record, solid_material=None) -> str | None:
    """The category an exporter should pick PBR constants from (metal vs not)."""
    return category_of(_material_id(record, solid_material))


def _channel(value: float) -> float:
    # The sRGB EOTF, exactly as glTF 2.0 (and USD) define it.
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def srgb_to_linear(color: str) -> tuple[float, float, float]:
    """``"#b0b6bd"`` → linear-light floats in [0, 1].

    An unparseable colour resolves to ``DEFAULT_COLOR`` rather than raising:
    the caller is halfway through writing a file.
    """
    text = normalize_hex(color) or DEFAULT_COLOR
    return tuple(  # type: ignore[return-value]
        _channel(int(text[i:i + 2], 16) / 255.0) for i in (1, 3, 5)
    )


# Import-time honesty: the maps are closed over the library's own vocabulary.
assert set(CATEGORY_COLORS) == set(CATEGORIES)
assert set(METAL_SUBCATEGORY_COLORS) == set(SUBCATEGORIES["metal"])
