"""Sheet templates as data (PRD-014 Drawings v2, Decision 2 — FR1).

A versioned ``SHEETS`` table: each format maps to a :class:`SheetTemplate`
carrying the sheet size, the frame inset, and four :class:`Zone` rectangles the
drawing handler draws its frame and lays its content into — no hard-coded
coordinates in the handler anymore. All landscape; default ``iso_a3`` (420×297,
byte-preserving the pre-v2 sheet size).

**Pure data + geometry. No OCP / build123d import** — the whole point of the
split is that a sheet layout is arithmetic, not a kernel object, so this module
(and the primitives it lays out) stay importable anywhere.

Zones are derived from the sheet size by one formula so every format is
consistent, and ``iso_a3`` reproduces the previous layout exactly: frame inset
6 mm, a 150×28 title block in the bottom-right corner, and a 150 mm-wide table
column whose top-left is (264, 18) — the clear rectangle the dimension table
already used.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Zone:
    """A rectangle on the sheet, in mm, y-down. ``x, y`` is the top-left."""

    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0


@dataclass(frozen=True)
class SheetTemplate:
    format: str
    w_mm: float
    h_mm: float
    margin: float
    frame_inset: float
    title_block: Zone     # bottom-right
    revision_block: Zone  # top-right (empty until PRD-015)
    table_zone: Zone      # right column, for hole/config tables
    view_area: Zone       # remaining area for projected views


#: Fixed title-block / column geometry, in mm, shared by every format so the
#: block a reader learns on an A4 is where they expect it on an A0. These are
#: exactly the pre-v2 A3 numbers.
_TB_W, _TB_H = 150.0, 28.0
_REV_H = 12.0            # revision block height, top-right
_TABLE_TOP_GAP = 12.0   # table column starts this far below the frame inset


def _template(fmt: str, w: float, h: float, *, margin: float = 10.0,
              inset: float = 6.0) -> SheetTemplate:
    """Build a template from the sheet size by the shared layout formula.

    Right column (title block at the bottom, revision block at the top, the
    table zone filling the gap between them); the view area is everything left
    of that column, inside the frame.
    """
    right_x = w - inset - _TB_W
    title_block = Zone(right_x, h - inset - _TB_H, _TB_W, _TB_H)
    revision_block = Zone(right_x, inset, _TB_W, _REV_H)
    table_top = inset + _TABLE_TOP_GAP
    table_zone = Zone(right_x, table_top, _TB_W,
                      max(0.0, title_block.y - 4.0 - table_top))
    view_left = inset + 2.0
    view_area = Zone(view_left, inset + 2.0,
                     max(0.0, right_x - 2.0 - view_left),
                     max(0.0, h - 2.0 * (inset + 2.0)))
    return SheetTemplate(fmt, w, h, margin, inset, title_block,
                         revision_block, table_zone, view_area)


#: All landscape (w >= h). ISO sizes exact; ANSI sizes are the inch dimensions
#: converted to mm (25.4 mm/in).
SHEETS: dict[str, SheetTemplate] = {
    "iso_a4": _template("iso_a4", 297.0, 210.0),
    "iso_a3": _template("iso_a3", 420.0, 297.0),
    "iso_a2": _template("iso_a2", 594.0, 420.0),
    "iso_a1": _template("iso_a1", 841.0, 594.0),
    "iso_a0": _template("iso_a0", 1189.0, 841.0),
    "ansi_a": _template("ansi_a", 279.4, 215.9),   # 11 x 8.5 in
    "ansi_b": _template("ansi_b", 431.8, 279.4),   # 17 x 11 in
    "ansi_c": _template("ansi_c", 558.8, 431.8),   # 22 x 17 in
    "ansi_d": _template("ansi_d", 863.6, 558.8),   # 34 x 22 in
}

#: The default and the only pre-v2 size.
DEFAULT_SHEET = "iso_a3"

#: Preferred scale ratios (drawing scale = drawn/real), largest first (FR1).
#: 2:1 is ratio 2.0, 1:2 is 0.5, etc.
SCALE_LADDER = [100.0, 50.0, 20.0, 10.0, 5.0, 2.0, 1.0,
                0.5, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005]


def scale_label(ratio: float) -> str:
    """A ladder ratio as an engineering scale string: ``2.0 -> "2:1"``,
    ``1.0 -> "1:1"``, ``0.5 -> "1:2"``, ``0.02 -> "1:50"``."""
    if ratio >= 1.0:
        n = int(round(ratio))
        return f"{n}:1"
    return f"1:{int(round(1.0 / ratio))}"
