"""Worker handler: sheet-metal flat pattern export (SVG/DXF).

Runs the part script and calls its optional contract function
``flat_pattern(p)``, which returns either a flat Part or ``(part, bend_lines)``
where bend_lines is the list-of-dicts shape from SheetPart.bend_lines().
The flat part's top view is projected via HLR (same call as the drawing
handler; the top view is an exact identity on model XY, verified empirically)
so bend lines given in flat/model coordinates overlay the outline directly.
SVG gets the outline in the visible style, dashed bend lines, a label and
overall W x H; DXF gets OUTLINE and BEND layers.
"""

from __future__ import annotations

import contextlib
import math
import os
import sys
import types

import build123d as b3d

# Only the geometry helpers are shared with the drawing handler; the flat
# pattern renders its own SVG strings (its own style set, HTML-entity text) and
# keeps its local copies of the edge/line/text emitters so it is independent of
# the drawing handler's display-list refactor (PRD-014).
from .drawing import _VIEW_DIRS, _arc_angles, _view_bounds

# Style strings, local to the flat pattern (the drawing handler moved these into
# its SVG backend). Bend lines: dashed amber, distinct from the outline/hidden.
_VIS = 'stroke="#111" stroke-width="0.5" fill="none" stroke-linecap="round"'
_HID = 'stroke="#777" stroke-width="0.25" fill="none" stroke-dasharray="2.4 1.2"'
_TXT = 'font-family="Helvetica, Arial, sans-serif" font-size="3.5" fill="#1a56db"'
_BEND = 'stroke="#b45309" stroke-width="0.35" fill="none" stroke-dasharray="4 1.5"'
_MARGIN = 15.0


def _edge_svg(e, ox, oy, scale, style):
    """One projected flat-pattern edge as an SVG element string.

    The same branches as the drawing handler's `_edge_prim` — a LINE is two
    points, an open circular edge is a real arc — but emitted as strings with
    this module's own local `:.3f` contract rather than through the display
    list's `fmt`. A relief cut is an arc and stays a `<path>` element (an `A`
    segment), so the outline is still "paths plus circles" for any caller
    counting them.
    """
    gt = e.geom_type.name
    if gt == "LINE":
        a, b = e.position_at(0.0), e.position_at(1.0)
        d = (f"M {ox + scale * a.X:.3f} {oy - scale * a.Y:.3f} "
             f"L {ox + scale * b.X:.3f} {oy - scale * b.Y:.3f}")
        return f'<path d="{d}" {style}/>'
    if gt == "CIRCLE":
        c, r = e.arc_center, e.radius
        cx, cy, cr = ox + scale * c.X, oy - scale * c.Y, scale * r
        start, end = (None, None) if e.is_closed else _arc_angles(e)
        if start is None:
            return (f'<circle cx="{cx:.3f}" cy="{cy:.3f}" '
                    f'r="{cr:.3f}" {style}/>')
        # Sheet-plane (y-down) angles swept in the increasing direction, which
        # is exactly SVG's sweep-flag 1.
        x0, y0 = (cx + cr * math.cos(math.radians(start)),
                  cy + cr * math.sin(math.radians(start)))
        x1, y1 = (cx + cr * math.cos(math.radians(end)),
                  cy + cr * math.sin(math.radians(end)))
        large = 1 if (end - start) % 360.0 > 180.0 else 0
        d = (f"M {x0:.3f} {y0:.3f} A {cr:.3f} {cr:.3f} 0 {large} 1 "
             f"{x1:.3f} {y1:.3f}")
        return f'<path d="{d}" {style}/>'
    n = max(8, min(256, int(e.length * scale / 0.4)))
    pts = [e.position_at(i / (n - 1)) for i in range(n)]
    d = "M " + " L ".join(f"{ox + scale * p.X:.3f} {oy - scale * p.Y:.3f}" for p in pts)
    return f'<path d="{d}" {style}/>'


def _line(a, b, style):
    return f'<line x1="{a[0]:.3f}" y1="{a[1]:.3f}" x2="{b[0]:.3f}" y2="{b[1]:.3f}" {style}/>'


def _text(pos, s, angle=0.0, anchor="middle"):
    tr = f' transform="rotate({angle:.2f} {pos[0]:.3f} {pos[1]:.3f})"' if abs(angle) > 1e-9 else ""
    return f'<text x="{pos[0]:.3f}" y="{pos[1]:.3f}" text-anchor="{anchor}" {_TXT}{tr}>{s}</text>'


def _normalize_bend_lines(bends, WorkerError, ERROR_CONTRACT) -> list[dict]:
    if not isinstance(bends, list):
        raise WorkerError(ERROR_CONTRACT,
                          "flat_pattern(p) bend lines must be a list of dicts")
    out = []
    for entry in bends:
        try:
            ax, ay = entry["a"]
            bx, by = entry["b"]
            out.append({
                "edge": str(entry.get("edge", "?")),
                "a": (float(ax), float(ay)),
                "b": (float(bx), float(by)),
                "angle_deg": float(entry.get("angle_deg", 90.0)),
                "inner_radius": float(entry.get("inner_radius", 0.0)),
            })
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkerError(
                ERROR_CONTRACT,
                "each bend line must be a dict with 'a' and 'b' (x, y) points "
                "(see SheetPart.bend_lines())",
            ) from exc
    return out


