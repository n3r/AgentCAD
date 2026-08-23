"""Degenerate-boolean detection for solid intersections (OCCT 7.9).

Underscore-prefixed, so ``worker._load_handler_packs`` skips it during pack
discovery — this is a shared helper, not a handler pack (the ``_pdf.py``
precedent).

**The measured root cause.** ``BRepAlgoAPI_Common`` (build123d's ``&``) is not
reliable when *both* operands carry G1-tangent face junctions — a cylinder
flowing into a torus flowing into a cylinder, i.e. any swept solid with a
filleted centre line. The failure is silent: ``IsDone()`` is ``True``, no
error is raised, and this OCP build exposes no ``HasErrors()``. What comes
back is an **empty** shape, a **whole-operand** shape, or (rarely) a shape
whose ``volume`` is **negative**. Measured on the bench's coolant elbow
(``fix_005``, a Ø24×3 annulus swept along a 24 mm-radius right-angle bend,
21 711.685 mm3): two *coincident* copies of that solid — the same geometry,
one built from the script and one round-tripped through STEP — intersect to
**nothing**, where the truth is the whole 21 711.685 mm3. The STEP round trip
is *not* the trigger (STEP ⊗ STEP is degenerate too — changelog 0282:214-223
measured that correctly); differing operand provenance merely stops OCCT
taking the "same shape" shortcut that hid the bug. The same lie shows up on
shifted pairs, where it is *order-dependent*: an earlier boolean in the same
process changes whether the next one answers truthfully.

No healing recipe fixes it. Fuzzy booleans at every tolerance, ``ShapeFix``,
``UnifySameDomain``, sewing, ``Copy``, ``Glue`` and OBB alignment were all
probed and all fail. So the answer is **detection and honest degradation**,
never a silent zero: the bench turns a degenerate pair into a ``status:
"error"`` subscore (excluded, FR7) and the product path reports the pair as
interfering with ``degenerate: true`` (fail-closed).

**How the detector works.** A *positive* volume is trusted. A *negative* one
is impossible and is reported degenerate outright (the old ``max(v, 0.0)``
clamp laundered exactly this into a clean zero). An *empty* result is the
interesting case: it is legitimate for the overwhelming majority of pairs — a
near-miss whose AABBs merely graze — so the recheck runs only when the two
AABBs overlap by at least :data:`SUSPECT_OVERLAP_FRACTION` of the smaller box.
Then :func:`_disagrees` crops one operand to each octant of the overlap box and
booleans the cropped pieces against the other operand. The crop is the point:
it cuts *through* the tangent junctions, so each piece is a simpler operand
that OCCT handles correctly. If any piece intersects, the two computations
disagree — the whole-solid boolean lied.

The recheck is a **detector only, never the measurement**: its octant sum is
not a valid intersection volume (the crops overlap on their shared faces and a
piece may itself be booleaned wrong), so a detected pair reports ``0.0`` plus
the ``degenerate`` flag and lets the caller decide how to fail.

**Residual blind spot.** A degenerate *plausible-positive* result — OCCT
returning some non-zero volume that is simply wrong — is not detected. Finding
it would mean re-deriving the intersection by an independent method for every
pair, which costs more than the measurement it checks. What is covered is the
empty-and-should-not-be case (the false "clean" / false IoU 0) and the
negative case; a wrong-but-positive volume still passes through.
"""

from __future__ import annotations

import math

import build123d as b3d

#: An empty ``A & B`` is rechecked only when the two AABBs overlap by at least
#: this fraction of the *smaller* box. Near-miss pairs — the common legitimate
#: empty, and the bulk of any assembly's pair list — never pay for the recheck.
SUSPECT_OVERLAP_FRACTION = 0.5

#: Overlap-box edges below this (mm) are treated as degenerate and skipped when
#: subdividing: a zero-thickness ``Box`` is not a boolean operand.
_MIN_EDGE_MM = 1e-6


def _box_volume(bb) -> float:
    return (max(bb.max.X - bb.min.X, 0.0) * max(bb.max.Y - bb.min.Y, 0.0)
            * max(bb.max.Z - bb.min.Z, 0.0))


