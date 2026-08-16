"""Threads/fasteners toolkit tests (bd_warehouse). Kept fast: build threads
standalone rather than in-context (in-context IsoThread is ~250x slower)."""

import time

import pytest

pytestmark = pytest.mark.portability


def test_external_iso_thread_valid():
    from agentcad.toolkit.threads import external_thread

    t0 = time.perf_counter()
    thread = external_thread(8, 1.25, 8)
    dt = time.perf_counter() - t0
    assert thread.is_valid
    assert thread.volume > 0
    assert dt < 5.0  # spike: ~0.09s
    assert 3.0 < thread.min_radius < 4.0  # M8 minor radius ~3.32mm


def test_threaded_rod_is_solid():
    from agentcad.toolkit.threads import threaded_rod

    rod = threaded_rod(8, 1.25, 12)
    assert rod.is_valid
    assert rod.volume > 0
    assert len(rod.solids()) == 1


def test_tapped_hole_thread_fast_and_present():
    from build123d import (Align, Axis, Box, BuildPart, Hole, Locations, add)

    from agentcad.toolkit.threads import tapped_hole_thread

    thr = tapped_hole_thread(8, 1.25, 12)
    t0 = time.perf_counter()
    with BuildPart() as part:
        Box(40, 40, 15)
        with Locations(part.faces().sort_by(Axis.Z)[-1]):
            Hole(radius=thr.min_radius, depth=12)
        add(thr)
    dt = time.perf_counter() - t0
    # must NOT hit the ~15s ThreadedHole(simple=False) trap
    assert dt < 8.0
    assert part.part.is_valid
    # thread geometry present: many more faces than a plain drilled plate
    assert len(part.part.faces()) > 20


def test_cap_screw_simple_vs_real():
    from agentcad.toolkit.threads import cap_screw

    simple = cap_screw("M8-1.25", 20, simple=True)
    real = cap_screw("M8-1.25", 20, simple=False)
    assert simple.is_valid and real.is_valid
    # real thread has far more faces than the cosmetic version
    assert len(real.faces()) > len(simple.faces())


def test_hex_bolt_constructs():
    """PRD-011: `hex_bolt` imported `HexBolt`, which the pinned bd_warehouse
    does not export — every call raised `ImportError`, and nothing in the tree
    called it, so nothing caught it. This is the call that would have."""
    from agentcad.toolkit.threads import hex_bolt

    bolt = hex_bolt("M8-1.25", 20, simple=True)
    assert bolt.is_valid
    box = bolt.bounding_box()
    # ISO 4014 M8: across flats s = 13.0, head height k = 5.3, and the shank
    # runs to -length from the under-head bearing face at z = 0.
    assert box.size.Y == pytest.approx(13.0, abs=0.05)
    assert box.max.Z == pytest.approx(5.3, abs=0.05)
    assert box.min.Z == pytest.approx(-20.0, abs=1e-6)


def test_hex_bolt_real_thread_has_more_faces():
    from agentcad.toolkit.threads import hex_bolt

    simple = hex_bolt("M8-1.25", 20, simple=True)
    real = hex_bolt("M8-1.25", 20, simple=False)
    assert real.is_valid
    assert len(real.faces()) > len(simple.faces())
