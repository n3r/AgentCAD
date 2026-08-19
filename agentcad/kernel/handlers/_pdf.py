"""A deterministic, dependency-free PDF backend for the drawing display list
(PRD-014 Drawings v2, Slice 2 — Decision 7/FR11, Decision 12/FR12).

:class:`PdfBackend` consumes the **same** ordered display list as
:class:`~agentcad.kernel.handlers._draw_primitives.SvgBackend` and emits a valid
single-page vector PDF directly — no ``reportlab``/``cairosvg`` (both risk
nondeterministic output, and neither is a dependency of this project), no SVG
re-parsing, no font embedding (text uses the base-14 **Helvetica** standard
font). Nothing here imports OCP/build123d; a PDF is arithmetic over the same
primitives an SVG is.

**Determinism is the whole point (FR12).** Two renders of the same display list
must produce byte-identical output, so:

* every coordinate/number goes through :func:`~..._draw_primitives.fmt` (the
  same round-half-even 3-dp formatter the SVG backend uses);
* object numbering and order are fixed (catalog, pages, page, font, contents);
* there is **no** ``/CreationDate``, **no** file ``/ID``, **no** ``Info`` dict —
  the only fields that would otherwise carry a clock or a random seed are simply
  omitted (a fully deterministic choice, argued in the design's Decision 7);
* the content stream is stored uncompressed (no filter), so there is no zlib
  implementation detail to pin.

**Coordinate system.** The display list is in sheet millimetres, y-down, origin
top-left (like SVG). PDF user space is points, y-up, origin bottom-left. Each
coordinate is transformed *in Python* to points — ``x_pt = x_mm·K``,
``y_pt = (H_mm − y_mm)·K`` with ``K = 72/25.4`` — rather than via a page CTM, so
that text is drawn upright (a y-flipping CTM would mirror every glyph). Line
widths, dash arrays and font sizes are all in millimetres in the display list
and are pre-scaled by ``K`` to points here.

**Text.** Anchored (start/middle/end) via the standard Helvetica AFM advance
widths below, so ``middle``/``end`` shift by the measured string width. Rotation
is applied with a text matrix (``Tm``); a positive SVG ``angle`` is clockwise in
the y-down sheet, which is clockwise on the page, i.e. ``-angle`` in y-up PDF
space. Strings are encoded WinAnsi/Latin-1; glyphs outside that range (the ⌀
diameter sign, GD&T characteristic symbols, the ↧ depth arrow) are not in the
base-14 Helvetica glyph set and are replaced with ``?`` — the dimension VALUES
and tolerances, which are ASCII, render in full. SVG keeps the full-fidelity
glyphs; this is a documented v1 limitation of the no-embedding PDF path.
"""

from __future__ import annotations

import math

from ._draw_primitives import (
    _STROKE,
    Arc,
    Circle,
    Hatch,
    Line,
    Polyline,
    Raw,
    Rect,
    Style,
    Text,
    fmt,
)

#: Millimetres → PostScript points. 1 in = 25.4 mm = 72 pt.
_K = 72.0 / 25.4

#: Bézier "magic constant": control-point offset for a 90° circular arc,
#: ``4/3·tan(π/8)``. Fixed, so the circle approximation is deterministic.
_KAPPA = 0.5522847498307936

#: Standard-14 **Helvetica** advance widths (AFM, per 1000 em) for the ASCII
#: printable range — enough to anchor text (middle/end) at the measured string
#: width. Non-ASCII glyphs fall back to the average below; anchoring stays
#: deterministic either way.
_HELV_W = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, "0": 556, "1": 556, "2": 556, "3": 556, "4": 556,
    "5": 556, "6": 556, "7": 556, "8": 556, "9": 556, ":": 278, ";": 278,
    "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015, "A": 667, "B": 667,
    "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278,
    "J": 500, "K": 667, "L": 556, "M": 833, "N": 722, "O": 778, "P": 667,
    "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944,
    "X": 667, "Y": 667, "Z": 611, "[": 278, "\\": 278, "]": 278, "^": 469,
    "_": 556, "`": 333, "a": 556, "b": 556, "c": 500, "d": 556, "e": 556,
    "f": 278, "g": 556, "h": 556, "i": 222, "j": 222, "k": 500, "l": 222,
    "m": 833, "n": 556, "o": 556, "p": 556, "q": 556, "r": 333, "s": 500,
    "t": 278, "u": 556, "v": 500, "w": 722, "x": 500, "y": 500, "z": 500,
    "{": 334, "|": 260, "}": 334, "~": 584,
}
_HELV_W_DEFAULT = 556


def _rgb(hexcolor: str) -> tuple[float, float, float]:
    """``#rgb``/``#rrggbb`` → three floats in ``[0, 1]``."""
    h = hexcolor.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
            int(h[4:6], 16) / 255.0)