def _overlap_extent(box_a, box_b) -> tuple[list[float], list[float]]:
    """The AABB intersection as ``(lo, hi)``; ``hi <= lo`` on a missing axis."""
    lo = [max(box_a.min.X, box_b.min.X), max(box_a.min.Y, box_b.min.Y),
          max(box_a.min.Z, box_b.min.Z)]
    hi = [min(box_a.max.X, box_b.max.X), min(box_a.max.Y, box_b.max.Y),
          min(box_a.max.Z, box_b.max.Z)]
    return lo, hi


def _suspicious(box_a, box_b) -> bool:
    """True when an empty intersection is surprising enough to recheck.

    A zero-volume AABB (a zero-thickness solid) is never suspicious: there is
    no denominator to measure the overlap against.
    """
    smaller = min(_box_volume(box_a), _box_volume(box_b))
    if smaller <= 0.0:
        return False
    lo, hi = _overlap_extent(box_a, box_b)
    overlap = 1.0
    for axis in range(3):
        overlap *= max(hi[axis] - lo[axis], 0.0)
    return overlap >= SUSPECT_OVERLAP_FRACTION * smaller


def _crop(solid, lo: list[float], hi: list[float]):
    """*solid* cropped to the axis-aligned box ``[lo, hi]``, or ``None``."""
    size = [hi[axis] - lo[axis] for axis in range(3)]
    if any(edge <= _MIN_EDGE_MM for edge in size):
        return None
    centre = [(hi[axis] + lo[axis]) / 2 for axis in range(3)]
    box = b3d.Pos(*centre) * b3d.Box(*size)
    piece = solid & box
    if piece is None or getattr(piece, "wrapped", None) is None:
        return None
    return piece


def _disagrees(solid_a, box_a, solid_b, box_b, volume_of) -> bool:
    """Does a cropped recomputation contradict the empty whole-solid boolean?

    *solid_a* is cropped to each octant of the overlap box and every resulting
    piece is booleaned against *solid_b*. The subdivision is what makes this a
    second opinion rather than a repeat: an octant boundary slices through the
    tangent face junctions that confuse ``BRepAlgoAPI_Common``, so each piece
    is an operand OCCT answers correctly. Short-circuits on the first piece
    that intersects; an exception anywhere in here is itself a disagreement —
    a boolean that throws where another said "empty and fine" is not a boolean
    to trust.
    """
    lo, hi = _overlap_extent(box_a, box_b)
    mid = [(lo[axis] + hi[axis]) / 2 for axis in range(3)]
    try:
        for corner in range(8):
            sub_lo = [lo[axis] if not (corner >> axis) & 1 else mid[axis]
                      for axis in range(3)]
            sub_hi = [mid[axis] if not (corner >> axis) & 1 else hi[axis]
                      for axis in range(3)]
            piece = _crop(solid_a, sub_lo, sub_hi)
            if piece is None:
                continue
            for part in (piece.solids() or [piece]):
                common = part & solid_b
                if common is None or getattr(common, "wrapped", None) is None:
                    continue
                volume = float(volume_of(common))
                if not math.isfinite(volume) or volume != 0.0:
                    return True
    except Exception:  # noqa: BLE001 — a throwing recheck is a failed boolean
        return True
    return False


def checked_common_volume(solid_a, box_a, solid_b, box_b,
                          volume_of) -> tuple[float, bool]:
    """``(volume_mm3, degenerate)`` for one Solid-vs-Solid intersection.

    *box_a*/*box_b* are the operands' already-computed AABBs (both callers have
    them; recomputing costs a kernel call per pair). *volume_of* measures a
    boolean result — ``worker._shape_volume``'s solids sum, never ``.volume``,
    because a boolean result is routinely a nested Compound.

    ``degenerate`` means **the intersection could not be computed**, not "the
    solids overlap". The volume is then ``0.0`` and carries no information: the
    caller must fail closed on the flag, never bank the zero.
    """
    common = solid_a & solid_b
    if common is not None and getattr(common, "wrapped", None) is not None:
        volume = float(volume_of(common))
        if not math.isfinite(volume):
            # A volume OCCT could not compute. Every comparison against NaN is
            # false, so this used to walk through both guards below and be
            # banked as a clean pair.
            return 0.0, True
        if volume < 0.0:
            # Never legitimate. Reported as-was by the product path and clamped
            # to 0.0 by the bench path, which laundered it into a clean zero.
            return 0.0, True
        if volume > 0.0:
            return volume, False
    if not _suspicious(box_a, box_b):
        return 0.0, False
    return 0.0, _disagrees(solid_a, box_a, solid_b, box_b, volume_of)
