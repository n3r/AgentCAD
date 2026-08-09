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
import os
import sys
import types

import build123d as b3d

from .drawing import _HID, _VIEW_DIRS, _VIS, _edge_svg, _line, _text, _view_bounds

# bend lines: dashed, distinct from _VIS (outline) and _HID (hidden edges)
_BEND = 'stroke="#b45309" stroke-width="0.35" fill="none" stroke-dasharray="4 1.5"'
_MARGIN = 15.0


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
    for e in vis:
        if e.geom_type.name == "CIRCLE" and e.is_closed:
            c = e.arc_center
            msp.add_circle((c.X, c.Y), e.radius, dxfattribs={"layer": "OUTLINE"})
        else:
            n = max(2, min(256, int(e.length / 0.4)))
            pts = [(p.X, p.Y) for p in (e.position_at(i / (n - 1)) for i in range(n))]
            msp.add_lwpolyline(pts, dxfattribs={"layer": "OUTLINE"})
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
