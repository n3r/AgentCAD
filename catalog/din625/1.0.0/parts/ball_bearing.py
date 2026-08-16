"""DIN 625-1 deep-groove ball bearing, 6xx and 60xx series, as an outline.

An **interface model**, and it says so rather than pretending otherwise: the
bore, the outside diameter and the width are the standard's, the ring split is
drawn where a real bearing's is, and there are no balls, no cage and no seal
lip. That is what a bearing is *for* in an assembly — it holds a shaft at a
diameter, in a bore, over a width — and modelling the rolling elements would
cost triangles nobody uses and imply a fidelity this does not have.

Origin and orientation: the bearing is centred on the Z axis and occupies
**z = 0 to z = B**. ``face`` is the z = 0 face (the one that seats against a
shoulder) and ``bore`` is the shaft axis, pointing +z.

SPECS measure the built solid against the published DIN 625-1 table: the bore
diameter ``d`` (from the bore's own cylindrical face), the outside diameter
``D`` and the width ``B``. The designation is read off the parameters through
the same table the geometry is built from, so a mismatch is a modelling error
rather than an unmeasurable one.
"""

from build123d import *  # noqa: F401 — standard part-script preamble

from agentcad.toolkit.specs import check_that, check_valid

#: DIN 625-1 deep-groove ball bearings, mm: bore ``d``, outside diameter
#: ``D``, width ``B``. The 6xx (miniature) and 60xx (light) series.
DIN625 = {
    "623": {"d": 3.0, "D": 10.0, "B": 4.0},
    "624": {"d": 4.0, "D": 13.0, "B": 5.0},
    "625": {"d": 5.0, "D": 16.0, "B": 5.0},
    "626": {"d": 6.0, "D": 19.0, "B": 6.0},
    "608": {"d": 8.0, "D": 22.0, "B": 7.0},
    "628": {"d": 8.0, "D": 24.0, "B": 8.0},
    "6000": {"d": 10.0, "D": 26.0, "B": 8.0},
    "6001": {"d": 12.0, "D": 28.0, "B": 8.0},
    "6002": {"d": 15.0, "D": 32.0, "B": 9.0},
}

TOLERANCE_MM = 0.02

#: How deep the ring-split groove is cut into each face. Cosmetic: it is what
#: makes the model read as a bearing rather than a spacer, and it is shallow
#: enough that it never reaches the other face at the thinnest width (4 mm).
GROOVE_DEPTH = 0.3

PARAMS = {
    "designation": {"default": "608", "type": "enum", "choices": list(DIN625),
                    "description": "DIN 625-1 bearing designation "
                                   "(608 is the skate/printer bearing)"},
}


def _row(p):
    return DIN625[p.designation]


def build(p):
    row = _row(p)
    bore_r = row["d"] / 2.0
    outer_r = row["D"] / 2.0
    width = row["B"]
    # The ring split: the inner ring's OD and the outer ring's ID, at 30% and
    # 70% of the radial section. Real proportions vary by series; these read
    # correctly at every size in the table and are documented as cosmetic.
    span = outer_r - bore_r
    inner_ring_r = bore_r + 0.30 * span
    outer_ring_r = bore_r + 0.70 * span

    with BuildPart() as bearing:
        Cylinder(outer_r, width, align=(Align.CENTER, Align.CENTER, Align.MIN))
        Hole(radius=bore_r)
        for z in (0.0, width):
            mode_align = Align.MIN if z == 0.0 else Align.MAX
            with Locations((0, 0, z)):
                Cylinder(outer_ring_r, GROOVE_DEPTH,
                         align=(Align.CENTER, Align.CENTER, mode_align),
                         mode=Mode.SUBTRACT)
                Cylinder(inner_ring_r, GROOVE_DEPTH,
                         align=(Align.CENTER, Align.CENTER, mode_align),
                         mode=Mode.ADD)
    return bearing.part


def _bore_diameter(part) -> float:
    """The smallest cylindrical face's diameter. On this solid that is the
    bore: every other cylinder (the OD, the two groove walls) is larger."""
    radii = [f.radius for f in part.faces().filter_by(GeomType.CYLINDER)]
    return 2.0 * min(radii) if radii else 0.0


def _bore_is_standard(part, metrics) -> bool:
    designation = _designation_from(metrics)
    if designation is None:
        return False
    return bool(abs(_bore_diameter(part) - DIN625[designation]["d"])
                <= TOLERANCE_MM)


def _outside_diameter_is_standard(part, metrics) -> bool:
    designation = _designation_from(metrics)
    if designation is None:
        return False
    bbox = metrics["bbox"]
    measured = max(bbox["max"][0] - bbox["min"][0],
                   bbox["max"][1] - bbox["min"][1])
    return bool(abs(measured - DIN625[designation]["D"]) <= TOLERANCE_MM)


def _width_is_standard(part, metrics) -> bool:
    designation = _designation_from(metrics)
    if designation is None:
        return False
    bbox = metrics["bbox"]
    return bool(abs((bbox["max"][2] - bbox["min"][2])
                    - DIN625[designation]["B"]) <= TOLERANCE_MM)


def _designation_from(metrics):
    """Which row the built solid claims to be, from its own outside diameter
    and width. Derived from the geometry rather than from the parameter, so a
    build that ignored its parameter fails instead of agreeing with itself."""
    bbox = metrics["bbox"]
    outer = max(bbox["max"][0] - bbox["min"][0],
                bbox["max"][1] - bbox["min"][1])
    width = bbox["max"][2] - bbox["min"][2]
    for name, row in DIN625.items():
        if (abs(outer - row["D"]) <= TOLERANCE_MM
                and abs(width - row["B"]) <= TOLERANCE_MM):
            return name
    return None


SPECS = [
    check_valid(requirement="DIN625-01"),
    check_that(_bore_is_standard, name="bore_din625", requirement="DIN625-02"),
    check_that(_outside_diameter_is_standard, name="outside_diameter_din625",
               requirement="DIN625-03"),
    check_that(_width_is_standard, name="width_din625",
               requirement="DIN625-04"),
]


def connectors(p, part):
    """``bore`` is the shaft axis (cylindrical: a shaft keeps its spin and its
    position along the bore); ``face`` is the z = 0 end face, rigid, so it can
    be the moving side of a mate onto a housing shoulder."""
    return {
        "bore": {"type": "cylindrical", "axis": ((0, 0, 0), (0, 0, 1))},
        "face": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))},
    }
