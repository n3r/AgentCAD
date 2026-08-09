"""Software renderer: rasterize ACM meshes to a PNG (numpy + stdlib only).

No GPU and no imaging dependencies: orthographic projection, z-buffered
per-triangle rasterization with flat Lambert shading (two directional
lights), and a minimal PNG encoder (8-bit truecolor, no interlace, one
zlib IDAT with filter 0 per scanline). View directions match the drawing
pack's ``_VIEW_DIRS``: front looks from -Y with +Z up, top from +Z with
+Y up, right from +X with +Z up, iso from (1,1,1) with +Z up.

Importable from any process — no OCP/build123d.
"""

from __future__ import annotations

import math
import struct
import zlib

import numpy as np

from .model import ValidationError

VIEWS = ("iso", "front", "top", "right")
MAX_TRIANGLES = 500_000
BACKGROUND = (0x14, 0x17, 0x1C)
DEFAULT_COLOR = "#98a2ad"

# Eye direction (orthographic camera looking at the origin) and up vector.
_VIEW_EYES = {
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "iso": ((1.0, 1.0, 1.0), (0.0, 0.0, 1.0)),
}

_MARGIN = 0.05  # fraction of each viewport dimension kept clear on each side

# Lighting in camera space (x right, y up, z forward into the scene): a key
# light from the camera's up-left and a dim fill from the opposite side.
_KEY_DIR = np.array([-0.5, 0.6, -1.0])
_KEY_DIR /= np.linalg.norm(_KEY_DIR)
_FILL_DIR = -_KEY_DIR
_AMBIENT, _KEY, _FILL = 0.18, 0.72, 0.22


def _transform_positions(
    pts: np.ndarray, position, rotation_deg
) -> np.ndarray:
    """Vectorized twin of service._apply_transform: intrinsic XYZ Euler
    degrees — rotate about Z, then Y, then X, then translate."""
    rx, ry, rz = (math.radians(a) for a in rotation_deg)
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    # rotate about Z
    x, y = (
        x * math.cos(rz) - y * math.sin(rz),
        x * math.sin(rz) + y * math.cos(rz),
    )
    # rotate about Y
    x, z = (
        x * math.cos(ry) + z * math.sin(ry),
        -x * math.sin(ry) + z * math.cos(ry),
    )
    # rotate about X
    y, z = (
        y * math.cos(rx) - z * math.sin(rx),
        y * math.sin(rx) + z * math.cos(rx),
    )
    return np.stack(
        [x + position[0], y + position[1], z + position[2]], axis=1
    )


def _camera_basis(view: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    eye, up = _VIEW_EYES[view]
    forward = -np.asarray(eye, dtype=np.float64)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.asarray(up, dtype=np.float64))
    right /= np.linalg.norm(right)
    upv = np.cross(right, forward)
    return right, upv, forward


