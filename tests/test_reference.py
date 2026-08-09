"""Reference (imported CAD) part tests — STEP round-trip, STL mesh-only, upload."""

import pytest

from agentcad.core.model import ValidationError
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT, make_test_service


@pytest.fixture
def service(kernel, tmp_path):
    return make_test_service(tmp_path / "projects", kernel)


def _make_step(kernel, out_path):
    """Export a two-solid STEP via the kernel from a scratch script."""
    script = (
        "from build123d import *\n"
        'PARAMS = {"s": {"default": 10.0, "min": 1.0, "max": 50.0}}\n'
        "def build(p):\n"
        "    a = Solid.make_box(p.s, p.s, p.s)\n"
        "    b = Solid.make_box(p.s, p.s, p.s).moved(Location((p.s * 2, 0, 0)))\n"
        "    return Compound(children=[a, b])\n"
    )
    kernel.request("export", {"script": script, "params": {}, "format": "step",
                              "out_path": str(out_path)})


def test_step_reference_roundtrip(service, kernel, tmp_path):
    step = tmp_path / "widget.step"
    _make_step(kernel, step)

    service.create_project("demo")
    registry = build_registry(service)
    result = registry.call("import_cad_file", {
        "project": "demo", "source": str(step), "part_id": "widget",
    })
    assert "error" not in result, result
    metrics = result["part"]["metrics"]
    # two 10mm cubes = 2000 mm^3 (nested-compound volume summed correctly)
    assert metrics["volume_mm3"] == pytest.approx(2000.0, rel=1e-3)
    assert metrics["n_solids"] == 2
    assert result["imported"]["mesh_only"] is False
    # mesh renders
    assert service.ensure_mesh("demo", "widget").read_bytes()[:4] == b"ACM1"


def test_reference_usable_in_boolean(service, kernel, tmp_path):
    step = tmp_path / "widget.step"
    _make_step(kernel, step)
    service.create_project("demo")
    registry = build_registry(service)
    registry.call("import_cad_file", {"project": "demo", "source": str(step),
                                      "part_id": "widget"})
    # a scripted part places the imported widget in an assembly with a box and
    # the interference check runs against the reference part (proves booleans)
    service.create_part("demo", "stock", script=BOX_SCRIPT)
    service.set_assembly("demo", [
        {"id": "w", "part": "widget", "position": [0, 0, 0]},
        {"id": "s", "part": "stock", "position": [0, 0, 0]},  # overlaps cube a
    ])
    result = service.check_interference("demo")
    assert result["pairs"], "reference part should intersect the overlapping box"


def test_stl_reference_is_mesh_only(service, kernel, tmp_path):
    stl = tmp_path / "blob.stl"
    kernel.request("export", {"script": BOX_SCRIPT, "params": {}, "format": "stl",
                              "out_path": str(stl)})
    service.create_project("demo")
    registry = build_registry(service)
    result = registry.call("import_cad_file", {"project": "demo", "source": str(stl),
                                               "part_id": "blob"})
    assert result["imported"]["mesh_only"] is True
    assert result["part"]["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=0.02)


def test_stl_reference_excluded_from_interference_not_crash(service, kernel, tmp_path):
    stl = tmp_path / "blob.stl"
    kernel.request("export", {"script": BOX_SCRIPT, "params": {}, "format": "stl",
                              "out_path": str(stl)})
    service.create_project("demo")
    registry = build_registry(service)
    registry.call("import_cad_file", {"project": "demo", "source": str(stl),
                                      "part_id": "blob"})
    service.create_part("demo", "stock", script=BOX_SCRIPT)
    service.set_assembly("demo", [
        {"id": "b", "part": "blob", "position": [0, 0, 0]},
        {"id": "s", "part": "stock", "position": [0, 0, 0]},
    ])
    result = service.check_interference("demo")  # must NOT segfault
    assert "b" in (result.get("skipped_mesh") or [])


def test_upload_rejects_bad_extension_and_traversal(service):
    from agentcad.core.imports import safe_import_name

    with pytest.raises(ValidationError):
        safe_import_name("notcad.txt")  # unsupported extension
    with pytest.raises(ValidationError):
        safe_import_name("../../etc/passwd")  # traversal, no supported ext
    # a path is safely reduced to its basename (cannot escape imports/)
    assert safe_import_name("a/b.step") == "b.step"
    assert safe_import_name("part.STEP") == "part.STEP"
