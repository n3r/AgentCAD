"""Our PMI model (``core/pmi.py``) → XCAF/AP242, and the AP242 writer.

Kernel-only (imports OCP). Underscore-prefixed so ``worker._load_handler_packs``
skips it as a pack — ``handlers/interop.py`` imports it directly.

The PRD-017 de-risking spike (``docs/superpowers/specs/
2026-08-23-interop-pack-spike.md``, section B) found six ways OCCT's XCAF PMI
path fails *silently* — a dropped tolerance, a ×1000 value, an inverted sign,
or a dead worker. This module owns all six as hard rules so no caller can miss
one:

1. **De-locate before ``AddShape``** — a located shape yields a *reference*
   label whose ``AddSubShape`` returns a null label for every face, and
   ``SetDatum`` then dies with "A null Label has no attribute". The location is
   *baked into the geometry*, not dropped: the spike's recipe drops it, which
   is only lossless because its fixtures sat at the origin.
2. **Construct ``STEPCAFControl_Writer`` first, then set
   ``write.step.schema = AP242DIS`` and assert the setter returned True** —
   set before construction the static does not exist yet and the write is a
   silent no-op producing an AP214 file with *zero* PMI. ``write_ap242`` also
   re-reads the header (``assert_ap242_header``).
3. **``DatumObject.SetPosition(1..3)`` always** — without it every
   datum-referencing FCF is dropped with no error (7/15 FCF types survive
   instead of 15/15).
4. **At least one DIMENSION in the document** — a dimension-less document
   makes OCCT mint METRE units for every geometric-tolerance measure, so
   0.05 mm reads back as 50.0. FCF-only PMI therefore gets one untoleranced
   auxiliary overall-size dimension plus a ``notes`` entry.
5. **Tolerances are magnitudes** — ``SetLowerTolValue(+minus)``; the writer
   negates. A signed value writes a standards-incorrect file that our own
   round trip would still pass.
6. **``Size_WithPath`` / ``Location_WithPath`` / two-target
   ``Location_Oriented`` segfault the writer** (exit 139, no Python
   exception), and angular dimensions round-trip in mismatched units. Every
   dimension type reaches the writer through :func:`dimension_type`, which
   refuses those as a ``pmi_skipped`` row.

PMI entry identity does not survive the writer (labels are overwritten by the
STEP type keyword), so nothing here relies on names round-tripping — the
skipped/attached bookkeeping is returned to the caller instead.
"""

from __future__ import annotations

import contextlib
import sys

import OCP.XCAFDimTolObjects as _dimtol
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.GProp import GProp_GProps
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_StepModelType
from OCP.TCollection import TCollection_ExtendedString, TCollection_HAsciiString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import (XCAFDoc_Datum, XCAFDoc_Dimension,
                         XCAFDoc_DocumentTool, XCAFDoc_GeomTolerance)

# --------------------------------------------------------------- trap 6

#: Dimension types that must never reach ``STEPCAFControl_Writer::Transfer``,
#: whatever the target count. The first two kill the process; the angular ones
#: are written in radians and read back in degrees with their tolerances
#: unconverted (spike B.2/B.3).
BLOCKED_DIMENSION_TYPES = {
    "Size_WithPath": "segfaults OCCT's STEP writer (spike B.2 trap 6)",
    "Location_WithPath": "segfaults OCCT's STEP writer (spike B.2 trap 6)",
    "Size_Angular": "angular dimensions are written in radians and read back "
                    "in degrees, with tolerances left unconverted (spike B.3)",
    "Location_Angular": "angular dimensions are written in radians and read "
                        "back in degrees, with tolerances left unconverted "
                        "(spike B.3)",
}

#: Types that are safe with one target label and fatal with two.
MULTI_TARGET_BLOCKED = {
    "Location_Oriented": "segfaults OCCT's STEP writer with two target faces "
                         "(spike B.2 trap 6)",
}

#: A ``Location_*`` type written with a single target produces a valid
#: DIMENSIONAL_LOCATION that reads back as nothing — a silent loss. Our model
#: only emits ``Size_*``; this is the guard for the day it does not.
SINGLE_TARGET_ONLY_PREFIX = "Location_"


