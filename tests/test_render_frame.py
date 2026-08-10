"""render_acm(frame=...) — the explicit, superimposable camera frame.

``frame`` is a world-space bounding box whose eight corners are projected
through the camera basis and replace the per-mesh auto-fit, so two renders
of different geometry share one camera. The first test pins the *absence*
of the argument to the bytes the auto-fit produced before it existed.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from agentcad.core.model import ValidationError
from agentcad.core.render import _frame_extents, render_acm

from .test_render import _decode_png

# sha256 of render_acm output captured from the pre-frame implementation;
# frame=None must stay byte-identical to it forever.
GOLDEN_ISO = "3536af056eaacb641e7af8ddafa7521e5d519399dfc6f15aa179d2beb9e1b646"
GOLDEN_FRONT = "18b42348443248642b28d53b94548277d2be9a851b3d65cc0d78149759790358"

_FACES = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
          [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]


def cube(size, offset, color="#aabbcc"):
    """A closed axis-aligned box mesh dict (no kernel needed)."""
    lo = np.asarray(offset, dtype=float)
    hi = lo + np.asarray(size, dtype=float)
    pts = [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
           for z in (lo[2], hi[2])]
    return {"positions": pts, "normals": [[0, 0, 1]] * 8, "indices": _FACES,
            "transform": None, "color": color}


def _marker_pixels(pixels):
    """Pixel coordinates of the pure-red marker (background and the grey body
    both have non-zero green/blue; a shaded #ff0000 keeps them at 0)."""
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    ys, xs = np.nonzero((green == 0) & (blue == 0) & (red > 0))
    assert len(ys), "marker not visible in the render"
    return float(xs.mean()), float(ys.mean())


def test_frame_none_is_byte_identical_to_the_autofit():
    iso = render_acm([cube((20, 10, 5), (0, 0, 0))], view="iso",
                     width=120, height=90)
    front = render_acm(
        [cube((20, 10, 5), (0, 0, 0)), cube((4, 4, 4), (30, 0, 0))],
        view="front", width=64, height=48,
    )
    assert hashlib.sha256(iso).hexdigest() == GOLDEN_ISO
    assert hashlib.sha256(front).hexdigest() == GOLDEN_FRONT


def test_frame_extents_projects_the_eight_corners():
    # front looks from -Y with +Z up: camera x is world x, camera y is world z.
    center, span = _frame_extents({"min": [0, 0, 0], "max": [20, 10, 5]}, "front")
    assert center == pytest.approx([10.0, 2.5])
    assert span == pytest.approx([20.0, 5.0])


def test_same_frame_places_a_marker_at_the_same_pixel():
    marker = cube((3, 3, 3), (40, 0, 0), color="#ff0000")
    frame = {"min": [-5, -5, -5], "max": [60, 40, 40]}
    small = [cube((10, 10, 10), (0, 0, 0)), marker]
    large = [cube((30, 30, 30), (0, 0, 0)), marker]

    framed = [render_acm(scene, view="iso", width=200, height=150, frame=frame)
              for scene in (small, large)]
    spots = [_marker_pixels(_decode_png(png)[2]) for png in framed]
    assert spots[0] == pytest.approx(spots[1], abs=0.5)

    # ... and the auto-fit does not: the guard that this test can fail.
    auto = [render_acm(scene, view="iso", width=200, height=150)
            for scene in (small, large)]
    loose = [_marker_pixels(_decode_png(png)[2]) for png in auto]
    assert abs(loose[0][0] - loose[1][0]) > 1.0


def test_frame_smaller_than_the_geometry_clips_but_renders():
    png = render_acm([cube((100, 100, 100), (0, 0, 0))], view="iso",
                     width=64, height=48, frame={"min": [0, 0, 0], "max": [5, 5, 5]})
    width, height, _pixels = _decode_png(png)
    assert (width, height) == (64, 48)


def test_degenerate_frame_does_not_divide_by_zero():
    png = render_acm([cube((10, 10, 10), (0, 0, 0))], view="top",
                     width=32, height=32, frame={"min": [1, 1, 1], "max": [1, 1, 1]})
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("frame", [
    {"min": [0, 0, 0]},
    {"min": [0, 0], "max": [1, 1]},
    {"min": [0, 0, 0], "max": "big"},
])
def test_malformed_frame_is_a_validation_error(frame):
    with pytest.raises(ValidationError):
        render_acm([cube((10, 10, 10), (0, 0, 0))], frame=frame)