def _fill_rgb(name: str) -> tuple[float, float, float]:
    """A :class:`Rect` fill (``"white"`` or a hex colour) → rgb."""
    if name == "white":
        return (1.0, 1.0, 1.0)
    if name.startswith("#"):
        return _rgb(name)
    return (0.0, 0.0, 0.0)


def _color_op(rgb: tuple[float, float, float], stroke: bool) -> str:
    """A colour-setting op: ``G``/``g`` for a gray (r==g==b), else ``RG``/``rg``
    (the design's "grays via G/g" rule)."""
    r, g, b = rgb
    if r == g == b:
        return f"{fmt(r)} {'G' if stroke else 'g'}"
    op = "RG" if stroke else "rg"
    return f"{fmt(r)} {fmt(g)} {fmt(b)} {op}"


def _stroke_setup(style: Style) -> list[str]:
    """Stroke colour + width (pt) + dash (pt) + cap for a primitive's style."""
    color, width, dash, cap = _STROKE[style]
    ops = [_color_op(_rgb(color), stroke=True), f"{fmt(width * _K)} w"]
    if dash:
        parts = " ".join(fmt(float(v) * _K) for v in dash.split())
        ops.append(f"[{parts}] 0 d")
    else:
        ops.append("[] 0 d")
    ops.append("1 J" if cap == "round" else "0 J")
    return ops


def _pdf_string(s: str) -> bytes:
    """A PDF literal string ``(…)``: escape ``\\ ( )``, encode WinAnsi/Latin-1
    (glyphs outside it — ⌀, GD&T symbols — become ``?``)."""
    out = (s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)"))
    return b"(" + out.encode("latin-1", "replace") + b")"


def _text_width(s: str, size_pt: float) -> float:
    """Rendered width of ``s`` in points at ``size_pt`` (Helvetica AFM)."""
    return sum(_HELV_W.get(ch, _HELV_W_DEFAULT) for ch in s) / 1000.0 * size_pt