class PmiRefusal(Exception):
    """A PMI entry that must not be emitted. Carries the ``pmi_skipped``
    reason verbatim."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def dimension_type(name: str, n_targets: int = 1):
    """The only door a dimension type passes through on its way to the writer.

    Returns the ``XCAFDimTolObjects_DimensionType_*`` enum value; raises
    :class:`PmiRefusal` for a blocklisted type (trap 6) or an unknown one.
    """
    reason = BLOCKED_DIMENSION_TYPES.get(name)
    if reason is None and n_targets >= 2:
        reason = MULTI_TARGET_BLOCKED.get(name)
    if reason is None and name.startswith(SINGLE_TARGET_ONLY_PREFIX) \
            and n_targets < 2:
        reason = (f"{name} needs two target faces; written with one it reads "
                  "back as nothing (spike B.3)")
    if reason is not None:
        raise PmiRefusal(f"blocked_dimension_type: {name} {reason}")
    enum = getattr(_dimtol, f"XCAFDimTolObjects_DimensionType_{name}", None)
    if enum is None:
        raise PmiRefusal(f"unknown_dimension_type: {name}")
    return enum


def geom_tolerance_type(name: str):
    """The ``XCAFDimTolObjects_GeomToleranceType_*`` enum for one of our five
    FCF types. All 15 OCCT types survive the round trip (spike B.2 trap 3), so
    there is no blocklist here — only an unknown-name refusal."""
    enum = getattr(_dimtol, f"XCAFDimTolObjects_GeomToleranceType_{name}", None)
    if enum is None:
        raise PmiRefusal(f"unknown_geom_tolerance_type: {name}")
    return enum


# --------------------------------------------------------- model → XCAF names

#: ``core/pmi.py`` dim kind → XCAF dimension type. Both are ``Size_*``: a
#: ``Location_*`` needs two target labels (spike B.3).
DIM_TYPE_BY_KIND = {"linear": "Size_Thickness", "diameter": "Size_Diameter"}

#: ``core/pmi.py`` FCF type → XCAF geometric-tolerance type.
FCF_TYPE_BY_NAME = {
    "flatness": "Flatness",
    "position": "Position",
    "perpendicularity": "Perpendicularity",
    "parallelism": "Parallelism",
    "cylindricity": "Cylindricity",
}

#: Datum face selector → outward normal (the box-face semantics of
#: ``core/pmi.py`` and ``toolkit/holes._NAMED_PLANES``).
FACE_NORMALS = {
    "top": (0.0, 0.0, 1.0), "bottom": (0.0, 0.0, -1.0),
    "left": (-1.0, 0.0, 0.0), "right": (1.0, 0.0, 0.0),
    "front": (0.0, -1.0, 0.0), "back": (0.0, 1.0, 0.0),
}

#: Linear dim target → (axis index, preferred outward normal). width = X,
#: height = Z, depth = Y (``core/pmi.py``).
LINEAR_AXES = {"width": (0, "right"), "height": (2, "top"), "depth": (1, "back")}

#: A cylindrical face counts as *the* feature of a diameter dim when its
#: diameter is within this of the declared nominal — the drawing renderer's
#: matching tolerance (``handlers/drawing.py``).
DIAMETER_MATCH_TOL = 0.05

#: Cosine floor for "this planar face faces that way".
_NORMAL_DOT = 0.999

AUX_DIM_NOTE = ("auxiliary overall-size dimension emitted to pin millimetre "
                "units")


# ------------------------------------------------------------ face catalogue


def delocated(ocp_shape):
    """An equivalent shape carrying an identity ``TopLoc_Location`` (trap 1).

    Dropping the location outright would MOVE the part (a script that returns
    ``Pos(50, 0, 0) * Box(...)`` carries its placement there), so the
    transformation is baked into the geometry instead."""
    location = ocp_shape.Location()
    if location.IsIdentity():
        return ocp_shape
    return BRepBuilderAPI_Transform(
        ocp_shape.Located(TopLoc_Location()), location.Transformation(), True
    ).Shape()


def _face_records(ocp_shape, shape_tool, part_label) -> list[dict]:
    """Every face of the (already de-located) shape as
    ``{label, kind, area, center, normal?, diameter?}``, in explorer order."""
    records: list[dict] = []
    explorer = TopExp_Explorer(ocp_shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        explorer.Next()
        label = shape_tool.AddSubShape(part_label, face)
        if label.IsNull():
            continue  # trap 1 fallout — a located shape has no sub-shape labels
        adaptor = BRepAdaptor_Surface(face)
        surface_type = adaptor.GetType()
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        center = props.CentreOfMass()
        record = {
            "label": label,
            "area": float(props.Mass()),
            "center": (center.X(), center.Y(), center.Z()),
            "kind": "other",
        }
        if surface_type == GeomAbs_SurfaceType.GeomAbs_Plane:
            direction = adaptor.Plane().Axis().Direction()
            normal = (direction.X(), direction.Y(), direction.Z())
            if face.Orientation() == TopAbs_REVERSED:
                normal = tuple(-c for c in normal)
            record["kind"] = "plane"
            record["normal"] = normal
        elif surface_type == GeomAbs_SurfaceType.GeomAbs_Cylinder:
            record["kind"] = "cylinder"
            record["diameter"] = 2.0 * float(adaptor.Cylinder().Radius())
        records.append(record)
    return records


def _largest(candidates: list[dict]):
    """The largest candidate, ties broken by face centre so the choice is
    reproducible across runs and machines."""
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: (-r["area"], r["center"]))[0]


def _plane_facing(records: list[dict], face_name: str):
    want = FACE_NORMALS[face_name]
    return _largest([
        r for r in records if r["kind"] == "plane"
        and sum(a * b for a, b in zip(r["normal"], want)) >= _NORMAL_DOT
    ])


def _largest_plane(records: list[dict]):
    return _largest([r for r in records if r["kind"] == "plane"])


def _cylinder_for(records: list[dict], diameter: float | None = None):
    """The largest cylindrical face, preferring one whose diameter matches
    ``diameter``. The preference is what makes a two-bore part target the bore
    the dimension is actually about; the fallback keeps the spec's plain
    "largest cylindrical face" rule."""
    cylinders = [r for r in records if r["kind"] == "cylinder"]
    if diameter is not None:
        matched = [r for r in cylinders
                   if abs(r["diameter"] - diameter) <= DIAMETER_MATCH_TOL]
        if matched:
            return _largest(matched)
    return _largest(cylinders)


# ------------------------------------------------------------------ emitters


def new_document():
    """A fresh XCAF document (the application singleton is process-wide)."""
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    return doc


def _finite(value, what: str) -> float:
    """*value* as a float, refused (as a ``pmi_skipped`` row) if not finite.

    ``core/pmi.validate_pmi`` compares against 0 — and every comparison with a
    NaN is False, so ``plus < 0``, ``plus == 0`` and ``tol <= 0`` all wave one
    through. It then reaches ``SetValue``, OCCT writes the literal ``NAN`` into
    the STEP file, and the export reports it as *attached*: a toleranced part
    whose tolerance is unreadable by every consumer, described as fine. The
    refusal here turns that into a named skip.
    """
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise PmiRefusal(f"non_finite_value: {what} is {value!r}")
    return number


def _emit_dimension(dimtol_tool, type_name, value, plus, minus, target_labels):
    """Add one dimension. ``plus``/``minus`` are MAGNITUDES (trap 5)."""
    enum = dimension_type(type_name, len(target_labels))  # trap 6 gate
    # Checked BEFORE `AddDimension`: a refusal must leave no half-built label
    # in the document (an empty dimension label is still a DIMENSIONAL_SIZE the
    # writer emits).
    value = _finite(value, "dimension value")
    plus = None if plus is None else _finite(plus, "dimension plus tolerance")
    minus = None if minus is None else _finite(minus,
                                               "dimension minus tolerance")
    label = dimtol_tool.AddDimension()
    obj = _dimtol.XCAFDimTolObjects_DimensionObject()
    obj.SetType(enum)
    obj.SetValue(float(value))
    if plus is not None:
        obj.SetUpperTolValue(abs(float(plus)))
    if minus is not None:
        obj.SetLowerTolValue(abs(float(minus)))
    XCAFDoc_Dimension.Set_s(label).SetObject(obj)
    for target in target_labels:
        dimtol_tool.SetDimension(target, label)
    return label


def _emit_datum(dimtol_tool, letter: str, position: int, target_label):
    name = TCollection_HAsciiString(letter)
    label = dimtol_tool.AddDatum(name, name, name)
    obj = _dimtol.XCAFDimTolObjects_DatumObject()
    obj.SetName(TCollection_HAsciiString(letter))
    obj.SetPosition(position)  # trap 3 — 1=primary 2=secondary 3=tertiary
    XCAFDoc_Datum.Set_s(label).SetObject(obj)
    sequence = TDF_LabelSequence()
    sequence.Append(target_label)
    dimtol_tool.SetDatum(sequence, label)
    return label


def _emit_geom_tolerance(dimtol_tool, type_name, value, target_label,
                         datum_labels, diameter_zone=False):
    enum = geom_tolerance_type(type_name)
    value = _finite(value, "tolerance value")     # before AddGeomTolerance
    label = dimtol_tool.AddGeomTolerance()
    obj = _dimtol.XCAFDimTolObjects_GeomToleranceObject()
    obj.SetType(enum)
    obj.SetValue(float(value))
    if diameter_zone:
        obj.SetTypeOfValue(
            _dimtol.XCAFDimTolObjects_GeomToleranceTypeValue_Diameter)
    XCAFDoc_GeomTolerance.Set_s(label).SetObject(obj)
    dimtol_tool.SetGeomTolerance(target_label, label)
    for datum_label in datum_labels:
        dimtol_tool.SetDatumToGeomTol(datum_label, label)
    return label


# ------------------------------------------------------------------ the map


def map_pmi(doc, shape, pmi: dict, name: str | None = None) -> dict:
    """Put ``shape`` and its normalized PMI section into the XCAF document.

    ``shape`` is a build123d shape; ``pmi`` is ``core/pmi.validate_pmi``'s
    output. Returns ``{"attached": {"dims", "datums", "fcf"}, "skipped":
    [{"id", "reason"}], "notes": [str]}`` — nothing is ever dropped silently
    (FR3).
    """
    pmi = pmi if isinstance(pmi, dict) else {}
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    dimtol_tool = XCAFDoc_DocumentTool.DimTolTool_s(doc.Main())

    # Trap 1: a placed shape carries a non-identity TopLoc_Location, and
    # AddShape on one makes a reference label whose sub-shape labels are null.
    ocp_shape = delocated(shape.wrapped)
    part_label = shape_tool.AddShape(ocp_shape, False)
    if name:
        TDataStd_Name.Set_s(part_label, TCollection_ExtendedString(name))
    records = _face_records(ocp_shape, shape_tool, part_label)
    bbox = shape.bounding_box()
    extents = (bbox.size.X, bbox.size.Y, bbox.size.Z)

    skipped: list[dict] = []
    notes: list[str] = []
    attached = {"dims": 0, "datums": 0, "fcf": 0}

    def skip(entry_id, reason):
        skipped.append({"id": entry_id, "reason": reason})

    def entries(key: str) -> list:
        """The section as a list of dicts. Defence in depth, not paranoia: the
        PMI section is a schema-tolerant LOOSE key on a manifest entry, so a
        hand edit, a bad merge or an older tool can put anything there. The
        server validates it before we are called (`tools_xchange._part_pmi`);
        this makes the second line of defence a `malformed_entry` ROW rather
        than a `KeyError` that answers 502 for a file the user can fix."""
        section = pmi.get(key)
        if not isinstance(section, list):
            if section:
                skip(key, f"malformed_entry: pmi.{key} is not a list")
            return []
        out = []
        for index, entry in enumerate(section):
            if isinstance(entry, dict):
                out.append(entry)
            else:
                skip(f"{key}[{index}]",
                     "malformed_entry: not an object")
        return out

    def entry_id(entry: dict, key: str, index: int):
        value = entry.get("id")
        return value if isinstance(value, str) and value else f"{key}[{index}]"

    # --- dimensions
    for index, dim in enumerate(entries("dims")):
        row = entry_id(dim, "dims", index)
        kind = dim.get("kind")
        if kind not in DIM_TYPE_BY_KIND:
            skip(row, f"malformed_entry: unknown dim kind {kind!r}")
            continue
        if kind == "linear":
            axes = LINEAR_AXES.get(dim.get("target"))
            if axes is None:
                skip(row, "malformed_entry: linear target must be one of "
                          + ", ".join(sorted(LINEAR_AXES)))
                continue
            axis, face_name = axes
            target = _plane_facing(records, face_name)
            if target is None:  # the opposite face carries the same extent
                target = _plane_facing(
                    records, {"right": "left", "top": "bottom",
                              "back": "front"}[face_name])
            if target is None:
                skip(row, f"no_planar_face: the part has no planar face "
                          f"normal to the {dim['target']} axis")
                continue
            value = extents[axis]
        else:  # diameter — the target IS the nominal diameter (core/pmi.py)
            try:
                value = float(dim.get("target"))    # type: ignore[arg-type]
            except (TypeError, ValueError):
                skip(row, "malformed_entry: diameter target must be a number")
                continue
            target = _cylinder_for(records, value)
            if target is None:
                skip(row, "no_cylindrical_face: the part has no "
                          "cylindrical face to carry a diameter "
                          "dimension")
                continue
        try:
            _emit_dimension(dimtol_tool, DIM_TYPE_BY_KIND[kind], value,
                            dim.get("plus"), dim.get("minus"),
                            [target["label"]])
        except PmiRefusal as refusal:
            skip(row, refusal.reason)
            continue
        except (TypeError, ValueError) as exc:
            skip(row, f"malformed_entry: {exc}")
            continue
        attached["dims"] += 1

    # --- datums
    datum_labels: dict[str, object] = {}
    datum_targets: dict[str, dict] = {}
    for index, datum in enumerate(entries("datums")):
        row = entry_id(datum, "datums", index)
        face = datum.get("face")
        if face not in FACE_NORMALS:
            skip(row, f"malformed_entry: unknown datum face {face!r}")
            continue
        target = _plane_facing(records, face)
        if target is None:
            skip(row, f"no_planar_face: the part has no planar face "
                      f"on its {face} side")
            continue
        # ASME Y14.5 has three precedence slots; a fourth declared datum shares
        # the tertiary one rather than inventing a position OCCT never writes.
        position = min(index + 1, 3)
        datum_labels[row] = _emit_datum(dimtol_tool, row, position,
                                        target["label"])
        datum_targets[row] = target
        attached["datums"] += 1

    # --- feature control frames
    for index, frame in enumerate(entries("fcf")):
        row = entry_id(frame, "fcf", index)
        kind = frame.get("type")
        if kind not in FCF_TYPE_BY_NAME:
            skip(row, f"malformed_entry: unknown fcf type {kind!r}")
            continue
        raw_datums = frame.get("datums") or []
        if not isinstance(raw_datums, list):
            skip(row, "malformed_entry: fcf datums must be a list")
            continue
        declared = [r for r in raw_datums]
        refs = [r for r in declared if r in datum_labels]
        if len(refs) != len(declared):
            # the referenced datum itself was skipped — say so rather than
            # emit a quietly weaker frame (FR3)
            notes.append(
                f"pmi fcf {row!r}: datum reference(s) "
                f"{', '.join(str(r) for r in declared if r not in refs)} "
                "dropped — those datums could not be attached")
        if kind == "cylindricity":
            target = _cylinder_for(records)
            if target is None:
                skip(row, "no_cylindrical_face: cylindricity needs a "
                          "cylindrical face")
                continue
        elif refs:
            target = datum_targets[refs[0]]
        else:
            target = _largest_plane(records)
            if target is None:
                skip(row, "no_planar_face: no planar face to carry "
                          "the feature control frame")
                continue
        try:
            _emit_geom_tolerance(
                dimtol_tool, FCF_TYPE_BY_NAME[kind], frame.get("tol_mm"),
                target["label"], [datum_labels[r] for r in refs],
                # Our model carries no zone shape; a position tolerance is
                # diametral by convention (ASME Y14.5) and that is what the
                # spike's recipe writes.
                diameter_zone=(kind == "position"),
            )
        except PmiRefusal as refusal:
            skip(row, refusal.reason)
            continue
        except (TypeError, ValueError) as exc:
            skip(row, f"malformed_entry: {exc}")
            continue
        attached["fcf"] += 1

    # --- trap 4: a document with no DIMENSION writes tolerances in METRES
    if attached["dims"] == 0 and (attached["datums"] or attached["fcf"]):
        if _emit_auxiliary_dimension(dimtol_tool, records, extents):
            attached["dims"] += 1
            notes.append(AUX_DIM_NOTE)
        else:
            notes.append("no face could carry the auxiliary overall-size "
                         "dimension; tolerance values may be written in metres")

    return {"attached": attached, "skipped": skipped, "notes": notes}


def _emit_auxiliary_dimension(dimtol_tool, records, extents) -> bool:
    """One untoleranced ``DIMENSIONAL_SIZE`` of an overall bbox extent — true
    information, emitted only to pin the model's millimetre units (trap 4)."""
    for axis, face_name in ((2, "top"), (2, "bottom"), (0, "right"),
                            (0, "left"), (1, "back"), (1, "front")):
        target = _plane_facing(records, face_name)
        if target is not None:
            _emit_dimension(dimtol_tool, DIM_TYPE_BY_KIND["linear"],
                            extents[axis], None, None, [target["label"]])
            return True
    fallback = _largest(records)
    if fallback is None:
        return False
    _emit_dimension(dimtol_tool, DIM_TYPE_BY_KIND["linear"], max(extents),
                    None, None, [fallback["label"]])
    return True