def _svg_flat(vis, hid, bends, bounds, label: str) -> str:
    bx0, by0, bx1, by1 = bounds
    w, h = max(bx1 - bx0, 1e-3), max(by1 - by0, 1e-3)
    W, H = w + 2 * _MARGIN, h + 2 * _MARGIN + 8  # extra rows for the text
    ox, oy = _MARGIN - bx0, _MARGIN + by1  # sx = ox + x, sy = oy - y (1:1 mm)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.2f}mm" '
           f'height="{H:.2f}mm" viewBox="0 0 {W:.2f} {H:.2f}">',
           f'<rect x="0" y="0" width="{W:.2f}" height="{H:.2f}" fill="white"/>']
    for e in hid:
        svg.append(_edge_svg(e, ox, oy, 1.0, _HID))
    for e in vis:
        svg.append(_edge_svg(e, ox, oy, 1.0, _VIS))
    svg.append('<g id="BEND">')
    for bl in bends:
        svg.append(_line((ox + bl["a"][0], oy - bl["a"][1]),
                         (ox + bl["b"][0], oy - bl["b"][1]), _BEND))
        mid = ((bl["a"][0] + bl["b"][0]) / 2, (bl["a"][1] + bl["b"][1]) / 2)
        svg.append(_text((ox + mid[0], oy - mid[1] - 1.2),
                         f'{bl["angle_deg"]:g}&#176; R{bl["inner_radius"]:g}'))
    svg.append('</g>')
    svg.append(_text((ox + (bx0 + bx1) / 2, H - 8),
                     f'{label} &#183; flat pattern'))
    svg.append(_text((ox + (bx0 + bx1) / 2, H - 3.5),
                     f'{w:.2f} &#215; {h:.2f} mm'))
    svg.append('</svg>')
    return "\n".join(svg)


def _dxf_flat(vis, bends, out_path: str) -> None:
    import ezdxf

    doc = ezdxf.new()
    doc.layers.add("OUTLINE")
    doc.layers.add("BEND", color=1)
    msp = doc.modelspace()
    attrs = {"layer": "OUTLINE"}
    for e in vis:
        # Native entities, mirroring `drawing._build_dxf`: the flat pattern is
        # what goes to the laser, and a LINE/ARC is the shape itself rather than
        # a 256-segment approximation of it. Model-plane angles (no sheet
        # y-flip), CCW start -> end, which is ezdxf's ARC contract.
        gt = e.geom_type.name
        if gt == "LINE":
            a, b = e.position_at(0.0), e.position_at(1.0)
            msp.add_line((a.X, a.Y), (b.X, b.Y), dxfattribs=attrs)
        elif gt == "CIRCLE":
            c = e.arc_center
            start, end = ((None, None) if e.is_closed
                          else _arc_angles(e, y_down=False))
            if start is None:
                msp.add_circle((c.X, c.Y), e.radius, dxfattribs=attrs)
            else:
                msp.add_arc((c.X, c.Y), e.radius, start, end, dxfattribs=attrs)
        else:
            n = max(2, min(256, int(e.length / 0.4)))
            pts = [(p.X, p.Y) for p in (e.position_at(i / (n - 1)) for i in range(n))]
            msp.add_lwpolyline(pts, dxfattribs=attrs)
    for bl in bends:
        msp.add_line(bl["a"], bl["b"], dxfattribs={"layer": "BEND"})
    doc.saveas(out_path)


def register(toolbox: dict) -> dict:
    build_shape_ns = toolbox["build_shape_ns"]
    atomic_write = toolbox["atomic_write"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def flat_pattern(params: dict) -> dict:
        fmt = params.get("format", "svg")
        out_path = params["out_path"]
        _shape, values, _warnings, ns = build_shape_ns(
            params["script"], params.get("params", {}))
        fn = ns.get("flat_pattern")
        if not callable(fn):
            raise WorkerError(ERROR_CONTRACT, "script does not define flat_pattern(p)")
        with contextlib.redirect_stdout(sys.stderr):
            result = fn(types.SimpleNamespace(**values))
        bends_raw = []
        if isinstance(result, tuple):
            if len(result) != 2:
                raise WorkerError(ERROR_CONTRACT,
                                  "flat_pattern(p) must return a part or (part, bend_lines)")
            flat, bends_raw = result
        else:
            flat = result
        if not isinstance(flat, (b3d.Part, b3d.Solid, b3d.Compound)):
            raise WorkerError(
                ERROR_CONTRACT,
                "flat_pattern(p) must return a build123d Part, Solid, or Compound "
                f"(got {type(flat).__name__})")
        bends = _normalize_bend_lines(bends_raw, WorkerError, ERROR_CONTRACT)

        vis, hid = flat.project_to_viewport(look_at=(0, 0, 0), **_VIEW_DIRS["top"])
        bounds = _view_bounds(list(vis) + list(hid))
        label = params.get("label", "part")
        if fmt == "svg":
            atomic_write(out_path, _svg_flat(vis, hid, bends, bounds, label).encode())
        elif fmt == "dxf":
            _dxf_flat(vis, bends, out_path)
        else:
            raise WorkerError(ERROR_CONTRACT, f"unknown flat pattern format {fmt!r}")
        return {
            "path": out_path,
            "size_bytes": os.path.getsize(out_path),
            "flat_bbox_mm": {"w": bounds[2] - bounds[0], "h": bounds[3] - bounds[1]},
            "n_bend_lines": len(bends),
        }

    return {"flat_pattern": flat_pattern}
