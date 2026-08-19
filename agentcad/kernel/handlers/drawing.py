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

Optional ``params["dim_table"]`` (PRD-012) adds a boxed **dimension table** in
the sheet's clear right column: one row per configuration, its configured
parameters, and the overall X/Y/Z extents of that configuration's own built
shape. The rows are built and measured *here* — the module's contract is that
a drawing prints what the geometry is, and a table of parameters the caller
already had would assert nothing. A member that will not build is one row of
em dashes with its error, never the loss of the sheet.
``detected["dim_table"]`` echoes the whole thing structurally. DXF ignores it.

Hole callouts read the part's **hole records** when it has them (PRD-010): the
records ride on the built shape, this handler already builds it, so they are
read in-process — no second kernel call and no service round trip. A record
that matches a detected circle group by diameter *and* centre prints its
designation (``8× M5×0.8 - 6H ↧12``) and marks that group
``from_metadata: true``; a group with no record keeps the measured text
(``8× ⌀6.60``) and ``from_metadata: false``. The distinction is the point: a
⌀4.2 circle on a projection cannot tell a drilled hole from an M5 tap, and only
the record knows which one the author meant.

**A record is intent; a drawing is a measurement, and where they disagree the
drawing wins.** ``holes.carry()`` moves records across later operations without
re-verifying them, by design, so this handler re-measures **four specific
things** before asserting them — not "everything", and the difference is the
list:

* the printed **count** is the circles actually matched, never the record's;
* a record whose **designation is not what its own fields spell** is skipped
  with a warning (``hole_standards.validate_record``, the same contract the
  harvest raises on and the sidecar discards on — this used to be a five-field
  spot-check, so a plausible dict `setattr`-ed onto the shape printed a
  fabricated callout);
* a blind record's **bottom**: one point classified just past the recorded
  depth. Gone ⇒ the callout drops the depth and the recorded number travels in
  ``hole_warnings``, where it cannot be read as a dimension;
* a counterbore's or countersink's **seat**: four points around its outer
  radius at its own mid-depth, plus one inside it. Nothing in material at any
  azimuth, or the seat's own space no longer empty ⇒ the callout drops the
  seat. Both degradations are spelled by ``designation_for_record`` from a
  modified copy of the record, so a degraded callout uses the same grammar as
  an honest one.

**What is NOT re-measured, and must not be read as if it were.** Each field is
exactly what it is named and nothing more. ``bottom_present`` catches a hole
made deeper and not one made SHALLOWER, because milling the part's top down
leaves the bottom precisely where the record says it is — measured, a 6 mm
blind M8 on a 12 mm plate with 3 mm taken off the top prints ``↧6`` over a 3 mm
hole, ``bottom_present: true``, byte-identical to the control.
``seat_present`` is **"nothing surrounds it at any of four azimuths at its
mid-depth, or its space is not empty"** — that sentence and no wider one. It
therefore catches a seat region milled off completely and a pocket filled back
in, and it does **not** catch a seat milled off that leaves anything at one
azimuth (a 2×2 mm pin reads ``true``), a slot cut across it leaving 0.25 mm
crescents (reads ``true``), or a diameter/depth/angle that changed. That is the
`any` bias, and it is measured rather than assumed: a bounding-box-filtered
``all`` catches the pin and the slot, keeps both edge cases — and reads
``false`` on a CORRECT counterbore beside an ordinary pocket, which is
degrading a true drawing on a routine layout. The recorded **diameter** is not
re-measured against the circle it matched beyond ``_HOLE_DIA_TOL``, and nothing
on a face other than the top is measured at all. Measuring a hole's true depth
means finding where its wall begins, which is a ray cast into a projection this
handler does not build; guessing it would be worse than the gap.

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

from ._draw_primitives import (
    Circle,
    Hatch,
    Line,
    Polyline,
    Rect,
    Style,
    SvgBackend,
    Text,
    fmt,
)
from ._pdf import PdfBackend
from ._sheets import DEFAULT_SHEET, SCALE_LADDER, SHEETS, scale_label

# ---- dimension / callout geometry (sheet coords, mm, y-down) ----------------
#
# The composition below builds an ordered **display list** of the typed
# primitives from ``_draw_primitives`` and hands it to a backend
# (``SvgBackend`` or, since Slice 2, ``PdfBackend``). Every function here
# returns lists of primitives, never SVG strings — the byte-locked ``_text``
# helper the dimension table used is gone: the table is typed primitives too
# (Slice 2), so BOTH backends render it from the one list.

_ARROW_L, _ARROW_W = 3.0, 1.0


def _unit(v):
    n = math.hypot(v[0], v[1]) or 1e-9
    return (v[0] / n, v[1] / n)


def _arrow(tip, direction):
    """A filled dimension arrow head as a closed, filled Polyline primitive."""
    d = _unit(direction)
    p = (-d[1], d[0])
    b1 = (tip[0] - _ARROW_L * d[0] + _ARROW_W * p[0], tip[1] - _ARROW_L * d[1] + _ARROW_W * p[1])
    b2 = (tip[0] - _ARROW_L * d[0] - _ARROW_W * p[0], tip[1] - _ARROW_L * d[1] - _ARROW_W * p[1])
    return Polyline((tip, b1, b2), style=Style.DIM, closed=True, fill=True)


def _linear_dim(pa, pb, offset, text):
    """A linear dimension (extension lines, dimension line, two arrows, text)
    as a list of primitives."""
    d = _unit((pb[0] - pa[0], pb[1] - pa[1]))
    n = (-d[1], d[0])
    s = 1.0 if offset >= 0 else -1.0
    off = abs(offset)
    qa = (pa[0] + s * n[0] * off, pa[1] + s * n[1] * off)
    qb = (pb[0] + s * n[0] * off, pb[1] + s * n[1] * off)
    els = []
    for p, q in ((pa, qa), (pb, qb)):
        a = (p[0] + s * n[0] * 1.5, p[1] + s * n[1] * 1.5)
        b = (q[0] + s * n[0] * 2.0, q[1] + s * n[1] * 2.0)
        els.append(Line(a[0], a[1], b[0], b[1], Style.DIM))
    els.append(Line(qa[0], qa[1], qb[0], qb[1], Style.DIM))
    els.append(_arrow(qa, (-d[0], -d[1])))
    els.append(_arrow(qb, d))
    ang = math.degrees(math.atan2(d[1], d[0]))
    if ang > 90 or ang <= -90:
        ang += 180
    mid = ((qa[0] + qb[0]) / 2, (qa[1] + qb[1]) / 2)
    els.append(Text(mid[0] - s * n[0] * 1.0, mid[1] - s * n[1] * 1.0, text,
                    Style.DIM, size=3.5, angle=ang))
    return els


# ---- PMI callout primitives (sheet coords, mm, y-down) ---------------------

# Standard Unicode GD&T characteristic symbols (rendered as text).
_FCF_SYMBOLS = {
    "flatness": "⏥",
    "position": "⌖",
    "perpendicularity": "⟂",
    "parallelism": "∥",
    "cylindricity": "⌭",
}


# ---- dimension table (PRD-012) --------------------------------------------