class PdfBackend:
    """Renders a display list to a single-page PDF (bytes).

    ``render(display_list, width_mm, height_mm)`` mirrors
    :meth:`SvgBackend.render` (the drawing dispatch has the sheet size in hand,
    and no clock-derived metadata is used, so there is nothing else to pass).
    """

    def render(self, display_list: list, width_mm: float,
               height_mm: float) -> bytes:
        self._h = float(height_mm)
        stream = "\n".join(
            s for s in (self._one(p) for p in display_list) if s
        ).encode("latin-1", "replace")
        return self._document(width_mm, height_mm, stream)

    # -- coordinate transform ----------------------------------------------

    def _xy(self, x: float, y: float) -> tuple[float, float]:
        return (x * _K, (self._h - y) * _K)

    def _p(self, x: float, y: float) -> str:
        px, py = self._xy(x, y)
        return f"{fmt(px)} {fmt(py)}"

    # -- per-primitive content-stream ops ----------------------------------

    def _one(self, p) -> str:
        return getattr(self, f"_{type(p).__name__.lower()}")(p)

    def _line(self, p: Line) -> str:
        ops = ["q", *_stroke_setup(p.style),
               f"{self._p(p.x1, p.y1)} m {self._p(p.x2, p.y2)} l S", "Q"]
        return "\n".join(ops)

    def _polyline(self, p: Polyline) -> str:
        if not p.pts:
            return ""
        pts = list(p.pts)
        path = f"{self._p(*pts[0])} m " + " ".join(
            f"{self._p(x, y)} l" for x, y in pts[1:])
        if p.fill:
            color = _STROKE[p.style][0]
            fill = _color_op(_rgb(color), stroke=False)
            ops = ["q", fill, *_stroke_setup(p.style), path + " h B", "Q"]
        else:
            tail = " h S" if p.closed else " S"
            ops = ["q", *_stroke_setup(p.style), path + tail, "Q"]
        return "\n".join(ops)

    def _circle(self, p: Circle) -> str:
        c = _KAPPA * p.r
        cx, cy = p.cx, p.cy
        pts = [(cx + p.r, cy), (cx + p.r, cy + c), (cx + c, cy + p.r),
               (cx, cy + p.r), (cx - c, cy + p.r), (cx - p.r, cy + c),
               (cx - p.r, cy), (cx - p.r, cy - c), (cx - c, cy - p.r),
               (cx, cy - p.r), (cx + c, cy - p.r), (cx + p.r, cy - c),
               (cx + p.r, cy)]
        path = [f"{self._p(*pts[0])} m"]
        for i in range(1, 13, 3):
            path.append(f"{self._p(*pts[i])} {self._p(*pts[i + 1])} "
                        f"{self._p(*pts[i + 2])} c")
        ops = ["q", *_stroke_setup(p.style), " ".join(path) + " S", "Q"]
        return "\n".join(ops)

    def _arc(self, p: Arc) -> str:
        a0 = math.radians(p.start_deg)
        sweep = (p.end_deg - p.start_deg) % 360.0 or 360.0
        n = max(1, math.ceil(sweep / 90.0))
        step = math.radians(sweep) / n
        r = p.r
        path = [f"{self._p(p.cx + r * math.cos(a0), p.cy + r * math.sin(a0))} m"]
        for i in range(n):
            s0 = a0 + step * i
            s1 = s0 + step
            k = (4.0 / 3.0) * math.tan(step / 4.0)
            p1 = (p.cx + r * (math.cos(s0) - k * math.sin(s0)),
                  p.cy + r * (math.sin(s0) + k * math.cos(s0)))
            p2 = (p.cx + r * (math.cos(s1) + k * math.sin(s1)),
                  p.cy + r * (math.sin(s1) - k * math.cos(s1)))
            p3 = (p.cx + r * math.cos(s1), p.cy + r * math.sin(s1))
            path.append(f"{self._p(*p1)} {self._p(*p2)} {self._p(*p3)} c")
        ops = ["q", *_stroke_setup(p.style), " ".join(path) + " S", "Q"]
        return "\n".join(ops)

    def _text(self, p: Text) -> str:
        size_pt = p.size * _K
        color = _STROKE[p.style][0]
        dx = 0.0
        if p.anchor in ("middle", "end"):
            w = _text_width(p.s, size_pt)
            dx = -w / 2.0 if p.anchor == "middle" else -w
        x_pt, y_pt = self._xy(p.x, p.y)
        phi = math.radians(-p.angle)
        c, s = math.cos(phi), math.sin(phi)
        ex, ey = x_pt + dx * c, y_pt + dx * s
        body = _pdf_string(p.s).decode("latin-1")
        ops = ["q", _color_op(_rgb(color), stroke=False), "BT",
               f"/F1 {fmt(size_pt)} Tf",
               f"{fmt(c)} {fmt(s)} {fmt(-s)} {fmt(c)} {fmt(ex)} {fmt(ey)} Tm",
               f"{body} Tj", "ET", "Q"]
        return "\n".join(ops)

    def _rect(self, p: Rect) -> str:
        corners = [(p.x, p.y), (p.x + p.w, p.y), (p.x + p.w, p.y + p.h),
                   (p.x, p.y + p.h)]
        path = (f"{self._p(*corners[0])} m " +
                " ".join(f"{self._p(x, y)} l" for x, y in corners[1:]) + " h")
        ops = ["q"]
        do_fill = p.fill is not None
        do_stroke = p.style is not None
        if do_fill:
            ops.append(_color_op(_fill_rgb(p.fill), stroke=False))
        if do_stroke:
            ops.extend(_stroke_setup(p.style))
        paint = "B" if (do_fill and do_stroke) else "f" if do_fill else \
            "S" if do_stroke else "n"
        ops.append(f"{path} {paint}")
        ops.append("Q")
        return "\n".join(ops)

    def _hatch(self, p: Hatch) -> str:
        # No producer emits Hatch this slice; mirror SvgBackend's fallback and
        # outline each loop so a stray Hatch is visible rather than dropped.
        out = []
        for loop in p.loops:
            pts = list(loop)
            if not pts:
                continue
            path = f"{self._p(*pts[0])} m " + " ".join(
                f"{self._p(x, y)} l" for x, y in pts[1:])
            out.append("\n".join(["q", *_stroke_setup(p.style),
                                  path + " h S", "Q"]))
        return "\n".join(out)

    def _raw(self, p: Raw) -> str:
        # A verbatim SVG fragment cannot be rendered to PDF. Slice 2 converts
        # the one Raw producer (the dimension table) to typed primitives, so
        # this is never reached; it is a no-op guard, not a rendering path.
        return ""

    # -- PDF object assembly (fixed order → deterministic bytes) ------------

    def _document(self, width_mm: float, height_mm: float,
                  stream: bytes) -> bytes:
        w_pt, h_pt = fmt(width_mm * _K), fmt(height_mm * _K)
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w_pt} {h_pt}] "
             f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
             ).encode("latin-1"),
            (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
             b"/Encoding /WinAnsiEncoding >>"),
            (b"<< /Length " + str(len(stream)).encode("latin-1") + b" >>\n"
             b"stream\n" + stream + b"\nendstream"),
        ]
        buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objects, start=1):
            offsets.append(len(buf))
            buf += f"{i} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
        xref_off = len(buf)
        n = len(objects) + 1
        buf += f"xref\n0 {n}\n".encode("latin-1")
        buf += b"0000000000 65535 f \n"
        for off in offsets:
            buf += f"{off:010d} 00000 n \n".encode("latin-1")
        buf += (f"trailer\n<< /Size {n} /Root 1 0 R >>\n"
                f"startxref\n{xref_off}\n%%EOF\n").encode("latin-1")
        return bytes(buf)
