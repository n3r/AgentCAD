"""Worker handler: 2D engineering drawings (projected views + dimensions).

Projects a part to front/top/right/iso views via build123d's HLR
(project_to_viewport), renders visible (solid) and hidden (dashed) edges to a
hand-rolled SVG, and overlays an annotation layer: per-view overall
dimensions plus diameter callouts for circles detected in the top view. Also
emits DXF (visible edges) via ezdxf. Values are measured from the projected
geometry, not copied from parameters.
"""

from __future__ import annotations

import math
from collections import defaultdict

import build123d as b3d

# ---- SVG dimension primitives (sheet coords, mm, y-down) --------------------

_DIM = 'stroke="#1a56db" stroke-width="0.18" fill="none"'
_DIMFILL = 'stroke="#1a56db" stroke-width="0.18" fill="#1a56db"'
_TXT = 'font-family="Helvetica, Arial, sans-serif" font-size="3.5" fill="#1a56db"'
_VIS = 'stroke="#111" stroke-width="0.5" fill="none" stroke-linecap="round"'
_HID = 'stroke="#777" stroke-width="0.25" fill="none" stroke-dasharray="2.4 1.2"'
_ARROW_L, _ARROW_W = 3.0, 1.0


def _unit(v):
    n = math.hypot(v[0], v[1]) or 1e-9
    return (v[0] / n, v[1] / n)


def _arrow(tip, direction):
    d = _unit(direction)
    p = (-d[1], d[0])
    b1 = (tip[0] - _ARROW_L * d[0] + _ARROW_W * p[0], tip[1] - _ARROW_L * d[1] + _ARROW_W * p[1])
    b2 = (tip[0] - _ARROW_L * d[0] - _ARROW_W * p[0], tip[1] - _ARROW_L * d[1] - _ARROW_W * p[1])
    pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in (tip, b1, b2))
    return f'<polygon points="{pts}" {_DIMFILL}/>'


def _line(a, b, style=_DIM):
    return f'<line x1="{a[0]:.3f}" y1="{a[1]:.3f}" x2="{b[0]:.3f}" y2="{b[1]:.3f}" {style}/>'


def _text(pos, s, angle=0.0, anchor="middle"):
    tr = f' transform="rotate({angle:.2f} {pos[0]:.3f} {pos[1]:.3f})"' if abs(angle) > 1e-9 else ""
    return f'<text x="{pos[0]:.3f}" y="{pos[1]:.3f}" text-anchor="{anchor}" {_TXT}{tr}>{s}</text>'


def _linear_dim(pa, pb, offset, text):
    d = _unit((pb[0] - pa[0], pb[1] - pa[1]))
    n = (-d[1], d[0])
    s = 1.0 if offset >= 0 else -1.0
    off = abs(offset)
    qa = (pa[0] + s * n[0] * off, pa[1] + s * n[1] * off)
    qb = (pb[0] + s * n[0] * off, pb[1] + s * n[1] * off)
    els = []
    for p, q in ((pa, qa), (pb, qb)):
        els.append(_line((p[0] + s * n[0] * 1.5, p[1] + s * n[1] * 1.5),
                         (q[0] + s * n[0] * 2.0, q[1] + s * n[1] * 2.0)))
    els.append(_line(qa, qb))
    els.append(_arrow(qa, (-d[0], -d[1])))
    els.append(_arrow(qb, d))
    ang = math.degrees(math.atan2(d[1], d[0]))
    if ang > 90 or ang <= -90:
        ang += 180
    mid = ((qa[0] + qb[0]) / 2, (qa[1] + qb[1]) / 2)
    els.append(_text((mid[0] - s * n[0] * 1.0, mid[1] - s * n[1] * 1.0), text, angle=ang))
    return els


# ---- edge rendering --------------------------------------------------------

def _edge_svg(e, ox, oy, scale, style):
    if e.geom_type.name == "CIRCLE" and e.is_closed:
        c, r = e.arc_center, e.radius
        return (f'<circle cx="{ox + scale * c.X:.3f}" cy="{oy - scale * c.Y:.3f}" '
                f'r="{scale * r:.3f}" {style}/>')
    n = max(8, min(256, int(e.length * scale / 0.4)))
    pts = [e.position_at(i / (n - 1)) for i in range(n)]
    d = "M " + " L ".join(f"{ox + scale * p.X:.3f} {oy - scale * p.Y:.3f}" for p in pts)
    return f'<path d="{d}" {style}/>'


_VIEW_DIRS = {
    "front": dict(viewport_origin=(0, -500, 0), viewport_up=(0, 0, 1)),
    "top": dict(viewport_origin=(0, 0, 500), viewport_up=(0, 1, 0)),
    "right": dict(viewport_origin=(500, 0, 0), viewport_up=(0, 0, 1)),
    "iso": dict(viewport_origin=(500, 500, 500), viewport_up=(0, 0, 1)),
}


def _view_bounds(edges):
    xs, ys = [], []
    for e in edges:
        for i in range(6):
            p = e.position_at(i / 5)
            xs.append(p.X)
            ys.append(p.Y)
    if not xs:
        return (0, 0, 1, 1)
    return (min(xs), min(ys), max(xs), max(ys))


