"""Worker handler: 2D engineering drawings (projected views + dimensions).

Projects a part to front/top/right/iso views via build123d's HLR
(project_to_viewport), renders visible (solid) and hidden (dashed) edges to a
hand-rolled SVG, and overlays an annotation layer: per-view overall
dimensions plus diameter callouts for circles detected in the top view. Also
emits DXF (visible edges) via ezdxf. Values are measured from the projected
geometry, not copied from parameters.

Optional ``params["pmi"]`` (the part's normalized PMI section, see
agentcad/core/pmi.py) adds a GD&T callout layer to the SVG: tolerance
suffixes on the overall/diameter dimensions, boxed datum flags with leaders,
and a column of feature control frames above the title block.
``detected["pmi_rendered"]`` counts what was actually drawn and
``detected["pmi_warnings"]`` names PMI entries that could not be placed.
DXF output ignores PMI (v1).

Hole callouts read the part's **hole records** when it has them (PRD-010): the
records ride on the built shape, this handler already builds it, so they are
read in-process — no second kernel call and no service round trip. A record
that matches a detected circle group by diameter *and* centre prints its
designation (``8× M5×0.8 - 6H ↧12``) and marks that group
``from_metadata: true``; a group with no record keeps the measured text
(``8× ⌀6.60``) and ``from_metadata: false``. The distinction is the point: a
⌀4.2 circle on a projection cannot tell a drilled hole from an M5 tap, and only
the record knows which one the author meant.

**Known limitation, inherited and deliberate: this reads the TOP VIEW only.**
``_detect_circles`` collects closed CIRCLE edges from the top projection, so a
hole on a side face has a perfect record and no callout — and a drawing with
no top view has none at all. Rather than partially patch it here, every record
that could not be drawn is named in ``detected["hole_warnings"]``; making side
views carry callouts is PRD-014's job.

Also inherited: ``_detect_circles`` only reports a *geometric* group at
``count >= 3``. A record is drawn whatever its count — a single tapped hole is
a callout — so the threshold applies to guessing, not to intent.
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


# ---- PMI callout primitives (sheet coords, mm, y-down) ---------------------

_NOTE_TXT = 'font-family="Helvetica, Arial, sans-serif" font-size="2.5" fill="#777"'
_BOX = 'fill="white" stroke="#1a56db" stroke-width="0.3"'

# Standard Unicode GD&T characteristic symbols (rendered as SVG text).
_FCF_SYMBOLS = {
    "flatness": "⏥",
    "position": "⌖",
    "perpendicularity": "⟂",
    "parallelism": "∥",
    "cylindricity": "⌭",
}


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tol_suffix(plus, minus):
    if abs(plus - minus) < 1e-9:
        return f" ±{plus:.2f}"
    return f" +{plus:.2f}/-{minus:.2f}"


def _fmt_tol(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s or "0"


def _datum_flag(letter, box_center, anchor):
    """Boxed datum letter, leader to the anchor, filled anchor triangle.

    Paint order matters: the leader runs to the box center and the white
    box rect then covers the segment inside it.
    """
    x, y = box_center
    els = [_line(anchor, (x, y)),
           _arrow(anchor, (anchor[0] - x, anchor[1] - y)),
           f'<rect x="{x - 3:.3f}" y="{y - 3:.3f}" width="6" height="6" {_BOX}/>',
           _text((x, y + 1.3), letter)]
    return els


def _fcf_frame(x, y_top, frame, h):
    """One feature control frame: [symbol][tol][datum letters...] + gray note.

    Returns the SVG elements; cell widths are fixed so positions stay
    deterministic.
    """
    tol = _fmt_tol(frame["tol_mm"])
    cells = [(8.0, _FCF_SYMBOLS[frame["type"]]),
             (max(12.0, 2.2 * len(tol) + 4.0), tol)]
    cells += [(7.0, d) for d in frame.get("datums", [])]
    els, cx = [], x
    for w, label in cells:
        els.append(f'<rect x="{cx:.3f}" y="{y_top:.3f}" width="{w:.3f}" '
                   f'height="{h:.3f}" {_BOX}/>')
        els.append(_text((cx + w / 2, y_top + h / 2 + 1.3), label))
        cx += w
    if frame.get("note"):
        els.append(f'<text x="{cx + 2:.3f}" y="{y_top + h / 2 + 1:.3f}" '
                   f'text-anchor="start" {_NOTE_TXT}>{_esc(frame["note"])}</text>')
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


# ---- hole records -> callouts ----------------------------------------------

# The diameter window a record has to fall in to claim a detected group. Same
# 0.05 mm the PMI diameter dims already use, so intent and tolerance agree
# about what "this circle" means.
_HOLE_DIA_TOL = 0.05
# Centre proximity. The top view is an orthographic projection along Z with no
# scaling, so a matching centre agrees to floating-point noise; 0.05 mm is
# already three orders of magnitude of slack, and keeping it tight is what
# stops two same-diameter groups on one part from swapping designations.
_HOLE_CENTER_TOL = 0.05

#: What a record must carry before this handler will draw it. `hole_records`
#: (the harvest handler) is where record shape is *enforced*; a consumer that
#: raises on residue would take a whole drawing down over one bad dict, so
#: here a malformed record is reported and skipped.
_RECORD_KEYS = ("id", "designation", "d", "count", "centers")


def _record_problem(record) -> str | None:
    if not isinstance(record, dict):
        return (f"hole record is a {type(record).__name__}, not a dict; it was "
                f"not produced by a toolkit.holes helper and cannot be drawn")
    missing = [key for key in _RECORD_KEYS if key not in record]
    if missing:
        return (f"hole record {record.get('id', '?')!r} is missing "
                f"{missing} and cannot be drawn")
    if not isinstance(record["centers"], list) or not record["centers"]:
        return (f"hole record {record.get('id', '?')!r} carries no centers, so "
                f"there is nowhere to point a leader")
    return None


def _top_xy(center):
    """A record's world centre in TOP-view coordinates.

    The top view looks down -Z with +Y up (``_VIEW_DIRS``), so the projection
    is the identity on X and Y — asserted by the callouts landing on their
    circles in ``tests/test_drawing_holes.py`` rather than assumed here.
    """
    return (float(center[0]), float(center[1]))


def _match_record(record, groups):
    """The detected group this record describes: ``(diameter, circles)`` or
    None.

    Both halves are required. Diameter alone would let one of two ⌀5.5 groups
    on the same plate claim the other's circles — which is exactly the kind of
    silent mislabelling a drawing must not do — so a record must also land on
    the circles' centres.
    """
    wanted = [_top_xy(c) for c in record["centers"]]
    best = None
    for dia, centers in groups:
        if abs(dia - float(record["d"])) > _HOLE_DIA_TOL:
            continue
        hit = [c for c in centers
               if any(math.dist((c.X, c.Y), w) <= _HOLE_CENTER_TOL
                      for w in wanted)]
        if hit and (best is None or len(hit) > len(best[1])):
            best = (dia, hit)
    return best


def _records_on(shape) -> list:
    """The hole records riding on the built shape — in-process, no kernel call.

    This handler already has the shape (design Decision 10), and the records
    are an attribute on it, so reading them costs one `getattr`. Whether they
    are *complete* is the harvest's question, not this one: a script that drops
    its records is warned about on the rebuild, and a drawing must not fail
    because of it.
    """
    try:
        from agentcad.toolkit import holes

        return list(holes.records(shape))
    except Exception:                                          # noqa: BLE001
        return []


def _callout_text(designation: str, count: int) -> str:
    """``8× ⌀6.6``; a lone hole is just ``⌀6.6``.

    ``1×`` is not a drafting convention — it reads as a quantity someone forgot
    to finish — so the prefix appears only where it carries information.
    """
    return f"{count}× {designation}" if count > 1 else designation


def _build_svg(part, views, detected_out, pmi=None, hole_records=()):
    proj = {name: part.project_to_viewport(look_at=(0, 0, 0), **_VIEW_DIRS[name])
            for name in views}
    W, H = 420, 297
    hole_warnings: list[str] = []

    # PMI callout state. `pmi` is the normalized section from core/pmi.py.
    pmi = pmi or {}
    pmi_dims = pmi.get("dims") or []
    pmi_datums = pmi.get("datums") or []
    pmi_fcf = pmi.get("fcf") or []
    pmi_active = bool(pmi_dims or pmi_datums or pmi_fcf)
    pmi_warnings: list[str] = []
    rendered_linear: set[str] = set()  # dim ids drawn on >= 1 rendered view
    n_dia_rendered = 0
    # First linear dim per target wins the suffix slot on that dimension.
    linear_by_target: dict = {}
    for d in pmi_dims:
        if d.get("kind") == "linear":
            linear_by_target.setdefault(d["target"], d)
    placements: dict = {}  # view name -> (ox, oy, scale, bounds)
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
        placements[name] = (ox, oy, scale, (bx0, by0, bx1, by1))
        if name != "iso":
            for e in hid:
                svg.append(_edge_svg(e, ox, oy, scale, _HID))
        for e in vis:
            svg.append(_edge_svg(e, ox, oy, scale, _VIS))
        # overall dimensions on front/top (width along X, height along Y).
        # PMI linear dims tolerance the overall extents: "width" = X in both
        # views, "height" = front-view Y (world Z), "depth" = top-view Y.
        if name in ("front", "top"):
            x_dim = linear_by_target.get("width")
            y_dim = linear_by_target.get("height" if name == "front" else "depth")
            x_text, y_text = f"{w:.2f}", f"{h:.2f}"
            if x_dim is not None:
                x_text += _tol_suffix(x_dim["plus"], x_dim["minus"])
                rendered_linear.add(x_dim["id"])
            if y_dim is not None:
                y_text += _tol_suffix(y_dim["plus"], y_dim["minus"])
                rendered_linear.add(y_dim["id"])
            svg += _linear_dim((ox + scale * bx0, oy - scale * by0),
                               (ox + scale * bx1, oy - scale * by0),
                               offset=14, text=x_text)
            svg += _linear_dim((ox + scale * bx0, oy - scale * by0),
                               (ox + scale * bx0, oy - scale * by1),
                               offset=-14, text=y_text)
        svg.append(f'<text x="{cx}" y="{cy - 78}" font-size="4" fill="#111" '
                   f'text-anchor="middle">{name.upper()}</text>')

    # diameter callouts from top-view circles (distinct radii)
    dia_dims = [d for d in pmi_dims if d.get("kind") == "diameter"]
    if "top" in views:
        vis_top, _ = proj["top"]
        circles = _detect_circles(vis_top)
        by_r = defaultdict(list)
        for c, r in circles:
            by_r[round(r, 2)].append(c)
        detected_out["diameters_mm"] = sorted(round(2 * r, 2) for r in by_r)
        # The GEOMETRIC groups, at the inherited count >= 3 threshold. A record
        # below it is added from metadata further down: the threshold is a
        # guard against calling two coincidental circles a hole pattern, and
        # intent needs no such guard.
        hole_groups = [
            {"diameter_mm": round(2 * r, 2), "count": len(cs),
             "from_metadata": False}
            for r, cs in sorted(by_r.items()) if len(cs) >= 3
        ]
        by_dia = {group["diameter_mm"]: group for group in hole_groups}
        # PMI diameter dims: attach a toleranced callout to the detected
        # circle group whose diameter is within 0.05 mm of the target.
        groups = [(round(2 * r, 2), cs) for r, cs in sorted(by_r.items())]
        pmi_drawn: set = set()      # group diameters PMI already annotated
        ox_t, oy_t, sc_t, _bounds = placements["top"]
        slot = 0

        def _leader(centers, text):
            """One callout: a leader from the column to a circle, and the text.

            The target is the extreme circle by (x, y), so two runs of the same
            part put the leader on the same hole. One implementation for all
            three kinds of callout — PMI, record and measured — because their
            only real difference is what the text says.
            """
            nonlocal slot
            tx, ty = 196.0, 40.0 + 8.0 * slot   # column right of the top view
            c = max(centers, key=lambda c: (c.X, c.Y))
            tip = (ox_t + sc_t * c.X, oy_t - sc_t * c.Y)
            tail = (tx - 1.5, ty - 1.2)
            svg.append(_line(tail, tip))
            svg.append(_arrow(tip, (tip[0] - tail[0], tip[1] - tail[1])))
            svg.append(_text((tx, ty), _esc(text), anchor="start"))
            slot += 1

        for d in dia_dims:
            best = None
            for dia, cs in groups:
                err = abs(dia - d["target"])
                if err <= 0.05 and (best is None or err < best[0]):
                    best = (err, dia, cs)
            if best is None:
                pmi_warnings.append(
                    f"pmi dim {d['id']!r}: no detected diameter within "
                    f"0.05 mm of {d['target']:g}")
                continue
            _err, dia, cs = best
            prefix = f"{len(cs)}x " if len(cs) > 1 else ""
            _leader(cs, f"{prefix}⌀{dia:.2f}"
                        f"{_tol_suffix(d['plus'], d['minus'])}")
            n_dia_rendered += 1
            pmi_drawn.add(dia)

        # Hole records: what the author asked for, printed instead of guessed.
        for record in hole_records:
            problem = _record_problem(record)
            if problem is not None:
                hole_warnings.append(problem)
                continue
            match = _match_record(record, groups)
            if match is None:
                hole_warnings.append(
                    f"hole record {record['id']!r} ({record['designation']}, "
                    f"⌀{float(record['d']):g}, {record['count']} instance(s)) "
                    f"has no matching circle in the top view, so it has no "
                    f"callout — drawings read the TOP VIEW only, and a hole on "
                    f"another face has a record and no callout (PRD-014)")
                continue
            dia, centers = match
            group = by_dia.get(dia)
            if group is None:
                # Below the detector's count >= 3 threshold: the record is the
                # authority, so the group is created from it.
                group = {"diameter_mm": dia, "count": int(record["count"]),
                         "from_metadata": False}
                hole_groups.append(group)
                by_dia[dia] = group
            if (group["from_metadata"]
                    and group.get("designation") != record["designation"]):
                hole_warnings.append(
                    f"hole records {group.get('record_id')!r} and "
                    f"{record['id']!r} both claim the ⌀{dia:g} circles with "
                    f"different designations "
                    f"({group.get('designation')!r} vs "
                    f"{record['designation']!r}); the group reports the first")
            else:
                group.update({"from_metadata": True,
                              "designation": record["designation"],
                              "family": record.get("family"),
                              "record_id": record["id"]})
            if dia not in pmi_drawn:
                _leader(centers,
                        _callout_text(record["designation"],
                                      int(record["count"])))

        # Whatever is left is a hole we can only measure: same callout shape,
        # measured text, and `from_metadata: false` says which is which.
        for group in hole_groups:
            dia = group["diameter_mm"]
            if group["from_metadata"] or dia in pmi_drawn:
                continue
            centers = next((cs for gd, cs in groups if gd == dia), None)
            if centers:
                _leader(centers, _callout_text(f"⌀{dia:.2f}", group["count"]))

        detected_out["hole_groups"] = sorted(
            hole_groups, key=lambda g: g["diameter_mm"])
        detected_out["hole_warnings"] = hole_warnings
    else:
        for d in dia_dims:
            pmi_warnings.append(
                f"pmi dim {d['id']!r}: no detected diameter for target "
                f"{d['target']:g} (top view not rendered)")
        for record in hole_records:
            problem = _record_problem(record)
            hole_warnings.append(problem if problem is not None else (
                f"hole record {record['id']!r} ({record['designation']}) has "
                f"no callout: hole callouts are detected in the top view and "
                f"this drawing does not render one"))
        detected_out["hole_warnings"] = hole_warnings

    # title block
    tb_x, tb_y, tb_w, tb_h = W - 6 - 150, H - 6 - 28, 150, 28
    svg.append(f'<rect x="{tb_x}" y="{tb_y}" width="{tb_w}" height="{tb_h}" '
               f'fill="white" stroke="#111" stroke-width="0.5"/>')
    svg.append(f'<text x="{tb_x+4}" y="{tb_y+9}" font-size="5" fill="#111">'
               f'{detected_out.get("label", "part")}</text>')
    svg.append(f'<text x="{tb_x+4}" y="{tb_y+18}" font-size="3" fill="#111">'
               f'AgentCAD · mm · third angle</text>')

    # PMI datum flags: boxed letter + leader anchored to a side of the FRONT
    # view's bbox (top/bottom/left/right); "front"/"back" anchor to the TOP
    # view's bottom/top edge. Standoff 20 clears the 14 mm dimension lines on
    # the bottom/left sides; repeats on a face shift 10 mm along the edge.
    n_datums = 0
    face_counts = defaultdict(int)
    for datum in pmi_datums:
        face = datum["face"]
        view_name = "front" if face in ("top", "bottom", "left", "right") else "top"
        if view_name not in placements:
            continue  # anchoring view not rendered — counts 0
        ox_v, oy_v, sc, (bx0, by0, bx1, by1) = placements[view_name]
        x0, x1 = ox_v + sc * bx0, ox_v + sc * bx1
        y0, y1 = oy_v - sc * by1, oy_v - sc * by0  # sheet y-down: y0 above y1
        shift = 10.0 * face_counts[face]
        face_counts[face] += 1
        if face in ("bottom", "front"):
            anchor = ((x0 + x1) / 2 + shift, y1)
            box = (anchor[0], y1 + 20.0)
        elif face in ("top", "back"):
            anchor = ((x0 + x1) / 2 + shift, y0)
            box = (anchor[0], y0 - 9.0)
        elif face == "left":
            anchor = (x0, (y0 + y1) / 2 + shift)
            box = (x0 - 20.0, anchor[1])
        else:  # right
            anchor = (x1, (y0 + y1) / 2 + shift)
            box = (x1 + 9.0, anchor[1])
        svg += _datum_flag(datum["id"], box, anchor)
        n_datums += 1

    # PMI feature control frames: a column left-aligned with the title block,
    # first frame just above it, stacking upward.
    n_fcf = 0
    fcf_h, fcf_bottom = 7.0, tb_y - 4.0
    for frame in pmi_fcf:
        fcf_top = fcf_bottom - fcf_h
        svg += _fcf_frame(tb_x, fcf_top, frame, fcf_h)
        n_fcf += 1
        fcf_bottom = fcf_top - 2.0

    if pmi_active:
        detected_out["pmi_rendered"] = {
            "dims": len(rendered_linear) + n_dia_rendered,
            "datums": n_datums,
            "fcf": n_fcf,
        }
        detected_out["pmi_warnings"] = pmi_warnings

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
            svg = _build_svg(shape, views, detected, pmi=params.get("pmi"),
                             hole_records=_records_on(shape))
            atomic_write(out_path, svg.encode())
        elif fmt == "dxf":
            _build_dxf(shape, out_path)  # DXF ignores PMI (v1)
        else:
            raise WorkerError(ERROR_CONTRACT, f"unknown drawing format {fmt!r}")
        import os
        return {"path": out_path, "size_bytes": os.path.getsize(out_path),
                "detected": detected}

    return {"drawing": drawing}
