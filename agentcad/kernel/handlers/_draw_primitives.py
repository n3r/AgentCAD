"""Drawing display-list primitives, styles, the central float formatter, and
the SVG backend (PRD-014 Drawings v2, Decision 1 — the foundation).

The drawing handler no longer writes SVG strings inline. It builds an ordered
**display list** of the small frozen primitive vocabulary below, and a backend
renders that list. Today there is one backend, :class:`SvgBackend`; Slice 2
adds a ``PdfBackend`` over the *same* list. Z-order is insertion order — a flat
list, not a scene graph (that was considered and rejected as overkill).

**Why a display list at all.** The PDF writer (FR11) and the byte-stability
guarantee (FR12) both need a representation that is not an SVG string. Parsing
SVG back into PDF is the nondeterministic dependency the PRD rejects; a shared
list of primitives makes hatching, sections and details (later slices) *more
primitives* instead of more string-splicing into a 1000-line function.

**Why ``fmt`` and why round-half-even.** Every user-visible coordinate and
number goes through :func:`fmt`. Determinism (two renders of the same geometry
⇒ identical bytes) is impossible if precision leaks through ad-hoc ``:.3f`` /
``:.2f`` / locale-sensitive formatting. Round-half-even (banker's rounding) is
the unbiased tie-break: it does not drift sums upward the way round-half-up
does, and Python's own ``round`` uses it, so ``fmt`` agrees with any incidental
``round`` already in the pipeline. The canonical form strips trailing zeros and
a trailing dot and never emits ``-0``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum

# The quantum every coordinate is rounded to: 3 decimal places (micron
# precision at millimetre scale — finer than any drawing needs and finer than
# the projection's own noise).
_Q = Decimal("0.001")


def fmt(x: float) -> str:
    """Canonical, deterministic, locale-free string for a drawing number.

    Round-half-even to 3 dp, strip trailing zeros and a trailing dot, and never
    return ``-0``. ``fmt(1.0) == "1"``, ``fmt(1.5) == "1.5"``,
    ``fmt(-0.0) == "0"``, ``fmt(1.2345) == "1.234"`` (the dropped ``5`` ties to
    the even ``4``). The one canonical form is locked by tests — no caller may
    re-format a number a second, different way.
    """
    # `Decimal(str(...))`: parse the *decimal* value, not the exact binary
    # float (which would be a 50-digit tail), so the rounding is on the number
    # a human would read. Deterministic and locale-independent.
    q = Decimal(str(float(x))).quantize(_Q, rounding=ROUND_HALF_EVEN)
    if q == 0:                      # collapses Decimal('-0.000') and '0.000'
        q = Decimal("0")
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


class Style(Enum):
    """The frozen style set. A primitive names a style; the backend maps it to
    concrete stroke/dash/fill. Adding a linetype is adding one enum member and
    one backend row — never a new primitive kind.
    """

    VIS = "vis"      # visible edges — solid black, thick
    HID = "hid"      # hidden edges — gray, dashed
    THIN = "thin"    # thin solid black (center marks — Slice 4)
    CHAIN = "chain"  # centerline — chain dash (Slice 4)
    DIM = "dim"      # dimension lines/arrows/text — blue
    HATCH = "hatch"  # section hatching — blue thin (Slice 3)
    FRAME = "frame"  # sheet frame / boxes — black
    TEXT = "text"    # annotation text — black
    NOTE = "note"    # secondary note text — gray


# ---- the primitive vocabulary (all coords in sheet mm, y-down) --------------


@dataclass(frozen=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    style: Style = Style.DIM


@dataclass(frozen=True)
class Polyline:
    #: A tuple of (x, y) points. ``closed`` joins the last to the first;
    #: ``fill`` paints the interior in the style colour (arrow heads).
    pts: tuple
    style: Style = Style.VIS
    closed: bool = False
    fill: bool = False


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float
    style: Style = Style.VIS


@dataclass(frozen=True)
class Arc:
    #: Kept in the vocabulary for Slice 3 section outlines; the SVG backend
    #: renders it as an elliptical-arc path segment. Angles in degrees, CCW,
    #: measured in the sheet plane (y-down).
    cx: float
    cy: float
    r: float
    start_deg: float
    end_deg: float
    style: Style = Style.VIS


@dataclass(frozen=True)
class Text:
    x: float
    y: float
    s: str
    style: Style = Style.TEXT
    anchor: str = "middle"     # start | middle | end
    size: float = 3.5
    angle: float = 0.0         # rotation about (x, y), degrees


@dataclass(frozen=True)
class Hatch:
    #: Section hatching (Slice 3): a set of closed loops filled with parallel
    #: lines at ``angle`` degrees, ``pitch`` mm apart. Carried in the vocabulary
    #: now so the backend contract is frozen; no producer emits it this slice.
    loops: tuple
    angle: float = 45.0
    pitch: float = 2.0
    style: Style = Style.HATCH


@dataclass(frozen=True)
class Rect:
    #: A box. ``style`` gives the stroke (``None`` for no stroke — the sheet
    #: background); ``fill`` is a colour name or ``None`` for no fill.
    x: float
    y: float
    w: float
    h: float
    style: Style | None = Style.FRAME
    fill: str | None = None


@dataclass(frozen=True)
class Raw:
    """A verbatim SVG fragment.

    A deliberate, documented escape hatch — **not** the general path. The
    per-configuration dimension table (``drawing._dim_table``) emits exact SVG
    strings whose bytes are locked by direct-call unit tests
    (``tests/test_configs_drawing.py``), so it is spliced in through ``Raw``
    rather than rebuilt as primitives in this slice. New composition uses the
    typed primitives above; ``Raw`` exists only for that one byte-locked helper
    and is ignored by non-SVG backends.
    """

    svg: str


# ---- the SVG backend --------------------------------------------------------

#: Style -> (stroke, stroke-width, dasharray|None). Text/note colours reuse the
#: stroke colour. This is the single source of truth the inline ``_VIS``/``_HID``
#: constants used to be; the mapping reproduces them exactly.
_STROKE = {
    Style.VIS: ("#111", 0.5, None, "round"),
    Style.HID: ("#777", 0.25, "2.4 1.2", None),
    Style.THIN: ("#111", 0.25, None, None),
    Style.CHAIN: ("#111", 0.25, "4 1 1 1", None),
    Style.DIM: ("#1a56db", 0.18, None, None),
    Style.HATCH: ("#1a56db", 0.18, None, None),
    Style.FRAME: ("#111", 0.5, None, None),
    Style.TEXT: ("#111", 0.0, None, None),
    Style.NOTE: ("#777", 0.0, None, None),
}
_FONT = 'font-family="Helvetica, Arial, sans-serif"'


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _stroke_attrs(style: Style) -> str:
    color, width, dash, cap = _STROKE[style]
    out = f'stroke="{color}" stroke-width="{fmt(width)}"'
    if dash:
        out += f' stroke-dasharray="{dash}"'
    if cap:
        out += f' stroke-linecap="{cap}"'
    return out


class SvgBackend:
    """Renders a display list to an SVG document string.

    ``render(display_list, width_mm, height_mm)`` — one ``<svg>`` sized to the
    sheet, then one element per primitive in list order. Every coordinate goes
    through :func:`fmt`.
    """

    def render(self, display_list: list, width_mm: float,
               height_mm: float) -> str:
        w, h = fmt(width_mm), fmt(height_mm)
        out = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" '
            f'height="{h}mm" viewBox="0 0 {w} {h}" {_FONT}>'
        ]
        for prim in display_list:
            out.append(self._one(prim))
        out.append("</svg>")
        return "\n".join(out)

    # -- per-primitive rendering --------------------------------------------

    def _one(self, p) -> str:
        return getattr(self, f"_{type(p).__name__.lower()}")(p)

    def _line(self, p: Line) -> str:
        return (f'<line x1="{fmt(p.x1)}" y1="{fmt(p.y1)}" x2="{fmt(p.x2)}" '
                f'y2="{fmt(p.y2)}" {_stroke_attrs(p.style)} fill="none"/>')

    def _polyline(self, p: Polyline) -> str:
        if p.fill:
            pts = " ".join(f"{fmt(x)},{fmt(y)}" for x, y in p.pts)
            color = _STROKE[p.style][0]
            return (f'<polygon points="{pts}" {_stroke_attrs(p.style)} '
                    f'fill="{color}"/>')
        if not p.pts:
            return ""
        d = "M " + " L ".join(f"{fmt(x)} {fmt(y)}" for x, y in p.pts)
        if p.closed:
            d += " Z"
        return f'<path d="{d}" {_stroke_attrs(p.style)} fill="none"/>'

    def _circle(self, p: Circle) -> str:
        return (f'<circle cx="{fmt(p.cx)}" cy="{fmt(p.cy)}" r="{fmt(p.r)}" '
                f'{_stroke_attrs(p.style)} fill="none"/>')

    def _arc(self, p: Arc) -> str:
        import math

        def _pt(a):
            return (p.cx + p.r * math.cos(math.radians(a)),
                    p.cy + p.r * math.sin(math.radians(a)))

        x0, y0 = _pt(p.start_deg)
        x1, y1 = _pt(p.end_deg)
        sweep = (p.end_deg - p.start_deg) % 360.0
        large = 1 if sweep > 180.0 else 0
        r = fmt(p.r)
        return (f'<path d="M {fmt(x0)} {fmt(y0)} A {r} {r} 0 {large} 1 '
                f'{fmt(x1)} {fmt(y1)}" {_stroke_attrs(p.style)} fill="none"/>')

    def _text(self, p: Text) -> str:
        color = _STROKE[p.style][0]
        tr = ""
        if abs(p.angle) > 1e-9:
            tr = f' transform="rotate({fmt(p.angle)} {fmt(p.x)} {fmt(p.y)})"'
        return (f'<text x="{fmt(p.x)}" y="{fmt(p.y)}" text-anchor="{p.anchor}" '
                f'{_FONT} font-size="{fmt(p.size)}" fill="{color}"{tr}>'
                f'{_esc(p.s)}</text>')

    def _hatch(self, p: Hatch) -> str:
        # No producer this slice; render loops as thin outlines so a stray
        # Hatch is at least visible rather than dropped.
        out = []
        for loop in p.loops:
            pts = " ".join(f"{fmt(x)} {fmt(y)}" for x, y in loop)
            if pts:
                out.append(f'<path d="M {pts} Z" {_stroke_attrs(p.style)} '
                           f'fill="none"/>')
        return "".join(out) if out else f"<!-- hatch {fmt(p.angle)} -->"

    def _rect(self, p: Rect) -> str:
        fill = p.fill if p.fill is not None else "none"
        stroke = _stroke_attrs(p.style) if p.style is not None else ""
        return (f'<rect x="{fmt(p.x)}" y="{fmt(p.y)}" width="{fmt(p.w)}" '
                f'height="{fmt(p.h)}" fill="{fill}" {stroke}/>')

    def _raw(self, p: Raw) -> str:
        return p.svg