def _detect_circles(vis_edges):
    return [(e.arc_center, e.radius) for e in vis_edges
            if e.geom_type.name == "CIRCLE" and e.is_closed]


def _build_svg(part, views, detected_out):
    proj = {name: part.project_to_viewport(look_at=(0, 0, 0), **_VIEW_DIRS[name])
            for name in views}
    W, H = 420, 297
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" '
           f'viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="white"/>',
           f'<rect x="6" y="6" width="{W-12}" height="{H-12}" fill="none" '
           f'stroke="#111" stroke-width="0.5"/>']

    # Grid cells for up to 4 views.
    cells = {"top": (110, 90), "front": (110, 210), "right": (300, 210), "iso": (320, 90)}
    order = [v for v in ("top", "front", "right", "iso") if v in views]
    for name in order:
        vis, hid = proj[name]
        cx, cy = cells[name]
        bx0, by0, bx1, by1 = _view_bounds(list(vis) + list(hid))
        w, h = max(bx1 - bx0, 1e-3), max(by1 - by0, 1e-3)
        scale = min(150 / w, 90 / h, 2.0)
        if name == "iso":
            scale *= 0.6
        # center the view's bbox on the cell
        ox = cx - scale * (bx0 + bx1) / 2
        oy = cy + scale * (by0 + by1) / 2
        if name != "iso":
            for e in hid:
                svg.append(_edge_svg(e, ox, oy, scale, _HID))
        for e in vis:
            svg.append(_edge_svg(e, ox, oy, scale, _VIS))
        # overall dimensions on front/top (width along X, height along Y)
        if name in ("front", "top"):
            svg += _linear_dim((ox + scale * bx0, oy - scale * by0),
                               (ox + scale * bx1, oy - scale * by0),
                               offset=14, text=f"{w:.2f}")
            svg += _linear_dim((ox + scale * bx0, oy - scale * by0),
                               (ox + scale * bx0, oy - scale * by1),
                               offset=-14, text=f"{h:.2f}")
        svg.append(f'<text x="{cx}" y="{cy - 78}" font-size="4" fill="#111" '
                   f'text-anchor="middle">{name.upper()}</text>')

    # diameter callouts from top-view circles (distinct radii)
    if "top" in views:
        vis_top, _ = proj["top"]
        circles = _detect_circles(vis_top)
        by_r = defaultdict(list)
        for c, r in circles:
            by_r[round(r, 2)].append(c)
        detected_out["diameters_mm"] = sorted(round(2 * r, 2) for r in by_r)
        detected_out["hole_groups"] = [
            {"diameter_mm": round(2 * r, 2), "count": len(cs)}
            for r, cs in sorted(by_r.items()) if len(cs) >= 3
        ]

    # title block
    tb_x, tb_y, tb_w, tb_h = W - 6 - 150, H - 6 - 28, 150, 28
    svg.append(f'<rect x="{tb_x}" y="{tb_y}" width="{tb_w}" height="{tb_h}" '
               f'fill="white" stroke="#111" stroke-width="0.5"/>')
    svg.append(f'<text x="{tb_x+4}" y="{tb_y+9}" font-size="5" fill="#111">'
               f'{detected_out.get("label", "part")}</text>')
    svg.append(f'<text x="{tb_x+4}" y="{tb_y+18}" font-size="3" fill="#111">'
               f'AgentCAD · mm · third angle</text>')
    svg.append("</svg>")
    return "\n".join(svg)


def _build_dxf(part, out_path):
    import ezdxf

    doc = ezdxf.new()
    msp = doc.modelspace()
    vis, _hid = part.project_to_viewport(look_at=(0, 0, 0), **_VIEW_DIRS["top"])
    for e in vis:
        if e.geom_type.name == "CIRCLE" and e.is_closed:
            c = e.arc_center
            msp.add_circle((c.X, c.Y), e.radius)
        else:
            n = max(2, min(256, int(e.length / 0.4)))
            pts = [(p.X, p.Y) for p in (e.position_at(i / (n - 1)) for i in range(n))]
            msp.add_lwpolyline(pts)
    doc.saveas(out_path)


def register(toolbox: dict):
    build_shape = toolbox["build_shape"]
    atomic_write = toolbox["atomic_write"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def drawing(params: dict) -> dict:
        views = params.get("views") or ["top", "front", "right", "iso"]
        fmt = params.get("format", "svg")
        out_path = params["out_path"]
        shape, _values, _warnings = build_shape(params["script"], params.get("params", {}))
        detected: dict = {"label": params.get("label", "part")}
        if fmt == "svg":
            svg = _build_svg(shape, views, detected)
            atomic_write(out_path, svg.encode())
        elif fmt == "dxf":
            _build_dxf(shape, out_path)
        else:
            raise WorkerError(ERROR_CONTRACT, f"unknown drawing format {fmt!r}")
        import os
        return {"path": out_path, "size_bytes": os.path.getsize(out_path),
                "detected": detected}

    return {"drawing": drawing}
