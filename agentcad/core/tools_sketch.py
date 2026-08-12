"""2D constraint-solved sketch tool pack.

Wraps the first-party scipy solver (agentcad.toolkit.sketch) so agents can
solve a constrained sketch to exact coordinates, then feed those into a normal
build123d BuildLine/BuildSketch in a part script.

**`over_constrained` is not an error; unsatisfiable is.** A redundant but
consistent constraint set solves fine and comes back `ok: true` with
`diagnostics.redundant` listing what was ignored — every incumbent sketcher
tells you about a duplicate and carries on. Only a non-empty
`diagnostics.conflicting` (equivalently `max_residual > 1e-7`) raises, and it
raises with the whole diagnostics block attached so an agent can act on it.
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

_DIAGNOSTICS = (
    "Every result carries a `diagnostics` block: {status: well_constrained|"
    "under_constrained|over_constrained|did_not_converge, dof, rank, "
    "n_params, n_residuals, free_entities, redundant, conflicting, "
    "analysis_ms, analysis_complete}. `dof` is n_params - rank(J), so it is "
    "never negative; `free_entities` names the entities an under-constrained "
    "sketch can still move. `redundant` and `conflicting` are *a* dependent "
    "set, not necessarily the unique culprit: removing any one member "
    "resolves the dependency, and the member that gets named is chosen by "
    "declaration order — the later constraint is blamed, on the heuristic "
    "that it is the one you just added, so a spec submitted in arbitrary "
    "order gets an arbitrary (but still correct) member. A redundant but "
    "consistent constraint is not an error; a conflicting one is. When the "
    "analysis runs out of its time budget, `analysis_complete` is false and "
    "the two sets are omitted rather than reported as empty."
)

_INITIAL = (
    "Optional warm start: {points:{name:{x,y}}, circles:{name:{r}}}. It seeds "
    "the starting coordinates only — it cannot fix a point, override fixed_r "
    "or introduce an entity — so its job is to **select the solution branch** "
    "(which side of a mirror pair you land on), not to make the solve faster. "
    "An unknown entity name is an error; an `initial` that does not cover "
    "every free entity degrades to a cold start with warm_started: false and "
    "an `initial_incomplete` warning."
)


def register(registry, service) -> None:
    def solve(entities: dict, constraints: list, initial: dict | None = None) -> dict:
        spec = {
            "points": entities.get("points", []),
            "lines": entities.get("lines", []),
            "circles": entities.get("circles", []),
            "constraints": constraints,
            "initial": initial,
        }
        try:
            result = solve_sketch(spec)
        except SketchError as exc:
            raise ValidationError(str(exc)) from exc

        diag = result["diagnostics"]
        details = {"diagnostics": diag,
                   "max_residual": result["max_residual"],
                   "dof": result["dof"]}
        conflicting = diag.get("conflicting") or []
        if conflicting:
            named = ", ".join(
                f"#{c['index']} {c['type']}"
                + (f" (from {c['origin']})" if c.get("origin") else "")
                for c in conflicting)
            raise ValidationError(
                f"sketch is over-constrained and cannot be satisfied (max "
                f"residual {result['max_residual']:.2e}): {named} conflicts "
                "with the constraints declared before it. That is *a* "
                "dependent set, not necessarily the unique culprit — removing "
                "any one member of it resolves the conflict.",
                details,
            )
        if not result["ok"]:
            raise ValidationError(
                f"sketch did not converge (max residual "
                f"{result['max_residual']:.2e}; dof {result['dof']}). No "
                "dependent constraint set was found, so this is a solver "
                "failure rather than a contradiction between two constraints: "
                f"check the starting coordinates and the targets. {_USAGE}",
                details,
            )
        return result

    registry.register(Tool(
        "solve_sketch",
        "Solve a 2D constrained sketch to exact coordinates you can feed into "
        "build123d BuildLine/BuildSketch. " + _USAGE + " " + _DIAGNOSTICS,
        schema(
            {
                "entities": {"type": "object", "description":
                             "{points:[{name,x,y,fixed?}], lines:[{name,p1,p2}], "
                             "circles:[{name,center,r,fixed_r?}]}"},
                "constraints": {"type": "array", "description":
                                "[{type, ...kwargs}] — see tool description"},
                "initial": {"type": "object", "description": _INITIAL},
            },
            ["entities", "constraints"],
        ),
        solve,
    ))