def _parse_color(color: str | None) -> np.ndarray:
    text = (color or DEFAULT_COLOR).lstrip("#")
    try:
        if len(text) != 6:
            raise ValueError
        rgb = [int(text[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        rgb = [int(DEFAULT_COLOR[i:i + 2], 16) for i in (1, 3, 5)]
    return np.asarray(rgb, dtype=np.float64)


def render_acm(
    meshes: list[dict], view: str = "iso", width: int = 800, height: int = 600
) -> bytes:
    """Render mesh dicts ({positions, normals, indices, transform, color}) to
    PNG bytes. ``transform`` is ``(position, rotation_deg)`` or None; shading
    is flat per-triangle (geometric normals), so ``normals`` is accepted but
    unused in v1."""
    if view not in VIEWS:
        raise ValidationError(f"view must be one of: {', '.join(VIEWS)}")
    if width < 1 or height < 1:
        raise ValidationError("width and height must be positive")
    right, up, forward = _camera_basis(view)

    prepared: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    total_triangles = 0
    for mesh in meshes:
        pts = np.asarray(mesh["positions"], dtype=np.float64).reshape(-1, 3)
        idx = np.asarray(mesh["indices"], dtype=np.int64).reshape(-1, 3)
        if len(pts) == 0 or len(idx) == 0:
            continue
        transform = mesh.get("transform")
        if transform is not None:
            pts = _transform_positions(pts, transform[0], transform[1])
        cam = np.stack([pts @ right, pts @ up, pts @ forward], axis=1)
        prepared.append((cam, idx, _parse_color(mesh.get("color"))))
        total_triangles += len(idx)
    if not prepared:
        raise ValidationError("nothing to render: no triangles")
    if total_triangles > MAX_TRIANGLES:
        raise ValidationError(
            f"scene has {total_triangles} triangles (limit {MAX_TRIANGLES}): "
            "render a single part_id or reduce mesh detail"
        )

    # Fit the combined projected bbox with a margin, preserving aspect.
    mins = np.min([cam[:, :2].min(axis=0) for cam, _i, _c in prepared], axis=0)
    maxs = np.max([cam[:, :2].max(axis=0) for cam, _i, _c in prepared], axis=0)
    center = (mins + maxs) / 2.0
    span = np.maximum(maxs - mins, 1e-9)
    scale = min(
        width * (1.0 - 2.0 * _MARGIN) / span[0],
        height * (1.0 - 2.0 * _MARGIN) / span[1],
    )

    img = np.empty((height, width, 3), dtype=np.uint8)
    img[:] = BACKGROUND
    zbuf = np.full((height, width), np.inf, dtype=np.float64)

    for cam, idx, base_color in prepared:
        sx = (cam[:, 0] - center[0]) * scale + width * 0.5
        sy = height * 0.5 - (cam[:, 1] - center[1]) * scale  # y-down raster
        sz = cam[:, 2]  # depth along forward: smaller = closer to the camera

        # Flat shading: geometric per-triangle normals in camera space,
        # flipped toward the camera (open/imported meshes stay lit).
        v0, v1, v2 = (cam[idx[:, k]] for k in range(3))
        normals = np.cross(v1 - v0, v2 - v0)
        lengths = np.linalg.norm(normals, axis=1)
        normals = normals / np.maximum(lengths, 1e-12)[:, None]
        away = normals[:, 2] > 0.0
        normals[away] = -normals[away]
        shade = np.clip(
            _AMBIENT
            + _KEY * np.clip(normals @ _KEY_DIR, 0.0, None)
            + _FILL * np.clip(normals @ _FILL_DIR, 0.0, None),
            0.0, 1.0,
        )
        colors = np.clip(base_color[None, :] * shade[:, None], 0.0, 255.0)
        colors = colors.astype(np.uint8)

        for t in range(len(idx)):
            i0, i1, i2 = idx[t]
            x0, x1, x2 = sx[i0], sx[i1], sx[i2]
            y0, y1, y2 = sy[i0], sy[i1], sy[i2]
            minx = max(int(math.floor(min(x0, x1, x2))), 0)
            maxx = min(int(math.ceil(max(x0, x1, x2))), width - 1)
            miny = max(int(math.floor(min(y0, y1, y2))), 0)
            maxy = min(int(math.ceil(max(y0, y1, y2))), height - 1)
            if minx > maxx or miny > maxy:
                continue
            denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(denom) < 1e-12:
                continue  # degenerate / edge-on triangle
            px = np.arange(minx, maxx + 1, dtype=np.float64) + 0.5
            py = (np.arange(miny, maxy + 1, dtype=np.float64) + 0.5)[:, None]
            w0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / denom
            w1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / denom
            w2 = 1.0 - w0 - w1
            mask = (w0 >= 0.0) & (w1 >= 0.0) & (w2 >= 0.0)
            if not mask.any():
                continue
            z = w0 * sz[i0] + w1 * sz[i1] + w2 * sz[i2]
            ztile = zbuf[miny:maxy + 1, minx:maxx + 1]
            update = mask & (z < ztile)
            if not update.any():
                continue
            ztile[update] = z[update]
            img[miny:maxy + 1, minx:maxx + 1][update] = colors[t]

    return encode_png(img)


def encode_png(rgb: np.ndarray) -> bytes:
    """Encode an HxWx3 uint8 array as a PNG: 8-bit truecolor, no interlace,
    one zlib IDAT, filter 0 on every scanline."""
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    height, width = rgb.shape[:2]
    raw = np.zeros((height, 1 + width * 3), dtype=np.uint8)
    raw[:, 1:] = rgb.reshape(height, width * 3)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join([
        b"\x89PNG\r\n\x1a\n",
        chunk(b"IHDR", ihdr),
        chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)),
        chunk(b"IEND", b""),
    ])
