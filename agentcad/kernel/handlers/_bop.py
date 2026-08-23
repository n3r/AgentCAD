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
interesting case: the recheck runs only when the two AABBs overlap by at least
:data:`SUSPECT_OVERLAP_FRACTION` of the smaller box. Then :func:`_disagrees`
crops one operand to each octant of the overlap box and booleans the cropped
pieces against the other operand — by more than the caller's own
``min_volume``, so a tangential-contact sliver is not mistaken for evidence.
The crop is the point: it cuts *through* the tangent junctions, so each piece
is a simpler operand that OCCT handles correctly. If any piece intersects, the
two computations disagree — the whole-solid boolean lied.

**How often that costs anything.** For whole-part pairs, rarely: a near-miss
has little AABB overlap and never reaches the recheck. But
``worker.pairwise_interference`` also calls this from its *crop* branch, where
one operand has already been cut down to the overlap box — a piece whose AABB
sits entirely inside the other solid's, i.e. an overlap fraction of ~1.0. Down
that branch the recheck fires on essentially **every** empty result, and that
branch is the fastener path, the one with the highest pair cardinality. Forcing
:func:`_disagrees` to answer ``True`` visibly drops the interference subscore of
the bench's ``asm_001_thrust_chamber`` and ``asm_003_bolted_joint`` references,
which is how we know it runs there. Measured, the cost is inside run-to-run
noise on those references — but ``handlers/motion.py`` calls
``pairwise_interference`` once **per sweep sample**, so whatever it costs is
multiplied by the sample count, and that has not been measured.

The recheck is a **detector only, never the measurement**: its octant sum is
not a valid intersection volume (the crops overlap on their shared faces and a
piece may itself be booleaned wrong), so a detected pair reports ``0.0`` plus
the ``degenerate`` flag and lets the caller decide how to fail.

**Residual blind spots.** This narrows the hole; it does not close it. Three
false negatives remain, and none of them should be read as covered:

1. A degenerate *plausible-positive* result — OCCT returning some non-zero
   volume that is simply wrong — is not detected at all. Finding it would mean
   re-deriving every intersection by an independent method, which costs more
   than the measurement it checks. The same order-dependence that produces the
   empty answers produces these: two back-to-back evaluations of the *same*
   operand pair in one process have been observed answering "empty" and
   "924.22 mm3".
2. :data:`SUSPECT_OVERLAP_FRACTION` is a **coverage cliff, not only a cost
   gate**. A degenerate pair whose AABBs overlap by less than half the smaller
   box is banked clean exactly as before. The bench elbow pair shifted 40 mm in
   Z already sits at 0.44 — one shift away from the gate — so this is not a
   remote corner.
3. :func:`_disagrees` is one subdivision level of one operand, evaluated by the
   *same* unreliable kernel. It can answer "no disagreement" on a pair that is
   genuinely degenerate, and there is no second opinion behind it.
"""

from __future__ import annotations

import math

import build123d as b3d

#: An empty ``A & B`` is rechecked only when the two AABBs overlap by at least
#: this fraction of the *smaller* box. Whole-part pairs that merely graze —
#: the common legitimate empty — never pay for the recheck. Note that this is a
#: **coverage cliff, not only a cost gate**: see the module docstring.
SUSPECT_OVERLAP_FRACTION = 0.5

#: The default volume below which a rechecked octant is noise rather than a
#: disagreement, used when the caller declares no threshold of its own (the
#: bench ``iou`` path). Deliberately the same 0.001 mm3 that
#: ``worker.pairwise_interference`` defaults ``min_volume`` to. The flip side:
#: a part genuinely smaller than 0.001 mm3 sits under the detector's floor and
#: a degenerate empty against it goes undetected — accepted, since no real
#: part is a sub-(0.1 mm)^3 solid.
DEGENERATE_MIN_VOLUME_MM3 = 0.001

#: A hard floor under the caller's threshold, so a caller asking for
#: ``min_volume=0`` ("report every overlap") does not thereby ask the detector
#: to treat a 1e-12 mm3 tangential sliver as evidence. 1e-9 mm3 is a 1 um cube.
_SLIVER_VOLUME_MM3 = 1e-9

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


def _disagrees(solid_a, box_a, solid_b, box_b, volume_of,
               min_volume: float = DEGENERATE_MIN_VOLUME_MM3) -> bool:
    """Does a cropped recomputation contradict the empty whole-solid boolean?

    *solid_a* is cropped to each octant of the overlap box and every resulting
    piece is booleaned against *solid_b*. The subdivision is what makes this a
    second opinion rather than a repeat: an octant boundary slices through the
    tangent face junctions that confuse ``BRepAlgoAPI_Common``, so each piece
    is an operand OCCT answers correctly. Short-circuits on the first piece
    that intersects; an exception anywhere in here is itself a disagreement —
    a boolean that throws where another said "empty and fine" is not a boolean
    to trust.

    A piece only counts if it intersects by more than *min_volume*. Without
    that floor the detector would be **more** sensitive than the measurement it
    is checking: ``pairwise_interference`` refuses to report a pair below its
    own ``min_volume`` precisely because OCCT leaves 1e-12 mm3 slivers at a
    tangential contact, and a zero-clearance fit — a shaft exactly filling a
    bore, a lip seated in its groove — is both legitimately empty *and* ~100 %
    AABB overlap, so it reaches this function on every check. Firing on a
    sliver there would mean permanent phantom interference on the product path
    and ``clear: False`` on every motion sweep. The threshold is therefore the
    caller's own "too small to matter" line, floored so that an explicit
    ``min_volume=0`` still does not make noise into evidence.
    """
    threshold = max(float(min_volume), _SLIVER_VOLUME_MM3)
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
                if not math.isfinite(volume) or abs(volume) > threshold:
                    return True
    except Exception:  # noqa: BLE001 — a throwing recheck is a failed boolean
        return True
    return False


def checked_common_volume(solid_a, box_a, solid_b, box_b, volume_of,
                          min_volume: float = DEGENERATE_MIN_VOLUME_MM3
                          ) -> tuple[float, bool]:
    """``(volume_mm3, degenerate)`` for one Solid-vs-Solid intersection.

    *box_a*/*box_b* are the operands' already-computed AABBs (both callers have
    them; recomputing costs a kernel call per pair). *min_volume* is the
    caller's own noise floor, below which a rechecked octant is not evidence
    (see :func:`_disagrees`). *volume_of* measures a
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
    return 0.0, _disagrees(solid_a, box_a, solid_b, box_b, volume_of,
                           min_volume)
