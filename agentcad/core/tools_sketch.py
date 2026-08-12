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
from .sketch_emit import EmitError
from .sketch_emit import emit as emit_code
from .tools import Tool, schema

_USAGE = (
    "Provide rough starting coordinates in the shape you want — the solver "
    "converges to the nearest solution, so a mirrored initial guess yields a "
    "mirrored result. Constraint types: fixed, coincident, distance, "
    "distance_x, distance_y, horizontal, vertical, parallel, perpendicular, "
    "angle, point_on_line, point_on_circle, radius, equal_radius, midpoint, "
    "tangent_line_circle, tangent_circles, tangent, symmetric, equal_length, "
    "concentric."
)

_ARCS = (
    "Arcs are authored either centre-form ({name, center: <point>, r, "
    "start_deg, end_deg, fixed_r?}) or 3-point ({name, start: [x,y], mid: "
    "[x,y], end: [x,y]}, compiled to the centre form at ingestion). Angles "
    "are **degrees, counter-clockwise**, and an arc adds exactly three "
    "parameters — r, start, end — plus the two its centre point costs. Its "
    "endpoints are the **virtual handles** `<name>.start` and `<name>.end`: "
    "write them anywhere a point name goes (`{type: coincident, p: "
    "\"arc1.end\", q: \"p3\"}`) and the whole point vocabulary applies to "
    "them for free. `radius`, `equal_radius`, `point_on_circle` and both v1 "
    "tangency types accept an arc wherever they accept a circle. Entity names "
    "may not contain a '.' — that namespace belongs to handles and compiled "
    "sub-entities. Reported angles are normalized so start is in [0, 360) and "
    "end carries the full signed sweep."
)

_NEW_CONSTRAINTS = (
    "`tangent {a, b, at?, kind?}` is one constraint dispatched on what a and b "
    "are: line+circle/arc (1 row, or 3 when `at` names the tangency point), or "
    "curve+curve (1 row, kind external|internal). `symmetric {a, b, about}` "
    "mirrors two points — or two lines, endpoint-for-endpoint in declaration "
    "order — about a line, in **two rows**: the midpoint lies on the axis and "
    "the pair is perpendicular to it. `equal_length {l1, l2}` and `concentric "
    "{a, b}` (2 rows, centre coincidence) complete the set. The v1 names "
    "`tangent_line_circle` and `tangent_circles` keep working unchanged — "
    "`tangent` is a new front door, not a rename."
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
    "Optional warm start: {points:{name:{x,y}}, circles:{name:{r}}, "
    "arcs:{name:{r,start_deg,end_deg}}, slots:{name:{r}}}. It seeds "
    "the starting coordinates only — it cannot fix a point, override fixed_r "
    "or introduce an entity — so its job is to **select the solution branch** "
    "(which side of a mirror pair you land on), not to make the solve faster. "
    "An unknown entity name is an error; an `initial` that does not cover "
    "every free entity degrades to a cold start with warm_started: false and "
    "an `initial_incomplete` warning."
)


_SPLINES_AND_SLOTS = (
    "A **spline** is an ordered list of named points, degree 3, non-periodic, "
    "and it owns no parameters: its points are ordinary points, so every "
    "point constraint works on them. It emits as build123d `Spline`, which "
    "interpolates them (measured to 7.1e-15 mm, well inside the emission "
    "tolerance), so the solved coordinates *are* the curve's. Constraints "
    "apply to the control points and to the **end tangents** (`{type: "
    "tangent, a: \"sp1.start\", b: \"ln4\"}`, a direction residual against "
    "the first control-polygon leg); **on-curve point constraints are out of "
    "scope** — say what you mean with a control point. A **slot** {name, c1, "
    "c2, width} compiles at ingestion into two arcs and two lines with **one "
    "shared radius** and structural junctions, so it contributes exactly five "
    "rows: radius = width/2 and four tangencies. Its sub-entities are named "
    "`<name>.arc_a`, `<name>.arc_b`, `<name>.side_1`, `<name>.side_2`; you "
    "may reference them in constraints but not declare them, and a diagnostic "
    "reports the slot with `origin: \"slot:<name>\"` and `index: null` "
    "rather than blaming a constraint you did not write. `initial` seeds a "
    "slot by its radius alone (`slots: {name: {r}}`) — the caps' angles are "
    "re-derived from the seeded centres."
)


