"""Robustness toolkit + Error Doctor tests (run the real kernel geometry)."""

import pytest

from agentcad.kernel.error_doctor import diagnose, diagnose_text

from .conftest import BOX_SCRIPT


# ---- Error Doctor: signature matching (string-level, no kernel needed) ----

@pytest.mark.parametrize("message,expect_id", [
    ("Failed creating a fillet with radius of 12", "fillet_radius_too_large"),
    ("There are no suitable edges for chamfer or fillet", "fillet_edges_not_on_part"),
    ("offset Error, an alternative kind may resolve this error", "offset_failed_alternative_kind"),
    ("Face can only be created with closed wires", "face_from_open_wire"),
    ("Polyline requires two or more pts", "polyline_too_few_points"),
    ("BRep_API: command not done", "generic_occt_not_done"),
])
def test_error_doctor_matches_signatures(message, expect_id):
    entry = diagnose_text("Exception", message, "")
    assert entry is not None and entry["id"] == expect_id
    hint = diagnose("Exception", message, "")
    assert hint and "Fix:" in hint


def test_error_doctor_unknown_returns_none():
    assert diagnose("Exception", "some totally novel error xyzzy", "") is None


# ---- safe_fillet: recover from an impossible radius --------------------------

def test_safe_fillet_finds_largest_working_radius():
    from build123d import Axis, Box, BuildPart

    from agentcad.toolkit import safe_fillet

    with BuildPart() as bp:
        Box(20, 20, 10)
    part = bp.part
    edges = part.edges().filter_by(Axis.Z)
    # radius 12 > half-width (10): adjacent corner fillets collide -> OCCT fails
    filleted, achieved, warning = safe_fillet(part, edges, radius=12.0)
    assert filleted.is_valid and filleted.volume > 0
    assert 0 < achieved < 12.0
    assert warning and "largest working radius" in warning


def test_safe_fillet_success_no_warning():
    from build123d import Axis, Box, BuildPart

    from agentcad.toolkit import safe_fillet

    with BuildPart() as bp:
        Box(80, 60, 10)
    part = bp.part
    filleted, achieved, warning = safe_fillet(part, part.edges().filter_by(Axis.Z), radius=2.0)
    assert achieved == pytest.approx(2.0)
    assert warning is None


# ---- safe_shell: fallback yields valid geometry -----------------------------

def test_safe_shell_basic_box():
    from build123d import Box, BuildPart

    from agentcad.toolkit import safe_shell

    with BuildPart() as bp:
        Box(40, 40, 40)
    part = bp.part
    top = part.faces().sort_by()[-1]
    shelled, warning = safe_shell(part, thickness=2.0, opening_faces=[top])
    assert shelled.is_valid and 0 < shelled.volume < part.volume


# ---- safe_bool: fuzzy tolerance rescues a tangent-face fuse ------------------

def test_safe_bool_fuses_touching_boxes():
    from build123d import Box, Pos

    from agentcad.toolkit import safe_bool

    a = Box(10, 10, 10)
    # b sits a sub-tolerance gap away so a plain fuse leaves two solids
    b = Pos(10 + 1e-5, 0, 0) * Box(10, 10, 10)
    result, warning = safe_bool(a, b, "fuse")
    assert result.is_valid
    assert len(result.solids()) == 1  # fused into one despite the gap


# ---- integration: a kernel error now carries details.hint -------------------

def test_kernel_error_gains_hint(kernel, tmp_path):
    from agentcad.kernel.client import KernelError

    # fillet radius too large -> kernel_error, Error Doctor adds a hint
    script = (
        "from build123d import *\n"
        'PARAMS = {"r": {"default": 50.0, "min": 1.0, "max": 100.0}}\n'
        "def build(p):\n"
        "    with BuildPart() as part:\n"
        "        Box(20, 20, 4)\n"
        "        fillet(part.edges().filter_by(Axis.Z), radius=p.r)\n"
        "    return part.part\n"
    )
    with pytest.raises(KernelError) as exc_info:
        kernel.request("build", {
            "script": script, "params": {},
            "density_g_cm3": 2.7, "mesh_path": str(tmp_path / "m.acm"),
        })
    assert exc_info.value.details.get("hint")
    assert "fillet" in exc_info.value.details["hint"].lower()