#: The sheet's one clear rectangle is (264,18)-(414,60): the right column above
#: the ISO view, below its label. 150 mm wide, 42 mm tall.
_TABLE_X, _TABLE_Y, _TABLE_W, _TABLE_ROW_H = 264.0, 18.0, 150.0, 4.5

#: Rows beyond this are dropped with a warning rather than drawn off the sheet:
#: nine rows at 4.5 mm plus the header is already 45 mm in a 42 mm rectangle.
_MAX_TABLE_ROWS = 8


def _cell(value) -> str:
    """One table cell. ``None`` is an em dash, not an empty box: a blank cell
    reads as a value someone forgot to fill in."""
    if value is None:
        return "—"
    if isinstance(value, bool):          # before int: bool IS an int
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _measure_table(build_shape, script, table):
    """One measured row per configuration: ``{columns, rows, warnings}``.

    **Every number here comes from a built shape**, which is this module's
    contract and the reason the table is worth drawing at all — a row of
    parameters could have been printed by the caller. ``X``/``Y``/``Z`` are the
    overall extents of the WORLD bounding box (the same quantity the front and
    top views' overall dimensions print, which are that bbox projected).

    A configuration that will not build is ``ok: false`` with its error: the
    row prints em dashes and the rest of the table is still drawn. One member
    of a family being broken is exactly when the others are worth seeing.

    ``values`` carries the **resolved** parameter map ``build_shape`` returned,
    not the override map the request sent. A family is routinely ragged — one
    member overrides three parameters, another only one — and echoing the
    overrides would print an em dash where the geometry has the script's
    default, and an un-canonicalized enum where the build canonicalized it.
    The table would then disagree with the shape it just measured, which is the
    one thing it exists not to do. Extra resolved keys are inert: the renderer
    prints only the requested ``columns``.
    """
    columns = [name for name in (table.get("columns") or [])
               if isinstance(name, str)]
    requested = list(table.get("rows") or [])
    warnings: list[str] = []
    if len(requested) > _MAX_TABLE_ROWS:
        warnings.append(
            f"{len(requested)} configurations were requested; the dimension "
            f"table prints the first {_MAX_TABLE_ROWS} (the sheet's table "
            f"rectangle holds no more)")
        requested = requested[:_MAX_TABLE_ROWS]
    rows = []
    for entry in requested:
        name = entry.get("config")
        params = entry.get("params") or {}
        # `str(...)`: a hand-edited or merged manifest can put anything in
        # `label`, and a non-string would TypeError the backend's escape.
        row = {"config": name, "label": str(entry.get("label") or name),
               "values": {}, "ok": True}
        try:
            shape, values, _warnings = build_shape(script, params)
            size = shape.bounding_box().size
            row["values"] = {**values,
                             "X": round(size.X, 3),
                             "Y": round(size.Y, 3),
                             "Z": round(size.Z, 3)}
        except Exception as exc:  # noqa: BLE001 — one broken member must not
            # take the sheet with it; the row carries the reason instead.
            row["ok"] = False
            row["error"] = getattr(exc, "message", None) or str(exc)
            warnings.append(f"configuration {name!r} did not build, so its row "
                            f"prints no values: {row['error']}")
        rows.append(row)
    return {"columns": columns, "rows": rows, "warnings": warnings}


def _row_label(row) -> str:
    """The first cell: the configuration NAME, and the label beside it.

    The name is the identity every other surface uses (the manifest key, the
    ``part@config`` CI subject, ``?config=``), so a sheet that printed only
    ``Small`` could not be traced back to ``s``. A label that adds nothing —
    absent, or equal to the name — is not repeated.
    """
    config = row.get("config") or ""
    label = row.get("label")
    if label and label != config:
        return f"{label} ({config})"
    return config


def _dim_table(rows, columns, x=_TABLE_X, y_top=_TABLE_Y, row_h=_TABLE_ROW_H):
    """``(elements, dropped, warnings)`` — the boxed table as **primitives**.

    Header (``config``, the configured parameters, ``X``/``Y``/``Z``) then one
    row per configuration. Column widths follow ``_fcf_frame``'s rule
    (``max(14, 2.2·len + 4)``) so the numbers stay deterministic, and the whole
    table has to fit the sheet's 150 mm column: trailing PARAMETER columns are
    dropped, with a warning naming each, until it does. ``config`` and the
    measured extents are never dropped — they are what the table is for, and
    *dropped* names what came off so a caller can say why a column is missing.

    Slice 2 (PRD-014): this returns typed ``Rect``/``Text`` primitives, not SVG
    strings, so BOTH backends render the table from one display list (the SVG
    backend escapes ``&``/``<``/``>`` on the way out, so the raw label text is
    handed through unescaped here — pre-escaping would double it).
    """
    warnings: list[str] = []
    columns = list(columns)
    dropped: list[str] = []
    while True:
        lines = [["config", *columns, "X", "Y", "Z"]]
        for row in rows:
            label = _row_label(row)
            if not row.get("ok", True):
                # A row that did not build prints em dashes: there is no
                # measurement, and an empty cell would read as one.
                lines.append([label] + ["—"] * (len(columns) + 3))
                continue
            values = row.get("values") or {}
            lines.append([label]
                         + [_cell(values.get(name)) for name in columns]
                         + [_cell(values.get(axis)) for axis in ("X", "Y", "Z")])
        widths = [max(14.0, 2.2 * max(len(line[i]) for line in lines) + 4.0)
                  for i in range(len(lines[0]))]
        if sum(widths) <= _TABLE_W or not columns:
            break
        cut = columns.pop()
        dropped.append(cut)
        warnings.append(
            f"column {cut!r} was dropped from the dimension table: "
            f"the table is wider than the sheet's {_TABLE_W:g} mm column")
    if sum(widths) > _TABLE_W:
        warnings.append(
            f"the dimension table is {sum(widths):.0f} mm wide and overflows "
            f"the sheet's {_TABLE_W:g} mm column (the configuration labels "
            f"alone do not fit)")
    els, y = [], y_top
    for line in lines:
        cx = x
        for width, text in zip(widths, line):
            els.append(Rect(cx, y, width, row_h, style=Style.DIM, fill="white"))
            els.append(Text(cx + width / 2, y + row_h / 2 + 1.0, text,
                            Style.DIM, anchor="middle", size=3.5))
            cx += width
        y += row_h
    return els, dropped, warnings


def _tol_suffix(plus, minus):
    if abs(plus - minus) < 1e-9:
        return f" ±{plus:.2f}"
    return f" +{plus:.2f}/-{minus:.2f}"


