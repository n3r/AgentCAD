"""render_view tests: software rasterizer output, tool validation, route.

The PNG decode helper relies on the encoder's contract: 8-bit truecolor,
no interlace, one zlib stream, filter 0 on every scanline (so each row is
literal bytes after the filter byte).
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

BOX = '''\
from build123d import *
PARAMS = {"a": {"default": 40.0, "min": 10.0, "max": 100.0, "unit": "mm", "description": "len"}}
def build(p):
    return Box(p.a, 30, 20)
'''

PLATE = '''\
from build123d import *
PARAMS = {"t": {"default": 2.0, "min": 0.5, "max": 10.0, "unit": "mm", "description": "thickness"}}
def build(p):
    return Box(100, 80, p.t)
'''

BROKEN = "PARAMS = {}\ndef build(p):\n    raise RuntimeError('nope')\n"

BACKGROUND = np.array([0x14, 0x17, 0x1C], np.uint8)


@pytest.fixture
def demo(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    service.create_project("demo")
    service.create_part("demo", "box", script=BOX)
    service.create_part("demo", "plate", script=PLATE)
    return service


def _decode_png(data: bytes):
    """Parse our encoder's PNG: return (width, height, HxWx3 uint8 pixels)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    off = 8
    width = height = None
    idat = b""
    while off < len(data):
        (length,) = struct.unpack_from(">I", data, off)
        tag = data[off + 4:off + 8]
        payload = data[off + 8:off + 8 + length]
        if tag == b"IHDR":
            width, height, depth, ctype = struct.unpack_from(">IIBB", payload, 0)
            assert depth == 8 and ctype == 2  # 8-bit truecolor
        elif tag == b"IDAT":
            idat += payload
        off += 12 + length
    raw = zlib.decompress(idat)
    assert len(raw) == height * (1 + width * 3)
    rows = np.frombuffer(raw, np.uint8).reshape(height, 1 + width * 3)
    assert (rows[:, 0] == 0).all()  # filter 0: scanlines are literal
    return width, height, rows[:, 1:].reshape(height, width, 3)


def _render(service, args):
    result = build_registry(service).call("render_view", args)
    assert "error" not in result, result
    return result


def _foreground(pixels) -> int:
    return int((pixels != BACKGROUND).any(axis=2).sum())


def test_box_iso_render_is_wellformed_png(demo):
    result = _render(demo, {"project": "demo", "part_id": "box"})
    assert result["view"] == "iso"
    assert result["width"] == 800 and result["height"] == 600
    png = base64.b64decode(result["png_base64"])
    width, height, pixels = _decode_png(png)
    assert (width, height) == (800, 600)
    assert pixels.astype(float).var() > 0  # not a blank image
    assert _foreground(pixels) > 1000
    out = Path(result["path"])
    assert out.is_file() and out.name == "box_iso.png"
    assert out.read_bytes() == png


def test_flat_plate_silhouette_top_vs_front(demo):
    counts = {}
    for view in ("top", "front"):
        result = _render(
            demo, {"project": "demo", "part_id": "plate", "view": view}
        )
        _w, _h, pixels = _decode_png(base64.b64decode(result["png_base64"]))
        counts[view] = _foreground(pixels)
    assert counts["front"] > 0
    assert counts["top"] > 3 * counts["front"]


def test_assembly_render_honors_transforms_and_colors(demo):
    demo.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "b", "part": "box", "position": [80, 0, 0], "color": "#cc2222"},
    ])
    assembly = _render(demo, {"project": "demo"})
    single = _render(demo, {"project": "demo", "part_id": "box"})
    assert assembly["png_base64"] != single["png_base64"]
    assert "skipped" not in assembly
    assert Path(assembly["path"]).name == "assembly_iso.png"
    _w, _h, pixels = _decode_png(base64.b64decode(assembly["png_base64"]))
    px = pixels.astype(int)
    red_dominant = (px[:, :, 0] > px[:, :, 1]) & (px[:, :, 0] > px[:, :, 2])
    assert int(red_dominant.sum()) > 100  # the red instance is visible


def test_assembly_render_skips_broken_instances(demo):
    demo.create_part("demo", "broken", script=BROKEN)
    demo.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "c", "part": "broken", "position": [50, 0, 0]},
    ])
    result = _render(demo, {"project": "demo"})
    assert result["skipped"] == ["c"]


def test_render_view_validation_errors(demo):
    registry = build_registry(demo)
    bad_view = registry.call(
        "render_view", {"project": "demo", "part_id": "box", "view": "back"}
    )
    assert bad_view["error"]["type"] == "validation_error"
    giant = registry.call(
        "render_view", {"project": "demo", "part_id": "box", "width": 999999}
    )
    assert giant["error"]["type"] == "validation_error"
    empty = registry.call("render_view", {"project": "demo"})  # no instances
    assert empty["error"]["type"] == "validation_error"


def test_render_route_returns_png_bytes(demo):
    app = create_app(
        demo, build_registry(demo), extra_allowed_hosts={"testserver"}
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post(
        "/api/projects/demo/render", json={"part_id": "box", "view": "top"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    width, height, _pixels = _decode_png(response.content)
    assert (width, height) == (800, 600)

    bad = client.post(
        "/api/projects/demo/render", json={"part_id": "box", "view": "back"}
    )
    assert bad.json()["error"]["type"] == "validation_error"