# ------------------------------------------------------------------- writing


def write_ap242(doc, path: str) -> None:
    """Write ``doc`` as AP242 with PMI. Trap 2 lives here: the STEP statics do
    not exist until a writer has been constructed, so setting the schema first
    is a silent no-op that yields an AP214 file with zero PMI."""
    with contextlib.redirect_stdout(sys.stderr):  # OCCT prints transfer stats
        writer = STEPCAFControl_Writer()
        previous = Interface_Static.CVal_s("write.step.schema")
        # The try opens BEFORE the setter, not after it: a raise between "the
        # static was captured" and "the restore is armed" leaks AP242DIS into
        # every later export in this worker (the `interop._write_assembly_ap242`
        # twin, where a second setter made the window real).
        try:
            if not Interface_Static.SetCVal_s("write.step.schema", "AP242DIS"):
                raise RuntimeError(
                    "Interface_Static.SetCVal_s('write.step.schema', "
                    "'AP242DIS') returned False — the file would be AP214 "
                    "with no PMI")
            writer.SetDimTolMode(True)
            writer.SetColorMode(True)
            writer.SetNameMode(True)
            writer.SetLayerMode(True)
            if not writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs):
                raise RuntimeError("STEPCAFControl_Writer.Transfer failed")
            status = writer.Write(str(path))
        finally:
            # write.step.schema is a process-wide static: leaving it at AP242
            # would silently change every later b3d.export_step in this worker.
            Interface_Static.SetCVal_s("write.step.schema", previous or "AP214IS")
    if str(status) != "IFSelect_ReturnStatus.IFSelect_RetDone":
        raise RuntimeError(f"STEP write failed: {status}")


def schema_of(path: str) -> str:
    """The FILE_SCHEMA statement of a STEP file's header, on one line (in the
    file it spans several, so a line-wise search would miss the schema name)."""
    with open(path, "r", errors="replace") as handle:
        header = handle.read(8192)
    start = header.find("FILE_SCHEMA")
    if start < 0:
        return ""
    end = header.find(";", start)
    return " ".join(header[start:end if end > 0 else None].split())


def assert_ap242_header(path: str) -> str:
    """Re-open the written file and prove the schema (trap 2's cheap half)."""
    schema = schema_of(path)
    if "AP242" not in schema:
        raise RuntimeError(
            f"written STEP is not AP242 — PMI would be absent: {schema!r}")
    return schema