def _fmt_tol(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s or "0"


def _datum_flag(letter, box_center, anchor):
    """Boxed datum letter, leader to the anchor, filled anchor triangle — as a
    list of primitives.

    Paint order matters: the leader runs to the box center and the white
    box rect then covers the segment inside it.
    """
    x, y = box_center
    return [
        Line(anchor[0], anchor[1], x, y, Style.DIM),
        _arrow(anchor, (anchor[0] - x, anchor[1] - y)),
        Rect(x - 3, y - 3, 6, 6, style=Style.DIM, fill="white"),
        Text(x, y + 1.3, letter, Style.DIM, size=3.5),
    ]


def _fcf_frame(x, y_top, frame, h):
    """One feature control frame: [symbol][tol][datum letters...] + gray note.

    Returns a list of primitives; cell widths are fixed so positions stay
    deterministic.
    """
    tol = _fmt_tol(frame["tol_mm"])
    cells = [(8.0, _FCF_SYMBOLS[frame["type"]]),
             (max(12.0, 2.2 * len(tol) + 4.0), tol)]
    cells += [(7.0, d) for d in frame.get("datums", [])]
    els, cx = [], x
    for w, label in cells:
        els.append(Rect(cx, y_top, w, h, style=Style.DIM, fill="white"))
        els.append(Text(cx + w / 2, y_top + h / 2 + 1.3, label, Style.DIM,
                        size=3.5))
        cx += w
    if frame.get("note"):
        els.append(Text(cx + 2, y_top + h / 2 + 1, frame["note"], Style.NOTE,
                        anchor="start", size=2.5))
    return els


# ---- edge rendering --------------------------------------------------------

def _edge_prim(e, ox, oy, scale, style):
    """One projected edge as a primitive: a ``Circle`` for a closed circle,
    else a sampled ``Polyline``. Sheet coords are y-down (``oy - scale*Y``)."""
    if e.geom_type.name == "CIRCLE" and e.is_closed:
        c, r = e.arc_center, e.radius
        return Circle(ox + scale * c.X, oy - scale * c.Y, scale * r, style)
    n = max(8, min(256, int(e.length * scale / 0.4)))
    pts = tuple((ox + scale * p.X, oy - scale * p.Y)
                for p in (e.position_at(i / (n - 1)) for i in range(n)))
    return Polyline(pts, style)


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


# ---- section views (FR6) ---------------------------------------------------
#
# The section geometry is cut HERE, in the worker that already holds the built
# shape (Decision 10) — no second kernel round-trip and `affinity=part_id` is
# trivially satisfied. `analysis._section` computes only area/n_faces and throws
# the geometry away; this keeps it. Every 2D coordinate is a plane-LOCAL
# coordinate (`Plane.to_local_coords`), so the loops are already the section's
# own 2D frame, and every wire is traced in connectivity order
# (`wire.order_edges()`) with a fixed sampling density and then the whole result
# is sorted by a geometric key — nothing depends on OCCT's internal iteration
# order, which is the FR12 determinism requirement.

#: The three orthogonal section planes, keyed by the lowercase spec name. A
#: positive `offset_mm` moves the cut along the plane's own normal
#: (`Plane.offset`): +Z for xy, -Y for xz, +X for yz.
_SECTION_PLANES = {"xy": "XY", "xz": "XZ", "yz": "YZ"}

#: The standard view each cut is seen edge-on in, so the cutting-plane line and
#: arrows land on a view that actually shows where A-A is taken. Deterministic;
#: falls back to the first rendered view when the preferred one is absent.
_SECTION_PARENT = {"xy": "front", "xz": "top", "yz": "front"}


def _letter(i: int) -> str:
    """0->'A', 1->'B', ... 25->'Z', 26->'AA' — the section/detail label run."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _wire_loop_2d(wire, plane) -> tuple:
    """One section wire as a closed ring of plane-local (x, y) points.

    Traced in connectivity order via ``order_edges`` (a line contributes its
    start vertex; a curved edge is sampled at a fixed density) so the ring never
    self-intersects and never depends on OCCT vertex order. The final point is
    dropped — the ring is closed by the consumer — so no vertex is duplicated.
    """
    pts: list = []
    for edge in wire.order_edges():
        if edge.geom_type.name == "LINE":
            p = plane.to_local_coords(edge.position_at(0.0))
            pts.append((float(p.X), float(p.Y)))
        else:
            n = max(2, min(256, int(edge.length / 0.4)))
            for i in range(n):
                p = plane.to_local_coords(edge.position_at(i / n))
                pts.append((float(p.X), float(p.Y)))
    return tuple(pts)


def _loop_key(loop) -> tuple:
    """A stable geometric sort key for a loop (min-x, min-y, size, count)."""
    if not loop:
        return (0.0, 0.0, 0.0, 0.0, 0)
    xs = [x for x, _ in loop]
    ys = [y for _, y in loop]
    return (round(min(xs), 6), round(min(ys), 6),
            round(max(xs), 6), round(max(ys), 6), len(loop))


def _section_bodies(part, plane_name: str, offset_mm: float) -> list:
    """Cut `part` at the plane+offset; per-solid closed 2D loops, or ``[]``.

    Each solid body is sectioned **separately** (the design's per-body rule, so a
    multi-solid part hatches per body) and every section face contributes its
    outer wire plus any inner wires (a bore leaves an even-odd hole). Bodies with
    no intersection are dropped; a completely-missed plane yields ``[]``. Loops
    within a body and the bodies themselves are sorted by ``_loop_key`` so the
    output is order-stable across runs.
    """
    plane = getattr(b3d.Plane, _SECTION_PLANES[plane_name]).offset(float(offset_mm))
    bodies: list = []
    for solid in (part.solids() or [part]):
        sec = b3d.section(solid, section_by=plane)
        loops: list = []
        for face in sec.faces():
            for wire in (face.outer_wire(), *face.inner_wires()):
                loop = _wire_loop_2d(wire, plane)
                if len(loop) >= 3:
                    loops.append(loop)
        if loops:
            loops.sort(key=_loop_key)
            bodies.append(loops)
    bodies.sort(key=lambda ls: _loop_key(ls[0]))
    return bodies


def _bbox2d(loops):
    """(x0, y0, x1, y1) over every point in every loop, or a unit box."""
    xs = [x for loop in loops for x, _ in loop]
    ys = [y for loop in loops for _, y in loop]
    if not xs:
        return (0.0, 0.0, 1.0, 1.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _extra_slots(va, n: int) -> list:
    """`n` view slots in a row along the bottom of the view area (sections then
    details land here beneath the standard views). Returns (cx, cy, w, h)."""
    n = max(1, n)
    cell_w = va.w / n
    cell_h = va.h * 0.30
    cy = va.y + va.h - cell_h * 0.5
    return [(va.x + cell_w * (k + 0.5), cy, cell_w, cell_h) for k in range(n)]


def _cutting_marks(placement, plane_name, offset_mm, label):
    """Cutting-plane line + two arrows + the section letter on the parent view.

    Horizontal for an xy/xz cut, vertical for a yz cut; positioned at the cut's
    projected coordinate in the parent view (front: projX=worldX, projY=worldZ;
    top: projY=worldY). A cut off the geometry still draws — a reader sees the
    plane leaving the part."""
    ox, oy, scale, (bx0, by0, bx1, by1) = placement
    x0, x1 = ox + scale * bx0, ox + scale * bx1
    y_top, y_bot = oy - scale * by1, oy - scale * by0
    els: list = []
    if plane_name == "yz":                      # vertical line at worldX=offset
        sx = ox + scale * float(offset_mm)
        a, b = (sx, y_top - 8.0), (sx, y_bot + 8.0)
        els.append(Line(a[0], a[1], b[0], b[1], Style.CHAIN))
        els.append(_arrow(a, (1.0, 0.0)))
        els.append(_arrow(b, (1.0, 0.0)))
        els.append(Text(a[0], a[1] - 2.0, label, Style.TEXT, size=4))
        els.append(Text(b[0], b[1] + 5.0, label, Style.TEXT, size=4))
    else:                                       # horizontal line
        v = float(offset_mm) if plane_name == "xy" else -float(offset_mm)
        sy = oy - scale * v
        a, b = (x0 - 8.0, sy), (x1 + 8.0, sy)
        els.append(Line(a[0], a[1], b[0], b[1], Style.CHAIN))
        els.append(_arrow(a, (0.0, 1.0)))
        els.append(_arrow(b, (0.0, 1.0)))
        els.append(Text(a[0] - 3.0, sy + 1.0, label, Style.TEXT, size=4))
        els.append(Text(b[0] + 3.0, sy + 1.0, label, Style.TEXT, size=4))
    return els


def _clip_edges_to_circle(edges, cx, cy, radius):
    """Sub-polylines of each projected edge that fall inside the detail circle
    (projected 2D coords). A pure 2D op — reuses the parent view's projection, no
    kernel rebuild (FR7)."""
    runs: list = []
    for e in edges:
        n = max(2, min(256, int(e.length / 0.4)))
        cur: list = []
        for i in range(n):
            p = e.position_at(i / (n - 1))
            if math.hypot(float(p.X) - cx, float(p.Y) - cy) <= radius:
                cur.append((float(p.X), float(p.Y)))
            elif len(cur) >= 2:
                runs.append(cur)
                cur = []
            else:
                cur = []
        if len(cur) >= 2:
            runs.append(cur)
    return runs


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

#: How deep past a blind hole's recorded bottom this handler looks for the
#: material that bottom is made of. Small enough that it is inside any real
#: stock, large enough to clear the classifier's own boundary tolerance and the
#: tessellation-free B-rep face it is testing against.
_BLIND_PROBE_MM = 0.05

#: How far outside a seat's outer radius the seat probe looks for the material
#: the seat is cut into. Comfortably clear of the seat wall and of the
#: classifier's boundary tolerance, and small next to any real seat.
_SEAT_PROBE_MM = 0.25


def _record_problem(record) -> str | None:
    """Why this record may not be drawn, or None.

    **The same validator the harvest raises on** — `hole_standards.
    validate_record`, which checks the record's shape *and* that its
    designation is what its own numbers spell. This used to be a five-field
    spot-check (`id`, `designation`, `d`, `count`, `centers`), so a plausible
    dict `setattr`-ed onto the shape with a fabricated designation beside one
    real diameter and centre printed that designation on the sheet. A drawing
    is the one surface where a record becomes a manufacturing instruction, so
    it is the last place a weaker check belongs.

    It is reported and skipped here rather than raised, because one bad dict
    must not take a whole sheet down.
    """
    from agentcad.toolkit import hole_standards

    problem = hole_standards.validate_record(record)
    if problem is not None:
        return f"{problem}; it is not drawn"
    if not record["centers"]:
        return (f"hole record {record['id']!r} ({record['designation']}) "
                f"claims no instance that removed material, so it has no "
                f"centre to point a leader at and no callout")
    return None


def _without(record, *, seat: bool = False, depth: bool = False) -> str:
    """The record's callout with a feature the geometry no longer supports
    taken out of it.

    Built by `hole_standards.designation_for_record` from a modified copy of
    the record — the same function that built the original — so a degraded
    callout is spelled by the same grammar as an honest one and no string
    surgery happens here. `designation_base` covers the depth-only case and is
    kept for readers that have only the record; this covers the seat, and both
    at once.
    """
    from agentcad.toolkit import hole_standards

    patch = dict(record)
    if depth:
        patch.update({"thru": True, "depth_mm": None})
    if seat:
        patch.update({"family": "clearance", "cbore": None, "csk": None})
    try:
        return hole_standards.designation_for_record(patch)
    except Exception:                                          # noqa: BLE001
        return record["designation"]


def _matched_world_centers(record, circles) -> list:
    """The record's own world centres that a matched projected circle sits on.

    `_match_record` hands back the *circles*; the depth probe needs the record's
    3-D centres, and only the ones whose hole is still in the geometry — a
    centre whose circle is gone is not a hole to check the depth of.
    """
    return [c for c in record["centers"]
            if any(math.dist((circle.X, circle.Y), _top_xy(c))
                   <= _HOLE_CENTER_TOL for circle in circles)]


def _seat_geometry(record) -> tuple[float, float, float] | None:
    """``(outer_radius, mid_depth, void_radius)`` of a counterbore pocket or
    countersink cone, in millimetres, or None when the record has no seat.

    A counterbore's pocket is a cylinder of the recorded diameter and depth, so
    at mid-depth its void runs the whole way out to ``outer_radius``. A
    countersink's cone runs from the recorded seat diameter at the surface down
    to the bore radius — the same arithmetic ``holes.countersink`` uses to
    build it — and **at mid-depth its radius is exactly ``(seat_r + bore_r)/2``
    whatever the included angle**, because the cone is straight-sided and the
    angle cancels out of the mid-point. That is worth writing down: it is why
    the outer probe below is outside the cone for every angle (verified
    60–140°) and why ``void_radius`` can be stated without one.
    """
    bore_r = float(record["d"]) / 2.0
    if record["family"] == "counterbore":
        seat = record.get("cbore") or {}
        seat_r = float(seat["d"]) / 2.0
        return seat_r, float(seat["depth"]) / 2.0, (bore_r + seat_r) / 2.0
    if record["family"] == "countersink":
        seat = record.get("csk") or {}
        seat_r = float(seat["d"]) / 2.0
        half = math.radians(float(seat["angle_deg"]) / 2.0)
        height = (seat_r - bore_r) / math.tan(half)
        # Strictly between the bore wall and the cone wall at mid-depth, so the
        # point is in the seat's void for any angle.
        return seat_r, height / 2.0, (3.0 * bore_r + seat_r) / 4.0
    return None


def _seat_present(shape, record, centers) -> bool | None:
    """Two questions about a counterbore pocket or countersink cone, both at
    its own mid-depth: **is there material at any of four azimuths just outside
    it, and is the seat's own space still empty?**

    `True` both hold, `False` either fails, `None` the record has no seat or
    the question could not be asked. That sentence is the whole claim — this
    docstring has been wrong three times by describing the check as "catches a
    seat machined away", which it does not in general.

    **What it catches**, measured on a 30 mm plate with an M8 counterbore
    (⌀14.5 × 8.8):

    * the seat region milled off entirely — no material at any azimuth. That
      was the round-4 finding: two seats machined off the top still printed
      `⌀9 ⌴⌀14.5↧8.8` and `⌀6.6 ⌵⌀13.44×90°`, byte-identical to the control;
    * the pocket **filled solid** — the void probe. Measured: volume 430 091
      against the control's 429 198 (*above* it), and the outer probe alone
      read `true` with no warning at any sampling density, because "is there
      material around the seat" is not "is the seat a void".

    **What it does NOT catch, and cannot at this bias:**

    * the seat region milled off with **anything left at one azimuth** — a
      2×2 mm pin (volume 303 967 against 429 198) reads `true`;
    * a **slot milled across it** leaving 0.25 mm crescents at ±X (volume
      415 856) reads `true`;
    * a seat whose **diameter, depth or angle changed** while remaining a void
      in material.

    Both misses follow from `any` rather than `all`, and that bias is a
    measured choice, not an oversight. A bounding-box-filtered `all` catches
    the pin and the slot and keeps both edge cases — but it reads `false` on a
    **correct** counterbore with an ordinary pocket touching or overlapping its
    probe ring, i.e. it degrades a true drawing on a routine layout. Degrading
    a correct callout is the worse failure, so `any` stays and the misses are
    written down here instead.
    """
    geometry = _seat_geometry(record)
    if geometry is None or not centers:
        return None
    try:
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_IN
        from OCP.gp import gp_Pnt

        outer_r, mid_depth, void_r = geometry
        axis = [float(v) for v in record["axis"]]
        # Two unit vectors spanning the plane the seat's annulus lies in. Any
        # vector not parallel to the axis seeds them; the seat is round, so
        # which one does not matter.
        seed = [0.0, 0.0, 1.0] if abs(axis[2]) < 0.9 else [1.0, 0.0, 0.0]
        u = _cross(axis, seed)
        u = _normalized(u)
        v = _normalized(_cross(axis, u))
        radius = outer_r + _SEAT_PROBE_MM
        classifier = BRepClass3d_SolidClassifier(shape.wrapped)
        for center in centers:
            base = [float(c) for c in center]
            found = False
            for du, dv in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
                point = [base[k] + axis[k] * mid_depth
                         + (u[k] * du + v[k] * dv) * radius for k in range(3)]
                classifier.Perform(gp_Pnt(*point), 1e-7)
                if classifier.State() == TopAbs_IN:
                    found = True
                    break
            if not found:
                return False
            # …and the seat's own space is still empty. One point, inside the
            # seat by construction, so it is inside the part's footprint
            # wherever the seat is and cannot degrade a seat near an edge —
            # which is what makes it free to add over the `any` bias above.
            # It is the only thing that sees a pocket filled back in.
            #
            # ONE azimuth against the ring's four, deliberately: the two
            # probes need opposite quantifiers. The ring asks `any` because a
            # missing azimuth may be a legitimate edge, so more samples only
            # add ways to say yes; this one asks `all` in effect (any filled
            # sample refuses), so more samples would only add ways to say no —
            # and a partly filled seat is still a seat whose callout is right
            # about its diameter. One sample is where that stops.
            inside = [base[k] + axis[k] * mid_depth + u[k] * void_r
                      for k in range(3)]
            classifier.Perform(gp_Pnt(*inside), 1e-7)
            if classifier.State() == TopAbs_IN:
                return False
        return True
    except Exception:                                          # noqa: BLE001
        return None


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _normalized(v):
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / length for c in v]


def _bottom_present(shape, record, centers) -> bool | None:
    """Is the material a blind record's flat bottom is made of still there?

    `True` the material just past the recorded bottom is present, `False` it is
    gone, `None` the question could not be asked (no classifier, an unusable
    axis). A record is INTENT and `carry()` moves it across operations without
    re-measuring, so a later cut that deepens or opens a blind hole leaves an
    obsolete `↧` in the callout — measured: an M8 tapped hole recorded blind at
    6 mm, then drilled through, still printed `M8×1.25 - 6H ↧6` with no warning.

    **The name is the whole claim, and it used to be `_blind_depth_holds` /
    `depth_verified`, which claimed more.** This classifies ONE point, on the
    axis, just past the recorded bottom. It therefore catches a hole made
    DEEPER, and it does not catch a hole made SHALLOWER: mill 3 mm off the
    *top* of a 12 mm plate holding a 6 mm blind hole and the real depth is 3 mm
    while the bottom is exactly where it was — measured, the sheet printed
    `↧6` over a 3 mm hole and was byte-identical to the control. Measuring the
    hole's actual depth means finding where its wall begins, which is a ray
    cast into a projection this handler does not have; `true` here means
    "the bottom is present", nothing more, and the field is named for that.
    """
    try:
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_IN
        from OCP.gp import gp_Pnt

        depth = float(record["depth_mm"])
        axis = [float(v) for v in record["axis"]]
        if not centers:
            return None
        classifier = BRepClass3d_SolidClassifier(shape.wrapped)
        for center in centers:
            base = [float(v) for v in center]
            point = [base[k] + axis[k] * (depth + _BLIND_PROBE_MM)
                     for k in range(3)]
            classifier.Perform(gp_Pnt(*point), 1e-7)
            if classifier.State() != TopAbs_IN:
                return False
        return True
    except Exception:                                          # noqa: BLE001
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


def _largest_fit(s_fit: float) -> float:
    """The largest ladder ratio that is <= ``s_fit`` (clamped to the ladder)."""
    for ratio in SCALE_LADDER:                 # largest first
        if ratio <= s_fit:
            return ratio
    return SCALE_LADDER[-1]


def _choose_scale(template, order, vbb, override):
    """Uniform auto-scale (FR1): one scale for ALL views, the largest ladder
    ratio whose scaled views each fit their quadrant of the view area.

    Returns ``(ratio, warning_or_None)``. An explicit ``override`` is honored
    verbatim; if it overflows the fit, the warning names the crowding risk (a
    tripwire, not a refusal — the caller asked for it).
    """
    va = template.view_area
    quad_w, quad_h = va.w / 2.0, va.h / 2.0
    # Leave room inside the quadrant for the 14 mm dimension offset and the
    # view label: fit the bbox into ~72% of the quadrant.
    fill = 0.72
    s_fit = None
    for name in order:
        bx0, by0, bx1, by1 = vbb[name]
        w, h = max(bx1 - bx0, 1e-3), max(by1 - by0, 1e-3)
        s = min(quad_w * fill / w, quad_h * fill / h)
        s_fit = s if s_fit is None else min(s_fit, s)
    if s_fit is None:
        s_fit = 1.0
    if override is not None:
        ratio = float(override)
        warn = None
        if ratio > s_fit * 1.0001:
            warn = (f"requested scale {scale_label(ratio)} overflows the view "
                    f"area on sheet {template.format} (largest that fits is "
                    f"{scale_label(_largest_fit(s_fit))}); views may crowd or "
                    f"clip")
        return ratio, warn
    return _largest_fit(s_fit), None


def _title_block(dl, template, scale_str, title, fallback_label):
    """Data-driven title block (FR2). Every value arrives as a plain string in
    ``title`` — this handler renders, it never reads git or the clock."""
    title = title or {}
    tb = template.title_block
    dl.append(Rect(tb.x, tb.y, tb.w, tb.h, style=Style.FRAME, fill="white"))
    label = title.get("label") or fallback_label
    dl.append(Text(tb.x + 4, tb.y + 8, label, Style.TEXT, anchor="start",
                   size=5))
    units = title.get("units") or "mm"
    lines = [
        f"AgentCAD · {units} · third angle",
        f"scale {scale_str}   sheet {template.format}",
        f"material {title.get('material') or '—'}   "
        f"mass {title.get('mass') or '—'}",
        f"rev {title.get('version_ref') or '-'}   "
        f"{title.get('version_date') or '-'}",
    ]
    for key, prefix in (("company", "company"), ("author", "author"),
                        ("project_code", "project"),
                        ("approved_by", "approved"), ("notes", "notes")):
        val = title.get(key)
        if val:
            lines.append(f"{prefix}: {val}")
    y = tb.y + 12.5
    for ln in lines:
        dl.append(Text(tb.x + 4, y, ln, Style.TEXT, anchor="start", size=2.6))
        y += 2.9


def _build_display_list(part, views, detected_out, pmi=None, hole_records=(),
                        dim_table=None, sheet=DEFAULT_SHEET,
                        scale_override=None, title=None, sections=(),
                        details=()):
    """Build the ordered display list for one sheet — the SINGLE composition
    both backends render (Slice 2, Decision 1/7). Returns
    ``(display_list, width_mm, height_mm, meta)``; ``detected_out`` is filled in
    place. ``_build_svg`` / ``_build_pdf`` are thin wrappers that hand the list
    to :class:`SvgBackend` / :class:`PdfBackend`."""
    template = SHEETS.get(sheet) or SHEETS[DEFAULT_SHEET]
    W, H = template.w_mm, template.h_mm
    proj = {name: part.project_to_viewport(look_at=(0, 0, 0), **_VIEW_DIRS[name])
            for name in views}
    hole_warnings: list[str] = []
    warnings: list[str] = []

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

    # The display list: an ordered list of typed primitives (z-order = insert
    # order), rendered by SvgBackend at the end. Sheet background + frame from
    # the selected template (no hard-coded 420x297 anymore).
    dl: list = [
        Rect(0, 0, W, H, style=None, fill="white"),
        Rect(template.frame_inset, template.frame_inset,
             W - 2 * template.frame_inset, H - 2 * template.frame_inset,
             style=Style.FRAME),
    ]

    order = [v for v in ("top", "front", "right", "iso") if v in views]

    # Uniform auto-scale (FR1): one scale chosen for ALL views from the
    # preferred ladder, not today's per-view independent fit.
    vbb = {name: _view_bounds(list(proj[name][0]) + list(proj[name][1]))
           for name in order}
    scale, scale_warn = _choose_scale(template, order, vbb, scale_override)
    if scale_warn:
        warnings.append(scale_warn)
    scale_str = scale_label(scale)

    va = template.view_area
    # Quadrant centres inside the view area (top-left/top-right/bottom-*).
    cells = {
        "top":   (va.x + va.w * 0.25, va.y + va.h * 0.25),
        "iso":   (va.x + va.w * 0.75, va.y + va.h * 0.25),
        "front": (va.x + va.w * 0.25, va.y + va.h * 0.75),
        "right": (va.x + va.w * 0.75, va.y + va.h * 0.75),
    }
    for name in order:
        vis, hid = proj[name]
        cx, cy = cells[name]
        bx0, by0, bx1, by1 = vbb[name]
        w, h = max(bx1 - bx0, 1e-3), max(by1 - by0, 1e-3)
        # center the view's bbox on the cell, at the uniform scale
        ox = cx - scale * (bx0 + bx1) / 2
        oy = cy + scale * (by0 + by1) / 2
        placements[name] = (ox, oy, scale, (bx0, by0, bx1, by1))
        if name != "iso":
            for e in hid:
                dl.append(_edge_prim(e, ox, oy, scale, Style.HID))
        for e in vis:
            dl.append(_edge_prim(e, ox, oy, scale, Style.VIS))
        # overall dimensions on front/top (width along X, height along Y).
        # PMI linear dims tolerance the overall extents: "width" = X in both
        # views, "height" = front-view Y (world Z), "depth" = top-view Y.
        if name in ("front", "top"):
            x_dim = linear_by_target.get("width")
            y_dim = linear_by_target.get("height" if name == "front" else "depth")
            x_text, y_text = fmt(w), fmt(h)
            if x_dim is not None:
                x_text += _tol_suffix(x_dim["plus"], x_dim["minus"])
                rendered_linear.add(x_dim["id"])
            if y_dim is not None:
                y_text += _tol_suffix(y_dim["plus"], y_dim["minus"])
                rendered_linear.add(y_dim["id"])
            dl += _linear_dim((ox + scale * bx0, oy - scale * by0),
                              (ox + scale * bx1, oy - scale * by0),
                              offset=14, text=x_text)
            dl += _linear_dim((ox + scale * bx0, oy - scale * by0),
                              (ox + scale * bx0, oy - scale * by1),
                              offset=-14, text=y_text)
        # view label, just above the view's projected bbox
        dl.append(Text(cx, oy - scale * by1 - 4, name.upper(), Style.TEXT,
                       anchor="middle", size=4))

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
            # A callout column just left of the sheet's right-hand column,
            # stacking downward — on-sheet for every format (the old absolute
            # 196 was tied to A3).
            tx = template.title_block.x - 4.0
            ty = template.revision_block.y + 16.0 + 8.0 * slot
            c = max(centers, key=lambda c: (c.X, c.Y))
            tip = (ox_t + sc_t * c.X, oy_t - sc_t * c.Y)
            tail = (tx - 1.5, ty - 1.2)
            dl.append(Line(tail[0], tail[1], tip[0], tip[1], Style.DIM))
            dl.append(_arrow(tip, (tip[0] - tail[0], tip[1] - tail[1])))
            dl.append(Text(tx, ty, text, Style.DIM, anchor="start", size=3.5))
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
            # What the leader is actually drawn over. `_match_record` already
            # worked this out — it is the set of projected circles that both
            # share the record's diameter and land on one of its centres — and
            # the callout used to discard it and print `record["count"]`
            # instead. The record is INTENT ("drill four"); the circles are
            # what the geometry ended up with, and a drawing describes the
            # part in front of it. Print the circles; report the divergence.
            drawn = len(centers)
            claimed = int(record["count"])
            # A blind depth is a claim about geometry the record cannot see:
            # `carry()` moves records across later operations without
            # re-measuring, so a cut that deepened or opened this hole leaves
            # an obsolete `↧` in the designation. Degrade rather than guess —
            # print the callout WITHOUT the depth (`designation_base`, the same
            # string built by the same function) and say what was recorded, in
            # the warning, where it cannot be mistaken for a dimension.
            text = record["designation"]
            # `bottom_present`, and NOT `depth_verified`: `null` means no blind
            # bottom was looked for (a through hole has none, and a check that
            # could not run says so in `hole_warnings`), `false` means the
            # material under the recorded bottom is gone. `true` means the
            # bottom is there — it does **not** mean the depth is right, and
            # the field carried a name that said it did. A hole made shallower
            # from the top keeps its bottom and its `true`.
            world = _matched_world_centers(record, centers)
            # The SEAT is the other half of the same question, and it used to
            # be asked of nothing at all: a counterbore's pocket and a
            # countersink's cone travel inside `designation` and printed
            # verbatim, so two seats machined entirely off a plate still put
            # four numbers on the sheet. Degraded the same way a lost blind
            # depth is: the seat comes off the callout and the record's own
            # numbers spell what is left.
            seat_present = _seat_present(part, record, world)
            if seat_present is False:
                text = _without(record, seat=True)
                seat = record.get("cbore") or record.get("csk") or {}
                hole_warnings.append(
                    f"hole record {record['id']!r} states a "
                    f"{record['family']} seat of ⌀{float(seat.get('d', 0)):g}, "
                    f"but the final geometry shows no recess there at its own "
                    f"depth — either nothing surrounds it at any of four "
                    f"azimuths, or its space is no longer empty. The callout "
                    f"is printed WITHOUT the seat ({text!r}); the bore is what "
                    f"the sheet can be measured against")
            bottom_present = None
            if not record["thru"] and record.get("depth_mm") is not None:
                bottom_present = _bottom_present(part, record, world)
                if bottom_present is False:
                    text = _without(record, seat=seat_present is False,
                                    depth=True)
                    hole_warnings.append(
                        f"hole record {record['id']!r} states a blind depth of "
                        f"{float(record['depth_mm']):g} mm, but the material "
                        f"under that depth is gone in the final geometry — a "
                        f"later operation deepened or opened the hole. The "
                        f"callout is printed WITHOUT the depth "
                        f"({text!r}); the recorded depth is stale and this "
                        f"drawing does not assert it")
                elif bottom_present is None:
                    hole_warnings.append(
                        f"hole record {record['id']!r} states a blind depth of "
                        f"{float(record['depth_mm']):g} mm that could not be "
                        f"checked against the final geometry; the callout "
                        f"prints it as recorded")
            group = by_dia.get(dia)
            if group is None:
                # Below the detector's count >= 3 threshold: the record is the
                # authority for the designation, but the count still comes
                # from the circles that are there to be counted.
                group = {"diameter_mm": dia, "count": drawn,
                         "from_metadata": False}
                hole_groups.append(group)
                by_dia[dia] = group
            if drawn != claimed:
                hole_warnings.append(
                    f"hole record {record['id']!r} ({record['designation']}) "
                    f"states {claimed} instance(s) but {drawn} matching "
                    f"⌀{dia:g} circle(s) are in the top view; the callout "
                    f"reads {drawn} — the count the sheet can be measured "
                    f"against — and the record is stale about the rest")
            elif group["count"] > drawn:
                # The record accounts for only part of a group of same-diameter
                # circles. The unmatched ones are deliberately NOT swept into
                # this callout: a second feature that happens to share a
                # diameter would then be mislabelled, which is the exact
                # mistake `_match_record` requires centre agreement to avoid.
                hole_warnings.append(
                    f"hole record {record['id']!r} ({record['designation']}) "
                    f"accounts for {drawn} of the {group['count']} ⌀{dia:g} "
                    f"circles in the top view; the remaining "
                    f"{group['count'] - drawn} carry no callout because no "
                    f"record claims them")
            if (group["from_metadata"]
                    and group.get("designation") != text):
                hole_warnings.append(
                    f"hole records {group.get('record_id')!r} and "
                    f"{record['id']!r} both claim the ⌀{dia:g} circles with "
                    f"different designations "
                    f"({group.get('designation')!r} vs "
                    f"{text!r}); the group reports the first")
            else:
                group.update({"from_metadata": True,
                              "designation": text,
                              "family": record.get("family"),
                              "record_id": record["id"],
                              "bottom_present": bottom_present,
                              "seat_present": seat_present})
            if dia not in pmi_drawn:
                _leader(centers, _callout_text(text, drawn))

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

    # The per-configuration dimension table, in the sheet's clear right column
    # above the ISO view. `detected` echoes it structurally, so a caller never
    # has to parse the SVG back to find out what was measured.
    if dim_table:
        # The table zone comes from the sheet template now (Decision 2). For
        # iso_a3 it is (264, 18) — the pre-v2 clear rectangle — so the table
        # renders in the same place. Slice 2: typed primitives, extended into
        # the display list so both the SVG and the PDF backend render it.
        table_els, table_dropped, table_warnings = _dim_table(
            dim_table["rows"], dim_table["columns"],
            x=template.table_zone.x, y_top=template.table_zone.y)
        dl.extend(table_els)
        detected_out["dim_table"] = {
            # `columns` is what was ASKED for, and `dropped` what did not fit:
            # a caller comparing the echo to its own request should not have to
            # diff two lists to discover a column is missing.
            "columns": list(dim_table["columns"]),
            "dropped": table_dropped,
            "rows": dim_table["rows"],
            "placement": "right-column",
            "warnings": list(dim_table.get("warnings") or []) + table_warnings,
        }

    # Data-driven title block (FR2), drawn from the template's title-block zone.
    _title_block(dl, template, scale_str, title,
                 detected_out.get("label", "part"))
    tb = template.title_block

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
        dl.extend(_datum_flag(datum["id"], box, anchor))
        n_datums += 1

    # PMI feature control frames: a column left-aligned with the title block,
    # first frame just above it, stacking upward.
    n_fcf = 0
    fcf_h, fcf_bottom = 7.0, tb.y - 4.0
    for frame in pmi_fcf:
        fcf_top = fcf_bottom - fcf_h
        dl.extend(_fcf_frame(tb.x, fcf_top, frame, fcf_h))
        n_fcf += 1
        fcf_bottom = fcf_top - 2.0

    if pmi_active:
        detected_out["pmi_rendered"] = {
            "dims": len(rendered_linear) + n_dia_rendered,
            "datums": n_datums,
            "fcf": n_fcf,
        }
        detected_out["pmi_warnings"] = pmi_warnings

    # Section & detail views (FR6/FR7). Both land in a row of slots beneath the
    # standard views; sections cut the built shape here (no second kernel call),
    # details clip the parent view's already-computed projection (no rebuild).
    section_specs = list(sections or [])
    detail_specs = list(details or [])
    slots = _extra_slots(va, len(section_specs) + len(detail_specs))
    section_descs: list = []
    detail_descs: list = []
    slot_i = 0

    for s_i, spec in enumerate(section_specs):
        label = spec.get("label") or f"{_letter(s_i)}-{_letter(s_i)}"
        plane_name = spec["plane"]
        offset_mm = float(spec.get("offset_mm", 0.0))
        bodies = _section_bodies(part, plane_name, offset_mm)
        cx, cy, cell_w, cell_h = slots[slot_i]
        slot_i += 1
        # Cutting-plane marks on the parent view (whichever is rendered).
        parent = _SECTION_PARENT[plane_name]
        if parent not in placements:
            parent = order[0] if order else None
        if parent is not None:
            dl.extend(_cutting_marks(placements[parent], plane_name,
                                     offset_mm, label))
        if not bodies:
            # A plane that misses the solid: a warning + an empty, labelled
            # section view — never a silent blank sheet.
            warnings.append(f"section {label}: plane misses the solid")
            dl.append(Text(cx, cy, f"{label} (empty)", Style.TEXT, size=4))
            section_descs.append({"label": label, "plane": plane_name,
                                  "offset_mm": offset_mm, "bodies": 0,
                                  "empty": True})
            continue
        x0, y0, x1, y1 = _bbox2d([p for body in bodies for p in body])
        ox = cx - scale * (x0 + x1) / 2.0
        oy = cy + scale * (y0 + y1) / 2.0
        for b_i, body in enumerate(bodies):
            sheet_loops = tuple(
                tuple((ox + scale * lx, oy - scale * ly) for lx, ly in loop)
                for loop in body)
            for sl in sheet_loops:
                dl.append(Polyline(sl, style=Style.VIS, closed=True))
            # Alternating hatch angle across bodies: 45, 135, 45, ...
            angle = 45.0 if b_i % 2 == 0 else 135.0
            dl.append(Hatch(sheet_loops, angle=angle, pitch=2.0))
        dl.append(Text(cx, oy - scale * y0 + 6.0, label, Style.TEXT, size=4))
        section_descs.append({"label": label, "plane": plane_name,
                              "offset_mm": offset_mm, "bodies": len(bodies)})

    for d_i, spec in enumerate(detail_specs):
        label = _letter(d_i)
        view = spec["view"]
        cxm, cym = spec["center_mm"]
        radius = float(spec["radius_mm"])
        dscale = float(spec["scale"])
        cx, cy, cell_w, cell_h = slots[slot_i]
        slot_i += 1
        if view not in placements or view not in proj:
            warnings.append(
                f"detail {label}: view {view!r} is not rendered, so it has no "
                f"magnified view")
            detail_descs.append({"label": label, "view": view,
                                 "center_mm": [cxm, cym], "radius_mm": radius,
                                 "scale": dscale, "clipped": False})
            continue
        ox_p, oy_p, sc_p, _bb = placements[view]
        # The labelled circle on the parent view (where the detail is taken).
        dl.append(Circle(ox_p + sc_p * cxm, oy_p - sc_p * cym, sc_p * radius,
                         Style.THIN))
        dl.append(Text(ox_p + sc_p * cxm, oy_p - sc_p * cym - sc_p * radius - 2.0,
                       label, Style.TEXT, size=4))
        # The magnified view: clip the parent projection to the circle, scale up.
        vis, hid = proj[view]
        for edges, style in ((hid, Style.HID), (vis, Style.VIS)):
            if view == "iso" and style is Style.HID:
                continue
            for run in _clip_edges_to_circle(edges, cxm, cym, radius):
                pts = tuple((cx + dscale * (px - cxm), cy - dscale * (py - cym))
                            for px, py in run)
                dl.append(Polyline(pts, style))
        dl.append(Circle(cx, cy, dscale * radius, Style.THIN))
        dl.append(Text(cx, cy + dscale * radius + 6.0,
                       f"{label} ({scale_label(dscale)})", Style.TEXT, size=4))
        detail_descs.append({"label": label, "view": view,
                             "center_mm": [cxm, cym], "radius_mm": radius,
                             "scale": dscale, "clipped": True})

    return dl, W, H, {"scale": scale_str, "views": order, "warnings": warnings,
                      "sections": section_descs, "details": detail_descs}


def _build_svg(part, views, detected_out, pmi=None, hole_records=(),
               dim_table=None, sheet=DEFAULT_SHEET, scale_override=None,
               title=None, sections=(), details=()):
    """Render the sheet to an SVG string (thin wrapper over the shared list)."""
    dl, W, H, meta = _build_display_list(
        part, views, detected_out, pmi=pmi, hole_records=hole_records,
        dim_table=dim_table, sheet=sheet, scale_override=scale_override,
        title=title, sections=sections, details=details)
    return SvgBackend().render(dl, W, H), meta


def _build_pdf(part, views, detected_out, pmi=None, hole_records=(),
               dim_table=None, sheet=DEFAULT_SHEET, scale_override=None,
               title=None, sections=(), details=()):
    """Render the sheet to PDF bytes (the SAME list, a different backend —
    FR11). Deterministic: see :mod:`agentcad.kernel.handlers._pdf`."""
    dl, W, H, meta = _build_display_list(
        part, views, detected_out, pmi=pmi, hole_records=hole_records,
        dim_table=dim_table, sheet=sheet, scale_override=scale_override,
        title=title, sections=sections, details=details)
    return PdfBackend().render(dl, W, H), meta


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
        # `out_format`, not `fmt`: `fmt` is the imported float formatter.
        views = params.get("views") or ["top", "front", "right", "iso"]
        out_format = params.get("format", "svg")
        sheet = params.get("sheet") or DEFAULT_SHEET
        out_path = params["out_path"]
        shape, _values, _warnings = build_shape(params["script"], params.get("params", {}))
        detected: dict = {"label": params.get("label", "part")}
        meta = {"scale": scale_label(1.0), "views": list(views), "warnings": []}
        if out_format in ("svg", "pdf"):
            # One extra `build_shape` per configuration — measured here, in the
            # process that owns the kernel, because a drawing prints geometry.
            # DXF ignores the table exactly as it ignores PMI (v1), so nothing
            # is measured for a format that cannot draw it. SVG and PDF render
            # the SAME display list (PRD-014 Slice 2), so both draw the table
            # and PMI; only the backend differs.
            table = params.get("dim_table")
            measured = (_measure_table(build_shape, params["script"], table)
                        if isinstance(table, dict) and table.get("rows")
                        else None)
            build = _build_svg if out_format == "svg" else _build_pdf
            payload, meta = build(
                shape, views, detected, pmi=params.get("pmi"),
                hole_records=_records_on(shape), dim_table=measured,
                sheet=sheet, scale_override=params.get("scale"),
                title=params.get("title"),
                sections=params.get("sections") or (),
                details=params.get("details") or ())
            # SVG is a str, PDF is already bytes.
            atomic_write(out_path,
                         payload.encode() if isinstance(payload, str)
                         else payload)
        elif out_format == "dxf":
            _build_dxf(shape, out_path)  # DXF ignores PMI and the table (v1)
            meta = {"scale": scale_label(1.0),  # DXF is real-scale geometry
                    "views": ["top"], "warnings": []}
        else:
            raise WorkerError(ERROR_CONTRACT,
                              f"unknown drawing format {out_format!r}")
        import os
        # FR13 machine-readable result skeleton. `sections`/`details` are filled
        # from the composition (Slice 3); DXF renders neither, so they stay empty
        # for that format. `detected` keeps pmi_rendered/hole_groups/dim_table.
        return {"path": out_path, "size_bytes": os.path.getsize(out_path),
                "sheet": sheet, "scale": meta["scale"],
                "views": meta["views"], "sections": meta.get("sections", []),
                "details": meta.get("details", []),
                "detected": detected, "warnings": meta["warnings"]}

    return {"drawing": drawing}
