"""Tool pack: the ISO/ANSI hole standards, as data an agent or a dialog can read.

`hole_standards {family?, size?, std?}` answers out of the vendored tables in
`agentcad/toolkit/hole_standards.py` — no kernel call, no geometry, no OCP —
so a UI size picker and a part script use the same numbers the drilled hole
used. It is registered **unconditionally**: a pure-data tool can always run.

This pack loads at `h`, which is **before** `tools_proposals` (`p`),
`tools_specs` (`s`) and `tools_versioning` (`v`). It therefore must never read
`service.branches` / `service.specs` / `service.gate_providers` in
`register()`; when later slices add the rebuild seam here they read those
inside methods. Today it reads nothing off `service` at all, and
`tests/test_hole_standards.py` pins that.
"""

from __future__ import annotations

from .model import ValidationError
from .tools import Tool, schema

DESCRIPTION = (
    "Look up ISO hole standards: clearance diameters (ISO 273, fine/medium/"
    "coarse — also spelled close/normal/loose), thread pitch and tap drill "
    "(ISO 261/262), and counterbore/countersink geometry from the fastener "
    "head standards (ISO 4762, ISO 10642). Omit everything to list the "
    "families and their tabulated sizes; give family+size for the row. "
    "Clearance returns all three fits at once. Every answer carries the "
    "standard, the revision and the two published sources it was transcribed "
    "from. Counterbore answers name the head dimensions (the standard part) "
    "separately from the bore (a documented shop clearance rule, since the "
    "published counterbore charts disagree). Data only — it drills nothing."
)


def register(registry, service) -> None:
    # `service` is deliberately unused: this pack is pure data, and reading a
    # cross-pack seam here would run before the pack that installs it.
    def hole_standards(family: str | None = None, size: str | None = None,
                       std: str | None = None) -> dict:
        from agentcad.toolkit import hole_standards as tables
        try:
            return tables.lookup(family=family, size=size,
                                 std=(std or "iso"))
        except ValueError as exc:
            # A bad argument is a caller error, not a 500: the registry maps
            # AppError subclasses to structured payloads, and ValueError would
            # otherwise escape into the server.
            raise ValidationError(str(exc)) from exc

    registry.register(Tool(
        "hole_standards",
        DESCRIPTION,
        schema(
            {
                "family": {"type": "string",
                           "description": "clearance | tapped | counterbore | "
                                          "countersink (cbore/csk/thread also "
                                          "accepted)"},
                "size": {"type": "string",
                         "description": "Metric designation, e.g. M5"},
                "std": {"type": "string", "description": "iso (default) | ansi"},
            },
            [],
        ),
        hole_standards,
    ))
