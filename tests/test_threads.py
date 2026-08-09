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
