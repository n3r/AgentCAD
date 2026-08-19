"""PRD-013 Assembly v2 — slice 4: simplified_rep proxy-mesh tier.

`simplify_rep` is a NEW build kind (a convex hull, or a coarse decimation),
NOT a coarser LOD tolerance. It is cached as an ACM sidecar `<key>.simplified.acm`
lazily produced on a `?lod=simplified` miss and served through the existing
`mesh_info(lod=)` tier mechanism unchanged. The load-bearing property: it is
DISPLAY-ONLY — mass and interference still measure the real geometry.
"""

from pathlib import Path

import pytest

from agentcad.kernel import acm
from agentcad.core.tools import build_registry

from .conftest import make_test_service

# A hollow-ish part: a box with a big bore. Its full tessellation has many
# triangles (the cylinder wall); its convex hull is a handful.
HOLLOW = '''\
from build123d import *

PARAMS = {"s": {"default": 40.0, "min": 10.0, "max": 100.0}}

def build(p):
    with BuildPart() as part:
        Box(p.s, p.s, p.s)
        Cylinder(radius=p.s / 3, height=p.s, mode=Mode.SUBTRACT)
    return part.part
'''


@pytest.fixture
def svc(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    build_registry(service)
    service.create_project("proj")
    service.create_part("proj", "block", script=HOLLOW)
    return service


def test_simplify_rep_writes_small_convex_tier(svc, tmp_path):
    """The convex-hull tier has far fewer triangles than the full mesh."""
    info = svc.mesh_info("proj", "block")
    key = info["key"]
    cache = svc.store.cache_dir("proj")
    full = acm.read(cache / f"{key}.acm")
    out = cache / f"{key}.simplified.acm"
    svc.kernel.request("simplify_rep", {
        "script": HOLLOW, "params": {"s": 40.0}, "mode": "convex",
        "mesh_path": str(out)})
    simp = acm.read(out)
    assert len(simp["indices"]) >= 4          # a real closed hull
    assert len(simp["indices"]) < len(full["indices"]) // 5


def test_simplified_tier_served_through_mesh_info(svc):
    """Lazy production: `mesh_info(lod='simplified')` produces the sidecar on a
    miss and returns it with `lod='simplified'`; a second call is cached."""
    first = svc.mesh_info("proj", "block", lod="simplified")
    assert first["lod"] == "simplified"
    assert Path(first["path"]).name.endswith(".simplified.acm")
    assert Path(first["path"]).is_file()
    mtime = Path(first["path"]).stat().st_mtime_ns
    second = svc.mesh_info("proj", "block", lod="simplified")
    assert second["lod"] == "simplified"
    # content-addressed: not rebuilt on the second read
    assert Path(second["path"]).stat().st_mtime_ns == mtime


def test_simplified_is_display_only_mass_unchanged(svc):
    """Negation guard: asking for the simplified tier must NOT change what the
    kernel measures. Mass and the full mesh's triangle count are identical
    before and after the proxy is produced."""
    mass_before = svc.get_metrics("proj", "block")["mass_g"]
    tris_before = svc.mesh_summary("proj", "block")["triangles"]
    svc.mesh_info("proj", "block", lod="simplified")   # produce the proxy
    mass_after = svc.get_metrics("proj", "block")["mass_g"]
    tris_after = svc.mesh_summary("proj", "block")["triangles"]
    assert mass_after == mass_before
    assert tris_after == tris_before
    # the full mesh path still serves the FULL mesh, untouched
    assert svc.mesh_info("proj", "block")["lod"] is None


def test_decimated_mode_also_produces_a_tier(svc):
    info = svc.mesh_info("proj", "block")
    key = info["key"]
    out = svc.store.cache_dir("proj") / f"{key}.decimated.acm"
    svc.kernel.request("simplify_rep", {
        "script": HOLLOW, "params": {"s": 40.0}, "mode": "decimated",
        "mesh_path": str(out)})
    simp = acm.read(out)
    full = acm.read(svc.store.cache_dir("proj") / f"{key}.acm")
    assert 0 < len(simp["indices"]) <= len(full["indices"])
