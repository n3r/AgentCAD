"""ERROR DOCTOR — catalog of real OCCT / build123d 0.11.1 failure signatures.

Every entry was actually triggered on build123d 0.11.1 / OCP 7.8.x (macOS
arm64); see test_error_doctor.py which triggers each one and proves the regex
matches.

Usage (worker-side, agentcad/kernel/worker.py):

    from agentcad.toolkit.error_doctor import diagnose
    try:
        exec_user_script()
    except Exception as e:
        entry = diagnose(e)          # or diagnose_text(type_name, msg, tb)
        details["hint"] = entry and f"{entry['diagnosis']} Fix: {entry['fix']}"

Each entry:
    id        - stable slug for telemetry
    regex     - matched (re.search) against "<ExcType>: <message>\\n<traceback>"
                (?s) multi-line lookaheads used where message alone is ambiguous
    diagnosis - plain-language what-went-wrong
    fix       - concrete next action for the agent
Order matters: first match wins; specific entries precede generic ones.
"""
from __future__ import annotations

import re
import traceback as _traceback

ERROR_DOCTOR: list[dict[str, str]] = [
    {
        "id": "fillet_edges_not_on_part",
        "regex": r"no suitable edges for chamfer or fillet",
        "diagnosis": (
            "None of the edges passed to fillet/chamfer belong to the solid "
            "being modified (stale edge references from a previous feature, or "
            "edges of another part)."
        ),
        "fix": (
            "Re-select edges from the CURRENT part state, e.g. "
            "part.edges().filter_by(...) immediately before the fillet call — "
            "edge references are invalidated by every operation."
        ),
    },
    {
        "id": "fillet_radius_too_large",
        "regex": r"Failed creating a fillet with radius",
        "diagnosis": (
            "The fillet radius is too large for at least one selected edge — "
            "the rolling ball does not fit (radius exceeds adjacent wall/half-"
            "thickness, or neighboring fillets collide)."
        ),
        "fix": (
            "Reduce the radius below the smallest adjacent feature size, fillet "
            "fewer edges per call, or use toolkit.safe_fillet() which finds the "
            "largest working radius automatically."
        ),
    },
    {
        "id": "chamfer_too_large",
        "regex": r"Failed creating a chamfer",
        "diagnosis": (
            "The chamfer length is too large for at least one selected edge "
            "(it would consume an adjacent face)."
        ),
        "fix": "Reduce the chamfer length or chamfer fewer edges per call.",
    },
    {
        "id": "face_from_open_wire",
        "regex": r"Face can only be created with closed wires",
        "diagnosis": (
            "make_face received an open wire — the sketch outline does not "
            "close back on its start point."
        ),
        "fix": (
            "Close the profile: end the last segment exactly at the start "
            "point, or use Line(last, first) / mirror + make_hull. Check for "
            "tiny gaps from rounding of coordinates."
        ),
    },
    {
        "id": "degenerate_sketch_face",
        "regex": r"Cannot build face\(s\): wires not planar",
        "diagnosis": (
            "The sketch wire is degenerate (zero-size shape such as Circle(0) "
            "or Rectangle with a zero side) or genuinely non-planar, so no "
            "face can be built."
        ),
        "fix": (
            "Ensure all sketch dimensions are positive and all points of a "
            "profile lie on one plane; check parameter values that may "
            "evaluate to 0."
        ),
    },
    {
        "id": "wire_edges_disconnected",
        "regex": r"Edges are disconnected",
        "diagnosis": (
            "The edges given to Wire/make_face do not connect end-to-end — "
            "there is a gap between consecutive segments."
        ),
        "fix": (
            "Make each segment start exactly where the previous ended (reuse "
            "the previous end point variable instead of retyping coordinates), "
            "or pass sequenced=True to Wire to let it reorder edges."
        ),
    },
    {
        "id": "offset_failed_alternative_kind",
        "regex": r"offset Error, an alternative kind may resolve this error",
        "diagnosis": (
            "offset()/shell failed with the current corner treatment "
            "(Kind.ARC by default). Typical on bodies with tangent curved "
            "faces (e.g. a sphere dome meeting a cylinder) or when the wall "
            "is thicker than the smallest surface curvature radius."
        ),
        "fix": (
            "Retry with kind=Kind.INTERSECTION, use a different wall "
            "thickness, or use toolkit.safe_shell() which tries both kinds "
            "and falls back to a boolean-subtract approximation."
        ),
    },
    {
        "id": "offset_shell_collapsed",
        "regex": r"(?s)^(?=.*Null TopoDS_Shape object)(?=.*\boffset\b)",
        "diagnosis": (
            "offset()/shell failed: the requested wall thickness cannot be "
            "propagated — the inner offset self-intersects (thickness larger "
            "than half the part, or larger than the smallest fillet/feature "
            "radius)."
        ),
        "fix": (
            "Use a thinner wall, or shell BEFORE adding small fillets/features, "
            "or use toolkit.safe_shell() which falls back to a boolean-subtract "
            "approximation."
        ),
    },
    {
        "id": "null_shape_result",
        "regex": r"Null TopoDS_Shape object",
        "diagnosis": (
            "An operation produced an empty (null) shape — OCCT computed "
            "nothing for the given inputs."
        ),
        "fix": (
            "Check that the inputs actually overlap/intersect and that all "
            "dimensions are positive and non-degenerate."
        ),
    },
    {
        "id": "spline_degenerate_points",
        "regex": r"(?s)^(?=.*Standard_Failure)(?=.*GeomAPI_Interpolate)",
        "diagnosis": (
            "Spline interpolation failed — consecutive control points are "
            "coincident (duplicates) or otherwise degenerate."
        ),
        "fix": (
            "Remove duplicate/near-duplicate consecutive points from the "
            "spline point list (points closer than ~1e-6 mm count as equal)."
        ),
    },
    {
        "id": "primitive_nonpositive_dimension",
        "regex": r"(?s)^(?=.*Standard_Failure)(?=.*make_(?:cylinder|cone|box|sphere|torus|wedge))",
        "diagnosis": (
            "A primitive (Cylinder/Hole/Cone/Box/...) was constructed with a "
            "non-positive dimension, e.g. Hole(radius=-2) or height 0."
        ),
        "fix": (
            "Validate radius/height/side parameters are > 0 before creating "
            "the primitive; check parametric expressions that may go negative."
        ),
    },
    {
        "id": "zero_thickness_extrude",
        "regex": r"BRepSweep_Translation::Constructor",
        "diagnosis": "extrude() was called with amount=0 (zero-length sweep vector).",
        "fix": (
            "Give extrude a non-zero amount; if the amount is computed, guard "
            "against it evaluating to 0."
        ),
    },
    {
        "id": "revolve_profile_crosses_axis",
        "regex": r"(?s)^(?=.*BRep_API: command not done)(?=.*revolve)",
        "diagnosis": (
            "revolve() failed — the profile crosses the rotation axis (material "
            "would sweep through itself). The profile must lie entirely on one "
            "side of the axis."
        ),
        "fix": (
            "Move the sketch so it touches but does not cross the axis, or "
            "revolve half the profile; check that the intended axis matches "
            "the sketch plane."
        ),
    },
    {
        "id": "loft_vertex_position",
        "regex": r"Vertices must be the first, last, or first and last elements",
        "diagnosis": (
            "loft() got a Vertex in the middle of the section list — point "
            "sections may only cap the ends of a loft."
        ),
        "fix": "Reorder loft sections so vertices are only first and/or last.",
    },
    {
        "id": "polyline_too_few_points",
        "regex": r"Polyline requires two or more pts",
        "diagnosis": "Polyline was given fewer than two points.",
        "fix": "Provide at least two points, or use a different primitive.",
    },
    {
        "id": "plane_zero_normal",
        "regex": r"z_dir must be non null",
        "diagnosis": (
            "A Plane was constructed with a zero-length normal vector "
            "(z_dir=(0,0,0)), often from a cross product of parallel vectors."
        ),
        "fix": "Supply a non-zero z_dir; check vector math that may cancel out.",
    },
    {
        "id": "builder_missing_sketch",
        "regex": r"A face or sketch must be provided",
        "diagnosis": (
            "extrude()/loft() etc. found no pending sketch — the BuildSketch "
            "block is missing, empty, or on the wrong builder."
        ),
        "fix": (
            "Create the profile in a BuildSketch context inside the BuildPart "
            "before calling extrude, or pass the face explicitly."
        ),
    },
    {
        "id": "generic_occt_not_done",
        "regex": r"BRep_API: command not done",
        "diagnosis": (
            "OCCT could not complete the operation for the given inputs "
            "(generic kernel failure: degenerate geometry, self-intersection, "
            "or impossible parameters)."
        ),
        "fix": (
            "Simplify the inputs: check for zero/negative dimensions, profiles "
            "crossing axes, self-intersecting paths, or tangent faces; try the "
            "toolkit safe_* variants."
        ),
    },
    # ---- signatures raised by worker-side validation (not exceptions from
    #      OCCT itself, but triggered by real geometry defects we reproduce) --
    {
        "id": "invalid_result_self_intersection",
        "regex": r"(?s)^(?=.*invalid)(?=.*(self.intersect|negative volume|degenerate))",
        "diagnosis": (
            "The operation 'succeeded' but produced an invalid solid (e.g. a "
            "self-intersecting profile was extruded: is_valid=False, volume "
            "can even be negative). OCCT does not raise for this."
        ),
        "fix": (
            "Fix the profile so it does not cross itself; after risky ops "
            "check part.is_valid and part.volume > 0 (toolkit.validate_part)."
        ),
    },
    {
        "id": "bool_disjoint_result",
        "regex": r"disjoint solids",
        "diagnosis": (
            "A fuse produced multiple disjoint solids — the parts do not "
            "actually touch (a sub-tolerance gap of ~1e-5 between 'touching' "
            "faces is enough)."
        ),
        "fix": (
            "Ensure real overlap (small interference) between parts, or use "
            "toolkit.safe_bool(..., fuzzy=1e-4) which fuses across tiny gaps."
        ),
    },
    {
        "id": "bool_empty_result",
        "regex": r"(?s)^(?=.*empty)(?=.*(boolean|intersect|cut|hole|section|split))",
        "diagnosis": (
            "A boolean/section produced an empty result — the tools do not "
            "overlap the target (or a hole/cut consumed the whole part)."
        ),
        "fix": (
            "Check positions/sizes: the cutting tool must intersect the part "
            "and must not swallow it entirely."
        ),
    },
]

_COMPILED = [(e, re.compile(e["regex"])) for e in ERROR_DOCTOR]


def diagnose_text(exc_type: str, message: str, tb_text: str = "") -> dict | None:
    """Match an error signature; returns the ERROR_DOCTOR entry or None."""
    blob = f"{exc_type}: {message}\n{tb_text}"
    for entry, rx in _COMPILED:
        if rx.search(blob):
            return entry
    return None


def diagnose_exception(exc: BaseException) -> dict | None:
    """Diagnose a caught exception (walks the __cause__/__context__ chain)."""
    tb_text = "".join(_traceback.format_exception(exc))
    seen, cur = set(), exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        entry = diagnose_text(type(cur).__name__, str(cur), tb_text)
        if entry is not None:
            return entry
        cur = cur.__cause__ or cur.__context__
    return None


def diagnose(err_type: str, message: str, traceback_text: str = "") -> str | None:
    """Worker hook: return a plain-language hint string for a failure, or None.

    Called by worker._diagnose to enrich every kernel/script error's
    ``details.hint`` (see agentcad/kernel/worker.py).
    """
    entry = diagnose_text(err_type, message, traceback_text)
    if entry is None:
        return None
    return f"{entry['diagnosis']} Fix: {entry['fix']}"
