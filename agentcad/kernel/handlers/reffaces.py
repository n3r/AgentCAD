"""Worker handler: the B-rep faces of an IMPORTED reference part.

``face_info`` (``handlers/facemod.py``) reports one face of a **script** part —
it takes ``script`` and ``params`` and builds. A reference part has no script,
so nothing in this tree could read the faces of an imported STEP, and PRD-011
FR13's connector assist needs exactly that: the planar faces a ``rigid``
connector seats on and the cylindrical faces a ``cylindrical`` connector runs
down.

Two things it deliberately is not:

* **not a connector inference.** It reports geometry; the author (human or
  agent) writes ``connectors``. Inferring which cylinder is "the shaft" from
  an unlabelled solid is a research problem, not a handler (design spec
  divergence 7).
* **not a mesh derivation.** PRD-008's ``anchors.signature_table`` derives
  per-face area/centroid/normal from the ``.acm`` + ``.faces.u32`` sidecar with
  no kernel call, and it cannot serve here for two measured reasons: the
  reference build path writes **no ``.faces.u32`` sidecar** at all (a
  ``signature_table`` over a freshly built reference part returns zero rows),
  and an area-weighted normal over a closed cylinder nearly cancels, so a
  cylinder's *axis* is not recoverable from it (AGENTS.md, PRD-008).

Indices are the ``TopExp_Explorer(FACE)`` walk — the same ordinal ``face_info``
and ``mesh.py`` use — so a face index here means what it means everywhere else.
"""

from __future__ import annotations

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_SurfaceType

from ...toolkit.facemod import faces_in_mesh_order
from ..refload import load_reference

#: How many faces one call reports. A vendor STEP assembly routinely carries
#: thousands, and a JSON-RPC payload with all of them is not a suggestion list.
#: The largest by area are the ones a connector seats on, so the cap is applied
#: after an area sort and `n_faces` always reports the true total.
DEFAULT_LIMIT = 24

_KINDS = {
    GeomAbs_SurfaceType.GeomAbs_Plane: "planar",
    GeomAbs_SurfaceType.GeomAbs_Cylinder: "cylindrical",
    GeomAbs_SurfaceType.GeomAbs_Cone: "conical",
    GeomAbs_SurfaceType.GeomAbs_Sphere: "spherical",
    GeomAbs_SurfaceType.GeomAbs_Torus: "toroidal",
}


def _xyz(point) -> list[float]:
    return [float(point.X()), float(point.Y()), float(point.Z())]


def register(toolbox: dict) -> dict:
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def reference_faces(params: dict) -> dict:
        source = params["source_path"]
        limit = int(params.get("limit") or DEFAULT_LIMIT)
        shape, kind = load_reference(source)
        if kind == "mesh":
            # One welded triangulation Face with no surface — `BRep_Tool.
            # Surface_s(face)` is None, so there is nothing here to report and
            # nothing a connector could seat on. Refused rather than answered
            # with an empty list, because "no faces" would read as "no
            # candidates found" instead of "this format cannot have any".
            raise WorkerError(
                ERROR_CONTRACT,
                "an STL reference is one welded mesh face with no surface: it "
                "has no planar or cylindrical faces to suggest connectors "
                "from, and its booleans segfault OCCT",
                {"kind": kind, "source": source})
        faces = faces_in_mesh_order(shape)
        rows = []
        counts: dict[str, int] = {}
        for index, face in enumerate(faces):
            surface = BRepAdaptor_Surface(face.wrapped)
            name = _KINDS.get(surface.GetType(), "other")
            counts[name] = counts.get(name, 0) + 1
            row = {"index": index, "kind": name,
                   "area_mm2": float(face.area)}
            if name == "planar":
                center, normal = face.center(), face.normal_at()
                row["center"] = [float(center.X), float(center.Y),
                                 float(center.Z)]
                row["normal"] = [float(normal.X), float(normal.Y),
                                 float(normal.Z)]
            elif name == "cylindrical":
                cylinder = surface.Cylinder()
                axis = cylinder.Axis()
                row["radius_mm"] = float(cylinder.Radius())
                row["axis_origin"] = _xyz(axis.Location())
                row["axis_direction"] = _xyz(axis.Direction())
            rows.append(row)
        rows.sort(key=lambda row: (-row["area_mm2"], row["index"]))
        return {"n_faces": len(faces), "kinds": counts, "limit": limit,
                "truncated": len(rows) > limit, "faces": rows[:limit]}

    return {"reference_faces": reference_faces}