_DRAG = (
    "Optional drag frame: {point, x, y, weight?} pulls `point` (any point "
    "name or virtual handle) toward the cursor. It is an **objective, not a "
    "constraint** — excluded from `ok`, `max_residual`, `n_residuals`, `rank`, "
    "`dof` and `diagnostics`, all of which describe the constraint rows alone; "
    "the pull's own slack is reported as `drag.gap`. Default weight 0.05, "
    "measured. Send it with `initial` seeded from the **previous frame's "
    "solution**, never from the cursor: seeding the dragged point at the "
    "cursor is what causes a mirror-branch flip when the cursor crosses the "
    "boundary, and the weak pull is what prevents one. Dragging a "
    "fully-constrained entity moves it (almost) not at all — that is correct, "
    "and the old behaviour that looked responsive was it teleporting to "
    "another solution."
)

_DIAGNOSTICS_MODE = (
    "Optional: 'auto' (default) computes the diagnostics block, except on a "
    "drag frame, where the constraint set cannot have changed and the cached "
    "block is served; 'full' always recomputes; 'cached' prefers the cache. "
    "The cache is keyed on the compiled residual structure and the constraint "
    "targets, so any constraint edit invalidates it. `diagnostics_source` in "
    "the result says which you got — a cached block reports the "
    "`analysis_ms` of the solve that produced it."
)

_EMIT = (
    "Optional: also return idiomatic build123d source for the solved sketch, "
    "as `emit: {code, warnings, style}`. `\"function\"` wraps it in a "
    "`sketch_profile()` you call from `build(p)`; `\"buildline\"` is the bare "
    "`with BuildSketch(...)` block. Omit it (or send null/false) to skip "
    "emission. The GUI and agents share **this** emitter, so the same spec "
    "produces byte-identical code either way. Curves are anchored on their "
    "shared solved endpoints at 9 decimals behind a 1e-8 mm closure gate: "
    "measured, a centre-parametrized arc chain at 6 decimals leaves a 7.58e-7 "
    "mm gap and `make_face()` refuses it, so an emission that would not "
    "rebuild is a validation_error naming the junction rather than code you "
    "find out about later."
)


def register(registry, service) -> None:
    def solve(entities: dict, constraints: list, initial: dict | None = None,
              emit: str | bool | None = None, drag: dict | None = None,
              diagnostics: str | None = None) -> dict:
        spec = {
            "points": entities.get("points", []),
            "lines": entities.get("lines", []),
            "circles": entities.get("circles", []),
            "arcs": entities.get("arcs", []),
            "splines": entities.get("splines", []),
            "slots": entities.get("slots", []),
            "constraints": constraints,
            "initial": initial,
            "drag": drag,
            "diagnostics": diagnostics,
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
                # `index` is None for a constraint compiled from an entity (a
                # slot's internal machinery): there is no entry of
                # `constraints` to point at, so name the origin instead.
                (f"#{c['index']} {c['type']}" if c.get("index") is not None
                 else f"{c['type']} compiled from {c['origin']}")
                + (f" (from {c['origin']})"
                   if c.get("origin") and c.get("index") is not None else "")
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
        if emit:
            # After the conflict/convergence gates: emitting code for a sketch
            # that did not solve would be emitting the wrong geometry.
            try:
                result["emit"] = emit_code(
                    result, spec, style="function" if emit is True else emit)
            except EmitError as exc:
                raise ValidationError(str(exc), details) from exc
        return result

    registry.register(Tool(
        "solve_sketch",
        "Solve a 2D constrained sketch to exact coordinates you can feed into "
        "build123d BuildLine/BuildSketch. " + _USAGE + " " + _ARCS + " "
        + _SPLINES_AND_SLOTS + " " + _NEW_CONSTRAINTS + " " + _DIAGNOSTICS
        + " " + _DRAG + " " + _DIAGNOSTICS_MODE + " " + _EMIT,
        schema(
            {
                "entities": {"type": "object", "description":
                             "{points:[{name,x,y,fixed?}], lines:[{name,p1,p2}], "
                             "circles:[{name,center,r,fixed_r?}], "
                             "arcs:[{name,center,r,start_deg,end_deg,fixed_r?}], "
                             "splines:[{name,points:[<point names>]}], "
                             "slots:[{name,c1,c2,width}]}"
                             + " " + _ARCS + " " + _SPLINES_AND_SLOTS},
                "constraints": {"type": "array", "description":
                                "[{type, ...kwargs}] — see tool description"},
                "initial": {"type": "object", "description": _INITIAL},
                "drag": {"type": "object", "description": _DRAG},
                "diagnostics": {"type": "string",
                                "description": _DIAGNOSTICS_MODE},
                "emit": {"type": "string", "description": _EMIT},
            },
            ["entities", "constraints"],
        ),
        solve,
    ))
