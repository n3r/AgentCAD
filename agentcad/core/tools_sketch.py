"""2D constraint-solved sketch tool pack.

Wraps the first-party scipy solver (agentcad.toolkit.sketch) so agents can
solve a constrained sketch to exact coordinates, then feed those into a normal
build123d BuildLine/BuildSketch in a part script.
"""

from __future__ import annotations

from ..toolkit.sketch import SketchError, solve_sketch
from .model import ValidationError
from .tools import Tool, schema

_USAGE = (
    "Provide rough starting coordinates in the shape you want — the solver "
    "converges to the nearest solution, so a mirrored initial guess yields a "
    "mirrored result. Constraint types: fixed, coincident, distance, "
    "distance_x, distance_y, horizontal, vertical, parallel, perpendicular, "
    "angle, point_on_line, point_on_circle, radius, equal_radius, midpoint, "
    "tangent_line_circle, tangent_circles."
)


def register(registry, service) -> None:
    def solve(entities: dict, constraints: list, initial: dict | None = None) -> dict:
        spec = {
            "points": entities.get("points", []),
            "lines": entities.get("lines", []),
            "circles": entities.get("circles", []),
            "constraints": constraints,
        }
        try:
            result = solve_sketch(spec)
        except SketchError as exc:
            raise ValidationError(str(exc)) from exc
        if not result["ok"]:
            raise ValidationError(
                f"sketch did not converge (max residual {result['max_residual']:.2e}; "
                f"dof {result['dof']}); check for over/under-constraint or a bad "
                f"initial guess. {_USAGE}",
                {"max_residual": result["max_residual"], "dof": result["dof"]},
            )
        return result

    registry.register(Tool(
        "solve_sketch",
        "Solve a 2D constrained sketch to exact coordinates you can feed into "
        "build123d BuildLine/BuildSketch. " + _USAGE,
        schema(
            {
                "entities": {"type": "object", "description":
                             "{points:[{name,x,y,fixed?}], lines:[{name,p1,p2}], "
                             "circles:[{name,center,r,fixed_r?}]}"},
                "constraints": {"type": "array", "description":
                                "[{type, ...kwargs}] — see tool description"},
                "initial": {"type": "object", "description": "unused; reserved"},
            },
            ["entities", "constraints"],
        ),
        solve,
    ))
