"""Threads and fasteners via bd_warehouse (Apache-2.0).

Real ISO thread geometry is exact but heavy (~9k triangles for an M8 screw at
tolerance 0.1) and can be slow to construct in some paths — notably
``bd_warehouse.fastener.ThreadedHole(simple=False)`` takes ~15 s and does NOT
auto-insert the thread. Prefer the wrappers here.

Guidance for agents:
  * Assembly previews / fit checks: use simple (cosmetic) threads — fast, light.
  * Manufacturing drawings / real mating: use real threads (external=True).
  * To tap a hole: bore a hole at ``internal_thread(...).min_radius`` (the tap
    drill), then fuse the internal thread — ``tapped_hole_thread`` gives you
    that thread solid ready to add.
"""

from __future__ import annotations

from bd_warehouse.thread import IsoThread

# Rough triangle budget at mesh tolerance 0.1 (from the validation spike):
# a real M8x20 thread ~9k tris vs ~1k cosmetic — keep real threads for parts
# that need them.
REAL_THREAD_TRIANGLES_HINT = "~9k triangles per real thread at tolerance 0.1"


def external_thread(diameter: float, pitch: float, length: float,
                    end_finishes=("fade", "fade")):
    """An external ISO thread solid (e.g. for a stud/bolt shank). Fuse it onto
    a cylinder of radius ``thread.min_radius``."""
    return IsoThread(diameter, pitch, length, external=True,
                     end_finishes=end_finishes)


def internal_thread(diameter: float, pitch: float, length: float,
                    end_finishes=("fade", "fade")):
    """An internal ISO thread solid (for tapping a hole). Bore a hole at
    ``.min_radius`` (the tap-drill size) then fuse this in."""
    return IsoThread(diameter, pitch, length, external=False,
                     end_finishes=end_finishes)


def threaded_rod(diameter: float, pitch: float, length: float):
    """A ready-to-use externally threaded rod (thread fused onto its core
    cylinder). Returns a build123d Part."""
    from build123d import Align, Cylinder, Pos

    thread = external_thread(diameter, pitch, length)
    core = Pos(0, 0, length / 2) * Cylinder(
        radius=thread.min_radius, height=length,
        align=(Align.CENTER, Align.CENTER, Align.CENTER),
    )
    return core.fuse(thread)


def tapped_hole_thread(diameter: float, pitch: float, depth: float):
    """The internal thread solid for a tapped hole of the given depth. Usage::

        thr = tapped_hole_thread(8, 1.25, 12)
        with BuildPart() as part:
            Box(40, 40, 15)
            with Locations(part.faces().sort_by(Axis.Z)[-1]):
                Hole(radius=thr.min_radius, depth=12)   # tap-drill
            add(thr)                                     # fuse the thread in
    """
    return internal_thread(diameter, pitch, depth)


def cap_screw(size: str = "M8-1.25", length: float = 20.0, simple: bool = False):
    """A socket-head cap screw (ISO 4762). simple=True is a cosmetic thread
    (fast, light); simple=False is real thread geometry."""
    from bd_warehouse.fastener import SocketHeadCapScrew

    return SocketHeadCapScrew(size=size, length=length, simple=simple)


def hex_bolt(size: str = "M8-1.25", length: float = 20.0, simple: bool = False):
    """A hex-head bolt. simple as for cap_screw."""
    from bd_warehouse.fastener import HexBolt

    return HexBolt(size=size, length=length, simple=simple)
